"""اجزای مشترک پنل مدیریت داخل ربات: فیلتر ادمین، کنترل دسترسی، حالت‌های FSM و ابزارها."""
from aiogram import BaseMiddleware, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

from service import Shop
from settings_schema import GROUP_IDS
from ui import btn, ikb, rtl_fix

TXT = F.text & ~F.text.startswith("/")
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
C2S = {"d": "", "p": "primary", "s": "success", "x": "danger"}


class IsAdmin(BaseFilter):
    async def __call__(self, event, shop: Shop) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and shop.is_admin(u.id))


class Adm(StatesGroup):
    user_q = State()
    reject_reason = State()
    bal = State()
    ban_reason = State()
    msg_user = State()
    user_note = State()
    user_pdisc = State()
    user_code = State()
    user_pcode = State()
    price = State()
    margin = State()
    title = State()
    new_pack = State()
    set_value = State()
    btn_label = State()
    btn_emoji = State()
    role_emoji = State()
    bk_emoji = State()
    bk_text = State()
    badd_label = State()
    badd_extra = State()
    disc_wiz = State()
    disc_extend = State()
    bcast = State()
    admin_id = State()
    admin_name = State()
    admin_web = State()
    topup = State()
    restore = State()
    lang_new = State()
    lang_text = State()
    fj_val = State()


# پیشوند callback ← مجوز لازم (بلندترین پیشوند منطبق برنده است؛ None = هر ادمین)
PERM_MAP = [
    ("adm:home", None), ("adm:noop", None), ("adm:web", None), ("menu", None),
    ("adm:stats", "stats"),
    ("adm:queue", "orders"), ("adm:retryall", "orders"), ("adm:ord:", "orders"), ("oadm:", "orders"),
    ("adm:pays", "receipts"), ("padm:", "receipts"),
    ("adm:users", "users_view"), ("adm:ul:", "users_view"), ("adm:u:", "users_view"), ("adm:uo:", "users_view"),
    ("adm:up:", "users_view"), ("adm:ur:", "users_view"), ("adm:uw:", "users_view"),
    ("adm:ub:", "users_edit"), ("adm:ubn:", "users_edit"), ("adm:um:", "users_edit"), ("adm:unb:", "users_edit"),
    ("adm:upd:", "users_edit"), ("adm:ucd:", "users_edit"), ("adm:upc:", "users_edit"), ("adm:unote:", "users_edit"),
    ("adm:ulg:", "users_edit"), ("adm:ulang:", "users_edit"), ("adm:urf:", "users_edit"),
    ("adm:prod", "products"), ("adm:pk:", "products"), ("adm:gsync", "products"), ("adm:p:", "products"),
    ("adm:pt:", "products"), ("adm:pc:", "products"), ("adm:pp:", "products"), ("adm:ptt:", "products"),
    ("adm:pnew", "products"), ("adm:pm:", "products"), ("adm:pmc:", "products"),
    ("adm:rates", "pricing"), ("adm:ratesnow", "pricing"),
    ("adm:btn", "buttons"), ("adm:ba:", "buttons"), ("adm:bk", "buttons"), ("adm:bt:", "buttons"), ("adm:bcol:", "buttons"), ("adm:bmv:", "buttons"), ("adm:bena:", "buttons"),
    ("adm:bdel:", "buttons"), ("adm:bren:", "buttons"), ("adm:bext:", "buttons"), ("adm:badd", "buttons"), ("adm:breset", "buttons"),
    ("adm:roles", "buttons"), ("adm:ro:", "buttons"), ("adm:rc:", "buttons"), ("adm:bem:", "buttons"), ("adm:rem:", "buttons"),
    ("adm:fj", "settings"),
    ("adm:disc", "discounts"), ("adm:dc:", "discounts"), ("adm:dt:", "discounts"), ("adm:dx:", "discounts"),
    ("adm:dnew", "discounts"), ("adm:dnewadv", "discounts"), ("adm:ds:", "discounts"), ("adm:dw:", "discounts"), ("adm:dext:", "discounts"), ("adm:dlog:", "discounts"),
    ("adm:bcast", "broadcast"), ("adm:bgo", "broadcast"),
    ("adm:admins", "admins"), ("adm:aadd", "admins"), ("adm:arm:", "admins"), ("adm:ap:", "admins"), ("adm:apre:", "admins"),
    ("adm:an:", "admins"), ("adm:aw:", "admins"), ("adm:ad:", "admins"),
    ("adm:stars", "stars"), ("adm:stopup", "stars"), ("adm:grp:", "settings"),
    ("adm:backup", "backup"), ("adm:restore", "backup"), ("adm:vpn", "vpn"),
    ("adm:langs", "languages"), ("adm:lg:", "languages"), ("adm:lnew", "languages"), ("adm:lt:", "languages"),
]
_PERM_SORTED = sorted(PERM_MAP, key=lambda x: -len(x[0]))


def perm_for_callback(data: str):
    """مجوز لازم برای یک callback؛ ('?' = فقط مدیر کامل)"""
    if data.startswith(("adm:set", "adm:sg:", "adm:sf:", "adm:sv:")):
        parts = data.split(":")
        if data.startswith("adm:sg:") and len(parts) > 2 and parts[2] in GROUP_IDS:
            return GROUP_IDS[parts[2]][2]
        if data.startswith(("adm:sf:", "adm:sv:")) and len(parts) > 2:
            from settings_schema import GROUP_OF
            gid = GROUP_OF.get(parts[2])
            return GROUP_IDS[gid][2] if gid else "settings"
        return "settings"
    for prefix, perm in _PERM_SORTED:
        if data.startswith(prefix):
            return perm
    return "?"


class PermGuard(BaseMiddleware):
    """هر callback مدیریتی فقط برای ادمینی اجرا می‌شود که مجوز آن بخش را دارد (سایر callbackها دست‌نخورده رد می‌شوند)."""

    async def __call__(self, handler, event, data):
        shop: Shop = data["shop"]
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith(("adm:", "oadm:", "padm:")) \
                and shop.is_admin(event.from_user.id):
            uid = event.from_user.id
            perm = perm_for_callback(event.data)
            if perm is None:
                ok = True
            elif perm == "?":
                ok = shop.admin_perms.get(uid) == "*"
            else:
                ok = shop.can(uid, perm)
            if not ok:
                await event.answer("⛔ شما به این بخش دسترسی ندارید.", show_alert=True)
                return
        return await handler(event, data)


def num(text) -> int | None:
    t = (text or "").translate(DIGITS).replace(",", "").replace("٬", "").strip()
    return int(t) if t.isdigit() else None


async def view(t, text, kb=None):
    """نمایش صفحه: برای callback پیام را ویرایش می‌کند، وگرنه پیام جدید می‌فرستد."""
    text = rtl_fix(text)
    if isinstance(t, CallbackQuery):
        try:
            await t.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                await t.message.answer(text, reply_markup=kb)
    else:
        await t.answer(text, reply_markup=kb)


def back_row(to="adm:home", label="🔙 بازگشت"):
    return [btn(label, to, role="back")]


def with_data(cb: CallbackQuery, data: str) -> CallbackQuery:
    return cb.model_copy(update={"data": data})


def make_router():
    from aiogram import Router
    r = Router()
    r.message.filter(F.chat.type == "private", IsAdmin())
    r.callback_query.filter(IsAdmin())
    r.callback_query.outer_middleware(PermGuard())
    return r


def can_see(shop: Shop, uid: int, perm):
    return shop.can(uid, perm)


def short(txt: str, n: int) -> str:
    """کوتاه‌کردن متن در مرز کلمه (نه وسط کلمه) تا دکمه‌ی تلگرام بریده‌ی بی‌معنی نشان ندهد."""
    txt = " ".join(str(txt).split())
    if len(txt) <= n:
        return txt
    cut = txt[:n].rsplit(" ", 1)[0] or txt[:n]
    return cut.rstrip(" :،-—") + "…"


async def ans(target, card):
    """ارسال (متن, کیبورد) به‌صورت پیام جدید. توجه: در aiogram 3.31 آرگومان دوم answer() کیبورد نیست."""
    text, kb = card
    return await target.answer(rtl_fix(text), reply_markup=kb)
