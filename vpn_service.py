"""منطق فروش VPN: سفارش (جدید/تمدید/افزودنی)، ساخت و مدیریت سرویس روی پنل‌ها، همگام‌سازی، یادآوری و حذف خودکار.

از همان خط لوله‌ی سفارش/پرداخت/تخفیف/کیف پول ربات استفاده می‌کند (orders.kind='vpn'):
    ref = vpn:new:{plan_id}[:{username}]  |  vpn:renew:{service_id}:{plan_id}  |  vpn:addon:{service_id}:{addon_id}
    qty = تعداد روز پلن
"""
import io
import json
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING

from aiogram.types import BufferedInputFile

import vpn_panels
from i18n import N_
from ui import btn, esc, fmt, ikb
from vpn_panels import GB, PanelError

if TYPE_CHECKING:  # pragma: no cover
    from service import Shop

log = logging.getLogger("vpn")

NAME_RE = re.compile(r"^[a-z0-9_]{3,24}$")
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


def days_left(ts) -> int | None:
    if not ts:
        return None
    d = ts - time.time()
    return 0 if d <= 0 else int(d // 86400) + (1 if d % 86400 else 0)


def valid_name(n: str) -> bool:
    return bool(NAME_RE.match(n or ""))


def parse_ref(ref: str) -> list[str]:
    return (ref or "").split(":")


class VPN:
    def __init__(self, shop: "Shop"):
        self.shop = shop
        self.db = shop.db
        self._drivers: dict[int, tuple[tuple, vpn_panels.BasePanel]] = {}
        self._last_sync = 0.0

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
        for p in await self.db.list_vpn_plans(active_only=True):
            p = dict(p)
            p["user_price"] = self.shop._personal(p["price"], user)
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
    async def create_order(self, user_id: int, plan_id: int, name: str | None = None) -> int:
        if not self.enabled():
            raise VpnError(N_("فروش VPN فعال نیست."))
        plan = await self.db.get_vpn_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        if not plan or not plan["active"] or not panel or not panel["enabled"]:
            raise VpnError(N_("این پلن دیگر در دسترس نیست."))
        if not await self._capacity_ok(panel):
            raise VpnError(N_("ظرفیت این سرور تکمیل است؛ پلن دیگری انتخاب کنید."))
        if name:
            name = await self.make_name(panel["id"], user_id, name)
        user = await self.db.get_user(user_id)
        price = self.shop._personal(plan["price"], user)
        ref = f"vpn:new:{plan_id}" + (f":{name}" if name else "")
        return await self.db.create_vpn_order(user_id, plan["title"], plan["days"], ref, price, plan["cost"])

    async def renew_order(self, user_id: int, service_id: int, plan_id: int) -> int:
        svc = await self.db.get_vpn_service(service_id)
        plan = await self.db.get_vpn_plan(plan_id)
        if not svc or svc["user_id"] != user_id or svc["status"] in ("deleted", "pending_manual"):
            raise VpnError(N_("سرویس پیدا نشد."))
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
        if not ad or not ad["active"]:
            raise VpnError(N_("این بسته دیگر در دسترس نیست."))
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
            if not existing:
                name = await self.make_name(panel["id"], o["user_id"], asked)
                await self.db.add_vpn_service(user_id=o["user_id"], panel_id=panel["id"], plan_id=plan_id, name=name, status="pending_manual",
                                              order_id=o["id"], title=plan["title"], total_bytes=int(plan["gb"] * GB),
                                              expires_at=0)
            await shop._to_manual(o["id"], "ساخت دستی سرویس VPN", f"🛡 سفارش VPN (پنل دستی «{esc(panel['name'])}») — کانفیگ را از وب‌پنل ← VPN ← سرویس‌ها ثبت کنید:")
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
        if not await self._applied(svc, o["id"]):
            drv = await self.driver(svc["panel_id"])
            meta = json.loads(svc["meta"] or "{}")
            u = None
            if ad["gb"] > 0:
                u = await drv.add_traffic(svc["name"], meta, ad["gb"])
            if ad["days"] > 0:
                u = await drv.add_days(svc["name"], meta, ad["days"])
            await self._mark_applied(svc, o["id"])
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
    async def can_test(self, user_id: int) -> bool:
        return self.enabled() and self._flag("vpn_test_enabled") and not await self.db.vpn_has_test(user_id)

    async def create_test(self, user_id: int) -> int:
        if not self.enabled() or not self._flag("vpn_test_enabled"):
            raise VpnError(N_("اکانت تست فعال نیست."))
        if await self.db.vpn_has_test(user_id):
            raise VpnError(N_("شما قبلاً اکانت تست گرفته‌اید."))
        pid = self._int("vpn_test_panel")
        panel = await self.db.get_vpn_panel(pid) if pid else None
        if not panel or not panel["enabled"]:
            panel = next((p for p in await self.db.list_vpn_panels(only_enabled=True) if p["type"] != "manual"), None)
        if not panel:
            raise VpnError(N_("اکانت تست در حال حاضر در دسترس نیست."))
        if panel["type"] == "manual":
            raise VpnError(N_("اکانت تست در حال حاضر در دسترس نیست."))
        days, gb = max(self._int("vpn_test_days", 1), 1), float(self.s.get("vpn_test_gb") or 0)
        drv = await self.driver(panel["id"])
        # ثبت پیش از ساخت: جلوی دوبار گرفتن همزمان را می‌گیرد (ux_vpns_name + vpn_has_test)
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
        await self.db.update_vpn_service(svc["id"], **f)
        return await self.db.get_vpn_service(svc["id"])

    async def tick(self):
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

    async def _remind(self, svc):
        if svc["status"] not in ("active", "limited", "expired"):
            return
        notified = set(filter(None, (svc["notified"] or "").split(",")))
        new = set(notified)
        uid = svc["user_id"]
        rd, ru = self._int("vpn_remind_days"), self._int("vpn_remind_usage")
        left = days_left(svc["expires_at"])
        tr = await self.shop.tr_for(uid)
        kb = ikb([[btn(tr("🔄 تمدید / مشاهده"), f"vpn:s:{svc['id']}", role="action")]])
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

    async def admin_delete(self, sid: int, refund=False, actor="admin"):
        svc = await self.db.get_vpn_service(sid)
        if not svc or svc["status"] == "deleted":
            return False, N_("سرویس پیدا نشد.")
        if svc["status"] != "pending_manual":
            try:
                drv = await self.driver(svc["panel_id"])
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

    async def service_text(self, svc, tr) -> str:
        left = days_left(svc["expires_at"])
        total = svc["total_bytes"]
        lines = [f"🛡 <b>{esc(svc['title'] or tr('سرویس VPN'))}</b>",
                 tr("نام کاربری: {n}", n=f"<code>{esc(svc['name'])}</code>"),
                 tr("وضعیت: {s}", s=tr(STATUS_FA.get(svc["status"], svc["status"])))]
        if svc["status"] != "pending_manual":
            lines.append(tr("مصرف: {u}", u=ltr(f"{fmt_gb(svc['used_bytes'])} / " + (fmt_gb(total) if total else tr("نامحدود")))) + f"\n{bar(svc['used_bytes'], total)}")
            lines.append(tr("زمان باقی‌مانده: {t}", t=tr("نامحدود") if left is None else tr("{d} روز", d=left)))
        else:
            lines.append(tr("سرویس شما پس از آماده‌سازی توسط پشتیبانی ارسال می‌شود."))
        if svc["sub_url"]:
            lines.append("\n" + tr("🔗 لینک اشتراک (روی آن بزنید تا کپی شود):") + f"\n<code>{esc(svc['sub_url'])}</code>")
        links = json.loads(svc["links"] or "[]")
        if links and not svc["sub_url"]:
            lines.append("\n" + tr("🔗 کانفیگ‌ها:"))
            lines += [f"<code>{esc(x)}</code>" for x in links[:6]]
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
                row = [btn(tr("🔄 تمدید"), f"vpn:rn:{sid}", role="action")]
                if self._flag("vpn_allow_addon", "1"):
                    row.append(btn(tr("➕ حجم/زمان اضافه"), f"vpn:ad:{sid}", role="action"))
                rows.append(row)
            rows.append([btn(tr("📲 QR کد"), f"vpn:qr:{sid}", role="action"), btn(tr("♻️ بروزرسانی"), f"vpn:rf:{sid}", role="action")])
            if self._flag("vpn_allow_revoke", "1") and svc["sub_url"]:
                rows.append([btn(tr("🔑 لینک جدید"), f"vpn:rv:{sid}", role="action")])
        rows.append([btn(tr("🔙 سرویس‌های من"), back, role="back")])
        return ikb(rows)

    def qr_png(self, text: str) -> bytes | None:
        try:
            import segno
        except ImportError:
            return None
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="png", scale=8, border=3, dark="#111111", light="#ffffff")
        return buf.getvalue()

    async def send_service(self, uid, svc, new=False):
        tr = await self.shop.tr_for(uid)
        text = (tr("✅ سرویس شما آماده است!") + "\n\n" if new else "") + await self.service_text(svc, tr)
        help_t = (self.s.get("vpn_help_text") or "").strip()
        if new and help_t:
            text += "\n\n" + help_t
        kb = self.service_kb(svc, tr)
        await self.shop.safe_send(uid, text, reply_markup=kb, disable_web_page_preview=True)
        if new and svc["sub_url"]:
            await self.send_qr(uid, svc)

    async def send_qr(self, uid, svc):
        data = svc["sub_url"] or ((json.loads(svc["links"] or "[]") or [""])[0])
        png = self.qr_png(data) if data else None
        if not png:
            return False
        try:
            await self.shop.bot.send_photo(uid, BufferedInputFile(png, filename=f"{svc['name']}.png"), caption=f"<code>{esc(svc['name'])}</code>")
            return True
        except Exception as e:
            log.warning("qr send failed: %s", e)
            return False
