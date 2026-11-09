"""فروش پنل نمایندگی: خریدار پلن نمایندگی می‌خرد و ربات به‌صورت خودکار یک «ادمین» در پنل (پاسارگارد، مرزبان، مرزنشین) می‌سازد
و آدرس پنل + نام کاربری + رمز را برایش می‌فرستد.

از همان خط لوله‌ی سفارش/پرداخت/تخفیف/کیف پول ربات استفاده می‌کند (orders.kind='vpn'):
    ref = vpn:agent:{plan_id}[:{username}]  |  vpn:arenew:{agent_id}:{plan_id}
    qty = تعداد روز پلن (۰ = بدون انقضا)
انقضای نماینده را خود ربات اجرا می‌کند (غیرفعال‌کردن ادمین در پنل)؛ پنل‌ها برای ادمین زمان انقضا ندارند.
"""
import json
import math
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING

import vpn_panels
from i18n import N_
from ui import btn, esc, fmt, ikb
from vpn_panels import GB, PanelError, gen_password
from vpn_ops import MoveError
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

    # ------------------------------------------------------------------ حجم دلخواه برای خرید پنل
    async def cv_cfg(self):
        """خرید پنل با حجم دلخواه: تنظیمات مستقل (قیمت هر گیگ، بازه‌ی حجم، مدت، سقف کاربر، پنل) یا None (خاموش / ناقص)."""
        if not self.enabled() or not self._flag("vpn_agent_cv_on"):
            return None
        price = self._int("vpn_agent_cv_price")
        if price <= 0:
            return None
        mn = float(self.s.get("vpn_agent_cv_min") or 1) or 1
        mx = float(self.s.get("vpn_agent_cv_max") or 10 ** 6) or 10 ** 6
        if mx < mn:
            return None
        pid = self._int("vpn_agent_cv_panel")                 # پنل را ادمین دستی انتخاب می‌کند (بدون انتخاب خودکار)
        panel = await self.db.get_vpn_panel(pid) if pid else None
        if not panel or not panel["enabled"] or not getattr(vpn_panels.DRIVERS.get(panel["type"]), "ADMIN", False):
            return None
        return {"price": price, "min": mn, "max": mx, "days": max(self._int("vpn_agent_cv_days", 30), 0), "users": max(self._int("vpn_agent_cv_users"), 0),
                "panel": panel["id"], "cost": max(self._int("vpn_agent_cv_cost"), 0)}

    async def cv_plan(self, panel_id: int, gb: float, days: int, users: int) -> dict:
        """پلنِ مجازی برای حجم دلخواه: فقط از تنظیمات خودش (حجم، مدت، سقف کاربر)؛ از هیچ پلن نمایندگی چیزی به ارث نمی‌برد."""
        base = {"id": None, "role_id": "", "service_ids": "", "modify_users": 1, "data_min_gb": 0, "data_max_gb": 0, "expire_min_days": 0, "expire_max_days": 0, "note": ""}
        base.update(panel_id=panel_id, days=days, max_users=users, traffic_gb=gb, price=0, cost=0, active=1, title=f"{N_('پنل نمایندگی')} {gb:g} GB")
        return base

    async def cv_quote(self, gb, user) -> tuple:
        """(تنظیمات, حجم گردشده, قیمت کاربر, هزینه) یا VpnError."""
        cfg = await self.cv_cfg()
        if not cfg:
            raise VpnError(N_("این امکان دیگر در دسترس نیست."))
        try:
            gb = round(float(gb), 2)
        except (TypeError, ValueError):
            raise VpnError(N_("مقدار خارج از بازه‌ی مجاز است.")) from None
        if not math.isfinite(gb) or not cfg["min"] <= gb <= cfg["max"]:
            raise VpnError(N_("مقدار خارج از بازه‌ی مجاز است."))
        return cfg, gb, self.shop._personal(int(round(gb * cfg["price"])), user), int(round(gb * cfg["cost"]))

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
    async def create_order(self, user_id: int, plan_id: int, name: str | None = None, gb=None) -> int:
        if not self.enabled():
            raise VpnError(N_("فروش پنل نمایندگی فعال نیست."))
        await self.vpn.guard_pending(user_id)
        if gb is not None:                                   # حجم دلخواه (تنظیمات مستقل)
            user = await self.db.get_user(user_id)
            cfg, gb, price, cost = await self.cv_quote(gb, user)
            panel = await self.db.get_vpn_panel(cfg["panel"])
            await self._check_plan(await self.cv_plan(cfg["panel"], gb, cfg["days"], cfg["users"]), panel)
            if name:
                name = await self.make_name(panel["id"], user_id, name)
            ref = f"vpn:agent:c:{gb:g}:{cfg['days']}:{cfg['users']}:{cfg['panel']}" + (f":{name}" if name else "")
            return await self.db.create_vpn_order(user_id, f"{N_('پنل نمایندگی')} {gb:g} GB", cfg["days"], ref, price, cost)
        plan = await self.db.get_agent_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        await self._check_plan(plan, panel)
        if name:
            name = await self.make_name(panel["id"], user_id, name)
        user = await self.db.get_user(user_id)
        price = self.shop._personal(plan["price"], user)
        ref = f"vpn:agent:{plan_id}" + (f":{name}" if name else "")
        return await self.db.create_vpn_order(user_id, plan["title"], plan["days"], ref, price, plan["cost"])

    async def renew_stacks(self, panel_id: int) -> bool:
        """آیا تمدید این پنل حجم را روی حجم فعلی اضافه می‌کند (سقف حجم ادمین پشتیبانی شود)؟ وگرنه تمدید کلاسیک (حجم و مصرف از نو)."""
        row = await self.db.get_vpn_panel(panel_id)
        cls = vpn_panels.DRIVERS.get((row or {}).get("type"))
        return bool(cls and cls.admin_set_traffic is not vpn_panels.BasePanel.admin_set_traffic)

    async def renew_same(self, ag, user):
        """تمدید «همان پنل»: حجم پایه‌ی خودِ پنل (نه حجم‌های اضافه‌ای که خریده شده) با مدت و قیمت فعلی. {gb, days, price} یا None."""
        if ag["status"] == "deleted":
            return None
        if self.is_custom(ag):
            cfg = await self.cv_cfg()
            gb = float(json.loads(ag["meta"])["custom_gb"])
            if not cfg or cfg["panel"] != ag["panel_id"] or not cfg["min"] <= gb <= cfg["max"]:
                return None
            return {"gb": gb, "days": cfg["days"], "price": self.shop._personal(int(round(gb * cfg["price"])), user), "plan_id": 0, "stack": await self.renew_stacks(ag["panel_id"])}
        plan = await self.db.get_agent_plan(ag["plan_id"]) if ag.get("plan_id") else None
        if not plan or not plan["active"] or plan["panel_id"] != ag["panel_id"]:
            return None
        return {"gb": plan["traffic_gb"], "days": plan["days"], "price": self.shop._personal(plan["price"], user), "plan_id": plan["id"], "stack": await self.renew_stacks(ag["panel_id"])}

    async def renew_order(self, user_id: int, agent_id: int, plan_id: int) -> int:
        ag = await self.db.get_agent(agent_id)
        if not ag or ag["user_id"] != user_id or ag["status"] == "deleted":
            raise VpnError(N_("نماینده پیدا نشد."))
        await self.vpn.guard_pending(user_id)
        user = await self.db.get_user(user_id)
        cv = (json.loads(ag.get("meta") or "{}")).get("custom_gb")
        cfg = await self.cv_cfg() if cv else None
        if cv and cfg and cfg["panel"] == ag["panel_id"] and not plan_id:    # پنل با حجم دلخواه ← همان حجم، با مدت/کاربر/قیمت فعلی تنظیمات
            _c, cv, price, cost = await self.cv_quote(cv, user)
            return await self.db.create_vpn_order(user_id, f"{N_('تمدید پنل نمایندگی')} {ag['username']} — {cv:g} GB", cfg["days"],
                                                  f"vpn:arenew:{agent_id}:0:{cv:g}:{cfg['days']}:{cfg['users']}", price, cost)
        plan = await self.db.get_agent_plan(plan_id or ag.get("plan_id") or 0)       # plan_id=0 ← همان پلنِ خودِ پنل
        if not plan or not plan["active"] or plan["panel_id"] != ag["panel_id"]:
            raise VpnError(N_("این پلن برای تمدید مجاز نیست."))
        price = self.shop._personal(plan["price"], user)
        return await self.db.create_vpn_order(user_id, f"{N_('تمدید پنل نمایندگی')} {ag['username']} — {plan['title']}", plan["days"],
                                              f"vpn:arenew:{agent_id}:{plan['id']}", price, plan["cost"])

    # ------------------------------------------------------------------ حجم / زمان اضافه
    async def addons_for(self, ag) -> list:
        """بسته‌های افزودنی فعال پلن فعلی نماینده (اگر ادمین برای آن پلن خاموش کرده باشد، خالی)."""
        if not ag.get("plan_id") or ag["status"] == "deleted" or self.is_cv(ag):    # پنل حجم‌دلخواه: بسته‌ی ثابت ندارد؛ فقط حجم اضافه با قیمت خودش (custom_for)
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
        if self.is_cv(ag):                                      # قیمت حجم اضافه‌ی مخصوص پنل‌های حجم دلخواه (تنظیمات مستقل)
            price = self._int("vpn_agent_cv_add_price")
            if not self._flag("vpn_agent_cv_add_on") or price <= 0 or not ag["total_bytes"]:
                return {}
            return {"gb": {"price": price, "min": float(self.s.get("vpn_agent_cv_add_min") or 1) or 1, "max": float(self.s.get("vpn_agent_cv_add_max") or 10 ** 6) or 10 ** 6}}
        plan = await self.db.get_agent_plan(ag["plan_id"]) if ag.get("plan_id") else None
        if plan and (not plan.get("addon_on", 1) or plan["panel_id"] != ag["panel_id"]):
            return {}
        out = {}
        for k in ("gb", "days"):
            if k == "gb" and not ag["total_bytes"]:
                continue
            if not plan or not plan.get(f"custom_{k}_on") or (plan.get(f"custom_{k}_price") or 0) <= 0:
                continue
            cfg = {"price": int(plan[f"custom_{k}_price"]), "min": plan[f"custom_{k}_min"], "max": plan[f"custom_{k}_max"]}
            if cfg["price"] <= 0:
                continue
            cfg["min"] = cfg["min"] or 1
            cfg["max"] = cfg["max"] or 10 ** 6
            out[k] = cfg
        return out

    @staticmethod
    def _amt_text(amount) -> str:
        return f"{float(amount):.2f}".rstrip("0").rstrip(".") or "0"

    async def custom_order(self, user_id: int, agent_id: int, kind: str, amount: float) -> int:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise VpnError(N_("مقدار خارج از بازه‌ی مجاز است.")) from None
        if not math.isfinite(amount) or amount <= 0:             # NaN / بی‌نهایت / صفر / منفی
            raise VpnError(N_("مقدار خارج از بازه‌ی مجاز است."))
        await self.vpn.guard_pending(user_id)
        ag = await self.db.get_agent(agent_id)
        if not ag or ag["user_id"] != user_id or ag["status"] == "deleted":
            raise VpnError(N_("نماینده پیدا نشد."))
        cfg = (await self.custom_for(ag)).get(kind)
        if not cfg:
            raise VpnError(N_("این امکان دیگر در دسترس نیست."))
        amount = round(amount, 2) if kind == "gb" else int(amount)
        if amount < cfg["min"] or amount > cfg["max"]:
            raise VpnError(N_("مقدار خارج از بازه‌ی مجاز است."))
        if kind == "days" and not ag["expires_at"]:
            raise VpnError(N_("این پنل تاریخ انقضا ندارد؛ نیازی به خرید زمان نیست."))
        user = await self.db.get_user(user_id)
        price = self.shop._personal(int(round(cfg["price"] * amount)), user)
        if price <= 0:                                                 # گرد شدن به صفر = سفارش رایگان؛ ممنوع
            raise VpnError(N_("مقدار خارج از بازه‌ی مجاز است."))
        title = self.addon_title({"kind": kind, "amount": amount})
        return await self.db.create_vpn_order(user_id, f"{ag['username']} — {title}", int(amount) if kind == "days" else 0,
                                              f"vpn:aaddon:{agent_id}:c:{kind}:{self._amt_text(amount)}", price, 0)

    async def addon_order(self, user_id: int, agent_id: int, addon_id: int) -> int:
        ag = await self.db.get_agent(agent_id)
        ad = await self.db.get_agent_addon(addon_id)
        if not ag or ag["user_id"] != user_id or ag["status"] == "deleted":
            raise VpnError(N_("نماینده پیدا نشد."))
        if not ad or not ad["active"] or ad["plan_id"] != ag["plan_id"] or ad["id"] not in [a["id"] for a in await self.addons_for(ag)]:
            raise VpnError(N_("این بسته دیگر در دسترس نیست."))
        if ad["kind"] == "gb" and not ag["total_bytes"]:
            raise VpnError(N_("حجم این پنل نامحدود است؛ نیازی به خرید حجم نیست."))
        if ad["kind"] == "days" and not ag["expires_at"]:
            raise VpnError(N_("این پنل تاریخ انقضا ندارد؛ نیازی به خرید زمان نیست."))
        await self.vpn.guard_pending(user_id)
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
            if ref[2] == "c":                                # حجم دلخواه: vpn:agent:c:{gb}:{days}:{users}:{panel}[:{name}]
                await self._fulfil_new(o, 0, ref[7] if len(ref) > 7 else None, cv=(float(ref[3]), int(ref[4]), int(ref[5]), int(ref[6])))
            else:
                await self._fulfil_new(o, int(ref[2]), ref[3] if len(ref) > 3 else None)
        elif ref[1] == "arenew":
            await self._fulfil_renew(o, int(ref[2]), int(ref[3]), cv=(float(ref[4]), int(ref[5]), int(ref[6])) if len(ref) > 6 else None)
        else:
            raise VpnError("bad agent ref")

    async def _fulfil_new(self, o, plan_id, asked, cv=None):
        existing = await self.db.agent_by_order(o["id"])
        if existing:                                         # تلاش مجدد پس از ساخت موفق
            existing = await self.db.get_agent(existing["id"])
            await self.send_agent(existing["user_id"], existing, new=True)
            await self.shop.finish_order(o["id"], f"agent #{existing['id']}")
            return
        gb = cv[0] if cv else None
        plan = await self.cv_plan(cv[3], cv[0], cv[1], cv[2]) if cv else await self.db.get_agent_plan(plan_id)
        panel = await self.db.get_vpn_panel(plan["panel_id"]) if plan else None
        if not plan or not panel:
            raise VpnError("plan/panel missing")
        plan_id = plan.get("id") if cv else plan_id
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
                                          order_id=o["id"], title=plan["title"], last_sync=int(time.time()),
                                          **({"meta": json.dumps({"cv": 1, "custom_gb": cv[0], "custom_days": cv[1], "custom_users": cv[2]})} if cv else {}))
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

    async def _fulfil_renew(self, o, aid, plan_id, cv=None):
        ag = await self.db.get_agent(aid)
        plan = await self.cv_plan(ag["panel_id"], cv[0], cv[1], cv[2]) if (cv and ag) else await self.db.get_agent_plan(plan_id)
        if not ag or not plan or ag["status"] == "deleted":
            raise VpnError("agent/plan missing")
        if cv:
            plan_id = plan.get("id")
        meta = json.loads(ag["meta"] or "{}")
        if o["id"] not in meta.get("applied", []):
            drv = await self.vpn.driver(ag["panel_id"])
            add_gb = plan["traffic_gb"] or 0
            if add_gb and type(drv).admin_set_traffic is not vpn_panels.BasePanel.admin_set_traffic:
                # تمدید = اضافه‌شدن حجم پایه روی حجم فعلی؛ مصرف صفر نمی‌شود (۱۰۰ گیگ مصرف‌شده + تمدید ۱۰۰ ← کل ۲۰۰، باقی‌مانده ۱۰۰)
                new_total = int((ag["total_bytes"] or 0) + add_gb * GB)
                await drv.admin_set_traffic(ag["username"], new_total / GB)
                await drv.admin_set_enabled(ag["username"], True, ag["password"])
                new_used = ag["used_bytes"]
            else:                                          # پنلی که سقف حجم ادمین را پشتیبانی نمی‌کند: تمدید کلاسیک (حجم و مصرف از نو)
                await drv.admin_renew(ag["username"], agent_spec(plan), ag["password"])
                new_total, new_used = int(add_gb * GB), 0
            exp = vpn_panels.new_expiry(ag["expires_at"], plan["days"], self._flag("vpn_stack", "1"))
            meta["applied"] = (meta.get("applied", []) + [o["id"]])[-30:]
            if cv:
                meta.update(cv=1, custom_gb=cv[0], custom_days=cv[1], custom_users=cv[2])
            else:                                            # تمدید با پلن معمولی: دیگر «حجم دلخواه» نیست
                for k in ("custom_gb", "custom_days", "custom_users"):
                    meta.pop(k, None)
            await self.db.update_agent(aid, status="active" if (not new_total or new_used < new_total) else "limited", expires_at=exp, total_bytes=new_total, used_bytes=new_used,
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
        return bool(self._flag("vpn_autorenew_on", "1") and ag.get("plan_id") and ag["status"] != "deleted" and not self.is_custom(ag))

    @staticmethod
    def is_custom(ag) -> bool:
        try:
            return bool(json.loads(ag.get("meta") or "{}").get("custom_gb"))
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def is_cv(ag) -> bool:
        """پنلی که با «حجم دلخواه» خریده شده (حتی اگر بعداً با پلن معمولی تمدید شده باشد) ← قیمت حجم اضافه از تنظیمات حجم دلخواه."""
        try:
            m = json.loads(ag.get("meta") or "{}")
            return bool(m.get("cv") or m.get("custom_gb"))
        except (ValueError, AttributeError):
            return False

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
        if self.is_custom(ag):
            cfg = await self.cv_cfg()
            if cfg and cfg["panel"] == ag["panel_id"]:
                gb = json.loads(ag["meta"])["custom_gb"]
                user = await self.db.get_user(ag["user_id"])
                rows.append([btn(f"{tr('⚡ تمدید همین حجم')} {gb:g}GB | {fmt(self.shop._personal(int(round(gb * cfg['price'])), user))} {tr('ت')}", f"vpn:agq:{ag['id']}", role="action")])
        elif plan and plan["active"] and plan["panel_id"] == ag["panel_id"]:
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
            if act == "sub_days":
                if not ag["expires_at"]:
                    return False, N_("این نماینده تاریخ انقضا ندارد.")
                await self.db.update_agent(aid, expires_at=max(ag["expires_at"] - int(value) * 86400, int(time.time()) + 60))
            elif act == "add_days":
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

    # ------------------------------------------------------------------ تغییر لوکیشن نماینده (انتقال اکانت ادمین به پنل دیگر)
    def move_enabled(self) -> bool:
        return self._flag("vpn_agent_move")

    async def move_targets(self, ag, admin=False) -> list[dict]:
        """پنل‌های مقصد: فقط پنل‌های فعالِ پشتیبان‌ِ ساخت ادمین. نماینده: فقط پنل‌هایی که «مقصد نماینده‌ها» تیک خورده؛ ادمین: همه."""
        from vpn_service import pflag
        out = []
        for p in await self.db.list_vpn_panels():
            if p["id"] == ag["panel_id"] or not p["enabled"] or p["type"] == "manual" or self.vpn.is_down(p["id"]):
                continue
            if not admin and not pflag(p, "amove_to"):
                continue
            try:
                if not getattr(await self.vpn.driver(p["id"]), "ADMIN", False):
                    continue
            except Exception:  # noqa: BLE001
                continue
            out.append(p)
        return out

    def can_user_move(self, ag) -> bool:
        if not self.move_enabled() or ag["status"] not in ("active", "limited"):
            return False
        mx = self._int("vpn_agent_move_max", 1)
        return not (mx > 0 and json.loads(ag["meta"] or "{}").get("n_move", 0) >= mx)

    async def move(self, aid: int, target_pid: int, actor: str = "admin", user_id: int | None = None, batch: str = "") -> dict:
        """اکانت نماینده را با حجم/زمان/سقف کاربرِ باقی‌مانده روی پنل مقصد می‌سازد و اکانت قبلی را غیرفعال (نه حذف) می‌کند.
        کاربرانِ داخل پنل قبلیِ نماینده خودکار منتقل نمی‌شوند."""
        ag = await self.db.get_agent(aid)
        if not ag or ag["status"] == "deleted" or (user_id is not None and ag["user_id"] != user_id):
            raise MoveError(N_("نماینده پیدا نشد."))
        if user_id is not None and not self.can_user_move(ag):
            raise MoveError(N_("تغییر لوکیشن برای این پنل نمایندگی فعال نیست."))
        dst = next((p for p in await self.move_targets(ag, admin=user_id is None) if p["id"] == int(target_pid)), None)
        if not dst:
            raise MoveError(N_("این لوکیشن در دسترس نیست."))
        try:
            ag = await self.sync(ag, force=True)
        except Exception as e:  # noqa: BLE001
            raise MoveError(N_("ارتباط با پنل فعلی ممکن نشد؛ کمی بعد دوباره تلاش کنید.")) from e
        rest = (ag["total_bytes"] - ag["used_bytes"]) if ag["total_bytes"] else 0
        if ag["total_bytes"] and rest <= 0:
            raise MoveError(N_("حجم این پنل نمایندگی تمام شده است؛ ابتدا حجم اضافه بخرید یا تمدید کنید."))
        if ag["expires_at"] and ag["expires_at"] <= time.time():
            raise MoveError(N_("زمان این پنل نمایندگی تمام شده است؛ ابتدا تمدید کنید."))
        plan = await self.db.get_agent_plan(ag["plan_id"]) if ag.get("plan_id") else None
        if plan and plan["panel_id"] == dst["id"]:
            spec = agent_spec(plan)
        else:                                               # شناسه‌ی نقش/سرویس مخصوص هر پنل است؛ برای پنل دیگر پیش‌فرض (همه‌ی سرویس‌ها)
            spec = {"role_id": "", "service_ids": [], "all_services": True, "modify_users": True, "data_min_gb": 0, "data_max_gb": 0,
                    "expire_min_days": 0, "expire_max_days": 0, "note": ag["title"] or ""}
        spec["traffic_gb"] = math.ceil(rest / GB * 100) / 100 if rest else 0
        spec["max_users"] = ag["max_users"] or 0
        spec["telegram_id"] = 0
        drv = await self.vpn.driver(dst["id"])
        name, pw = await self.make_name(dst["id"], ag["user_id"], ag["username"] if not await self.db.one(
            "SELECT 1 x FROM vpn_agents WHERE panel_id=? AND username=? AND status!='deleted'", (dst["id"], ag["username"])) else None), gen_password()
        try:
            await drv.admin_create(name, pw, spec)
        except PanelError as e:
            raise MoveError(f"{N_('ساخت روی پنل جدید ناموفق بود')}: {str(e)[:160]}") from e
        meta = json.loads(ag["meta"] or "{}")
        meta["prev"] = {"panel_id": ag["panel_id"], "username": ag["username"], "password": ag["password"], "total": ag["total_bytes"], "used": ag["used_bytes"]}
        meta["n_move"] = int(meta.get("n_move", 0)) + 1
        old_panel, old_name, old_pw = ag["panel_id"], ag["username"], ag["password"]
        try:
            await self.db.update_agent(aid, panel_id=dst["id"], username=name, password=pw, total_bytes=int(rest) if ag["total_bytes"] else 0, used_bytes=0,
                                       users_count=0, status="active", notified="", meta=meta, last_sync=int(time.time()))
        except Exception:
            try:
                await drv.admin_delete(name)
            except Exception:  # noqa: BLE001
                log.exception("agent move rollback failed")
            raise MoveError(N_("ثبت انتقال ناموفق بود؛ سرویس قبلی دست‌نخورده ماند.")) from None
        try:                                                 # اکانت قبلی غیرفعال می‌شود، حذف نمی‌شود (کاربرانش سالم می‌مانند)
            await (await self.vpn.driver(old_panel)).admin_set_enabled(old_name, False, old_pw)
        except Exception as e:  # noqa: BLE001
            await self.shop.notify_admins(f"⚠️ نماینده #{aid} منتقل شد ولی غیرفعال‌کردن ادمین قبلی <code>{esc(old_name)}</code> روی پنل #{old_panel} ناموفق بود؛ دستی غیرفعالش کنید.\n{esc(str(e)[:150])}",
                                          perm="vpn", topic="alerts")
        await self.db.log(actor, "agent_move", f"#{aid} {old_panel}->{dst['id']}")
        await self.db.vm_add(batch, "agent", aid, ag["user_id"], old_panel, dst["id"], old_name, name, actor)
        return await self.db.get_agent(aid)

    async def revert_move(self, mid: int, actor: str = "admin") -> dict:
        """بازگرداندن نماینده به اکانت و پنل قبلی (اکانت قبلی دوباره فعال؛ اکانت جدید غیرفعال می‌شود، حذف نمی‌شود)."""
        row = await self.db.one("SELECT * FROM vpn_moves WHERE id=?", (mid,))
        if not row or row["kind"] != "agent" or row["status"] != "done":
            raise MoveError(N_("این انتقال قبلاً برگردانده شده یا پیدا نشد."))
        ag = await self.db.get_agent(row["sid"])
        meta = json.loads(ag["meta"] or "{}") if ag else {}
        prev = meta.get("prev")
        if not ag or ag["status"] == "deleted" or not prev or ag["panel_id"] != row["to_panel"]:
            raise MoveError(N_("این نماینده بعد از آن انتقال تغییر کرده است؛ از صفحه‌ی خودش اقدام کنید."))
        try:
            ag = await self.sync(ag, force=True)
        except Exception:  # noqa: BLE001
            pass
        new_used = ag["used_bytes"]
        try:
            await (await self.vpn.driver(prev["panel_id"])).admin_set_enabled(prev["username"], True, prev["password"])
        except PanelError as e:
            raise MoveError(f"{N_('خطای پنل')}: {str(e)[:160]}") from e
        try:
            await (await self.vpn.driver(ag["panel_id"])).admin_set_enabled(ag["username"], False, ag["password"])
        except Exception as e:  # noqa: BLE001
            log.warning("agent revert: could not disable new admin: %s", e)
        meta.pop("prev", None)
        await self.db.update_agent(ag["id"], panel_id=prev["panel_id"], username=prev["username"], password=prev["password"], total_bytes=prev["total"],
                                   used_bytes=min(prev["used"] + new_used, prev["total"]) if prev["total"] else prev["used"] + new_used, status="active",
                                   notified="", meta=meta, last_sync=int(time.time()))
        await self.db.vm_set(mid, "reverted")
        await self.db.log(actor, "agent_move_revert", f"#{ag['id']}")
        return await self.db.get_agent(ag["id"])

    async def notify_moved(self, ag, back: bool = False):
        tr = await self.shop.tr_for(ag["user_id"])
        t = N_("↩️ پنل نمایندگی شما به لوکیشن قبلی برگشت. آدرس و مشخصات ورود:") if back else \
            N_("📍 پنل نمایندگی شما به لوکیشن جدید منتقل شد؛ حجم و زمان باقی‌مانده حفظ شد. آدرس و مشخصات ورود جدید:")
        await self.shop.safe_send(ag["user_id"], tr(t))
        await self.send_agent(ag["user_id"], ag)

    async def revert_batch(self, batch: str, actor: str = "admin", progress=None) -> dict:
        import asyncio
        rows = [r for r in await self.db.vm_batch_rows(batch) if r["status"] == "done" and r["kind"] == "agent"]
        res = {"ok": 0, "fail": 0, "errors": []}
        for r in rows:
            try:
                await self.notify_moved(await self.revert_move(r["id"], actor), back=True)
                res["ok"] += 1
            except MoveError as e:
                res["fail"] += 1
                res["errors"].append(f"#{r['sid']}: {e}")
            except Exception as e:  # noqa: BLE001
                log.exception("agent revert failed")
                res["fail"] += 1
                res["errors"].append(f"#{r['sid']}: {str(e)[:100]}")
            await asyncio.sleep(0.25)
        return res

    # ------------------------------------------------------------------ عملیات گروهی روی نماینده‌ها
    async def select(self, panel_id, flt: str, limit: int = 3000) -> list[dict]:
        st = None if flt in ("all", "sel") else flt
        return await self.db.list_agents(status=st, panel_id=panel_id or None, limit=limit)

    async def search_page(self, panel_id, q: str, page: int, per: int = 8):
        total = await self.db.count_agents(q=q or "", panel_id=panel_id or None)
        return await self.db.list_agents(q=q or "", panel_id=panel_id or None, limit=per, offset=page * per), total

    async def bulk_run(self, rows: list[dict], act: str, value=None, target_pid=None, actor="admin", progress=None) -> dict:
        import asyncio
        res = {"ok": 0, "fail": 0, "errors": []}
        batch = res["batch"] = f"a{int(time.time())}" if act == "move" else ""
        seen = set()
        for i, ag in enumerate(rows, 1):
            try:
                if act in ("credit", "sub_credit", "discount"):
                    if ag["user_id"] in seen:
                        continue
                    seen.add(ag["user_id"])
                    from vpn_ops import _owner_act
                    ok, msg = await _owner_act(self.shop, ag["user_id"], act, value, actor)
                elif act == "add_days":
                    ok, msg = await self.admin_action(ag["id"], "add_days", value, actor=actor)
                elif act == "add_gb":
                    if not ag["total_bytes"]:
                        ok, msg = False, N_("حجم نامحدود است.")
                    else:
                        ok, msg = await self.admin_action(ag["id"], "set_traffic", ag["total_bytes"] / GB + float(value), actor=actor)
                elif act == "sub_days":
                    ok, msg = await self.admin_action(ag["id"], "sub_days", value, actor=actor)
                elif act == "sub_gb":
                    if not ag["total_bytes"]:
                        ok, msg = False, N_("حجم نامحدود است.")
                    else:
                        ok, msg = await self.admin_action(ag["id"], "set_traffic", max(ag["total_bytes"] / GB - float(value), (ag["used_bytes"] or 0) / GB, 0.01), actor=actor)
                elif act in ("enable", "disable"):
                    ok, msg = await self.admin_action(ag["id"], act, actor=actor)
                elif act == "move":
                    new = await self.move(ag["id"], int(target_pid), actor=actor, batch=batch)
                    await self.notify_moved(new)
                    ok, msg = True, ""
                elif act == "msg":
                    ok, msg = bool(await self.shop.safe_send(ag["user_id"], str(value))), N_("ارسال نشد")
                else:
                    ok, msg = False, N_("عملیات نامعتبر.")
                if ok:
                    res["ok"] += 1
                else:
                    res["fail"] += 1
                    res["errors"].append(f"{ag['username']}: {msg}")
            except MoveError as e:
                res["fail"] += 1
                res["errors"].append(f"{ag['username']}: {e}")
            except Exception as e:  # noqa: BLE001
                log.exception("agent bulk %s failed", act)
                res["fail"] += 1
                res["errors"].append(f"{ag['username']}: {str(e)[:100]}")
            if progress and i % 50 == 0:
                await progress(i, len(rows))
            await asyncio.sleep(0.25)
        return res

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
        if self.can_user_move(ag) and await self.move_targets(ag):
            rows.append([btn(tr("📍 تغییر لوکیشن"), f"vpn:agm:{i}", role="action")])
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
