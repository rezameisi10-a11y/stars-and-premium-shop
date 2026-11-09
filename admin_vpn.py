"""مدیریت فروش VPN داخل ربات (برای ادمین‌ها با مجوز «vpn»): پنل‌ها، پلن‌ها، بسته‌های افزودنی، سرویس‌ها و ساخت/تحویل دستی."""
import json
import re
import time

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import vpn_panels
from admin_common import TXT, back_row, make_router, view
from service import Shop
from ui import btn, esc, fmt, ikb, rtl_fix
from vpn_service import PANEL_FLAG_TITLES, PANEL_FLAGS, STATUS_FA, bar, days_left, fmt_gb, hidden_for, parse_stock_text, pextra, pflag, plimit

router = make_router()
PER = 8
URL_RE = re.compile(r"(?<![>\w/])(https?://[^\s<]+)")


async def ans(msg, text, reply_markup=None):
    """ارسال پیام با راست‌به‌چپ درست؛ آدرس‌های لاتین داخل <code> می‌روند تا با متن فارسی قاطی نشوند."""
    await msg.answer(rtl_fix(URL_RE.sub(r"<code>\1</code>", text)), reply_markup=reply_markup)


def onoff(v) -> str:
    return "✅ روشن" if v else "⛔ خاموش"

TYPE_LABEL = dict(vpn_panels.TYPES)

def e(x: str) -> str:
    """نوشته‌ی انگلیسی/عدد را داخل متن فارسی ثابت نگه می‌دارد (جداکننده‌ی چپ‌به‌راست)."""
    return "\u2066" + x + "\u2069"


FIELD_PROMPT = {
    "name": "یک اسم دلخواه برای این پنل بنویسید. کاربران همین اسم را می‌بینند؛ مثلاً «آلمان» یا «مستقیم».",
    "url": f"آدرس پنل را بفرستید. باید با {e('http://')} یا {e('https://')} شروع شود و اگر پورت دارد آن را هم بنویسید.\nمثال: {e('https://panel.example.com:8000')}",
    "username": f"نام کاربری ادمین پنل را بفرستید. بهتر است ادمین اصلی ({e('sudo')}) باشد.",
    "password": "رمز همان ادمین را بفرستید. بعد از ذخیره، پیام شما پاک می‌شود.",
    "token": f"کلید {e('API')} پنل را بفرستید.",
    "sub_url": "آدرس پایه‌ی لینک اشتراک را بفرستید. اگر نمی‌دانید فقط «-» بفرستید تا همان آدرس خود پنل استفاده شود.",
    "inbound_ids": ("کاربران روی کدام اینباند/گروه ساخته شوند؟\n\n"
                    f"راه اول: شناسه (عدد) را بفرستید. اگر چند تاست با کاما جدا کنید؛ مثال: {e('1,2')}\n"
                    "راه دوم (ساده‌تر): داخل خود پنل یک کانفیگ بسازید، اینباندهای دلخواه را به آن وصل کنید و فقط «اسم همان کانفیگ» را اینجا بفرستید. ربات بقیه‌ی کانفیگ‌ها را شبیه همان می‌سازد.\n"
                    "برای «همه‌ی اینباندها» فقط «-» بفرستید."),
    "capacity": "حداکثر چند سرویس روی این پنل ساخته شود؟ برای بدون محدودیت عدد ۰ بفرستید.",
    "test_days": "اکانت تست این پنل چند روز اعتبار داشته باشد؟ (برای مقدار پیش‌فرض «-» بفرستید)",
    "test_gb": "اکانت تست این پنل چند گیگابایت حجم داشته باشد؟ (۰ یعنی نامحدود؛ برای مقدار پیش‌فرض «-» بفرستید)",
    "revoke_max": "هر سرویس حداکثر چند بار بتواند «لینک جدید» بگیرد؟ (۰ = بدون محدودیت)",
    "revoke_gap": "بین دو بار «لینک جدید» حداقل چند دقیقه فاصله باشد؟ (۰ = پیش‌فرض ۲ دقیقه)",
    "toggle_max": "هر سرویس حداکثر چند بار بتواند خودش اکانت را خاموش/روشن کند؟ (۰ = بدون محدودیت)",
    "toggle_gap": "بین دو بار خاموش/روشن کردن حداقل چند دقیقه فاصله باشد؟ (۰ = تنظیم عمومی)",
    "addon_max": "هر سرویس حداکثر چند بار بتواند حجم/زمان اضافه بخرد؟ (۰ = بدون محدودیت)",
    "move_max": "هر سرویس حداکثر چند بار بتواند لوکیشن عوض کند؟ (۰ = بدون محدودیت)",
    "test_max": "هر کاربر روی این پنل حداکثر چند اکانت تست بتواند بگیرد؟ (یک عدد، حداقل ۱؛ برای مقدار پیش‌فرض «-» بفرستید)",
}
LIMIT_KEYS = ("revoke_max", "revoke_gap", "toggle_max", "toggle_gap", "addon_max", "move_max")
OPTIONAL = {"sub_url", "inbound_ids", "test_days", "test_gb", "test_max", *LIMIT_KEYS}


class AV(StatesGroup):
    wiz = State()
    pb_emoji = State()


def _ask_kb(cancel="adm:vpn"):
    return ikb([[btn("❌ انصراف", cancel, role="back")]])


# ============================================================ خانه
# قابلیت‌های کلی (جدول «عنوان | وضعیت | ⚙️» مثل میرزا)
# (کلید، عنوان، نوع: bool=کلید روشن/خاموش | val=مقدار عددی/متنی، تنظیمات مرتبط با ⚙️)
FEATURES = [("vpn_enabled", "فروش VPN", "bool", ["vpn_name_mode", "vpn_prefix", "vpn_sync_min"]),
            ("vpn_allow_addon", "خرید حجم/زمان اضافه", "bool", []),
            ("vpn_allow_revoke", "ساخت لینک جدید توسط کاربر", "bool", []),
            ("vpn_stack", "جمع شدن روزهای باقی‌مانده در تمدید", "bool", []),
            ("vpn_notify_admin", "اعلان فروش به ادمین", "bool", []),
            ("vpn_agent_enabled", "فروش پنل نمایندگی", "bool", ["vpn_agent_name_mode", "vpn_agent_prefix", "vpn_agent_remind_days", "vpn_agent_autodelete_days", "vpn_agent_help_text"]),
            ("vpn_remind_days", "هشدار زمان", "val", ["vpn_remind_days"]),
            ("vpn_remind_usage", "هشدار حجم", "val", ["vpn_remind_usage"]),
            ("vpn_autodelete_days", "حذف خودکار سرویس منقضی", "val", ["vpn_autodelete_days"]),
            ("vpn_help_text", "راهنمای اتصال", "val", ["vpn_help_text"])]
FEATURE_BY_KEY = {f[0]: f for f in FEATURES}
VPN_KEYS = [k for k, *_ in __import__("settings_schema").GROUP_IDS["vpn"][3]]
# حجم/زمان اضافه‌ی دلخواه: دو بخش جدا (کاربران خرید VPN | پنل نمایندگی) با صفحه‌ی مخصوص خودشان
ADDON_SECS = {"u": ("👤 کاربران (خرید VPN)", "vpn_user")}
ADDON_KEYS = {f"{pre}_{g}_{f}" for _, pre in ADDON_SECS.values() for g in ("cgb", "cd") for f in ("on", "price", "min", "max")}
VPN_FLAT_KEYS = [k for k in VPN_KEYS if k not in ADDON_KEYS]
# زیرمنوهای تنظیمات VPN (کلید حجم/زمان اضافه‌ی کل داخل صفحه‌ی «حجم و زمان اضافه» است)
VPN_SECTIONS = [
    ("g", "🧩 عمومی", ["vpn_enabled", "vpn_name_mode", "vpn_prefix", "vpn_stack", "vpn_allow_revoke", "vpn_notify_admin", "vpn_sync_min", "vpn_autodelete_days", "vpn_help_text"]),
    ("r", "🔔 یادآوری و تمدید", ["vpn_remind_days", "vpn_remind_usage", "vpn_autorenew_on", "vpn_autorenew_days"]),
    ("w", "🎁 بازگشت مشتری", ["vpn_winback_on", "vpn_winback_percent", "vpn_winback_after", "vpn_winback_valid"]),
    ("h", "🩺 پایش سلامت پنل‌ها", ["vpn_health_on", "vpn_health_min", "vpn_health_fails", "vpn_health_autoclose", "vpn_health_cpu", "vpn_health_mem", "vpn_health_disk", "vpn_health_cap"]),
    ("a", "🧑‍💼 پنل نمایندگی", ["vpn_agent_enabled", "vpn_agent_cv_on", "vpn_agent_cv_price", "vpn_agent_cv_min", "vpn_agent_cv_max", "vpn_agent_cv_days", "vpn_agent_cv_users", "vpn_agent_cv_cost", "vpn_agent_cv_add_on", "vpn_agent_cv_add_price", "vpn_agent_cv_add_min", "vpn_agent_cv_add_max", "vpn_agent_name_mode", "vpn_agent_prefix", "vpn_agent_remind_days", "vpn_agent_autodelete_days", "vpn_agent_help_text"]),
]
_SEC_BY_ID = {x[0]: x for x in VPN_SECTIONS}
_placed = {k for _, _, ks in VPN_SECTIONS for k in ks} | {"vpn_allow_addon"}
_SEC_BY_ID["g"][2].extend(k for k in VPN_FLAT_KEYS if k not in _placed)   # هر تنظیم جدیدِ جاماندن به «عمومی» می‌رود


async def hub(shop: Shop):
    st = await shop.db.vpn_stats()
    pend = await shop.db.count_vpn_services(status="pending_manual")
    panels = await shop.db.list_vpn_panels()
    text = (f"🛡 <b>فروش VPN</b> — {onoff(shop.vpn.enabled())}\n\n"
            f"🖧 پنل‌ها: {len(panels)}\n📡 سرویس‌ها: {st['total']} (فعال {st['active']}، منقضی {st['expired']}، تست {st['tests']})\n"
            f"🧑‍💼 نماینده‌ها: {await shop.db.count_agents()}\n"
            f"💰 فروش: {fmt(st['revenue'])} تومان | سود: {fmt(st['profit'])} تومان ({st['orders']} سفارش)"
            + (f"\n\n🕓 منتظر تحویل دستی: <b>{pend}</b>" if pend else ""))
    rows = [[btn("🖧 پنل‌ها", "adm:vpn:pl", role="admin"), btn("📦 محصولات (پلن‌ها)", "adm:vpn:pn", role="admin")],
            [btn("📡 سرویس‌ها", "adm:vpn:sv:all:0", role="admin"), btn("👤 مدیریت کاربران", "adm:vpn:us:0", role="admin")],
            [btn("🔎 جستجو", "adm:vpn:find", role="admin"), btn("🆕 ساخت سرویس دستی", "adm:vpn:new", role="admin")],
            [btn("🧑‍💼 پلن‌های نمایندگی", "adm:vpn:agp", role="admin"), btn("🗂 نماینده‌ها", "adm:vpn:agl:all:0", role="admin")],
            [btn("📊 وضعیت قابلیت‌ها", "adm:vpn:ft", role="admin"), btn("🩺 سلامت پنل‌ها", "adm:vpn:hl", role="admin")],
            [btn("🧰 عملیات گروهی", "adm:vpn:bk", role="admin"), btn("⚙️ تنظیمات", "adm:vpn:st", role="admin")]]
    if pend:
        rows.insert(0, [btn(f"🕓 منتظر تحویل ({pend})", "adm:vpn:sv:pending_manual:0", role="action")])
    rows.append(back_row())
    return text, ikb(rows)


@router.callback_query(F.data == "adm:vpn")
async def cb_hub(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    text, kb = await hub(shop)
    await view(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


# ---- جدول وضعیت قابلیت‌ها
def _is_on(shop: Shop, key, kind) -> bool:
    v = shop.s.get(key, "0")
    return v == "1" if kind == "bool" else v not in ("", "0")


async def _features(cb: CallbackQuery, shop: Shop):
    rows = [[btn("عنوان", "adm:vpn:noop", role="back"), btn("وضعیت", "adm:vpn:noop", role="back"), btn("تنظیمات", "adm:vpn:noop", role="back")]]
    for key, title, kind, rel in FEATURES:
        st = btn(onoff(_is_on(shop, key, kind)), f"adm:vpn:ftg:{key}" if kind == "bool" else f"adm:vpn:se:{key}:f", role="action")
        rows.append([btn(title, "adm:vpn:noop", role="back"), st, btn("⚙️", f"adm:vpn:fs:{key}", role="admin")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "📊 <b>وضعیت قابلیت‌ها</b>\nبرای روشن یا خاموش کردن، روی وضعیت بزنید. برای تنظیمات هر مورد، روی ⚙️ بزنید.", ikb(rows))


@router.callback_query(F.data == "adm:vpn:ft")
async def cb_features(cb: CallbackQuery, shop: Shop):
    await _features(cb, shop)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:ftg:"))
async def cb_feature_toggle(cb: CallbackQuery, shop: Shop):
    key = cb.data.split(":")[3]
    if key not in FEATURE_BY_KEY or FEATURE_BY_KEY[key][2] != "bool":
        await cb.answer("نامعتبر", show_alert=True)
        return
    await shop.set_setting(key, "0" if shop.s.get(key, "0") == "1" else "1", f"tg:{cb.from_user.id}")
    await _features(cb, shop)
    await cb.answer("انجام شد")


# ---- تنظیمات مرتبط با یک قابلیت (فقط همان‌ها، نه همه‌ی تنظیمات)
def _val_text(shop: Shop, key) -> str:
    from settings_schema import FIELDS
    typ = FIELDS[key][1]
    v = shop.s.get(key, "")
    if typ == "bool":
        return onoff(v == "1")
    if typ == "textarea":
        return "✅ ثبت شده" if v else "—"
    return v if v != "" else "—"


async def _settings_list(cb: CallbackQuery, shop: Shop, keys, origin, title, back, top=None):
    from settings_schema import FIELDS
    rows = list(top or []) + [[btn(f"{FIELDS[k][0]}: {_val_text(shop, k)}"[:64], f"adm:vpn:se:{k}:{origin}", role="admin")] for k in keys]
    rows.append(back_row(back))
    await view(cb, title, ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:fs:"))
async def cb_feature_settings(cb: CallbackQuery, shop: Shop):
    key = cb.data.split(":")[3]
    f = FEATURE_BY_KEY.get(key)
    if not f:
        await cb.answer("نامعتبر", show_alert=True)
        return
    if key == "vpn_allow_addon":
        await _addon_screen(cb, shop, "u")
        await cb.answer()
        return
    if not f[3]:
        await cb.answer("این مورد تنظیم دیگری ندارد.", show_alert=True)
        return
    await _settings_list(cb, shop, f[3], "f:" + key, f"⚙️ <b>تنظیمات «{f[1]}»</b>", "adm:vpn:ft")
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:st")
async def cb_settings(cb: CallbackQuery, shop: Shop):
    rows = [[btn(t, f"adm:vpn:ss:{sid}", role="admin")] for sid, t, _ in VPN_SECTIONS[:1]]
    rows.append([btn("➕ حجم و زمان اضافه — کاربران", "adm:vpn:xs:u", role="admin")])
    rows += [[btn(t, f"adm:vpn:ss:{sid}", role="admin")] for sid, t, _ in VPN_SECTIONS[1:]]
    rows.append([btn("📍 تغییر لوکیشن — کاربران", "adm:vpn:mvs", role="admin"), btn("📍 تغییر لوکیشن — نماینده‌ها", "adm:vpn:amvs", role="admin")])
    rows.append([btn("🖼 پس‌زمینه QR", "adm:vpn:qb", role="admin")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "⚙️ <b>تنظیمات فروش VPN</b>\nیک بخش را انتخاب کنید:", ikb(rows))
    await cb.answer()


async def _move_screen(cb: CallbackQuery, shop: Shop):
    from vpn_service import pextra
    on = shop.s.get("vpn_allow_move", "0") == "1"
    panels = [p for p in await shop.db.list_vpn_panels() if p["type"] != "manual"]
    nb = len(await shop.db.vm_batches("svc", 15))
    lines = ["📍 <b>تغییر لوکیشن</b>", "",
             f"<b>۱) دستی — توسط خود کاربر</b> ({onoff(on)})",
             "کاربر در «سرویس‌های من» روی «📍 تغییر لوکیشن» می‌زند و سرویس با <b>حجم و زمانِ باقی‌مانده</b> (نه تازه) روی پنل مقصد ساخته می‌شود؛ نسخه‌ی قبلی از پنل قبلی پاک می‌شود. "
             "پنل‌هایی را که کاربر می‌تواند به آن‌ها برود تیک بزنید (بدون مقصد، دکمه‌ای نشان داده نمی‌شود). سقف دفعات هر سرویس: «🧩 قابلیت‌های پنل».",
             "", "<b>۲) خودکار — توسط شما</b>",
             "برای کاربران انتخابی، همه، یا کسانی که حجم/زمانشان رو به پایان است؛ لینک جدید برای همه فرستاده می‌شود. اشتباه شد؟ «↩️ بازگشت جابه‌جایی‌ها»."]
    rows = [[btn(f"تغییر لوکیشن توسط کاربر: {onoff(on)}", "adm:vpn:mvg", role="action")]]
    for p in panels:
        rows.append([btn(f"{'✅' if pflag(p, 'move_to') else '⬜'} مقصد: {p['name']}", f"adm:vpn:mvt:{p['id']}", role="action")])
    if not panels:
        lines.append("\n⚠️ هنوز پنل غیر دستی ثبت نشده است.")
    rows.append([btn("🔁 جابه‌جایی خودکار (ادمین)", "adm:vpn:bkm", role="admin"), btn(f"↩️ بازگشت جابه‌جایی‌ها ({nb})" if nb else "↩️ بازگشت جابه‌جایی‌ها", "adm:vpn:mvb", role="admin")])
    rows.append(back_row("adm:vpn:st"))
    await view(cb, "\n".join(lines), ikb(rows))


@router.callback_query(F.data == "adm:vpn:mvs")
async def cb_move_screen(cb: CallbackQuery, shop: Shop):
    await _move_screen(cb, shop)
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:mvg")
async def cb_move_toggle(cb: CallbackQuery, shop: Shop):
    await shop.set_setting("vpn_allow_move", "0" if shop.s.get("vpn_allow_move", "0") == "1" else "1", f"tg:{cb.from_user.id}")
    await _move_screen(cb, shop)
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:vpn:mvt:"))
async def cb_move_dest(cb: CallbackQuery, shop: Shop):
    from vpn_service import pextra
    p = await shop.db.get_vpn_panel(int(cb.data.split(":")[3]))
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    ex = dict(pextra(p))
    ex["move_to"] = 0 if pflag(p, "move_to") else 1
    await shop.db.update_vpn_panel(p["id"], extra=json.dumps(ex, ensure_ascii=False))
    await _move_screen(cb, shop)
    await cb.answer("انجام شد")


async def _section(cb: CallbackQuery, shop: Shop, sid: str):
    _, title, keys = _SEC_BY_ID[sid]
    top = None
    if sid == "a":
        pid = int(shop.s.get("vpn_agent_cv_panel", "0") or 0)
        pn = await shop.db.get_vpn_panel(pid) if pid else None
        top = [[btn(f"🖥 پنل حجم دلخواه: {pn['name'] if pn else 'انتخاب نشده ⚠️'}"[:64], "adm:vpn:cvp", role="admin")]]
    await _settings_list(cb, shop, keys, f"s:{sid}", f"⚙️ <b>{title}</b>\nروی هر مورد بزنید تا تغییرش بدهید.", "adm:vpn:st", top)


@router.callback_query(F.data == "adm:vpn:cvp")
async def cb_cv_panel(cb: CallbackQuery, shop: Shop):
    cur = int(shop.s.get("vpn_agent_cv_panel", "0") or 0)
    panels = [p for p in await shop.db.list_vpn_panels() if getattr(vpn_panels.DRIVERS.get(p["type"]), "ADMIN", False)]
    if not panels:
        await cb.answer("ابتدا یک پنل پاسارگارد، مرزبان یا مرزنشین اضافه کنید.", show_alert=True)
        return
    rows = [[btn(f"{'✅' if p['id'] == cur else '⬜'} {p['name']} · {p['type']}", f"adm:vpn:cvq:{p['id']}", role="action")] for p in panels]
    rows.append(back_row("adm:vpn:ss:a"))
    await view(cb, "🖥 <b>حجم دلخواه نمایندگی روی کدام پنل ساخته شود؟</b>\nتا انتخاب نکنید، این امکان برای کاربر نشان داده نمی‌شود.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:cvq:"))
async def cb_cv_panel_set(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    if not await shop.db.get_vpn_panel(pid):
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await shop.set_setting("vpn_agent_cv_panel", str(pid), f"tg:{cb.from_user.id}")
    await cb_cv_panel(cb, shop)


@router.callback_query(F.data.startswith("adm:vpn:ss:"))
async def cb_section(cb: CallbackQuery, shop: Shop):
    sid = cb.data.split(":")[3]
    if sid not in _SEC_BY_ID:
        await cb.answer("نامعتبر", show_alert=True)
        return
    await _section(cb, shop, sid)
    await cb.answer()


# ---- حجم و زمان اضافه (هر بخش یک صفحه: اول روشن/خاموش، بعد قیمت‌ها، بعد حداقل و حداکثر)
def _sv(shop: Shop, key, default="0"):
    return shop.s.get(key, default) or default


def _fnum(v) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return "—"


async def _addon_screen(cb: CallbackQuery, shop: Shop, sec: str):
    title, pre = ADDON_SECS[sec]
    o = f"x:{sec}"
    g, d = f"{pre}_cgb", f"{pre}_cd"
    on_g, on_d = _sv(shop, f"{g}_on") == "1", _sv(shop, f"{d}_on") == "1"
    warn = ""
    for on, k, nm in ((on_g, g, "حجم"), (on_d, d, "زمان")):
        if on and float(_sv(shop, f"{k}_price") or 0) <= 0:
            warn += f"\n⚠️ قیمت {nm} صفر است؛ تا قیمت نگذارید به کاربر نشان داده نمی‌شود."
    text = (f"➕ <b>حجم و زمان اضافه — {title}</b>\n\n"
            f"📦 حجم دلخواه: {onoff(on_g)}\n   قیمت هر گیگ: {fmt(int(float(_sv(shop, g + '_price') or 0)))} تومان | بازه: {_fnum(_sv(shop, g + '_min', '1'))} تا {_fnum(_sv(shop, g + '_max', '1000'))} گیگ\n"
            f"⏳ زمان دلخواه: {onoff(on_d)}\n   قیمت هر روز: {fmt(int(float(_sv(shop, d + '_price') or 0)))} تومان | بازه: {_fnum(_sv(shop, d + '_min', '1'))} تا {_fnum(_sv(shop, d + '_max', '365'))} روز"
            + warn + "\n\nبرای تغییر هر مورد روی آن بزنید.")
    rows = []
    if sec == "u":
        rows.append([btn(f"خرید حجم/زمان اضافه (کل): {onoff(shop.s.get('vpn_allow_addon', '1') == '1')}", f"adm:vpn:se:vpn_allow_addon:{o}", role="action")])
        rows.append([btn("📦 بسته‌های افزودنی ثابت", "adm:vpn:ad", role="admin")])
    rows += [[btn(f"📦 حجم: {onoff(on_g)}", f"adm:vpn:se:{g}_on:{o}", role="action"), btn(f"⏳ زمان: {onoff(on_d)}", f"adm:vpn:se:{d}_on:{o}", role="action")],
             [btn(f"💰 قیمت هر گیگ: {_fnum(_sv(shop, g + '_price'))}", f"adm:vpn:se:{g}_price:{o}", role="admin"),
              btn(f"💰 قیمت هر روز: {_fnum(_sv(shop, d + '_price'))}", f"adm:vpn:se:{d}_price:{o}", role="admin")],
             [btn(f"⬇️ حداقل حجم: {_fnum(_sv(shop, g + '_min', '1'))}", f"adm:vpn:se:{g}_min:{o}", role="admin"),
              btn(f"⬇️ حداقل زمان: {_fnum(_sv(shop, d + '_min', '1'))}", f"adm:vpn:se:{d}_min:{o}", role="admin")],
             [btn(f"⬆️ حداکثر حجم: {_fnum(_sv(shop, g + '_max', '1000'))}", f"adm:vpn:se:{g}_max:{o}", role="admin"),
              btn(f"⬆️ حداکثر زمان: {_fnum(_sv(shop, d + '_max', '365'))}", f"adm:vpn:se:{d}_max:{o}", role="admin")],
             back_row("adm:vpn:st")]
    await view(cb, text, ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:xs:"))
async def cb_addon_screen(cb: CallbackQuery, shop: Shop):
    sec = cb.data.split(":")[3]
    if sec not in ADDON_SECS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    await _addon_screen(cb, shop, sec)
    await cb.answer()


async def _back_of(origin: str) -> tuple[str, str]:
    if origin.startswith("f:"):
        return "adm:vpn:fs:" + origin[2:], "adm:vpn:fs:" + origin[2:]
    if origin.startswith("x:"):
        return "adm:vpn:xs:" + origin[2:], "adm:vpn:xs:" + origin[2:]
    if origin.startswith("s:"):
        return "adm:vpn:ss:" + origin[2:], "adm:vpn:ss:" + origin[2:]
    if origin == "f":
        return "adm:vpn:ft", "adm:vpn:ft"
    return "adm:vpn:st", "adm:vpn:st"


@router.callback_query(F.data.startswith("adm:vpn:se:"))
async def cb_setting_edit(cb: CallbackQuery, shop: Shop, state: FSMContext):
    from settings_schema import FIELDS
    from ui import hint_tg
    parts = cb.data.split(":", 4)
    key, origin = parts[3], parts[4] if len(parts) > 4 else "s"
    if key not in FIELDS or key not in VPN_KEYS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    back, _ = await _back_of(origin)
    label, typ, hint = FIELDS[key]
    if typ == "bool":
        await shop.set_setting(key, "0" if shop.s.get(key, "0") == "1" else "1", f"tg:{cb.from_user.id}")
        await cb.answer("انجام شد")
        if origin == "f":
            await _features(cb, shop)
        elif origin.startswith("x:"):
            await _addon_screen(cb, shop, origin[2:])
        elif origin.startswith("s:"):
            await _section(cb, shop, origin[2:])
        elif origin.startswith("f:"):
            await _settings_list(cb, shop, FEATURE_BY_KEY[origin[2:]][3], origin, f"⚙️ <b>تنظیمات «{FEATURE_BY_KEY[origin[2:]][1]}»</b>", "adm:vpn:ft")
        else:
            await cb_settings(cb, shop)
        return
    if typ.startswith("select:"):
        opts = typ.split(":", 1)[1].split("|")
        rows = [[btn(("✅ " if shop.s.get(key) == o else "") + o, f"adm:vpn:ssv:{key}:{i}:{origin}", role="action")] for i, o in enumerate(opts)]
        rows.append(back_row(back))
        await view(cb, f"<b>{esc(label)}</b>\n{hint_tg(hint)}", ikb(rows))
        await cb.answer()
        return
    await state.set_state(AV.wiz)
    await state.update_data(flow="setting", key=key, origin=origin)
    cur = shop.s.get(key, "")
    await ans(cb.message, f"<b>{esc(label)}</b>\n{hint_tg(hint) if hint else ''}\n\nمقدار فعلی: <code>{esc(cur) if cur else '—'}</code>\nمقدار جدید را بفرستید. برای خالی کردن «-» بفرستید.", _ask_kb(back))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:ssv:"))
async def cb_setting_select(cb: CallbackQuery, shop: Shop):
    from settings_schema import FIELDS
    parts = cb.data.split(":", 5)
    key, idx, origin = parts[3], int(parts[4]), parts[5] if len(parts) > 5 else "s"
    opts = FIELDS[key][1].split(":", 1)[1].split("|")
    await shop.set_setting_validated(key, opts[idx], f"tg:{cb.from_user.id}")
    await cb.answer("انجام شد")
    back, _ = await _back_of(origin)
    if origin.startswith("f:"):
        await _settings_list(cb, shop, FEATURE_BY_KEY[origin[2:]][3], origin, f"⚙️ <b>تنظیمات «{FEATURE_BY_KEY[origin[2:]][1]}»</b>", "adm:vpn:ft")
    elif origin.startswith("s:"):
        await _section(cb, shop, origin[2:])
    else:
        await cb_settings(cb, shop)


# ============================================================ عملیات گروهی (انتخاب پنل ← فیلتر ← اقدام ← تأیید)
def _bk_title(d: dict, shop_panels: dict) -> str:
    from vpn_ops import BULK_ACTIONS, BULK_FILTERS
    pn = shop_panels.get(d.get("p")) if d.get("p") else None
    f = BULK_FILTERS.get(d.get("f"), "—").replace("N", str(d.get("n", "")))
    t = f"🧰 <b>عملیات گروهی</b>\n🖧 پنل: <b>{esc(pn['name']) if pn else 'همه‌ی پنل‌ها'}</b>"
    if d.get("f"):
        t += f"\n🔎 فیلتر: <b>{f}</b>"
    if d.get("a"):
        t += f"\n⚙️ اقدام: <b>{BULK_ACTIONS[d['a']]}</b>" + (f" — مقدار: <code>{esc(str(d['v'])[:60])}</code>" if d.get("v") not in (None, "") else "")
    if d.get("t"):
        t += f"\n📍 مقصد: <b>{esc(shop_panels[d['t']]['name'])}</b>"
    return t


async def _bk_panels(shop: Shop) -> dict:
    return {p["id"]: p for p in await shop.db.list_vpn_panels()}


@router.callback_query(F.data == "adm:vpn:bk")
async def cb_bulk(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await _bk_first(cb, shop)


@router.callback_query(F.data == "adm:vpn:bkm")
async def cb_bulk_move_entry(cb: CallbackQuery, shop: Shop, state: FSMContext):
    """ورود از «📍 تغییر لوکیشن ← جابه‌جایی خودکار»: همان ویزارد، ولی اقدام از قبل «انتقال» است."""
    await state.clear()
    await state.update_data(bkpre="move")
    await _bk_first(cb, shop, move=True)


async def _bk_first(cb, shop: Shop, move: bool = False):
    panels = await shop.db.list_vpn_panels()
    rows = [[btn("🖧 همه‌ی پنل‌ها", "adm:vpn:bkp:0", role="admin")]]
    rows += [[btn(f"{p['name']} ({p['n_services']})", f"adm:vpn:bkp:{p['id']}", role="admin")] for p in panels]
    rows.append(back_row("adm:vpn:mvs" if move else "adm:vpn"))
    await view(cb, ("🔁 <b>جابه‌جایی خودکار کاربران به پنل دیگر</b>\n۱) پنلِ <b>مبدأ</b> را انتخاب کنید. بعد می‌گویید کدام کاربرها (همه / فیلتر پایان حجم و زمان / انتخاب دستی با تیک) و مقصد، و لینک جدید برای همه ارسال می‌شود."
                    if move else "🧰 <b>عملیات گروهی روی کاربران (سرویس‌ها)</b>\n۱) پنل موردنظر را انتخاب کنید. بعد فیلتر یا «انتخاب کاربر» و اقدام را می‌گویید و قبل از اجرا فهرست را می‌بینید و تأیید می‌کنید.\n(برای نماینده‌ها: 🧑‍💼 نماینده‌ها ← عملیات گروهی)"), ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:bkp:"))
async def cb_bulk_panel(cb: CallbackQuery, shop: Shop, state: FSMContext):
    from vpn_ops import BULK_FILTERS
    pid = int(cb.data.split(":")[3])
    d = {"p": pid or None}
    if (await state.get_data()).get("bkpre") == "move":
        d["pre"] = "move"
    await state.update_data(bk=d)
    rows = [[btn(t.replace(" N ", " ؟ "), f"adm:vpn:bkf:{k}", role="admin")] for k, t in BULK_FILTERS.items()]
    rows.append(back_row("adm:vpn:bk"))
    await view(cb, _bk_title(d, await _bk_panels(shop)) + "\n\n۲) کدام سرویس‌ها؟", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:bkf:"))
async def cb_bulk_filter(cb: CallbackQuery, shop: Shop, state: FSMContext):
    flt = cb.data.split(":")[3]
    d = (await state.get_data()).get("bk")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    d["f"] = flt
    await state.update_data(bk=d)
    if flt == "sel":
        d.setdefault("sel", [])
        await state.update_data(bk=d)
        await _bk_picker(cb, shop, state, 0)
        await cb.answer()
        return
    if flt in ("soon", "lowvol"):
        opts = (1, 3, 7, 14) if flt == "soon" else (5, 10, 20, 30)
        unit = "روز" if flt == "soon" else "٪"
        rows = [[btn(f"{n} {unit}", f"adm:vpn:bkn:{n}", role="admin") for n in opts], back_row("adm:vpn:bk")]
        await view(cb, _bk_title(d, await _bk_panels(shop)) + ("\n\nتا چند روز دیگر به پایان زمان برسند؟" if flt == "soon" else "\n\nکمتر از چند درصد حجم مانده باشد؟"), ikb(rows))
    else:
        await _bk_pick_action(cb, shop, d)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:bkn:"))
async def cb_bulk_n(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("bk")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    d["n"] = int(cb.data.split(":")[3])
    await state.update_data(bk=d)
    await _bk_pick_action(cb, shop, d)
    await cb.answer()


# ---- «انتخاب کاربر»: فهرست سرویس‌ها با تیک (صفحه‌بندی، جستجو، انتخاب همه‌ی صفحه)
BK_PER = 8


async def _bk_picker(cb, shop: Shop, state: FSMContext, page: int):
    d = (await state.get_data()).get("bk") or {}
    sel = set(d.get("sel") or [])
    rows_db, total = await shop.vpn.ops.search_page(d.get("p"), d.get("q", ""), page, BK_PER)
    rows = [[btn(f"{'☑️' if r['id'] in sel else '⬜️'} {r['name']} · {r['user_id']}" + (f" · {r['panel_name']}" if not d.get("p") and r.get("panel_name") else ""),
                 f"adm:vpn:bkc:{r['id']}:{page}", role="confirm" if r["id"] in sel else "admin")] for r in rows_db]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:vpn:bkg:{page - 1}", role="back"))
    if (page + 1) * BK_PER < total:
        nav.append(btn("▶️", f"adm:vpn:bkg:{page + 1}", role="action"))
    if nav:
        rows.append(nav)
    rows.append([btn("☑️ همه‌ی این صفحه", f"adm:vpn:bkca:{page}", role="action"), btn("🧹 پاک کردن انتخاب", f"adm:vpn:bkcx:{page}", role="action")])
    rows.append([btn("🔎 جستجو (نام/آیدی)" + (" ✅" if d.get("q") else ""), "adm:vpn:bkq", role="admin")])
    if sel:
        rows.append([btn(f"➡️ ادامه با {fmt(len(sel))} سرویس", "adm:vpn:bkd", role="confirm")])
    rows.append(back_row("adm:vpn:bk"))
    text = (_bk_title(d, await _bk_panels(shop)) + f"\n\n۲) سرویس‌های موردنظر را تیک بزنید (انتخاب‌شده: <b>{fmt(len(sel))}</b>)"
            + (f"\n🔎 جستجو: <code>{esc(d['q'])}</code>" if d.get("q") else "") + f"\nصفحه {page + 1} از {max(1, -(-total // BK_PER))} · کل: {fmt(total)}")
    if isinstance(cb, Message):
        await ans(cb, text, ikb(rows))
    else:
        await view(cb, text, ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:bkg:"))
async def cb_bk_page(cb: CallbackQuery, shop: Shop, state: FSMContext):
    if (await state.get_data()).get("bk") is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    await _bk_picker(cb, shop, state, int(cb.data.split(":")[3]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:bkc:"))
async def cb_bk_toggle(cb: CallbackQuery, shop: Shop, state: FSMContext):
    _, _, _, sid, page = cb.data.split(":")
    d = (await state.get_data()).get("bk")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    sel = set(d.get("sel") or [])
    sel.symmetric_difference_update({int(sid)})
    d["sel"] = sorted(sel)
    await state.update_data(bk=d)
    await _bk_picker(cb, shop, state, int(page))
    await cb.answer()


@router.callback_query(F.data.startswith(("adm:vpn:bkca:", "adm:vpn:bkcx:")))
async def cb_bk_bulk_toggle(cb: CallbackQuery, shop: Shop, state: FSMContext):
    kind, page = cb.data.split(":")[2], int(cb.data.split(":")[3])
    d = (await state.get_data()).get("bk")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    if kind == "bkcx":
        d["sel"] = []
    else:
        rows_db, _ = await shop.vpn.ops.search_page(d.get("p"), d.get("q", ""), page, BK_PER)
        d["sel"] = sorted(set(d.get("sel") or []) | {r["id"] for r in rows_db})
    await state.update_data(bk=d)
    await _bk_picker(cb, shop, state, page)
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:bkq")
async def cb_bk_search(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="bkq")
    await ans(cb.message, "بخشی از نام سرویس یا آیدی عددی کاربر را بفرستید (برای پاک‌کردن جستجو «-»):", _ask_kb("adm:vpn:bkg:0"))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:bkd")
async def cb_bk_done(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("bk") or {}
    if not d.get("sel"):
        await cb.answer("هیچ سرویسی تیک نخورده.", show_alert=True)
        return
    await _bk_pick_action(cb, shop, d)
    await cb.answer()


async def _bk_target_picker(cb, shop: Shop, d: dict):
    panels = await shop.db.list_vpn_panels()
    rows = [[btn(f"📍 {p['name']}", f"adm:vpn:bkt:{p['id']}", role="admin")] for p in panels if p["enabled"] and p["type"] != "manual" and p["id"] != d.get("p")]
    rows.append(back_row("adm:vpn:bk"))
    await view(cb, _bk_title(d, await _bk_panels(shop)) + "\n\nبه کدام پنل منتقل شوند؟ (حجم و زمان باقی‌مانده حفظ می‌شود و لینک جدید برای همه فرستاده می‌شود)", ikb(rows))


async def _bk_pick_action(cb, shop: Shop, d: dict):
    from vpn_ops import BULK_ACTIONS
    if d.get("pre") == "move":                                  # ورود از «جابه‌جایی خودکار»: مستقیم برو سراغ مقصد
        await _bk_target_picker(cb, shop, d)
        return
    rows, row = [], []
    for k, t in BULK_ACTIONS.items():
        row.append(btn(t, f"adm:vpn:bka:{k}", role="reject" if k == "delete" else "admin"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(back_row("adm:vpn:bk"))
    await view(cb, _bk_title(d, await _bk_panels(shop)) + "\n\n۳) چه کاری؟", ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:bka:"))
async def cb_bulk_action(cb: CallbackQuery, shop: Shop, state: FSMContext):
    act = cb.data.split(":")[3]
    d = (await state.get_data()).get("bk")
    if d is None or act not in __import__("vpn_ops").BULK_ACTIONS:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    d["a"] = act
    await state.update_data(bk=d)
    if act in ("add_days", "add_gb", "sub_days", "sub_gb", "msg", "credit", "sub_credit", "discount"):
        await state.set_state(AV.wiz)
        await state.update_data(flow="bkval")
        q = {"add_days": "چند روز اضافه شود؟ (عدد)", "add_gb": "چند گیگ اضافه شود؟ (عدد)",
             "sub_days": "چند روز از زمان هر سرویس کم شود؟ (عدد؛ سرویس بی‌انقضا رد می‌شود)",
             "sub_gb": "چند گیگ از حجم هر سرویس کم شود؟ (عدد؛ پایین‌تر از مصرف فعلی نمی‌رود؛ نامحدود رد می‌شود)",
             "sub_credit": "چند تومان از کیف پول هر کاربر کم شود؟ (عدد؛ اگر موجودی کم باشد رد می‌شود؛ هر کاربر فقط یک‌بار)",
             "credit": "چند تومان به کیف پول هر کاربر اضافه شود؟ (عدد؛ برای کاربری که چند سرویس دارد فقط یک‌بار)",
             "discount": "درصد تخفیف شخصی (۰ تا ۹۰)؟ برای برداشتن تخفیف ۰ بفرستید. جایگزین تخفیف قبلی می‌شود.", "msg": "متن پیام را بفرستید (به صاحب هر سرویس یک‌بار ارسال می‌شود؛ HTML ساده مجاز است):"}[act]
        await ans(cb.message, q, _ask_kb("adm:vpn:bk"))
    elif act == "move":
        panels = await shop.db.list_vpn_panels()
        rows = [[btn(f"📍 {p['name']}", f"adm:vpn:bkt:{p['id']}", role="admin")] for p in panels if p["enabled"] and p["type"] != "manual" and p["id"] != d.get("p")]
        rows.append(back_row("adm:vpn:bk"))
        await view(cb, _bk_title(d, await _bk_panels(shop)) + "\n\nبه کدام پنل منتقل شوند؟ (حجم و زمان باقی‌مانده حفظ می‌شود)", ikb(rows))
    else:
        await _bk_confirm(cb, shop, state)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:bkt:"))
async def cb_bulk_target(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("bk")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    d["t"] = int(cb.data.split(":")[3])
    if d.get("pre") == "move":
        d["a"] = "move"
    await state.update_data(bk=d)
    await _bk_confirm(cb, shop, state)
    await cb.answer()


async def _bk_value(m: Message, shop: Shop, state: FSMContext, d0):
    d = d0.get("bk") or {}
    act = d.get("a")
    try:
        if act == "msg":
            val = (m.html_text or m.text or "").strip()
            if not val:
                raise ValueError("متن خالی است.")
            val = val[:3500]
        else:
            val = _num("float" if act in ("add_gb", "sub_gb") else "int", m.text)
            if act == "discount":
                if not 0 <= val <= 90:
                    raise ValueError("عدد باید بین ۰ تا ۹۰ باشد.")
            elif val <= 0:
                raise ValueError("عدد باید بیشتر از صفر باشد.")
    except ValueError as e:
        await ans(m, f"❌ {e}")
        return
    d["v"] = val
    await state.set_state(None)
    await state.update_data(bk=d, flow=None)
    await _bk_confirm(m, shop, state)


async def _bk_confirm(src, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("bk") or {}
    if d.get("f") == "sel":
        rows_ = [r for r in [await shop.db.get_vpn_service(i) for i in d.get("sel") or []] if r and r["status"] not in ("deleted", "pending_manual")]
    else:
        rows_ = await shop.vpn.ops.select(d.get("p"), d.get("f"), d.get("n", 3))
    if d.get("a") == "move" and d.get("t"):
        rows_ = [r for r in rows_ if r["panel_id"] != d["t"] and (r.get("panel_type") or "") != "manual"]
    if d.get("a") not in ("move",):
        pass
    cap = 2000
    over = len(rows_) > cap
    rows_ = rows_[:cap]
    if not rows_:
        await ans(src if isinstance(src, Message) else src.message, _bk_title(d, await _bk_panels(shop)) + "\n\n⚠️ هیچ سرویسی با این فیلتر پیدا نشد.", ikb([back_row("adm:vpn:bk")]))
        return
    d["ids"] = [r["id"] for r in rows_]
    await state.update_data(bk=d)
    sample = "، ".join(f"<code>{esc(r['name'])}</code>" for r in rows_[:5])
    users = len({r["user_id"] for r in rows_})
    danger = d["a"] == "delete"
    text = (_bk_title(d, await _bk_panels(shop)) + f"\n\n📊 <b>{fmt(len(rows_))}</b> سرویس از <b>{fmt(users)}</b> کاربر" + (f" (سقف {cap} در هر بار)" if over else "")
            + f"\nنمونه: {sample}" + ("\n\n⚠️ <b>حذف برگشت‌پذیر نیست و سرویس‌ها از پنل هم پاک می‌شوند.</b>" if danger else "")
            + ("\n\n📍 لینک قبلی همه‌ی این کاربران از کار می‌افتد و <b>لینک جدید برای هر کدام ارسال می‌شود</b>. اگر اشتباه بود، از «📍 تغییر لوکیشن ← ↩️ بازگشت جابه‌جایی‌ها» می‌توانید برگردانید." if d.get("a") == "move" else "")
            + "\n\nاجرا شود؟")
    kb = ikb([[btn("🗑 بله، حذف شود" if danger else "✅ اجرا", "adm:vpn:bkgo", role="reject" if danger else "confirm"), btn("❌ لغو", "adm:vpn:bk", role="back")]])
    await ans(src if isinstance(src, Message) else src.message, text, kb)


@router.callback_query(F.data == "adm:vpn:bkgo")
async def cb_bulk_go(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("bk") or {}
    ids = d.get("ids") or []
    if not ids or not d.get("a"):
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    uid = cb.from_user.id
    if shop.vpn.ops.jobs.get(uid, {}).get("running"):
        await cb.answer("یک عملیات گروهی دیگر از شما در حال اجراست.", show_alert=True)
        return
    await state.clear()
    chat = cb.message.chat.id
    rows = [r for r in [await shop.db.get_vpn_service(i) for i in ids] if r]       # وضعیت تازه، نه لحظه‌ی انتخاب
    job = shop.vpn.ops.jobs[uid] = {"running": True}
    panels = await _bk_panels(shop)
    summary = _bk_title(d, panels)

    async def run():
        async def progress(i, n):
            await shop.safe_send(chat, f"⏳ {i}/{n} ...")
        try:
            res = await shop.vpn.ops.bulk_run(rows, d["a"], d.get("v"), d.get("t"), actor=f"tg:{uid}", progress=progress)
        finally:
            job["running"] = False
        err = ("\n\n" + "\n".join(esc(x) for x in res["errors"])) if res["errors"] else ""
        extra = [[btn("↩️ بازگشت این جابه‌جایی", f"adm:vpn:mvb:{res['batch']}", role="action")]] if res.get("batch") and res["ok"] else []
        await shop.safe_send(chat, f"🏁 <b>پایان عملیات گروهی</b>\n{summary}\n\n✅ موفق: {res['ok']}\n❌ ناموفق: {res['fail']}{err}", reply_markup=ikb(extra + [back_row("adm:vpn")]))
        await shop.audit(f"tg:{uid}", f"🧰 عملیات گروهی VPN: {d['a']} روی {len(rows)} سرویس (موفق {res['ok']}، ناموفق {res['fail']})")

    shop.spawn(run())
    await shop.db.log(f"tg:{uid}", "vpn_bulk", f"{d['a']} n={len(rows)}")
    await ans(cb.message, f"🚀 اجرا شروع شد ({fmt(len(rows))} سرویس)؛ وقتی تمام شد گزارش می‌دهم. می‌توانید از ربات استفاده کنید.", ikb([back_row("adm:vpn")]))
    await cb.answer()


# ---- انتقال یک سرویس به پنل دیگر (ادمین)
@router.callback_query(F.data.regexp(r"^adm:vpn:smv:\d+$"))
async def cb_svc_move(cb: CallbackQuery, shop: Shop):
    svc = await shop.db.get_vpn_service(int(cb.data.split(":")[3]))
    if not svc:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    tg = await shop.vpn.ops.targets(svc, admin=True)
    if not tg:
        await cb.answer("پنل فعال دیگری برای انتقال نیست.", show_alert=True)
        return
    rows = [[btn(f"📍 {p['name']}", f"adm:vpn:smg:{svc['id']}:{p['id']}", role="admin")] for p in tg]
    rows.append(back_row(f"adm:vpn:s:{svc['id']}"))
    await view(cb, f"📍 <b>انتقال «{esc(svc['name'])}»</b>\nحجم و زمانِ باقی‌مانده حفظ می‌شود و نسخه‌ی قبلی از پنل قبلی پاک می‌شود. مقصد؟", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:smg:\d+:\d+$"))
async def cb_svc_move_go(cb: CallbackQuery, shop: Shop):
    from vpn_ops import MoveError
    _, _, _, sid, pid = cb.data.split(":")
    await cb.answer("در حال انتقال ...")
    try:
        row = await shop.vpn.ops.move(int(sid), int(pid), actor=f"tg:{cb.from_user.id}")
    except MoveError as e:
        await ans(cb.message, f"❌ {esc(str(e))}", ikb([back_row(f"adm:vpn:s:{sid}")]))
        return
    await shop.vpn.ops.notify_moved(row)
    text, kb = await svc_card(shop, row)
    await view(cb, "✅ منتقل شد و برای کاربر ارسال شد.\n\n" + text, kb)


# ============================================================ پنل‌ها
@router.callback_query(F.data == "adm:vpn:pl")
async def cb_panels(cb: CallbackQuery, shop: Shop):
    rows = [[btn(f"{'🟢' if p['enabled'] else '⚫️'} {p['name']} · {TYPE_LABEL.get(p['type'], p['type'])} · {p['n_services']}", f"adm:vpn:p:{p['id']}", role="admin")]
            for p in await shop.db.list_vpn_panels()]
    rows.append([btn("➕ افزودن پنل", "adm:vpn:pa", role="confirm")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "🖧 <b>پنل‌ها</b>\nروی هر پنل بزنید تا تنظیماتش باز شود.", ikb(rows))
    await cb.answer()


def panel_card(p, shop) -> str:
    ex = pextra(p)
    return (f"🖧 <b>{esc(p['name'])}</b> — {'✅ فعال' if p['enabled'] else '⚫️ غیرفعال'}\n"
            f"نوع: {esc(TYPE_LABEL.get(p['type'], p['type']))}\n"
            f"آدرس: <code>{esc(p['url'] or '-')}</code>\nکاربر: <code>{esc(p['username'] or '-')}</code>\n"
            f"اینباند/گروه: <code>{esc(p['inbound_ids'] or '-')}</code>\nآدرس اشتراک: <code>{esc(p['sub_url'] or '-')}</code>\n"
            f"ظرفیت: {p['capacity'] or 'نامحدود'} | سرویس‌ها: {p.get('n_services', 0)}\n"
            f"🎁 تست: {'روشن' if shop.vpn.test_on(p) else 'خاموش'} | {shop.vpn.test_params(p)[0]} روز / {shop.vpn.test_params(p)[1]:g} گیگ / {shop.vpn.test_params(p)[2]} بار برای هر کاربر"
            + (f"\n🚫 مخفی برای {len(ex.get('hidden', []))} کاربر" if ex.get("hidden") else ""))


async def _show_panel(cb: CallbackQuery, shop: Shop, pid: int, fresh=False):
    p = next((x for x in await shop.db.list_vpn_panels() if x["id"] == pid), None)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    rows = [[btn("🔌 تست اتصال", f"adm:vpn:pt:{pid}", role="action"), btn("📈 وضعیت پنل", f"adm:vpn:ps:{pid}", role="action")],
            [btn("⏯ فعال/غیرفعال", f"adm:vpn:ptg:{pid}", role="action"), btn("🧩 قابلیت‌های پنل", f"adm:vpn:pf:{pid}", role="admin")],
            [btn("✏️ نام", f"adm:vpn:pe:{pid}:name", role="admin"), btn("🔗 آدرس", f"adm:vpn:pe:{pid}:url", role="admin")],
            [btn("👤 نام کاربری", f"adm:vpn:pe:{pid}:username", role="admin"), btn("🔑 رمز", f"adm:vpn:pe:{pid}:password", role="admin")],
            [btn("🗝 توکن", f"adm:vpn:pe:{pid}:token", role="admin"), btn("🌐 آدرس اشتراک", f"adm:vpn:pe:{pid}:sub_url", role="admin")],
            [btn("🔀 اینباند/گروه", f"adm:vpn:pe:{pid}:inbound_ids", role="admin"), btn("📊 ظرفیت", f"adm:vpn:pe:{pid}:capacity", role="admin")],
            [btn("🚫 مخفی کردن برای کاربر", f"adm:vpn:ph:{pid}", role="admin")]]
    if p["type"] == "manual":
        rows += [[btn("➕ اضافه کردن کانفیگ", f"adm:vpn:cfa:{pid}", role="confirm"), btn("📦 موجودی کانفیگ", f"adm:vpn:cfs:{pid}", role="action")],
                 [btn("🗑 حذف کانفیگ", f"adm:vpn:cfd:{pid}", role="reject"), btn("✏️ ویرایش کانفیگ", f"adm:vpn:cfe:{pid}", role="admin")]]
    rows += [[btn("🗑 حذف پنل", f"adm:vpn:pdel:{pid}", role="reject")], back_row("adm:vpn:pl")]
    if fresh:
        await ans(cb.message, panel_card(p, shop), ikb(rows))
    else:
        await view(cb, panel_card(p, shop), ikb(rows))


@router.callback_query(F.data.regexp(r"^adm:vpn:p:\d+$"))
async def cb_panel(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_vpn_panel(pid)
    if p and p["type"] != "manual":                # مثل میرزا: اول مانیتورینگ سرور، بعد منوی پنل
        await cb.answer("در حال گرفتن وضعیت سرور...")
        ok, txt = await shop.vpn.panel_status_text(pid)
        await ans(cb.message, txt if ok else f"🖥 <b>وضعیت سرور</b>\n⚠️ مانیتورینگ در دسترس نیست:\n<code>{esc(txt)}</code>")
        await _show_panel(cb, shop, pid, fresh=True)
        return
    await _show_panel(cb, shop, pid)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:ptg:"))
async def cb_panel_toggle(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_vpn_panel(pid)
    if p:
        await shop.db.update_vpn_panel(pid, enabled=0 if p["enabled"] else 1)
    await _show_panel(cb, shop, pid)
    await cb.answer("انجام شد")


# ---- قابلیت‌های هر پنل (جدول عنوان | وضعیت)
async def _panel_features(cb: CallbackQuery, shop: Shop, pid: int):
    p = await shop.db.get_vpn_panel(pid)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    rows = [[btn("عنوان", "adm:vpn:noop", role="back"), btn("وضعیت", "adm:vpn:noop", role="back")]]
    for key in (k for k in PANEL_FLAGS if k not in ("move_to", "amove_to")):         # مقصدها در «تنظیمات ← تغییر لوکیشن»
        on = shop.vpn.test_on(p) if key == "test" else pflag(p, key)
        rows.append([btn(PANEL_FLAG_TITLES[key], "adm:vpn:noop", role="back"), btn(onoff(on), f"adm:vpn:pft:{pid}:{key}", role="action")])
    days, gb, mx = shop.vpn.test_params(p)
    rows += [[btn("🎁 مدت تست (روز)", "adm:vpn:noop", role="back"), btn(f"{days}", f"adm:vpn:pe:{pid}:test_days", role="admin")],
             [btn("🎁 حجم تست (GB)", "adm:vpn:noop", role="back"), btn(f"{gb:g}" if gb else "نامحدود", f"adm:vpn:pe:{pid}:test_gb", role="admin")],
             [btn("🎁 تعداد تست هر کاربر", "adm:vpn:noop", role="back"), btn(f"{mx}", f"adm:vpn:pe:{pid}:test_max", role="admin")]]
    for k, lab in (("revoke_max", "🔑 سقف دفعات لینک جدید"), ("revoke_gap", "🔑 فاصله لینک جدید (دقیقه)"), ("toggle_max", "⏻ سقف دفعات خاموش/روشن"),
                   ("toggle_gap", "⏻ فاصله خاموش/روشن (دقیقه)"), ("addon_max", "➕ سقف دفعات خرید حجم/زمان اضافه"), ("move_max", "📍 سقف دفعات تغییر لوکیشن")):
        v = plimit(p, k)
        rows.append([btn(lab, "adm:vpn:noop", role="back"), btn(f"{v}" if v else "بدون محدودیت", f"adm:vpn:pe:{pid}:{k}", role="admin")])
    rows.append(back_row(f"adm:vpn:p:{pid}"))
    await view(cb, f"🧩 <b>قابلیت‌های پنل «{esc(p['name'])}»</b>\nبرای تغییر، روی وضعیت یا مقدار بزنید. تنظیمات اکانت تست این پنل هم همین‌جاست.", ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:pf:"))
async def cb_panel_features(cb: CallbackQuery, shop: Shop):
    await _panel_features(cb, shop, int(cb.data.split(":")[3]))
    await cb.answer()


async def _set_extra(shop: Shop, pid: int, **kv):
    p = await shop.db.get_vpn_panel(pid)
    ex = pextra(p)
    for k, v in kv.items():
        if v is None:
            ex.pop(k, None)
        else:
            ex[k] = v
    await shop.db.update_vpn_panel(pid, extra=json.dumps(ex, ensure_ascii=False))


@router.callback_query(F.data.startswith("adm:vpn:pft:"))
async def cb_panel_flag(cb: CallbackQuery, shop: Shop):
    _, _, _, pid, key = cb.data.split(":")
    p = await shop.db.get_vpn_panel(int(pid))
    if not p or key not in PANEL_FLAGS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    await _set_extra(shop, int(pid), **{key: 0 if (shop.vpn.test_on(p) if key == "test" else pflag(p, key)) else 1})
    await _panel_features(cb, shop, int(pid))
    await cb.answer("انجام شد")


# ---- مخفی کردن پنل برای کاربر
async def _hidden_view(cb: CallbackQuery, shop: Shop, pid: int):
    p = await shop.db.get_vpn_panel(pid)
    ids = pextra(p).get("hidden", [])
    rows = [[btn(f"❌ {uid}", f"adm:vpn:phr:{pid}:{uid}", role="reject")] for uid in ids[:40]]
    rows.append([btn("➕ مخفی کردن برای یک کاربر", f"adm:vpn:pha:{pid}", role="confirm")])
    rows.append(back_row(f"adm:vpn:p:{pid}"))
    await view(cb, f"🚫 <b>کاربرانی که این پنل را نمی‌بینند</b>\n{'برای برداشتن، روی آیدی بزنید.' if ids else 'هنوز کسی نیست.'}", ikb(rows))


@router.callback_query(F.data.regexp(r"^adm:vpn:ph:\d+$"))
async def cb_hidden(cb: CallbackQuery, shop: Shop):
    await _hidden_view(cb, shop, int(cb.data.split(":")[3]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:phr:"))
async def cb_hidden_remove(cb: CallbackQuery, shop: Shop):
    _, _, _, pid, uid = cb.data.split(":")
    p = await shop.db.get_vpn_panel(int(pid))
    ids = [x for x in pextra(p).get("hidden", []) if str(x) != uid]
    await _set_extra(shop, int(pid), hidden=ids or None)
    await _hidden_view(cb, shop, int(pid))
    await cb.answer("برداشته شد")


@router.callback_query(F.data.startswith("adm:vpn:pha:"))
async def cb_hidden_add(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[3])
    await state.set_state(AV.wiz)
    await state.update_data(flow="hide", pid=pid)
    await ans(cb.message, "آیدی عددی کاربر را بفرستید:", _ask_kb(f"adm:vpn:ph:{pid}"))
    await cb.answer()


# ---- وضعیت پنل
@router.callback_query(F.data.startswith("adm:vpn:ps:"))
async def cb_panel_status(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_vpn_panel(pid)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await cb.answer("در حال گرفتن وضعیت سرور...")
    ok, txt = await shop.vpn.panel_status_text(pid)
    load = await shop.db.panel_load(pid)
    extra = f"\n\n📡 سرویس‌های زنده‌ی ربات روی این پنل: {load}" + (f" از {p['capacity']}" if p["capacity"] else "")
    await ans(cb.message, (txt if ok else f"🖥 <b>وضعیت سرور «{esc(p['name'])}»</b>\n🔴 وصل نشد یا مانیتورینگ ندارد:\n<code>{esc(txt)}</code>") + extra)


@router.callback_query(F.data.startswith("adm:vpn:pt:"))
async def cb_panel_test(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_vpn_panel(pid)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await cb.answer("در حال تست...")
    ok, info = await shop.vpn.test_panel(p)
    if not ok:
        await ans(cb.message, f"❌ اتصال ناموفق بود:\n<code>{esc(str(info))}</code>")
        return
    opts = "\n".join(f"• <code>{esc(str(o['id']))}</code> {esc(str(o['name']))}" for o in info.get("options_list", [])[:25])
    await ans(cb.message, "✅ اتصال موفق بود" + (f" — نسخه <code>{esc(str(info['version']))}</code>" if info.get("version") else "") + f"\nگزینه‌ها: {info.get('options', 0)}"
              + (f"\n\n{opts}" if opts else ""))


@router.callback_query(F.data.startswith("adm:vpn:pdel:"))
async def cb_panel_del(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    parts = cb.data.split(":")
    if len(parts) == 4:
        await ans(cb.message, "⚠️ پنل و پلن‌هایش پاک می‌شوند. اگر سرویس دارد حذف نمی‌شود. مطمئنید؟",
                  ikb([[btn("✅ بله، حذف", f"adm:vpn:pdel:{pid}:y", role="reject"), btn("❌ خیر", f"adm:vpn:p:{pid}", role="back")]]))
        await cb.answer()
        return
    if await shop.db.delete_vpn_panel(pid):
        await shop.vpn.forget(pid)
        await shop.db.log(f"tg:{cb.from_user.id}", "vpn_panel_delete", f"#{pid}")
        await cb.answer("حذف شد")
        text, kb = await hub(shop)
        await ans(cb.message, text, kb)
    else:
        await cb.answer("این پنل سرویس دارد؛ به‌جای حذف، غیرفعالش کنید.", show_alert=True)


# ---- ویرایش یک فیلد پنل
EXTRA_FIELDS = {"test_days", "test_gb", "test_max", *LIMIT_KEYS}


@router.callback_query(F.data.startswith("adm:vpn:pe:"))
async def cb_panel_edit(cb: CallbackQuery, state: FSMContext):
    _, _, _, pid, field = cb.data.split(":")
    await state.set_state(AV.wiz)
    await state.update_data(flow="pedit", pid=int(pid), field=field)
    txt = FIELD_PROMPT[field] + ("\nبرای خالی کردن «-» بفرستید." if field in OPTIONAL else "")
    if field in ("password", "token"):
        txt += "\n🔒 پیام شما بعد از ذخیره پاک می‌شود."
    await ans(cb.message, txt, _ask_kb(f"adm:vpn:p:{pid}"))
    await cb.answer()


# ---- افزودن پنل (ویزارد)
@router.callback_query(F.data == "adm:vpn:pa")
async def cb_panel_add(cb: CallbackQuery):
    rows = [[btn(label, f"adm:vpn:pat:{t}", role="admin")] for t, label in vpn_panels.TYPES]
    rows += [[btn(f"⏳ {label}", f"adm:vpn:soon:{t}", role="back")] for t, label in vpn_panels.SOON]
    rows.append(back_row("adm:vpn:pl"))
    await view(cb, "🖧 <b>نوع پنل را انتخاب کنید</b>\nموارد ⏳ هنوز آماده نیستند.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:soon:"))
async def cb_panel_soon(cb: CallbackQuery):
    await cb.answer("این نوع پنل هنوز پشتیبانی نمی‌شود. برای فروش آن می‌توانید «فروش دستی» بسازید.", show_alert=True)


@router.callback_query(F.data.startswith("adm:vpn:pat:"))
async def cb_panel_type(cb: CallbackQuery, state: FSMContext):
    t = cb.data.split(":")[3]
    cls = vpn_panels.DRIVERS.get(t)
    if not cls:
        await cb.answer("نامعتبر", show_alert=True)
        return
    steps = ["name", *cls.NEEDS]
    if cls.OPTION_LABEL and "inbound_ids" not in steps:
        steps.append("inbound_ids")
    if "sub_url" not in steps and t != "manual":
        steps.append("sub_url")
    if t != "manual":
        steps.append("capacity")
    else:
        steps = ["name", "capacity"]
    await state.set_state(AV.wiz)
    await state.update_data(flow="padd", ptype=t, steps=steps, i=0, vals={"type": t})
    await ans(cb.message, f"ℹ️ {cls.HELP}" if cls.HELP else "شروع افزودن پنل")
    await _ask_step(cb.message, state)
    await cb.answer()


async def _ask_step(msg, state: FSMContext, shop: Shop | None = None):
    d = await state.get_data()
    key = d["steps"][d["i"]]
    extra = ""
    if key == "inbound_ids" and shop is not None:
        ok, info = await shop.vpn.test_panel(d["vals"])
        if ok and info.get("options_list"):
            extra = "\n\n✅ گزینه‌هایی که روی پنل پیدا شد:\n" + "\n".join(f"• <code>{esc(str(o['id']))}</code> {esc(str(o['name']))}" for o in info["options_list"][:25])
        elif ok and info.get("options_error"):
            extra = "\n\nℹ️ این حساب اجازه‌ی دیدن لیست اینباند/گروه را ندارد (برای ادمین محدود طبیعی است). مشکلی نیست: داخل پنل یک کانفیگ بسازید، اینباندهای دلخواه را به آن وصل کنید و فقط اسم همان کانفیگ را بفرستید."
        elif not ok:
            extra = f"\n\n⚠️ اتصال به پنل برقرار نشد (<code>{esc(str(info)[:150])}</code>). می‌توانید ادامه دهید و بعداً «تست اتصال» بزنید."
    cls = vpn_panels.DRIVERS[d["ptype"]]
    req = key in cls.NEEDS
    await ans(msg, f"<b>{d['i'] + 1}/{len(d['steps'])}</b> — " + FIELD_PROMPT[key] + extra
                     + ("" if req or key in ("name", "capacity") else ""), _ask_kb("adm:vpn:pl"))


def _conv(key, text):
    text = (text or "").strip()
    if key in OPTIONAL and text == "-":
        return ""
    if key in ("test_days", "test_gb", "test_max", *LIMIT_KEYS) and text == "-":
        return ""
    if key in ("capacity", "test_days", "test_gb", "test_max", *LIMIT_KEYS):
        t = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")).replace(",", "")
        try:
            v = float(t)
        except ValueError:
            raise ValueError("فقط عدد وارد کنید.") from None
        if v < 0:
            raise ValueError("عدد منفی مجاز نیست.")
        if key == "test_max" and v < 1:
            raise ValueError("حداقل ۱ باشد.")
        return int(v) if key != "test_gb" else v
    if not text:
        raise ValueError("مقدار خالی است.")
    if key == "url" and not text.startswith(("http://", "https://")):
        raise ValueError("آدرس باید با http:// یا https:// شروع شود.")
    if key == "inbound_ids":
        return text.replace("،", ",").replace(" ", "")
    return text


async def _safe_delete(m: Message):
    try:
        await m.delete()
    except Exception:  # noqa: BLE001
        pass


@router.message(AV.wiz, F.text == "/all")
@router.message(AV.wiz, TXT)
async def wiz_text(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    flow = d.get("flow")
    if flow == "stock_add":
        await _stock_ingest(m, shop, d, m.text)
        return
    if flow == "stock_edit":
        await _stock_edit_save(m, shop, state, d)
        return
    if flow == "padd":
        key = d["steps"][d["i"]]
        try:
            val = _conv(key, m.text)
        except ValueError as e:
            await ans(m, f"❌ {e}")
            return
        if key in ("password", "token"):
            await _safe_delete(m)
        vals = {**d["vals"], key: val}
        i = d["i"] + 1
        if i >= len(d["steps"]):
            await state.clear()
            pid = await shop.db.add_vpn_panel(**vals)
            await shop.db.log(f"tg:{m.from_user.id}", "vpn_panel_add", f"#{pid} {vals['type']}")
            kb = ikb([[btn("🔌 تست اتصال", f"adm:vpn:pt:{pid}", role="action"), btn("📦 افزودن پلن", "adm:vpn:pna", role="confirm")], back_row("adm:vpn:pl")])
            await ans(m, f"✅ پنل «{esc(vals['name'])}» ثبت شد. حتماً «تست اتصال» بزنید.", kb)
            return
        await state.update_data(vals=vals, i=i)
        await _ask_step(m, state, shop)
        return
    if flow == "pedit":
        field = d["field"]
        try:
            val = _conv(field, m.text)
        except ValueError as e:
            await ans(m, f"❌ {e}")
            return
        if field in ("password", "token"):
            await _safe_delete(m)
        await state.clear()
        if field in EXTRA_FIELDS:
            await _set_extra(shop, d["pid"], **{field: val if val != "" else None})
        else:
            await shop.db.update_vpn_panel(d["pid"], **{field: val})
        await shop.vpn.forget(d["pid"])
        await ans(m, "✅ ذخیره شد.", ikb([back_row(f"adm:vpn:p:{d['pid']}")]))
        return
    if flow == "np":
        await _np_step(m, shop, state, d)
        return
    if flow == "uact":
        await _uact_step(m, shop, state, d)
        return
    if flow == "gprice":
        try:
            pct = float(m.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")))
            if pct <= 0 or pct >= 1000 or (not d["up"] and pct >= 100):
                raise ValueError
        except ValueError:
            await ans(m, "❌ یک عدد درست بفرستید (برای کاهش کمتر از ۱۰۰).")
            return
        await state.clear()
        k = 1 + pct / 100 if d["up"] else 1 - pct / 100
        plans = await shop.db.list_vpn_plans()
        for p in plans:
            await shop.db.update_vpn_plan(p["id"], price=max(int(round(p["price"] * k / 100.0)) * 100, 0))
        await shop.db.log(f"tg:{m.from_user.id}", "vpn_group_price", f"{'+' if d['up'] else '-'}{pct}%")
        await ans(m, f"✅ قیمت {len(plans)} محصول {'بیشتر' if d['up'] else 'کمتر'} شد (گرد‌شده به ۱۰۰ تومان).", ikb([back_row("adm:vpn:pn")]))
        return
    if flow == "setting":
        raw = "" if m.text.strip() == "-" else m.text.strip()
        ok, res = await shop.set_setting_validated(d["key"], raw, f"tg:{m.from_user.id}")
        if not ok:
            await ans(m, f"❌ {esc(res)}")
            return
        await state.clear()
        back, _ = await _back_of(d["origin"])
        await ans(m, "✅ ذخیره شد.", ikb([back_row(back)]))
        return
    if flow == "hide":
        t = m.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not t.isdigit():
            await ans(m, "❌ فقط آیدی عددی بفرستید.")
            return
        await state.clear()
        p = await shop.db.get_vpn_panel(d["pid"])
        ids = list(pextra(p).get("hidden", []))
        if int(t) not in ids:
            ids.append(int(t))
        await _set_extra(shop, d["pid"], hidden=ids)
        await ans(m, "✅ از حالا این پنل برای او نمایش داده نمی‌شود.", ikb([back_row(f"adm:vpn:ph:{d['pid']}")]))
        return
    if flow in ("plan", "addon", "pedit2"):
        await _wiz_money(m, shop, state, d)
        return
    if flow == "find":
        await state.clear()
        await _services(m, shop, "all", 0, q=m.text.strip())
        return
    if flow == "svcval":
        await _svc_value(m, shop, state, d)
        return
    if flow == "bkval":
        await _bk_value(m, shop, state, d)
        return
    if flow == "bkq":
        bk = d.get("bk") or {}
        q = (m.text or "").strip()
        bk["q"] = "" if q == "-" else q[:40]
        await state.set_state(None)
        await state.update_data(bk=bk, flow=None)
        await _bk_picker(m, shop, state, 0)
        return
    if flow == "deliver":
        await _deliver_step(m, shop, state, d)
        return
    if flow == "newuser":
        await _newuser_step(m, shop, state, d)
        return


# ============================================================ پلن‌ها
@router.callback_query(F.data == "adm:vpn:pn")
async def cb_plans(cb: CallbackQuery, shop: Shop, state: FSMContext = None):
    if state:
        await state.clear()
    n = len(await shop.db.list_vpn_plans())
    rows = [[btn("🛍 اضافه کردن محصول", "adm:vpn:pna", role="confirm"), btn("❌ حذف محصول", "adm:vpn:pnd", role="reject")],
            [btn("✏️ ویرایش محصول", "adm:vpn:pne", role="admin")],
            [btn("⬆️ افزایش گروهی قیمت", "adm:vpn:pgi", role="admin"), btn("⬇️ کاهش گروهی قیمت", "adm:vpn:pgd", role="admin")],
            back_row("adm:vpn", "🔙 بازگشت به منوی فروش")]
    await view(cb, f"🛍 <b>مدیریت محصولات</b> — {n} محصول\nیک گزینه را انتخاب کنید.", ikb(rows))
    await cb.answer()


async def _plan_list(cb: CallbackQuery, shop: Shop, mode):
    plans = await shop.db.list_vpn_plans()
    pre = "adm:vpn:n:" if mode == "e" else "adm:vpn:ndel:"
    rows = [[btn(f"{'🟢' if p['active'] else '⚫️'} {p['title']} · {p['panel_name']} · {fmt(p['price'])} · {p.get('utype') or 'f'}", f"{pre}{p['id']}", role="admin" if mode == "e" else "reject")] for p in plans[:40]]
    rows.append(back_row("adm:vpn:pn", "▶️ بازگشت به منوی قبل"))
    await view(cb, "✏️ کدام محصول را ویرایش می‌کنید؟" if mode == "e" else "❌ کدام محصول حذف شود؟" if plans else "هنوز محصولی ندارید.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:pne")
async def cb_plans_edit(cb: CallbackQuery, shop: Shop):
    await _plan_list(cb, shop, "e")


@router.callback_query(F.data == "adm:vpn:pnd")
async def cb_plans_del(cb: CallbackQuery, shop: Shop):
    await _plan_list(cb, shop, "d")


@router.callback_query(F.data.in_({"adm:vpn:pgi", "adm:vpn:pgd"}))
async def cb_group_price(cb: CallbackQuery, state: FSMContext):
    up = cb.data.endswith("pgi")
    await state.set_state(AV.wiz)
    await state.update_data(flow="gprice", up=up)
    await ans(cb.message, f"قیمت همه‌ی محصولات چند درصد {'بیشتر' if up else 'کمتر'} شود؟ (فقط عدد، مثلاً 10)", _ask_kb("adm:vpn:pn"))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:n:\d+$"))
async def cb_plan(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = next((x for x in await shop.db.list_vpn_plans() if x["id"] == pid), None)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    txt = (f"📦 <b>{esc(p['title'])}</b> — {'✅ فعال' if p['active'] else '⚫️ غیرفعال'}\nپنل: {esc(p['panel_name'])}\n"
           f"مدت: {p['days'] or '∞'} روز | حجم: {p['gb']:g} GB {'(نامحدود)' if not p['gb'] else ''} | محدودیت IP: {p['ip_limit'] or '∞'}\n"
           f"قیمت: {fmt(p['price'])} تومان\n"
           f"نوع کاربر: <code>{p.get('utype') or 'f'}</code>" + (f"\n📝 {esc(p['note'])}" if p.get("note") else ""))
    rows = [[btn("⏯ فعال/غیرفعال", f"adm:vpn:ntg:{pid}", role="action"), btn("🗑 حذف", f"adm:vpn:ndel:{pid}", role="reject")],
            [btn("✏️ قیمت", f"adm:vpn:ne:{pid}:price", role="admin")],
            [btn("✏️ روز", f"adm:vpn:ne:{pid}:days", role="admin"), btn("✏️ حجم", f"adm:vpn:ne:{pid}:gb", role="admin")],
            [btn("✏️ عنوان", f"adm:vpn:ne:{pid}:title", role="admin"), btn("✏️ محدودیت IP", f"adm:vpn:ne:{pid}:ip_limit", role="admin")],
            [btn("✏️ نوع کاربر", f"adm:vpn:ne:{pid}:utype", role="admin"), btn("📝 یادداشت", f"adm:vpn:ne:{pid}:note", role="admin")],
            [btn("🎨 رنگ و ایموجی دکمه‌ها", f"adm:vpn:pb:v:{pid}", role="admin")],
            back_row("adm:vpn:pne", "▶️ بازگشت به منوی قبل")]
    await view(cb, txt, ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:ntg:"))
async def cb_plan_toggle(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_vpn_plan(pid)
    if p:
        await shop.db.update_vpn_plan(pid, active=0 if p["active"] else 1)
    await cb_plan(cb, shop)


@router.callback_query(F.data.startswith("adm:vpn:ndel:"))
async def cb_plan_del(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    await shop.db.delete_vpn_plan(pid)
    await shop.db.log(f"tg:{cb.from_user.id}", "vpn_plan_delete", f"#{pid}")
    await cb.answer("حذف شد")
    await _plan_list(cb, shop, "d")


PLAN_STEPS = [("title", "اسم پلن را بنویسید (مثلاً «۳۰ روزه ۵۰ گیگ»):", "text"), ("days", "چند روز؟ (۰ یعنی بدون انقضا):", "int"),
              ("gb", "چند گیگ؟ (۰ یعنی نامحدود):", "float"), ("price", "قیمت فروش به تومان:", "int"),
              ("ip_limit", "حداکثر چند نفر همزمان؟ (۰ یعنی نامحدود):", "int")]
ADDON_STEPS = [("title", "اسم بسته را بنویسید (مثلاً «۱۰ گیگ اضافه»):", "text"), ("gb", "چند گیگ اضافه شود؟ (اگر فقط زمان است ۰):", "float"),
               ("days", "چند روز اضافه شود؟ (اگر فقط حجم است ۰):", "int"), ("price", "قیمت به تومان:", "int")]
KIND_OF = {k: t for k, _, t in PLAN_STEPS + ADDON_STEPS} | {"utype": "utype", "note": "note"}
PROMPT_OF = {k: p for k, p, _ in PLAN_STEPS + ADDON_STEPS} | {"utype": "نوع کاربر را بفرستید: f (عادی)، n (نماینده نوع ۲) یا n2 (نماینده نوع ۳):",
                                                               "note": "یادداشت محصول را بفرستید (در پیش‌فاکتور کاربر نشان داده می‌شود). برای پاک کردن «-» بفرستید:"}


def _num(kind, text):
    t = (text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬", "0123456789.,")).replace(",", "")
    if kind == "utype":
        if t.lower() not in ("f", "n", "n2"):
            raise ValueError("فقط f یا n یا n2.")
        return t.lower()
    if kind == "note":
        return "" if t == "-" else (text or "").strip()[:300]
    if kind == "text":
        if not t:
            raise ValueError("متن خالی است.")
        return (text or "").strip()[:80]
    try:
        v = float(t) if kind == "float" else int(t)
    except ValueError:
        raise ValueError("فقط عدد وارد کنید.") from None
    if v < 0:
        raise ValueError("عدد منفی مجاز نیست.")
    return v


# ---- افزودن محصول (مرحله‌به‌مرحله و با همان متن‌های میرزا)
NP_BACK_MENU, NP_BACK_PREV = "🏠 بازگشت به منوی مدیریت", "▶️ بازگشت به منوی قبل"
NP_TEXT = {
    "title": "ابتدا نام اشتراک خود را ارسال نمایید\n⚠️ نکات هنگام وارد کردن نام محصول:\n• در کنار نام اشتراک حتما قیمت اشتراک را هم وارد کنید.\n• در کنار نام اشتراک حتما زمان اشتراک را هم وارد کنید.",
    "utype": "محصول برای چه کاربری نشان داده شود\nنوع اول <code>f</code> : کاربر عادی می باشد\nنوع دوم <code>n</code> : نماینده نوع دوم می باشد با قابلیت های محدود\nنوع سوم <code>n2</code> : نماینده نوع سوم می باشد که قابلیت های بیشتر",
    "panel": "📌 موقعیت محصول خود را انتخاب کنید\n\n⭕ برای تعریف محصول در همه موقعیت ها دستور /all را ارسال کنید",
    "gb": "حجم اشتراک را ارسال کنید توجه واحد حجم گیگابایت است\n\nاگر میخواهید حجم نامحدود باشد عدد 0 ارسال کنید",
    "days": "زمان اشتراک را وارد نمایید توجه واحد زمان اشتراک روز است\nاگر می‌خواهید زمان نامحدود باشد عدد 0 را ارسال کنید",
    "price": "قیمت اشتراک را ارسال کنید.\nتوجه:\nمحصول براساس تومان است و قیمت را بدون هیچ کاراکتر اضافی ارسال نمایید.",
    "note": "📝 یادداشت را برای محصول ارسال کنید. این یادداشت در پیش فاکتور کاربر نشان داده می شود.",
}
NP_ORDER = ["title", "utype", "panel", "gb", "days", "price", "note"]


def _np_kb(step, panels=()):
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    rows = []
    if step == "utype":
        rows = [[KeyboardButton(text="f"), KeyboardButton(text="n"), KeyboardButton(text="n2")]]
    elif step == "panel":
        names = [p["name"] for p in panels]
        rows = [[KeyboardButton(text=n) for n in names[i:i + 2]] for i in range(0, len(names), 2)] + [[KeyboardButton(text="/all")]]
    rows.append([KeyboardButton(text=NP_BACK_PREV), KeyboardButton(text=NP_BACK_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def _np_ask(msg, shop: Shop, state: FSMContext, step):
    panels = await shop.db.list_vpn_panels() if step == "panel" else ()
    await state.update_data(flow="np", step=step)
    await ans(msg, NP_TEXT[step], _np_kb(step, panels))


@router.callback_query(F.data == "adm:vpn:pna")
async def cb_plan_add(cb: CallbackQuery, shop: Shop, state: FSMContext):
    if not await shop.db.list_vpn_panels():
        await cb.answer("ابتدا یک پنل اضافه کنید.", show_alert=True)
        return
    await state.set_state(AV.wiz)
    await state.update_data(vals={})
    await _np_ask(cb.message, shop, state, "title")
    await cb.answer()


async def _np_step(m: Message, shop: Shop, state: FSMContext, d):
    from aiogram.types import ReplyKeyboardRemove
    t = (m.text or "").strip()
    step, vals = d["step"], dict(d.get("vals") or {})
    if t in (NP_BACK_MENU, NP_BACK_PREV):
        await state.clear()
        await m.answer("↩️", reply_markup=ReplyKeyboardRemove())
        if t == NP_BACK_MENU:
            text, kb = await hub(shop)
            await ans(m, text, kb)
        else:
            await ans(m, "🛍 یک گزینه را انتخاب کنید.", ikb([[btn("🛍 اضافه کردن محصول", "adm:vpn:pna", role="confirm")], back_row("adm:vpn:pn")]))
        return
    try:
        if step == "title":
            if not t:
                raise ValueError("متن خالی است.")
            vals["title"] = t[:80]
        elif step == "utype":
            if t.lower() not in ("f", "n", "n2"):
                raise ValueError("فقط یکی از f یا n یا n2 را بفرستید.")
            vals["utype"] = t.lower()
        elif step == "panel":
            panels = await shop.db.list_vpn_panels()
            if t == "/all":
                vals["panels"] = [p["id"] for p in panels]
            else:
                hit = next((p for p in panels if p["name"] == t), None)
                if not hit:
                    raise ValueError("از دکمه‌های پایین یکی را انتخاب کنید.")
                vals["panels"] = [hit["id"]]
        elif step in ("gb", "days", "price"):
            vals[step] = _num("float" if step == "gb" else "int", t)
            if step == "price" and not vals[step]:
                raise ValueError("قیمت باید بیشتر از صفر باشد.")
        elif step == "note":
            vals["note"] = "" if t in ("0", "-") else t[:300]
    except ValueError as e:
        await ans(m, f"❌ {e}")
        return
    nxt = NP_ORDER.index(step) + 1
    if nxt < len(NP_ORDER):
        await state.update_data(vals=vals)
        await _np_ask(m, shop, state, NP_ORDER[nxt])
        return
    await state.clear()
    for pid in vals["panels"]:
        await shop.db.add_vpn_plan(title=vals["title"], panel_id=pid, days=vals["days"], gb=vals["gb"], price=vals["price"], utype=vals["utype"], note=vals["note"])
    await shop.db.log(f"tg:{m.from_user.id}", "vpn_plan_add", f"{vals['title']} x{len(vals['panels'])}")
    await m.answer("🎉😋 محصول با موفقیت ذخیره شد", reply_markup=ReplyKeyboardRemove())
    await ans(m, "🛍 یک گزینه را انتخاب کنید.", ikb([[btn("🛍 اضافه کردن محصول", "adm:vpn:pna", role="confirm")], back_row("adm:vpn:pn", "🔙 بازگشت به منوی محصولات")]))


@router.callback_query(F.data.startswith("adm:vpn:ne:"))
async def cb_plan_edit(cb: CallbackQuery, state: FSMContext):
    _, _, _, pid, field = cb.data.split(":")
    await state.set_state(AV.wiz)
    await state.update_data(flow="pedit2", obj="plan", oid=int(pid), field=field)
    await ans(cb.message, PROMPT_OF[field], _ask_kb(f"adm:vpn:n:{pid}"))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:ada")
async def cb_addon_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="addon", steps=[s[0] for s in ADDON_STEPS], i=0, vals={})
    await ans(cb.message, f"<b>1/{len(ADDON_STEPS)}</b> — {ADDON_STEPS[0][1]}", _ask_kb("adm:vpn:ad"))
    await cb.answer()


async def _wiz_money(m: Message, shop: Shop, state: FSMContext, d):
    flow = d["flow"]
    if flow == "pedit2":
        field = d["field"]
        try:
            v = _num(KIND_OF[field], m.text)
        except ValueError as e:
            await ans(m, f"❌ {e}")
            return
        await state.clear()
        if d["obj"] == "plan":
            await shop.db.update_vpn_plan(d["oid"], **{field: v})
            back = f"adm:vpn:n:{d['oid']}"
        else:
            await shop.db.update_vpn_addon(d["oid"], **{field: v})
            back = f"adm:vpn:ae:{d['oid']}"
        await ans(m, "✅ ذخیره شد.", ikb([back_row(back)]))
        return
    steps = d["steps"]
    key = steps[d["i"]]
    try:
        v = _num(KIND_OF[key], m.text)
    except ValueError as e:
        await ans(m, f"❌ {e}")
        return
    vals = {**d["vals"], key: v}
    i = d["i"] + 1
    if i >= len(steps):
        await state.clear()
        if flow == "plan":
            pid = await shop.db.add_vpn_plan(**vals)
            await ans(m, f"✅ پلن «{esc(vals['title'])}» ثبت شد.", ikb([[btn("📦 مشاهده", f"adm:vpn:n:{pid}", role="admin")], back_row("adm:vpn:pn")]))
        else:
            if not vals.get("gb") and not vals.get("days"):
                await ans(m, "❌ حجم یا زمان باید بیشتر از صفر باشد؛ دوباره از «افزودن بسته» شروع کنید.", ikb([back_row("adm:vpn:ad")]))
                return
            aid = await shop.db.add_vpn_addon(**vals)
            await ans(m, f"✅ بسته «{esc(vals['title'])}» ثبت شد.", ikb([[btn("➕ مشاهده", f"adm:vpn:ae:{aid}", role="admin")], back_row("adm:vpn:ad")]))
        return
    await state.update_data(vals=vals, i=i)
    await ans(m, f"<b>{i + 1}/{len(steps)}</b> — {PROMPT_OF[steps[i]]}", _ask_kb("adm:vpn:pn" if flow == "plan" else "adm:vpn:ad"))


# ============================================================ بسته‌های افزودنی
@router.callback_query(F.data == "adm:vpn:ad")
async def cb_addons(cb: CallbackQuery, shop: Shop):
    rows = [[btn(f"{'🟢' if a['active'] else '⚫️'} {a['title']} · {fmt(a['price'])}", f"adm:vpn:ae:{a['id']}", role="admin")] for a in await shop.db.list_vpn_addons()]
    rows.append([btn("➕ بسته‌ی جدید", "adm:vpn:ada", role="confirm")])
    rows.append(back_row("adm:vpn:xs:u"))
    await view(cb, "➕ <b>بسته‌های افزودنی</b>\nکاربر با این بسته‌ها به سرویسش حجم یا زمان اضافه می‌کند.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:ae:\d+$"))
async def cb_addon(cb: CallbackQuery, shop: Shop):
    aid = int(cb.data.split(":")[3])
    a = await shop.db.get_vpn_addon(aid)
    if not a:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    rows = [[btn("⏯ فعال/غیرفعال", f"adm:vpn:atg:{aid}", role="action"), btn("🗑 حذف", f"adm:vpn:adel:{aid}", role="reject")],
            [btn("✏️ قیمت", f"adm:vpn:aq:{aid}:price", role="admin")],
            back_row("adm:vpn:ad")]
    await view(cb, f"➕ <b>{esc(a['title'])}</b> — {'✅ فعال' if a['active'] else '⚫️ غیرفعال'}\nحجم: {a['gb']:g} GB | زمان: {a['days']} روز\nقیمت: {fmt(a['price'])} تومان", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:aq:"))
async def cb_addon_edit(cb: CallbackQuery, state: FSMContext):
    _, _, _, aid, field = cb.data.split(":")
    await state.set_state(AV.wiz)
    await state.update_data(flow="pedit2", obj="addon", oid=int(aid), field=field)
    await ans(cb.message, PROMPT_OF[field], _ask_kb(f"adm:vpn:ae:{aid}"))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:atg:"))
async def cb_addon_toggle(cb: CallbackQuery, shop: Shop):
    aid = int(cb.data.split(":")[3])
    a = await shop.db.get_vpn_addon(aid)
    if a:
        await shop.db.update_vpn_addon(aid, active=0 if a["active"] else 1)
    await cb.answer("انجام شد")
    await cb_addons(cb, shop)


@router.callback_query(F.data.startswith("adm:vpn:adel:"))
async def cb_addon_del(cb: CallbackQuery, shop: Shop):
    await shop.db.delete_vpn_addon(int(cb.data.split(":")[3]))
    await cb.answer("حذف شد")
    await cb_addons(cb, shop)


# ============================================================ سرویس‌ها
async def _services(target, shop: Shop, status: str, page: int, q=""):
    st = None if status == "all" else status
    total = await shop.db.count_vpn_services(status=st, q=q)
    rows_db = await shop.db.list_vpn_services(status=st, q=q, limit=PER, offset=page * PER)
    rows = [[btn(f"{tr_st(s['status'])[:2]} {s['name']} · {s['user_id']}" + (" 🎁" if s["is_test"] else ""), f"adm:vpn:s:{s['id']}", role="admin")] for s in rows_db]
    if not q:
        nav = []
        if page > 0:
            nav.append(btn("◀️", f"adm:vpn:sv:{status}:{page - 1}", role="back"))
        if (page + 1) * PER < total:
            nav.append(btn("▶️", f"adm:vpn:sv:{status}:{page + 1}", role="action"))
        if nav:
            rows.append(nav)
        rows.append([btn("فعال", "adm:vpn:sv:active:0", role="action"), btn("منقضی", "adm:vpn:sv:expired:0", role="action"), btn("همه", "adm:vpn:sv:all:0", role="action")])
    rows.append(back_row("adm:vpn"))
    await view(target, f"📡 <b>سرویس‌های VPN</b> — {total} مورد" + (f"\nجستجو: <code>{esc(q)}</code>" if q else ""), ikb(rows))
    if isinstance(target, CallbackQuery):
        await target.answer()


def tr_st(s: str) -> str:
    return STATUS_FA.get(s, s)


@router.callback_query(F.data.startswith("adm:vpn:sv:"))
async def cb_services(cb: CallbackQuery, shop: Shop):
    _, _, _, status, page = cb.data.split(":")
    await _services(cb, shop, status, int(page))


@router.callback_query(F.data == "adm:vpn:find")
async def cb_find(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="find")
    await ans(cb.message, "آیدی عددی کاربر، یوزرنیم او یا اسم سرویس را بفرستید:", _ask_kb())
    await cb.answer()


async def svc_card(shop: Shop, svc):
    u = await shop.db.get_user(svc["user_id"])
    left = days_left(svc["expires_at"])
    txt = (f"📡 <b>{esc(svc['name'])}</b> (#{svc['id']}){' 🎁 تست' if svc['is_test'] else ''}\n"
           f"وضعیت: {tr_st(svc['status'])}\nپنل: {esc(svc.get('panel_name') or '-')} | پلن: {esc(svc['title'] or '-')}\n"
           f"کاربر: <code>{svc['user_id']}</code>" + (f" @{esc(u['username'])}" if u and u.get("username") else "") + "\n"
           f"مصرف: {fmt_gb(svc['used_bytes'])} / {fmt_gb(svc['total_bytes']) if svc['total_bytes'] else 'نامحدود'}  {bar(svc['used_bytes'], svc['total_bytes'])}\n"
           f"زمان باقی‌مانده: {'نامحدود' if left is None else f'{left} روز'}\nلینک: <code>{esc(svc['sub_url'] or '-')}</code>"
           + (f"\n📝 {esc(svc['note'])}" if svc["note"] else "") + f"\nآخرین همگام‌سازی: {time.strftime('%m-%d %H:%M', time.localtime(svc['last_sync'])) if svc['last_sync'] else '-'}")
    sid = svc["id"]
    if svc["status"] == "pending_manual":
        rows = [[btn("📝 ثبت کانفیگ و ارسال برای کاربر", f"adm:vpn:sd:{sid}", role="confirm")],
                [btn("🗑 حذف", f"adm:vpn:sa:{sid}:delete", role="reject"), btn("💸 حذف + بازگشت وجه", f"adm:vpn:sa:{sid}:refund", role="reject")]]
    else:
        rows = [[btn("➕ روز", f"adm:vpn:sv2:{sid}:add_days", role="admin"), btn("➕ حجم (GB)", f"adm:vpn:sv2:{sid}:add_gb", role="admin")],
                [btn("♻️ ریست مصرف", f"adm:vpn:sa:{sid}:reset", role="action"), btn("🔄 همگام‌سازی", f"adm:vpn:sa:{sid}:sync", role="action")],
                [btn("▶️ فعال" if svc["status"] == "disabled" else "⏸ غیرفعال", f"adm:vpn:sa:{sid}:{'enable' if svc['status'] == 'disabled' else 'disable'}", role="action"),
                 btn("🔑 لینک جدید", f"adm:vpn:sa:{sid}:revoke", role="action")],
                [btn("📤 ارسال مجدد برای کاربر", f"adm:vpn:sa:{sid}:resend", role="action"), btn("📝 یادداشت", f"adm:vpn:sv2:{sid}:note", role="admin")],
                [btn("📍 انتقال به پنل دیگر", f"adm:vpn:smv:{sid}", role="admin")] + (
                    [btn("↩️ بازگشت به لوکیشن قبلی", f"adm:vpn:srv:{sid}", role="action")] if await _can_revert(shop, svc) else []),
                [btn("🗑 حذف", f"adm:vpn:sa:{sid}:delete", role="reject"), btn("💸 حذف + بازگشت وجه", f"adm:vpn:sa:{sid}:refund", role="reject")]]
    rows.append(back_row("adm:vpn:sv:all:0"))
    return txt, ikb(rows)


def _jd(ts) -> str:
    from handlers_admin import jdate
    return jdate(ts)


async def _can_revert(shop: Shop, svc) -> bool:
    last = await shop.db.vm_last("svc", svc["id"])
    return bool(last and last["to_panel"] == svc["panel_id"])


@router.callback_query(F.data.regexp(r"^adm:vpn:srv:\d+(:y)?$"))
async def cb_svc_revert(cb: CallbackQuery, shop: Shop):
    from vpn_ops import MoveError
    parts = cb.data.split(":")
    sid = int(parts[3])
    last = await shop.db.vm_last("svc", sid)
    if not last:
        await cb.answer("انتقال ثبت‌شده‌ای برای برگرداندن نیست.", show_alert=True)
        return
    if len(parts) == 4:
        pn = await shop.db.get_vpn_panel(last["from_panel"])
        await view(cb, f"↩️ سرویس روی «{esc(pn['name']) if pn else last['from_panel']}» با حجم و زمانِ باقی‌مانده‌ی فعلی دوباره ساخته می‌شود، از پنل فعلی پاک می‌شود و لینک جدید برای کاربر ارسال می‌شود. ادامه؟",
                   ikb([[btn("✅ بله، برگردان", f"adm:vpn:srv:{sid}:y", role="confirm"), btn("❌ خیر", f"adm:vpn:s:{sid}", role="back")]]))
        await cb.answer()
        return
    await cb.answer("در حال برگرداندن ...")
    try:
        row = await shop.vpn.ops.revert(last["id"], actor=f"tg:{cb.from_user.id}")
    except MoveError as e:
        await ans(cb.message, f"❌ {esc(str(e))}", ikb([back_row(f"adm:vpn:s:{sid}")]))
        return
    await shop.vpn.ops.notify_moved(row, back=True)
    text, kb = await svc_card(shop, row)
    await view(cb, "✅ برگشت داده شد و لینک برای کاربر ارسال شد.\n\n" + text, kb)


# ---- بازگشت جابه‌جایی‌های دسته‌ای
@router.callback_query(F.data == "adm:vpn:mvb")
async def cb_batches(cb: CallbackQuery, shop: Shop):
    bs = await shop.db.vm_batches("svc", 15)
    pn = await _bk_panels(shop)
    nm = lambda i: (pn.get(i) or {}).get("name", f"#{i}")  # noqa: E731
    rows = [[btn(f"{_jd(b['at'])} · {b['n_done']}/{b['n']} · {nm(b['from_panel'])} ➜ {nm(b['to_panel'])}", f"adm:vpn:mvb:{b['batch']}", role="admin")] for b in bs]
    rows.append(back_row("adm:vpn:mvs"))
    await view(cb, "↩️ <b>بازگشت جابه‌جایی‌ها</b>\n" + ("هر مورد یک بار «انتقال» (تکی یا گروهی) است؛ بزنید تا همه‌ی سرویس‌های آن دسته به لوکیشن قبلی برگردند." if bs else "هنوز انتقالی ثبت نشده است."), ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:mvb:[A-Za-z0-9_.:-]+$"))
async def cb_batch(cb: CallbackQuery, shop: Shop):
    batch = cb.data.split(":", 3)[3]
    run_it = batch.endswith(":y")
    batch = batch[:-2] if run_it else batch
    rows_db = await shop.db.vm_batch_rows(batch)
    n_done = sum(1 for r in rows_db if r["status"] == "done")
    if not rows_db:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    if not run_it:
        pn = await _bk_panels(shop)
        nm = lambda i: (pn.get(i) or {}).get("name", f"#{i}")  # noqa: E731
        r0 = rows_db[0]
        kb = [[btn(f"↩️ برگردان همه ({n_done})", f"adm:vpn:mvb:{batch}:y", role="confirm")]] if n_done else []
        await view(cb, f"↩️ <b>جابه‌جایی {_jd(r0['at'])}</b>\n{nm(r0['from_panel'])} ➜ {nm(r0['to_panel'])}\nکل: {len(rows_db)} | هنوز روی مقصد: <b>{n_done}</b> | برگردانده‌شده: {len(rows_db) - n_done}\n\n"
                         "با «برگردان»، هر سرویس روی پنل قبلی با <b>حجم و زمانِ باقی‌مانده‌ی همین لحظه</b> دوباره ساخته، از پنل مقصد پاک و لینک برای کاربرش ارسال می‌شود.",
                   ikb(kb + [back_row("adm:vpn:mvb")]))
        await cb.answer()
        return
    uid = cb.from_user.id
    if shop.vpn.ops.jobs.get(uid, {}).get("running"):
        await cb.answer("یک عملیات گروهی دیگر از شما در حال اجراست.", show_alert=True)
        return
    job = shop.vpn.ops.jobs[uid] = {"running": True}
    chat = cb.message.chat.id

    async def run():
        try:
            res = await shop.vpn.ops.revert_batch(batch, actor=f"tg:{uid}")
        finally:
            job["running"] = False
        err = ("\n\n" + "\n".join(esc(x) for x in res["errors"][:20])) if res["errors"] else ""
        await shop.safe_send(chat, f"🏁 <b>بازگشت جابه‌جایی</b>\n✅ موفق: {res['ok']}\n❌ ناموفق: {res['fail']}{err}", reply_markup=ikb([back_row("adm:vpn:mvb")]))
        await shop.audit(f"tg:{uid}", f"↩️ بازگشت جابه‌جایی دسته‌ای {batch}: موفق {res['ok']}، ناموفق {res['fail']}")

    shop.spawn(run())
    await shop.db.log(f"tg:{uid}", "vpn_revert_batch", f"{batch} n={n_done}")
    await ans(cb.message, f"🚀 بازگشت شروع شد ({fmt(n_done)} سرویس)؛ وقتی تمام شد گزارش می‌دهم.", ikb([back_row("adm:vpn:mvb")]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:s:\d+$"))
async def cb_service(cb: CallbackQuery, shop: Shop):
    svc = await shop.db.get_vpn_service(int(cb.data.split(":")[3]))
    if not svc:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    if svc["status"] not in ("deleted", "pending_manual"):         # آمار لحظه‌ای از پنل
        svc = await shop.vpn.sync_safe(svc, min_gap=3)
        svc = await shop.db.get_vpn_service(svc["id"]) or svc
    text, kb = await svc_card(shop, svc)
    await view(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:sa:"))
async def cb_svc_action(cb: CallbackQuery, shop: Shop):
    _, _, _, sid, act = cb.data.split(":")[:5]
    sid = int(sid)
    actor = f"tg:{cb.from_user.id}"
    if act == "resend":
        svc = await shop.db.get_vpn_service(sid)
        if svc:
            await shop.vpn.send_service(svc["user_id"], svc)
        await cb.answer("ارسال شد")
        return
    if act in ("delete", "refund") and not cb.data.endswith(":y"):
        await ans(cb.message, "⚠️ سرویس از پنل VPN هم حذف می‌شود" + (" و مبلغ سفارش به کیف پول کاربر برمی‌گردد" if act == "refund" else "") + ". مطمئنید؟",
                                ikb([[btn("✅ بله", f"adm:vpn:sa:{sid}:{act}:y", role="reject"), btn("❌ خیر", f"adm:vpn:s:{sid}", role="back")]]))
        await cb.answer()
        return
    if act in ("delete", "refund"):
        ok, msg = await shop.vpn.admin_delete(sid, refund=(act == "refund"), actor=actor)
        await cb.answer(msg, show_alert=not ok)
        if ok:
            await _services(cb, shop, "all", 0)
        return
    ok, msg = await shop.vpn.admin_action(sid, act, actor=actor)
    await cb.answer(msg, show_alert=not ok)
    svc = await shop.db.get_vpn_service(sid)
    if ok and svc:
        text, kb = await svc_card(shop, svc)
        await view(cb, text, kb)


SV_PROMPT = {"add_days": "چند روز اضافه شود؟", "add_gb": "چند گیگ اضافه شود؟", "note": "یادداشت را بنویسید (برای پاک کردن «-» بفرستید):"}


@router.callback_query(F.data.startswith("adm:vpn:sv2:"))
async def cb_svc_prompt(cb: CallbackQuery, state: FSMContext):
    _, _, _, sid, act = cb.data.split(":")
    await state.set_state(AV.wiz)
    await state.update_data(flow="svcval", sid=int(sid), act=act)
    await ans(cb.message, SV_PROMPT[act], _ask_kb(f"adm:vpn:s:{sid}"))
    await cb.answer()


async def _svc_value(m: Message, shop: Shop, state: FSMContext, d):
    act, sid = d["act"], d["sid"]
    if act == "note":
        val = "" if m.text.strip() == "-" else m.text.strip()
    else:
        try:
            val = _num("float" if act == "add_gb" else "int", m.text)
        except ValueError as e:
            await ans(m, f"❌ {e}")
            return
        if not val:
            await ans(m, "❌ عدد باید بیشتر از صفر باشد.")
            return
    await state.clear()
    ok, msg = await shop.vpn.admin_action(sid, act, val, actor=f"tg:{m.from_user.id}")
    svc = await shop.db.get_vpn_service(sid)
    text, kb = await svc_card(shop, svc)
    await ans(m, ("✅ " if ok else "❌ ") + esc(msg) + "\n\n" + text, kb)


# ---- تحویل دستی
@router.callback_query(F.data.startswith("adm:vpn:sd:"))
async def cb_deliver(cb: CallbackQuery, state: FSMContext):
    sid = int(cb.data.split(":")[3])
    await state.set_state(AV.wiz)
    await state.update_data(flow="deliver", sid=sid, step=0)
    await ans(cb.message, "لینک اشتراک را بفرستید. اگر ندارید «-» بفرستید:", _ask_kb(f"adm:vpn:s:{sid}"))
    await cb.answer()


async def _deliver_step(m: Message, shop: Shop, state: FSMContext, d):
    if d["step"] == 0:
        await state.update_data(sub="" if m.text.strip() == "-" else m.text.strip(), step=1)
        await ans(m, "کانفیگ‌ها را بفرستید (هر خط یکی). اگر ندارید «-» بفرستید:", _ask_kb(f"adm:vpn:s:{d['sid']}"))
        return
    links = "" if m.text.strip() == "-" else m.text
    await state.clear()
    ok, msg = await shop.vpn.deliver_manual(d["sid"], d.get("sub", ""), links, actor=f"tg:{m.from_user.id}")
    await ans(m, ("✅ " if ok else "❌ ") + esc(msg), ikb([back_row(f"adm:vpn:s:{d['sid']}")]))


# ---- ساخت سرویس برای کاربر
@router.callback_query(F.data == "adm:vpn:new")
async def cb_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="newuser")
    await ans(cb.message, "آیدی عددی کاربر را بفرستید:", _ask_kb())
    await cb.answer()


async def _newuser_step(m: Message, shop: Shop, state: FSMContext, d):
    t = m.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not t.isdigit() or not await shop.db.get_user(int(t)):
        await ans(m, "❌ این کاربر پیدا نشد. آیدی عددی را درست بفرستید.")
        return
    await state.clear()
    plans = await shop.db.list_vpn_plans()
    rows = [[btn(f"{p['title']} · {p['panel_name']}", f"adm:vpn:cp:{t}:{p['id']}", role="admin")] for p in plans[:40]]
    rows.append(back_row())
    await ans(m, "پلن را انتخاب کنید:", ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:cp:"))
async def cb_create_for(cb: CallbackQuery, shop: Shop):
    _, _, _, uid, plan = cb.data.split(":")
    ok, msg, sid = await shop.vpn.admin_create(int(uid), int(plan), actor=f"tg:{cb.from_user.id}")
    await cb.answer(msg, show_alert=not ok)
    if ok and sid:
        text, kb = await svc_card(shop, await shop.db.get_vpn_service(sid))
        await ans(cb.message, text, kb)


# ============================================================ مدیریت کاربران VPN (مثل میرزا)
@router.callback_query(F.data.startswith("adm:vpn:us:"))
async def cb_users(cb: CallbackQuery, shop: Shop):
    page = int(cb.data.split(":")[3])
    total = await shop.db.count_vpn_users()
    users = await shop.db.vpn_users(limit=PER, offset=page * PER)
    rows = [[btn("شناسه", "adm:vpn:noop", role="back"), btn("نام کاربری", "adm:vpn:noop", role="back"), btn("عملیات", "adm:vpn:noop", role="back")]]
    for u in users:
        rows.append([btn(str(u["id"]), "adm:vpn:noop", role="back"), btn(u["username"] or u["first_name"] or "-", "adm:vpn:noop", role="back"),
                     btn(f"⚙️ مدیریت ({u['n']})", f"adm:vpn:u:{u['id']}", role="admin")])
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", f"adm:vpn:us:{page - 1}", role="back"))
    if (page + 1) * PER < total:
        nav.append(btn("بعدی ▶️", f"adm:vpn:us:{page + 1}", role="action"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔎 جستجوی کاربر", "adm:vpn:find", role="admin")])
    rows.append(back_row("adm:vpn"))
    await view(cb, f"👤 <b>کاربران VPN</b> — {total} نفر\nبرای مدیریت هر کاربر، روی «مدیریت» بزنید.", ikb(rows))
    await cb.answer()


LEVEL_TXT = {"f": "کاربر عادی (f)", "n": "نماینده نوع ۲ (n)", "n2": "نماینده نوع ۳ (n2)"}


async def _user_card(cb, shop: Shop, uid: int):
    u = await shop.db.get_user(uid)
    if not u:
        await cb.answer("کاربر پیدا نشد", show_alert=True)
        return
    st = await shop.db.user_stats(uid)
    n_all = await shop.db.count_vpn_services(user_id=uid)
    n_live = await shop.db.count_vpn_services(user_id=uid, status="active")
    joined = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["created_at"])) if u.get("created_at") else "-"
    seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_seen"])) if u.get("last_seen") else "-"
    text = (f"👀 <b>اطلاعات کاربر</b>\n\n🔗 <b>اطلاعات کاربری</b>\n\n"
            f"⭕ وضعیت کاربر: {'🚫 مسدود' if u['banned'] else '🟢 فعال'}\n⭕ نام کاربری: {('@' + esc(u['username'])) if u.get('username') else 'ندارد'}\n"
            f"⭕ آیدی عددی: <code>{uid}</code>\n⭕ زمان عضویت: {joined}\n⭕ آخرین بازدید: {seen}\n"
            f"⭕ نوع کاربری: {LEVEL_TXT.get(u.get('vpn_level') or 'f', 'f')}\n⭕ درصد تخفیف: {u.get('personal_discount') or 0}٪\n\n"
            f"💎 <b>گزارشات مالی</b>\n\n🔰 موجودی کاربر: {fmt(u['balance'])} تومان\n🔰 تعداد خرید کل: {st['done']}\n🔰 جمع کل خرید: {fmt(st['spent'])} تومان\n\n"
            f"📡 <b>سرویس‌های VPN</b>\n\n🔰 همه: {n_all}\n🔰 فعال: {n_live}")
    a = f"adm:vpn:ua:{uid}:"
    rows = [[btn("♻️ بروزرسانی اطلاعات", f"adm:vpn:u:{uid}", role="action")],
            [btn("👇 کم کردن موجودی", a + "bm", role="reject"), btn("☝️ افزایش موجودی", a + "bp", role="confirm")],
            [btn("🔓 رفع مسدودی کاربر" if u["banned"] else "🔒 مسدود کردن کاربر", a + ("unban" if u["banned"] else "ban"), role="action")],
            [btn("🤖 افزودن نماینده", a + "ag", role="admin"), btn("🤖 حذف نماینده", a + "agx", role="reject")],
            [btn("🎁 درصد تخفیف", a + "disc", role="admin"), btn("✍️ ارسال پیام به کاربر", a + "msg", role="admin")],
            [btn("🛍 مشاهده سفارشات کاربر", a + "ord", role="admin")],
            [btn("➕ محدودیت اکانت تست (یک تست دیگر)", a + "test", role="admin")],
            [btn("💰 مشاهده پرداختی کاربر", a + "pay", role="admin"), btn("0️⃣ صفر کردن موجودی", a + "b0", role="reject")],
            [btn("💡 روشن کردن اکانت‌ها", a + "on", role="action"), btn("💡 خاموش کردن اکانت‌ها", a + "off", role="action")],
            [btn("📡 سرویس‌های این کاربر", f"adm:vpn:uv:{uid}", role="admin"), btn("🆕 ساخت سرویس", f"adm:vpn:ucn:{uid}", role="confirm")],
            back_row("adm:vpn:us:0")]
    await view(cb, text, ikb(rows))


@router.callback_query(F.data.regexp(r"^adm:vpn:u:\d+$"))
async def cb_user(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await _user_card(cb, shop, int(cb.data.split(":")[3]))
    await cb.answer()


UA_PROMPT = {"bp": "چند تومان به موجودی کاربر اضافه شود؟", "bm": "چند تومان از موجودی کاربر کم شود؟",
             "disc": "درصد تخفیف شخصی کاربر؟ (۰ تا ۹۰؛ ۰ یعنی بدون تخفیف)", "msg": "متن پیام را بفرستید:"}


@router.callback_query(F.data.startswith("adm:vpn:ua:"))
async def cb_user_action(cb: CallbackQuery, shop: Shop, state: FSMContext):
    _, _, _, uid, act = cb.data.split(":")[:5]
    uid = int(uid)
    actor = f"tg:{cb.from_user.id}"
    u = await shop.db.get_user(uid)
    if not u:
        await cb.answer("کاربر پیدا نشد", show_alert=True)
        return
    if act in UA_PROMPT:
        await state.set_state(AV.wiz)
        await state.update_data(flow="uact", uid=uid, act=act)
        await ans(cb.message, UA_PROMPT[act], _ask_kb(f"adm:vpn:u:{uid}"))
        await cb.answer()
        return
    if act in ("ban", "unban"):
        await shop.db.set_banned(uid, act == "ban", "VPN admin")
        await shop.db.log(actor, f"user_{act}", str(uid))
    elif act == "b0":
        await shop.db.set_balance_exact(uid, 0, "admin: zero balance")
        await shop.db.log(actor, "user_balance_zero", str(uid))
    elif act == "ag":
        await ans(cb.message, "نوع نماینده را انتخاب کنید:", ikb([[btn("n — نماینده نوع ۲", f"adm:vpn:ua:{uid}:lvn", role="admin"), btn("n2 — نماینده نوع ۳", f"adm:vpn:ua:{uid}:lvn2", role="admin")], back_row(f"adm:vpn:u:{uid}")]))
        await cb.answer()
        return
    elif act in ("lvn", "lvn2", "agx"):
        await shop.db.set_vpn_level(uid, {"lvn": "n", "lvn2": "n2", "agx": "f"}[act])
        await shop.db.log(actor, "vpn_level", f"{uid} {act}")
    elif act == "test":
        await shop.db.run("UPDATE vpn_services SET is_test=2 WHERE user_id=? AND is_test=1", (uid,))
        await cb.answer("کاربر می‌تواند یک اکانت تست دیگر بگیرد.", show_alert=True)
        return
    elif act == "ord":
        rows = await shop.db.user_orders(uid, 10)
        txt = "🛍 <b>آخرین سفارش‌های کاربر</b>\n\n" + ("\n".join(f"#{o['id']} | {esc(o['title'])} | {fmt(o['amount'])} ت | {o['status']}" for o in rows) or "سفارشی ندارد.")
        await ans(cb.message, txt)
        await cb.answer()
        return
    elif act == "pay":
        rows = await shop.db.user_payments(uid, 10)
        txt = "💰 <b>آخرین پرداخت‌های کاربر</b>\n\n" + ("\n".join(f"#{x['id']} | {fmt(x['amount'])} ت | {x['status']}" for x in rows) or "پرداختی ندارد.")
        await ans(cb.message, txt)
        await cb.answer()
        return
    elif act in ("on", "off"):
        n = 0
        for svc in await shop.db.list_vpn_services(user_id=uid, limit=200):
            if svc["status"] in ("active", "disabled", "limited"):
                ok, _m = await shop.vpn.admin_action(svc["id"], "enable" if act == "on" else "disable", actor=actor)
                n += 1 if ok else 0
        await cb.answer(f"روی {n} سرویس انجام شد.", show_alert=True)
        return
    await _user_card(cb, shop, uid)
    await cb.answer("انجام شد")


async def _uact_step(m: Message, shop: Shop, state: FSMContext, d):
    uid, act = d["uid"], d["act"]
    actor = f"tg:{m.from_user.id}"
    t = (m.text or "").strip()
    if act == "msg":
        await state.clear()
        await shop.safe_send(uid, t)
        await ans(m, "✅ پیام ارسال شد.", ikb([back_row(f"adm:vpn:u:{uid}")]))
        return
    try:
        v = _num("int", t)
    except ValueError as e:
        await ans(m, f"❌ {e}")
        return
    if act in ("bp", "bm") and not v:
        await ans(m, "❌ مبلغ باید بیشتر از صفر باشد.")
        return
    if act == "disc" and v > 90:
        await ans(m, "❌ عدد بین ۰ و ۹۰ بفرستید.")
        return
    await state.clear()
    if act == "disc":
        await shop.db.set_personal_discount(uid, v)
    elif act == "bp":
        await shop.db.add_balance(uid, v, "admin: add (vpn menu)")
    else:
        await shop.db.add_balance(uid, -v, "admin: sub (vpn menu)")
    await shop.db.log(actor, f"user_{act}", f"{uid} {v}")
    await ans(m, "✅ انجام شد.", ikb([back_row(f"adm:vpn:u:{uid}")]))


@router.callback_query(F.data.startswith("adm:vpn:uv:"))
async def cb_user_services(cb: CallbackQuery, shop: Shop):
    await _services(cb, shop, "all", 0, q=cb.data.split(":")[3])


@router.callback_query(F.data.startswith("adm:vpn:ucn:"))
async def cb_user_new(cb: CallbackQuery, shop: Shop):
    uid = cb.data.split(":")[3]
    plans = await shop.db.list_vpn_plans()
    if not plans:
        await cb.answer("اول یک پلن بسازید.", show_alert=True)
        return
    rows = [[btn(f"{p['title']} · {p['panel_name']}", f"adm:vpn:cp:{uid}:{p['id']}", role="admin")] for p in plans[:40]]
    rows.append(back_row(f"adm:vpn:u:{uid}"))
    await view(cb, "پلن را انتخاب کنید:", ikb(rows))
    await cb.answer()


# ============================================================ پس‌زمینه‌ی QR (مثل میرزا)
async def _qr_screen(cb: CallbackQuery, shop: Shop):
    import vpn_qr
    cur = shop.s.get("vpn_qr_bg", "")
    rows, line = [], []
    for k, (label, *_c) in vpn_qr.PRESETS.items():
        line.append(btn(("✅ " if cur == k else "") + label, f"adm:vpn:qbs:{k}", role="action"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([btn(("✅ " if cur == "custom" else "") + "📤 تصویر دلخواه من", "adm:vpn:qbc", role="confirm")])
    rows.append([btn(("✅ " if not cur else "") + "⚪️ بدون پس‌زمینه", "adm:vpn:qbs:none", role="action"), btn("👁 پیش‌نمایش", "adm:vpn:qbp", role="admin")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "🖼 <b>پس‌زمینه‌ی QR کد</b>\nQR هر سرویس روی این پس‌زمینه ساخته می‌شود. یکی را انتخاب کنید یا عکس خودتان را بفرستید.", ikb(rows))


@router.callback_query(F.data == "adm:vpn:qb")
async def cb_qr(cb: CallbackQuery, shop: Shop):
    await _qr_screen(cb, shop)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:qbs:"))
async def cb_qr_set(cb: CallbackQuery, shop: Shop):
    import vpn_qr
    k = cb.data.split(":")[3]
    if k != "none" and k not in vpn_qr.PRESETS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    await shop.set_setting("vpn_qr_bg", "" if k == "none" else k, f"tg:{cb.from_user.id}")
    await _qr_screen(cb, shop)
    await cb.answer("انجام شد")


@router.callback_query(F.data == "adm:vpn:qbp")
async def cb_qr_preview(cb: CallbackQuery, shop: Shop):
    from aiogram.types import BufferedInputFile
    import vpn_qr
    miss = vpn_qr.missing_libs()
    if miss:
        await cb.answer("کتابخانه نصب نیست: " + ", ".join(miss) + "\nروی سرور بزنید: pip install " + " ".join(miss) + " و ربات را ریستارت کنید (یا آپدیت بدهید).", show_alert=True)
        return
    png = await shop.vpn.qr_png("https://example.com/sub/preview")
    if not png:
        await cb.answer("ساخت QR ممکن نیست.", show_alert=True)
        return
    await cb.message.answer_photo(BufferedInputFile(png, "preview.png"), caption="👁 پیش‌نمایش")
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:qbc")
async def cb_qr_custom(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="qrbg")
    await ans(cb.message, "عکس پس‌زمینه را بفرستید (به‌صورت عکس، نه فایل). بهتر است مربعی باشد و وسطش شلوغ نباشد.", _ask_kb("adm:vpn:qb"))
    await cb.answer()


@router.message(AV.wiz, F.photo)
async def wiz_photo(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    if d.get("flow") != "qrbg":
        return
    path = shop.vpn.qr_bg_path()
    await shop.bot.download(m.photo[-1], destination=path)
    await state.clear()
    await shop.set_setting("vpn_qr_bg", "custom", f"tg:{m.from_user.id}")
    await ans(m, "✅ پس‌زمینه ذخیره شد.", ikb([[btn("👁 پیش‌نمایش", "adm:vpn:qbp", role="admin")], back_row("adm:vpn:qb")]))


# ============================================================ موجودی کانفیگ پنل دستی (مثل میرزا)
STOCK_HELP = ("📤 کانفیگ‌های خود را مثل مثال زیر بفرستید (می‌توانید ده‌ها یا صدها کانفیگ را در یک پیام یا چند پیام، یا یک فایل txt بفرستید):\n\n"
              "<code># نام کانفیگ (فقط در یک خط، با # اول نام)\nکانفیگ (یک یا چند خط)\n\n"
              "# نام کانفیگ دوم\nکانفیگ دوم</code>\n\n"
              "اگر «#» ننویسید، هر خط یک کانفیگ حساب می‌شود. لینک اشتراک (http...) هم می‌تواند کانفیگ باشد.\n"
              "کانفیگ تکراری اضافه نمی‌شود. بعد از هر پیام نتیجه را می‌بینید؛ در پایان «✅ پایان» را بزنید.")


async def _stock_groups(shop: Shop, pid: int):
    """گروه‌های موجودی یک پنل: [(plan_id, عنوان)] — پلن‌های پنل + «هر محصول» + «تست»."""
    plans = await shop.db.list_vpn_plans(panel_id=pid)
    return [(p["id"], p["title"]) for p in plans] + [(0, "📦 هر محصول این پنل"), (-1, "🎁 اکانت تست")]


def _gname(groups, plan_id) -> str:
    return next((t for i, t in groups if i == plan_id), f"#{plan_id}")


async def _stock_pick(cb: CallbackQuery, shop: Shop, pid: int, nxt: str, title: str, only_with_stock=False):
    groups = await _stock_groups(shop, pid)
    summ = {r["plan_id"]: r for r in await shop.db.vpn_stock_summary(pid)}
    rows = []
    for i, t in groups:
        free = (summ.get(i) or {}).get("free") or 0
        if only_with_stock and not free:
            continue
        rows.append([btn(f"{t} ({free})", f"adm:vpn:{nxt}:{pid}:{i}", role="action")])
    if not rows:
        await cb.answer("موجودی‌ای ثبت نشده.", show_alert=True)
        return
    rows.append(back_row(f"adm:vpn:p:{pid}"))
    await view(cb, title, ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:cfs:\d+$"))
async def cb_stock_summary(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    pid = int(cb.data.split(":")[3])
    groups = await _stock_groups(shop, pid)
    summ = {r["plan_id"]: r for r in await shop.db.vpn_stock_summary(pid)}
    lines = ["📦 <b>موجودی کانفیگ</b>", "عنوان | آزاد | فروخته‌شده"]
    for i, t in groups:
        r = summ.get(i)
        if r:
            lines.append(f"{esc(t)} | <b>{r['free'] or 0}</b> | {r['sold'] or 0}")
    if len(lines) == 2:
        lines.append("هنوز موجودی ثبت نشده. با «➕ اضافه کردن کانفیگ» اضافه کنید؛ بعد از آن فروش این پنل با موجودی انجام می‌شود (تمام شدن = «ناموجود»).")
    await view(cb, "\n".join(lines), ikb([[btn("➕ اضافه کردن کانفیگ", f"adm:vpn:cfa:{pid}", role="confirm")], back_row(f"adm:vpn:p:{pid}")]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:cfa:\d+$"))
async def cb_stock_add(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    await _stock_pick(cb, shop, pid, "cfa2", "➕ <b>اضافه کردن کانفیگ</b>\nکانفیگ‌ها برای کدام محصول است؟ (عدد داخل پرانتز = موجودی فعلی)")


@router.callback_query(F.data.regexp(r"^adm:vpn:cfa2:\d+:-?\d+$"))
async def cb_stock_add2(cb: CallbackQuery, shop: Shop, state: FSMContext):
    _, _, _, pid, plan = cb.data.split(":")
    pid, plan = int(pid), int(plan)
    await state.set_state(AV.wiz)
    await state.update_data(flow="stock_add", panel_id=pid, plan_id=plan, added=0)
    kb = ikb([[btn("✅ پایان", f"adm:vpn:cfs:{pid}", role="confirm")]])
    groups = await _stock_groups(shop, pid)
    await cb.message.answer(rtl_fix(f"محصول: <b>{esc(_gname(groups, plan))}</b>\n\n" + STOCK_HELP), reply_markup=kb)
    await cb.answer()


async def _stock_ingest(m: Message, shop: Shop, d: dict, text: str):
    pid, plan = d["panel_id"], d["plan_id"]
    items = parse_stock_text(text)
    if not items:
        await m.answer("❌ کانفیگی در پیام پیدا نشد. مثال را دوباره ببینید.")
        return
    n, dup, delivered = await shop.vpn.stock_add(pid, plan, text)
    await shop.db.log(f"tg:{m.from_user.id}", "vpn_stock_add", f"panel {pid} plan {plan}: +{n} dup {dup}")
    free = await shop.db.count_vpn_stock(pid, plan)
    msg = f"✅ <b>{n}</b> کانفیگ اضافه شد" + (f" | ⚠️ {dup} تکراری رد شد" if dup else "") + f"\n📦 موجودی این محصول: {free}"
    if delivered:
        msg += f"\n🚚 {delivered} سفارش منتظر هم خودکار تحویل شد."
    await m.answer(rtl_fix(msg + "\nکانفیگ‌های بعدی را بفرستید یا «✅ پایان» را بزنید."),
                   reply_markup=ikb([[btn("✅ پایان", f"adm:vpn:cfs:{pid}", role="confirm")]]))


@router.message(AV.wiz, F.document)
async def wiz_document(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    if d.get("flow") != "stock_add":
        return
    doc = m.document
    if (doc.file_size or 0) > 2_000_000:
        await m.answer("❌ فایل بزرگ است (حداکثر ۲ مگابایت).")
        return
    import io
    buf = io.BytesIO()
    await shop.bot.download(doc, destination=buf)
    await _stock_ingest(m, shop, d, buf.getvalue().decode("utf-8", "ignore"))


# ---- حذف
@router.callback_query(F.data.regexp(r"^adm:vpn:cfd:\d+$"))
async def cb_stock_del(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    await _stock_pick(cb, shop, pid, "cfd2", "🗑 <b>حذف کانفیگ</b>\nمحصول را انتخاب کنید:", only_with_stock=True)


async def _stock_list(cb: CallbackQuery, shop: Shop, pid: int, plan: int, page: int, mode: str):
    total = await shop.db.count_vpn_stock(pid, plan)
    items = await shop.db.list_vpn_stock(pid, plan, limit=PER, offset=page * PER)
    act = "cfdx" if mode == "del" else "cfe3"
    rows = [[btn(("❌ " if mode == "del" else "✏️ ") + (it["name"] or f"#{it['id']}"), f"adm:vpn:{act}:{it['id']}", role="reject" if mode == "del" else "admin")] for it in items]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:vpn:{'cfd2' if mode == 'del' else 'cfe2'}:{pid}:{plan}:{page - 1}", role="back"))
    if (page + 1) * PER < total:
        nav.append(btn("▶️", f"adm:vpn:{'cfd2' if mode == 'del' else 'cfe2'}:{pid}:{plan}:{page + 1}", role="action"))
    if nav:
        rows.append(nav)
    if mode == "del" and total:
        rows.append([btn(f"🗑 حذف همه‌ی {total} کانفیگ آزاد", f"adm:vpn:cfda:{pid}:{plan}", role="reject")])
    rows.append(back_row(f"adm:vpn:{'cfd' if mode == 'del' else 'cfe'}:{pid}"))
    groups = await _stock_groups(shop, pid)
    await view(cb, f"{'🗑 حذف' if mode == 'del' else '✏️ ویرایش'} کانفیگ — <b>{esc(_gname(groups, plan))}</b>\nآزاد: {total}" + ("\nروی هر کانفیگ بزنید." if total else "\nکانفیگ آزادی نیست."), ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:cfd2:\d+:-?\d+(:\d+)?$"))
async def cb_stock_del2(cb: CallbackQuery, shop: Shop):
    p = cb.data.split(":")
    await _stock_list(cb, shop, int(p[3]), int(p[4]), int(p[5]) if len(p) > 5 else 0, "del")


@router.callback_query(F.data.regexp(r"^adm:vpn:cfdx:\d+$"))
async def cb_stock_del_one(cb: CallbackQuery, shop: Shop):
    st = await shop.db.get_vpn_stock(int(cb.data.split(":")[3]))
    if not st:
        await cb.answer("پیدا نشد.", show_alert=True)
        return
    if not await shop.db.delete_vpn_stock(st["id"]):
        await cb.answer("این کانفیگ فروخته شده و حذف نمی‌شود.", show_alert=True)
        return
    await shop.db.log(f"tg:{cb.from_user.id}", "vpn_stock_del", f"#{st['id']}")
    await _stock_list(cb, shop, st["panel_id"], st["plan_id"], 0, "del")


@router.callback_query(F.data.regexp(r"^adm:vpn:cfda:\d+:-?\d+(:y)?$"))
async def cb_stock_del_all(cb: CallbackQuery, shop: Shop):
    p = cb.data.split(":")
    pid, plan = int(p[3]), int(p[4])
    if len(p) < 6:
        n = await shop.db.count_vpn_stock(pid, plan)
        await view(cb, f"⚠️ همه‌ی <b>{n}</b> کانفیگ آزاد این محصول حذف شود؟ (کانفیگ‌های فروخته‌شده می‌مانند)",
                   ikb([[btn("✅ بله، حذف شود", f"adm:vpn:cfda:{pid}:{plan}:y", role="reject")], back_row(f"adm:vpn:cfd2:{pid}:{plan}")]))
        await cb.answer()
        return
    n = await shop.db.clear_vpn_stock(pid, plan)
    await shop.db.log(f"tg:{cb.from_user.id}", "vpn_stock_clear", f"panel {pid} plan {plan}: {n}")
    await cb.answer(f"{n} کانفیگ حذف شد.", show_alert=True)
    await cb_stock_summary_for(cb, shop, pid)


async def cb_stock_summary_for(cb: CallbackQuery, shop: Shop, pid: int):
    await _show_panel(cb, shop, pid)


# ---- ویرایش
@router.callback_query(F.data.regexp(r"^adm:vpn:cfe:\d+$"))
async def cb_stock_edit(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    await _stock_pick(cb, shop, pid, "cfe2", "✏️ <b>ویرایش کانفیگ</b>\nمحصول را انتخاب کنید:", only_with_stock=True)


@router.callback_query(F.data.regexp(r"^adm:vpn:cfe2:\d+:-?\d+(:\d+)?$"))
async def cb_stock_edit2(cb: CallbackQuery, shop: Shop):
    p = cb.data.split(":")
    await _stock_list(cb, shop, int(p[3]), int(p[4]), int(p[5]) if len(p) > 5 else 0, "edit")


@router.callback_query(F.data.regexp(r"^adm:vpn:cfe3:\d+$"))
async def cb_stock_edit3(cb: CallbackQuery, shop: Shop, state: FSMContext):
    st = await shop.db.get_vpn_stock(int(cb.data.split(":")[3]))
    if not st or st["used"]:
        await cb.answer("پیدا نشد یا فروخته شده است.", show_alert=True)
        return
    await state.set_state(AV.wiz)
    await state.update_data(flow="stock_edit", stock_id=st["id"], panel_id=st["panel_id"], plan_id=st["plan_id"])
    cur = f"# {st['name']}\n{st['config']}" if st["name"] else st["config"]
    await cb.message.answer(rtl_fix("✏️ کانفیگ فعلی:\n<code>" + esc(cur[:3000]) + "</code>\n\nمتن جدید را با همان قالب بفرستید (خط اول «# نام» و زیرش کانفیگ)."),
                            reply_markup=_ask_kb(f"adm:vpn:cfe2:{st['panel_id']}:{st['plan_id']}"))
    await cb.answer()


async def _stock_edit_save(m: Message, shop: Shop, state: FSMContext, d: dict):
    st = await shop.db.get_vpn_stock(d["stock_id"])
    items = parse_stock_text(m.text)
    if not st or st["used"] or not items:
        await m.answer("❌ متن نامعتبر است یا این کانفیگ دیگر قابل ویرایش نیست.")
        return
    name, cfg = items[0]
    if not any(x.strip().startswith("#") for x in m.text.split("\n")):
        name, cfg = st["name"], m.text.strip()
    await shop.db.update_vpn_stock(st["id"], name, cfg)
    await shop.db.log(f"tg:{m.from_user.id}", "vpn_stock_edit", f"#{st['id']}")
    await state.clear()
    await m.answer("✅ ویرایش شد.", reply_markup=ikb([back_row(f"adm:vpn:cfe2:{st['panel_id']}:{st['plan_id']}")]))


# ============================================================ سلامت پنل‌ها
def _ago(ts) -> str:
    d = int(time.time() - (ts or 0))
    return "هرگز" if not ts else (f"{d} ثانیه پیش" if d < 90 else f"{d // 60} دقیقه پیش" if d < 5400 else f"{d // 3600} ساعت پیش")


async def _health_screen(cb: CallbackQuery, shop: Shop):
    v = shop.vpn
    lines = ["🩺 <b>سلامت پنل‌ها</b>" + (" — پایش روشن" if v._flag("vpn_health_on", "1") else " — ⚠️ پایش خاموش است (تنظیمات)")]
    panels = [p for p in await shop.db.list_vpn_panels(only_enabled=True) if p["type"] != "manual"]
    if not panels:
        lines.append("\nپنل غیردستی فعالی وجود ندارد.")
    for p in panels:
        h = v._health.get(p["id"])
        closed = " ⛔️ فروش بسته" if v.is_down(p["id"]) else ""
        if not h or h["ok"] is None:
            lines.append(f"\n⚪️ <b>{esc(p['name'])}</b> — هنوز بررسی نشده")
            continue
        m = h.get("m") or {}
        bits = []
        if m.get("cpu") is not None:
            bits.append(f"CPU {float(m['cpu']):.0f}٪")
        for k, lb in (("mem", "RAM"), ("disk", "دیسک")):
            pc = v._pct(m.get(k + "_used"), m.get(k + "_total"))
            if pc is not None:
                bits.append(f"{lb} {pc:.0f}٪")
        if p.get("capacity"):
            bits.append(f"ظرفیت {await shop.db.panel_load(p['id'])}/{p['capacity']}")
        lines.append(f"\n{'🟢' if h['ok'] else '🔴'} <b>{esc(p['name'])}</b> — {_ago(h['last'])}{closed}"
                     + (f"\n   {' | '.join(bits)}" if bits else "") + (f"\n   <code>{esc(h['err'])}</code>" if h["err"] else ""))
    kb = ikb([[btn("🔄 بررسی همین الان", "adm:vpn:hlr", role="action")], back_row("adm:vpn")])
    await view(cb, rtl_fix("\n".join(lines)), kb)


@router.callback_query(F.data == "adm:vpn:hl")
async def cb_health(cb: CallbackQuery, shop: Shop):
    await _health_screen(cb, shop)
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:hlr")
async def cb_health_run(cb: CallbackQuery, shop: Shop):
    await cb.answer("در حال بررسی...")
    await shop.vpn.health_tick(force=True)
    await _health_screen(cb, shop)


# ============================================================ رنگ و ایموجی پریمیوم دکمه‌های هر محصول (وی‌پی‌ان v | پنل نمایندگی a)
_PB = {"v": ("get_vpn_plan", "update_vpn_plan", "adm:vpn:n:"), "a": ("get_agent_plan", "update_agent_plan", "adm:vpn:agpv:")}
_PB_PART = {"s": ("📊 دکمه‌ی مشخصات", "s"), "p": ("💲 دکمه‌ی قیمت", "p")}
_SDOT = {"": "⚪ پیش‌فرض", "primary": "🔵 آبی", "success": "🟢 سبز", "danger": "🔴 قرمز"}


async def _pb_view(cb: CallbackQuery, shop: Shop, k: str, pid: int):
    from admin_settings import style_row
    from admin_common import C2S
    get, _upd, back = _PB[k]
    p = await getattr(shop.db, get)(pid)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    lines, rows = [f"🎨 <b>رنگ و ایموجی دکمه‌های «{esc(p['title'])}»</b>",
                   "این تنظیم فقط روی دکمه‌های همین محصول در لیست خرید ربات اثر دارد؛ هر محصول جدید هم از همین‌جا قابل تنظیم است."], []
    for part, (lab, _x) in _PB_PART.items():
        st, em = p.get(f"{part}_style") or "", p.get(f"{part}_emoji") or ""
        lines.append(f"\n{lab}: {_SDOT.get(st, '⚪')} | " + (f"🌟 <code>{esc(em)}</code>" if em else "🌟 بدون ایموجی"))
        rows.append([btn(lab, "vpn:agnoop", style="")])
        rows.append(style_row(f"adm:vpn:pbc:{k}:{pid}:{part}", st))
        rows.append([btn("🌟 ایموجی پریمیوم", f"adm:vpn:pbe:{k}:{pid}:{part}", role="action"), btn("✖️ حذف ایموجی", f"adm:vpn:pbe:{k}:{pid}:{part}:clr", role="reject")])
    rows.append(back_row(f"{back}{pid}"))
    await view(cb, "\n".join(lines), ikb(rows))


@router.callback_query(F.data.regexp(r"^adm:vpn:pb:(v|a):\d+$"))
async def cb_pb(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    _, _, _, k, pid = cb.data.split(":")
    await _pb_view(cb, shop, k, int(pid))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:pbc:(v|a):\d+:(s|p):(d|p|s|x)$"))
async def cb_pb_color(cb: CallbackQuery, shop: Shop):
    from admin_common import C2S
    _, _, _, k, pid, part, c = cb.data.split(":")
    await getattr(shop.db, _PB[k][1])(int(pid), **{f"{part}_style": C2S[c]})
    await shop.db.log(f"tg:{cb.from_user.id}", "plan_btn_color", f"{k}{pid}.{part}={C2S[c]}")
    await _pb_view(cb, shop, k, int(pid))
    await cb.answer("انجام شد")


@router.callback_query(F.data.regexp(r"^adm:vpn:pbe:(v|a):\d+:(s|p)(:clr)?$"))
async def cb_pb_emoji(cb: CallbackQuery, shop: Shop, state: FSMContext):
    from admin_settings import EMOJI_HELP
    parts = cb.data.split(":")
    k, pid, part = parts[3], int(parts[4]), parts[5]
    if len(parts) > 6:
        await getattr(shop.db, _PB[k][1])(pid, **{f"{part}_emoji": ""})
        await _pb_view(cb, shop, k, pid)
        await cb.answer("حذف شد")
        return
    await state.set_state(AV.pb_emoji)
    await state.update_data(k=k, pid=pid, part=part)
    await cb.message.answer(EMOJI_HELP, reply_markup=_ask_kb(f"adm:vpn:pb:{k}:{pid}"))
    await cb.answer()


@router.message(AV.pb_emoji, ~F.text.startswith("/"))
async def got_pb_emoji(m: Message, shop: Shop, state: FSMContext):
    from admin_settings import extract_emoji_id
    eid = extract_emoji_id(m)
    if not eid:
        await m.answer("ایموجی پریمیوم در پیام پیدا نشد. یک پیام با ایموجی پریمیوم بفرستید یا شناسه‌ی عددی را بفرستید.")
        return
    d = await state.get_data()
    await state.clear()
    await getattr(shop.db, _PB[d["k"]][1])(d["pid"], **{f"{d['part']}_emoji": eid})
    await shop.db.log(f"tg:{m.from_user.id}", "plan_btn_emoji", f"{d['k']}{d['pid']}.{d['part']}={eid}")
    await m.answer(f"✅ ایموجی پریمیوم ثبت شد (<code>{eid}</code>).\nاگر مالک ربات تلگرام پریمیوم نباشد، تلگرام آن را نشان نمی‌دهد و ربات خودکار بدون ایموجی ادامه می‌دهد.",
                   reply_markup=ikb([[btn("🎨 بازگشت به رنگ و ایموجی", f"adm:vpn:pb:{d['k']}:{d['pid']}", role="back")]]))
