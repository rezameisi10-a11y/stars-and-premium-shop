"""فروش پنل نمایندگی: خریدار پلن نمایندگی می‌خرد و ربات به‌صورت خودکار یک «ادمین» در پنل (پاسارگارد، مرزبان، مرزنشین) می‌سازد
و آدرس پنل + نام کاربری + رمز را برایش می‌فرستد.

از همان خط لوله‌ی سفارش/پرداخت/تخفیف/کیف پول ربات استفاده می‌کند (orders.kind='vpn'):
    ref = vpn:agent:{plan_id}[:{username}]  |  vpn:arenew:{agent_id}:{plan_id}
    qty = تعداد روز پلن (۰ = بدون انقضا)
انقضای نماینده را خود ربات اجرا می‌کند (غیرفعال‌کردن ادمین در پنل)؛ پنل‌ها برای ادمین زمان انقضا ندارند.
"""
import json
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING

import vpn_panels
from i18n import N_
from ui import btn, esc, fmt, ikb
from vpn_panels import GB, PanelError, gen_password
from vpn_service import NAME_RE, STATUS_FA, VpnError, bar, days_left, fmt_gb, ltr

if TYPE_CHECKING:  # pragma: no cover
    from service import Shop

log = logging.getLogger("vpn.agent")

AGENT_STATUS_FA = {**STATUS_FA, "expired": N_("🔴 منقضی"), "disabled": N_("⚫️ غیرفعال")}


def agent_spec(plan: dict) -> dict:
    """مشخصات پلن به شکلی که درایور پنل می‌فهمد."""
    return {"role_id": (plan.get("role_id") or "").strip(), "traffic_gb": plan.get("traffic_gb") or 0, "max_users": plan.get("max_users") or 0,
            "service_ids": [int(x) for x in re.split(r"[,\s]+", plan.get("service_ids") or "") if x.isdigit()], "all_services": not (plan.get("service_ids") or "").strip(),
            "modify_users": bool(plan.get("modify_users", 1)), "data_min_gb": plan.get("data_min_gb") or 0, "data_max_gb": plan.get("data_max_gb") or 0,
            "expire_min_days": plan.get("expire_min_days") or 0, "expire_max_days": plan.get("expire_max_days") or 0, "note": plan.get("title") or ""}


def plan_features(plan: dict, tr) -> list[str]:
    """خلاصه‌ی قابلیت‌های پلن برای نمایش به خریدار."""
    out = [tr("مدت: {d}", d=tr("{d} روز", d=plan["days"]) if plan["days"] else tr("نامحدود")),
           tr("حجم کل: {g}", g=tr("نامحدود") if not plan["traffic_gb"] else ltr(f"{plan['traffic_gb']:g} GB"))]
    out.append(tr("سقف تعداد کاربر: {n}", n=plan["max_users"] if plan["max_users"] else tr("نامحدود")))
    return out


class AgentSales:
    def __init__(self, vpn):
        self.vpn = vpn
        self.shop = vpn.shop
        self.db = vpn.db
        self._last_sync = 0.0

    # ------------------------------------------------------------------ تنظیمات
    @property
    def s(self):
        return self.shop.s or {}

    def enabled(self) -> bool:
        return self.vpn.enabled() and self.s.get("vpn_agent_enabled", "1") == "1"

    def _int(self, key, default=0) -> int:
        try:
            return int(float(self.s.get(key) or default))
        except (TypeError, ValueError):
            return default

    def _flag(self, key, default="0") -> bool:
        return self.s.get(key, default) == "1"

    async def available(self) -> bool:
        return self.enabled() and bool(await self.db.list_agent_plans(active_only=True))

    async def plans_for(self, user):
        out = []
        for p in await self.db.list_agent_plans(active_only=True):
            p = dict(p)
            p["user_price"] = self.shop._personal(p["price"], user)
            out.append(p)
        return out

    async def make_name(self, panel_id: int, user_id: int, asked: str | None = None) -> str:
        prefix = re.sub(r"[^a-z0-9_]", "", (self.s.get("vpn_agent_prefix") or "agent").lower())[:10] or "agent"
        taken = "SELECT 1 x FROM vpn_agents WHERE panel_id=? AND username=? AND status!='deleted'"
        if asked:
            n = asked.strip().lower()
            if not NAME_RE.match(n):
                raise VpnError(N_("نام کاربری نامعتبر است (۳ تا ۲۴ حرف کوچک انگلیسی، عدد یا _)."))
            if await self.db.one(taken, (panel_id, n)):
                raise VpnError(N_("این نام کاربری قبلاً استفاده شده است."))
            return n
        n = f"{prefix}_{secrets.token_hex(3)}"
        while await self.db.one(taken, (panel_id, n)):
            n = f"{prefix}_{secrets.token_hex(3)}"
        return n

    async def _check_plan(self, plan, panel):
        if not plan or not plan["active"] or not panel or not panel["enabled"]:
            raise VpnError(N_("این پلن دیگر در دسترس نیست."))
        drv = await self.vpn.driver(panel["id"])
        if not getattr(drv, "ADMIN", False):
            raise VpnError(N_("این پلن دیگر در دسترس نیست."))

    # ------------------------------------------------------------------ ساخت سفارش
    async def create_order(self, user_id: int, plan_id: int, name: str | None = None) -> int:
        if not self.enabled():
            raise VpnError(N_("فروش پنل نمایندگی فعال نیست."))
        plan = await self.db.get_agent_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        await self._check_plan(plan, panel)
        if name:
            name = await self.make_name(panel["id"], user_id, name)
        user = await self.db.get_user(user_id)
        price = self.shop._personal(plan["price"], user)
        ref = f"vpn:agent:{plan_id}" + (f":{name}" if name else "")
        return await self.db.create_vpn_order(user_id, plan["title"], plan["days"], ref, price, plan["cost"])

    async def renew_order(self, user_id: int, agent_id: int, plan_id: int) -> int:
        ag = await self.db.get_agent(agent_id)
        plan = await self.db.get_agent_plan(plan_id)
        if not ag or ag["user_id"] != user_id or ag["status"] == "deleted":
            raise VpnError(N_("نماینده پیدا نشد."))
        if not plan or not plan["active"] or plan["panel_id"] != ag["panel_id"]:
            raise VpnError(N_("این پلن برای تمدید مجاز نیست."))
        user = await self.db.get_user(user_id)
        price = self.shop._personal(plan["price"], user)
        return await self.db.create_vpn_order(user_id, f"{N_('تمدید پنل نمایندگی')} {ag['username']} — {plan['title']}", plan["days"],
                                              f"vpn:arenew:{agent_id}:{plan_id}", price, plan["cost"])

    # ------------------------------------------------------------------ حجم / زمان اضافه
    async def addons_for(self, ag) -> list:
        """بسته‌های افزودنی فعال پلن فعلی نماینده (اگر ادمین برای آن پلن خاموش کرده باشد، خالی)."""
        if not ag.get("plan_id") or ag["status"] == "deleted":
            return []
        plan = await self.db.get_agent_plan(ag["plan_id"])
        if not plan or not plan.get("addon_on", 1) or plan["panel_id"] != ag["panel_id"]:
            return []
        return [dict(a) for a in await self.db.list_agent_addons(plan["id"], active_only=True)]

    @staticmethod
    def addon_title(a, tr=None) -> str:
        tt = tr or (lambda x, **k: x.format(**k) if k else x)
        return tt("➕ {g} گیگابایت حجم", g=f"{a['amount']:g}") if a["kind"] == "gb" else tt("➕ {d} روز زمان", d=f"{int(a['amount'])}")

    async def custom_for(self, ag) -> dict:
        """حجم/زمان دلخواه فعال: {"gb": {price,min,max}, "days": {...}} (کلید غایب = خاموش).
        اگر پلن قیمت اختصاصی گذاشته باشد همان؛ وگرنه تنظیم عمومی «تنظیمات ← پنل نمایندگی»."""
        if ag["status"] == "deleted":
            return {}
        plan = await self.db.get_agent_plan(ag["plan_id"]) if ag.get("plan_id") else None
        if plan and (not plan.get("addon_on", 1) or plan["panel_id"] != ag["panel_id"]):
            return {}
        out = {}
        for k, g in (("gb", "cgb"), ("days", "cd")):
            if k == "gb" and not ag["total_bytes"]:
                continue
            if plan and (plan.get(f"custom_{k}_price") or 0) > 0:
                if not plan.get(f"custom_{k}_on"):
                    continue
                cfg = {"price": int(plan[f"custom_{k}_price"]), "min": plan[f"custom_{k}_min"], "max": plan[f"custom_{k}_max"]}
            else:
                sv = self.s
                if sv.get(f"vpn_agent_{g}_on", "0") != "1":
                    continue
                try:
                    cfg = {"price": int(float(sv.get(f"vpn_agent_{g}_price") or 0)), "min": float(sv.get(f"vpn_agent_{g}_min") or 1),
                           "max": float(sv.get(f"vpn_agent_{g}_max") or 0)}
                except ValueError:
                    continue
            if cfg["price"] <= 0:
                continue
            cfg["min"] = cfg["min"] or 1
            cfg["max"] = cfg["max"] or 10 ** 6
            out[k] = cfg
        return out

    async def custom_order(self, user_id: int, agent_id: int, kind: str, amount: float) -> int:
        ag = await self.db.get_agent(agent_id)
        if not ag or ag["user_id"] != user_id or ag["status"] == "deleted":
            raise VpnError(N_("نماینده پیدا نشد."))
        cfg = (await self.custom_for(ag)).get(kind)
        if not cfg:
            raise VpnError(N_("این امکان دیگر در دسترس نیست."))
        amount = round(float(amount), 2) if kind == "gb" else int(amount)
        if amount < cfg["min"] or amount > cfg["max"]:
            raise VpnError(N_("مقدار خارج از بازه‌ی مجاز است."))
        user = await self.db.get_user(user_id)
        price = self.shop._personal(int(round(cfg["price"] * amount)), user)
        title = self.addon_title({"kind": kind, "amount": amount})
        return await self.db.create_vpn_order(user_id, f"{ag['username']} — {title}", int(amount) if kind == "days" else 0,
                                              f"vpn:aaddon:{agent_id}:c:{kind}:{amount:g}", price, 0)

    async def addon_order(self, user_id: int, agent_id: int, addon_id: int) -> int:
        ag = await self.db.get_agent(agent_id)
        ad = await self.db.get_agent_addon(addon_id)
        if not ag or ag["user_id"] != user_id or ag["status"] == "deleted":
            raise VpnError(N_("نماینده پیدا نشد."))
        if not ad or not ad["active"] or ad["plan_id"] != ag["plan_id"] or ad["id"] not in [a["id"] for a in await self.addons_for(ag)]:
            raise VpnError(N_("این بسته دیگر در دسترس نیست."))
        if ad["kind"] == "gb" and not ag["total_bytes"]:
            raise VpnError(N_("حجم این پنل نامحدود است؛ نیازی به خرید حجم نیست."))
        user = await self.db.get_user(user_id)
        price = self.shop._personal(ad["price"], user)
        return await self.db.create_vpn_order(user_id, f"{ag['username']} — {self.addon_title(ad)}", int(ad["amount"]) if ad["kind"] == "days" else 0,
                                              f"vpn:aaddon:{agent_id}:{addon_id}", price, ad["cost"])

    async def _fulfil_addon(self, o, aid, addon_id, custom=None):
        ag = await self.db.get_agent(aid)
        ad = {"kind": custom[0], "amount": float(custom[1])} if custom else await self.db.get_agent_addon(addon_id)
        if not ag or not ad or ag["status"] == "deleted":
            raise VpnError("agent/addon missing")
        meta = json.loads(ag["meta"] or "{}")
        if o["id"] not in meta.get("applied", []):
            drv = await self.vpn.driver(ag["panel_id"])
            f = {}
            if ad["kind"] == "gb":
                new_total = int(ag["total_bytes"] + ad["amount"] * GB)
                await drv.admin_set_traffic(ag["username"], new_total / GB)
                f["total_bytes"] = new_total
                if ag["status"] == "limited" and ag["used_bytes"] < new_total:        # حجم تمام‌شده بود: دوباره فعال
                    await drv.admin_set_enabled(ag["username"], True, ag["password"])
                    f["status"] = "active"
            else:
                base = ag["expires_at"] if ag["expires_at"] > time.time() else int(time.time())
                exp = base + int(ad["amount"]) * 86400
                f["expires_at"] = exp
                if ag["status"] == "expired":
                    await drv.admin_set_enabled(ag["username"], True, ag["password"])
                    f["status"] = "active"
            meta["applied"] = (meta.get("applied", []) + [o["id"]])[-30:]
            await self.db.update_agent(aid, notified="", meta=meta, last_sync=int(time.time()), **f)
        ag = await self.db.get_agent(aid)
        tr = await self.shop.tr_for(ag["user_id"])
        await self.shop.safe_send(ag["user_id"], tr("➕ بسته‌ی اضافه روی پنل نمایندگی <code>{n}</code> اعمال شد.", n=esc(ag["username"])) + "\n\n" + await self.agent_text(ag, tr),
                                  reply_markup=await self.agent_kb(ag, tr), disable_web_page_preview=True)
        await self.vpn._notify_admin(f"➕ افزودنی پنل نمایندگی\nسفارش #{o['id']} | {esc(ag['username'])} | {esc(self.addon_title(ad))}\nکاربر: <code>{o['user_id']}</code> | مبلغ: {fmt(o['amount'])} تومان")
        await self.shop.finish_order(o["id"], f"aaddon #{aid}")

    # ------------------------------------------------------------------ تحویل
    async def fulfil(self, o: dict):
        ref = (o["ref"] or "").split(":")
        if ref[1] == "aaddon":
            if ref[3] == "c":
                await self._fulfil_addon(o, int(ref[2]), 0, (ref[4], ref[5]))
            else:
                await self._fulfil_addon(o, int(ref[2]), int(ref[3]))
        elif ref[1] == "agent":
            await self._fulfil_new(o, int(ref[2]), ref[3] if len(ref) > 3 else None)
        elif ref[1] == "arenew":
            await self._fulfil_renew(o, int(ref[2]), int(ref[3]))
        else:
            raise VpnError("bad agent ref")

    async def _fulfil_new(self, o, plan_id, asked):
        existing = await self.db.agent_by_order(o["id"])
        if existing:                                         # تلاش مجدد پس از ساخت موفق
            existing = await self.db.get_agent(existing["id"])
            await self.send_agent(existing["user_id"], existing, new=True)
            await self.shop.finish_order(o["id"], f"agent #{existing['id']}")
            return
        plan = await self.db.get_agent_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        if not plan or not panel:
            raise VpnError("plan/panel missing")
        drv = await self.vpn.driver(panel["id"])
        spec = agent_spec(plan)
        spec["telegram_id"] = 0
        pw, name, info = gen_password(), "", None
        for attempt in range(3):
            name = await self.make_name(panel["id"], o["user_id"], asked if attempt == 0 else None)
            try:
                info = await drv.admin_create(name, pw, spec)
                break
            except PanelError as e:
                if attempt < 2 and not asked and any(w in str(e).lower() for w in ("exist", "duplicate", "already", "409")):
                    continue
                raise
        exp = int(time.time()) + plan["days"] * 86400 if plan["days"] else 0
        try:
            aid = await self.db.add_agent(user_id=o["user_id"], panel_id=panel["id"], plan_id=plan_id, username=name, password=pw, status="active",
                                          expires_at=exp, total_bytes=int((plan["traffic_gb"] or 0) * GB), used_bytes=info.used, max_users=plan["max_users"],
                                          order_id=o["id"], title=plan["title"], last_sync=int(time.time()))
        except Exception:
            log.exception("agent store failed; rolling back panel admin %s", name)
            try:
                await drv.admin_delete(name)
            except Exception:
                log.exception("agent rollback failed")
            raise
        ag = await self.db.get_agent(aid)
        await self.send_agent(o["user_id"], ag, new=True)
        await self.vpn._notify_admin(f"🧑‍💼 فروش پنل نمایندگی\nسفارش #{o['id']} | {esc(plan['title'])}\nپنل: {esc(panel['name'])} | ادمین: <code>{esc(name)}</code>\n"
                                     f"کاربر: <code>{o['user_id']}</code> | مبلغ: {fmt(o['amount'])} تومان")
        await self.shop.finish_order(o["id"], f"agent #{aid}")

    async def _fulfil_renew(self, o, aid, plan_id):
        ag = await self.db.get_agent(aid)
        plan = await self.db.get_agent_plan(plan_id)
        if not ag or not plan or ag["status"] == "deleted":
            raise VpnError("agent/plan missing")
        meta = json.loads(ag["meta"] or "{}")
        if o["id"] not in meta.get("applied", []):
            drv = await self.vpn.driver(ag["panel_id"])
            await drv.admin_renew(ag["username"], agent_spec(plan), ag["password"])
            exp = vpn_panels.new_expiry(ag["expires_at"], plan["days"], self._flag("vpn_stack", "1"))
            meta["applied"] = (meta.get("applied", []) + [o["id"]])[-30:]
            await self.db.update_agent(aid, status="active", expires_at=exp, total_bytes=int((plan["traffic_gb"] or 0) * GB), used_bytes=0,
                                       max_users=plan["max_users"], plan_id=plan_id, title=plan["title"], notified="", meta=meta, last_sync=int(time.time()))
        ag = await self.db.get_agent(aid)
        tr = await self.shop.tr_for(ag["user_id"])
        await self.shop.safe_send(ag["user_id"], tr("🔄 پنل نمایندگی <code>{n}</code> تمدید شد.", n=esc(ag["username"])) + "\n\n" + await self.agent_text(ag, tr),
                                  reply_markup=await self.agent_kb(ag, tr), disable_web_page_preview=True)
        await self.vpn._notify_admin(f"🔄 تمدید پنل نمایندگی\nسفارش #{o['id']} | {esc(ag['username'])} | {esc(plan['title'])}\nکاربر: <code>{o['user_id']}</code> | مبلغ: {fmt(o['amount'])} تومان")
        await self.shop.finish_order(o["id"], f"arenew #{aid}")

    # ------------------------------------------------------------------ همگام‌سازی، انقضا، یادآوری
    async def sync(self, ag, force=True):
        if ag["status"] == "deleted":
            return ag
        drv = await self.vpn.driver(ag["panel_id"])
        try:
            info = await drv.admin_info(ag["username"])
        except PanelError as e:
            await self.db.update_agent(ag["id"], note=str(e)[:200], last_sync=int(time.time()))
            if force:
                raise
            return ag
        st = ag["status"]
        if st != "expired" and drv.TYPE != "marzban":        # مرزبان وضعیت ادمین ندارد؛ وضعیت را خود ربات نگه می‌دارد
            st = info.status if info.status in ("disabled", "limited") else "active"
            if st == "disabled" and ag["status"] == "limited":
                st = "limited"
        f = dict(used_bytes=info.used, users_count=info.users, last_sync=int(time.time()), note="", status=st)
        if info.total:
            f["total_bytes"] = info.total
        if info.max_users:
            f["max_users"] = info.max_users
        await self.db.update_agent(ag["id"], **f)
        ag = await self.db.get_agent(ag["id"])
        cap = ag["total_bytes"]
        if cap and ag["used_bytes"] >= cap and ag["status"] == "active":      # پنل‌هایی که سقف حجم ادمین ندارند: ربات خودش اجرا می‌کند
            try:
                await drv.admin_set_enabled(ag["username"], False, ag["password"])
                await self.db.update_agent(ag["id"], status="limited")
                tr = await self.shop.tr_for(ag["user_id"])
                await self.shop.say(ag["user_id"], "📉 حجم پنل نمایندگی <code>{n}</code> تمام شد و غیرفعال شد. برای ادامه تمدید کنید.",
                                    ikb([[btn(tr("🔄 تمدید"), f"vpn:agn:{ag['id']}", role="action")]]), n=esc(ag["username"]))
                ag = await self.db.get_agent(ag["id"])
            except PanelError as e:
                log.warning("agent limit-disable failed %s: %s", ag["id"], e)
        return ag

    async def tick(self):
        if not self.enabled() and not await self.db.one("SELECT 1 x FROM vpn_agents LIMIT 1"):
            return
        every = max(self._int("vpn_sync_min", 10), 1) * 60
        if time.time() - self._last_sync < min(every, 60):
            return
        self._last_sync = time.time()
        now = int(time.time())
        for ag in await self.db.agents_syncable(now - every, limit=40):
            try:
                if ag["expires_at"] and ag["expires_at"] < now and ag["status"] in ("active", "limited"):
                    await self._expire(ag)
                    continue
                ag = await self.sync(ag, force=False)
                await self._remind(ag)
            except Exception:
                log.exception("agent tick %s failed", ag["id"])
        await self._autodelete()

    async def _expire(self, ag):
        drv = await self.vpn.driver(ag["panel_id"])
        try:
            await drv.admin_set_enabled(ag["username"], False, ag["password"])
        except PanelError as e:
            await self.db.update_agent(ag["id"], note=str(e)[:200], last_sync=int(time.time()))
            log.warning("agent expire failed %s: %s", ag["id"], e)
            return
        await self.db.update_agent(ag["id"], status="expired", last_sync=int(time.time()))
        tr = await self.shop.tr_for(ag["user_id"])
        await self.shop.say(ag["user_id"], "⏰ پنل نمایندگی <code>{n}</code> منقضی و غیرفعال شد. برای فعال‌سازی دوباره تمدید کنید.",
                            ikb([[btn(tr("🔄 تمدید"), f"vpn:agn:{ag['id']}", role="action")]]), n=esc(ag["username"]))
        await self.db.update_agent(ag["id"], notified="expired")

    # ------------------------------------------------------------------ تمدید خودکار (اختیاری، پیش‌فرض خاموش) / تمدید یک‌کلیکی
    def _can_auto(self, ag) -> bool:
        return bool(self._flag("vpn_autorenew_on", "1") and ag.get("plan_id") and ag["status"] != "deleted")

    def is_auto(self, ag) -> bool:
        try:
            return bool(json.loads(ag.get("meta") or "{}").get("auto"))
        except (ValueError, AttributeError):
            return False

    async def set_auto(self, ag, on: bool):
        meta = json.loads(ag["meta"] or "{}")
        if on:
            meta["auto"] = 1
        else:
            meta.pop("auto", None)
        await self.db.update_agent(ag["id"], meta=meta)

    async def quick_kb(self, ag, tr):
        rows = []
        plan = await self.db.get_agent_plan(ag["plan_id"]) if ag["plan_id"] else None
        if plan and plan["active"] and plan["panel_id"] == ag["panel_id"]:
            user = await self.db.get_user(ag["user_id"])
            rows.append([btn(f"{tr('⚡ تمدید همین پلن')} | {fmt(self.shop._personal(plan['price'], user))} {tr('ت')}", f"vpn:agq:{ag['id']}", role="action")])
        rows.append([btn(tr("🔄 تمدید / مشاهده"), f"vpn:ag:{ag['id']}", role="action")])
        return ikb(rows)

    async def _auto_renew(self, ag, notified: set):
        """True = تمدید شد · None = پیام خطا فرستاده شد (notified تغییر کرد) · False = کاری نشد."""
        if not self.is_auto(ag) or not self._can_auto(ag) or ag["status"] not in ("active", "limited", "expired"):
            return False
        left = days_left(ag["expires_at"])
        if not (ag["status"] == "expired" or (left is not None and left <= max(self._int("vpn_autorenew_days", 1), 0))):
            return False
        uid = ag["user_id"]
        plan = await self.db.get_agent_plan(ag["plan_id"])
        tr = await self.shop.tr_for(uid)
        kb = await self.quick_kb(ag, tr)
        user = await self.db.get_user(uid)
        if not plan or not plan["active"] or plan["panel_id"] != ag["panel_id"]:
            if "autofail" not in notified:
                await self.shop.say(uid, "⚠️ تمدید خودکار پنل نمایندگی <code>{n}</code> انجام نشد: پلن قبلی دیگر موجود نیست. لطفاً دستی تمدید کنید.", kb, n=esc(ag["username"]))
                notified.add("autofail")
                return None
            return False
        price = self.shop._personal(plan["price"], user)
        if (user["balance"] or 0) < price:
            if "autofail" not in notified:
                await self.shop.say(uid, "⚠️ تمدید خودکار پنل نمایندگی <code>{n}</code> انجام نشد: موجودی کیف پول کافی نیست (نیاز: {p} تومان). کیف پول را شارژ کنید تا خودکار تمدید شود.", kb, n=esc(ag["username"]), p=fmt(price))
                notified.add("autofail")
                return None
            return False
        try:
            oid = await self.renew_order(uid, ag["id"], plan["id"])
        except VpnError:
            return False
        if await self.shop.pay_with_wallet(oid, uid):
            await self.db.log("auto", "agent_autorenew", f"#{ag['id']} order {oid}")
            return True
        return False

    async def _remind(self, ag):
        if ag["status"] not in ("active", "limited", "expired"):
            return
        notified = set(filter(None, (ag["notified"] or "").split(",")))
        before = set(notified)
        r = await self._auto_renew(ag, notified)
        if r:
            return                                          # تمدید شد؛ پیام تمدید خودش فرستاده می‌شود
        if ag["status"] == "active":
            tr = await self.shop.tr_for(ag["user_id"])
            kb = await self.quick_kb(ag, tr)
            left, rd = days_left(ag["expires_at"]), self._int("vpn_agent_remind_days", 3)
            if rd and left is not None and left <= rd and "time" not in notified:
                await self.shop.say(ag["user_id"], "⏳ فقط <b>{d}</b> روز تا پایان پنل نمایندگی <code>{n}</code> مانده است.", kb, d=left, n=esc(ag["username"]))
                notified.add("time")
            pct = min(max(self._int("vpn_remind_usage", 90), 0), 100)
            if pct and ag["total_bytes"] and ag["used_bytes"] * 100 >= ag["total_bytes"] * pct and "usage" not in notified:
                await self.shop.say(ag["user_id"], "📉 مصرف پنل نمایندگی <code>{n}</code> به {p}٪ رسید ({u}). برای جلوگیری از قطع شدن، تمدید کنید.", kb,
                                    n=esc(ag["username"]), p=pct, u=ltr(f"{fmt_gb(ag['used_bytes'])} / {fmt_gb(ag['total_bytes'])}"))
                notified.add("usage")
        if notified != before:
            await self.db.update_agent(ag["id"], notified=",".join(sorted(notified)))

    async def _autodelete(self):
        d = self._int("vpn_agent_autodelete_days")
        if d <= 0:
            return
        cut = int(time.time()) - d * 86400
        for ag in await self.db.all("SELECT * FROM vpn_agents WHERE status='expired' AND expires_at>0 AND expires_at<? LIMIT 10", (cut,)):
            try:
                await self.admin_delete(ag["id"], actor="auto")
            except Exception:
                log.exception("agent autodelete %s failed", ag["id"])

    # ------------------------------------------------------------------ عملیات ادمین (مشترک بین ربات و وب)
    async def admin_action(self, aid: int, act: str, value=None, actor="admin"):
        """act: sync | add_days | set_traffic | reset | enable | disable | password | delete | note. خروجی (ok, پیام)."""
        ag = await self.db.get_agent(aid)
        if not ag or ag["status"] == "deleted":
            return False, N_("نماینده پیدا نشد.")
        await self.db.log(actor, f"agent_{act}", f"#{aid} {ag['username']}")
        if act == "note":
            await self.db.update_agent(aid, note=str(value or "")[:300])
            return True, N_("ثبت شد.")
        if act == "delete":
            return await self.admin_delete(aid, actor=actor)
        drv = await self.vpn.driver(ag["panel_id"])
        try:
            if act == "sync":
                await self.sync(ag)
                return True, N_("همگام شد.")
            if act == "add_days":
                d = int(value)
                base = ag["expires_at"] if ag["expires_at"] > time.time() else int(time.time())
                await self.db.update_agent(aid, expires_at=base + d * 86400 if ag["expires_at"] or d else 0, notified="")
                if ag["status"] == "expired" and base + d * 86400 > time.time():
                    await drv.admin_set_enabled(ag["username"], True, ag["password"])
                    await self.db.update_agent(aid, status="active")
            elif act == "set_traffic":
                g = float(value)
                await drv.admin_set_traffic(ag["username"], g)
                await self.db.update_agent(aid, total_bytes=vpn_panels.gb_bytes(g))
            elif act == "reset":
                await drv.admin_renew(ag["username"], {"traffic_gb": ag["total_bytes"] / GB}, ag["password"])
                await self.db.update_agent(aid, used_bytes=0)
            elif act in ("enable", "disable"):
                await drv.admin_set_enabled(ag["username"], act == "enable", ag["password"])
                await self.db.update_agent(aid, status="active" if act == "enable" else "disabled", notified="" if act == "enable" else ag["notified"])
            elif act == "password":
                pw = (str(value).strip() if value else "") or gen_password()
                await drv.admin_set_password(ag["username"], pw)
                await self.db.update_agent(aid, password=pw)
            else:
                return False, N_("عملیات نامعتبر.")
        except (PanelError, ValueError) as e:
            return False, f"{N_('خطای پنل')}: {str(e)[:200]}"
        return True, N_("انجام شد.")

    async def admin_delete(self, aid: int, refund=False, actor="admin"):
        ag = await self.db.get_agent(aid)
        if not ag or ag["status"] == "deleted":
            return False, N_("نماینده پیدا نشد.")
        try:
            drv = await self.vpn.driver(ag["panel_id"])
            await drv.admin_delete(ag["username"])
        except PanelError as e:
            return False, f"{N_('خطای پنل')}: {str(e)[:200]}"
        await self.db.update_agent(aid, status="deleted")
        msg = N_("حذف شد.")
        if refund and ag.get("order_id"):
            o = await self.db.get_order(ag["order_id"])
            if o and o["status"] == "done" and await self.db.transition(o["id"], ["done"], "refunded", "حذف نماینده + بازگشت وجه"):
                await self.db.add_balance(o["user_id"], o["amount"], f"refund: order {o['id']}", ref_key=f"refund:{o['id']}")
                await self.shop.say(o["user_id"], "↩️ مبلغ سفارش #{oid} ({amount} تومان) به کیف پول شما برگشت.", oid=o["id"], amount=fmt(o["amount"]))
                msg = N_("حذف شد و مبلغ به کیف پول برگشت.")
        await self.db.log(actor, "agent_delete", f"#{aid} {ag['username']}")
        return True, msg

    async def admin_create(self, user_id: int, plan_id: int, name: str | None = None, actor="admin"):
        """ساخت مستقیم نماینده برای کاربر توسط ادمین (بدون پرداخت). خروجی (ok, پیام, agent_id)."""
        plan = await self.db.get_agent_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        if not plan or not panel:
            return False, N_("پلن پیدا نشد."), None
        if not await self.db.get_user(user_id):
            return False, N_("کاربر پیدا نشد."), None
        drv = await self.vpn.driver(panel["id"])
        if not getattr(drv, "ADMIN", False):
            return False, N_("این پنل از ساخت نماینده پشتیبانی نمی‌کند."), None
        try:
            nm = await self.make_name(panel["id"], user_id, name or None)
            pw = gen_password()
            info = await drv.admin_create(nm, pw, agent_spec(plan))
        except VpnError as e:
            return False, str(e), None
        except PanelError as e:
            return False, f"{N_('خطای پنل')}: {str(e)[:200]}", None
        exp = int(time.time()) + plan["days"] * 86400 if plan["days"] else 0
        aid = await self.db.add_agent(user_id=user_id, panel_id=panel["id"], plan_id=plan_id, username=nm, password=pw, status="active", expires_at=exp,
                                      total_bytes=int((plan["traffic_gb"] or 0) * GB), used_bytes=info.used, max_users=plan["max_users"], title=plan["title"],
                                      last_sync=int(time.time()))
        await self.db.log(actor, "agent_create", f"#{aid} user {user_id}")
        await self.send_agent(user_id, await self.db.get_agent(aid), new=True)
        return True, N_("نماینده ساخته و برای کاربر ارسال شد."), aid

    # ------------------------------------------------------------------ نمایش برای کاربر
    def panel_link(self, ag) -> str:
        try:
            pub = str(json.loads(ag.get("panel_extra") or "{}").get("public_url") or "").strip()    # آدرس عمومی پنل (اختیاری) در «extra» پنل
        except (ValueError, AttributeError):
            pub = ""
        base = (pub or ag.get("panel_url") or "").rstrip("/")
        t = ag.get("panel_type") or ""
        if t in ("pasarguard", "marzban", "marzneshin"):
            base = re.sub(r"/dashboard(/.*)?$", "", base, flags=re.I).rstrip("/")
        return base + "/dashboard/" if t in ("pasarguard", "marzban", "marzneshin") else base

    async def agent_text(self, ag, tr) -> str:
        left = days_left(ag["expires_at"])
        total = ag["total_bytes"]
        lines = [f"🧑‍💼 <b>{esc(ag['title'] or tr('پنل نمایندگی'))}</b>",
                 tr("وضعیت: {s}", s=tr(AGENT_STATUS_FA.get(ag["status"], ag["status"]))),
                 tr("آدرس پنل: {u}", u=f"<code>{esc(self.panel_link(ag))}</code>"),
                 tr("نام کاربری: {n}", n=f"<code>{esc(ag['username'])}</code>"),
                 tr("رمز عبور: {p}", p=f"<code>{esc(ag['password'])}</code>")]
        if total:
            lines.append(tr("مصرف: {u}", u=ltr(f"{fmt_gb(ag['used_bytes'])} / {fmt_gb(total)}")) + f"\n{bar(ag['used_bytes'], total)}")
        else:
            lines.append(tr("مصرف: {u}", u=ltr(f"{fmt_gb(ag['used_bytes'])} / ") + tr("نامحدود")))
        lines.append(tr("تعداد کاربران: {n}", n=ltr(f"{ag['users_count']} / {ag['max_users']}" if ag["max_users"] else str(ag["users_count"]))))
        lines.append(tr("زمان باقی‌مانده: {t}", t=tr("نامحدود") if left is None else tr("{d} روز", d=left)))
        if self._can_auto(ag):
            lines.append(tr("🔁 تمدید خودکار: {s}", s=tr("روشن") if self.is_auto(ag) else tr("خاموش")))
        return "\n".join(lines)

    async def agent_kb(self, ag, tr, back="vpn:agl"):
        i = ag["id"]
        rows = [[btn(tr("🔄 تمدید"), f"vpn:agn:{i}", role="action"), btn(tr("♻️ بروزرسانی"), f"vpn:agr:{i}", role="action")]]
        if await self.addons_for(ag):
            rows.append([btn(tr("➕ بسته‌ی حجم/زمان اضافه"), f"vpn:aga:{i}", role="action")])
        custom = await self.custom_for(ag)
        cr = ([btn(tr("✏️ حجم اضافه دلخواه"), f"vpn:agc:{i}:gb", role="action")] if "gb" in custom else []) + \
             ([btn(tr("✏️ زمان اضافه دلخواه"), f"vpn:agc:{i}:days", role="action")] if "days" in custom else [])
        if cr:
            rows.append(cr)
        rows.append([btn(tr("🔑 رمز جدید"), f"vpn:agw:{i}", role="action")])
        if self._can_auto(ag):
            rows.append([btn(tr("🔁 تمدید خودکار: روشن ✅") if self.is_auto(ag) else tr("🔁 تمدید خودکار: خاموش"), f"vpn:agt:{i}", role="action")])
        rows.append([btn(tr("🔙 پنل‌های نمایندگی من"), back, role="back")])
        return ikb(rows)

    async def send_agent(self, uid, ag, new=False):
        tr = await self.shop.tr_for(uid)
        text = (tr("✅ پنل نمایندگی شما آماده است!") + "\n\n" if new else "") + await self.agent_text(ag, tr)
        help_t = (self.s.get("vpn_agent_help_text") or "").strip()
        if new and help_t:
            text += "\n\n" + help_t
        await self.shop.safe_send(uid, text, reply_markup=await self.agent_kb(ag, tr), disable_web_page_preview=True)
