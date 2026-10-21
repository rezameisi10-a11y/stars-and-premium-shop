"""عناصر مشترک رابط کاربری: دکمه‌های رنگی تلگرام (Bot API 9.4+) و قالب‌بندی."""
import html
import json
import logging
import re

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from i18n import NW_

log = logging.getLogger("ui")

# مقادیر مجاز فیلد style در دکمه‌های inline تلگرام. خالی = رنگ پیش‌فرض (خاکستری)
VALID_STYLES = ("", "primary", "success", "danger")
STYLE_LABELS = {"": NW_("⚪ پیش‌فرض"), "primary": NW_("🔵 آبی"), "success": NW_("🟢 سبز"), "danger": NW_("🔴 قرمز")}
STYLE_DOTS = {"": "⚪", "primary": "🔵", "success": "🟢", "danger": "🔴"}

# نقش‌های دکمه‌های ثابت ربات که رنگشان از پنل قابل تنظیم است
ROLE_LABELS = {
    "product": NW_("دکمه‌های لیست محصولات"),
    "nav": NW_("دکمه‌های صفحه‌بندی (قبلی/بعدی)"),
    "back": NW_("دکمه‌های بازگشت"),
    "pay": NW_("دکمه‌های روش پرداخت"),
    "confirm": NW_("دکمه‌های تأیید (ادمین)"),
    "reject": NW_("دکمه‌های رد / لغو (ادمین)"),
    "action": NW_("دکمه‌های عملیاتی (شارژ، بررسی پرداخت، تلاش مجدد ...)"),
    "admin": NW_("دکمه‌های منوی پنل مدیریت"),
}
ROLE_DEFAULTS = {"product": "", "nav": "", "back": "", "pay": "primary", "confirm": "success",
                 "reject": "danger", "action": "success", "admin": ""}

STYLES = dict(ROLE_DEFAULTS)
OVR: dict[str, dict] = {}    # شخصی‌سازی تک‌تک دکمه‌ها: {کلید: {"s": style|"d", "e": emoji_id, "t": text}}
EMOJIS = {r: "" for r in ROLE_DEFAULTS}   # شناسه‌ی ایموجی پریمیوم (custom emoji) هر گروه دکمه
PREMIUM_ON = True


_STATIC_SEG = re.compile(r"^[a-z_]+$")


def bkey(data: str) -> str:
    """کلید پایدار یک دکمه از callback_data: بخش‌های ثابت (حروف کوچک/زیرخط) تا اولین بخش متغیر (عدد، کد، ...)."""
    keep = []
    for part in str(data or "").split(":"):
        if _STATIC_SEG.match(part):
            keep.append(part)
        else:
            break
        if len(keep) >= 4:
            break
    return ":".join(keep)


def ovr_lookup(key: str):
    """شخصی‌سازی دکمه: اول کلید دقیق، بعد پیشوندهای کوتاه‌تر (حداقل دو بخش) برای دکمه‌های پویا مثل adm:sg:<گروه>."""
    if not OVR or not key:
        return None
    if key in OVR:
        return OVR[key]
    parts = key.split(":")
    for n in range(len(parts) - 1, 1, -1):
        v = OVR.get(":".join(parts[:n]))
        if v:
            return v
    return None


def load_overrides(raw) -> dict:
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                st = v.get("s") if v.get("s") in ("d", "primary", "success", "danger") else None
                em = str(v.get("e") or "")
                em = em if em.isdigit() else ""
                tx = str(v.get("t") or "")[:60]
                if st or em or tx:
                    out[str(k)] = {"s": st, "e": em, "t": tx}
    return out


def set_styles(settings: dict) -> None:
    """رنگ و ایموجی نقش‌ها را از تنظیمات (style_<role>, emoji_<role>) در حافظه بارگذاری می‌کند."""
    global PREMIUM_ON
    OVR.clear()
    OVR.update(load_overrides(settings.get("btn_ovr", "")))
    for role, default in ROLE_DEFAULTS.items():
        v = settings.get("style_" + role)
        STYLES[role] = v if v in VALID_STYLES else default
        e = (settings.get("emoji_" + role) or "").strip()
        EMOJIS[role] = e if e.isdigit() else ""
    PREMIUM_ON = settings.get("premium_emoji_enabled", "1") == "1"


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def btn(text, data=None, url=None, role=None, style=None, emoji=None):
    """ساخت دکمه inline. style/emoji صریح اولویت دارند؛ وگرنه از رنگ/ایموجی نقش (role) خوانده می‌شود.
    emoji = شناسه‌ی عددی ایموجی پریمیوم که قبل از متن دکمه نمایش داده می‌شود (icon_custom_emoji_id، Bot API 9.4)."""
    ov = ovr_lookup(bkey(data)) if (OVR and data) else None
    st = style if style is not None else (STYLES.get(role, "") if role else "")
    em = emoji if emoji is not None else (EMOJIS.get(role, "") if role else "")
    if ov:                      # شخصی‌سازی اختصاصی همین دکمه از همه‌ی موارد بالاتر است
        if ov["s"]:
            st = "" if ov["s"] == "d" else ov["s"]
        if ov["e"]:
            em = ov["e"]
        if ov["t"] and not url:
            text = ov["t"]
    kw = {"style": st} if st in ("primary", "success", "danger") else {}
    if em and PREMIUM_ON and str(em).isdigit():
        kw["icon_custom_emoji_id"] = str(em)
    if url:
        return InlineKeyboardButton(text=text, url=url, **kw)
    return InlineKeyboardButton(text=text, callback_data=data, **kw)


def ikb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def strip_emoji(markup):
    """کپی از کیبورد بدون ایموجی‌های پریمیوم (برای حالتی که تلگرام آن‌ها را نپذیرد)."""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    rows = [[b.model_copy(update={"icon_custom_emoji_id": None}) for b in row] for row in markup.inline_keyboard]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def has_emoji(markup) -> bool:
    return isinstance(markup, InlineKeyboardMarkup) and any(getattr(b, "icon_custom_emoji_id", None) for r in markup.inline_keyboard for b in r)


async def emoji_fallback_middleware(make_request, bot, method):
    """اگر تلگرام ایموجی پریمیوم دکمه‌ها را نپذیرد (مالک ربات پریمیوم نیست)، ایموجی‌ها حذف و یک بار دوباره ارسال می‌شود
    و قابلیت تا اصلاح تنظیمات در حافظه خاموش می‌شود تا ربات از کار نیفتد."""
    global PREMIUM_ON
    try:
        return await make_request(bot, method)
    except TelegramBadRequest as e:
        mk = getattr(method, "reply_markup", None)
        if has_emoji(mk) and ("emoji" in str(e).lower() or "icon" in str(e).lower() or "BUTTON" in str(e)):
            log.warning("premium emoji rejected by Telegram, disabling: %s", e)
            PREMIUM_ON = False
            return await make_request(bot, method.model_copy(update={"reply_markup": strip_emoji(mk)}))
        raise
