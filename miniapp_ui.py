"""متن‌ها و ایموجی‌های قابل‌ویرایش مینی‌اپ. ذخیره: تنظیم JSON به نام miniapp_ui = {کلید: {"t": متن, "e": آیدی ایموجی پریمیوم}}.
متن خالی = متن پیش‌فرض (که با زبان کاربر ترجمه می‌شود)؛ متن واردشده برای همه‌ی زبان‌ها یکسان نمایش داده می‌شود."""
import json

from i18n import N_

# (کلید، متن پیش‌فرض فارسی، ایموجی پیش‌فرض، چندخطی؟، گروه)
ELEMENTS = [
    ("greet", N_("سلام"), "👋", False, N_("صفحه‌ی خانه")),
    ("welcome", N_("به فروشگاه استارز و گیفت تلگرام خوش آمدید 🌟\nیکی از گزینه‌های زیر را انتخاب کنید."), "", True, N_("صفحه‌ی خانه")),
    ("wallet_title", N_("موجودی کیف پول"), "👛", False, N_("صفحه‌ی خانه")),
    ("btn_buy", N_("خرید"), "🛍", False, N_("صفحه‌ی خانه")),
    ("btn_topup", N_("افزایش موجودی"), "➕", False, N_("صفحه‌ی خانه")),
    ("tile_done", N_("خریدها"), "🛒", False, N_("کادرهای رنگی")),
    ("tile_orders", N_("سفارش‌های من"), "🧾", False, N_("کادرهای رنگی")),
    ("tile_spent", N_("مجموع خرید"), "📈", False, N_("کادرهای رنگی")),
    ("tile_invited", N_("دعوت‌شده‌ها"), "🤝", False, N_("کادرهای رنگی")),
    ("profile_title", N_("مشخصات شما"), "🪪", False, N_("مشخصات")),
    ("profile_id", N_("شناسه"), "🆔", False, N_("مشخصات")),
    ("profile_user", N_("نام کاربری"), "👤", False, N_("مشخصات")),
    ("kind_stars", N_("خرید استارز"), "⭐", False, N_("محصولات")),
    ("kind_premium", N_("اشتراک پریمیوم"), "👑", False, N_("محصولات")),
    ("kind_gift", N_("گیفت تلگرام"), "🎁", False, N_("محصولات")),
    ("kind_vpn", N_("خرید VPN"), "🛡", False, N_("محصولات")),
    ("vpn_my", N_("سرویس‌های VPN من"), "📡", False, N_("محصولات")),
    ("tab_home", N_("خانه"), "🏠", False, N_("منوی پایین")),
    ("tab_products", N_("محصولات"), "🛍", False, N_("منوی پایین")),
    ("tab_wallet", N_("کیف پول"), "👛", False, N_("منوی پایین")),
    ("tab_invite", N_("دعوت"), "🎁", False, N_("منوی پایین")),
    ("btn_more", N_("بیشتر"), "☰", False, N_("منوی پایین")),
]
KEYS = {e[0] for e in ELEMENTS}
MAX_TEXT = 60
MAX_AREA = 600


def load(settings: dict) -> dict:
    try:
        data = json.loads((settings or {}).get("miniapp_ui") or "{}")
    except ValueError:
        return {}
    out = {}
    for k, v in (data.items() if isinstance(data, dict) else []):
        if k in KEYS and isinstance(v, dict):
            t = str(v.get("t") or "").strip()
            e = str(v.get("e") or "")
            out[k] = {"t": t, "e": e if e.isdigit() and len(e) < 30 else ""}
    return out


def clean(form) -> str:
    """از فرم وب (فیلدهای t_<key> و e_<key>) JSON قابل‌ذخیره می‌سازد؛ مقدارهای خالی حذف می‌شوند."""
    out = {}
    for key, _d, _i, area, _g in ELEMENTS:
        t = str(form.get("t_" + key, "")).strip()[: MAX_AREA if area else MAX_TEXT]
        e = str(form.get("e_" + key, "")).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not e.isdigit() or len(e) >= 30:
            e = ""
        if t or e:
            out[key] = {"t": t, "e": e}
    return json.dumps(out, ensure_ascii=False)


def emoji_ids(settings: dict) -> set:
    return {v["e"] for v in load(settings).values() if v["e"]}
