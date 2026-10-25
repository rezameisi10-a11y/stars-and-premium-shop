"""مدیریت فروش VPN داخل ربات (برای ادمین‌ها با مجوز «vpn»): پنل‌ها، پلن‌ها، بسته‌های افزودنی، سرویس‌ها و ساخت/تحویل دستی."""
import json
import time

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import vpn_panels
from admin_common import TXT, back_row, make_router, view
from service import Shop
from ui import btn, esc, fmt, ikb
from vpn_service import STATUS_FA, bar, days_left, fmt_gb

router = make_router()
PER = 8
TYPE_LABEL = dict(vpn_panels.TYPES)

FIELD_PROMPT = {
    "name": "نام نمایشی پنل (مثلاً «آلمان ۱»):",
    "url": "آدرس پنل را بفرستید (با http/https و پورت، برای 3x-ui همراه مسیر WebBasePath):",
    "username": "نام کاربری ادمین پنل:",
    "password": "رمز عبور ادمین پنل:",
    "token": "توکن / API key ادمین:",
    "sub_url": "آدرس پایه‌ی لینک اشتراک (مثلاً https://sub.example.com:2096/sub) — برای حالت پیش‌فرض «-» بفرستید:",
    "inbound_ids": "شناسه‌ی اینباند/گروه/سرویس (چند مورد با کاما). برای «همه/پیش‌فرض» «-» بفرستید:",
    "capacity": "حداکثر تعداد سرویس روی این پنل (۰ = نامحدود):",
}
OPTIONAL = {"sub_url", "inbound_ids"}


class AV(StatesGroup):
    wiz = State()


def _ask_kb(cancel="adm:vpn"):
    return ikb([[btn("❌ انصراف", cancel, role="back")]])


# ============================================================ خانه
async def hub(shop: Shop):
    st = await shop.db.vpn_stats()
    pend = await shop.db.count_vpn_services(status="pending_manual")
    panels = await shop.db.list_vpn_panels()
    text = (f"🛡 <b>فروش VPN</b> — {'✅ فعال' if shop.vpn.enabled() else '⛔ غیرفعال'}\n\n"
            f"پنل‌ها: {len(panels)} | سرویس‌ها: {st['total']} (فعال {st['active']}، منقضی {st['expired']}، تست {st['tests']})\n"
            f"سفارش تحویل‌شده: {st['orders']} | فروش: {fmt(st['revenue'])} ت | سود: {fmt(st['profit'])} ت"
            + (f"\n🕓 منتظر تحویل دستی: <b>{pend}</b>" if pend else ""))
    rows = [[btn("🖧 پنل‌ها", "adm:vpn:pl", role="admin"), btn("📦 پلن‌ها", "adm:vpn:pn", role="admin")],
            [btn("➕ بسته‌های افزودنی", "adm:vpn:ad", role="admin"), btn("📡 سرویس‌ها", "adm:vpn:sv:all:0", role="admin")],
            [btn("🔎 جستجوی سرویس", "adm:vpn:find", role="admin"), btn("🆕 ساخت سرویس برای کاربر", "adm:vpn:new", role="admin")]]
    if pend:
        rows.append([btn(f"🕓 منتظر تحویل ({pend})", "adm:vpn:sv:pending_manual:0", role="action")])
    rows.append([btn("⚙️ تنظیمات VPN", "adm:sg:vpn", role="admin")])
    rows.append(back_row())
    return text, ikb(rows)


@router.callback_query(F.data == "adm:vpn")
async def cb_hub(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    text, kb = await hub(shop)
    await view(cb, text, kb)
    await cb.answer()


# ============================================================ پنل‌ها
@router.callback_query(F.data == "adm:vpn:pl")
async def cb_panels(cb: CallbackQuery, shop: Shop):
    rows = [[btn(f"{'🟢' if p['enabled'] else '⚫️'} {p['name']} · {TYPE_LABEL.get(p['type'], p['type']).split(' ')[0]} · {p['n_services']}", f"adm:vpn:p:{p['id']}", role="admin")]
            for p in await shop.db.list_vpn_panels()]
    rows.append([btn("➕ افزودن پنل", "adm:vpn:pa", role="confirm")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "🖧 <b>پنل‌های VPN</b>\nهر پنل می‌تواند چند پلن داشته باشد. فروش دستی یعنی بدون API.", ikb(rows))
    await cb.answer()


def panel_card(p) -> str:
    return (f"🖧 <b>{esc(p['name'])}</b> — {'✅ فعال' if p['enabled'] else '⚫️ غیرفعال'}\n"
            f"نوع: {esc(TYPE_LABEL.get(p['type'], p['type']))}\nآدرس: <code>{esc(p['url'] or '-')}</code>\n"
            f"کاربر: <code>{esc(p['username'] or '-')}</code> | اینباند/گروه: <code>{esc(p['inbound_ids'] or '-')}</code>\n"
            f"آدرس اشتراک: <code>{esc(p['sub_url'] or '-')}</code>\nظرفیت: {p['capacity'] or '∞'} | سرویس‌ها: {p.get('n_services', '')}")


async def _show_panel(cb: CallbackQuery, shop: Shop, pid: int):
    p = next((x for x in await shop.db.list_vpn_panels() if x["id"] == pid), None)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    rows = [[btn("🔌 تست اتصال", f"adm:vpn:pt:{pid}", role="action"), btn("⏯ فعال/غیرفعال", f"adm:vpn:ptg:{pid}", role="action")],
            [btn("✏️ نام", f"adm:vpn:pe:{pid}:name", role="admin"), btn("✏️ آدرس", f"adm:vpn:pe:{pid}:url", role="admin")],
            [btn("✏️ کاربر", f"adm:vpn:pe:{pid}:username", role="admin"), btn("✏️ رمز", f"adm:vpn:pe:{pid}:password", role="admin")],
            [btn("✏️ توکن", f"adm:vpn:pe:{pid}:token", role="admin"), btn("✏️ آدرس اشتراک", f"adm:vpn:pe:{pid}:sub_url", role="admin")],
            [btn("✏️ اینباند/گروه", f"adm:vpn:pe:{pid}:inbound_ids", role="admin"), btn("✏️ ظرفیت", f"adm:vpn:pe:{pid}:capacity", role="admin")],
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
        await cb.message.answer(f"❌ اتصال ناموفق:\n<code>{esc(str(info))}</code>")
        return
    opts = "\n".join(f"• <code>{esc(str(o['id']))}</code> {esc(str(o['name']))}" for o in info.get("options_list", [])[:25])
    await cb.message.answer(f"✅ اتصال موفق" + (f" — نسخه {esc(str(info['version']))}" if info.get("version") else "") + f"\nتعداد گزینه‌ها: {info.get('options', 0)}"
                            + (f"\n\n{opts}" if opts else ""))


@router.callback_query(F.data.startswith("adm:vpn:pdel:"))
async def cb_panel_del(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    parts = cb.data.split(":")
    if len(parts) == 4:
        await cb.message.answer("⚠️ پنل و پلن‌های آن حذف می‌شود (پنل دارای سرویس حذف نمی‌شود). مطمئنید؟",
                                reply_markup=ikb([[btn("✅ بله، حذف", f"adm:vpn:pdel:{pid}:y", role="reject"), btn("❌ خیر", f"adm:vpn:p:{pid}", role="back")]]))
        await cb.answer()
        return
    if await shop.db.delete_vpn_panel(pid):
        await shop.vpn.forget(pid)
        await shop.db.log(f"tg:{cb.from_user.id}", "vpn_panel_delete", f"#{pid}")
        await cb.answer("حذف شد")
        text, kb = await hub(shop)
        await cb.message.answer(text, reply_markup=kb)
    else:
        await cb.answer("این پنل سرویس دارد؛ به‌جای حذف، غیرفعالش کنید.", show_alert=True)


# ---- ویرایش یک فیلد پنل
@router.callback_query(F.data.startswith("adm:vpn:pe:"))
async def cb_panel_edit(cb: CallbackQuery, state: FSMContext):
    _, _, _, pid, field = cb.data.split(":")
    await state.set_state(AV.wiz)
    await state.update_data(flow="pedit", pid=int(pid), field=field)
    txt = FIELD_PROMPT[field] + ("\n(برای خالی کردن «-» بفرستید)" if field in OPTIONAL else "")
    if field in ("password", "token"):
        txt += "\n🔒 پیام شما بعد از ذخیره پاک می‌شود."
    await cb.message.answer(txt, reply_markup=_ask_kb(f"adm:vpn:p:{pid}"))
    await cb.answer()


# ---- افزودن پنل (ویزارد)
@router.callback_query(F.data == "adm:vpn:pa")
async def cb_panel_add(cb: CallbackQuery):
    rows = [[btn(label, f"adm:vpn:pat:{t}", role="admin")] for t, label in vpn_panels.TYPES]
    rows.append(back_row("adm:vpn:pl"))
    await view(cb, "🖧 نوع پنل را انتخاب کنید:", ikb(rows))
    await cb.answer()


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
    await cb.message.answer(f"ℹ️ {esc(cls.HELP)}" if cls.HELP else "شروع افزودن پنل")
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
    await msg.answer(f"<b>{d['i'] + 1}/{len(d['steps'])}</b> — " + FIELD_PROMPT[key].replace(":", hint + ":", 1) + extra
                     + ("" if req or key in ("name", "capacity") else ""), reply_markup=_ask_kb("adm:vpn:pl"))


def _conv(key, text):
    text = (text or "").strip()
    if key in OPTIONAL and text == "-":
        return ""
    if key == "capacity":
        t = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not t.isdigit():
            raise ValueError("فقط عدد وارد کنید.")
        return int(t)
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
            await m.answer(f"❌ {e}")
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
            await m.answer(f"✅ پنل «{esc(vals['name'])}» ثبت شد. حتماً «تست اتصال» بزنید.", reply_markup=kb)
            return
        await state.update_data(vals=vals, i=i)
        await _ask_step(m, state, shop)
        return
    if flow == "pedit":
        field = d["field"]
        try:
            val = _conv(field, m.text)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        if field in ("password", "token"):
            await _safe_delete(m)
        await state.clear()
        await shop.db.update_vpn_panel(d["pid"], **{field: val})
        await shop.vpn.forget(d["pid"])
        await m.answer("✅ ذخیره شد.", reply_markup=ikb([back_row(f"adm:vpn:p:{d['pid']}")]))
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
    await view(cb, "📦 <b>پلن‌های VPN</b>\nقیمت به تومان؛ هزینه برای محاسبه‌ی سود است.", ikb(rows))
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


PLAN_STEPS = [("title", "عنوان پلن (مثلاً «۳۰ روزه ۵۰ گیگ»):", "text"), ("days", "مدت به روز (۰ = بدون انقضا):", "int"),
              ("gb", "حجم به گیگابایت (۰ = نامحدود؛ اعشار مجاز):", "float"), ("price", "قیمت فروش (تومان):", "int"),
              ("cost", "هزینه‌ی تمام‌شده برای شما (تومان، برای محاسبه‌ی سود؛ ۰ = نامشخص):", "int"),
              ("ip_limit", "محدودیت تعداد کاربر همزمان (۰ = نامحدود):", "int")]
ADDON_STEPS = [("title", "عنوان بسته (مثلاً «+۱۰ گیگ»):", "text"), ("gb", "حجم اضافه (GB؛ ۰ اگر فقط زمان):", "float"),
               ("days", "زمان اضافه (روز؛ ۰ اگر فقط حجم):", "int"), ("price", "قیمت (تومان):", "int"), ("cost", "هزینه (تومان):", "int")]
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
    await cb.message.answer(f"<b>1/{len(PLAN_STEPS)}</b> — {PLAN_STEPS[0][1]}", reply_markup=_ask_kb("adm:vpn:pn"))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:ne:"))
async def cb_plan_edit(cb: CallbackQuery, state: FSMContext):
    _, _, _, pid, field = cb.data.split(":")
    await state.set_state(AV.wiz)
    await state.update_data(flow="pedit2", obj="plan", oid=int(pid), field=field)
    await cb.message.answer(PROMPT_OF[field], reply_markup=_ask_kb(f"adm:vpn:n:{pid}"))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:ada")
async def cb_addon_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="addon", steps=[s[0] for s in ADDON_STEPS], i=0, vals={})
    await cb.message.answer(f"<b>1/{len(ADDON_STEPS)}</b> — {ADDON_STEPS[0][1]}", reply_markup=_ask_kb("adm:vpn:ad"))
    await cb.answer()


async def _wiz_money(m: Message, shop: Shop, state: FSMContext, d):
    flow = d["flow"]
    if flow == "pedit2":
        field = d["field"]
        try:
            v = _num(KIND_OF[field], m.text)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        await state.clear()
        if d["obj"] == "plan":
            await shop.db.update_vpn_plan(d["oid"], **{field: v})
            back = f"adm:vpn:n:{d['oid']}"
        else:
            await shop.db.update_vpn_addon(d["oid"], **{field: v})
            back = f"adm:vpn:ae:{d['oid']}"
        await m.answer("✅ ذخیره شد.", reply_markup=ikb([back_row(back)]))
        return
    steps = d["steps"]
    key = steps[d["i"]]
    try:
        v = _num(KIND_OF[key], m.text)
    except ValueError as e:
        await m.answer(f"❌ {e}")
        return
    vals = {**d["vals"], key: v}
    i = d["i"] + 1
    if i >= len(steps):
        await state.clear()
        if flow == "plan":
            pid = await shop.db.add_vpn_plan(**vals)
            await m.answer(f"✅ پلن «{esc(vals['title'])}» ثبت شد.", reply_markup=ikb([[btn("📦 مشاهده", f"adm:vpn:n:{pid}", role="admin")], back_row("adm:vpn:pn")]))
        else:
            if not vals.get("gb") and not vals.get("days"):
                await m.answer("❌ حجم یا زمان باید بیشتر از صفر باشد؛ دوباره از «افزودن بسته» شروع کنید.", reply_markup=ikb([back_row("adm:vpn:ad")]))
                return
            aid = await shop.db.add_vpn_addon(**vals)
            await m.answer(f"✅ بسته «{esc(vals['title'])}» ثبت شد.", reply_markup=ikb([[btn("➕ مشاهده", f"adm:vpn:ae:{aid}", role="admin")], back_row("adm:vpn:ad")]))
        return
    await state.update_data(vals=vals, i=i)
    await m.answer(f"<b>{i + 1}/{len(steps)}</b> — {PROMPT_OF[steps[i]]}", reply_markup=_ask_kb("adm:vpn:pn" if flow == "plan" else "adm:vpn:ad"))


# ============================================================ بسته‌های افزودنی
@router.callback_query(F.data == "adm:vpn:ad")
async def cb_addons(cb: CallbackQuery, shop: Shop):
    rows = [[btn(f"{'🟢' if a['active'] else '⚫️'} {a['title']} · {fmt(a['price'])}", f"adm:vpn:ae:{a['id']}", role="admin")] for a in await shop.db.list_vpn_addons()]
    rows.append([btn("➕ بسته‌ی جدید", "adm:vpn:ada", role="confirm")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "➕ <b>بسته‌های افزودنی</b> (حجم یا زمان اضافه برای سرویس‌های فعال)", ikb(rows))
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
    await cb.message.answer(PROMPT_OF[field], reply_markup=_ask_kb(f"adm:vpn:ae:{aid}"))
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
    await cb.message.answer("نام کاربری سرویس، آیدی عددی یا یوزرنیم کاربر را بفرستید:", reply_markup=_ask_kb())
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
        await cb.message.answer("⚠️ سرویس از پنل VPN هم حذف می‌شود" + (" و مبلغ سفارش به کیف پول کاربر برمی‌گردد" if act == "refund" else "") + ". مطمئنید؟",
                                reply_markup=ikb([[btn("✅ بله", f"adm:vpn:sa:{sid}:{act}:y", role="reject"), btn("❌ خیر", f"adm:vpn:s:{sid}", role="back")]]))
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


SV_PROMPT = {"add_days": "چند روز اضافه شود؟", "add_gb": "چند گیگابایت اضافه شود؟", "note": "متن یادداشت (برای پاک کردن «-»):"}


@router.callback_query(F.data.startswith("adm:vpn:sv2:"))
async def cb_svc_prompt(cb: CallbackQuery, state: FSMContext):
    _, _, _, sid, act = cb.data.split(":")
    await state.set_state(AV.wiz)
    await state.update_data(flow="svcval", sid=int(sid), act=act)
    await cb.message.answer(SV_PROMPT[act], reply_markup=_ask_kb(f"adm:vpn:s:{sid}"))
    await cb.answer()


async def _svc_value(m: Message, shop: Shop, state: FSMContext, d):
    act, sid = d["act"], d["sid"]
    if act == "note":
        val = "" if m.text.strip() == "-" else m.text.strip()
    else:
        try:
            val = _num("float" if act == "add_gb" else "int", m.text)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        if not val:
            await m.answer("❌ عدد باید بیشتر از صفر باشد.")
            return
    await state.clear()
    ok, msg = await shop.vpn.admin_action(sid, act, val, actor=f"tg:{m.from_user.id}")
    svc = await shop.db.get_vpn_service(sid)
    text, kb = await svc_card(shop, svc)
    await m.answer(("✅ " if ok else "❌ ") + esc(msg) + "\n\n" + text, reply_markup=kb)


# ---- تحویل دستی
@router.callback_query(F.data.startswith("adm:vpn:sd:"))
async def cb_deliver(cb: CallbackQuery, state: FSMContext):
    sid = int(cb.data.split(":")[3])
    await state.set_state(AV.wiz)
    await state.update_data(flow="deliver", sid=sid, step=0)
    await cb.message.answer("لینک اشتراک را بفرستید (اگر ندارید «-»):", reply_markup=_ask_kb(f"adm:vpn:s:{sid}"))
    await cb.answer()


async def _deliver_step(m: Message, shop: Shop, state: FSMContext, d):
    if d["step"] == 0:
        await state.update_data(sub="" if m.text.strip() == "-" else m.text.strip(), step=1)
        await m.answer("کانفیگ‌های مستقیم را بفرستید (هر خط یک کانفیگ؛ اگر ندارید «-»):", reply_markup=_ask_kb(f"adm:vpn:s:{d['sid']}"))
        return
    links = "" if m.text.strip() == "-" else m.text
    await state.clear()
    ok, msg = await shop.vpn.deliver_manual(d["sid"], d.get("sub", ""), links, actor=f"tg:{m.from_user.id}")
    await m.answer(("✅ " if ok else "❌ ") + esc(msg), reply_markup=ikb([back_row(f"adm:vpn:s:{d['sid']}")]))


# ---- ساخت سرویس برای کاربر
@router.callback_query(F.data == "adm:vpn:new")
async def cb_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AV.wiz)
    await state.update_data(flow="newuser")
    await cb.message.answer("آیدی عددی کاربر را بفرستید:", reply_markup=_ask_kb())
    await cb.answer()


async def _newuser_step(m: Message, shop: Shop, state: FSMContext, d):
    t = m.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not t.isdigit() or not await shop.db.get_user(int(t)):
        await m.answer("❌ کاربر پیدا نشد؛ آیدی عددی دقیق را بفرستید.")
        return
    await state.clear()
    plans = await shop.db.list_vpn_plans()
    rows = [[btn(f"{p['title']} · {p['panel_name']}", f"adm:vpn:cp:{t}:{p['id']}", role="admin")] for p in plans[:40]]
    rows.append(back_row())
    await m.answer("پلن را انتخاب کنید:", reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:cp:"))
async def cb_create_for(cb: CallbackQuery, shop: Shop):
    _, _, _, uid, plan = cb.data.split(":")
    ok, msg, sid = await shop.vpn.admin_create(int(uid), int(plan), actor=f"tg:{cb.from_user.id}")
    await cb.answer(msg, show_alert=not ok)
    if ok and sid:
        text, kb = await svc_card(shop, await shop.db.get_vpn_service(sid))
        await cb.message.answer(text, reply_markup=kb)
