"""مدیریت فروش پنل نمایندگی داخل ربات (مجوز «vpn»): پلن‌های نمایندگی، فهرست/کارت نماینده‌ها، عملیات و ساخت برای کاربر.
callbackها همه با پیشوند adm:vpn:ag… هستند تا همان کنترل دسترسی بخش VPN اعمال شود."""
import time

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from admin_common import TXT, back_row, make_router, view
from admin_vpn import _num, _ask_kb
from service import Shop
from ui import btn, esc, fmt, ikb
from vpn_service import bar, days_left, fmt_gb

router = make_router()
PER = 8


class AG(StatesGroup):
    wiz = State()


STEP = {
    "title": ("عنوان پلن (مثلاً «نمایندگی ماهانه ۱ ترابایت»):", "text"),
    "days": ("مدت اعتبار به روز (۰ = بدون انقضا):", "int"),
    "traffic_gb": ("سقف حجم کل ادمین به گیگابایت (۰ = نامحدود؛ ۱ ترابایت = 1024):", "float"),
    "max_users": ("سقف تعداد کاربرانی که نماینده می‌تواند بسازد (۰ = نامحدود):", "int"),
    "role_id": ("شناسه‌ی نقش (role id) در پاسارگاد — برای حالت بدون نقش «-» بفرستید:", "opt"),
    "service_ids": ("شناسه‌ی سرویس‌هایی که نماینده به آن‌ها دسترسی دارد (چند مورد با کاما) — «-» یعنی همه‌ی سرویس‌ها:", "opt"),
    "price": ("قیمت فروش (تومان):", "int"),
    "cost": ("هزینه‌ی تمام‌شده برای شما (تومان، برای محاسبه‌ی سود؛ ۰ = نامشخص):", "int"),
}
EDITABLE = ("title", "days", "traffic_gb", "max_users", "price")
CUSTOM_FIELDS = ("custom_gb_price", "custom_gb_min", "custom_gb_max", "custom_days_price", "custom_days_min", "custom_days_max")
STEP.update({
    "custom_gb_price": ("قیمت هر گیگابایت حجم اضافه (تومان):", "int"),
    "custom_gb_min": ("حداقل حجم قابل خرید (گیگابایت):", "float"),
    "custom_gb_max": ("حداکثر حجم قابل خرید (گیگابایت):", "float"),
    "custom_days_price": ("قیمت هر روز زمان اضافه (تومان):", "int"),
    "custom_days_min": ("حداقل زمان قابل خرید (روز):", "int"),
    "custom_days_max": ("حداکثر زمان قابل خرید (روز):", "int"),
})
EDITABLE = EDITABLE + CUSTOM_FIELDS
STEP["a_amount_gb"] = ("مقدار حجم بسته به گیگابایت (مثلاً 50):", "float")
STEP["a_amount_days"] = ("مقدار زمان بسته به روز (مثلاً 30):", "int")
STEP["a_price"] = ("قیمت این بسته (تومان):", "int")


def steps_for(ptype: str) -> list[str]:
    s = ["title", "days"]
    if ptype == "pasarguard":
        s += ["traffic_gb", "max_users", "role_id"]
    elif ptype == "marzneshin":
        s += ["service_ids"]
    return s + ["price"]


def plan_card(p) -> str:
    extra = []
    if p["panel_type"] == "pasarguard":
        extra = [f"حجم کل: {p['traffic_gb']:g} GB" if p["traffic_gb"] else "حجم کل: نامحدود", f"سقف کاربر: {p['max_users'] or '∞'}",
                 f"نقش: {esc(p['role_id']) or 'بدون نقش'}"]
    elif p["panel_type"] == "marzneshin":
        extra = [f"سرویس‌ها: {esc(p['service_ids']) or 'همه'}"]
    return (f"🧑‍💼 <b>{esc(p['title'])}</b> — {'✅ فعال' if p['active'] else '⚫️ غیرفعال'}\nپنل: {esc(p['panel_name'])} ({p['panel_type']})\n"
            f"مدت: {p['days'] or '∞'} روز" + ("\n" + " | ".join(extra) if extra else "") +
            f"\nقیمت: {fmt(p['price'])} تومان\nحجم/زمان اضافه: {'✅ روشن' if p.get('addon_on', 1) else '⛔ خاموش'}")


# ============================================================ پلن‌ها
@router.callback_query(F.data == "adm:vpn:agp")
async def cb_plans(cb: CallbackQuery, shop: Shop):
    plans = await shop.db.list_agent_plans()
    rows = [[btn(f"{'🟢' if p['active'] else '⚫️'} {p['title']} · {p['panel_name']} · {fmt(p['price'])}", f"adm:vpn:agpv:{p['id']}", role="admin")] for p in plans[:40]]
    rows.append([btn("➕ پلن نمایندگی جدید", "adm:vpn:agpa", role="confirm")])
    rows.append(back_row("adm:vpn"))
    await view(cb, "🧑‍💼 <b>پلن‌های پنل نمایندگی</b>\nبا خرید، ادمین در پنل (پاسارگاد، مرزبان، مرزنشین) ساخته و اطلاعات ورود برای خریدار ارسال می‌شود.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:agpv:\d+$"))
async def cb_plan(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = next((x for x in await shop.db.list_agent_plans() if x["id"] == pid), None)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    rows = [[btn("⏯ فعال/غیرفعال", f"adm:vpn:agpt:{pid}", role="action"), btn("🗑 حذف", f"adm:vpn:agpd:{pid}", role="reject")],
            [btn("➕ حجم/زمان اضافه: " + ("روشن ✅" if p.get("addon_on", 1) else "خاموش ⛔"), f"adm:vpn:agpo:{pid}", role="action"),
             btn("📦 بسته‌های افزودنی", f"adm:vpn:agao:{pid}", role="admin")],
            [btn("✏️ حجم/زمان دلخواه", f"adm:vpn:agco:{pid}", role="admin")]]
    fields = [f for f in EDITABLE if f in ("title", "days", "price") or (f in ("traffic_gb", "max_users") and p["panel_type"] == "pasarguard")]
    lab = {"title": "عنوان", "days": "روز", "price": "قیمت", "traffic_gb": "حجم", "max_users": "سقف کاربر"}
    btns = [btn(f"✏️ {lab[f]}", f"adm:vpn:agpe:{pid}:{f}", role="admin") for f in fields]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append(back_row("adm:vpn:agp"))
    await view(cb, plan_card(p), ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:agpo:"))
async def cb_plan_addon_toggle(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_agent_plan(pid)
    if p:
        await shop.db.update_agent_plan(pid, addon_on=0 if p.get("addon_on", 1) else 1)
    await cb.answer("انجام شد")
    await cb_plan(cb, shop)


def _addon_label(a) -> str:
    return (f"+{a['amount']:g} GB" if a["kind"] == "gb" else f"+{int(a['amount'])} روز")


@router.callback_query(F.data.regexp(r"^adm:vpn:agco:\d+$"))
async def cb_custom(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_agent_plan(pid)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    rows, lines = [], []
    for k, name, unit in (("gb", "حجم", "GB"), ("days", "زمان", "روز")):
        on = bool(p.get(f"custom_{k}_on"))
        lines.append(f"{'🟢' if on else '⚫️'} <b>{name} دلخواه</b>: قیمت هر {unit} = {fmt(p.get(f'custom_{k}_price', 0))} | بازه: {p.get(f'custom_{k}_min', 0):g} تا {p.get(f'custom_{k}_max', 0):g}")
        rows.append([btn(f"{name}: " + ("روشن ✅" if on else "خاموش ⛔"), f"adm:vpn:agct:{pid}:{k}", role="action")])
        rows.append([btn("✏️ قیمت", f"adm:vpn:agpe:{pid}:custom_{k}_price", role="admin"), btn("✏️ حداقل", f"adm:vpn:agpe:{pid}:custom_{k}_min", role="admin"),
                     btn("✏️ حداکثر", f"adm:vpn:agpe:{pid}:custom_{k}_max", role="admin")])
    rows.append(back_row(f"adm:vpn:agpv:{pid}"))
    await view(cb, f"✏️ <b>حجم/زمان دلخواه «{esc(p['title'])}»</b>\n" + "\n".join(lines) +
               "\nکاربر عدد دلخواهش را (در بازه‌ی مجاز) می‌فرستد و مبلغ = عدد × قیمت واحد.\nبرای کار کردن، «حجم/زمان اضافه» پلن هم باید روشن و قیمت واحد بیشتر از صفر باشد.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:agct:\d+:(gb|days)$"))
async def cb_custom_toggle(cb: CallbackQuery, shop: Shop):
    _, _, _, pid, k = cb.data.split(":")
    p = await shop.db.get_agent_plan(int(pid))
    if p:
        await shop.db.update_agent_plan(int(pid), **{f"custom_{k}_on": 0 if p.get(f"custom_{k}_on") else 1})
    await cb.answer("انجام شد")
    await cb_custom(cb, shop)


@router.callback_query(F.data.regexp(r"^adm:vpn:agao:\d+$"))
async def cb_addons(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_agent_plan(pid)
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    adds = await shop.db.list_agent_addons(pid)
    rows = [[btn(f"{'🟢' if a['active'] else '⚫️'} {_addon_label(a)} · {fmt(a['price'])}", f"adm:vpn:agav:{a['id']}", role="admin")] for a in adds]
    rows.append([btn("➕ بسته‌ی حجم", f"adm:vpn:agaa:{pid}:gb", role="confirm"), btn("➕ بسته‌ی زمان", f"adm:vpn:agaa:{pid}:days", role="confirm")])
    rows.append(back_row(f"adm:vpn:agpv:{pid}"))
    await view(cb, f"📦 <b>بسته‌های افزودنی «{esc(p['title'])}»</b>\nوضعیت: {'✅ روشن' if p.get('addon_on', 1) else '⛔ خاموش (کاربر نمی‌بیند)'}\n"
                   "هر بسته یک قیمت جدا دارد؛ مشتری از صفحه‌ی پنل نمایندگی خودش می‌خرد.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:agav:\d+$"))
async def cb_addon(cb: CallbackQuery, shop: Shop):
    aid = int(cb.data.split(":")[3])
    a = await shop.db.get_agent_addon(aid)
    if not a:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    rows = [[btn("⏯ فعال/غیرفعال", f"adm:vpn:agat:{aid}", role="action"), btn("🗑 حذف", f"adm:vpn:agad:{aid}", role="reject")],
            [btn("✏️ قیمت", f"adm:vpn:agae:{aid}:price", role="admin"), btn("✏️ مقدار", f"adm:vpn:agae:{aid}:amount", role="admin")],
            back_row(f"adm:vpn:agao:{a['plan_id']}")]
    await view(cb, f"📦 <b>{_addon_label(a)}</b> — {'✅ فعال' if a['active'] else '⚫️ غیرفعال'}\nقیمت: {fmt(a['price'])} تومان", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:agat:"))
async def cb_addon_toggle(cb: CallbackQuery, shop: Shop):
    aid = int(cb.data.split(":")[3])
    a = await shop.db.get_agent_addon(aid)
    if a:
        await shop.db.update_agent_addon(aid, active=0 if a["active"] else 1)
    await cb.answer("انجام شد")
    await cb_addon(cb, shop)


@router.callback_query(F.data.startswith("adm:vpn:agad:"))
async def cb_addon_del(cb: CallbackQuery, shop: Shop):
    aid = int(cb.data.split(":")[3])
    a = await shop.db.get_agent_addon(aid)
    await shop.db.delete_agent_addon(aid)
    await cb.answer("حذف شد")
    if a:
        cb.data = f"adm:vpn:agao:{a['plan_id']}"
        await cb_addons(cb, shop)


@router.callback_query(F.data.startswith("adm:vpn:agaa:"))
async def cb_addon_add(cb: CallbackQuery, state: FSMContext):
    _, _, _, pid, kind = cb.data.split(":")
    if kind not in ("gb", "days"):
        await cb.answer()
        return
    await state.set_state(AG.wiz)
    await state.update_data(flow="aoadd", pid=int(pid), kind=kind, i=0, steps=["a_amount_" + kind, "a_price"], vals={})
    await cb.message.answer(f"<b>1/2</b> — {STEP['a_amount_' + kind][0]}", reply_markup=_ask_kb(f"adm:vpn:agao:{pid}"))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:agae:"))
async def cb_addon_edit(cb: CallbackQuery, state: FSMContext, shop: Shop):
    _, _, _, aid, field = cb.data.split(":")
    a = await shop.db.get_agent_addon(int(aid))
    if not a or field not in ("price", "amount"):
        await cb.answer()
        return
    key = "a_price" if field == "price" else ("a_amount_gb" if a["kind"] == "gb" else "a_amount_days")
    await state.set_state(AG.wiz)
    await state.update_data(flow="aoedit", oid=int(aid), field=field, key=key)
    await cb.message.answer(STEP[key][0], reply_markup=_ask_kb(f"adm:vpn:agav:{aid}"))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:agpt:"))
async def cb_plan_toggle(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    p = await shop.db.get_agent_plan(pid)
    if p:
        await shop.db.update_agent_plan(pid, active=0 if p["active"] else 1)
    await cb.answer("انجام شد")
    await cb_plans(cb, shop)


@router.callback_query(F.data.startswith("adm:vpn:agpd:"))
async def cb_plan_del(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[3])
    await shop.db.delete_agent_plan(pid)
    await shop.db.log(f"tg:{cb.from_user.id}", "agent_plan_delete", f"#{pid}")
    await cb.answer("حذف شد")
    await cb_plans(cb, shop)


@router.callback_query(F.data == "adm:vpn:agpa")
async def cb_plan_add(cb: CallbackQuery, shop: Shop):
    import vpn_panels
    panels = [p for p in await shop.db.list_vpn_panels() if getattr(vpn_panels.DRIVERS.get(p["type"]), "ADMIN", False)]
    if not panels:
        await cb.answer("ابتدا یک پنل پاسارگاد، مرزبان یا مرزنشین اضافه کنید.", show_alert=True)
        return
    rows = [[btn(f"{p['name']} · {p['type']}", f"adm:vpn:agpn:{p['id']}", role="admin")] for p in panels]
    rows.append(back_row("adm:vpn:agp"))
    await view(cb, "پلن نمایندگی برای کدام پنل ساخته شود؟", ikb(rows))
    await cb.answer()


async def _ask(msg, shop: Shop, state: FSMContext, d, i: int):
    key = d["steps"][i]
    text = f"<b>{i + 1}/{len(d['steps'])}</b> — {STEP[key][0]}"
    if key in ("role_id", "service_ids"):
        try:
            opts = await (await shop.vpn.driver(d["vals"]["panel_id"])).admin_role_options()
            if opts:
                text += "\n\n" + "\n".join(f"• <code>{esc(str(o['id']))}</code> — {esc(o['name'])}" for o in opts[:30])
        except Exception as e:
            text += f"\n\n(دریافت فهرست از پنل ممکن نشد: {esc(str(e)[:100])})"
    await msg.answer(text, reply_markup=_ask_kb("adm:vpn:agp"))


@router.callback_query(F.data.startswith("adm:vpn:agpn:"))
async def cb_plan_panel(cb: CallbackQuery, shop: Shop, state: FSMContext):
    pid = int(cb.data.split(":")[3])
    panel = await shop.db.get_vpn_panel(pid)
    if not panel:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    d = {"flow": "plan", "steps": steps_for(panel["type"]), "i": 0, "vals": {"panel_id": pid}}
    await state.set_state(AG.wiz)
    await state.update_data(**d)
    await _ask(cb.message, shop, state, d, 0)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:agpe:"))
async def cb_plan_edit(cb: CallbackQuery, state: FSMContext):
    _, _, _, pid, field = cb.data.split(":")
    if field not in EDITABLE:
        await cb.answer()
        return
    await state.set_state(AG.wiz)
    await state.update_data(flow="pedit", oid=int(pid), field=field)
    await cb.message.answer(STEP[field][0], reply_markup=_ask_kb(f"adm:vpn:agpv:{pid}"))
    await cb.answer()


def _val(key, text):
    kind = STEP[key][1]
    if kind == "opt":
        t = (text or "").strip()
        return "" if t in ("-", "") else t.replace(" ", "")[:120]
    return _num(kind, text)


@router.message(AG.wiz, TXT)
async def wiz_text(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    flow = d.get("flow")
    if flow == "pedit":
        try:
            v = _val(d["field"], m.text)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        await state.clear()
        await shop.db.update_agent_plan(d["oid"], **{d["field"]: v})
        await m.answer("✅ ذخیره شد.", reply_markup=ikb([back_row(f"adm:vpn:agpv:{d['oid']}")]))
        return
    if flow == "aoadd":
        key = d["steps"][d["i"]]
        try:
            v = _val(key, m.text)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        vals = {**d["vals"], key: v}
        if d["i"] == 0:
            await state.update_data(vals=vals, i=1)
            await m.answer(f"<b>2/2</b> — {STEP['a_price'][0]}", reply_markup=_ask_kb(f"adm:vpn:agao:{d['pid']}"))
            return
        amount = vals["a_amount_" + d["kind"]]
        if amount <= 0:
            await m.answer("❌ مقدار باید بیشتر از صفر باشد.")
            return
        await state.clear()
        await shop.db.add_agent_addon(plan_id=d["pid"], kind=d["kind"], amount=amount, price=vals["a_price"])
        await m.answer("✅ بسته ثبت شد.", reply_markup=ikb([back_row(f"adm:vpn:agao:{d['pid']}")]))
        return
    if flow == "aoedit":
        try:
            v = _val(d["key"], m.text)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        await state.clear()
        await shop.db.update_agent_addon(d["oid"], **{d["field"]: v})
        await m.answer("✅ ذخیره شد.", reply_markup=ikb([back_row(f"adm:vpn:agav:{d['oid']}")]))
        return
    if flow == "plan":
        key = d["steps"][d["i"]]
        try:
            v = _val(key, m.text)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        vals = {**d["vals"], key: v}
        i = d["i"] + 1
        if i >= len(d["steps"]):
            await state.clear()
            pid = await shop.db.add_agent_plan(**vals)
            await shop.db.log(f"tg:{m.from_user.id}", "agent_plan_add", f"#{pid}")
            await m.answer(f"✅ پلن «{esc(vals['title'])}» ثبت شد.", reply_markup=ikb([[btn("🧑‍💼 مشاهده", f"adm:vpn:agpv:{pid}", role="admin")], back_row("adm:vpn:agp")]))
            return
        await state.update_data(vals=vals, i=i)
        await _ask(m, shop, state, {**d, "vals": vals}, i)
        return
    if flow == "find":
        await state.clear()
        await _agents(m, shop, "all", 0, m.text.strip())
        return
    if flow == "val":
        act = d["act"]
        try:
            v = _num("float" if act == "set_traffic" else "int" if act == "add_days" else "text", m.text) if act != "note" else (m.text.strip()[:300] if m.text.strip() != "-" else "")
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        await state.clear()
        ok, msg = await shop.vpn.agents.admin_action(d["aid"], act, v, actor=f"tg:{m.from_user.id}")
        await m.answer(("✅ " if ok else "❌ ") + esc(msg))
        ag = await shop.db.get_agent(d["aid"])
        if ag:
            text, kb = await agent_card(shop, ag)
            await m.answer(text, reply_markup=kb)
        return
    if flow == "newuser":
        t = m.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        if not t.isdigit() or not await shop.db.get_user(int(t)):
            await m.answer("❌ کاربر پیدا نشد؛ آیدی عددی دقیق را بفرستید.")
            return
        await state.clear()
        rows = [[btn(f"{p['title']} · {p['panel_name']}", f"adm:vpn:agcp:{t}:{p['id']}", role="admin")] for p in (await shop.db.list_agent_plans())[:40]]
        rows.append(back_row("adm:vpn:agl:all:0"))
        await m.answer("پلن را انتخاب کنید:", reply_markup=ikb(rows))


# ============================================================ نماینده‌ها
AST = {"active": "🟢 فعال", "disabled": "⚫️ غیرفعال", "limited": "🟠 حجم تمام", "expired": "🔴 منقضی", "deleted": "🗑 حذف"}


AG_FILTERS = [("all", "همه"), ("active", "🟢 فعال"), ("soon", "⚠️ رو به اتمام"), ("limited", "🟠 حجم تمام"), ("expired", "🔴 منقضی")]


async def _agents(target, shop: Shop, status: str, page: int, q=""):
    if status not in [f[0] for f in AG_FILTERS] + ["disabled"]:
        status = "all"
    st = None if status == "all" else status
    total = await shop.db.count_agents(status=st, q=q)
    items = await shop.db.list_agents(status=st, q=q, limit=PER, offset=page * PER)
    rows = [[btn(("✅ " if status == k else "") + lbl, f"adm:vpn:agl:{k}:0", role="admin") for k, lbl in AG_FILTERS[:3]],
            [btn(("✅ " if status == k else "") + lbl, f"adm:vpn:agl:{k}:0", role="admin") for k, lbl in AG_FILTERS[3:]]]
    rows += [[btn(f"{AST.get(a['status'], a['status'])[:2]} {a['username']} · {a['user_id']}", f"adm:vpn:ag:{a['id']}", role="admin")] for a in items]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:vpn:agl:{status}:{page - 1}", role="back"))
    if (page + 1) * PER < total:
        nav.append(btn("▶️", f"adm:vpn:agl:{status}:{page + 1}", role="action"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔎 جستجو", "adm:vpn:agf", role="admin"), btn("🆕 ساخت برای کاربر", "adm:vpn:agn", role="admin")])
    rows.append(back_row("adm:vpn"))
    n_all = await shop.db.count_agents()
    n_soon = await shop.db.count_agents(status="soon")
    text = (f"🗂 <b>نماینده‌ها</b> ({total})" + (f" — جستجو: <code>{esc(q)}</code>" if q else "")
            + f"\nکل: {n_all} | ⚠️ رو به اتمام: {n_soon}")
    if isinstance(target, CallbackQuery):
        await view(target, text, ikb(rows))
    else:
        await target.answer(text, reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:agl:"))
async def cb_list(cb: CallbackQuery, shop: Shop):
    _, _, _, status, page = cb.data.split(":")
    await _agents(cb, shop, status, int(page))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:agf")
async def cb_find(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AG.wiz)
    await state.update_data(flow="find")
    await cb.message.answer("نام کاربری ادمین، آیدی عددی یا @یوزرنیم خریدار را بفرستید:", reply_markup=_ask_kb("adm:vpn:agl:all:0"))
    await cb.answer()


async def agent_card(shop: Shop, ag):
    u = await shop.db.get_user(ag["user_id"])
    left = days_left(ag["expires_at"])
    txt = (f"🧑‍💼 <b>{esc(ag['username'])}</b> (#{ag['id']})\nوضعیت: {AST.get(ag['status'], ag['status'])}\nپنل: {esc(ag.get('panel_name') or '-')} | پلن: {esc(ag['title'] or '-')}\n"
           f"آدرس: <code>{esc(shop.vpn.agents.panel_link(ag))}</code>\nرمز: <code>{esc(ag['password'])}</code>\n"
           f"کاربر: <code>{ag['user_id']}</code>" + (f" @{esc(u['username'])}" if u and u.get("username") else "") + "\n"
           f"مصرف: {fmt_gb(ag['used_bytes'])} / {fmt_gb(ag['total_bytes']) if ag['total_bytes'] else 'نامحدود'}  {bar(ag['used_bytes'], ag['total_bytes'])}\n"
           f"کاربران: {ag['users_count']}" + (f" / {ag['max_users']}" if ag["max_users"] else "") + f" | زمان باقی‌مانده: {'نامحدود' if left is None else f'{left} روز'}"
           + (f"\n📝 {esc(ag['note'])}" if ag["note"] else "") + f"\nآخرین همگام‌سازی: {time.strftime('%m-%d %H:%M', time.localtime(ag['last_sync'])) if ag['last_sync'] else '-'}")
    aid = ag["id"]
    rows = [[btn("➕ روز", f"adm:vpn:agv:{aid}:add_days", role="admin"), btn("📦 سقف حجم", f"adm:vpn:agv:{aid}:set_traffic", role="admin")],
            [btn("♻️ ریست مصرف", f"adm:vpn:aga:{aid}:reset", role="action"), btn("🔄 همگام‌سازی", f"adm:vpn:aga:{aid}:sync", role="action")],
            [btn("▶️ فعال" if ag["status"] in ("disabled", "expired") else "⏸ غیرفعال", f"adm:vpn:aga:{aid}:{'enable' if ag['status'] in ('disabled', 'expired') else 'disable'}", role="action"),
             btn("🔑 رمز جدید", f"adm:vpn:aga:{aid}:password", role="action")],
            [btn("📤 ارسال مجدد برای کاربر", f"adm:vpn:aga:{aid}:resend", role="action"), btn("📝 یادداشت", f"adm:vpn:agv:{aid}:note", role="admin")],
            [btn("🗑 حذف", f"adm:vpn:aga:{aid}:delete", role="reject"), btn("💸 حذف + بازگشت وجه", f"adm:vpn:aga:{aid}:refund", role="reject")],
            back_row("adm:vpn:agl:all:0")]
    return txt, ikb(rows)


@router.callback_query(F.data.regexp(r"^adm:vpn:ag:\d+$"))
async def cb_agent(cb: CallbackQuery, shop: Shop):
    ag = await shop.db.get_agent(int(cb.data.split(":")[3]))
    if not ag:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    text, kb = await agent_card(shop, ag)
    await view(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:aga:"))
async def cb_action(cb: CallbackQuery, shop: Shop):
    _, _, _, aid, act = cb.data.split(":")[:5]
    aid = int(aid)
    actor = f"tg:{cb.from_user.id}"
    if act == "resend":
        ag = await shop.db.get_agent(aid)
        if ag:
            await shop.vpn.agents.send_agent(ag["user_id"], ag)
        await cb.answer("ارسال شد")
        return
    if act in ("delete", "refund") and not cb.data.endswith(":y"):
        await cb.message.answer("⚠️ ادمین از پنل هم حذف می‌شود" + (" و مبلغ سفارش به کیف پول کاربر برمی‌گردد" if act == "refund" else "") + ". مطمئنید؟",
                                reply_markup=ikb([[btn("✅ بله", f"adm:vpn:aga:{aid}:{act}:y", role="reject"), btn("❌ خیر", f"adm:vpn:ag:{aid}", role="back")]]))
        await cb.answer()
        return
    if act in ("delete", "refund"):
        ok, msg = await shop.vpn.agents.admin_delete(aid, refund=(act == "refund"), actor=actor)
        await cb.answer(msg, show_alert=not ok)
        if ok:
            await _agents(cb, shop, "all", 0)
        return
    ok, msg = await shop.vpn.agents.admin_action(aid, act, actor=actor)
    await cb.answer(msg, show_alert=not ok)
    ag = await shop.db.get_agent(aid)
    if ok and ag:
        if act == "password":
            await shop.vpn.agents.send_agent(ag["user_id"], ag)
        text, kb = await agent_card(shop, ag)
        await view(cb, text, kb)


PROMPT = {"add_days": "چند روز اضافه شود؟", "set_traffic": "سقف حجم جدید (GB؛ ۰ = نامحدود):", "note": "متن یادداشت (برای پاک کردن «-»):"}


@router.callback_query(F.data.startswith("adm:vpn:agv:"))
async def cb_prompt(cb: CallbackQuery, state: FSMContext):
    _, _, _, aid, act = cb.data.split(":")
    await state.set_state(AG.wiz)
    await state.update_data(flow="val", aid=int(aid), act=act)
    await cb.message.answer(PROMPT[act], reply_markup=_ask_kb(f"adm:vpn:ag:{aid}"))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:agn")
async def cb_new(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AG.wiz)
    await state.update_data(flow="newuser")
    await cb.message.answer("آیدی عددی کاربر را بفرستید:", reply_markup=_ask_kb("adm:vpn:agl:all:0"))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:agcp:"))
async def cb_create_for(cb: CallbackQuery, shop: Shop):
    _, _, _, uid, plan = cb.data.split(":")
    ok, msg, aid = await shop.vpn.agents.admin_create(int(uid), int(plan), actor=f"tg:{cb.from_user.id}")
    await cb.answer(msg, show_alert=not ok)
    if ok and aid:
        text, kb = await agent_card(shop, await shop.db.get_agent(aid))
        await cb.message.answer(text, reply_markup=kb)
