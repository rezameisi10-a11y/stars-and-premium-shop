"""منطق فروش VPN: سفارش (جدید/تمدید/افزودنی)، ساخت و مدیریت سرویس روی پنل‌ها، همگام‌سازی، یادآوری و حذف خودکار.

از همان خط لوله‌ی سفارش/پرداخت/تخفیف/کیف پول ربات استفاده می‌کند (orders.kind='vpn'):
    ref = vpn:new:{plan_id}[:{username}]  |  vpn:renew:{service_id}:{plan_id}  |  vpn:addon:{service_id}:{addon_id}
    qty = تعداد روز پلن
"""
import asyncio
import io
import json
import os
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING

from aiogram.types import BufferedInputFile

import aiohttp

import vpn_panels
import vpn_qr
import wg_file
from i18n import N_
from ui import btn, esc, fmt, ikb
from vpn_panels import GB, PanelError

if TYPE_CHECKING:  # pragma: no cover
    from service import Shop

log = logging.getLogger("vpn")

NAME_RE = re.compile(r"^[a-z0-9_]{3,24}$")
SCHEME_RE = re.compile(r"^(vless|vmess|trojan|ss|ssr|hysteria2?|hy2|tuic|wireguard|socks5?)://\S+", re.I)


def parse_stock_text(text: str) -> list[tuple[str, str]]:
    """متن ورودی ادمین → [(نام, کانفیگ)]. قالب میرزا: خط «# نام» و زیرش کانفیگ (یک یا چند خط). بدون «#»: هر خط یک کانفیگ."""
    lines = [x.strip() for x in (text or "").replace("\r", "").split("\n")]
    if not any(x.startswith("#") for x in lines):
        return [("", x) for x in lines if x]
    items, name, buf = [], "", []

    def flush():
        nonlocal name, buf
        cfg = "\n".join(x for x in buf if x).strip()
        if cfg:
            items.append((name, cfg))
        name, buf = "", []

    for x in lines:
        if x.startswith("#"):
            flush()
            name = x.lstrip("#").strip()
        elif x:
            buf.append(x)
    flush()
    return items


def split_config(config: str) -> tuple[str, list[str]]:
    """متن کانفیگ ذخیره‌شده → (لینک اشتراک یا خالی, لیست کانفیگ‌ها)."""
    lines = [x.strip() for x in (config or "").split("\n") if x.strip()]
    sub = ""
    if lines and re.match(r"^https?://", lines[0], re.I):
        sub = lines.pop(0)
    return sub, lines


def parse_sub_body(raw: bytes) -> list[str]:
    """محتوای لینک ساب (متن ساده یا base64) → کانفیگ‌ها."""
    import base64
    txt = raw.decode("utf-8", "ignore").strip()
    if txt and not SCHEME_RE.search(txt.replace("\r", "").split("\n")[0] if txt else ""):
        comp = re.sub(r"\s+", "", txt)
        for dec in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                out = dec(comp + "=" * (-len(comp) % 4)).decode("utf-8", "ignore")
                if "://" in out:
                    txt = out
                    break
            except Exception:  # noqa: BLE001
                continue
    seen, res = set(), []
    for x in txt.replace("\r", "").split("\n"):
        x = x.strip()
        if SCHEME_RE.match(x) and x not in seen:
            seen.add(x)
            res.append(x)
    return res[:100]
STATUS_FA = {"active": N_("🟢 فعال"), "disabled": N_("⚫️ غیرفعال"), "limited": N_("🟠 حجم تمام شده"), "expired": N_("🔴 منقضی"),
             "on_hold": N_("⏸ در انتظار شروع"), "pending_manual": N_("🕓 در انتظار تحویل"), "deleted": N_("🗑 حذف شده")}
LIVE_OK = ("active", "on_hold")


class VpnError(Exception):
    """خطایی که پیامش (فارسی منبع) به کاربر/ادمین نشان داده می‌شود."""


def fmt_gb(b) -> str:
    b = int(b or 0)
    if b >= GB:
        v = b / GB
        return f"{v:.2f}".rstrip("0").rstrip(".") + " GB"
    return f"{b / 1024 ** 2:.0f} MB"


def ltr(s) -> str:
    """ایزوله‌ی چپ‌به‌راست: اعداد و واحد لاتین داخل متن راست‌به‌چپ جابه‌جا نمایش داده نمی‌شوند."""
    return f"\u2066{s}\u2069"


def clean_gb(gb) -> str:
    return f"{float(gb):g}"


def bar(used, total, n=10) -> str:
    if not total:
        return "♾"
    r = min(max(used / total, 0), 1)
    k = round(r * n)
    return "▓" * k + "░" * (n - k) + f" {int(r * 100)}%"


def jdate(ts) -> str:
    """تاریخ شمسی به وقت تهران."""
    from datetime import datetime, timedelta, timezone
    from webkit import g2j
    d = datetime.fromtimestamp(ts, timezone(timedelta(hours=3, minutes=30)))
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{jy}/{jm:02d}/{jd:02d}"


def left_text(ts, tr) -> str:
    """«۲۶ روز و ۱۷ ساعت و ۴۳ دقیقه»؛ اگر گذشته باشد «پایان‌یافته»."""
    d = int(ts - time.time())
    if d <= 0:
        return tr("پایان‌یافته")
    days, rem = divmod(d, 86400)
    h, rem = divmod(rem, 3600)
    parts = ([tr("{n} روز", n=days)] if days else []) + ([tr("{n} ساعت", n=h)] if h else []) + ([tr("{n} دقیقه", n=rem // 60)] if not days and rem // 60 else [])
    return (" " + tr("و") + " ").join(parts) or tr("کمتر از ۱ دقیقه")


def days_left(ts) -> int | None:
    if not ts:
        return None
    d = ts - time.time()
    return 0 if d <= 0 else int(d // 86400) + (1 if d % 86400 else 0)


def valid_name(n: str) -> bool:
    return bool(NAME_RE.match(n or ""))


# تنظیمات هر پنل (داخل ستون extra؛ مثل «قابلیت‌های پنل» در میرزا). مقدار 1 = روشن
PANEL_FLAGS = {"show": 1, "test": 1, "renew": 1, "addon": 1, "sub": 1, "config": 1, "qr": 1, "copy_sub": 1, "copy_cfg": 1,
               "revoke": 1, "toggle": 1, "transfer": 1, "unote": 1, "refund": 0}
PANEL_FLAG_TITLES = {"show": N_("نمایش پنل به کاربران"), "test": N_("اکانت تست"), "renew": N_("تمدید"), "addon": N_("حجم/زمان اضافه"),
                     "sub": N_("ارسال لینک اشتراک"), "config": N_("ارسال کانفیگ مستقیم"), "qr": N_("دکمه‌ی QR کد"),
                     "revoke": N_("دکمه‌ی تغییر لینک"), "toggle": N_("دکمه‌ی خاموش/روشن کردن اکانت"), "transfer": N_("دکمه‌ی انتقال سرویس به کاربر دیگر"),
                     "unote": N_("دکمه‌ی تغییر یادداشت"), "refund": N_("دکمه‌ی بازگشت وجه (با محدودیت‌های تنظیمات)"),
                     "copy_sub": N_("کپی لینک اشتراک با یک کلیک (خاموش = نگه‌داشتن انگشت)"), "copy_cfg": N_("کپی کانفیگ‌ها با یک کلیک (خاموش = نگه‌داشتن انگشت)")}


def pextra(panel) -> dict:
    raw = (panel or {}).get("panel_extra", (panel or {}).get("extra"))
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def pflag(panel, key) -> bool:
    return str(pextra(panel).get(key, PANEL_FLAGS.get(key, 1))) != "0"


def plimit(panel, key) -> int:
    """محدودیت عددی هر پنل (۰ یا خالی = بدون محدودیت)."""
    try:
        return max(int(float(pextra(panel).get(key) or 0)), 0)
    except (TypeError, ValueError):
        return 0


def hidden_for(panel, uid) -> bool:
    return uid in [int(x) for x in pextra(panel).get("hidden", []) if str(x).lstrip("-").isdigit()]


def parse_ref(ref: str) -> list[str]:
    return (ref or "").split(":")


def _ago(ts, tr) -> str:
    if not ts:
        return tr("متصل نشده")
    d = max(int(time.time() - ts), 0)
    if d < 90:
        return tr("همین الان")
    if d < 5400:
        return tr("{n} دقیقه پیش", n=d // 60)
    if d < 172800:
        return tr("{n} ساعت پیش", n=d // 3600)
    return tr("{n} روز پیش", n=d // 86400)


def _hb(n) -> str:
    """بایت → «۱.۸۷ گیگابایت» (فارسی)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit, div in (("ترابایت", 1024 ** 4), ("گیگابایت", 1024 ** 3), ("مگابایت", 1024 ** 2), ("کیلوبایت", 1024)):
        if n >= div:
            return f"{ltr(f'{n / div:.2f}')} {unit}"
    return f"{ltr(int(n))} بایت"


def _tree(rows: list) -> str:
    out = []
    for i, r in enumerate(rows):
        out.append(("└ " if i == len(rows) - 1 else "├ ") + r)
    return "\n".join(out)


def _uptime(sec) -> str:
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "-"
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    return (f"{d} روز و " if d else "") + f"{h} ساعت و {r // 60} دقیقه"


def render_status(st: dict) -> str:
    """متن مانیتورینگ سرور؛ فقط بخش‌هایی که پنل داده نشان داده می‌شود."""
    L = ["🖥 <b>وضعیت سرور</b>"]
    if "cpu" in st or st.get("cores"):
        rows = []
        if "cpu" in st:
            rows.append(f"مصرف: {ltr(str(round(float(st['cpu']), 1)) + '%')}")
        if st.get("cores"):
            rows.append(f"هسته‌ها: {ltr(st['cores'])}" + (f" (منطقی: {ltr(st['logical'])})" if st.get("logical") else ""))
        if st.get("cpu_mhz"):
            rows.append(f"فرکانس: {ltr(str(round(float(st['cpu_mhz']) / 1000, 1)) + ' GHz')}")
        L.append("\n⚙️ <b>پردازنده</b>\n" + _tree(rows))
    if st.get("load"):
        L.append("\n📊 <b>بار سیستم (۱/۵/۱۵ دقیقه)</b>\n" + _tree([ltr(" | ".join(f"{float(x):.2f}" for x in st["load"][:3]))]))
    for key, title, icon in (("mem", "رم", "🧠"), ("disk", "دیسک", "💾")):
        u, t = st.get(key + "_used"), st.get(key + "_total")
        if u is not None and t:
            L.append(f"\n{icon} <b>{title}</b>\n" + _tree([f"{_hb(u)} / {_hb(t)} ({ltr('%.1f%%' % (float(u) * 100 / float(t)))})"]))
    if "up_speed" in st or "down_speed" in st:
        L.append("\n🌐 <b>شبکه (لحظه‌ای)</b>\n" + _tree([f"آپلود: {_hb(st.get('up_speed', 0))}{ltr('/s')}", f"دانلود: {_hb(st.get('down_speed', 0))}{ltr('/s')}"]))
    if "sent" in st or "recv" in st:
        L.append("\n📡 <b>ترافیک کل</b>\n" + _tree([f"ارسالی: {_hb(st.get('sent', 0))}", f"دریافتی: {_hb(st.get('recv', 0))}"]))
    if "tcp" in st or "udp" in st:
        L.append("\n🔌 <b>اتصالات</b>\n" + _tree([ltr(f"TCP: {st.get('tcp', 0)} | UDP: {st.get('udp', 0)}")]))
    if st.get("users") is not None:
        L.append("\n👥 <b>کاربران پنل</b>\n" + _tree([f"کل: {ltr(st['users'])}"] + ([f"فعال: {ltr(st['users_active'])}"] if st.get("users_active") is not None else [])))
    if st.get("xray_state"):
        run = str(st["xray_state"]).lower() == "running"
        L.append("\n🛡 <b>Xray</b>\n" + _tree([f"وضعیت: {'🟢 فعال' if run else '🔴 ' + esc(st['xray_state'])}"] + ([f"نسخه: {ltr(st['xray_version'])}"] if st.get("xray_version") else [])))
    tail = []
    if st.get("version"):
        tail.append(f"🏷 <b>نسخه پنل:</b> {ltr(st['version'])}")
    if st.get("uptime"):
        tail.append(f"⏱ <b>آپتایم:</b> {_uptime(st['uptime'])}")
    if tail:
        L.append("\n" + "\n".join(tail))
    if st.get("ipv4") or st.get("ipv6"):
        L.append("\n🔗 <b>IP عمومی</b>\n" + _tree([f"IPv4: {ltr(st.get('ipv4') or 'N/A')}", f"IPv6: {ltr(st.get('ipv6') or 'N/A')}"]))
    return "\n".join(L)


class VPN:
    def __init__(self, shop: "Shop"):
        self.shop = shop
        self.db = shop.db
        self._drivers: dict[int, tuple[tuple, vpn_panels.BasePanel]] = {}
        self._last_sync = 0.0
        self._last_health = 0.0
        self._health: dict = {}
        self._test_locks: dict[int, asyncio.Lock] = {}
        self._revoke_ts: dict[int, float] = {}
        from vpn_agent import AgentSales          # import تأخیری (جلوگیری از چرخه)
        self.agents = AgentSales(self)

    # ------------------------------------------------------------------ تنظیمات و درایور
    @property
    def s(self):
        return self.shop.s or {}

    def enabled(self) -> bool:
        return self.s.get("vpn_enabled", "1") == "1"

    def _flag(self, key, default="0") -> bool:
        return self.s.get(key, default) == "1"

    def _int(self, key, default=0) -> int:
        try:
            return int(float(self.s.get(key) or default))
        except (TypeError, ValueError):
            return default

    async def driver(self, panel_id: int):
        row = await self.db.get_vpn_panel(panel_id)
        if not row:
            raise PanelError("پنل پیدا نشد")
        sig = tuple(row.get(k) for k in ("type", "url", "username", "password", "token", "sub_url", "inbound_ids", "extra"))
        cur = self._drivers.get(panel_id)
        if cur and cur[0] == sig:
            return cur[1]
        if cur:
            try:
                await cur[1].close()
            except Exception:  # pragma: no cover
                pass
        d = vpn_panels.make_driver(row)
        self._drivers[panel_id] = (sig, d)
        return d

    async def forget(self, panel_id: int):
        cur = self._drivers.pop(panel_id, None)
        if cur:
            try:
                await cur[1].close()
            except Exception:  # pragma: no cover
                pass

    async def close(self):
        for pid in list(self._drivers):
            await self.forget(pid)

    async def test_panel(self, row: dict):
        """تست اتصال با پارامترهای دلخواه (بدون ذخیره). خروجی (ok, dict اطلاعات یا متن خطا)."""
        try:
            drv = vpn_panels.make_driver(row)
        except PanelError as e:
            return False, str(e)
        try:
            info = await drv.test()
            try:
                info["options_list"] = await drv.options()
            except Exception as e:  # noqa: BLE001
                info["options_list"] = []
                info["options_error"] = str(e)[:150]
            return True, info
        except (PanelError, Exception) as e:  # noqa: BLE001
            return False, str(e)[:300]
        finally:
            try:
                await drv.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def panel_missing(ptype: str, f: dict) -> list[str]:
        cls = vpn_panels.DRIVERS.get(ptype)
        if not cls:
            return ["type"]
        return [k for k in cls.NEEDS if not str(f.get(k) or "").strip()]

    # ------------------------------------------------------------------ پلن‌ها و قیمت
    async def plans_for(self, user: dict | None):
        out = []
        uid = (user or {}).get("id")
        level = (user or {}).get("vpn_level") or "f"
        panels = {x["id"]: x for x in await self.db.list_vpn_panels()}
        for p in await self.db.list_vpn_plans(active_only=True):
            pn = panels.get(p["panel_id"])
            if (p.get("utype") or "f") != level:
                continue
            if pn and (not pflag(pn, "show") or (uid and hidden_for(pn, uid)) or self.is_down(pn["id"])):
                continue
            p = dict(p)
            p["user_price"] = self.shop._personal(p["price"], user)
            p["out_of_stock"] = bool(pn and pn["type"] == "manual" and await self.db.vpn_stock_mode(pn["id"])
                                     and not await self.db.vpn_stock_free(pn["id"], p["id"]))
            out.append(p)
        return out

    async def _capacity_ok(self, panel: dict) -> bool:
        cap = panel.get("capacity") or 0
        return not cap or await self.db.panel_load(panel["id"]) < cap

    async def make_name(self, panel_id: int, user_id: int, asked: str | None = None) -> str:
        prefix = re.sub(r"[^a-z0-9_]", "", (self.s.get("vpn_prefix") or "vpn").lower())[:10] or "vpn"
        mode = self.s.get("vpn_name_mode") or "random"
        if asked:
            n = asked.strip().lower()
            if not valid_name(n):
                raise VpnError(N_("نام کاربری نامعتبر است (۳ تا ۲۴ حرف کوچک انگلیسی، عدد یا _)."))
            if await self.db.one("SELECT 1 x FROM vpn_services WHERE panel_id=? AND name=? AND status!='deleted'", (panel_id, n)):
                raise VpnError(N_("این نام کاربری قبلاً استفاده شده است."))
            return n
        base = f"{prefix}{user_id}" if mode == "id" else f"{prefix}_{secrets.token_hex(3)}"
        n, k = base, 1
        while await self.db.one("SELECT 1 x FROM vpn_services WHERE panel_id=? AND name=? AND status!='deleted'", (panel_id, n)):
            k += 1
            n = f"{base}_{k}" if mode == "id" else f"{prefix}_{secrets.token_hex(3)}"
        return n

    # ------------------------------------------------------------------ ساخت سفارش (قبل از پرداخت)
    MAX_PENDING = 15                                       # سقف سفارش‌های پرداخت‌نشده‌ی VPN برای هر کاربر (ضد اسپم)

    async def guard_pending(self, user_id: int):
        if await self.db.count_pending_vpn_orders(user_id) >= self.MAX_PENDING:
            raise VpnError(N_("سفارش‌های پرداخت‌نشده‌ی زیادی دارید؛ ابتدا آن‌ها را پرداخت کنید یا کمی صبر کنید تا منقضی شوند."))

    # ---------- ابزارهای کاربر روی سرویس خودش ----------
    def refund_info(self, svc):
        """(مجاز؟، مبلغ، پیام خطا) — بازگشت وجه فقط برای سفارش پرداخت‌شده، در مهلت و با سقف مصرف تنظیم‌شده."""
        if svc["is_test"] or not svc.get("order_id") or svc["status"] in ("deleted", "pending_manual"):
            return False, 0, "این سرویس قابل بازگشت وجه نیست."
        hours = self._int("vpn_refund_hours", 24)
        if hours <= 0:
            return False, 0, "بازگشت وجه غیرفعال است."
        if time.time() - svc["created_at"] > hours * 3600:
            return False, 0, f"مهلت بازگشت وجه ({hours} ساعت) گذشته است."
        mx = float(self.s.get("vpn_refund_max_used_pct") or 5)
        used_pct = (svc["used_bytes"] / svc["total_bytes"] * 100) if svc["total_bytes"] else (100 if svc["used_bytes"] > 50 * 2**20 else 0)
        if used_pct > mx:
            return False, 0, f"بیش از {mx:g}٪ حجم مصرف شده است."
        m = json.loads(svc["meta"] or "{}")
        if m.get("refunded"):
            return False, 0, "قبلاً بازگشت وجه شده است."
        return True, 0, ""

    async def user_refund(self, uid, sid):
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["user_id"] != uid:
            return False, "سرویس پیدا نشد."
        if not pflag(svc, "refund"):
            return False, "این قابلیت غیرفعال است."
        await self.sync_safe(svc)
        svc = await self.db.get_vpn_service(sid)
        ok, _a, why = self.refund_info(svc)
        if not ok:
            return False, why
        o = await self.db.get_order(svc["order_id"])
        if not o or o["user_id"] != uid or o["status"] not in ("done", "manual_pending"):
            return False, "سفارش قابل بازگشت نیست."
        ok, msg = await self.admin_delete(sid, refund=True, actor=f"user:{uid}")
        if ok:
            await self.shop.audit(f"user:{uid}", f"💸 کاربر <code>{uid}</code> بازگشت وجه سرویس <code>{esc(svc['name'])}</code> را انجام داد ({fmt(o['amount'])} تومان)")
        return ok, msg

    async def sync_safe(self, svc, min_gap=5):
        """همگام‌سازی لحظه‌ای با پنل (حداکثر هر min_gap ثانیه)؛ خطا نادیده گرفته می‌شود."""
        if time.time() - (svc.get("last_sync") or 0) < min_gap:
            return svc
        try:
            return await self.sync(svc, force=False)
        except Exception:  # noqa: BLE001
            return svc

    async def user_toggle(self, uid, sid):
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["user_id"] != uid or not pflag(svc, "toggle"):
            return False, "سرویس پیدا نشد."
        if svc["status"] not in ("active", "disabled"):
            return False, "در این وضعیت امکان تغییر نیست."
        if err := self.limit_error(svc, "toggle"):
            return False, err
        m = json.loads(svc["meta"] or "{}")
        gap = (plimit(svc, "toggle_gap") or self._int("vpn_toggle_cooldown_min", 10)) * 60
        if time.time() - m.get("toggle_at", 0) < gap:
            return False, f"لطفاً {int((m.get('toggle_at', 0) + gap - time.time()) // 60) + 1} دقیقه‌ی دیگر دوباره تلاش کنید."
        ok, msg = await self.admin_action(sid, "enable" if svc["status"] == "disabled" else "disable", actor=f"user:{uid}")
        if ok:
            svc = await self.db.get_vpn_service(sid)
            m = json.loads(svc["meta"] or "{}")
            m["toggle_at"] = int(time.time())
            m["n_toggle"] = int(m.get("n_toggle", 0)) + 1
            await self.db.update_vpn_service(sid, meta=m)
        return ok, msg

    async def user_set_note(self, uid, sid, text):
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["user_id"] != uid or not pflag(svc, "unote"):
            return False
        m = json.loads(svc["meta"] or "{}")
        m["unote"] = (text or "").strip()[:60]
        await self.db.update_vpn_service(sid, meta=m)
        return True

    async def user_transfer(self, uid, sid, target):
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["user_id"] != uid or not pflag(svc, "transfer"):
            return False, "سرویس پیدا نشد."
        if target == uid or not await self.db.get_user(target):
            return False, "کاربر مقصد پیدا نشد (او باید قبلاً ربات را استارت کرده باشد)."
        m = json.loads(svc["meta"] or "{}")
        mx = self._int("vpn_transfer_max", 1)
        if mx > 0 and m.get("transfers", 0) >= mx:
            return False, "سقف تعداد انتقال این سرویس تمام شده است."
        m["transfers"] = m.get("transfers", 0) + 1
        await self.db.update_vpn_service(sid, user_id=target, meta=m)
        await self.db.log(f"user:{uid}", "vpn_transfer", f"#{sid} -> {target}")
        await self.shop.audit(f"user:{uid}", f"🚚 انتقال سرویس <code>{esc(svc['name'])}</code> از <code>{uid}</code> به <code>{target}</code>")
        return True, "انجام شد."

    def limit_error(self, svc, kind: str) -> str:
        """kind: revoke | toggle | addon — اگر سقف تعداد هر سرویس (تنظیم پنل) پر شده باشد، پیام خطا برمی‌گرداند."""
        mx = plimit(svc, f"{kind}_max")
        n = int(json.loads(svc["meta"] or "{}").get(f"n_{kind}", 0))
        if mx and n >= mx:
            return N_("سقف مجاز این قابلیت برای سرویس شما پر شده است؛ با پشتیبانی تماس بگیرید.")
        return ""

    async def count_use(self, sid: int, kind: str):
        svc = await self.db.get_vpn_service(sid)
        if svc:
            m = json.loads(svc["meta"] or "{}")
            m[f"n_{kind}"] = int(m.get(f"n_{kind}", 0)) + 1
            await self.db.update_vpn_service(sid, meta=m)

    def revoke_wait(self, sid: int, cooldown: int = 120) -> int:
        """ثانیه‌ی باقی‌مانده تا مجاز شدن «لینک جدید» (۰ = مجاز، و همین الان ثبت می‌شود)؛ جلوی اسپم و ضربه‌زدن به پنل را می‌گیرد."""
        now = time.time()
        left = int(self._revoke_ts.get(sid, 0) + cooldown - now)
        if left > 0:
            return left
        self._revoke_ts[sid] = now
        if len(self._revoke_ts) > 5000:
            self._revoke_ts = {k: v for k, v in self._revoke_ts.items() if now - v < cooldown}
        return 0

    async def create_order(self, user_id: int, plan_id: int, name: str | None = None) -> int:
        if not self.enabled():
            raise VpnError(N_("فروش VPN فعال نیست."))
        await self.guard_pending(user_id)
        plan = await self.db.get_vpn_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        if not plan or not plan["active"] or not panel or not panel["enabled"]:
            raise VpnError(N_("این پلن دیگر در دسترس نیست."))
        if not pflag(panel, "show") or hidden_for(panel, user_id):
            raise VpnError(N_("این پلن دیگر در دسترس نیست."))
        if (plan.get("utype") or "f") != ((await self.db.get_user(user_id)) or {}).get("vpn_level", "f"):
            raise VpnError(N_("این پلن دیگر در دسترس نیست."))
        if not await self._capacity_ok(panel):
            raise VpnError(N_("ظرفیت این سرور تکمیل است؛ پلن دیگری انتخاب کنید."))
        if panel["type"] == "manual" and await self.db.vpn_stock_mode(panel["id"]) and not await self.db.vpn_stock_free(panel["id"], plan_id):
            raise VpnError(N_("موجودی این محصول تمام شده است."))
        if self.is_down(panel["id"]):
            raise VpnError(N_("این پنل موقتاً در دسترس نیست؛ کمی بعد دوباره تلاش کنید یا پلن دیگری را انتخاب کنید."))
        if name:
            name = await self.make_name(panel["id"], user_id, name)
        user = await self.db.get_user(user_id)
        price = self.shop._personal(plan["price"], user)
        ref = f"vpn:new:{plan_id}" + (f":{name}" if name else "")
        return await self.db.create_vpn_order(user_id, plan["title"], plan["days"], ref, price, plan["cost"])

    async def renew_order(self, user_id: int, service_id: int, plan_id: int) -> int:
        if not self.enabled():
            raise VpnError(N_("فروش VPN فعال نیست."))
        await self.guard_pending(user_id)
        svc = await self.db.get_vpn_service(service_id)
        plan = await self.db.get_vpn_plan(plan_id)
        if not svc or svc["user_id"] != user_id or svc["status"] in ("deleted", "pending_manual"):
            raise VpnError(N_("سرویس پیدا نشد."))
        if svc["is_test"]:
            raise VpnError(N_("اکانت تست قابل تمدید نیست؛ سرویس جدید بخرید."))
        if not pflag(svc, "renew"):
            raise VpnError(N_("تمدید برای این سرویس فعال نیست."))
        panel = await self.db.get_vpn_panel(svc["panel_id"])
        if not panel or not panel["enabled"]:
            raise VpnError(N_("این پلن برای تمدید این سرویس مجاز نیست."))
        if not plan or not plan["active"] or plan["panel_id"] != svc["panel_id"]:
            raise VpnError(N_("این پلن برای تمدید این سرویس مجاز نیست."))
        user = await self.db.get_user(user_id)
        price = self.shop._personal(plan["price"], user)
        return await self.db.create_vpn_order(user_id, f"{N_('تمدید')} {svc['name']} — {plan['title']}", plan["days"],
                                              f"vpn:renew:{service_id}:{plan_id}", price, plan["cost"])

    async def addon_order(self, user_id: int, service_id: int, addon_id: int) -> int:
        if not self._flag("vpn_allow_addon", "1"):
            raise VpnError(N_("خرید بسته‌ی اضافه فعال نیست."))
        svc = await self.db.get_vpn_service(service_id)
        ad = await self.db.get_vpn_addon(addon_id)
        if not svc or svc["user_id"] != user_id or svc["status"] in ("deleted", "pending_manual"):
            raise VpnError(N_("سرویس پیدا نشد."))
        if svc["is_test"]:
            raise VpnError(N_("برای اکانت تست نمی‌شود حجم/زمان اضافه خرید."))
        if not pflag(svc, "addon"):
            raise VpnError(N_("خرید بسته‌ی اضافه برای این سرویس فعال نیست."))
        if err := self.limit_error(svc, "addon"):
            raise VpnError(err)
        if not ad or not ad["active"]:
            raise VpnError(N_("این بسته دیگر در دسترس نیست."))
        await self.guard_pending(user_id)
        if ad["gb"] > 0 and not svc["total_bytes"] and svc["status"] != "pending_manual":
            raise VpnError(N_("حجم این سرویس نامحدود است؛ نیازی به خرید حجم نیست."))
        if ad["days"] > 0 and not svc["expires_at"]:
            raise VpnError(N_("این سرویس تاریخ انقضا ندارد؛ نیازی به خرید زمان نیست."))
        user = await self.db.get_user(user_id)
        price = self.shop._personal(ad["price"], user)
        return await self.db.create_vpn_order(user_id, f"{svc['name']} — {ad['title']}", ad["days"],
                                              f"vpn:addon:{service_id}:{addon_id}", price, ad["cost"])

    # ------------------------------------------------------------------ تحویل سفارش پرداخت‌شده
    async def fulfil(self, o: dict):
        """از Shop.fulfil صدا زده می‌شود (وضعیت سفارش delivering). هر خطا ← failed + اعلان ادمین توسط Shop."""
        kind = parse_ref(o["ref"])
        if len(kind) < 3 or kind[0] != "vpn":
            raise VpnError("bad vpn ref")
        if kind[1] == "new":
            await self._fulfil_new(o, int(kind[2]), kind[3] if len(kind) > 3 else None)
        elif kind[1] == "renew":
            await self._fulfil_renew(o, int(kind[2]), int(kind[3]))
        elif kind[1] == "addon":
            await self._fulfil_addon(o, int(kind[2]), int(kind[3]))
        elif kind[1] in ("agent", "arenew", "aaddon"):
            await self.agents.fulfil(o)
        else:
            raise VpnError("bad vpn ref")

    async def _fulfil_new(self, o, plan_id, asked):
        shop = self.shop
        existing = await self.db.vpn_service_by_order(o["id"])
        if existing and existing["status"] != "pending_manual":      # تلاش مجدد پس از ساخت موفق
            await self.send_service(existing["user_id"], existing, new=True)
            await shop.finish_order(o["id"], f"vpn #{existing['id']}")
            return
        plan = await self.db.get_vpn_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        if not plan or not panel:
            raise VpnError("plan/panel missing")
        drv = await self.driver(panel["id"])
        if drv.manual:
            st = await self.db.claim_vpn_stock(panel["id"], plan_id)          # تحویل خودکار از موجودی کانفیگ
            if st:
                try:
                    if existing:
                        sub, links = split_config(st["config"])
                        ok, _m = await self.deliver_manual(existing["id"], sub, "\n".join(links), actor="stock")
                        if not ok:
                            raise VpnError("deliver failed")
                        await self.db.release_vpn_stock(st["id"], existing["id"])
                    else:
                        sid = await self._stock_service(o["user_id"], panel, plan, o["id"], st)
                        svc = await self.db.get_vpn_service(sid)
                        await self.send_service(o["user_id"], svc, new=True)
                        await self.shop.finish_order(o["id"], f"vpn #{sid} (stock)")
                except Exception:
                    await self.db.release_vpn_stock(st["id"], None)              # برگرداندن کانفیگ به موجودی
                    raise
                left = await self.db.vpn_stock_free(panel["id"], plan_id)
                await self._notify_admin(f"🛡 فروش VPN (از موجودی)\nسفارش #{o['id']} | {esc(plan['title'])} | پنل: {esc(panel['name'])}\n"
                                         f"کاربر: <code>{o['user_id']}</code> | مبلغ: {fmt(o['amount'])} تومان\n📦 موجودی باقی‌مانده: {left}" + (" ⚠️ کم است!" if left <= 3 else ""))
                return
            if not existing:
                name = await self.make_name(panel["id"], o["user_id"], asked)
                await self.db.add_vpn_service(user_id=o["user_id"], panel_id=panel["id"], plan_id=plan_id, name=name, status="pending_manual",
                                              order_id=o["id"], title=plan["title"], total_bytes=int(plan["gb"] * GB),
                                              expires_at=0)
            await shop._to_manual(o["id"], "ساخت دستی سرویس VPN", f"🛡 سفارش VPN (پنل دستی «{esc(panel['name'])}») — موجودی کانفیگ ندارد؛ کانفیگ را از ربات (🛡 فروش VPN ← سرویس‌ها) یا وب‌پنل ثبت کنید، یا موجودی اضافه کنید تا خودکار تحویل شود:")
            return
        user_tg = o["user_id"]
        user, meta = None, None
        for attempt in range(3):
            name = existing["name"] if existing else await self.make_name(panel["id"], o["user_id"], asked if attempt == 0 else None)
            try:
                user, meta = await drv.create(name, plan["days"], plan["gb"], note=f"tg:{user_tg} order:{o['id']}", tg_id=user_tg, limit_ip=plan["ip_limit"])
                break
            except PanelError as e:
                if attempt < 2 and not asked and any(w in str(e).lower() for w in ("exist", "duplicate", "already", "تکراری")):
                    continue
                raise
        sid = await self._store_new(o, plan, panel, name, user, meta)
        svc = await self.db.get_vpn_service(sid)
        await self.send_service(o["user_id"], svc, new=True)
        await self._notify_admin(f"🛡 فروش VPN\nسفارش #{o['id']} | {esc(plan['title'])}\nپنل: {esc(panel['name'])} | نام: <code>{esc(name)}</code>\nکاربر: <code>{o['user_id']}</code> | مبلغ: {fmt(o['amount'])} تومان")
        await shop.finish_order(o["id"], f"vpn #{sid}")

    async def _store_new(self, o, plan, panel, name, user, meta):
        try:
            return await self.db.add_vpn_service(
                user_id=o["user_id"], panel_id=panel["id"], plan_id=plan["id"], name=name, status=user.status, expires_at=user.expire,
                total_bytes=user.total or int(plan["gb"] * GB), used_bytes=user.used, sub_url=user.sub_url, links=user.links or [],
                meta=meta or {}, order_id=o["id"], title=plan["title"], last_sync=int(time.time()))
        except Exception:
            # ساخت در پنل انجام شده ولی ثبت دیتابیس خطا داد؛ برای جلوگیری از سرویس یتیم، از پنل پاک می‌کنیم
            log.exception("vpn store failed; rolling back panel user %s", name)
            try:
                await (await self.driver(panel["id"])).delete(name, meta or {})
            except Exception:
                log.exception("rollback failed")
            raise

    async def _applied(self, svc, oid) -> bool:
        meta = json.loads(svc["meta"] or "{}")
        return oid in meta.get("applied", [])

    async def _mark_applied(self, svc, oid, new_meta: dict | None = None, **fields):
        meta = json.loads(svc["meta"] or "{}")
        if new_meta:
            meta.update(new_meta)
        meta["applied"] = (meta.get("applied", []) + [oid])[-30:]
        await self.db.update_vpn_service(svc["id"], meta=meta, **fields)

    async def _apply_user(self, svc, u: vpn_panels.PanelUser, **extra):
        await self.db.update_vpn_service(svc["id"], status=u.status, expires_at=u.expire, total_bytes=u.total, used_bytes=u.used,
                                         last_sync=int(time.time()), notified="", **extra)

    async def _fulfil_renew(self, o, sid, plan_id):
        svc = await self.db.get_vpn_service(sid)
        plan = await self.db.get_vpn_plan(plan_id)
        if not svc or not plan or svc["status"] == "deleted":
            raise VpnError("service/plan missing")
        if (await self.driver(svc["panel_id"])).manual:
            await self.shop._to_manual(o["id"], "تمدید دستی سرویس VPN", f"🔄 تمدید VPN (پنل دستی) — سرویس <code>{esc(svc['name'])}</code>، پلن «{esc(plan['title'])}»: {plan['days']} روز / {plan['gb']:g} گیگ. بعد از اعمال، «تحویل شد» را بزنید:")
            return
        if not await self._applied(svc, o["id"]):
            drv = await self.driver(svc["panel_id"])
            meta = json.loads(svc["meta"] or "{}")
            u = await drv.renew(svc["name"], meta, plan["days"], plan["gb"], stack=self._flag("vpn_stack", "1"))
            await self._mark_applied(svc, o["id"])
            await self._apply_user(svc, u, sub_url=u.sub_url or svc["sub_url"], plan_id=plan_id, title=plan["title"])
        svc = await self.db.get_vpn_service(sid)
        await self.shop.say(svc["user_id"], "🔄 سرویس <code>{name}</code> تمدید شد.\n{info}", name=esc(svc["name"]), info=await self.info_line(svc, svc["user_id"]))
        await self._notify_admin(f"🔄 تمدید VPN\nسفارش #{o['id']} | سرویس {esc(svc['name'])} | {esc(plan['title'])}\nکاربر: <code>{o['user_id']}</code> | مبلغ: {fmt(o['amount'])} تومان")
        await self.shop.finish_order(o["id"], f"renew #{sid}")

    async def _fulfil_addon(self, o, sid, addon_id):
        svc = await self.db.get_vpn_service(sid)
        ad = await self.db.get_vpn_addon(addon_id)
        if not svc or not ad or svc["status"] == "deleted":
            raise VpnError("service/addon missing")
        if (await self.driver(svc["panel_id"])).manual:
            await self.shop._to_manual(o["id"], "بسته‌ی دستی VPN", f"➕ بسته‌ی افزودنی VPN (پنل دستی) — سرویس <code>{esc(svc['name'])}</code>، «{esc(ad['title'])}»: {ad['gb']:g} گیگ / {ad['days']} روز. بعد از اعمال، «تحویل شد» را بزنید:")
            return
        if not await self._applied(svc, o["id"]):
            drv = await self.driver(svc["panel_id"])
            meta = json.loads(svc["meta"] or "{}")
            u = None
            if ad["gb"] > 0:
                u = await drv.add_traffic(svc["name"], meta, ad["gb"])
            if ad["days"] > 0:
                u = await drv.add_days(svc["name"], meta, ad["days"])
            await self._mark_applied(svc, o["id"])
            await self.count_use(sid, "addon")
            if u:
                await self._apply_user(svc, u)
        svc = await self.db.get_vpn_service(sid)
        await self.shop.say(svc["user_id"], "➕ بسته‌ی اضافه روی سرویس <code>{name}</code> اعمال شد.\n{info}", name=esc(svc["name"]), info=await self.info_line(svc, svc["user_id"]))
        await self._notify_admin(f"➕ بسته‌ی افزودنی VPN\nسفارش #{o['id']} | سرویس {esc(svc['name'])} | {esc(ad['title'])}\nکاربر: <code>{o['user_id']}</code> | مبلغ: {fmt(o['amount'])} تومان")
        await self.shop.finish_order(o["id"], f"addon #{sid}")

    async def _notify_admin(self, text):
        if self._flag("vpn_notify_admin", "1"):
            await self.shop.notify_admins(text, perm="vpn", topic="sales")

    # ------------------------------------------------------------------ اکانت تست
    def test_on(self, p) -> bool:
        """اکانت تست این پنل روشن است؟ مقدار خودِ پنل (وضعیت قابلیت‌ها) مهم است؛ اگر هنوز برای پنل تنظیم نشده، تنظیم قدیمی کلی (vpn_test_enabled / vpn_test_panel) خوانده می‌شود."""
        ex = pextra(p)
        if "test" in ex:
            return str(ex["test"]) != "0"
        if not self._flag("vpn_test_enabled"):
            return False
        only = self._int("vpn_test_panel")
        return not only or only == p["id"]

    def test_params(self, p) -> tuple:
        """(روز، حجم GB، تعداد تست مجاز برای هر کاربر روی این پنل)"""
        ex = pextra(p)
        try:
            days = max(int(float(ex.get("test_days") or self._int("vpn_test_days", 1) or 1)), 1)
        except (TypeError, ValueError):
            days = 1
        try:
            gb = float(ex.get("test_gb") if ex.get("test_gb") not in (None, "") else (self.s.get("vpn_test_gb") or 0))
        except (TypeError, ValueError):
            gb = 0.0
        try:
            mx = max(int(float(ex.get("test_max") or 1)), 1)
        except (TypeError, ValueError):
            mx = 1
        return days, max(gb, 0.0), mx

    async def test_panels(self, user_id: int) -> list:
        """[(پنل, استفاده‌شده؟)] همه‌ی پنل‌هایی که اکانت تستشان روشن است و کاربر می‌بیند؛ حتی اگر کاربر سهمش را گرفته باشد (دکمه نمی‌پرد؛ فقط پیام «استفاده کردید»)."""
        if not self.enabled():
            return []
        out = []
        for p in await self.db.list_vpn_panels(only_enabled=True):
            if not (self.test_on(p) and pflag(p, "show") and not hidden_for(p, user_id) and not self.is_down(p["id"])):
                continue
            used = await self.db.vpn_test_count(user_id, p["id"]) >= self.test_params(p)[2]
            if p["type"] == "manual" and not used and await self.db.vpn_stock_free(p["id"], -1) <= 0:
                continue
            out.append((p, used))
        return out

    async def test_options(self, user_id: int) -> list:
        """پنل‌هایی که کاربر هنوز می‌تواند روی آن‌ها اکانت تست بگیرد."""
        return [p for p, used in await self.test_panels(user_id) if not used]

    async def test_visible(self, user_id: int) -> bool:
        """دکمه‌ی «اکانت تست» در منو/مینی‌اپ دیده شود؟ (حتی بعد از گرفتن تست)"""
        return bool(await self.test_panels(user_id))

    async def can_test(self, user_id: int) -> bool:
        return bool(await self.test_options(user_id))

    async def create_test(self, user_id: int, panel_id: int | None = None) -> int:
        if not self.enabled():
            raise VpnError(N_("اکانت تست فعال نیست."))
        lock = self._test_locks.setdefault(user_id, asyncio.Lock())
        async with lock:                     # دو کلیک همزمان نباید دو اکانت تست بسازد
            allp = await self.test_panels(user_id)
            if not allp:
                raise VpnError(N_("اکانت تست فعال نیست."))
            if panel_id:
                hit = next(((p, u) for p, u in allp if p["id"] == panel_id), None)
                if not hit:
                    raise VpnError(N_("اکانت تست در حال حاضر در دسترس نیست."))
                panel, used = hit
                if used:
                    raise VpnError(N_("شما قبلاً از اکانت تست این پنل استفاده کرده‌اید."))
            else:
                opts = [p for p, u in allp if not u]
                if not opts:
                    raise VpnError(N_("شما قبلاً از اکانت تست استفاده کرده‌اید."))
                panel = opts[0]
            days, gb, _mx = self.test_params(panel)
            if panel["type"] == "manual":                       # تست از موجودی «تست»
                st = await self.db.claim_vpn_stock(panel["id"], -1)
                if not st:
                    raise VpnError(N_("اکانت تست در حال حاضر در دسترس نیست."))
                try:
                    sid = await self._stock_service(user_id, panel, None, None, st, test=True, days=days, gb=gb)
                except Exception:
                    await self.db.release_vpn_stock(st["id"], None)
                    raise
                await self.db.log("vpn", "test_account", f"user {user_id} stock {st['id']}")
                return sid
            drv = await self.driver(panel["id"])
            for attempt in range(3):
                name = await self.make_name(panel["id"], user_id)
                try:
                    u, meta = await drv.create(name, days, gb, note=f"tg:{user_id} test", tg_id=user_id)
                    break
                except PanelError as e:
                    if attempt < 2 and any(w in str(e).lower() for w in ("exist", "duplicate", "already")):
                        continue
                    log.warning("vpn test create failed: %s", e)
                    raise VpnError(N_("ساخت اکانت تست با خطا روبه‌رو شد؛ بعداً دوباره تلاش کنید.")) from e
            try:
                sid = await self.db.add_vpn_service(user_id=user_id, panel_id=panel["id"], name=name, status=u.status, expires_at=u.expire,
                                                    total_bytes=u.total or int(gb * GB), used_bytes=u.used, sub_url=u.sub_url, links=u.links or [],
                                                    meta=meta or {}, is_test=1, title="Test", last_sync=int(time.time()))
            except Exception:
                try:
                    await drv.delete(name, meta or {})
                except Exception:
                    pass
                raise
        await self.db.log("vpn", "test_account", f"user {user_id} {name}")
        return sid

    # ------------------------------------------------------------------ همگام‌سازی
    async def sync(self, svc, force=True):
        """وضعیت/مصرف را از پنل می‌خواند و در دیتابیس ذخیره می‌کند. خروجی: ردیف تازه."""
        if svc["status"] in ("deleted", "pending_manual"):
            return svc
        drv = await self.driver(svc["panel_id"])
        if getattr(drv, "manual", False):
            return svc
        meta = json.loads(svc["meta"] or "{}")
        try:
            u = await drv.info(svc["name"], meta)
        except PanelError as e:
            await self.db.update_vpn_service(svc["id"], note=str(e)[:200], last_sync=int(time.time()))
            if force:
                raise
            return svc
        f = dict(status=u.status, expires_at=u.expire, total_bytes=u.total, used_bytes=u.used, last_sync=int(time.time()), note="")
        if u.sub_url:
            f["sub_url"] = u.sub_url
        if u.links:
            f["links"] = u.links
        meta.update(online_at=u.online_at or 0, sub_updated=getattr(u, "sub_updated", 0) or 0, ua=getattr(u, "ua", "") or "")
        f["meta"] = meta
        await self.db.update_vpn_service(svc["id"], **f)
        return await self.db.get_vpn_service(svc["id"])

    async def tick(self):
        try:
            await self.agents.tick()
        except Exception:
            log.exception("agent tick failed")
        try:
            await self.health_tick()
        except Exception:
            log.exception("vpn health tick failed")
        if not self.enabled() and not await self.db.one("SELECT 1 x FROM vpn_services LIMIT 1"):
            return
        every = max(self._int("vpn_sync_min", 10), 1) * 60
        if time.time() - self._last_sync < min(every, 60):
            return
        self._last_sync = time.time()
        for svc in await self.db.vpn_syncable(int(time.time()) - every, limit=60):
            try:
                svc = await self.sync(svc, force=False)
                await self._remind(svc)
            except Exception:
                log.exception("vpn sync %s failed", svc["id"])
        await self._autodelete()

    # ------------------------------------------------------------------ پایش سلامت پنل‌ها
    def down_ids(self) -> dict:
        try:
            d = json.loads(self.s.get("vpn_down") or "{}")
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    def is_down(self, panel_id) -> bool:
        """پنلِ قطع که ادمین «بستن خودکار فروش» را روشن کرده است."""
        return self._flag("vpn_health_autoclose") and str(panel_id) in self.down_ids()

    async def _set_down(self, panel_id, down: bool):
        d = self.down_ids()
        if down:
            d[str(panel_id)] = int(time.time())
        else:
            d.pop(str(panel_id), None)
        await self.shop.set_setting("vpn_down", json.dumps(d), "system")

    async def health_tick(self, force=False):
        if not force and not self._flag("vpn_health_on", "1"):
            return
        every = max(self._int("vpn_health_min", 5), 1) * 60
        if not force and time.time() - self._last_health < every:
            return
        self._last_health = time.time()
        for p in await self.db.list_vpn_panels(only_enabled=True):
            if p["type"] == "manual":
                continue
            try:
                await self.check_panel_health(p)
            except Exception:
                log.exception("health check %s failed", p["id"])

    async def check_panel_health(self, p: dict):
        pid = p["id"]
        h = self._health.setdefault(pid, {"fails": 0, "down": str(pid) in self.down_ids(), "last": 0, "ok": None, "err": "", "m": {}})
        ok, err, m = True, "", {}
        try:
            drv = await self.driver(pid)
            await asyncio.wait_for(drv.test(), 25)
            try:
                m = await asyncio.wait_for(drv.status(), 20) or {}
            except Exception:  # noqa: BLE001 - مانیتورینگ همه‌ی پنل‌ها پشتیبانی نمی‌شود؛ اتصال سالم است
                m = {}
        except Exception as e:  # noqa: BLE001
            ok, err = False, (str(e) or type(e).__name__)[:200]
        h.update(last=int(time.time()), ok=ok, err=err, m=m)
        name = esc(p["name"])
        if not ok:
            h["fails"] += 1
            if h["fails"] >= max(self._int("vpn_health_fails", 2), 1) and not h["down"]:
                h["down"] = True
                closed = self._flag("vpn_health_autoclose")
                if closed:
                    await self._set_down(pid, True)
                await self._notify_alert(f"🔴 <b>پنل «{name}» از دسترس خارج شد</b>\n<code>{esc(err)}</code>"
                                         + ("\n⛔️ فروش این پنل تا برگشتن خودکار بسته شد." if closed else ""))
            return
        h["fails"] = 0
        if h["down"] or str(pid) in self.down_ids():
            h["down"] = False
            was_closed = str(pid) in self.down_ids()
            if was_closed:
                await self._set_down(pid, False)
            await self._notify_alert(f"🟢 <b>پنل «{name}» دوباره در دسترس است</b>" + ("\n✅ فروش این پنل باز شد." if was_closed else ""))
        await self._check_thresholds(p, h, m)

    @staticmethod
    def _pct(used, total):
        try:
            used, total = float(used), float(total)
            return used * 100 / total if total > 0 else None
        except (TypeError, ValueError):
            return None

    async def _check_thresholds(self, p, h, m):
        pid, name = p["id"], esc(p["name"])
        checks = []
        try:
            cpu = float(m.get("cpu")) if m.get("cpu") is not None else None
        except (TypeError, ValueError):
            cpu = None
        checks.append(("cpu", cpu, self._int("vpn_health_cpu", 90), "پردازنده (CPU)"))
        checks.append(("mem", self._pct(m.get("mem_used"), m.get("mem_total")), self._int("vpn_health_mem", 90), "حافظه (RAM)"))
        checks.append(("disk", self._pct(m.get("disk_used"), m.get("disk_total")), self._int("vpn_health_disk", 90), "دیسک"))
        cap = p.get("capacity") or 0
        if cap:
            checks.append(("cap", await self.db.panel_load(pid) * 100 / cap, self._int("vpn_health_cap", 90), "ظرفیت کاربران"))
        for key, val, limit, label in checks:
            if val is None or limit <= 0 or val < limit:
                continue
            if self.shop.warn_once(f"vpnh:{pid}:{key}", 6):
                await self._notify_alert(f"⚠️ <b>پنل «{name}»</b>: {label} در حد <b>{val:.0f}٪</b> است (آستانه {limit}٪).")

    async def _notify_alert(self, text):
        await self.shop.notify_admins(text, perm="vpn", topic="alerts")

    def health_overview(self) -> list:
        return [(pid, h) for pid, h in self._health.items()]

    # ------------------------------------------------------------------ تمدید خودکار / تمدید یک‌کلیکی / تخفیف بازگشت
    def _can_auto(self, svc) -> bool:
        """گزینه‌ی تمدید خودکار فقط برای سرویس‌های واقعی پنل‌های غیر دستی که تمدیدشان فعال است."""
        if not self._flag("vpn_autorenew_on", "1") or svc["is_test"] or not svc["plan_id"] or svc["status"] in ("deleted", "pending_manual"):
            return False
        return pflag(svc, "renew")

    async def set_auto(self, svc, on: bool):
        meta = json.loads(svc["meta"] or "{}")
        if on:
            meta["auto"] = 1
        else:
            meta.pop("auto", None)
        await self.db.update_vpn_service(svc["id"], meta=meta)

    async def quick_kb(self, svc, tr):
        """دکمه‌های پیام یادآوری: تمدید همین پلن با یک کلیک + مشاهده."""
        rows = []
        plan = await self.db.get_vpn_plan(svc["plan_id"]) if svc["plan_id"] else None
        if plan and plan["active"] and plan["panel_id"] == svc["panel_id"] and pflag(svc, "renew") and not svc["is_test"]:
            user = await self.db.get_user(svc["user_id"])
            rows.append([btn(f"{tr('⚡ تمدید همین پلن')} | {fmt(self.shop._personal(plan['price'], user))} {tr('ت')}", f"vpn:qk:{svc['id']}", role="action")])
        rows.append([btn(tr("🔄 تمدید / مشاهده"), f"vpn:s:{svc['id']}", role="action")])
        return ikb(rows)

    async def _auto_renew(self, svc, notified: set) -> bool:
        """اگر کاربر تمدید خودکار را روشن کرده و موجودی کافی است، از کیف پول تمدید می‌کند. خروجی: True اگر تمدید شد."""
        meta = json.loads(svc["meta"] or "{}")
        if not meta.get("auto") or not self._can_auto(svc):
            return False
        left = days_left(svc["expires_at"])
        if not (svc["status"] == "expired" or (left is not None and left <= max(self._int("vpn_autorenew_days", 1), 0))):
            return False
        uid = svc["user_id"]
        plan = await self.db.get_vpn_plan(svc["plan_id"])
        tr = await self.shop.tr_for(uid)
        kb = await self.quick_kb(svc, tr)
        if not plan or not plan["active"] or plan["panel_id"] != svc["panel_id"]:
            if "autofail" not in notified:
                await self.shop.say(uid, "⚠️ تمدید خودکار سرویس <code>{name}</code> انجام نشد: پلن قبلی دیگر موجود نیست. لطفاً دستی تمدید کنید.", kb, name=esc(svc["name"]))
                notified.add("autofail")
                return None
            return False
        user = await self.db.get_user(uid)
        price = self.shop._personal(plan["price"], user)
        if (user["balance"] or 0) < price:
            if "autofail" not in notified:
                await self.shop.say(uid, "⚠️ تمدید خودکار سرویس <code>{name}</code> انجام نشد: موجودی کیف پول کافی نیست (نیاز: {p} تومان). کیف پول را شارژ کنید تا خودکار تمدید شود.", kb, name=esc(svc["name"]), p=fmt(price))
                notified.add("autofail")
                return None
            return False
        try:
            oid = await self.renew_order(uid, svc["id"], plan["id"])
        except VpnError:
            return False
        if await self.shop.pay_with_wallet(oid, uid):
            await self.db.log("auto", "vpn_autorenew", f"#{svc['id']} order {oid}")
            return True
        return False

    async def _winback(self, svc, notified: set) -> bool:
        """چند روز بعد از انقضا، یک کد تخفیف شخصی و یک‌بارمصرف می‌فرستد (اگر ادمین روشن کرده باشد)."""
        if not self._flag("vpn_winback_on") or svc["is_test"] or svc["status"] != "expired" or "winback" in notified:
            return False
        pct = min(max(self._int("vpn_winback_percent", 0), 0), 100)
        after = max(self._int("vpn_winback_after", 2), 0)
        if pct <= 0 or not svc["expires_at"] or time.time() - svc["expires_at"] < after * 86400:
            return False
        valid = max(self._int("vpn_winback_valid", 3), 1)
        code = f"WB{secrets.token_hex(3).upper()}"
        if not await self.db.create_discount(code, pct, 0, 1, int(time.time()) + valid * 86400, per_user=1, applies_to="vpn",
                                             users=[svc["user_id"]], note=f"vpn winback #{svc['id']}"):
            return False
        tr = await self.shop.tr_for(svc["user_id"])
        await self.shop.say(svc["user_id"], "🎁 دلتنگتان شدیم! برای تمدید سرویس <code>{name}</code> یک کد تخفیف <b>{p}٪</b> مخصوص شما ساخته شد:\n<code>{code}</code>\n(یک‌بار مصرف، تا {d} روز معتبر؛ هنگام پرداخت وارد کنید)",
                            await self.quick_kb(svc, tr), name=esc(svc["name"]), p=pct, code=code, d=valid)
        notified.add("winback")
        return True

    async def _remind(self, svc):
        if svc["status"] not in ("active", "limited", "expired"):
            return
        notified = set(filter(None, (svc["notified"] or "").split(",")))
        before = set(notified)
        r = await self._auto_renew(svc, notified)
        if r:
            return                                     # تمدید شد؛ پیام تمدید خودش فرستاده می‌شود
        if await self._winback(svc, notified) or notified != before:
            await self.db.update_vpn_service(svc["id"], notified=",".join(sorted(notified)))
        svc = await self.db.get_vpn_service(svc["id"])
        notified = set(filter(None, (svc["notified"] or "").split(",")))
        new = set(notified)
        uid = svc["user_id"]
        rd, ru = self._int("vpn_remind_days"), self._int("vpn_remind_usage")
        left = days_left(svc["expires_at"])
        tr = await self.shop.tr_for(uid)
        kb = await self.quick_kb(svc, tr)
        sent = False
        if svc["status"] == "expired" or (left is not None and left <= 0):
            if "expired" not in notified:
                await self.shop.say(uid, "⏰ سرویس <code>{name}</code> منقضی شد. برای ادامه تمدید کنید.", kb, name=esc(svc["name"]))
                new.add("expired")
                sent = True
        elif rd and left is not None and left <= rd and "time" not in notified:
            await self.shop.say(uid, "⏳ فقط <b>{d}</b> روز تا پایان سرویس <code>{name}</code> مانده است.", kb, d=left, name=esc(svc["name"]))
            new.add("time")
            sent = True
        if svc["status"] == "limited":
            if "limited" not in notified:
                await self.shop.say(uid, "📉 حجم سرویس <code>{name}</code> تمام شد. می‌توانید تمدید کنید یا حجم اضافه بخرید.", kb, name=esc(svc["name"]))
                new.add("limited")
                sent = True
        elif ru and svc["total_bytes"] and svc["used_bytes"] * 100 >= svc["total_bytes"] * ru and "usage" not in notified:
            await self.shop.say(uid, "📊 بیش از <b>{p}٪</b> حجم سرویس <code>{name}</code> مصرف شده است.", kb, p=ru, name=esc(svc["name"]))
            new.add("usage")
            sent = True
        if sent:
            await self.db.update_vpn_service(svc["id"], notified=",".join(sorted(new)))

    async def _autodelete(self):
        d = self._int("vpn_autodelete_days")
        if d <= 0:
            return
        cut = int(time.time()) - d * 86400
        rows = await self.db.all("SELECT * FROM vpn_services WHERE status='expired' AND expires_at>0 AND expires_at<? LIMIT 20", (cut,))
        for svc in rows:
            try:
                await self.admin_delete(svc["id"], actor="auto")
            except Exception:
                log.exception("vpn autodelete %s failed", svc["id"])

    # ------------------------------------------------------------------ عملیات ادمین (مشترک بین ربات و وب)
    async def admin_action(self, sid: int, act: str, value=None, actor="admin"):
        """act: sync | add_days | add_gb | reset | enable | disable | revoke | delete | note | set_owner.
        خروجی (ok, پیام فارسی)."""
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["status"] == "deleted":
            return False, N_("سرویس پیدا نشد.")
        await self.db.log(actor, f"vpn_{act}", f"#{sid} {svc['name']}")
        if act == "note":
            await self.db.update_vpn_service(sid, note=str(value or "")[:300])
            return True, N_("ثبت شد.")
        if svc["status"] == "pending_manual":
            return False, N_("این سرویس هنوز تحویل داده نشده است.")
        drv = await self.driver(svc["panel_id"])
        meta = json.loads(svc["meta"] or "{}")
        if drv.manual and act != "delete":
            return await self._manual_action(svc, act, value)
        try:
            if act == "sync":
                await self.sync(svc)
                return True, N_("همگام شد.")
            if act == "add_days":
                u = await drv.add_days(svc["name"], meta, int(value))
            elif act == "add_gb":
                u = await drv.add_traffic(svc["name"], meta, float(value))
            elif act == "reset":
                await drv.reset_usage(svc["name"], meta)
                u = await drv.info(svc["name"], meta)
            elif act in ("enable", "disable"):
                await drv.set_enabled(svc["name"], meta, act == "enable")
                u = await drv.info(svc["name"], meta)
            elif act == "revoke":
                sub, meta2 = await drv.revoke(svc["name"], meta)
                meta.update(meta2 or {})
                await self.db.update_vpn_service(sid, sub_url=sub, meta=meta)
                u = await drv.info(svc["name"], meta)
            elif act == "delete":
                return await self.admin_delete(sid, actor=actor)
            else:
                return False, N_("عملیات نامعتبر.")
        except (PanelError, ValueError) as e:
            return False, f"{N_('خطای پنل')}: {str(e)[:200]}"
        f = dict(status=u.status, expires_at=u.expire, total_bytes=u.total, used_bytes=u.used, last_sync=int(time.time()))
        if u.sub_url:
            f["sub_url"] = u.sub_url
        if u.links:
            f["links"] = u.links
        if act in ("add_days", "add_gb", "reset", "enable"):
            f["notified"] = ""
        await self.db.update_vpn_service(sid, **f)
        return True, N_("انجام شد.")

    async def _manual_action(self, svc, act, value):
        """پنل «فروش دستی» API ندارد؛ فقط اطلاعات داخل ربات به‌روز می‌شود (ادمین خودش در پنل واقعی اعمال می‌کند)."""
        f = {}
        if act == "add_days":
            base = svc["expires_at"] if svc["expires_at"] > time.time() else int(time.time())
            f = dict(expires_at=base + int(value) * 86400, status="active")
        elif act == "add_gb":
            f = dict(total_bytes=(svc["total_bytes"] or 0) + int(float(value) * GB))
        elif act == "reset":
            f = dict(used_bytes=0)
        elif act in ("enable", "disable"):
            f = dict(status="active" if act == "enable" else "disabled")
        elif act == "sync":
            return True, N_("پنل دستی همگام نمی‌شود.")
        else:
            return False, N_("این عملیات برای پنل دستی ممکن نیست.")
        await self.db.update_vpn_service(svc["id"], notified="", **f)
        return True, N_("در ربات ثبت شد؛ در پنل واقعی هم اعمال کنید.")

    async def admin_delete(self, sid: int, refund=False, actor="admin"):
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["status"] == "deleted":
            return False, N_("سرویس پیدا نشد.")
        if svc["status"] != "pending_manual":
            try:
                drv = await self.driver(svc["panel_id"])
                if not drv.manual:
                    await drv.delete(svc["name"], json.loads(svc["meta"] or "{}"))
            except PanelError as e:
                return False, f"{N_('خطای پنل')}: {str(e)[:200]}"
        await self.db.update_vpn_service(sid, status="deleted")
        msg = N_("حذف شد.")
        if refund and svc.get("order_id"):
            o = await self.db.get_order(svc["order_id"])
            if o and o["status"] in ("done", "manual_pending") and await self.db.transition(o["id"], ["done", "manual_pending"], "refunded", "حذف سرویس + بازگشت وجه"):
                await self.db.add_balance(o["user_id"], o["amount"], f"refund: order {o['id']}", ref_key=f"refund:{o['id']}")
                await self.shop.say(o["user_id"], "↩️ مبلغ سفارش #{oid} ({amount} تومان) به کیف پول شما برگشت.", oid=o["id"], amount=fmt(o["amount"]))
                msg = N_("حذف شد و مبلغ به کیف پول برگشت.")
        await self.db.log(actor, "vpn_delete", f"#{sid} {svc['name']}")
        return True, msg

    async def _stock_service(self, user_id, panel, plan, order_id, st, test=False, days=0, gb=0.0):
        """از یک ردیف موجودی، سرویس فعال می‌سازد. خروجی: service_id."""
        base = re.sub(r"\s+", " ", st["name"] or "").strip()[:60] or await self.make_name(panel["id"], user_id)
        name, k = base, 1
        while await self.db.one("SELECT 1 x FROM vpn_services WHERE panel_id=? AND name=? AND status!='deleted'", (panel["id"], name)):
            k += 1
            name = f"{base}-{k}"
        sub, links = split_config(st["config"])
        d = int(days or (plan["days"] if plan else 0))
        g = float(gb or (plan["gb"] if plan else 0))
        now = int(time.time())
        sid = await self.db.add_vpn_service(
            user_id=user_id, panel_id=panel["id"], plan_id=plan["id"] if plan else None, name=name, status="active",
            expires_at=now + d * 86400 if d else 0, total_bytes=int(g * GB), sub_url=sub, links=links, meta={"stock": st["id"]},
            order_id=order_id, is_test=1 if test else 0, title=plan["title"] if plan else "Test", last_sync=now)
        await self.db.release_vpn_stock(st["id"], sid)
        return sid

    async def stock_add(self, panel_id: int, plan_id: int, text: str) -> tuple[int, int, int]:
        """افزودن موجودی؛ خروجی (اضافه‌شده، تکراری، تحویل‌شده‌به‌سفارش‌های منتظر)."""
        items = parse_stock_text(text)
        fresh, dup = [], 0
        seen = set()
        for name, cfg in items:
            if cfg in seen or await self.db.one("SELECT 1 x FROM vpn_stock WHERE panel_id=? AND config=?", (panel_id, cfg)):
                dup += 1
                continue
            seen.add(cfg)
            fresh.append((name, cfg))
        n = await self.db.add_vpn_stock(panel_id, plan_id, fresh) if fresh else 0
        delivered = await self.deliver_pending_from_stock(panel_id) if n and plan_id >= 0 else 0
        return n, dup, delivered

    async def deliver_pending_from_stock(self, panel_id: int) -> int:
        n = 0
        for svc in await self.db.all("SELECT * FROM vpn_services WHERE panel_id=? AND status='pending_manual' ORDER BY id", (panel_id,)):
            st = await self.db.claim_vpn_stock(panel_id, svc["plan_id"] or 0)
            if not st:
                continue
            sub, links = split_config(st["config"])
            try:
                ok, _m = await self.deliver_manual(svc["id"], sub, "\n".join(links), actor="stock")
            except Exception:  # noqa: BLE001
                log.exception("stock deliver failed")
                ok = False
            if ok:
                await self.db.release_vpn_stock(st["id"], svc["id"])
                n += 1
            else:
                await self.db.release_vpn_stock(st["id"], None)
        return n

    async def deliver_manual(self, sid: int, sub_url="", links_text="", days=0, gb=0.0, actor="admin"):
        """تحویل سرویس «پنل دستی»: ادمین لینک/کانفیگ را وارد می‌کند و برای کاربر ارسال می‌شود."""
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["status"] != "pending_manual":
            return False, N_("سرویس منتظر تحویل نیست.")
        links = [x.strip() for x in re.split(r"[\r\n]+", links_text or "") if x.strip()]
        if not sub_url and not links:
            return False, N_("لینک اشتراک یا کانفیگ را وارد کنید.")
        plan = await self.db.get_vpn_plan(svc["plan_id"]) if svc["plan_id"] else None
        d = int(days or (plan["days"] if plan else 0))
        total = int(float(gb) * GB) if gb else svc["total_bytes"]
        await self.db.update_vpn_service(sid, status="active", sub_url=sub_url.strip(), links=links, expires_at=int(time.time()) + d * 86400 if d else 0,
                                         total_bytes=total, last_sync=int(time.time()))
        svc = await self.db.get_vpn_service(sid)
        await self.send_service(svc["user_id"], svc, new=True)
        if svc.get("order_id"):
            await self.shop.finish_order(svc["order_id"], f"manual vpn #{sid}")
        await self.db.log(actor, "vpn_deliver", f"#{sid}")
        return True, N_("برای کاربر ارسال شد.")

    async def admin_create(self, user_id: int, plan_id: int, name: str | None = None, actor="admin"):
        """ساخت مستقیم سرویس برای کاربر توسط ادمین (بدون پرداخت). خروجی (ok, پیام, service_id)."""
        plan = await self.db.get_vpn_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        if not plan or not panel:
            return False, N_("پلن پیدا نشد."), None
        if not await self.db.get_user(user_id):
            return False, N_("کاربر پیدا نشد."), None
        drv = await self.driver(panel["id"])
        try:
            nm = await self.make_name(panel["id"], user_id, name or None)
        except VpnError as e:
            return False, str(e), None
        if drv.manual:
            sid = await self.db.add_vpn_service(user_id=user_id, panel_id=panel["id"], plan_id=plan_id, name=nm, status="pending_manual",
                                                title=plan["title"], total_bytes=int(plan["gb"] * GB))
            return True, N_("سرویس ساخته شد؛ کانفیگ را ثبت کنید."), sid
        try:
            u, meta = await drv.create(nm, plan["days"], plan["gb"], note=f"tg:{user_id} admin", tg_id=user_id, limit_ip=plan["ip_limit"])
        except PanelError as e:
            return False, f"{N_('خطای پنل')}: {str(e)[:200]}", None
        sid = await self.db.add_vpn_service(user_id=user_id, panel_id=panel["id"], plan_id=plan_id, name=nm, status=u.status, expires_at=u.expire,
                                            total_bytes=u.total or int(plan["gb"] * GB), used_bytes=u.used, sub_url=u.sub_url, links=u.links or [],
                                            meta=meta or {}, title=plan["title"], last_sync=int(time.time()))
        await self.db.log(actor, "vpn_create", f"#{sid} user {user_id}")
        svc = await self.db.get_vpn_service(sid)
        await self.send_service(user_id, svc, new=True)
        return True, N_("سرویس ساخته و برای کاربر ارسال شد."), sid

    # ------------------------------------------------------------------ نمایش برای کاربر
    async def info_line(self, svc, uid) -> str:
        tr = await self.shop.tr_for(uid)
        left = days_left(svc["expires_at"])
        used = ltr(f"{fmt_gb(svc['used_bytes'])} / " + (fmt_gb(svc["total_bytes"]) if svc["total_bytes"] else tr("نامحدود")))
        t = tr("نامحدود") if left is None else tr("{d} روز", d=left)
        return f"{tr(STATUS_FA.get(svc['status'], svc['status']))} | " + tr("حجم: {u}", u=used) + " | " + tr("زمان باقی‌مانده: {t}", t=t)

    async def service_text(self, svc, tr, compact=False, all_cfg=False) -> str:
        """compact: فقط تعداد کانفیگ (برای کپشن عکس) · all_cfg: همه‌ی کانفیگ‌ها زیر لینک اشتراک (تا ۱۰۰ عدد) · وگرنه ۶ کانفیگ."""
        left = days_left(svc["expires_at"])
        total = svc["total_bytes"]
        lines = [f"🛡 <b>{esc(svc['title'] or tr('سرویس VPN'))}</b>",
                 tr("نام کاربری: {n}", n=f"<code>{esc(svc['name'])}</code>"),
                 tr("وضعیت: {s}", s=tr(STATUS_FA.get(svc["status"], svc["status"])))]
        if svc["status"] != "pending_manual":
            lines.append(tr("مصرف: {u}", u=ltr(f"{fmt_gb(svc['used_bytes'])} / " + (fmt_gb(total) if total else tr("نامحدود")))) + f"\n{bar(svc['used_bytes'], total)}")
            if total:
                rest = max(total - svc["used_bytes"], 0)
                lines.append(tr("حجم باقی‌مانده: {r}", r=ltr(f"{fmt_gb(rest)} ({int(rest * 100 / total)}%)")))
            lines.append(tr("زمان باقی‌مانده: {t}", t=tr("نامحدود") if left is None else tr("{d} روز", d=left)))
            if svc["expires_at"]:
                lines.append(tr("📅 تاریخ پایان: {d} ({t})", d=ltr(jdate(svc["expires_at"])), t=left_text(svc["expires_at"], tr)))
            if svc.get("panel_name") and svc.get("panel_type") != "manual":
                lines.append(tr("🌍 موقعیت: {p}", p=esc(svc["panel_name"])))
        else:
            lines.append(tr("سرویس شما پس از آماده‌سازی توسط پشتیبانی ارسال می‌شود."))
        m = json.loads(svc["meta"] or "{}")
        if m.get("unote"):
            lines.append(tr("📝 یادداشت: {n}", n=esc(m["unote"])))
        if svc["status"] != "pending_manual":
            lines.append(tr("🟢 آخرین اتصال: {t}", t=_ago(m.get("online_at"), tr)))
            if m.get("sub_updated"):
                lines.append(tr("🔄 آخرین آپدیت لینک اشتراک: {t}", t=_ago(m["sub_updated"], tr)))
            if m.get("ua"):
                lines.append(tr("📱 کلاینت: {c}", c=ltr(esc(m["ua"]))))
        show_sub, show_cfg = pflag(svc, "sub"), pflag(svc, "config")
        tap_sub, tap_cfg = pflag(svc, "copy_sub"), pflag(svc, "copy_cfg")
        if svc["sub_url"] and show_sub:
            hint = tr("🔗 لینک اشتراک (روی آن بزنید تا کپی شود):") if tap_sub else tr("🔗 لینک اشتراک (انگشتتان را روی آن نگه دارید تا کپی شود):")
            lines.append("\n" + hint + "\n" + (f"<code>{esc(svc['sub_url'])}</code>" if tap_sub else esc(svc["sub_url"])))
        links = json.loads(svc["links"] or "[]") if show_cfg else []
        if links and (all_cfg or not compact):
            hint = tr("🔗 کانفیگ‌ها (روی هر کدام بزنید تا کپی شود):") if tap_cfg else tr("🔗 کانفیگ‌ها (انگشتتان را روی هر کدام نگه دارید تا کپی شود):")
            lines.append("\n" + hint)
            lines += [(f"<code>{esc(x)}</code>" if tap_cfg else esc(x)) for x in links[:(100 if all_cfg else 6)]]
        elif links:
            lines.append(tr("تعداد کانفیگ مستقیم: {n}", n=len(links)))
        return "\n".join(lines)

    def service_kb(self, svc, tr, back="vpn:my"):
        sid = svc["id"]
        rows = []
        if svc["status"] != "pending_manual":
            if svc["is_test"]:
                rows.append([btn(tr("🛒 خرید سرویس"), "vpn", role="action")])
            else:
                row = []
                if pflag(svc, "renew"):
                    row.append(btn(tr("🔄 تمدید"), f"vpn:rn:{sid}", role="action"))
                if row:
                    rows.append(row)
                if self._flag("vpn_allow_addon", "1") and pflag(svc, "addon"):
                    rows.append([btn(tr("➕ خرید حجم اضافه"), f"vpn:ad:{sid}:gb", role="action"), btn(tr("⏳ خرید زمان اضافه"), f"vpn:ad:{sid}:days", role="action")])
                if self._can_auto(svc):
                    on = bool(json.loads(svc["meta"] or "{}").get("auto"))
                    rows.append([btn(tr("🔁 تمدید خودکار: روشن ✅") if on else tr("🔁 تمدید خودکار: خاموش"), f"vpn:au:{sid}", role="action")])
            row = []
            if pflag(svc, "sub") and svc["sub_url"]:
                row.append(btn(tr("🔗 لینک اشتراک"), f"vpn:sl:{sid}", role="action"))
            if pflag(svc, "config"):
                row.append(btn(tr("📥 دریافت کانفیگ"), f"vpn:cf:{sid}", role="action"))
            if row:
                rows.append(row)
            row = [btn(tr("📲 QR کد"), f"vpn:qr:{sid}", role="action")] if pflag(svc, "qr") else []
            rows.append(row + [btn(tr("♻️ بروزرسانی"), f"vpn:rf:{sid}", role="action")])
            if self._flag("vpn_allow_revoke", "1") and svc["sub_url"] and pflag(svc, "revoke"):
                rows.append([btn(tr("🔑 لینک جدید"), f"vpn:rv:{sid}", role="action")])
            row = []
            if pflag(svc, "toggle") and svc["status"] in ("active", "disabled"):
                row.append(btn(tr("⏻ روشن کردن اکانت") if svc["status"] == "disabled" else tr("⏻ خاموش کردن اکانت"), f"vpn:tg:{sid}", role="action"))
            if pflag(svc, "unote"):
                row.append(btn(tr("📝 تغییر یادداشت"), f"vpn:nt:{sid}", role="action"))
            if row:
                rows.append(row)
            row = []
            if pflag(svc, "transfer"):
                row.append(btn(tr("🚚 انتقال به کاربر دیگر"), f"vpn:tf:{sid}", role="action"))
            if pflag(svc, "refund") and self.refund_info(svc)[0]:
                row.append(btn(tr("💸 بازگشت وجه"), f"vpn:rfd:{sid}", role="reject"))
            if row:
                rows.append(row)
        rows.append([btn(tr("🔙 سرویس‌های من"), back, role="back")])
        return ikb(rows)

    def qr_bg_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.shop.cfg.db_path)), "vpn_qr_bg.jpg")

    async def qr_png(self, text: str, bg: str | None = None) -> bytes | None:
        bg = self.s.get("vpn_qr_bg", "") if bg is None else bg
        return await asyncio.to_thread(vpn_qr.render, text, bg, self.qr_bg_path())

    def _qr_data(self, svc) -> str:
        return svc["sub_url"] or ((json.loads(svc["links"] or "[]") or [""])[0])

    async def fetch_sub_configs(self, sub_url: str) -> list[str]:
        """محتوای لینک ساب را می‌خواند و کانفیگ‌های داخلش را برمی‌گرداند (متن ساده یا base64)."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), connector=aiohttp.TCPConnector(ssl=False)) as ses:
                async with ses.get(sub_url, headers={"User-Agent": "v2rayN/6.45", "Accept": "*/*"}) as r:
                    if r.status != 200:
                        return []
                    raw = await r.content.read(1_000_000)
        except Exception as e:  # noqa: BLE001
            log.warning("sub fetch failed: %s", e)
            return []
        return parse_sub_body(raw)

    async def refresh_links(self, svc):
        """اگر «ارسال کانفیگ مستقیم» روشن و ساب موجود باشد، کانفیگ‌های داخل ساب را تازه می‌خواند و ذخیره می‌کند."""
        if not pflag(svc, "config") or not svc["sub_url"] or svc["status"] == "pending_manual":
            return svc
        links = await self.fetch_sub_configs(svc["sub_url"])
        if links and links != json.loads(svc["links"] or "[]"):
            await self.db.update_vpn_service(svc["id"], links=links)
            svc = await self.db.get_vpn_service(svc["id"])
        return svc

    @staticmethod
    def _split_text(text: str, limit: int = 3900) -> list[str]:
        """متن بلند را روی مرز خط به چند پیام (هرکدام کمتر از حد تلگرام) می‌شکند."""
        parts, cur, size = [], [], 0
        for ln in text.split("\n"):
            ln = ln[:limit]
            if cur and size + len(ln) + 1 > limit:
                parts.append("\n".join(cur))
                cur, size = [], 0
            cur.append(ln)
            size += len(ln) + 1
        if cur:
            parts.append("\n".join(cur))
        return parts

    async def send_service(self, uid, svc, new=False, tr=None):
        """مشخصات + لینک اشتراک + کانفیگ‌ها (زیر لینک اشتراک) + QR.
        اگر همه در ۱۰۲۴ حرف جا شود: یک پیام (عکس با کپشن). وگرنه: عکس QR و بعد یک پیام متنی کامل (در صورت طولانی بودن چند پیام پشت‌سرهم)."""
        tr = tr or await self.shop.tr_for(uid)
        head = (tr("✅ سرویس شما آماده است!") + "\n\n" if new else "")
        svc = await self.refresh_links(svc)
        kb = self.service_kb(svc, tr)
        data = self._qr_data(svc) if (pflag(svc, "qr") and svc["status"] != "pending_manual") else ""
        png = await self.qr_png(data) if data else None
        help_t = (self.s.get("vpn_help_text") or "").strip() if new else ""
        full = head + await self.service_text(svc, tr, all_cfg=True)
        texts, sent = [], False
        if png:
            photo = BufferedInputFile(png, filename=f"{svc['name']}.png")
            if len(full) <= 1024:
                cap, pkb = full, kb
            else:
                cap, pkb = f"🛡 <b>{esc(svc['title'] or tr('سرویس VPN'))}</b>", None
                texts = self._split_text(full)
            try:
                await self.shop.bot.send_photo(uid, photo, caption=cap, reply_markup=pkb)
                sent = True
            except Exception as e:  # noqa: BLE001
                log.warning("qr photo send failed: %s", e)
                texts = []
        if not sent:
            texts = self._split_text(full)
        for i, t in enumerate(texts):
            await self.shop.safe_send(uid, t, reply_markup=kb if i == len(texts) - 1 else None, disable_web_page_preview=True)
        if help_t:
            await self.shop.safe_send(uid, help_t, disable_web_page_preview=True)
        try:
            await self.send_wireguard(uid, svc, tr)
        except Exception as e:  # noqa: BLE001
            log.warning("wireguard send failed: %s", e)

    async def wg_confs(self, svc) -> list[tuple[str, str]]:
        """[(نام فایل، متن .conf)] اگر سرویس وایرگارد دارد. فقط پنل‌های پاسارگارد بررسی می‌شوند (یک درخواست اضافه)."""
        if svc["status"] == "pending_manual" or (svc.get("panel_type") or "") != "pasarguard" or not svc["sub_url"]:
            return []
        base = svc["name"]
        out: list[tuple[str, str]] = []
        links = [x for x in json.loads(svc["links"] or "[]") if wg_file.is_wg_uri(x)]
        if not links:
            try:
                links = [x for x in await self.fetch_sub_configs(svc["sub_url"]) if wg_file.is_wg_uri(x)]
            except Exception:  # noqa: BLE001
                links = []
        for i, uri in enumerate(links[:5]):
            c = wg_file.conf_from_uri(uri)
            if c:
                nm = wg_file.safe_filename(wg_file.parse_wg_uri(uri).get("name") or base, base)
                out.append((f"{nm}{'' if len(links) == 1 else '-' + str(i + 1)}.conf", c))
        if not out:                                          # فایل آماده‌ی خود پنل (اگر پنل مسیر /wireguard را بدهد)
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), connector=aiohttp.TCPConnector(ssl=False)) as ses:
                    async with ses.get(svc["sub_url"].rstrip("/") + "/wireguard", headers={"User-Agent": "WireGuard"}) as r:
                        if r.status == 200:
                            txt = (await r.content.read(200_000)).decode("utf-8", "ignore")
                            if wg_file.looks_like_conf(txt):
                                out.append((f"{wg_file.safe_filename(base)}.conf", txt if txt.endswith("\n") else txt + "\n"))
            except Exception:  # noqa: BLE001
                pass
        return out

    async def send_wireguard(self, uid, svc, tr=None):
        """اگر سرویس وایرگارد دارد: فایل .conf + QR (برای اسکن در اپ) + راهنمای ساده."""
        confs = await self.wg_confs(svc)
        if not confs:
            return False
        tr = tr or await self.shop.tr_for(uid)
        for fname, text in confs:
            await self.shop.bot.send_document(uid, BufferedInputFile(text.encode(), filename=fname),
                                              caption=tr("📄 فایل {wg} سرویس {n}", wg=f"{wg_file.LRM}WireGuard{wg_file.PDI}", n=f"<code>{esc(svc['name'])}</code>"))
            png = await self.qr_png(text, bg="") if len(text.encode()) < 2300 else None
            if png:
                await self.shop.bot.send_photo(uid, BufferedInputFile(png, filename=fname.rsplit(".", 1)[0] + ".png"),
                                               caption=tr("QR کد برای اسکن داخل برنامه‌ی {wg}", wg=f"{wg_file.LRM}WireGuard{wg_file.PDI}"))
        await self.shop.safe_send(uid, wg_file.guide_text(tr, svc["name"]), disable_web_page_preview=True)
        return True

    async def send_qr(self, uid, svc):
        data = self._qr_data(svc)
        png = await self.qr_png(data) if data else None
        if not png:
            return False
        try:
            await self.shop.bot.send_photo(uid, BufferedInputFile(png, filename=f"{svc['name']}.png"), caption=f"<code>{esc(svc['name'])}</code>")
            return True
        except Exception as e:
            log.warning("qr send failed: %s", e)
            return False

    # ------------------------------------------------------------------ مانیتورینگ سرور (مثل میرزا)
    async def panel_status_text(self, panel_id: int) -> tuple[bool, str]:
        try:
            drv = await self.driver(panel_id)
            st = await asyncio.wait_for(drv.status(), 20)
        except (PanelError, asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            return False, str(e)[:200] or "timeout"
        return True, render_status(st)
