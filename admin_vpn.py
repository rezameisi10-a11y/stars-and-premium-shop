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
from vpn_service import PANEL_FLAG_TITLES, PANEL_FLAGS, STATUS_FA, bar, days_left, fmt_gb, hidden_for, pextra, pflag

router = make_router()
PER = 8
URL_RE = re.compile(r"(?<![>\w/])(https?://[^\s<]+)")


async def ans(msg, text, reply_markup=None):
    """ارسال پیام با راست‌به‌چپ درست؛ آدرس‌های لاتین داخل <code> می‌روند تا با متن فارسی قاطی نشوند."""
    await msg.answer(rtl_fix(URL_RE.sub(r"<code>\1</code>", text)), reply_markup=reply_markup)


def onoff(v) -> str:
    return "✅ روشن" if v else "⛔ خاموش"

TYPE_LABEL = dict(vpn_panels.TYPES)

FIELD_PROMPT = {
    "name": "یک اسم برای پنل بنویسید (مثلاً «آلمان ۱»):",
    "url": "آدرس پنل را بفرستید (با http یا https و پورت):",
    "username": "نام کاربری ادمین پنل را بفرستید:",
    "password": "رمز ادمین پنل را بفرستید:",
    "token": "توکن (API key) را بفرستید:",
    "sub_url": "آدرس پایه‌ی لینک اشتراک را بفرستید. اگر نمی‌دانید «-» بفرستید:",
    "inbound_ids": "شناسه‌ی اینباند/گروه/سرویس را بفرستید (چند تا را با کاما جدا کنید). برای «همه» عدد «-» بفرستید:",
    "capacity": "حداکثر تعداد سرویس روی این پنل؟ (۰ یعنی نامحدود):",
    "test_days": "اکانت تست این پنل چند روز باشد؟ (برای تنظیمات کلی «-» بفرستید):",
    "test_gb": "اکانت تست این پنل چند گیگ باشد؟ (۰ نامحدود؛ برای تنظیمات کلی «-» بفرستید):",
}
OPTIONAL = {"sub_url", "inbound_ids", "test_days", "test_gb"}


class AV(StatesGroup):
    wiz = State()


def _ask_kb(cancel="adm:vpn"):
    return ikb([[btn("❌ انصراف", cancel, role="back")]])


# ============================================================ خانه
# قابلیت‌های کلی (جدول «عنوان | وضعیت | ⚙️» مثل میرزا)
FEATURES = [("vpn_enabled", "فروش VPN"), ("vpn_test_enabled", "اکانت تست رایگان"), ("vpn_allow_addon", "خرید حجم/زمان اضافه"),
            ("vpn_allow_revoke", "ساخت لینک جدید توسط کاربر"), ("vpn_stack", "جمع شدن روزهای باقی‌مانده در تمدید"),
            ("vpn_notify_admin", "اعلان فروش به ادمین"), ("vpn_agent_enabled", "فروش پنل نمایندگی")]


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
            [btn("➕ بسته‌های افزودنی", "adm:vpn:ad", role="admin"), btn("📡 سرویس‌ها", "adm:vpn:sv:all:0", role="admin")],
            [btn("👤 مدیریت کاربران", "adm:vpn:us:0", role="admin"), btn("🔎 جستجو", "adm:vpn:find", role="admin")],
            [btn("🆕 ساخت سرویس دستی", "adm:vpn:new", role="admin")],
            [btn("🧑‍💼 پلن‌های نمایندگی", "adm:vpn:agp", role="admin"), btn("🗂 نماینده‌ها", "adm:vpn:agl:all:0", role="admin")],
            [btn("📊 وضعیت قابلیت‌ها", "adm:vpn:ft", role="admin"), btn("⚙️ تنظیمات", "adm:sg:vpn", role="admin")]]
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
async def _features(cb: CallbackQuery, shop: Shop):
    rows = [[btn("عنوان", "adm:vpn:noop", role="back"), btn("وضعیت", "adm:vpn:noop", role="back"), btn("تنظیمات", "adm:vpn:noop", role="back")]]
    for key, title in FEATURES:
        on = shop.s.get(key, "0") == "1"
        rows.append([btn(title, "adm:vpn:noop", role="back"), btn(onoff(on), f"adm:vpn:ftg:{key}", role="action"), btn("⚙️", "adm:sg:vpn", role="admin")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "📊 <b>وضعیت قابلیت‌ها</b>\nبرای روشن یا خاموش کردن، روی وضعیت بزنید.", ikb(rows))


@router.callback_query(F.data == "adm:vpn:ft")
async def cb_features(cb: CallbackQuery, shop: Shop):
    await _features(cb, shop)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:ftg:"))
async def cb_feature_toggle(cb: CallbackQuery, shop: Shop):
    key = cb.data.split(":")[3]
    if key not in dict(FEATURES):
        await cb.answer("نامعتبر", show_alert=True)
        return
    await shop.set_setting(key, "0" if shop.s.get(key, "0") == "1" else "1", f"tg:{cb.from_user.id}")
    await _features(cb, shop)
    await cb.answer("انجام شد")


# ============================================================ پنل‌ها
@router.callback_query(F.data == "adm:vpn:pl")
async def cb_panels(cb: CallbackQuery, shop: Shop):
    rows = [[btn(f"{'🟢' if p['enabled'] else '⚫️'} {p['name']} · {TYPE_LABEL.get(p['type'], p['type'])} · {p['n_services']}", f"adm:vpn:p:{p['id']}", role="admin")]
            for p in await shop.db.list_vpn_panels()]
    rows.append([btn("➕ افزودن پنل", "adm:vpn:pa", role="confirm")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "🖧 <b>پنل‌ها</b>\nروی هر پنل بزنید تا تنظیماتش باز شود.", ikb(rows))
    await cb.answer()


def panel_card(p) -> str:
    ex = pextra(p)
    return (f"🖧 <b>{esc(p['name'])}</b> — {'✅ فعال' if p['enabled'] else '⚫️ غیرفعال'}\n"
            f"نوع: {esc(TYPE_LABEL.get(p['type'], p['type']))}\n"
            f"آدرس: <code>{esc(p['url'] or '-')}</code>\nکاربر: <code>{esc(p['username'] or '-')}</code>\n"
            f"اینباند/گروه: <code>{esc(p['inbound_ids'] or '-')}</code>\nآدرس اشتراک: <code>{esc(p['sub_url'] or '-')}</code>\n"
            f"ظرفیت: {p['capacity'] or 'نامحدود'} | سرویس‌ها: {p.get('n_services', 0)}\n"
            f"🎁 تست: {ex.get('test_days') or 'پیش‌فرض'} روز / {ex.get('test_gb') if ex.get('test_gb') not in (None, '') else 'پیش‌فرض'} گیگ"
            + (f"\n🚫 مخفی برای {len(ex.get('hidden', []))} کاربر" if ex.get("hidden") else ""))


async def _show_panel(cb: CallbackQuery, shop: Shop, pid: int):
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
            [btn("🎁 روز تست", f"adm:vpn:pe:{pid}:test_days", role="admin"), btn("🎁 حجم تست", f"adm:vpn:pe:{pid}:test_gb", role="admin")],
            [btn("🚫 مخفی کردن برای کاربر", f"adm:vpn:ph:{pid}", role="admin")],
            [btn("🗑 حذف پنل", f"adm:vpn:pdel:{pid}", role="reject")], back_row("adm:vpn:pl")]
    await view(cb, panel_card(p), ikb(rows))


@router.callback_query(F.data.regexp(r"^adm:vpn:p:\d+$"))
async def cb_panel(cb: CallbackQuery, shop: Shop):
    await _show_panel(cb, shop, int(cb.data.split(":")[3]))
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
    for key in PANEL_FLAGS:
        rows.append([btn(PANEL_FLAG_TITLES[key], "adm:vpn:noop", role="back"), btn(onoff(pflag(p, key)), f"adm:vpn:pft:{pid}:{key}", role="action")])
    rows.append(back_row(f"adm:vpn:p:{pid}"))
    await view(cb, f"🧩 <b>قابلیت‌های پنل «{esc(p['name'])}»</b>\nبرای تغییر، روی وضعیت بزنید.", ikb(rows))


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
    await _set_extra(shop, int(pid), **{key: 0 if pflag(p, key) else 1})
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
    await cb.answer("در حال بررسی...")
    ok, info = await shop.vpn.test_panel(p)
    load = await shop.db.panel_load(pid)
    head = f"📈 <b>وضعیت پنل «{esc(p['name'])}»</b>\n"
    if not ok:
        await ans(cb.message, head + f"🔴 وصل نشد:\n<code>{esc(str(info)[:300])}</code>")
        return
    await ans(cb.message, head + "🟢 وصل است" + (f"\nنسخه: <code>{esc(str(info['version']))}</code>" if info.get("version") else "")
              + f"\nگزینه‌های پنل: {info.get('options', 0)}\nسرویس‌های زنده‌ی ربات روی این پنل: {load}" + (f" از {p['capacity']}" if p["capacity"] else ""))


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
EXTRA_FIELDS = {"test_days", "test_gb"}


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
            extra = "\n\nگزینه‌های موجود روی پنل:\n" + "\n".join(f"• <code>{esc(str(o['id']))}</code> {esc(str(o['name']))}" for o in info["options_list"][:25])
        elif not ok:
            extra = f"\n\n⚠️ اتصال به پنل برقرار نشد (<code>{esc(str(info)[:150])}</code>). می‌توانید ادامه دهید و بعداً «تست اتصال» بزنید."
    cls = vpn_panels.DRIVERS[d["ptype"]]
    hint = ""
    if key == "inbound_ids" and cls.OPTION_LABEL:
        hint = f" ({cls.OPTION_LABEL})"
    req = key in cls.NEEDS
    await ans(msg, f"<b>{d['i'] + 1}/{len(d['steps'])}</b> — " + FIELD_PROMPT[key].replace(":", hint + ":", 1) + extra
                     + ("" if req or key in ("name", "capacity") else ""), _ask_kb("adm:vpn:pl"))


def _conv(key, text):
    text = (text or "").strip()
    if key in OPTIONAL and text == "-":
        return ""
    if key in ("test_days", "test_gb") and text == "-":
        return ""
    if key in ("capacity", "test_days", "test_gb"):
        t = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")).replace(",", "")
        try:
            v = float(t)
        except ValueError:
            raise ValueError("فقط عدد وارد کنید.") from None
        if v < 0:
            raise ValueError("عدد منفی مجاز نیست.")
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


@router.message(AV.wiz, TXT)
async def wiz_text(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    flow = d.get("flow")
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
    if flow == "deliver":
        await _deliver_step(m, shop, state, d)
        return
    if flow == "newuser":
        await _newuser_step(m, shop, state, d)
        return


# ============================================================ پلن‌ها
@router.callback_query(F.data == "adm:vpn:pn")
async def cb_plans(cb: CallbackQuery, shop: Shop):
    plans = await shop.db.list_vpn_plans()
    rows = [[btn(f"{'🟢' if p['active'] else '⚫️'} {p['title']} · {p['panel_name']} · {fmt(p['price'])}", f"adm:vpn:n:{p['id']}", role="admin")] for p in plans[:40]]
    rows.append([btn("➕ پلن جدید", "adm:vpn:pna", role="confirm")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "📦 <b>محصولات (پلن‌ها)</b>\nقیمت‌ها به تومان است.", ikb(rows))
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
           f"قیمت: {fmt(p['price'])} | هزینه: {fmt(p['cost'])} | سود: {fmt(p['price'] - p['cost'])}")
    rows = [[btn("⏯ فعال/غیرفعال", f"adm:vpn:ntg:{pid}", role="action"), btn("🗑 حذف", f"adm:vpn:ndel:{pid}", role="reject")],
            [btn("✏️ قیمت", f"adm:vpn:ne:{pid}:price", role="admin"), btn("✏️ هزینه", f"adm:vpn:ne:{pid}:cost", role="admin")],
            [btn("✏️ روز", f"adm:vpn:ne:{pid}:days", role="admin"), btn("✏️ حجم", f"adm:vpn:ne:{pid}:gb", role="admin")],
            [btn("✏️ عنوان", f"adm:vpn:ne:{pid}:title", role="admin"), btn("✏️ محدودیت IP", f"adm:vpn:ne:{pid}:ip_limit", role="admin")],
            back_row("adm:vpn:pn")]
    await view(cb, txt, ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:ntg:"))
async def cb_plan_toggle(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_vpn_plan(pid)
    if p:
        await shop.db.update_vpn_plan(pid, active=0 if p["active"] else 1)
    await cb.answer("انجام شد")
    await cb_plans(cb, shop)


@router.callback_query(F.data.startswith("adm:vpn:ndel:"))
async def cb_plan_del(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    await shop.db.delete_vpn_plan(pid)
    await shop.db.log(f"tg:{cb.from_user.id}", "vpn_plan_delete", f"#{pid}")
    await cb.answer("حذف شد")
    await cb_plans(cb, shop)


PLAN_STEPS = [("title", "اسم پلن را بنویسید (مثلاً «۳۰ روزه ۵۰ گیگ»):", "text"), ("days", "چند روز؟ (۰ یعنی بدون انقضا):", "int"),
              ("gb", "چند گیگ؟ (۰ یعنی نامحدود):", "float"), ("price", "قیمت فروش به تومان:", "int"),
              ("cost", "قیمت تمام‌شده برای شما به تومان (برای حساب سود؛ نمی‌دانید ۰):", "int"),
              ("ip_limit", "حداکثر چند نفر همزمان؟ (۰ یعنی نامحدود):", "int")]
ADDON_STEPS = [("title", "اسم بسته را بنویسید (مثلاً «۱۰ گیگ اضافه»):", "text"), ("gb", "چند گیگ اضافه شود؟ (اگر فقط زمان است ۰):", "float"),
               ("days", "چند روز اضافه شود؟ (اگر فقط حجم است ۰):", "int"), ("price", "قیمت به تومان:", "int"), ("cost", "قیمت تمام‌شده به تومان:", "int")]
KIND_OF = {k: t for k, _, t in PLAN_STEPS + ADDON_STEPS}
PROMPT_OF = {k: p for k, p, _ in PLAN_STEPS + ADDON_STEPS}


def _num(kind, text):
    t = (text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬", "0123456789.,")).replace(",", "")
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


@router.callback_query(F.data == "adm:vpn:pna")
async def cb_plan_add(cb: CallbackQuery, shop: Shop):
    panels = await shop.db.list_vpn_panels()
    if not panels:
        await cb.answer("ابتدا یک پنل اضافه کنید.", show_alert=True)
        return
    rows = [[btn(f"{p['name']}", f"adm:vpn:pnp:{p['id']}", role="admin")] for p in panels]
    rows.append(back_row("adm:vpn:pn"))
    await view(cb, "پلن برای کدام پنل ساخته شود؟", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:pnp:"))
async def cb_plan_panel(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="plan", steps=[s[0] for s in PLAN_STEPS], i=0, vals={"panel_id": int(cb.data.split(":")[3])})
    await ans(cb.message, f"<b>1/{len(PLAN_STEPS)}</b> — {PLAN_STEPS[0][1]}", _ask_kb("adm:vpn:pn"))
    await cb.answer()


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
    rows.append(back_row("adm:vpn"))
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
            [btn("✏️ قیمت", f"adm:vpn:aq:{aid}:price", role="admin"), btn("✏️ هزینه", f"adm:vpn:aq:{aid}:cost", role="admin")],
            back_row("adm:vpn:ad")]
    await view(cb, f"➕ <b>{esc(a['title'])}</b> — {'✅ فعال' if a['active'] else '⚫️ غیرفعال'}\nحجم: {a['gb']:g} GB | زمان: {a['days']} روز\nقیمت: {fmt(a['price'])} | هزینه: {fmt(a['cost'])}", ikb(rows))
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
                [btn("🗑 حذف", f"adm:vpn:sa:{sid}:delete", role="reject"), btn("💸 حذف + بازگشت وجه", f"adm:vpn:sa:{sid}:refund", role="reject")]]
    rows.append(back_row("adm:vpn:sv:all:0"))
    return txt, ikb(rows)


@router.callback_query(F.data.regexp(r"^adm:vpn:s:\d+$"))
async def cb_service(cb: CallbackQuery, shop: Shop):
    svc = await shop.db.get_vpn_service(int(cb.data.split(":")[3]))
    if not svc:
        await cb.answer("پیدا نشد", show_alert=True)
        return
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


@router.callback_query(F.data.regexp(r"^adm:vpn:u:\d+$"))
async def cb_user(cb: CallbackQuery, shop: Shop):
    uid = int(cb.data.split(":")[3])
    u = await shop.db.get_user(uid)
    if not u:
        await cb.answer("کاربر پیدا نشد", show_alert=True)
        return
    st = await shop.db.user_stats(uid)
    n_all = await shop.db.count_vpn_services(user_id=uid)
    n_live = await shop.db.count_vpn_services(user_id=uid, status="active")
    joined = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["created_at"])) if u.get("created_at") else "-"
    text = (f"👀 <b>اطلاعات کاربر</b>\n\n"
            f"🔴 وضعیت: {'🚫 مسدود' if u['banned'] else '🟢 فعال'}\n🔴 نام کاربری: {('@' + esc(u['username'])) if u.get('username') else 'ندارد'}\n"
            f"🔴 آیدی عددی: <code>{uid}</code>\n🔴 تاریخ عضویت: {joined}\n🔴 تخفیف شخصی: {u.get('personal_discount') or 0}٪\n\n"
            f"💎 <b>گزارش مالی</b>\n\n🔰 موجودی: {fmt(u['balance'])} تومان\n🔰 تعداد خرید: {st['done']}\n🔰 جمع خرید: {fmt(st['spent'])} تومان\n\n"
            f"📡 <b>سرویس‌های VPN</b>\n\n🔰 همه: {n_all}\n🔰 فعال: {n_live}")
    rows = [[btn("📡 سرویس‌های این کاربر", f"adm:vpn:uv:{uid}", role="admin"), btn("🆕 ساخت سرویس", f"adm:vpn:ucn:{uid}", role="confirm")],
            [btn("♻️ بروزرسانی", f"adm:vpn:u:{uid}", role="action")], back_row("adm:vpn:us:0")]
    await view(cb, text, ikb(rows))
    await cb.answer()


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
