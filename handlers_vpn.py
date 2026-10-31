"""بخش VPN در ربات برای کاربر: خرید، سرویس‌های من، تمدید، حجم/زمان اضافه، QR، لینک جدید و اکانت تست."""
import json
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from handlers_user import order_text
from i18n import N_
from service import Shop
from ui import btn, esc, fmt, ikb
from vpn_panels import PanelError
from vpn_service import STATUS_FA, VpnError, ltr

router = Router()
router.message.filter(F.chat.type == "private")

PER_PAGE = 8


class VpnSt(StatesGroup):
    name = State()
    agent_name = State()
    custom_amount = State()


def _plan_label(p, tr, price) -> str:
    gb = tr("نامحدود") if not p["gb"] else ltr(f"{p['gb']:g} GB")
    lab = f"{p['title']} | {tr('{d} روز', d=p['days']) if p['days'] else tr('بدون انقضا')} | {gb} | {fmt(price)} {tr('ت')}"
    return lab + (" | " + tr("❌ ناموجود") if p.get("out_of_stock") else "")


async def _offer(target, shop: Shop, tr, oid: int, uid: int):
    o = await shop.db.get_order(oid)
    msg = target.message if isinstance(target, CallbackQuery) else target
    await msg.answer(order_text(o, tr, o["title"], shop), reply_markup=await shop.pay_methods_kb(str(oid), uid, allow_wallet=True, tr=tr))


async def _own(cb: CallbackQuery, shop: Shop, tr, sid):
    svc = await shop.db.get_vpn_service(int(sid))
    if not svc or svc["user_id"] != cb.from_user.id or svc["status"] == "deleted":
        await cb.answer(tr("سرویس پیدا نشد."), show_alert=True)
        return None
    return svc


# ---------- خرید ----------
@router.callback_query(F.data == "vpn")
async def vpn_home(cb: CallbackQuery, shop: Shop, tr):
    if not shop.vpn.enabled():
        await cb.answer(tr("فروش VPN فعال نیست."), show_alert=True)
        return
    u = await shop.db.get_user(cb.from_user.id)
    plans = await shop.vpn.plans_for(u)
    rows = [[btn(_plan_label(p, tr, p["user_price"]), f"vpn:p:{p['id']}", role="action")] for p in plans[:40]]
    if await shop.vpn.can_test(cb.from_user.id):
        rows.append([btn(tr("🎁 دریافت اکانت تست رایگان"), "vpn:test", role="action")])
    rows.append([btn(tr("📡 سرویس‌های من"), "vpn:my", role="action")])
    rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    head = tr("🛡 <b>خرید VPN</b>") + "\n" + (tr("یکی از پلن‌ها را انتخاب کنید:") if plans else tr("در حال حاضر پلنی برای فروش موجود نیست."))
    await cb.message.answer(head, reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:p:"))
async def vpn_pick(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    plan_id = int(cb.data.split(":")[2])
    try:
        if (shop.s.get("vpn_name_mode") or "random") == "ask":
            plan = await shop.db.get_vpn_plan(plan_id)
            if not plan or not plan["active"]:
                raise VpnError(N_("این پلن دیگر در دسترس نیست."))
            await state.set_state(VpnSt.name)
            await state.update_data(plan_id=plan_id)
            await cb.message.answer(tr("نام کاربری دلخواه برای سرویس را بفرستید (۳ تا ۲۴ حرف کوچک انگلیسی، عدد یا _). برای انصراف /start:"))
            await cb.answer()
            return
        oid = await shop.vpn.create_order(cb.from_user.id, plan_id)
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()


@router.message(VpnSt.name, F.text)
async def vpn_name(m: Message, shop: Shop, state: FSMContext, tr):
    data = await state.get_data()
    try:
        oid = await shop.vpn.create_order(m.from_user.id, data["plan_id"], m.text.strip().lower())
    except VpnError as e:
        await m.answer(tr(str(e)))
        return
    await state.clear()
    await _offer(m, shop, tr, oid, m.from_user.id)


# ---------- اکانت تست ----------
@router.callback_query(F.data == "vpn:test")
async def vpn_test(cb: CallbackQuery, shop: Shop, tr):
    try:
        sid = await shop.vpn.create_test(cb.from_user.id)
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    svc = await shop.db.get_vpn_service(sid)
    await shop.vpn.send_service(cb.from_user.id, svc, new=True)
    await cb.answer()


# ---------- سرویس‌های من ----------
async def _list(cb: CallbackQuery, shop: Shop, tr, page=0):
    uid = cb.from_user.id
    total = await shop.db.count_vpn_services(user_id=uid)
    if not total:
        if await shop.db.count_agents(user_id=uid):
            await _agent_list(cb, shop, tr)
            return
        await cb.answer(tr("هنوز سرویسی ندارید."), show_alert=True)
        return
    rows_db = await shop.db.list_vpn_services(user_id=uid, limit=PER_PAGE, offset=page * PER_PAGE)
    rows = [[btn(f"{tr(STATUS_FA.get(s['status'], s['status']))[:2]} {s['name']}" + (" 🎁" if s["is_test"] else ""), f"vpn:s:{s['id']}", role="action")] for s in rows_db]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"vpn:pg:{page - 1}", role="back"))
    if (page + 1) * PER_PAGE < total:
        nav.append(btn("▶️", f"vpn:pg:{page + 1}", role="action"))
    if nav:
        rows.append(nav)
    rows.append([btn(tr("🛒 خرید سرویس جدید"), "vpn", role="action"), btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    await cb.message.answer(tr("📡 <b>سرویس‌های VPN شما</b>"), reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data == "vpn:my")
async def vpn_my(cb: CallbackQuery, shop: Shop, tr):
    await _list(cb, shop, tr)


@router.callback_query(F.data.startswith("vpn:pg:"))
async def vpn_page(cb: CallbackQuery, shop: Shop, tr):
    await _list(cb, shop, tr, int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("vpn:s:"))
async def vpn_show(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if time.time() - (svc["last_sync"] or 0) > 60:
        try:
            svc = await shop.vpn.sync(svc, force=False)
        except Exception:
            pass
    await shop.vpn.send_service(cb.from_user.id, svc, tr=tr)
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:rf:"))
async def vpn_refresh(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    try:
        svc = await shop.vpn.sync(svc)
    except (PanelError, Exception):
        await cb.answer(tr("ارتباط با سرور برقرار نشد؛ کمی بعد دوباره تلاش کنید."), show_alert=True)
        return
    try:
        if cb.message.photo:
            _c = await shop.vpn.service_text(svc, tr, all_cfg=True)
            await cb.message.edit_caption(caption=_c if len(_c) <= 1024 else (await shop.vpn.service_text(svc, tr, compact=True))[:1024], reply_markup=shop.vpn.service_kb(svc, tr))
        else:
            _t = await shop.vpn.service_text(svc, tr, all_cfg=True)
            await cb.message.edit_text(_t if len(_t) <= 4000 else await shop.vpn.service_text(svc, tr), reply_markup=shop.vpn.service_kb(svc, tr), disable_web_page_preview=True)
    except Exception:
        pass
    await cb.answer(tr("بروزرسانی شد."))


@router.callback_query(F.data.startswith("vpn:qr:"))
async def vpn_qr(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if not await shop.vpn.send_qr(cb.from_user.id, svc):
        await cb.answer(tr("QR برای این سرویس در دسترس نیست."), show_alert=True)
        return
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:rv:"))
async def vpn_revoke_ask(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if shop.s.get("vpn_allow_revoke", "1") != "1":
        await cb.answer(tr("این قابلیت غیرفعال است."), show_alert=True)
        return
    await cb.message.answer(tr("⚠️ با ساخت لینک جدید، لینک فعلی دیگر کار نمی‌کند. ادامه می‌دهید؟"),
                            reply_markup=ikb([[btn(tr("✅ بله، لینک جدید"), f"vpn:rv2:{svc['id']}", role="confirm"), btn(tr("❌ انصراف"), f"vpn:s:{svc['id']}", role="reject")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:rv2:"))
async def vpn_revoke(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc or shop.s.get("vpn_allow_revoke", "1") != "1":
        return
    ok, msg = await shop.vpn.admin_action(svc["id"], "revoke", actor=f"user:{cb.from_user.id}")
    if not ok:
        await cb.answer(tr("انجام نشد؛ بعداً دوباره تلاش کنید."), show_alert=True)
        return
    svc = await shop.db.get_vpn_service(svc["id"])
    await shop.vpn.send_service(cb.from_user.id, svc, tr=tr)
    await cb.answer()


# ---------- تمدید ----------
@router.callback_query(F.data.startswith("vpn:rn:"))
async def vpn_renew(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    u = await shop.db.get_user(cb.from_user.id)
    plans = [p for p in await shop.vpn.plans_for(u) if p["panel_id"] == svc["panel_id"]]
    if not plans:
        await cb.answer(tr("پلنی برای تمدید این سرویس موجود نیست."), show_alert=True)
        return
    rows = [[btn(_plan_label(p, tr, p["user_price"]), f"vpn:rnp:{svc['id']}:{p['id']}", role="action")] for p in plans[:30]]
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:s:{svc['id']}", role="back")])
    await cb.message.answer(tr("🔄 تمدید سرویس {n}", n=f"<code>{esc(svc['name'])}</code>") + "\n" + tr("پلن مورد نظر را انتخاب کنید:"), reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:rnp:"))
async def vpn_renew_pick(cb: CallbackQuery, shop: Shop, tr):
    _, _, sid, pid = cb.data.split(":")
    try:
        oid = await shop.vpn.renew_order(cb.from_user.id, int(sid), int(pid))
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()


# ---------- تمدید خودکار (انتخاب کاربر) و تمدید یک‌کلیکی ----------
@router.callback_query(F.data.startswith("vpn:au:"))
async def vpn_auto_toggle(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if not shop.vpn._can_auto(svc):
        await cb.answer(tr("تمدید خودکار برای این سرویس در دسترس نیست."), show_alert=True)
        return
    on = not bool(json.loads(svc["meta"] or "{}").get("auto"))
    await shop.vpn.set_auto(svc, on)
    svc = await shop.db.get_vpn_service(svc["id"])
    try:
        await cb.message.edit_reply_markup(reply_markup=shop.vpn.service_kb(svc, tr))
    except Exception:  # noqa: BLE001
        pass
    if on:
        plan = await shop.db.get_vpn_plan(svc["plan_id"])
        user = await shop.db.get_user(cb.from_user.id)
        price = shop._personal(plan["price"], user) if plan else 0
        await cb.answer(tr("🔁 تمدید خودکار روشن شد. {d} روز مانده به پایان، {p} تومان از کیف پول کم می‌شود؛ موجودی را نگه دارید.",
                           d=max(int(float(shop.s.get("vpn_autorenew_days") or 1)), 0), p=fmt(price)), show_alert=True)
    else:
        await cb.answer(tr("تمدید خودکار خاموش شد."))


@router.callback_query(F.data.startswith("vpn:qk:"))
async def vpn_quick_renew(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if not svc["plan_id"]:
        await cb.answer(tr("پلنی برای تمدید این سرویس موجود نیست."), show_alert=True)
        return
    try:
        oid = await shop.vpn.renew_order(cb.from_user.id, svc["id"], svc["plan_id"])
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()


# ---------- حجم / زمان اضافه ----------
async def _swap_kb(cb: CallbackQuery, kb) -> bool:
    """کیبورد همین پیام (صفحه‌ی مشخصات) را عوض می‌کند تا کاربر از صفحه خارج نشود."""
    try:
        await cb.message.edit_reply_markup(reply_markup=kb)
        return True
    except Exception:  # noqa: BLE001
        return False


@router.callback_query(F.data.startswith("vpn:ad:"))
async def vpn_addons(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    u = await shop.db.get_user(cb.from_user.id)
    adds = await shop.db.list_vpn_addons(active_only=True)
    if not adds or shop.s.get("vpn_allow_addon", "1") != "1":
        await cb.answer(tr("بسته‌ی اضافه‌ای موجود نیست."), show_alert=True)
        return
    rows = [[btn(f"{a['title']} | {fmt(shop._personal(a['price'], u))} {tr('ت')}", f"vpn:adp:{svc['id']}:{a['id']}", role="action")] for a in adds[:30]]
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:adb:{svc['id']}", role="back")])
    if not await _swap_kb(cb, ikb(rows)):
        await cb.message.answer(tr("➕ بسته‌ی اضافه برای {n}", n=f"<code>{esc(svc['name'])}</code>") + "\n" + tr("یکی را انتخاب کنید:"), reply_markup=ikb(rows))
    await cb.answer(tr("یکی از بسته‌ها را انتخاب کنید."))


@router.callback_query(F.data.startswith("vpn:adb:"))
async def vpn_addons_back(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    await _swap_kb(cb, shop.vpn.service_kb(svc, tr))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:adp:"))
async def vpn_addon_pick(cb: CallbackQuery, shop: Shop, tr):
    _, _, sid, aid = cb.data.split(":")
    try:
        oid = await shop.vpn.addon_order(cb.from_user.id, int(sid), int(aid))
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()


# ======================================================================= پنل نمایندگی
async def _own_agent(cb: CallbackQuery, shop: Shop, tr, aid):
    ag = await shop.db.get_agent(int(aid))
    if not ag or ag["user_id"] != cb.from_user.id or ag["status"] == "deleted":
        await cb.answer(tr("نماینده پیدا نشد."), show_alert=True)
        return None
    return ag


def _agent_plan_text(p, tr, price) -> str:
    from vpn_agent import plan_features
    return f"🧑‍💼 <b>{esc(p['title'])}</b>\n" + "\n".join("• " + x for x in plan_features(p, tr)) + (f"\n{esc(p['note'])}" if p.get("note") else "") + \
        f"\n💰 {fmt(price)} {tr('تومان')}"


@router.callback_query(F.data == "vpn:agh")
async def agent_home(cb: CallbackQuery, shop: Shop, tr):
    if not shop.vpn.agents.enabled():
        await cb.answer(tr("فروش پنل نمایندگی فعال نیست."), show_alert=True)
        return
    u = await shop.db.get_user(cb.from_user.id)
    plans = await shop.vpn.agents.plans_for(u)
    head = tr("🧑‍💼 <b>خرید پنل نمایندگی</b>") + "\n" + (tr("پلن مورد نظر را انتخاب کنید؛ پس از پرداخت، پنل شما خودکار ساخته و اطلاعات ورود ارسال می‌شود.") if plans else tr("در حال حاضر پلنی برای فروش موجود نیست."))
    rows = [[btn(f"{p['title']} | {fmt(p['user_price'])} {tr('ت')}", f"vpn:agp:{p['id']}", role="action")] for p in plans[:40]]
    rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    await cb.message.answer(head, reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:agp:"))
async def agent_pick(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    plan_id = int(cb.data.split(":")[2])
    try:
        plan = await shop.db.get_agent_plan(plan_id)
        if not plan or not plan["active"]:
            raise VpnError(N_("این پلن دیگر در دسترس نیست."))
        if (shop.s.get("vpn_agent_name_mode") or "random") == "ask":
            await state.set_state(VpnSt.agent_name)
            await state.update_data(plan_id=plan_id)
            await cb.message.answer(_agent_plan_text(plan, tr, shop._personal(plan["price"], await shop.db.get_user(cb.from_user.id))) + "\n\n" +
                                    tr("نام کاربری دلخواه برای ادمین پنل را بفرستید (۳ تا ۲۴ حرف کوچک انگلیسی، عدد یا _). برای انصراف /start:"))
            await cb.answer()
            return
        oid = await shop.vpn.agents.create_order(cb.from_user.id, plan_id)
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()


@router.message(VpnSt.agent_name, F.text)
async def agent_name(m: Message, shop: Shop, state: FSMContext, tr):
    data = await state.get_data()
    try:
        oid = await shop.vpn.agents.create_order(m.from_user.id, data["plan_id"], m.text.strip().lower())
    except VpnError as e:
        await m.answer(tr(str(e)))
        return
    await state.clear()
    await _offer(m, shop, tr, oid, m.from_user.id)


async def _agent_list(cb: CallbackQuery, shop: Shop, tr):
    uid = cb.from_user.id
    items = await shop.db.list_agents(user_id=uid, limit=30)
    if not items:
        await cb.answer(tr("هنوز پنل نمایندگی ندارید."), show_alert=True)
        return
    rows = [[btn(f"{tr(STATUS_FA.get(a['status'], a['status']))[:2]} {a['username']}", f"vpn:ag:{a['id']}", role="action")] for a in items]
    rows.append([btn(tr("🛒 خرید پنل نمایندگی"), "vpn:agh", role="action"), btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    await cb.message.answer(tr("🗂 <b>پنل‌های نمایندگی شما</b>"), reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data == "vpn:agl")
async def agent_list(cb: CallbackQuery, shop: Shop, tr):
    await _agent_list(cb, shop, tr)


@router.callback_query(F.data.startswith("vpn:ag:"))
async def agent_show(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    if time.time() - (ag["last_sync"] or 0) > 60:
        try:
            ag = await shop.vpn.agents.sync(ag, force=False)
            ag = await shop.db.get_agent(ag["id"])
        except Exception:
            pass
    await cb.message.answer(await shop.vpn.agents.agent_text(ag, tr), reply_markup=await shop.vpn.agents.agent_kb(ag, tr), disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:agr:"))
async def agent_refresh(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    try:
        await shop.vpn.agents.sync(ag)
    except Exception:
        await cb.answer(tr("ارتباط با سرور برقرار نشد؛ کمی بعد دوباره تلاش کنید."), show_alert=True)
        return
    ag = await shop.db.get_agent(ag["id"])
    try:
        await cb.message.edit_text(await shop.vpn.agents.agent_text(ag, tr), reply_markup=await shop.vpn.agents.agent_kb(ag, tr), disable_web_page_preview=True)
    except Exception:
        pass
    await cb.answer(tr("بروزرسانی شد."))


@router.callback_query(F.data.startswith("vpn:agw:"))
async def agent_pw_ask(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    await cb.message.answer(tr("⚠️ رمز فعلی پنل تغییر می‌کند و رمز جدید برای شما ارسال می‌شود. ادامه می‌دهید؟"),
                            reply_markup=ikb([[btn(tr("✅ بله، رمز جدید"), f"vpn:agw2:{ag['id']}", role="confirm"), btn(tr("❌ انصراف"), f"vpn:ag:{ag['id']}", role="reject")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:agw2:"))
async def agent_pw(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag or ag["status"] in ("expired", "disabled"):
        await cb.answer(tr("انجام نشد؛ بعداً دوباره تلاش کنید."), show_alert=True)
        return
    ok, _msg = await shop.vpn.agents.admin_action(ag["id"], "password", actor=f"user:{cb.from_user.id}")
    if not ok:
        await cb.answer(tr("انجام نشد؛ بعداً دوباره تلاش کنید."), show_alert=True)
        return
    await shop.vpn.agents.send_agent(cb.from_user.id, await shop.db.get_agent(ag["id"]))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:agt:"))
async def agent_auto_toggle(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    if not shop.vpn.agents._can_auto(ag):
        await cb.answer(tr("تمدید خودکار برای این پنل در دسترس نیست."), show_alert=True)
        return
    on = not shop.vpn.agents.is_auto(ag)
    await shop.vpn.agents.set_auto(ag, on)
    ag = await shop.db.get_agent(ag["id"])
    try:
        await cb.message.edit_text(await shop.vpn.agents.agent_text(ag, tr), reply_markup=await shop.vpn.agents.agent_kb(ag, tr), disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        pass
    if on:
        plan = await shop.db.get_agent_plan(ag["plan_id"])
        user = await shop.db.get_user(cb.from_user.id)
        price = shop._personal(plan["price"], user) if plan else 0
        await cb.answer(tr("🔁 تمدید خودکار روشن شد. {d} روز مانده به پایان، {p} تومان از کیف پول کم می‌شود؛ موجودی را نگه دارید.",
                           d=max(int(float(shop.s.get("vpn_autorenew_days") or 1)), 0), p=fmt(price)), show_alert=True)
    else:
        await cb.answer(tr("تمدید خودکار خاموش شد."))


@router.callback_query(F.data.startswith("vpn:agq:"))
async def agent_quick_renew(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    if not ag["plan_id"]:
        await cb.answer(tr("پلنی برای تمدید موجود نیست."), show_alert=True)
        return
    try:
        oid = await shop.vpn.agents.renew_order(cb.from_user.id, ag["id"], ag["plan_id"])
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:aga:"))
async def agent_addons(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    u = await shop.db.get_user(cb.from_user.id)
    adds = [a for a in await shop.vpn.agents.addons_for(ag) if not (a["kind"] == "gb" and not ag["total_bytes"])]
    if not adds:
        await cb.answer(tr("بسته‌ی اضافه‌ای موجود نیست."), show_alert=True)
        return
    rows = [[btn(f"{shop.vpn.agents.addon_title(a, tr)} | {fmt(shop._personal(a['price'], u))} {tr('ت')}", f"vpn:agap:{ag['id']}:{a['id']}", role="action")] for a in adds[:30]]
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:agab:{ag['id']}", role="back")])
    if not await _swap_kb(cb, ikb(rows)):
        await cb.message.answer(tr("➕ بسته‌ی اضافه برای {n}", n=f"<code>{esc(ag['username'])}</code>") + "\n" + tr("یکی را انتخاب کنید:"), reply_markup=ikb(rows))
    await cb.answer(tr("یکی از بسته‌ها را انتخاب کنید."))


@router.callback_query(F.data.startswith("vpn:agab:"))
async def agent_addons_back(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    await _swap_kb(cb, await shop.vpn.agents.agent_kb(ag, tr))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:agc:"))
async def agent_custom_ask(cb: CallbackQuery, shop: Shop, tr, state: FSMContext):
    _, _, aid, kind = cb.data.split(":")
    ag = await _own_agent(cb, shop, tr, aid)
    if not ag:
        return
    cfg = (await shop.vpn.agents.custom_for(ag)).get(kind)
    if not cfg:
        await cb.answer(tr("این امکان دیگر در دسترس نیست."), show_alert=True)
        return
    u = await shop.db.get_user(cb.from_user.id)
    unit = shop._personal(cfg["price"], u)
    await state.set_state(VpnSt.custom_amount)
    await state.update_data(agent_id=ag["id"], kind=kind)
    if kind == "gb":
        txt = tr("📦 <b>خرید حجم اضافه</b>\nحداقل حجم خرید: {a} گیگابایت\nحداکثر حجم خرید: {b} گیگابایت\nقیمت هر گیگابایت: {p} تومان\n\nمقدار دلخواه را (فقط عدد، به گیگابایت) بفرستید. برای انصراف /start:", a=f"{cfg['min']:g}", b=f"{cfg['max']:g}", p=fmt(unit))
    else:
        txt = tr("⏳ <b>خرید زمان اضافه</b>\nحداقل زمان خرید: {a} روز\nحداکثر زمان خرید: {b} روز\nقیمت هر روز: {p} تومان\n\nمقدار دلخواه را (فقط عدد، به روز) بفرستید. برای انصراف /start:", a=f"{cfg['min']:g}", b=f"{cfg['max']:g}", p=fmt(unit))
    await cb.message.answer(txt)
    await cb.answer()


@router.message(VpnSt.custom_amount, F.text)
async def agent_custom_amount(m: Message, shop: Shop, state: FSMContext, tr):
    d = await state.get_data()
    t = (m.text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬٫", "0123456789...")).replace(",", "")
    try:
        v = float(t)
        if v <= 0:
            raise ValueError
    except ValueError:
        await m.answer(tr("فقط یک عدد معتبر بفرستید."))
        return
    ag = await shop.db.get_agent(d["agent_id"])
    cfg = (await shop.vpn.agents.custom_for(ag)).get(d["kind"]) if ag else None
    if cfg and not cfg["min"] <= v <= cfg["max"]:
        await m.answer(tr("مقدار خارج از بازه است؛ حداقل {a} و حداکثر {b} مجاز است.", a=f"{cfg['min']:g}", b=f"{cfg['max']:g}"))
        return
    try:
        oid = await shop.vpn.agents.custom_order(m.from_user.id, d["agent_id"], d["kind"], v)
    except VpnError as e:
        await m.answer(tr(str(e)))
        return
    await state.clear()
    await _offer(m, shop, tr, oid, m.from_user.id)


@router.callback_query(F.data.startswith("vpn:agap:"))
async def agent_addon_pick(cb: CallbackQuery, shop: Shop, tr):
    _, _, aid, adid = cb.data.split(":")
    try:
        oid = await shop.vpn.agents.addon_order(cb.from_user.id, int(aid), int(adid))
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:agn:"))
async def agent_renew(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    u = await shop.db.get_user(cb.from_user.id)
    plans = [p for p in await shop.vpn.agents.plans_for(u) if p["panel_id"] == ag["panel_id"]]
    if not plans:
        await cb.answer(tr("پلنی برای تمدید موجود نیست."), show_alert=True)
        return
    rows = [[btn(f"{p['title']} | {fmt(p['user_price'])} {tr('ت')}", f"vpn:agnp:{ag['id']}:{p['id']}", role="action")] for p in plans[:30]]
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:ag:{ag['id']}", role="back")])
    await cb.message.answer(tr("🔄 تمدید پنل نمایندگی {n}", n=f"<code>{esc(ag['username'])}</code>") + "\n" + tr("پلن مورد نظر را انتخاب کنید:"), reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:agnp:"))
async def agent_renew_pick(cb: CallbackQuery, shop: Shop, tr):
    _, _, aid, pid = cb.data.split(":")
    try:
        oid = await shop.vpn.agents.renew_order(cb.from_user.id, int(aid), int(pid))
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    await _offer(cb, shop, tr, oid, cb.from_user.id)
    await cb.answer()
