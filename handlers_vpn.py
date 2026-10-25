"""بخش VPN در ربات برای کاربر: خرید، سرویس‌های من، تمدید، حجم/زمان اضافه، QR، لینک جدید و اکانت تست."""
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


def _plan_label(p, tr, price) -> str:
    gb = tr("نامحدود") if not p["gb"] else ltr(f"{p['gb']:g} GB")
    return f"{p['title']} | {tr('{d} روز', d=p['days']) if p['days'] else tr('بدون انقضا')} | {gb} | {fmt(price)} {tr('ت')}"


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
    await cb.message.answer(await shop.vpn.service_text(svc, tr), reply_markup=shop.vpn.service_kb(svc, tr), disable_web_page_preview=True)
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
        await cb.message.edit_text(await shop.vpn.service_text(svc, tr), reply_markup=shop.vpn.service_kb(svc, tr), disable_web_page_preview=True)
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
    await shop.vpn.send_service(cb.from_user.id, svc)
    await shop.vpn.send_qr(cb.from_user.id, svc)
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


# ---------- حجم / زمان اضافه ----------
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
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:s:{svc['id']}", role="back")])
    await cb.message.answer(tr("➕ بسته‌ی اضافه برای {n}", n=f"<code>{esc(svc['name'])}</code>") + "\n" + tr("یکی را انتخاب کنید:"), reply_markup=ikb(rows))
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
