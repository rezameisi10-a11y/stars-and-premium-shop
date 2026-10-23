import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiohttp import ClientSession, ClientTimeout, web

import provider
import ui
from apiutil import D
from config import Config
from db import DB, DEFAULT_SETTINGS, default_title
from groupfeed import GroupFeed
from i18n import CATALOG, N_, NW_, SRC_LANG
from pricing import PREMIUM_STARS, Pricing
from settings_schema import TEXT_KEYS, validate
from ui import btn, esc, fmt, ikb  # noqa: F401  (re-export برای هندلرها)

log = logging.getLogger("shop")

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

STATUS_FA = {
    "pending_payment": N_("⏳ در انتظار پرداخت"),
    "paid": N_("💳 پرداخت شده"),
    "delivering": N_("🚚 در حال تحویل"),
    "manual_pending": N_("🕓 در صف تحویل"),
    "failed": N_("⚠️ خطا در تحویل"),
    "done": N_("✅ تحویل شد"),
    "refunded": N_("↩️ بازگشت وجه"),
    "cancelled": N_("❌ لغو شد"),
}
PAY_STATUS_FA = {"pending": N_("⏳ منتظر پرداخت"), "waiting_admin": N_("🧾 منتظر تأیید ادمین"), "confirmed": N_("✅ تأیید شد"),
                 "rejected": N_("❌ رد شد"), "expired": N_("⌛ منقضی"), "failed": N_("⚠️ ناموفق")}
METHOD_FA = {"wallet": N_("کیف پول"), "card": N_("کارت به کارت"), "zarinpal": N_("درگاه بانکی"),
             "ton": N_("تون (TON)"), "usdt": N_("تتر (USDT TRC20)")}
KIND_FA = {"stars": N_("استارز"), "premium": N_("پریمیوم"), "gift": N_("گیفت")}

DISC_MSG = {
    "invalid": N_("کد تخفیف نامعتبر است."),
    "not_started": N_("این کد تخفیف هنوز فعال نشده است."),
    "expired": N_("مهلت این کد تخفیف تمام شده است."),
    "exhausted": N_("ظرفیت استفاده از این کد تخفیف تمام شده است."),
    "not_allowed": N_("این کد تخفیف برای حساب شما فعال نیست."),
    "used": N_("شما قبلاً از این کد استفاده کرده‌اید."),
    "kind": N_("این کد برای این نوع محصول قابل استفاده نیست."),
    "min_amount": N_("مبلغ سفارش برای استفاده از این کد کمتر از حداقل مجاز است."),
    "first_only": N_("این کد فقط برای اولین خرید است."),
    "no_profit": N_("این کد برای این محصول قابل استفاده نیست."),
    "unusable": N_("این کد برای این سفارش قابل استفاده نیست."),
}

# عملیات داخلی هر دکمه منوی اصلی ← callback_data
MENU_CALLBACKS = {"stars": "cat:stars:0", "premium": "cat:premium:0", "gift": "cat:gift:0", "wallet": "wallet",
                  "orders": "orders", "referral": "referral", "rules": "rules", "support": "support", "admin": "adm:home",
                  "lang": "lang"}

# ---------------------------------------------------------------- دسترسی ادمین‌ها
PERMS = {
    "stats": NW_("مشاهده‌ی داشبورد و آمار"),
    "orders": NW_("سفارش‌ها و صف تحویل"),
    "receipts": NW_("بررسی و تأیید پرداخت‌ها/رسیدها"),
    "users_view": NW_("مشاهده‌ی کاربران"),
    "users_edit": NW_("مدیریت کاربران (مسدود، موجودی، تخفیف، پیام)"),
    "wallet": NW_("تراکنش‌های کیف پول"),
    "products": NW_("محصولات"),
    "pricing": NW_("نرخ‌ها، قیمت‌گذاری، سود و تحویل خودکار"),
    "discounts": NW_("کدهای تخفیف"),
    "broadcast": NW_("پیام همگانی"),
    "buttons": NW_("چیدمان، رنگ و ایموجی دکمه‌ها"),
    "languages": NW_("زبان‌ها و ترجمه‌ها"),
    "settings": NW_("تنظیمات عمومی، درگاه‌ها و ضدتقلب"),
    "stars": NW_("موجودی ستاره ربات"),
    "backup": NW_("بکاپ و وارد کردن بکاپ"),
    "logs": NW_("لاگ فعالیت"),
    "admins": NW_("مدیریت ادمین‌ها"),
}
PERM_PRESETS = {
    "full": (NW_("مدیر کامل"), ["*"]),
    "receipts": (NW_("بررسی‌کننده‌ی رسید"), ["receipts", "users_view"]),
    "support": (NW_("پشتیبان"), ["orders", "users_view", "users_edit", "wallet"]),
    "sales": (NW_("مدیر فروش"), ["stats", "orders", "receipts", "users_view", "users_edit", "products", "discounts", "wallet"]),
    "content": (NW_("مدیر محتوا"), ["buttons", "languages", "broadcast", "users_view"]),
    "finance": (NW_("مدیر مالی"), ["stats", "pricing", "stars", "wallet", "receipts", "backup"]),
}


def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2${salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex(), h)


def parse_perms(raw: str):
    raw = (raw or "").strip()
    if raw == "*":
        return "*"
    return {p for p in raw.split(",") if p in PERMS}


def normalize_url(u):
    u = (u or "").strip()
    if u.startswith(("t.me/", "telegram.me/")):
        u = "https://" + u
    elif u.startswith("@") and len(u) > 2:
        u = "https://t.me/" + u[1:]
    return u if u.startswith(("https://", "http://", "tg://")) else None


def describe_order(o: dict) -> str:
    """متن سفارش برای ادمین (فارسی)."""
    target = o.get("target_username") or o.get("target_id") or "-"
    if o.get("target_username"):
        target = "@" + str(target).lstrip("@")
    disc = f"\nکد تخفیف: {esc(o['discount_code'])}" if o.get("discount_code") else ""
    prof = ""
    if o.get("cost"):
        prof = f"\nهزینه: {fmt(o['cost'])} | سود: {fmt(o['amount'] - o['cost'])} تومان"
    return (f"سفارش #{o['id']} | {esc(o['title'])}\nکاربر: <code>{o['user_id']}</code>\nگیرنده: {esc(target)}\n"
            f"مبلغ: {fmt(o['amount'])} تومان{disc}{prof}\nوضعیت: {STATUS_FA.get(o['status'], o['status'])}"
            + (f"\nیادداشت: {esc(o['note'])}" if o.get("note") else ""))


TITLE_TEMPLATES = {"stars": N_("⭐ {n} استارز"), "premium": N_("👑 پریمیوم {n} ماهه"), "gift": N_("🎁 گیفت {n} ستاره‌ای")}
_GIFT_RE = re.compile(r"^(.+?) گیفت (\d+) ستاره‌ای$")


class Shop:
    def __init__(self, bot: Bot, db: DB, cfg: Config):
        self.bot = bot
        self.db = db
        self.cfg = cfg
        self.http: ClientSession | None = None
        self.bot_username = ""
        self._settings_cache: dict | None = None
        self.admin_ids: set[int] = set(cfg.admin_ids)
        self.admin_perms: dict[int, object] = {a: "*" for a in cfg.admin_ids}
        self.login_tokens: dict[str, tuple[int, float]] = {}
        self.broadcast_state = {"running": False, "total": 0, "sent": 0, "failed": 0}
        self._tasks: set[asyncio.Task] = set()
        self._star_alert_ts = 0.0
        self._join_cache: dict[int, tuple[dict, float]] = {}
        self._lang_cache: dict[int, str] = {}
        self.langs: list[dict] = []
        self.pricing = Pricing(self)
        self.group = GroupFeed(self)
        self._rate_last: dict[str, float] = {}
        self._ref_watch: set[int] = set()
        self._warned: dict[str, float] = {}
        self.provider_balance: Decimal | None = None
        self._prov_bal_ts = 0.0
        self.restart_requested = False
        self.restart_event = asyncio.Event()

    async def start(self):
        self.http = ClientSession(timeout=ClientTimeout(total=20))
        self.bot_username = (await self.bot.me()).username or ""
        CATALOG.load()
        await self.refresh_admins()
        await self.settings()
        await self.reload_i18n()
        self._ref_watch = {r["referred_id"] for r in await self.db.all("SELECT referred_id FROM referrals WHERE status IN ('pending','left')")}
        if (await self.settings()).get("rate_auto") == "1":
            self.spawn(self.pricing.refresh_rates())
        self.spawn(self.apply_miniapp_menu())

    async def stop(self):
        if self.http:
            await self.http.close()

    def spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def request_restart(self):
        self.restart_requested = True
        self.restart_event.set()

    async def reload_after_restore(self):
        """بعد از جایگزینی دیتابیس: همه‌ی کش‌ها از نو ساخته می‌شوند."""
        await self._reload_settings()
        await self.refresh_admins()
        await self.reload_i18n()
        self._lang_cache.clear()
        self._join_cache.clear()
        self.pricing.rates.clear()
        self.pricing.costs.clear()
        self.pricing._last_rates_run = 0
        self._rate_last.clear()
        self._ref_watch = {r["referred_id"] for r in await self.db.all("SELECT referred_id FROM referrals WHERE status IN ('pending','left')")}

    # ---------- تنظیمات / ادمین‌ها ----------
    async def settings(self) -> dict:
        """کش تنظیمات (فقط‌خواندنی؛ برای نوشتن از set_setting استفاده شود)."""
        if self._settings_cache is None:
            self._settings_cache = await self.db.settings()
            ui.set_styles(self._settings_cache)
        return self._settings_cache

    @property
    def s(self) -> dict:
        return self._settings_cache or {}

    async def _reload_settings(self):
        self._settings_cache = None
        await self.settings()

    async def set_setting(self, key, value, actor="system"):
        await self.db.set_setting(key, value)
        secret = key in ("zarinpal_merchant", "toncenter_key", "trongrid_key") or "headers" in key
        await self.db.log(actor, "setting", f"{key}={'***' if secret else str(value)[:80]}")
        await self._reload_settings()
        if key.startswith("miniapp_"):
            self.spawn(self.apply_miniapp_menu())
        if key.startswith(("usd_api", "ton_api", "rate_", "price_", "frag_")):
            self.pricing._last_rates_run = 0
            self.pricing._last_costs_run = 0

    async def set_setting_validated(self, key, raw, actor="system"):
        ok, res = validate(key, raw)
        if ok and key in ("default_lang", "web_default_lang"):
            lg = await self.db.get_language(res)
            if not lg:
                return False, NW_("این زبان در لیست زبان‌ها نیست؛ ابتدا از بخش «زبان‌ها» اضافه کنید.")
        if ok:
            await self.set_setting(key, res, actor)
        return ok, res

    async def set_style(self, role, style, actor="system"):
        if role not in ui.ROLE_DEFAULTS or style not in ui.VALID_STYLES:
            return False
        await self.set_setting("style_" + role, style, actor)
        return True

    async def set_role_emoji(self, role, emoji_id, actor="system"):
        if role not in ui.ROLE_DEFAULTS or (emoji_id and not emoji_id.isdigit()):
            return False
        await self.set_setting("emoji_" + role, emoji_id, actor)
        return True

    # ---------- شخصی‌سازی تک‌تک دکمه‌ها (کلید = ui.bkey(callback_data)) ----------
    @staticmethod
    def button_catalog() -> list[dict]:
        import json
        import os
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "button_catalog.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def button_overrides(self) -> dict:
        return ui.load_overrides(self.s.get("btn_ovr", ""))

    async def set_button_override(self, key, *, s=None, e=None, t=None, actor="system"):
        """فقط فیلدهای ارسالی تغییر می‌کنند؛ مقدار "" یعنی حذف همان فیلد. s: d|primary|success|danger"""
        import json
        cat = {c["key"]: c for c in self.button_catalog()}
        if key not in cat:
            return False, "دکمه‌ی نامعتبر"
        cur = self.button_overrides().get(key, {"s": None, "e": "", "t": ""})
        if s is not None:
            if s not in ("", "d", "primary", "success", "danger"):
                return False, "رنگ نامعتبر"
            cur["s"] = s or None
        if e is not None:
            if e and not (e.isdigit() and 5 <= len(e) <= 25):
                return False, "شناسه‌ی ایموجی باید عدد باشد"
            cur["e"] = e
        if t is not None:
            if t and cat[key]["dyn"]:
                return False, "متن این دکمه پویاست و قابل تغییر نیست"
            cur["t"] = t.strip()[:60]
        allv = self.button_overrides()
        if cur["s"] or cur["e"] or cur["t"]:
            allv[key] = cur
        else:
            allv.pop(key, None)
        await self.set_setting("btn_ovr", json.dumps(allv, ensure_ascii=False), actor)
        return True, ""

    async def replace_button_overrides(self, items: dict, actor="system"):
        """جایگزینی یکجای همه‌ی شخصی‌سازی‌ها (فرم وب). items: {key: {"s","e","t"}}؛ ورودی‌های نامعتبر نادیده/خطا می‌شوند."""
        import json
        cat = {c["key"]: c for c in self.button_catalog()}
        clean, errs = {}, []
        for k, v in items.items():
            if k not in cat:
                continue
            st = (v.get("s") or "")
            em = (v.get("e") or "").strip()
            tx = (v.get("t") or "").strip()[:60]
            if st not in ("", "d", "primary", "success", "danger"):
                errs.append(k)
                continue
            if em and not (em.isdigit() and 5 <= len(em) <= 25):
                errs.append(k)
                continue
            if tx and cat[k]["dyn"]:
                tx = ""
            if st or em or tx:
                clean[k] = {"s": st or None, "e": em, "t": tx}
        await self.set_setting("btn_ovr", json.dumps(clean, ensure_ascii=False) if clean else "", actor)
        return errs

    async def reset_button_overrides(self, actor="system"):
        await self.set_setting("btn_ovr", "", actor)

    async def refresh_admins(self):
        self.admin_perms = {a: "*" for a in self.cfg.admin_ids}
        for a in await self.db.list_admins():
            if a["user_id"] not in self.admin_perms:
                self.admin_perms[a["user_id"]] = parse_perms(a["perms"])
        self.admin_ids = set(self.admin_perms)

    def is_admin(self, uid: int) -> bool:
        return uid in self.admin_ids

    def can(self, uid: int, perm: str | None) -> bool:
        p = self.admin_perms.get(uid)
        if p is None:
            return False
        return perm is None or p == "*" or perm in p

    def admins_with(self, perm: str | None):
        return [a for a in self.admin_ids if self.can(a, perm)]

    def gw(self, key: str) -> str:
        """مقدار تنظیمات درگاه: اول از پنل (دیتابیس)، در غیر این‌صورت از .env قدیمی."""
        v = (self.s.get(key) or "").strip()
        if v:
            return v
        c = self.cfg
        return {"zarinpal_merchant": c.zarinpal_merchant, "ton_wallet": c.ton_wallet, "toncenter_key": c.toncenter_key,
                "usdt_wallet": c.usdt_trc20_wallet, "trongrid_key": c.trongrid_key}.get(key, "")

    # ---------- زبان‌ها ----------
    async def reload_i18n(self):
        CATALOG.set_overrides(await self.db.all_translations())
        self.langs = await self.db.list_languages()

    def bot_langs(self):
        return [l for l in self.langs if l["bot_enabled"]]

    def default_lang(self) -> str:
        d = self.s.get("default_lang") or SRC_LANG
        return d if any(l["code"] == d for l in self.langs) else SRC_LANG

    def resolve_bot_lang(self, code) -> str | None:
        if not code:
            return None
        code = code.lower()
        for cand in (code, code.split("-")[0]):
            if any(l["code"] == cand and l["bot_enabled"] for l in self.langs):
                return cand
        return None

    async def user_lang(self, uid: int) -> str:
        if uid in self._lang_cache:
            return self._lang_cache[uid]
        u = await self.db.get_user(uid)
        lang = self.resolve_bot_lang(u["lang"]) if u and u.get("lang") else None
        lang = lang or self.default_lang()
        self._lang_cache[uid] = lang
        return lang

    async def set_user_lang(self, uid: int, lang: str):
        await self.db.set_user_lang(uid, lang)
        self._lang_cache[uid] = lang

    def tr_lang(self, lang):
        return CATALOG.translator("bot", lang or SRC_LANG)

    async def tr_for(self, uid: int):
        return self.tr_lang(await self.user_lang(uid))

    async def say(self, uid, src, markup=None, **kw):
        """ارسال پیام به کاربر به زبان خودش؛ src متن فارسی منبع است (با {متغیر})."""
        tr = await self.tr_for(uid)
        return await self.safe_send(uid, tr(src, **kw), reply_markup=markup)

    def text(self, key: str, lang: str) -> str:
        """متن‌های قابل‌ویرایش ربات (خوش‌آمد، قوانین ...) به زبان کاربر."""
        base = self.s.get(key, "")
        if lang == SRC_LANG:
            return CATALOG.over.get(("bot", lang), {}).get("@" + key) or base
        ov = CATALOG.over.get(("bot", lang), {}).get("@" + key)
        if ov:
            return ov
        if base and base == DEFAULT_SETTINGS.get(key):
            return CATALOG.get("bot", lang, base)
        return base

    def menu_label(self, it: dict, lang: str) -> str:
        from db import DEFAULT_MENU
        ov = CATALOG.over.get(("bot", lang), {}).get(f"@btn:{it['id']}")
        if ov:
            return ov
        if lang == SRC_LANG:
            return it["label"]
        if any(it["label"] == lab for a, lab, *_ in DEFAULT_MENU if a == it["action"]):
            return CATALOG.get("bot", lang, it["label"])
        return it["label"]

    def button_text(self, it: dict, lang: str) -> str:
        """متن پیام دکمه‌ی «متن سفارشی» به زبان کاربر (در صورت وجود ترجمه‌ی اختصاصی)."""
        return CATALOG.over.get(("bot", lang), {}).get(f"@btntext:{it['id']}") or it["extra"]

    def product_title(self, p: dict, lang: str) -> str:
        ov = CATALOG.over.get(("bot", lang), {}).get(f"@prod:{p['id']}")
        if ov:
            return ov
        if lang == SRC_LANG:
            return p["title"]
        tr = self.tr_lang(lang)
        if p["title"] == default_title(p["kind"], p["qty"]):
            return tr(TITLE_TEMPLATES[p["kind"]], n=p["qty"])
        m = _GIFT_RE.match(p["title"])
        if m and p["kind"] == "gift":
            return tr("{e} گیفت {n} ستاره‌ای", e=m.group(1), n=m.group(2))
        return p["title"]

    # ---------- محتوای سفارشی (قابل‌ترجمه توسط ادمین) ----------
    CONTENT_TEXTS = [("welcome_text", NW_("متن خوش‌آمدگویی")), ("rules_text", NW_("متن قوانین")),
                     ("support_text", NW_("متن پشتیبانی")), ("maintenance_text", NW_("متن حالت تعمیر")),
                     ("join_text", NW_("متن عضویت اجباری")), ("success_text", NW_("متن پس از خرید"))]

    async def dynamic_items(self):
        """فهرست (کلید ترجمه، برچسب بخش، متن فارسی منبع) برای متن‌هایی که ادمین خودش نوشته و باید
        برای هر زبان جداگانه ترجمه شود؛ کلیدها با @ شروع می‌شوند و در جدول translations (scope=bot) ذخیره می‌شوند."""
        s = await self.settings()
        out = []
        for key, label in self.CONTENT_TEXTS:
            base = s.get(key, "")
            if base:
                out.append((f"@{key}", label, base))
        for it in await self.db.list_menu():
            out.append((f"@btn:{it['id']}", NW_("دکمه‌ی منو"), it["label"]))
            if it["action"] == "text" and it.get("extra"):
                out.append((f"@btntext:{it['id']}", NW_("پیام دکمه‌ی متنی"), it["extra"]))
        for kind in ("stars", "premium", "gift"):
            for p in await self.db.list_products(kind, only_active=False):
                out.append((f"@prod:{p['id']}", NW_("عنوان محصول"), p["title"]))
        return out

    def effective_content(self, key: str, base: str, lang: str) -> str:
        """متنی که همین الان به کاربرِ آن زبان نشان داده می‌شود (برای نمایش در پنل)."""
        kind, _sep, ident = key[1:].partition(":")
        try:
            if kind == "btn":
                from db import DEFAULT_MENU
                ov = CATALOG.over.get(("bot", lang), {}).get(key)
                if ov or lang == SRC_LANG:
                    return ov or base
                return CATALOG.get("bot", lang, base) if any(base == lab for _a, lab, *_r in DEFAULT_MENU) else base
            if kind == "btntext":
                return self.button_text({"id": int(ident), "extra": base}, lang)
            if kind == "prod":
                return CATALOG.over.get(("bot", lang), {}).get(key) or base
        except ValueError:
            return base
        return self.text(kind, lang)

    # ---------- قیمت ----------
    def price(self, product, s=None) -> int:
        return self.pricing.quote(product)[0]

    def price_u(self, product, user: dict | None) -> int:
        """قیمت نمایشی برای کاربر (با احتساب تخفیف شخصی)."""
        p = self.pricing.quote(product)[0]
        return self._personal(p, user)

    # ---------- نمایش قیمت: تومان/ریال + معادل تتر و تون ----------
    def equivalents(self, amount) -> dict:
        """معادل یک مبلغ تومانی: ریال (×۱۰)، تتر و تون با نرخ‌های فعلی (None اگر نرخ در دسترس نباشد)."""
        amount = int(amount or 0)
        usd, ton = self.pricing.usd_toman(), self.pricing.ton_toman()
        return {"toman": amount, "rial": amount * 10,
                "usdt": (Decimal(amount) / usd) if usd > 0 else None, "ton": (Decimal(amount) / ton) if ton > 0 else None}

    def money(self, amount, tr, short=False) -> str:
        """مبلغ به واحد انتخابی ادمین (تومان | ریال | هر دو)."""
        unit = (self.s or {}).get("price_unit", "toman")
        out = []
        if unit in ("toman", "both") or (short and unit == "both"):
            out.append(f"{fmt(amount)} {tr('ت') if short else tr('تومان')}")
        if unit == "rial" or (unit == "both" and not short):
            out.append(f"{fmt(int(amount) * 10)} {tr('ریال')}")
        return " | ".join(out)

    def crypto_line(self, amount) -> str:
        """«≈ 12.34 USDT | 4.512 TON» طبق تنظیمات؛ خالی اگر خاموش یا نرخ موجود نباشد."""
        s = self.s or {}
        eq = self.equivalents(amount)
        parts = []
        if s.get("show_usdt_price", "1") == "1" and eq["usdt"] is not None:
            parts.append(f"{eq['usdt']:,.2f} USDT")
        if s.get("show_ton_price", "1") == "1" and eq["ton"] is not None:
            parts.append(f"{eq['ton']:,.3f} TON")
        return ("≈ " + " | ".join(parts)) if parts else ""

    def _personal(self, price: int, user: dict | None) -> int:
        pd = (user or {}).get("personal_discount") or 0
        if pd:
            price = max(price * (100 - pd) // 100, int(D(self.s.get("min_price"), 1000)) if price >= 1000 else 0)
        return int(price)

    async def quote_order(self, product, user: dict | None):
        """(قیمت نهایی کاربر, هزینه‌ی تمام‌شده) هنگام ساخت سفارش، با قیمت تازه‌ی API."""
        price, cost = await self.pricing.quote_fresh(product)
        return self._personal(price, user), cost

    # ---------- ارسال ----------
    async def safe_send(self, chat_id, text, **kw):
        try:
            return await self.bot.send_message(chat_id, text, **kw)
        except Exception as e:  # کاربر ربات را بلاک کرده یا ...
            log.warning("send to %s failed: %s", chat_id, e)

    async def notify_admins(self, text, markup=None, perm=None, topic=None):
        for aid in self.admins_with(perm):
            await self.safe_send(aid, text, reply_markup=markup)
        if topic:                       # کپی در تاپیک مربوط گروه (اگر متصل و روشن باشد)
            await self.group.post(topic, text, markup=markup)

    def warn_once(self, key: str, hours: float = 6) -> bool:
        """True اگر در ساعت‌های اخیر برای این کلید هشدار نداده‌ایم (برای جلوگیری از اسپم هشدار)."""
        if time.time() - self._warned.get(key, 0) > hours * 3600:
            self._warned[key] = time.time()
            return True
        return False

    async def star_balance(self):
        """موجودی ستاره ربات؛ اگر خطا بدهد None."""
        try:
            return int((await self.bot.get_my_star_balance()).amount)
        except Exception as e:
            log.warning("star balance failed: %s", e)
            return None

    async def sync_gifts(self) -> int:
        res = await self.bot.get_available_gifts()
        items = []
        for g in res.gifts:
            emoji = getattr(getattr(g, "sticker", None), "emoji", "") or "🎁"
            remaining = getattr(g, "remaining_count", None)
            items.append((g.id, g.star_count, f"{emoji} گیفت {g.star_count} ستاره‌ای", remaining is None or remaining > 0))
        await self.db.sync_gifts(items)
        return len(items)

    # ---------- منوی اصلی ----------
    def _menu_button(self, it, lang):
        a = it["action"]
        label = self.menu_label(it, lang)
        emoji = it.get("emoji_id") or None
        if a == "url":
            u = normalize_url(it["extra"])
            return btn(label, url=u, style=it["style"], emoji=emoji) if u else None
        if a == "text":
            return btn(label, f"txt:{it['id']}", style=it["style"], emoji=emoji)
        if a in MENU_CALLBACKS:
            return btn(label, MENU_CALLBACKS[a], style=it["style"], emoji=emoji)
        return None

    async def main_menu_kb(self, uid):
        lang = await self.user_lang(uid)
        rows: dict[int, list] = {}
        multi = len(self.bot_langs()) > 1
        for it in await self.db.list_menu(only_enabled=True):
            if it["action"] == "admin" and not self.is_admin(uid):
                continue
            if it["action"] == "lang" and not multi:
                continue
            b = self._menu_button(it, lang)
            if b:
                rows.setdefault(it["row_no"], []).append(b)
        kb_rows = [rows[k] for k in sorted(rows)]
        url = self.miniapp_url()
        if url:
            kb_rows.insert(0, [btn(self.miniapp_title(lang), "app", webapp=url, role="action")])
        return ikb(kb_rows)

    # ---------- مینی‌اپ ----------
    def miniapp_url(self) -> str:
        """آدرس مینی‌اپ (فقط HTTPS؛ تلگرام http را نمی‌پذیرد)."""
        base = self.cfg.public_url
        if not base.startswith("https://") or (self.s or {}).get("miniapp_enabled", "1") != "1":
            return ""
        return base + "/app"

    MINIAPP_TITLE = N_("🛍 فروشگاه")

    def miniapp_title(self, lang: str) -> str:
        t = (self.s or {}).get("miniapp_title") or DEFAULT_SETTINGS.get("miniapp_title", "🛍")
        if t == DEFAULT_SETTINGS.get("miniapp_title"):
            return CATALOG.get("bot", lang, t)
        return t

    async def apply_miniapp_menu(self):
        """دکمه‌ی Menu Button کنار کادر پیام را (برای همه‌ی کاربران) روی مینی‌اپ یا پیش‌فرض تنظیم می‌کند."""
        from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo
        url = self.miniapp_url()
        try:
            if url and (self.s or {}).get("miniapp_menu_button", "1") == "1":
                await self.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text=self.miniapp_title(self.default_lang())[:16],
                                                                                 web_app=WebAppInfo(url=url)))
            else:
                await self.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        except Exception as e:  # noqa: BLE001
            log.warning("set_chat_menu_button failed: %s", e)

    # ---------- عضویت اجباری (چند کانال) ----------
    FORCE_MAX = 10

    def force_list(self) -> list[dict]:
        """کانال‌های عضویت اجباری: [{c: @کانال یا شناسه, t: متن دکمه, s: رنگ دکمه, l: لینک دعوت, e: ایموجی پریمیوم}].
        اگر فهرست جدید خالی باشد، تنظیم قدیمی force_channel (یک کانال) خوانده می‌شود."""
        s = self.s or {}
        try:
            data = json.loads(s.get("force_channels") or "[]")
        except ValueError:
            data = []
        out = []
        for it in data if isinstance(data, list) else []:
            if isinstance(it, dict) and it.get("c"):
                em = str(it.get("e") or "")
                out.append({"c": str(it["c"]), "t": str(it.get("t") or "")[:40],
                            "s": it.get("s") if it.get("s") in ("primary", "success", "danger") else "",
                            "l": str(it.get("l") or "")[:200], "e": em if em.isdigit() else ""})
        if not out and s.get("force_channel"):
            out.append({"c": s["force_channel"], "t": "", "s": "", "l": "", "e": ""})
        return out

    @staticmethod
    def norm_channel(text: str):
        """(ok, @username | -100… | پیام خطا). لینک t.me و نام بدون @ هم پذیرفته می‌شود."""
        v = (text or "").strip()
        m = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,31})/?$", v)
        if m:
            return True, "@" + m.group(1)
        v = v.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if re.match(r"^-100\d{5,}$", v):
            return True, v
        v = v.lstrip("@")
        if re.match(r"^[A-Za-z][A-Za-z0-9_]{3,31}$", v):
            return True, "@" + v
        return False, NW_("کانال نامعتبر است. مثال: @mychannel یا لینک t.me/mychannel (برای کانال خصوصی: شناسه‌ی -100… و لینک دعوت)")

    def force_url(self, ch: dict) -> str:
        if ch.get("l"):
            return ch["l"]
        c = ch["c"]
        return "https://t.me/" + c.lstrip("@") if c.startswith("@") else ""

    async def set_force_list(self, items: list[dict], actor="system"):
        """ذخیره‌ی فهرست کانال‌ها پس از اعتبارسنجی. خروجی: (ok, پیام خطا)."""
        clean, seen = [], set()
        if len(items) > self.FORCE_MAX:
            return False, NW_("حداکثر {n} کانال.").replace("{n}", str(self.FORCE_MAX))
        for it in items:
            ok, c = self.norm_channel(it.get("c", ""))
            if not ok:
                return False, c
            if c.lower() in seen:
                return False, NW_("این کانال قبلاً اضافه شده است.")
            seen.add(c.lower())
            link = str(it.get("l") or "").strip()
            if link and not re.match(r"^https://t\.me/\S+$", link):
                return False, NW_("لینک دعوت باید به شکل https://t.me/... باشد.")
            if c.startswith("-100") and not link:
                return False, NW_("برای کانال خصوصی لینک دعوت (https://t.me/+...) هم لازم است.")
            style = it.get("s") if it.get("s") in ("primary", "success", "danger") else ""
            em = str(it.get("e") or "")
            clean.append({"c": c, "t": str(it.get("t") or "").strip()[:40], "s": style, "l": link, "e": em if em.isdigit() else ""})
        await self.set_setting("force_channels", json.dumps(clean, ensure_ascii=False), actor)
        if (self.s or {}).get("force_channel"):
            await self.set_setting("force_channel", "", actor)     # به فهرست جدید منتقل شد
        self._join_cache.clear()
        return True, ""

    async def member_detail(self, uid, live=False) -> dict:
        """{کانال: True عضو | False غیرعضو | None نامشخص}؛ خالی اگر عضویت اجباری خاموش است."""
        s = await self.settings()
        chans = self.force_list()
        if s.get("force_join") != "1" or not chans:
            return {}
        if not live:
            cached = self._join_cache.get(uid)
            if cached and cached[1] > time.time():
                return cached[0]
        det = {}
        for ch in chans:
            try:
                m = await self.bot.get_chat_member(ch["c"], uid)
                st = getattr(m.status, "value", m.status)
                det[ch["c"]] = st in ("member", "administrator", "creator") or (st == "restricted" and getattr(m, "is_member", False))
            except Exception as e:
                log.warning("membership check failed for %s: %s", ch["c"], e)
                if self.warn_once("member_check:" + ch["c"]):
                    await self.notify_admins(f"⚠️ بررسی عضویت کانال {ch['c']} ممکن نیست؛ ربات را ادمین این کانال کنید. تا آن موقع جایزه‌های زیرمجموعه‌گیری نگه داشته می‌شود.",
                                             perm="settings")
                det[ch["c"]] = None
        self._join_cache[uid] = (det, time.time() + (300 if all(v is True for v in det.values()) else 5))
        return det

    async def member_status(self, uid, live=False):
        """True عضو همه‌ی کانال‌ها · False در دست‌کم یکی عضو نیست · None نامشخص (ربات ادمین کانال نیست/خطا)."""
        det = await self.member_detail(uid, live)
        if not det:
            return True
        if any(v is False for v in det.values()):
            return False
        if any(v is None for v in det.values()):
            return None
        return True

    async def join_buttons(self, uid, tr) -> list[dict]:
        """دکمه‌های کانال‌هایی که کاربر باید عضو شود: [{t, url, s, e}] (فقط کانال‌های ناعضو؛ اگر نامشخص بود همه)."""
        det = await self.member_detail(uid)
        chans = self.force_list()
        todo = [c for c in chans if det.get(c["c"]) is not True] or chans
        out = []
        for i, ch in enumerate(todo, 1):
            url = self.force_url(ch)
            if not url:
                continue
            label = ch["t"] or (tr("📢 عضویت در کانال") + (f" {i}" if len(todo) > 1 else ""))
            out.append({"t": label, "url": url, "s": ch["s"], "e": ch["e"]})
        return out

    async def join_kb(self, uid, tr):
        rows = [[btn(b["t"], url=b["url"], style=b["s"] or "primary", emoji=b["e"] or None)] for b in await self.join_buttons(uid, tr)]
        rows.append([btn(tr("✅ بررسی عضویت"), "jchk", role="confirm")])
        return ikb(rows)

    async def is_member(self, uid) -> bool:
        if self.is_admin(uid):
            return True
        return (await self.member_status(uid)) is not False   # اگر بررسی ممکن نباشد، کاربران قفل نشوند

    # ---------- زیرمجموعه‌گیری و ضدتقلب ----------
    def _need_join(self) -> bool:
        s = self.s
        return s.get("referral_require_join") == "1" and s.get("force_join") == "1" and bool(self.force_list())

    async def register_referral(self, uid: int, payload: str | None, is_new: bool):
        """ثبت معرف از لینک /start ref_<id>. فقط برای کاربر کاملاً جدید؛ جایزه تا تأیید عضویت کانال «در انتظار» می‌ماند."""
        if not is_new or not payload or not payload.startswith("ref_"):
            return
        try:
            ref = int(payload[4:])
        except ValueError:
            return
        if ref == uid or self.is_admin(uid):
            return
        r = await self.db.get_user(ref)
        if not r or r["banned"]:
            return
        up = await self.db.get_referral(ref)           # چرخه‌ی معرف: A→B و B→A
        if up and up["referrer_id"] == uid:
            return
        mx = int(D(self.s.get("referral_max_per_day"), 0))
        if mx and await self.db.count_referrals_today(ref) >= mx:
            await self.db.log("system", "referral_limit", f"referrer {ref} blocked new {uid}")
            return
        status = "pending" if self._need_join() else "active"
        if not await self.db.create_referral(uid, ref, status):
            return
        if status == "pending":
            self._ref_watch.add(uid)
        else:
            await self._activate_referral(uid, ref)

    async def on_gate_pass(self, uid: int):
        """وقتی کاربری از گیت عضویت رد شد (عضو است): زیرمجموعه‌ی در انتظار/ترک‌کرده فعال می‌شود."""
        if uid not in self._ref_watch:
            return
        ref = await self.db.get_referral(uid)
        if not ref or ref["status"] not in ("pending", "left"):
            self._ref_watch.discard(uid)
            return
        if self._need_join() and await self.member_status(uid, live=True) is not True:
            return
        was_pending = ref["status"] == "pending"
        await self.db.set_referral_status(uid, "active", "joined channel")
        self._ref_watch.discard(uid)
        if was_pending:
            await self._activate_referral(uid, ref["referrer_id"])

    async def _activate_referral(self, uid, referrer):
        await self.say(referrer, "🎉 یک کاربر جدید با لینک دعوت شما وارد ربات شد و عضویتش تأیید شد.")
        bonus = int(D(self.s.get("referral_join_bonus"), 0))
        if bonus > 0:
            await self._create_reward(referrer, uid, 0, "bonus", bonus)

    def _hold_until(self) -> int:
        h = D(self.s.get("referral_hold_hours"), 0)
        return int(time.time() + float(h) * 3600) if h > 0 else 0

    async def _create_reward(self, referrer, referred, order_id, kind, amount):
        release = self._hold_until()
        rid = await self.db.add_reward(referrer, referred, order_id, kind, amount, "held", release)
        if rid and not release:
            await self._pay_reward((await self.db.one("SELECT * FROM referral_rewards WHERE id=?", (rid,))))

    async def _pay_reward(self, rw: dict):
        """واریز جایزه با بررسی‌های ضدتقلب: فعال بودن زیرمجموعه، عدم مسدودی معرف و عضویت زنده در کانال."""
        ref = await self.db.get_referral(rw["referred_id"])
        referrer = await self.db.get_user(rw["referrer_id"])
        if not ref or ref["status"] != "active" or not referrer or referrer["banned"]:
            await self.db.set_reward_status(rw["id"], "void", "referral inactive/banned")
            return
        if self._need_join() and self.s.get("referral_recheck_join") == "1":
            m = await self.member_status(rw["referred_id"], live=True)
            if m is False:
                await self.db.set_reward_status(rw["id"], "void", "referred user left the channel")
                await self.db.set_referral_status(rw["referred_id"], "left", "left channel")
                self._ref_watch.add(rw["referred_id"])
                await self.db.log("system", "referral_void", f"reward {rw['id']} user {rw['referred_id']} left channel")
                return
            if m is None:     # قابل بررسی نیست: نگه دار و بعداً دوباره
                await self.db.run("UPDATE referral_rewards SET release_at=? WHERE id=?", (int(time.time()) + 3600, rw["id"]))
                return
        if not await self.db.set_reward_status(rw["id"], "paid", ""):
            return
        label = "bonus" if rw["kind"] == "bonus" else f"order {rw['order_id']}"
        if await self.db.add_balance(rw["referrer_id"], rw["amount"], f"referral: {label}", ref_key=f"rw:{rw['id']}"):
            await self.say(rw["referrer_id"], "🎉 پورسانت زیرمجموعه‌گیری: <b>{amount}</b> تومان به کیف پول شما اضافه شد.", amount=fmt(rw["amount"]))

    async def release_rewards(self):
        for rw in await self.db.due_rewards(int(time.time())):
            await self._pay_reward(rw)

    async def _referral_commission(self, o):
        s = await self.settings()
        pct = D(s.get("referral_percent"), 0)
        ref = await self.db.get_referral(o["user_id"])
        if pct <= 0 or not ref or ref["status"] != "active":
            return
        if o["amount"] < int(D(s.get("referral_min_order"), 0)):
            return
        commission = int(o["amount"] * pct / 100)
        if commission > 0:
            await self._create_reward(ref["referrer_id"], o["user_id"], o["id"], "commission", commission)

    # ---------- کیبورد روش‌های پرداخت ----------
    async def pay_methods_kb(self, ctx: str, user_id: int, allow_wallet: bool, tr=None, step=None):
        """step: None → خلاصه‌ی سفارش (کیف پول، کد تخفیف، «پرداخت») برای سفارش‌ها؛ "methods" → فهرست روش‌های پرداخت.
        شارژ کیف پول (ctx با t) و حالت خاموش بودن pay_step مستقیم روش‌ها را نشان می‌دهد."""
        tr = tr or await self.tr_for(user_id)
        s = await self.settings()
        two_step = allow_wallet and not ctx.startswith("t") and s.get("pay_step", "1") == "1"
        if two_step and step != "methods":
            rows = []
            if s["pm_wallet"] == "1":
                u = await self.db.get_user(user_id)
                rows.append([btn(tr("👛 کیف پول (موجودی {b} ت)", b=fmt(u["balance"])), f"pay:wallet:{ctx}", role="pay")])
            rows.append([btn(tr("🏷 کد تخفیف دارم"), f"disc:{ctx}", role="action")])
            rows.append([btn(tr("💳 پرداخت"), f"pm:{ctx}", role="pay")])
            rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
            return ikb(rows)
        rows = []
        if allow_wallet and s["pm_wallet"] == "1" and not two_step:
            u = await self.db.get_user(user_id)
            rows.append([btn(tr("👛 کیف پول (موجودی {b} ت)", b=fmt(u["balance"])), f"pay:wallet:{ctx}", role="pay")])
        if s["pm_card"] == "1" and s["card_number"]:
            rows.append([btn(tr("💳 کارت به کارت"), f"pay:card:{ctx}", role="pay")])
        if s["pm_zarinpal"] == "1" and self.gw("zarinpal_merchant") and self.cfg.public_url:
            rows.append([btn(tr("🏦 درگاه بانکی"), f"pay:zarinpal:{ctx}", role="pay")])
        if s["pm_ton"] == "1" and self.gw("ton_wallet"):
            rows.append([btn(tr("💎 تون (TON)"), f"pay:ton:{ctx}", role="pay")])
        if s["pm_usdt"] == "1" and self.gw("usdt_wallet"):
            rows.append([btn(tr("💵 تتر (USDT TRC20)"), f"pay:usdt:{ctx}", role="pay")])
        if two_step:
            if s["pm_wallet"] == "1":
                u = await self.db.get_user(user_id)
                rows.append([btn(tr("👛 کیف پول (موجودی {b} ت)", b=fmt(u["balance"])), f"pay:wallet:{ctx}", role="pay")])
            rows.append([btn(tr("🔙 بازگشت به سفارش"), f"ord:{ctx}", role="back")])
            return ikb(rows)
        if allow_wallet and not ctx.startswith("t"):
            rows.append([btn(tr("🏷 کد تخفیف دارم"), f"disc:{ctx}", role="action")])
        rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
        return ikb(rows)

    # ---------- کد تخفیف ----------
    async def apply_discount(self, oid, user_id, code, tr=None):
        tr = tr or await self.tr_for(user_id)
        o = await self.db.get_order(oid)
        if not o or o["user_id"] != user_id or o["status"] != "pending_payment":
            return False, tr("این سفارش دیگر قابل ویرایش نیست."), None
        base = o["orig_amount"] or o["amount"]
        ok, reason, disc = await self.db.check_discount(code, user_id, base, o["kind"], cost=o.get("cost") or 0)
        if not ok:
            return False, tr(DISC_MSG.get(reason, DISC_MSG["invalid"])), None
        new_amount = base - disc
        await self.db.set_order_discount(oid, code.strip().upper(), new_amount)
        return True, tr("✅ کد تخفیف اعمال شد: {d} تومان کسر شد.", d=fmt(disc)), new_amount

    async def discount_still_valid(self, oid) -> bool:
        """کد تخفیف فقط هنگام «پرداخت» مصرف می‌شود؛ پس چند سفارش باز می‌توانند یک کد محدود را نگه دارند.
        قبل از پرداخت دوباره بررسی می‌کنیم و اگر دیگر معتبر نبود، قیمت اصلی برمی‌گردد (False)."""
        o = await self.db.get_order(oid)
        if not o or not o.get("discount_code") or o["status"] != "pending_payment":
            return True
        base = o["orig_amount"] or o["amount"]
        ok, _r, disc = await self.db.check_discount(o["discount_code"], o["user_id"], base, o["kind"], cost=o.get("cost") or 0)
        if ok and base - disc == o["amount"]:
            return True
        await self.db.set_order_discount(oid, None, base)
        return False

    # ---------- ساخت پرداخت ----------
    async def create_payment(self, method, user_id, amount, order_id=None, tr=None):
        """خروجی: (payment_id, متن راهنما، کیبورد). در صورت مشکل ValueError با پیام (به زبان کاربر)."""
        tr = tr or await self.tr_for(user_id)
        if order_id and not await self.discount_still_valid(order_id):
            raise ValueError(tr("کد تخفیف دیگر معتبر نیست؛ قیمت اصلی اعمال شد. دوباره روش پرداخت را انتخاب کنید."))
        s = await self.settings()
        if method == "card":
            if not s["card_number"]:
                raise ValueError(tr("شماره کارت هنوز تنظیم نشده است."))
            pid = await self.db.create_payment(user_id, order_id, "card", amount)
            note = f"\n{esc(s['card_note'])}\n" if s.get("card_note") else ""
            text = (tr("💳 مبلغ <b>{amount}</b> تومان را به کارت زیر واریز کنید:", amount=fmt(amount)) + "\n\n"
                    f"<code>{esc(s['card_number'])}</code>\n" + tr("به نام: {owner}", owner=esc(s['card_owner'] or '-')) + f"\n{note}\n"
                    + tr("سپس <b>عکس رسید</b> را همین‌جا ارسال کنید.") + "\n" + tr("کد پیگیری: {code}", code=f"<code>P{pid}</code>"))
            return pid, text, None

        if method == "zarinpal":
            pid = await self.db.create_payment(user_id, order_id, "zarinpal", amount)
            authority = await self._zarinpal_request(amount, f"سفارش/شارژ P{pid}", pid)
            if not authority:
                await self.db.update_payment(pid, status="failed")
                raise ValueError(tr("اتصال به درگاه برقرار نشد، لطفا دوباره تلاش کنید یا روش دیگری انتخاب کنید."))
            await self.db.update_payment(pid, ref=authority)
            url = f"https://payment.zarinpal.com/pg/StartPay/{authority}"
            return pid, tr("🏦 برای پرداخت <b>{amount}</b> تومان روی دکمه زیر بزنید. بعد از پرداخت، نتیجه خودکار اعلام می‌شود.", amount=fmt(amount)), \
                ikb([[btn(tr("پرداخت آنلاین"), url=url, role="pay")]])

        if method == "ton":
            rate = self.pricing.ton_toman()
            if not (self.gw("ton_wallet") and rate > 0):
                raise ValueError(tr("پرداخت با تون فعال نیست."))
            ton = max(round(amount / float(rate), 4), 0.01)
            pid = await self.db.create_payment(user_id, order_id, "ton", amount, ton)
            text = (tr("💎 مقدار <b>{ton}</b> TON را به آدرس زیر ارسال کنید:", ton=ton) + f"\n<code>{esc(self.gw('ton_wallet'))}</code>\n\n"
                    + tr("⚠️ حتماً در بخش <b>Comment/Memo</b> این متن را وارد کنید:") + f"\n<code>P{pid}</code>\n\n"
                    + tr("پس از تأیید شبکه، خودکار ثبت می‌شود."))
            return pid, text, ikb([[btn(tr("🔄 بررسی پرداخت"), f"chk:{pid}", role="action")]])

        if method == "usdt":
            rate = self.pricing.usd_toman()
            if not (self.gw("usdt_wallet") and rate > 0):
                raise ValueError(tr("پرداخت با تتر فعال نیست."))
            base = round(amount / float(rate), 2)
            used = {round(p["crypto_amount"], 3) for p in await self.db.pending_payments("usdt")}
            usdt = None
            for k in range(1, 100):  # مبلغ یکتا برای شناسایی پرداخت (اعشار هزارم)
                cand = round(base + k * 0.001, 3)
                if cand not in used:
                    usdt = cand
                    break
            if usdt is None:
                raise ValueError(tr("ترافیک زیاد است، چند دقیقه بعد دوباره تلاش کنید."))
            pid = await self.db.create_payment(user_id, order_id, "usdt", amount, usdt)
            text = (tr("💵 دقیقاً مقدار <b>{usdt}</b> USDT را روی شبکه <b>TRC20</b> به آدرس زیر بفرستید:", usdt=f"{usdt:.3f}") + "\n"
                    f"<code>{esc(self.gw('usdt_wallet'))}</code>\n\n"
                    + tr("⚠️ مقدار باید با همین اعشار ارسال شود تا پرداخت شما شناسایی شود."))
            return pid, text, ikb([[btn(tr("🔄 بررسی پرداخت"), f"chk:{pid}", role="action")]])

        raise ValueError(tr("روش پرداخت نامعتبر است."))

    async def submit_receipt(self, pid, file_id):
        await self.db.update_payment(pid, receipt_file_id=file_id, status="waiting_admin")
        p = await self.db.get_payment(pid)
        for aid in self.admins_with("receipts"):
            await self.send_receipt_to(aid, p)
        await self.group_receipt(p)

    async def group_receipt(self, p):
        if not self.group.allowed("receipts"):
            return
        caption = (f"🧾 رسید کارت به کارت\nپرداخت P{p['id']} | کاربر <code>{p['user_id']}</code>\nمبلغ: {fmt(p['amount'])} تومان\n"
                   + (f"برای سفارش #{p['order_id']}" if p["order_id"] else "شارژ کیف پول"))
        kb = ikb([[btn("✅ تأیید", f"padm:ok:{p['id']}", role="confirm"), btn("❌ رد", f"padm:no:{p['id']}", role="reject")]])
        await self.group.post_photo("receipts", p["receipt_file_id"], caption, kb)

    async def send_receipt_to(self, chat_id, p):
        caption = (f"🧾 رسید کارت به کارت\nپرداخت P{p['id']} | کاربر <code>{p['user_id']}</code>\n"
                   f"مبلغ: {fmt(p['amount'])} تومان\n"
                   + (f"برای سفارش #{p['order_id']}" if p["order_id"] else "شارژ کیف پول"))
        kb = ikb([[btn("✅ تأیید", f"padm:ok:{p['id']}", role="confirm"), btn("❌ رد", f"padm:no:{p['id']}", role="reject")]])
        try:
            await self.bot.send_photo(chat_id, p["receipt_file_id"], caption=caption, reply_markup=kb)
        except Exception as e:
            log.warning("receipt to admin failed: %s", e)

    # ---------- تأیید پرداخت و سفارش ----------
    async def confirm_payment(self, pid, ref=None) -> bool:
        if not await self.db.claim_payment(pid, ref):
            return False
        p = await self.db.get_payment(pid)
        if p["order_id"] is None:
            await self.db.add_balance(p["user_id"], p["amount"], f"topup:P{pid}", ref_key=f"pay:{pid}")
            await self.say(p["user_id"], "✅ پرداخت تأیید شد و <b>{amount}</b> تومان به کیف پول شما اضافه شد.", amount=fmt(p["amount"]))
            await self.group.post("sales", f"👛 <b>شارژ کیف پول</b>\nکاربر: <code>{p['user_id']}</code>\nمبلغ: {fmt(p['amount'])} تومان | روش: {METHOD_FA.get(p['method'], p['method'])}")
            return True
        oid = p["order_id"]
        if await self.db.transition(oid, ["pending_payment"], "paid"):
            await self.say(p["user_id"], "✅ پرداخت سفارش #{oid} تأیید شد؛ در حال تحویل...", oid=oid)
            await self._after_paid(oid)
            await self.fulfil(oid)
        else:  # سفارش قبلاً پرداخت/لغو شده؛ مبلغ به کیف پول برمی‌گردد
            await self.db.add_balance(p["user_id"], p["amount"], f"fallback:P{pid} order {oid}", ref_key=f"payfb:{pid}")
            await self.say(p["user_id"], "ℹ️ سفارش #{oid} قبلاً پردازش شده بود؛ مبلغ پرداختی ({amount} تومان) به کیف پول شما اضافه شد.",
                           oid=oid, amount=fmt(p["amount"]))
        return True

    async def pay_with_wallet(self, oid, user_id) -> bool:
        o = await self.db.get_order(oid)
        if not o or o["user_id"] != user_id or o["status"] != "pending_payment":
            return False
        if not await self.discount_still_valid(oid):
            return False
        if not await self.db.deduct_balance(user_id, o["amount"], f"order:{oid}"):
            return False
        if not await self.db.transition(oid, ["pending_payment"], "paid"):
            await self.db.add_balance(user_id, o["amount"], f"rollback:order {oid}", ref_key=f"rb:{oid}")
            return False
        await self._after_paid(oid)
        await self.fulfil(oid)
        return True

    async def _after_paid(self, oid):
        o = await self.db.get_order(oid)
        if o.get("discount_code"):
            saved = max((o["orig_amount"] or o["amount"]) - o["amount"], 0)
            await self.db.use_discount(o["discount_code"], o["user_id"], oid, saved)
        s = await self.settings()
        if s.get("admin_notify_orders") == "1":
            await self.notify_admins("💰 سفارش پرداخت شد:\n\n" + describe_order(o), perm="orders")
        await self.group.post("sales", "🛒 <b>خرید جدید</b>\n\n" + describe_order(o))

    # ---------- تحویل ----------
    def order_admin_kb(self, o):
        rows = []
        if o["kind"] in ("gift", "premium"):
            rows.append([btn("🎁 ارسال خودکار با ستاره ربات", f"oadm:auto:{o['id']}", role="action")])
        if o["kind"] != "stars" or provider.provider_enabled(self.s, "stars"):
            rows.append([btn("🔁 تلاش مجدد", f"oadm:retry:{o['id']}", role="action")])
        rows.append([btn("✅ تحویل شد", f"oadm:done:{o['id']}", role="confirm"),
                     btn("↩️ بازگشت وجه به کیف پول", f"oadm:refund:{o['id']}", role="reject")])
        return ikb(rows)

    async def _to_manual(self, oid, note, admin_text):
        await self.db.transition(oid, ["delivering"], "manual_pending", note)
        o = await self.db.get_order(oid)
        await self.say(o["user_id"], "🕓 سفارش #{oid} پرداخت شد و در صف تحویل است. به‌محض ارسال، خبر می‌دهیم.", oid=oid)
        await self.notify_admins(admin_text + "\n\n" + describe_order(o), self.order_admin_kb(o), perm="orders", topic="alerts")

    async def _deliver_via_provider(self, o, kind):
        oid = o["id"]
        res, info, ref = await provider.deliver(self, kind, o)
        if ref:
            await self.db.set_order_provider_ref(oid, ref)
        await self.db.log("provider", f"deliver_{res}", f"order {oid} {info[:120]}")
        if res == "ok":
            await self.finish_order(oid, f"provider {ref}".strip())
            self.spawn(self._check_provider_balance(force=True))
            return
        if res == "fail":
            await self.db.transition(oid, ["delivering"], "failed", f"provider: {info}"[:300])
            o2 = await self.db.get_order(oid)
            await self.say(o["user_id"], "⚠️ در تحویل سفارش #{oid} مشکلی پیش آمد. پشتیبانی بررسی می‌کند و نتیجه اعلام می‌شود.", oid=oid)
            await self.notify_admins("❌ خطا در تحویل خودکار (API سرویس‌دهنده، ارسال انجام نشده):\n\n" + describe_order(o2),
                                     self.order_admin_kb(o2), perm="orders", topic="alerts")
            return
        await self._to_manual(oid, f"UNKNOWN: {info}"[:300],
                              "⚠️ نتیجه‌ی ارسال از سرویس‌دهنده نامشخص است (مهلت/خطای سرور). قبل از «تلاش مجدد» حتماً در پنل سرویس‌دهنده بررسی کنید که ارسال انجام نشده باشد.")

    async def fulfil(self, oid, force_auto=False):
        if not await self.db.transition(oid, ["paid", "failed", "manual_pending"], "delivering"):
            return
        o = await self.db.get_order(oid)
        uid = o["user_id"]
        s = await self.settings()
        try:
            if o["kind"] == "stars":
                if provider.provider_enabled(s, "stars"):
                    await self._deliver_via_provider(o, "stars")
                    return
                # ارسال استارز به کاربران دیگر از طریق Bot API ممکن نیست؛ تحویل دستی یا API سرویس‌دهنده
                await self._to_manual(oid, "تحویل دستی استارز لازم است", "⭐ سفارش استارز برای تحویل دستی:")
                return
            if o["kind"] == "premium":
                if provider.provider_enabled(s, "premium"):
                    await self._deliver_via_provider(o, "premium")
                    return
                if s.get("deliver_premium_mode") == "manual":
                    await self._to_manual(oid, "تحویل دستی پریمیوم", "👑 سفارش پریمیوم برای تحویل دستی:")
                    return

            if o["kind"] == "gift" and not force_auto and s.get("deliver_gift_mode") == "manual":
                await self._to_manual(oid, "تحویل دستی گیفت", "🎁 سفارش گیفت برای ارسال دستی (یا «ارسال خودکار با ستاره ربات»):")
                return

            need = PREMIUM_STARS[o["qty"]] if o["kind"] == "premium" else o["qty"]
            bal = await self.star_balance()
            if bal is not None and bal < need:
                await self._to_manual(oid, f"موجودی ستاره ربات کافی نیست ({bal} < {need})",
                                      f"⚠️ موجودی ستاره ربات کافی نیست ({bal} از {need}).\nاز پنل مدیریت ← «موجودی ستاره ربات» شارژ کنید؛ سفارش‌های صف خودکار تلاش مجدد می‌شوند.")
                return

            if o["kind"] == "premium":
                await self.bot.gift_premium_subscription(user_id=o["target_id"], month_count=o["qty"], star_count=need)
            else:
                await self.bot.send_gift(user_id=o["target_id"], gift_id=o["ref"])
        except Exception as e:
            log.exception("fulfil failed for order %s", oid)
            await self.db.transition(oid, ["delivering"], "failed", str(e)[:300])
            o = await self.db.get_order(oid)
            await self.say(uid, "⚠️ در تحویل سفارش #{oid} مشکلی پیش آمد. پشتیبانی بررسی می‌کند و نتیجه اعلام می‌شود.", oid=oid)
            await self.notify_admins("❌ خطا در تحویل خودکار:\n\n" + describe_order(o), self.order_admin_kb(o), perm="orders", topic="alerts")
            return
        await self.finish_order(oid)
        await self._star_alert()

    async def finish_order(self, oid, note=None) -> bool:
        if not await self.db.transition(oid, ["delivering", "manual_pending", "failed", "paid"], "done", note):
            return False
        o = await self.db.get_order(oid)
        tr = await self.tr_for(o["user_id"])
        lang = await self.user_lang(o["user_id"])
        p = await self.db.get_product(o["product_id"]) if o.get("product_id") else None
        title = self.product_title(p, lang) if p and p["title"] == o["title"] else o["title"]
        await self.safe_send(o["user_id"], tr("✅ سفارش #{oid} ({title}) با موفقیت تحویل داده شد.", oid=oid, title=esc(title)) + " "
                             + esc(self.text("success_text", lang)))
        await self._referral_commission(o)
        await self.group.post("sales", f"✅ سفارش #{oid} تحویل داده شد — {esc(o['title'])} ({fmt(o['amount'])} تومان)")
        return True

    async def _star_alert(self):
        s = await self.settings()
        th = int(D(s.get("star_alert_threshold"), 0))
        if th <= 0 or time.time() - self._star_alert_ts < 3600:
            return
        bal = await self.star_balance()
        if bal is not None and bal < th:
            self._star_alert_ts = time.time()
            await self.notify_admins(f"⚠️ موجودی ستاره ربات کم است: <b>{bal}</b> (آستانه هشدار {th}).", perm="stars", topic="alerts")

    async def _check_provider_balance(self, force=False):
        s = await self.settings()
        if not s.get("prov_balance_url"):
            return
        if not force and time.time() - self._prov_bal_ts < 300:
            return
        self._prov_bal_ts = time.time()
        self.provider_balance = await provider.get_balance(self)
        low = D(s.get("prov_balance_low"), 0)
        if self.provider_balance is not None and low > 0 and self.provider_balance < low and self.warn_once("prov_low", 3):
            await self.notify_admins(f"⚠️ موجودی حساب سرویس‌دهنده‌ی تحویل کم است: {provider.fmt_balance(self.provider_balance)} (آستانه {low}).", perm="pricing", topic="alerts")

    async def retry_queue(self, include_failed=False) -> int:
        """تلاش مجدد سفارش‌های صف. به‌صورت پیش‌فرض فقط سفارش‌های منتظر موجودی ستاره ربات؛
        با include_failed سفارش‌های خطادار هم (به‌جز «نتیجه نامشخص» سرویس‌دهنده که باید دستی بررسی شوند)."""
        n = 0
        s = await self.settings()
        for o in await self.db.queue_orders(50):
            note = o.get("note") or ""
            if note.startswith("UNKNOWN:"):
                continue
            if o["kind"] == "stars" and not provider.provider_enabled(s, "stars"):
                continue
            if o["kind"] == "premium" and provider.provider_enabled(s, "premium") and not include_failed:
                continue
            if o["status"] == "failed" and not include_failed:
                continue
            if o["status"] == "manual_pending" and not include_failed and "موجودی ستاره" not in note:
                continue
            await self.fulfil(o["id"])
            n += 1
        return n

    async def admin_order_action(self, act, oid, actor="admin") -> str:
        o = await self.db.get_order(oid)
        if not o:
            return NW_("سفارش پیدا نشد.")
        await self.db.log(actor, f"order_{act}", f"#{oid}")
        if act == "retry":
            if o["kind"] == "stars" and not provider.provider_enabled(await self.settings(), "stars"):
                return NW_("سفارش استارز دستی است (API تحویل فعال نیست).")
            if o["status"] not in ("manual_pending", "failed", "paid"):
                return NW_("وضعیت سفارش اجازه‌ی تلاش مجدد نمی‌دهد.")
            await self.fulfil(oid)
            return NW_("تلاش مجدد انجام شد.")
        if act == "auto":
            if o["kind"] not in ("gift", "premium"):
                return NW_("ارسال خودکار با ستاره ربات فقط برای گیفت و پریمیوم است.")
            if o["status"] not in ("manual_pending", "failed", "paid"):
                return NW_("وضعیت سفارش اجازه‌ی ارسال نمی‌دهد.")
            await self.fulfil(oid, force_auto=True)
            o2 = await self.db.get_order(oid)
            return NW_("✅ با ستاره ربات ارسال شد.") if o2 and o2["status"] == "done" else NW_("ارسال انجام نشد؛ موجودی ستاره ربات یا خطا را بررسی کنید.")
        if act == "done":
            return NW_("ثبت شد.") if await self.finish_order(oid, "تحویل دستی") else NW_("وضعیت سفارش اجازه‌ی این کار را نمی‌دهد.")
        if act == "refund":
            if await self.db.transition(oid, ["manual_pending", "failed", "paid"], "refunded", "بازگشت وجه توسط ادمین"):
                await self.db.add_balance(o["user_id"], o["amount"], f"refund: order {oid}", ref_key=f"refund:{oid}")
                await self.say(o["user_id"], "↩️ مبلغ سفارش #{oid} ({amount} تومان) به کیف پول شما برگشت.", oid=oid, amount=fmt(o["amount"]))
                return NW_("مبلغ به کیف پول کاربر برگشت.")
            return NW_("وضعیت سفارش اجازه بازگشت وجه نمی‌دهد.")
        return NW_("عملیات نامعتبر.")

    async def admin_payment_action(self, act, pid, actor="admin") -> str:
        p = await self.db.get_payment(pid)
        if not p:
            return NW_("پرداخت پیدا نشد.")
        await self.db.log(actor, f"payment_{act}", f"P{pid}")
        if act == "ok":
            return NW_("✅ تأیید و پردازش شد.") if await self.confirm_payment(pid, ref=f"admin:{pid}") else NW_("این پرداخت قبلاً بررسی شده است.")
        if await self.db.reject_payment(pid):
            await self.say(p["user_id"], "❌ پرداخت شما (کد P{pid}) تأیید نشد. در صورت اشتباه با پشتیبانی تماس بگیرید.", pid=pid)
            return NW_("❌ رد شد.")
        return NW_("این پرداخت قبلاً بررسی شده است.")

    # ---------- عملیات مدیریتی روی کاربر (مشترک بین ربات و وب) ----------
    async def admin_adjust_balance(self, uid, op, amount, reason, actor):
        """op: add | sub | set. خروجی: (ok, پیام فارسی)"""
        u = await self.db.get_user(uid)
        if not u:
            return False, NW_("کاربر پیدا نشد.")
        why = f"admin:{actor}" + (f" {reason}" if reason else "")
        if op == "add":
            await self.db.add_balance(uid, amount, why)
            await self.say(uid, "➕ مبلغ <b>{amount}</b> تومان توسط مدیریت به کیف پول شما اضافه شد.", amount=fmt(amount))
        elif op == "sub":
            if not await self.db.deduct_balance(uid, amount, why):
                return False, NW_("موجودی کاربر کافی نیست.")
            await self.say(uid, "➖ مبلغ <b>{amount}</b> تومان توسط مدیریت از کیف پول شما کسر شد.", amount=fmt(amount))
        elif op == "set":
            delta = await self.db.set_balance_exact(uid, amount, why)
            if delta:
                await self.say(uid, "👛 موجودی کیف پول شما توسط مدیریت به <b>{amount}</b> تومان تغییر کرد.", amount=fmt(amount))
        else:
            return False, NW_("عملیات نامعتبر.")
        await self.db.log(actor, "balance", f"{uid} {op} {amount} {reason}"[:200])
        return True, NW_("موجودی بروزرسانی شد.")

    async def admin_set_ban(self, uid, banned: bool, reason, actor):
        u = await self.db.get_user(uid)
        if not u:
            return False, NW_("کاربر پیدا نشد.")
        if banned and self.is_admin(uid):
            return False, NW_("ادمین را نمی‌توان مسدود کرد.")
        await self.db.set_banned(uid, banned, reason)
        await self.db.log(actor, "ban" if banned else "unban", f"{uid} {reason}"[:200])
        if banned:
            await self.say(uid, "⛔ حساب شما مسدود شد.") if not reason else \
                await self.say(uid, "⛔ حساب شما مسدود شد.\nدلیل: {r}", r=esc(reason))
        else:
            await self.say(uid, "✅ مسدودی حساب شما برداشته شد.")
        return True, NW_("انجام شد.")

    async def admin_set_personal_discount(self, uid, pct, actor):
        if not await self.db.get_user(uid):
            return False, NW_("کاربر پیدا نشد.")
        await self.db.set_personal_discount(uid, pct)
        await self.db.log(actor, "personal_discount", f"{uid}={pct}")
        if pct:
            await self.say(uid, "🎁 یک تخفیف اختصاصی <b>{p}٪</b> برای همه‌ی خریدهای شما فعال شد.", p=pct)
        return True, NW_("ذخیره شد.")

    async def _say_code(self, uid, code, percent, fixed):
        """اعلام کد تخفیف اختصاصی به کاربر (به زبان خودش)."""
        if percent:
            await self.say(uid, "🏷 یک کد تخفیف اختصاصی برای شما فعال شد:\n<code>{code}</code>\nمقدار: {v}٪\nهنگام پرداخت «کد تخفیف دارم» را بزنید.",
                           code=code, v=percent)
        else:
            await self.say(uid, "🏷 یک کد تخفیف اختصاصی برای شما فعال شد:\n<code>{code}</code>\nمقدار: {v} تومان\nهنگام پرداخت «کد تخفیف دارم» را بزنید.",
                           code=code, v=fmt(fixed))

    async def admin_grant_code(self, uid, code, actor):
        d = await self.db.get_discount(code)
        if not d:
            return False, NW_("این کد تخفیف وجود ندارد.")
        if not await self.db.get_user(uid):
            return False, NW_("کاربر پیدا نشد.")
        await self.db.add_discount_user(d["code"], uid)
        await self.db.log(actor, "discount_grant", f"{d['code']} -> {uid}")
        await self._say_code(uid, d["code"], d["percent"], d["fixed"])
        return True, NW_("کد برای کاربر فعال شد.")

    async def admin_personal_code(self, uid, percent, fixed, hours, actor, max_discount=0):
        """ساخت کد یک‌بارمصرف اختصاصی برای یک کاربر و اعلام آن به او."""
        if not await self.db.get_user(uid):
            return False, NW_("کاربر پیدا نشد."), ""
        code = None
        for _ in range(8):
            c = "U" + secrets.token_hex(3).upper()
            exp = int(time.time() + hours * 3600) if hours else 0
            if await self.db.create_discount(c, percent, fixed, 1, exp, per_user=1, max_discount=max_discount, users=[uid],
                                             note=f"personal {uid}"):
                code = c
                break
        if not code:
            return False, NW_("ساخت کد ناموفق بود."), ""
        await self.db.log(actor, "discount_personal", f"{code} -> {uid}")
        await self._say_code(uid, code, percent, fixed)
        return True, NW_("کد ساخته و برای کاربر ارسال شد."), code

    async def audience_ids(self, aud: str) -> list[int]:
        """مخاطبان پیام همگانی: all | یکی از فیلترهای کاربران | l-<کد زبان>. کاربران مسدود هرگز شامل نمی‌شوند."""
        if aud.startswith("l-"):
            code = aud[2:]
            if code == self.default_lang():
                rows = await self.db.all("SELECT id FROM users WHERE banned=0 AND (lang IS NULL OR lang='' OR lang=?)", (code,))
            else:
                rows = await self.db.all("SELECT id FROM users WHERE banned=0 AND lang=?", (code,))
            return [r["id"] for r in rows]
        if aud == "all":
            return await self.db.all_user_ids()
        ids = await self.db.user_ids_where("", aud)
        banned = {r["id"] for r in await self.db.all("SELECT id FROM users WHERE banned=1")}
        return [i for i in ids if i not in banned]

    # ---------- پیام همگانی ----------
    async def start_broadcast(self, sender, ids=None, lang=None) -> bool:
        """sender: تابع async که chat_id می‌گیرد و پیام را می‌فرستد. فقط یک ارسال همزمان مجاز است.
        ids: فهرست مقصدها (پیش‌فرض همه‌ی کاربران غیرمسدود)؛ lang: فقط کاربران یک زبان."""
        if self.broadcast_state["running"]:
            return False
        ids = ids if ids is not None else await self.db.all_user_ids(lang)
        self.broadcast_state = {"running": True, "total": len(ids), "sent": 0, "failed": 0}
        self.spawn(self._broadcast(ids, sender))
        return True

    async def _broadcast(self, ids, sender):
        st = self.broadcast_state
        for cid in ids:
            for attempt in range(2):
                try:
                    await sender(cid)
                    st["sent"] += 1
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except TelegramForbiddenError:
                    st["failed"] += 1
                    break
                except Exception:
                    st["failed"] += 1
                    break
            await asyncio.sleep(0.05)
        st["running"] = False
        await self.notify_admins(f"📢 ارسال همگانی پایان یافت.\nموفق: {st['sent']} | ناموفق: {st['failed']} | کل: {st['total']}", perm="broadcast")

    # ---------- ورود به پنل وب از داخل ربات ----------
    def make_login_link(self, uid):
        now = time.time()
        self.login_tokens = {t: v for t, v in self.login_tokens.items() if v[1] > now}
        token = secrets.token_urlsafe(24)
        self.login_tokens[token] = (uid, now + 300)
        base = self.cfg.public_url
        return f"{base}/auth?token={token}" if base else None

    def consume_login_token(self, token):
        v = self.login_tokens.pop(token, None)
        if v and v[1] > time.time() and self.is_admin(v[0]):
            return v[0]
        return None

    # ---------- زرین‌پال ----------
    async def _zarinpal_request(self, amount_toman, desc, pid):
        try:
            async with self.http.post(
                "https://payment.zarinpal.com/pg/v4/payment/request.json",
                json={"merchant_id": self.gw("zarinpal_merchant"), "amount": amount_toman * 10,
                      "callback_url": f"{self.cfg.public_url}/zarinpal/callback?pid={pid}", "description": desc},
                headers={"accept": "application/json"},
            ) as r:
                j = await r.json()
            data = j.get("data") or {}
            return data.get("authority") if data.get("code") == 100 else None
        except Exception as e:
            log.warning("zarinpal request failed: %s", e)
            return None

    async def zarinpal_verify(self, amount_toman, authority) -> bool:
        try:
            async with self.http.post(
                "https://payment.zarinpal.com/pg/v4/payment/verify.json",
                json={"merchant_id": self.gw("zarinpal_merchant"), "amount": amount_toman * 10, "authority": authority},
                headers={"accept": "application/json"},
            ) as r:
                j = await r.json()
            return (j.get("data") or {}).get("code") in (100, 101)
        except Exception as e:
            log.warning("zarinpal verify failed: %s", e)
            return False

    async def zarinpal_callback(self, request: web.Request) -> web.Response:
        try:
            pid = int(request.query.get("pid", "0"))
        except ValueError:
            pid = 0
        authority = request.query.get("Authority", "")
        ok = False
        p = await self.db.get_payment(pid) if pid else None
        if p and p["method"] == "zarinpal" and p["ref"] == authority and p["status"] == "pending" \
                and request.query.get("Status") == "OK":
            if await self.zarinpal_verify(p["amount"], authority):
                ok = await self.confirm_payment(pid)
        tr = await self.tr_for(p["user_id"]) if p else self.tr_lang(self.default_lang())
        lg = next((l for l in self.langs if l["code"] == tr.lang), None)
        rtl = bool(lg and lg["rtl"])
        msg = tr("پرداخت با موفقیت انجام شد ✅") if ok else tr("پرداخت ناموفق بود یا لغو شد ❌")
        link = f"https://t.me/{self.bot_username}"
        page = (f"<html dir='{'rtl' if rtl else 'ltr'}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                f"<body style='font-family:sans-serif;text-align:center;padding:40px'><h2>{msg}</h2>"
                f"<p><a href='{esc(link)}'>{esc(tr('بازگشت به ربات'))}</a></p></body></html>")
        return web.Response(text=page, content_type="text/html")

    # ---------- پایش پرداخت‌های کریپتو ----------
    async def check_crypto(self):
        await self._check_ton()
        await self._check_usdt()

    async def _check_ton(self):
        if not self.gw("ton_wallet"):
            return
        pend = await self.db.pending_payments("ton")
        if not pend:
            return
        try:
            key = self.gw("toncenter_key")
            headers = {"X-API-Key": key} if key else {}
            async with self.http.get("https://toncenter.com/api/v2/getTransactions",
                                     params={"address": self.gw("ton_wallet"), "limit": 50}, headers=headers) as r:
                j = await r.json()
            for tx in j.get("result", []):
                msg = tx.get("in_msg") or {}
                comment = (msg.get("message") or "").strip()
                value = int(msg.get("value") or 0)
                txhash = (tx.get("transaction_id") or {}).get("hash")
                if not comment or not txhash:
                    continue
                for p in pend:
                    if comment == f"P{p['id']}" and value >= int(p["crypto_amount"] * 1e9 * 0.99):
                        await self.confirm_payment(p["id"], ref=txhash)
        except Exception as e:
            log.warning("ton check failed: %s", e)

    async def _check_usdt(self):
        if not self.gw("usdt_wallet"):
            return
        pend = await self.db.pending_payments("usdt")
        if not pend:
            return
        try:
            key = self.gw("trongrid_key")
            headers = {"TRON-PRO-API-KEY": key} if key else {}
            async with self.http.get(
                f"https://api.trongrid.io/v1/accounts/{self.gw('usdt_wallet')}/transactions/trc20",
                params={"only_to": "true", "limit": 50, "contract_address": USDT_TRC20_CONTRACT}, headers=headers) as r:
                j = await r.json()
            for t in j.get("data", []):
                value = int(t["value"]) / (10 ** int(t["token_info"]["decimals"]))
                ts = t.get("block_timestamp", 0) / 1000
                for p in pend:
                    if abs(p["crypto_amount"] - value) < 0.0004 and ts >= p["created_at"] - 60:
                        await self.confirm_payment(p["id"], ref=t["transaction_id"])
        except Exception as e:
            log.warning("usdt check failed: %s", e)

    async def expire_old(self):
        s = await self.settings()
        minutes = max(int(D(s.get("pay_timeout_min"), 30)), 5)
        now = int(time.time())
        await self.db.expire_payments(now - minutes * 60)
        await self.db.expire_orders(now - 86400)

    async def background_loop(self):
        while True:
            for step in (self.check_crypto, self.expire_old, self.pricing.tick, self.release_rewards, self._check_provider_balance, self.group.tick):
                try:
                    await step()
                except Exception:
                    log.exception("background step %s failed", getattr(step, "__name__", step))
            await asyncio.sleep(30)
