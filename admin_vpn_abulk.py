"""عملیات گروهی و تغییر لوکیشن «نماینده‌ها» (جدا از کاربران عادی): انتخاب پنل ← فیلتر یا انتخاب با تیک ← اقدام ← تأیید.

نماینده = اکانت ادمین روی پنل. انتقال = ساخت اکانت جدید با حجم/زمان/سقف کاربرِ باقی‌مانده روی پنل مقصد و غیرفعال‌کردن (نه حذف) اکانت قبلی.
"""
from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from admin_common import TXT, back_row, make_router, view
from service import Shop
from ui import btn, esc, fmt, ikb
from vpn_ops import MoveError

router = make_router()
PER = 8

AB_FILTERS = {"all": "📡 همه‌ی نماینده‌ها", "active": "🟢 فعال‌ها", "soon": "⚠️ رو به اتمام (زمان یا ≥۹۰٪ حجم)", "limited": "🟠 حجم تمام‌شده",
              "expired": "🔴 منقضی‌شده‌ها", "disabled": "⚫️ غیرفعال‌ها", "sel": "✅ انتخاب نماینده (تیک‌زدن)"}
AB_ACTIONS = {"add_days": "➕ روز", "add_gb": "➕ حجم (GB)", "enable": "▶️ فعال‌سازی", "disable": "⏸ غیرفعال‌سازی",
              "move": "📍 انتقال به پنل دیگر", "msg": "✉️ پیام به نماینده‌ها"}


class AB(StatesGroup):
    wiz = State()


def _ask_kb(cancel="adm:vpn:abk"):
    return ikb([[btn("❌ انصراف", cancel, role="back")]])


async def _ans(m, text, kb):
    await m.answer(text, reply_markup=kb)


def _jd(ts) -> str:
    from handlers_admin import jdate
    return jdate(ts)


async def _panels(shop: Shop) -> dict:
    return {p["id"]: p for p in await shop.db.list_vpn_panels()}


async def _title(shop: Shop, d: dict) -> str:
    pn = (await _panels(shop)).get(d.get("p")) if d.get("p") else None
    t = f"🧰 <b>عملیات گروهی نماینده‌ها</b>\n🖧 پنل: <b>{esc(pn['name']) if pn else 'همه‌ی پنل‌ها'}</b>"
    if d.get("f"):
        t += f"\n🔎 فیلتر: <b>{AB_FILTERS.get(d['f'], '—')}</b>" + (f" ({fmt(len(d.get('sel') or []))})" if d["f"] == "sel" else "")
    if d.get("a"):
        t += f"\n⚙️ اقدام: <b>{AB_ACTIONS[d['a']]}</b>" + (f" — مقدار: <code>{esc(str(d['v'])[:60])}</code>" if d.get("v") not in (None, "") else "")
    if d.get("t"):
        t += f"\n📍 مقصد: <b>{esc((await _panels(shop))[d['t']]['name'])}</b>"
    return t


# ---------------------------------------------------------------- ورود و انتخاب پنل
async def _first(cb, shop: Shop, move: bool):
    panels = await shop.db.list_vpn_panels()
    rows = [[btn("🖧 همه‌ی پنل‌ها", "adm:vpn:abkp:0", role="admin")]]
    rows += [[btn(f"{p['name']}", f"adm:vpn:abkp:{p['id']}", role="admin")] for p in panels if p["type"] != "manual"]
    rows.append(back_row("adm:vpn:amvs" if move else "adm:vpn:agl:all:0"))
    await view(cb, ("🔁 <b>جابه‌جایی خودکار نماینده‌ها به پنل دیگر</b>\n۱) پنل <b>مبدأ</b>؟ بعد: همه / فیلتر / انتخاب با تیک، و مقصد."
                    if move else "🧰 <b>عملیات گروهی نماینده‌ها</b>\n۱) پنل موردنظر را انتخاب کنید. بعد فیلتر یا «انتخاب نماینده» و اقدام را می‌گویید و قبل از اجرا فهرست را می‌بینید.\n(برای کاربران عادی: 🛡 فروش VPN ← عملیات گروهی)"), ikb(rows))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:abk")
async def cb_start(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await _first(cb, shop, False)


@router.callback_query(F.data == "adm:vpn:abkm")
async def cb_start_move(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await state.update_data(abpre="move")
    await _first(cb, shop, True)


@router.callback_query(F.data.startswith("adm:vpn:abkp:"))
async def cb_panel(cb: CallbackQuery, shop: Shop, state: FSMContext):
    pid = int(cb.data.split(":")[3])
    d = {"p": pid or None}
    if (await state.get_data()).get("abpre") == "move":
        d["pre"] = "move"
    await state.update_data(ab=d)
    rows = [[btn(t, f"adm:vpn:abkf:{k}", role="admin")] for k, t in AB_FILTERS.items()]
    rows.append(back_row("adm:vpn:abk"))
    await view(cb, await _title(shop, d) + "\n\n۲) کدام نماینده‌ها؟", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:abkf:"))
async def cb_filter(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("ab")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    d["f"] = cb.data.split(":")[3]
    d.setdefault("sel", [])
    await state.update_data(ab=d)
    if d["f"] == "sel":
        await _picker(cb, shop, state, 0)
    else:
        await _pick_action(cb, shop, d)
    await cb.answer()


# ---------------------------------------------------------------- انتخاب با تیک
async def _picker(cb, shop: Shop, state: FSMContext, page: int):
    d = (await state.get_data()).get("ab") or {}
    sel = set(d.get("sel") or [])
    items, total = await shop.vpn.agents.search_page(d.get("p"), d.get("q", ""), page, PER)
    rows = [[btn(f"{'☑️' if a['id'] in sel else '⬜️'} {a['username']} · {a['user_id']}", f"adm:vpn:abkc:{a['id']}:{page}", role="confirm" if a["id"] in sel else "admin")] for a in items]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:vpn:abkg:{page - 1}", role="back"))
    if (page + 1) * PER < total:
        nav.append(btn("▶️", f"adm:vpn:abkg:{page + 1}", role="action"))
    if nav:
        rows.append(nav)
    rows.append([btn("☑️ همه‌ی این صفحه", f"adm:vpn:abkca:{page}", role="action"), btn("🧹 پاک کردن انتخاب", f"adm:vpn:abkcx:{page}", role="action")])
    rows.append([btn("🔎 جستجو (نام/آیدی)" + (" ✅" if d.get("q") else ""), "adm:vpn:abkq", role="admin")])
    if sel:
        rows.append([btn(f"➡️ ادامه با {fmt(len(sel))} نماینده", "adm:vpn:abkd", role="confirm")])
    rows.append(back_row("adm:vpn:abk"))
    text = (await _title(shop, d) + f"\n\n۲) نماینده‌های موردنظر را تیک بزنید (انتخاب‌شده: <b>{fmt(len(sel))}</b>)"
            + (f"\n🔎 جستجو: <code>{esc(d['q'])}</code>" if d.get("q") else "") + f"\nصفحه {page + 1} از {max(1, -(-total // PER))} · کل: {fmt(total)}")
    if isinstance(cb, Message):
        await _ans(cb, text, ikb(rows))
    else:
        await view(cb, text, ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:abkg:"))
async def cb_page(cb: CallbackQuery, shop: Shop, state: FSMContext):
    if (await state.get_data()).get("ab") is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    await _picker(cb, shop, state, int(cb.data.split(":")[3]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:abkc:"))
async def cb_toggle(cb: CallbackQuery, shop: Shop, state: FSMContext):
    _, _, _, aid, page = cb.data.split(":")
    d = (await state.get_data()).get("ab")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    sel = set(d.get("sel") or [])
    sel.symmetric_difference_update({int(aid)})
    d["sel"] = sorted(sel)
    await state.update_data(ab=d)
    await _picker(cb, shop, state, int(page))
    await cb.answer()


@router.callback_query(F.data.startswith(("adm:vpn:abkca:", "adm:vpn:abkcx:")))
async def cb_bulk_toggle(cb: CallbackQuery, shop: Shop, state: FSMContext):
    kind, page = cb.data.split(":")[2], int(cb.data.split(":")[3])
    d = (await state.get_data()).get("ab")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    if kind == "abkcx":
        d["sel"] = []
    else:
        items, _ = await shop.vpn.agents.search_page(d.get("p"), d.get("q", ""), page, PER)
        d["sel"] = sorted(set(d.get("sel") or []) | {a["id"] for a in items})
    await state.update_data(ab=d)
    await _picker(cb, shop, state, page)
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:abkq")
async def cb_search(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AB.wiz)
    await state.update_data(flow="abq")
    await cb.message.answer("بخشی از نام کاربری نماینده یا آیدی عددی کاربر را بفرستید (پاک‌کردن جستجو: «-»):", reply_markup=_ask_kb("adm:vpn:abkg:0"))
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:abkd")
async def cb_done(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("ab") or {}
    if not d.get("sel"):
        await cb.answer("هیچ نماینده‌ای تیک نخورده.", show_alert=True)
        return
    await _pick_action(cb, shop, d)
    await cb.answer()


# ---------------------------------------------------------------- اقدام
async def _target_picker(cb, shop: Shop, d: dict):
    rows = [[btn(f"📍 {p['name']}", f"adm:vpn:abkt:{p['id']}", role="admin")] for p in await shop.vpn.agents.move_targets({"panel_id": d.get("p") or 0}, admin=True)]
    rows.append(back_row("adm:vpn:abk"))
    await view(cb, await _title(shop, d) + "\n\nبه کدام پنل منتقل شوند؟ (حجم/زمان/سقف کاربرِ باقی‌مانده حفظ می‌شود؛ اکانت قبلی غیرفعال می‌شود و مشخصات جدید برای هر نماینده ارسال می‌شود)", ikb(rows))


async def _pick_action(cb, shop: Shop, d: dict):
    if d.get("pre") == "move":
        await _target_picker(cb, shop, d)
        return
    rows, row = [], []
    for k, t in AB_ACTIONS.items():
        row.append(btn(t, f"adm:vpn:abka:{k}", role="admin"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(back_row("adm:vpn:abk"))
    await view(cb, await _title(shop, d) + "\n\n۳) چه کاری؟", ikb(rows))


@router.callback_query(F.data.startswith("adm:vpn:abka:"))
async def cb_action(cb: CallbackQuery, shop: Shop, state: FSMContext):
    act = cb.data.split(":")[3]
    d = (await state.get_data()).get("ab")
    if d is None or act not in AB_ACTIONS:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    d["a"] = act
    await state.update_data(ab=d)
    if act in ("add_days", "add_gb", "msg"):
        await state.set_state(AB.wiz)
        await state.update_data(flow="abval")
        q = {"add_days": "چند روز اضافه شود؟ (عدد)", "add_gb": "چند گیگ به سقف حجم هر نماینده اضافه شود؟ (عدد؛ نماینده‌ی حجم نامحدود رد می‌شود)",
             "msg": "متن پیام را بفرستید (به صاحب هر پنل نمایندگی یک‌بار ارسال می‌شود؛ HTML ساده مجاز است):"}[act]
        await cb.message.answer(q, reply_markup=_ask_kb())
    elif act == "move":
        await _target_picker(cb, shop, d)
    else:
        await _confirm(cb, shop, state)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:vpn:abkt:"))
async def cb_target(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("ab")
    if d is None:
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    d["t"] = int(cb.data.split(":")[3])
    if d.get("pre") == "move":
        d["a"] = "move"
    await state.update_data(ab=d)
    await _confirm(cb, shop, state)
    await cb.answer()


@router.message(AB.wiz, TXT)
async def wiz_text(m: Message, shop: Shop, state: FSMContext):
    d0 = await state.get_data()
    flow, d = d0.get("flow"), d0.get("ab") or {}
    if flow == "abq":
        q = (m.text or "").strip()
        d["q"] = "" if q == "-" else q[:40]
        await state.set_state(None)
        await state.update_data(ab=d, flow=None)
        await _picker(m, shop, state, 0)
        return
    if flow != "abval":
        return
    try:
        if d.get("a") == "msg":
            val = (m.html_text or m.text or "").strip()[:3500]
            if not val:
                raise ValueError("متن خالی است.")
        else:
            val = float((m.text or "").strip().replace(",", "."))
            if val <= 0:
                raise ValueError("عدد باید بیشتر از صفر باشد.")
            if d["a"] == "add_days":
                val = int(val)
    except ValueError as e:
        await m.answer(f"❌ {e}")
        return
    d["v"] = val
    await state.set_state(None)
    await state.update_data(ab=d, flow=None)
    await _confirm(m, shop, state)


async def _confirm(src, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("ab") or {}
    ag = shop.vpn.agents
    if d.get("f") == "sel":
        items = [a for a in [await shop.db.get_agent(i) for i in d.get("sel") or []] if a and a["status"] != "deleted"]
    else:
        items = await ag.select(d.get("p"), d.get("f") or "all")
    if d.get("a") == "move" and d.get("t"):
        items = [a for a in items if a["panel_id"] != d["t"]]
    cap = 1000
    over = len(items) > cap
    items = items[:cap]
    target = src if isinstance(src, Message) else src.message
    if not items:
        await _ans(target, await _title(shop, d) + "\n\n⚠️ هیچ نماینده‌ای با این فیلتر پیدا نشد.", ikb([back_row("adm:vpn:abk")]))
        return
    d["ids"] = [a["id"] for a in items]
    await state.update_data(ab=d)
    sample = "، ".join(f"<code>{esc(a['username'])}</code>" for a in items[:5])
    warn = ("\n\n📍 برای هر نماینده روی پنل مقصد <b>اکانت جدید</b> با حجم/زمان/سقف کاربرِ باقی‌مانده ساخته و مشخصات جدید برایش ارسال می‌شود؛ اکانت قبلی غیرفعال (نه حذف) می‌شود. "
            "⚠️ <b>کاربرانی که نماینده داخل پنل قبلی ساخته خودکار منتقل نمی‌شوند.</b> برگشت: «📍 تغییر لوکیشن نماینده‌ها ← ↩️ بازگشت جابه‌جایی‌ها»." if d.get("a") == "move" else "")
    text = (await _title(shop, d) + f"\n\n📊 <b>{fmt(len(items))}</b> نماینده از <b>{fmt(len({a['user_id'] for a in items}))}</b> کاربر" + (f" (سقف {cap} در هر بار)" if over else "")
            + f"\nنمونه: {sample}" + warn + "\n\nاجرا شود؟")
    await _ans(target, text, ikb([[btn("✅ اجرا", "adm:vpn:abkgo", role="confirm"), btn("❌ لغو", "adm:vpn:abk", role="back")]]))


@router.callback_query(F.data == "adm:vpn:abkgo")
async def cb_go(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = (await state.get_data()).get("ab") or {}
    ids = d.get("ids") or []
    if not ids or not d.get("a"):
        await cb.answer("از اول شروع کنید.", show_alert=True)
        return
    uid = cb.from_user.id
    jobs = shop.vpn.ops.jobs
    if jobs.get(uid, {}).get("running"):
        await cb.answer("یک عملیات گروهی دیگر از شما در حال اجراست.", show_alert=True)
        return
    await state.clear()
    chat = cb.message.chat.id
    rows = [a for a in [await shop.db.get_agent(i) for i in ids] if a]
    job = jobs[uid] = {"running": True}
    summary = await _title(shop, d)

    async def run():
        async def progress(i, n):
            await shop.safe_send(chat, f"⏳ {i}/{n} ...")
        try:
            res = await shop.vpn.agents.bulk_run(rows, d["a"], d.get("v"), d.get("t"), actor=f"tg:{uid}", progress=progress)
        finally:
            job["running"] = False
        err = ("\n\n" + "\n".join(esc(x) for x in res["errors"][:20])) if res["errors"] else ""
        extra = [[btn("↩️ بازگشت این جابه‌جایی", f"adm:vpn:abb:{res['batch']}", role="action")]] if res.get("batch") and res["ok"] else []
        await shop.safe_send(chat, f"🏁 <b>پایان عملیات گروهی نماینده‌ها</b>\n{summary}\n\n✅ موفق: {res['ok']}\n❌ ناموفق: {res['fail']}{err}", reply_markup=ikb(extra + [back_row("adm:vpn:agl:all:0")]))
        await shop.audit(f"tg:{uid}", f"🧰 عملیات گروهی نماینده‌ها: {d['a']} روی {len(rows)} نماینده (موفق {res['ok']}، ناموفق {res['fail']})")

    shop.spawn(run())
    await shop.db.log(f"tg:{uid}", "agent_bulk", f"{d['a']} n={len(rows)}")
    await cb.message.answer(f"🚀 اجرا شروع شد ({fmt(len(rows))} نماینده)؛ وقتی تمام شد گزارش می‌دهم.", reply_markup=ikb([back_row("adm:vpn:agl:all:0")]))
    await cb.answer()


# ---------------------------------------------------------------- تنظیمات «تغییر لوکیشن نماینده‌ها» + بازگشت
async def _move_screen(cb: CallbackQuery, shop: Shop):
    from vpn_service import pextra, pflag
    on = shop.s.get("vpn_agent_move", "0") == "1"
    nb = len(await shop.db.vm_batches("agent", 15))
    mx = shop.s.get("vpn_agent_move_max", "1")
    panels = []
    for p in await shop.db.list_vpn_panels():
        if p["type"] != "manual" and getattr(await shop.vpn.driver(p["id"]), "ADMIN", False):
            panels.append(p)
    lines = ["📍 <b>تغییر لوکیشن نماینده‌ها</b>", "",
             f"<b>۱) دستی — توسط خود نماینده</b> ({'✅ روشن' if on else '⛔️ خاموش'})",
             "نماینده در «پنل‌های نمایندگی من» روی «📍 تغییر لوکیشن» می‌زند. حداکثر دفعات برای هر نماینده: " + f"<b>{esc(mx)}</b> (۰ = بی‌نهایت). پنل‌هایی را که می‌تواند به آن‌ها برود تیک بزنید.",
             "", "<b>۲) خودکار — توسط شما</b>", "برای نماینده‌های انتخابی، همه یا رو‌به‌اتمام؛ مشخصات جدید برای هرکدام ارسال می‌شود. اشتباه شد؟ «↩️ بازگشت جابه‌جایی‌ها».",
             "", "⚠️ انتقال یعنی اکانت جدید روی پنل مقصد با حجم/زمان/سقف کاربرِ باقی‌مانده؛ اکانت قبلی غیرفعال می‌شود ولی <b>کاربرانِ داخل پنل قبلی خودکار منتقل نمی‌شوند</b>."]
    rows = [[btn(f"تغییر لوکیشن توسط نماینده: {'✅ روشن' if on else '⛔️ خاموش'}", "adm:vpn:amvg", role="action"), btn("🔢 سقف دفعات", "adm:vpn:amvm", role="action")]]
    for p in panels:
        rows.append([btn(f"{'✅' if pflag(p, 'amove_to') else '⬜'} مقصد: {p['name']}", f"adm:vpn:amvt:{p['id']}", role="action")])
    if not panels:
        lines.append("\n⚠️ پنلی که از ساخت نماینده پشتیبانی کند ثبت نشده است.")
    rows.append([btn("🔁 جابه‌جایی خودکار (ادمین)", "adm:vpn:abkm", role="admin"), btn(f"↩️ بازگشت جابه‌جایی‌ها ({nb})" if nb else "↩️ بازگشت جابه‌جایی‌ها", "adm:vpn:abb", role="admin")])
    rows.append(back_row("adm:vpn:st"))
    await view(cb, "\n".join(lines), ikb(rows))


@router.callback_query(F.data == "adm:vpn:amvs")
async def cb_move_screen(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await _move_screen(cb, shop)
    await cb.answer()


@router.callback_query(F.data == "adm:vpn:amvg")
async def cb_move_toggle(cb: CallbackQuery, shop: Shop):
    await shop.set_setting("vpn_agent_move", "0" if shop.s.get("vpn_agent_move", "0") == "1" else "1", f"tg:{cb.from_user.id}")
    await _move_screen(cb, shop)
    await cb.answer("انجام شد")


@router.callback_query(F.data == "adm:vpn:amvm")
async def cb_move_max(cb: CallbackQuery, shop: Shop):
    cur = int(shop.s.get("vpn_agent_move_max", "1") or 1)
    await shop.set_setting("vpn_agent_move_max", str({1: 2, 2: 3, 3: 0}.get(cur, 1)), f"tg:{cb.from_user.id}")
    await _move_screen(cb, shop)
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:vpn:amvt:"))
async def cb_move_dest(cb: CallbackQuery, shop: Shop):
    import json
    from vpn_service import pextra, pflag
    p = await shop.db.get_vpn_panel(int(cb.data.split(":")[3]))
    if not p:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    ex = dict(pextra(p))
    ex["amove_to"] = 0 if pflag(p, "amove_to") else 1
    await shop.db.update_vpn_panel(p["id"], extra=json.dumps(ex, ensure_ascii=False))
    await _move_screen(cb, shop)
    await cb.answer("انجام شد")


@router.callback_query(F.data == "adm:vpn:abb")
async def cb_batches(cb: CallbackQuery, shop: Shop):
    bs = await shop.db.vm_batches("agent", 15)
    pn = await _panels(shop)
    nm = lambda i: (pn.get(i) or {}).get("name", f"#{i}")  # noqa: E731
    rows = [[btn(f"{_jd(b['at'])} · {b['n_done']}/{b['n']} · {nm(b['from_panel'])} ➜ {nm(b['to_panel'])}", f"adm:vpn:abb:{b['batch']}", role="admin")] for b in bs]
    rows.append(back_row("adm:vpn:amvs"))
    await view(cb, "↩️ <b>بازگشت جابه‌جایی نماینده‌ها</b>\n" + ("هر مورد یک بار انتقال است؛ بزنید تا همه‌ی نماینده‌های آن دسته به اکانت و لوکیشن قبلی برگردند." if bs else "هنوز انتقالی ثبت نشده است."), ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:abb:[A-Za-z0-9_.:-]+$"))
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
        pn = await _panels(shop)
        nm = lambda i: (pn.get(i) or {}).get("name", f"#{i}")  # noqa: E731
        r0 = rows_db[0]
        kb = [[btn(f"↩️ برگردان همه ({n_done})", f"adm:vpn:abb:{batch}:y", role="confirm")]] if n_done else []
        await view(cb, f"↩️ <b>جابه‌جایی {_jd(r0['at'])}</b>\n{nm(r0['from_panel'])} ➜ {nm(r0['to_panel'])}\nکل: {len(rows_db)} | هنوز روی مقصد: <b>{n_done}</b> | برگردانده‌شده: {len(rows_db) - n_done}\n\n"
                         "با «برگردان»، اکانت قبلی هر نماینده دوباره فعال می‌شود، اکانت جدید غیرفعال (نه حذف) و مشخصات برایش ارسال می‌شود. مصرفِ انجام‌شده روی اکانت جدید به مصرف قبلی اضافه می‌شود.",
                   ikb(kb + [back_row("adm:vpn:abb")]))
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
            res = await shop.vpn.agents.revert_batch(batch, actor=f"tg:{uid}")
        finally:
            job["running"] = False
        err = ("\n\n" + "\n".join(esc(x) for x in res["errors"][:20])) if res["errors"] else ""
        await shop.safe_send(chat, f"🏁 <b>بازگشت جابه‌جایی نماینده‌ها</b>\n✅ موفق: {res['ok']}\n❌ ناموفق: {res['fail']}{err}", reply_markup=ikb([back_row("adm:vpn:abb")]))
        await shop.audit(f"tg:{uid}", f"↩️ بازگشت جابه‌جایی نماینده‌ها {batch}: موفق {res['ok']}، ناموفق {res['fail']}")

    shop.spawn(run())
    await shop.db.log(f"tg:{uid}", "agent_revert_batch", f"{batch} n={n_done}")
    await cb.message.answer(f"🚀 بازگشت شروع شد ({fmt(n_done)} نماینده)؛ وقتی تمام شد گزارش می‌دهم.", reply_markup=ikb([back_row("adm:vpn:abb")]))
    await cb.answer()


# ---------------------------------------------------------------- یک نماینده: انتقال/بازگشت از کارت نماینده
@router.callback_query(F.data.regexp(r"^adm:vpn:amv:\d+$"))
async def cb_one_move(cb: CallbackQuery, shop: Shop):
    ag = await shop.db.get_agent(int(cb.data.split(":")[3]))
    if not ag:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    tg = await shop.vpn.agents.move_targets(ag, admin=True)
    if not tg:
        await cb.answer("پنل فعالِ دیگری که از نماینده پشتیبانی کند نیست.", show_alert=True)
        return
    rows = [[btn(f"📍 {p['name']}", f"adm:vpn:amg:{ag['id']}:{p['id']}", role="admin")] for p in tg]
    rows.append(back_row(f"adm:vpn:ag:{ag['id']}"))
    await view(cb, f"📍 <b>انتقال «{esc(ag['username'])}»</b>\nاکانت جدید با حجم/زمان/سقف کاربرِ باقی‌مانده روی مقصد ساخته می‌شود و اکانت قبلی غیرفعال (نه حذف) می‌شود. "
                   "⚠️ کاربران داخل پنل قبلی خودکار منتقل نمی‌شوند. مقصد؟", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:vpn:amg:\d+:\d+$"))
async def cb_one_move_go(cb: CallbackQuery, shop: Shop):
    from admin_vpn_agent import agent_card
    _, _, _, aid, pid = cb.data.split(":")
    await cb.answer("در حال انتقال ...")
    try:
        ag = await shop.vpn.agents.move(int(aid), int(pid), actor=f"tg:{cb.from_user.id}")
    except MoveError as e:
        await cb.message.answer(f"❌ {esc(str(e))}", reply_markup=ikb([back_row(f"adm:vpn:ag:{aid}")]))
        return
    await shop.vpn.agents.notify_moved(ag)
    text, kb = await agent_card(shop, ag)
    await view(cb, "✅ منتقل شد و مشخصات جدید برای نماینده ارسال شد.\n\n" + text, kb)


@router.callback_query(F.data.regexp(r"^adm:vpn:arv:\d+(:y)?$"))
async def cb_one_revert(cb: CallbackQuery, shop: Shop):
    from admin_vpn_agent import agent_card
    parts = cb.data.split(":")
    aid = int(parts[3])
    last = await shop.db.vm_last("agent", aid)
    if not last:
        await cb.answer("انتقال ثبت‌شده‌ای برای برگرداندن نیست.", show_alert=True)
        return
    if len(parts) == 4:
        await view(cb, "↩️ اکانت قبلی نماینده دوباره فعال می‌شود، اکانت جدید غیرفعال (نه حذف) و مشخصات برایش ارسال می‌شود. ادامه؟",
                   ikb([[btn("✅ بله، برگردان", f"adm:vpn:arv:{aid}:y", role="confirm"), btn("❌ خیر", f"adm:vpn:ag:{aid}", role="back")]]))
        await cb.answer()
        return
    await cb.answer("در حال برگرداندن ...")
    try:
        ag = await shop.vpn.agents.revert_move(last["id"], actor=f"tg:{cb.from_user.id}")
    except MoveError as e:
        await cb.message.answer(f"❌ {esc(str(e))}", reply_markup=ikb([back_row(f"adm:vpn:ag:{aid}")]))
        return
    await shop.vpn.agents.notify_moved(ag, back=True)
    text, kb = await agent_card(shop, ag)
    await view(cb, "✅ برگشت داده شد و مشخصات برای نماینده ارسال شد.\n\n" + text, kb)
