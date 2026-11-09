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
from ui import plan_btn_kw, btn, esc, fmt, ikb
from vpn_ops import MoveError
from vpn_panels import PanelError
from vpn_service import STATUS_FA, VpnError, ltr, pflag

router = Router()
router.message.filter(F.chat.type == "private")

PER_PAGE = 8


class VpnSt(StatesGroup):
    note = State()
    transfer = State()
    report = State()
    name = State()
    agent_name = State()
    agent_cv = State()
    agent_cv_name = State()
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
def _by_panel(plans) -> dict:
    out: dict = {}
    for p in plans:
        out.setdefault(p["panel_id"], []).append(p)
    return out


async def _show_plans(cb: CallbackQuery, shop: Shop, tr, plans, panel_name="", multi=False):
    rows = []
    if plans:                                       # دو ستون مثل خرید نمایندگی: مشخصات | قیمت
        rows.append([btn(tr("🛡 پلن مناسب خود را انتخاب کنید"), "vpn:agnoop", role="confirm")])
        rows.append([btn(tr("📊 مشخصات"), "vpn:agnoop", role="back"), btn(tr("💲 قیمت"), "vpn:agnoop", role="back")])
    for p in plans[:40]:
        gb = tr("نامحدود") if not p["gb"] else ltr(f"{p['gb']:g}GB")
        spec = f"{p['title']} | {gb} | {tr('{d} روز', d=p['days']) if p['days'] else tr('بدون انقضا')}"
        price = tr("❌ ناموجود") if p.get("out_of_stock") else f"{fmt(p['user_price'])} {tr('تومان')}"
        rows.append([btn(spec, f"vpn:p:{p['id']}", role="action", **plan_btn_kw(p, "s")), btn(price, f"vpn:p:{p['id']}", role="action", **plan_btn_kw(p, "p"))])
    if multi:
        rows.append([btn(tr("🔙 انتخاب لوکیشن/پنل"), "vpn", role="back")])
    else:
        rows.append([btn(tr("📡 سرویس‌های من"), "vpn:my", role="action")])
        rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    head = tr("🛡 <b>خرید VPN</b>") + (f" — <b>{esc(panel_name)}</b>" if panel_name else "") + "\n" + (tr("یکی از پلن‌ها را انتخاب کنید:") if plans else tr("در حال حاضر پلنی برای فروش موجود نیست."))
    await cb.message.answer(head, reply_markup=ikb(rows))


@router.callback_query(F.data == "vpn")
async def vpn_home(cb: CallbackQuery, shop: Shop, tr):
    """اول پنل‌ها (مثلاً مستقیم / تانل / مولتی‌لوکیشن) را نشان می‌دهد؛ با انتخاب هر کدام فقط پلن‌های همان پنل می‌آید. با یک پنل مستقیم پلن‌ها نمایش داده می‌شود."""
    if not shop.vpn.enabled():
        await cb.answer(tr("فروش VPN فعال نیست."), show_alert=True)
        return
    u = await shop.db.get_user(cb.from_user.id)
    plans = await shop.vpn.plans_for(u)
    groups = _by_panel(plans)
    if len(groups) <= 1:
        await _show_plans(cb, shop, tr, plans)
        await cb.answer()
        return
    rows = [[btn(f"{g[0].get('panel_name') or pid} ({len(g)})", f"vpn:pn:{pid}", role="action")] for pid, g in groups.items()]
    rows.append([btn(tr("📡 سرویس‌های من"), "vpn:my", role="action")])
    rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    await cb.message.answer(tr("🛡 <b>خرید VPN</b>") + "\n" + tr("نوع سرویس / لوکیشن را انتخاب کنید:"), reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^vpn:pn:\d+$"))
async def vpn_panel(cb: CallbackQuery, shop: Shop, tr):
    pid = int(cb.data.split(":")[2])
    u = await shop.db.get_user(cb.from_user.id)
    plans = [p for p in await shop.vpn.plans_for(u) if p["panel_id"] == pid]
    if not plans:
        await cb.answer(tr("در حال حاضر پلنی برای فروش موجود نیست."), show_alert=True)
        return
    await _show_plans(cb, shop, tr, plans, plans[0].get("panel_name") or "", multi=True)
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
async def _give_test(cb: CallbackQuery, shop: Shop, tr, panel_id=None):
    try:
        sid = await shop.vpn.create_test(cb.from_user.id, panel_id)
    except VpnError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    svc = await shop.db.get_vpn_service(sid)
    await shop.vpn.send_service(cb.from_user.id, svc, new=True)
    await cb.answer()


class MsgAsCb:
    """آداپتور: هندلرهای callback را برای پیام عادی (مثل لینک start=usertest) هم قابل‌استفاده می‌کند."""

    def __init__(self, m: Message):
        self.message, self.from_user = m, m.from_user

    async def answer(self, text=None, show_alert=False, **_kw):
        if text:
            await self.message.answer(text)


@router.callback_query(F.data.in_({"vpn:test", "vpn:tests"}))
async def vpn_tests(cb: CallbackQuery, shop: Shop, tr):
    """دکمه‌ی جدای «اکانت تست رایگان»: همیشه دیده می‌شود. اگر چند پنل تست روشن است ابتدا انتخاب پنل؛ اگر کاربر سهمش را گرفته باشد پیام «استفاده کردید»."""
    panels = await shop.vpn.test_panels(cb.from_user.id)
    if not panels:
        await cb.answer(tr("اکانت تست در حال حاضر در دسترس نیست."), show_alert=True)
        return
    if len(panels) > 1:
        rows = [[btn(("✅ " if used else "🎁 ") + p["name"] + (" — " + tr("استفاده شده") if used else ""), f"vpn:test:{p['id']}", role="action")] for p, used in panels[:30]]
        rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
        await cb.message.answer(tr("🎁 <b>اکانت تست رایگان</b>\nپنل (لوکیشن) مورد نظر را انتخاب کنید:"), reply_markup=ikb(rows))
        await cb.answer()
        return
    await _give_test(cb, shop, tr, panels[0][0]["id"])


@router.callback_query(F.data.regexp(r"^vpn:test:\d+$"))
async def vpn_test_panel(cb: CallbackQuery, shop: Shop, tr):
    await _give_test(cb, shop, tr, int(cb.data.split(":")[2]))


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
    if time.time() - (svc["last_sync"] or 0) > 3:
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
    await shop.vpn.ops.refresh_dest()
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
    if err := shop.vpn.limit_error(svc, "revoke"):
        await cb.answer(tr(err), show_alert=True)
        return
    from vpn_service import plimit
    wait = shop.vpn.revoke_wait(svc["id"], (plimit(svc, "revoke_gap") or 2) * 60)
    if wait:
        await cb.answer(tr("لطفاً {s} ثانیه‌ی دیگر دوباره تلاش کنید.", s=wait), show_alert=True)
        return
    ok, msg = await shop.vpn.admin_action(svc["id"], "revoke", actor=f"user:{cb.from_user.id}")
    if not ok:
        await cb.answer(tr("انجام نشد؛ بعداً دوباره تلاش کنید."), show_alert=True)
        return
    await shop.vpn.count_use(svc["id"], "revoke")
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
    kind = cb.data.split(":")[3] if cb.data.count(":") >= 3 else ""
    if kind == "gb":
        adds = [a for a in adds if a["gb"] > 0]
    elif kind == "days":
        adds = [a for a in adds if a["days"] > 0]
    custom = shop.vpn.custom_cfg(svc)
    if kind in ("gb", "days"):
        custom = {k: v for k, v in custom.items() if k == kind}
    if (not adds and not custom) or shop.s.get("vpn_allow_addon", "1") != "1":
        await cb.answer(tr("بسته‌ی اضافه‌ای موجود نیست."), show_alert=True)
        return
    rows = [[btn(f"{a['title']} | {fmt(shop._personal(a['price'], u))} {tr('ت')}", f"vpn:adp:{svc['id']}:{a['id']}", role="action")] for a in adds[:30]]
    rows += [[btn(tr("✏️ حجم اضافه دلخواه") if k == "gb" else tr("✏️ زمان اضافه دلخواه"), f"vpn:adc:{svc['id']}:{k}", role="action")] for k in custom]
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:adb:{svc['id']}", role="back")])
    if not await _swap_kb(cb, ikb(rows)):
        await cb.message.answer(tr("➕ بسته‌ی اضافه برای {n}", n=f"<code>{esc(svc['name'])}</code>") + "\n" + tr("یکی را انتخاب کنید:"), reply_markup=ikb(rows))
    await cb.answer(tr("یکی از بسته‌ها را انتخاب کنید."))


@router.callback_query(F.data.startswith("vpn:adc:"))
async def vpn_custom_ask(cb: CallbackQuery, shop: Shop, tr, state: FSMContext):
    _, _, sid, kind = cb.data.split(":")
    svc = await _own(cb, shop, tr, sid)
    if not svc:
        return
    cfg = shop.vpn.custom_cfg(svc).get(kind)
    if not cfg:
        await cb.answer(tr("این امکان دیگر در دسترس نیست."), show_alert=True)
        return
    u = await shop.db.get_user(cb.from_user.id)
    unit = shop._personal(cfg["price"], u)
    await state.set_state(VpnSt.custom_amount)
    await state.update_data(sid=svc["id"], kind=kind)
    if kind == "gb":
        txt = tr("📦 <b>خرید حجم اضافه</b>\nحداقل حجم خرید: {a} گیگابایت\nحداکثر حجم خرید: {b} گیگابایت\nقیمت هر گیگابایت: {p} تومان\n\nمقدار دلخواه را (فقط عدد، به گیگابایت) بفرستید. برای انصراف /start:", a=f"{cfg['min']:g}", b=f"{cfg['max']:g}", p=fmt(unit))
    else:
        txt = tr("⏳ <b>خرید زمان اضافه</b>\nحداقل زمان خرید: {a} روز\nحداکثر زمان خرید: {b} روز\nقیمت هر روز: {p} تومان\n\nمقدار دلخواه را (فقط عدد، به روز) بفرستید. برای انصراف /start:", a=f"{cfg['min']:g}", b=f"{cfg['max']:g}", p=fmt(unit))
    await cb.message.answer(txt)
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:sl:"))
async def vpn_sub_link(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if not svc["sub_url"] or not pflag(svc, "sub"):
        await cb.answer(tr("لینک اشتراک موجود نیست."), show_alert=True)
        return
    code = f"<code>{esc(svc['sub_url'])}</code>" if pflag(svc, "copy_sub") else esc(svc["sub_url"])
    await cb.message.answer(tr("🔗 لینک اشتراک (روی آن بزنید تا کپی شود):") + "\n" + code, disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:cf:"))
async def vpn_get_configs(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if not pflag(svc, "config"):
        await cb.answer(tr("ارسال کانفیگ برای این سرویس غیرفعال است."), show_alert=True)
        return
    try:
        await shop.vpn.refresh_links(svc)
    except Exception:  # noqa: BLE001
        pass
    svc = await shop.db.get_vpn_service(svc["id"])
    links = json.loads(svc["links"] or "[]")
    if not links:
        await cb.answer(tr("کانفیگ مستقیمی موجود نیست؛ از لینک اشتراک استفاده کنید."), show_alert=True)
        return
    tap = pflag(svc, "copy_cfg")
    for i in range(0, min(len(links), 100), 10):
        await cb.message.answer("\n\n".join((f"<code>{esc(x)}</code>" if tap else esc(x)) for x in links[i:i + 10]), disable_web_page_preview=True)
    await cb.answer()


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


# ---------- ابزارهای کاربر روی سرویس: خاموش/روشن، یادداشت، انتقال، بازگشت وجه، حذف منقضی، گزارش ----------
async def _refresh_card(cb: CallbackQuery, shop: Shop, tr, sid):
    svc = await shop.db.get_vpn_service(int(sid))
    if svc and svc["status"] != "deleted":
        await shop.vpn.send_service(cb.from_user.id, svc, tr=tr)


@router.callback_query(F.data.startswith("vpn:tg:"))
async def vpn_toggle(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    ok, msg = await shop.vpn.user_toggle(cb.from_user.id, svc["id"])
    if not ok:
        await cb.answer(tr(msg), show_alert=True)
        return
    await cb.answer(tr("انجام شد."))
    await _refresh_card(cb, shop, tr, svc["id"])


@router.callback_query(F.data.startswith("vpn:nt:"))
async def vpn_note_ask(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    await state.set_state(VpnSt.note)
    await state.update_data(sid=svc["id"])
    await cb.message.answer(tr("📝 یادداشت جدید را بفرستید (حداکثر ۶۰ حرف). برای پاک کردن «-» بفرستید. برای انصراف /start"))
    await cb.answer()


@router.message(VpnSt.note, F.text)
async def vpn_note_set(m: Message, shop: Shop, state: FSMContext, tr):
    d = await state.get_data()
    await state.clear()
    text = "" if m.text.strip() == "-" else m.text.strip()
    if not await shop.vpn.user_set_note(m.from_user.id, d["sid"], text):
        await m.answer(tr("تغییر یادداشت برای این سرویس فعال نیست."))
        return
    svc = await shop.db.get_vpn_service(d["sid"])
    await m.answer(tr("✅ یادداشت ذخیره شد."))
    if svc:
        await shop.vpn.send_service(m.from_user.id, svc, tr=tr)


@router.callback_query(F.data.startswith("vpn:tf:"))
async def vpn_transfer_ask(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    await state.set_state(VpnSt.transfer)
    await state.update_data(sid=svc["id"])
    await cb.message.answer(tr("🚚 آیدی عددی تلگرام کاربر مقصد را بفرستید (او باید قبلاً ربات را استارت کرده باشد). برای انصراف /start"))
    await cb.answer()


@router.message(VpnSt.transfer, F.text)
async def vpn_transfer_target(m: Message, shop: Shop, state: FSMContext, tr):
    t = m.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    d = await state.get_data()
    if not t.isdigit():
        await m.answer(tr("فقط آیدی عددی بفرستید."))
        return
    await state.clear()
    svc = await shop.db.get_vpn_service(d["sid"])
    if not svc or svc["user_id"] != m.from_user.id or int(t) == m.from_user.id or not await shop.db.get_user(int(t)):
        await m.answer(tr("کاربر مقصد پیدا نشد (او باید قبلاً ربات را استارت کرده باشد)."))
        return
    await m.answer(tr("⚠️ سرویس {n} به کاربر {u} منتقل شود؟ پس از انتقال دیگر مال شما نیست.", n=f"<code>{esc(svc['name'])}</code>", u=f"<code>{t}</code>"),
                   reply_markup=ikb([[btn(tr("✅ بله، منتقل کن"), f"vpn:tf2:{svc['id']}:{t}", role="confirm"), btn(tr("❌ انصراف"), f"vpn:s:{svc['id']}", role="reject")]]))


@router.callback_query(F.data.startswith("vpn:tf2:"))
async def vpn_transfer_do(cb: CallbackQuery, shop: Shop, tr):
    _, _, sid, target = cb.data.split(":")
    svc = await _own(cb, shop, tr, sid)
    if not svc:
        return
    ok, msg = await shop.vpn.user_transfer(cb.from_user.id, svc["id"], int(target))
    await cb.answer(tr(msg), show_alert=True)
    if ok:
        await shop.say(int(target), "🚚 یک سرویس VPN به حساب شما منتقل شد: <code>{n}</code>. از «سرویس‌های من» ببینید.", n=esc(svc["name"]))
        await cb.message.answer(tr("✅ سرویس منتقل شد."))


@router.callback_query(F.data.startswith("vpn:rfd:"))
async def vpn_refund_ask(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    ok, _a, why = shop.vpn.refund_info(svc)
    if not ok:
        await cb.answer(tr(why), show_alert=True)
        return
    await cb.message.answer(tr("💸 با بازگشت وجه، سرویس حذف و مبلغ سفارش به کیف پول شما برمی‌گردد. ادامه می‌دهید؟"),
                            reply_markup=ikb([[btn(tr("✅ بله، بازگشت وجه"), f"vpn:rfd2:{svc['id']}", role="confirm"), btn(tr("❌ انصراف"), f"vpn:s:{svc['id']}", role="reject")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:rfd2:"))
async def vpn_refund_do(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    ok, msg = await shop.vpn.user_refund(cb.from_user.id, svc["id"])
    await cb.answer(tr(msg), show_alert=True)


@router.callback_query(F.data.startswith("vpn:dl:"))
async def vpn_delete_ask(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    await cb.message.answer(tr("🗑 این سرویس منقضی برای همیشه حذف شود؟"),
                            reply_markup=ikb([[btn(tr("✅ بله، حذف"), f"vpn:dl2:{svc['id']}", role="reject"), btn(tr("❌ انصراف"), f"vpn:s:{svc['id']}", role="back")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("vpn:dl2:"))
async def vpn_delete_do(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    ok, msg = await shop.vpn.user_delete_expired(cb.from_user.id, svc["id"])
    await cb.answer(tr(msg if not ok else "سرویس حذف شد."), show_alert=True)


# ---------- تغییر لوکیشن ----------
@router.callback_query(F.data.regexp(r"^vpn:mv:\d+$"))
async def vpn_move_list(cb: CallbackQuery, shop: Shop, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    if not await shop.vpn.ops.can_user_move(svc):
        await cb.answer(tr("تغییر لوکیشن برای این سرویس فعال نیست."), show_alert=True)
        return
    rows = [[btn(f"📍 {p['name']}", f"vpn:mvp:{svc['id']}:{p['id']}", role="action")] for p in await shop.vpn.ops.targets(svc)]
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:rf:{svc['id']}", role="back")])
    await cb.message.answer(tr("📍 <b>تغییر لوکیشن</b>") + "\n" + tr("لوکیشن جدید را انتخاب کنید. حجم و زمان باقی‌مانده‌ی شما همان‌طور که هست منتقل می‌شود."), reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^vpn:mvp:\d+:\d+$"))
async def vpn_move_confirm(cb: CallbackQuery, shop: Shop, tr):
    _, _, sid, pid = cb.data.split(":")
    svc = await _own(cb, shop, tr, sid)
    if not svc:
        return
    svc = await shop.vpn.sync_safe(svc)
    svc = await shop.db.get_vpn_service(svc["id"]) or svc
    dst = next((p for p in await shop.vpn.ops.targets(svc) if p["id"] == int(pid)), None)
    if not dst or not await shop.vpn.ops.can_user_move(svc):
        await cb.answer(tr("این لوکیشن در دسترس نیست."), show_alert=True)
        return
    try:
        gb, days = shop.vpn.ops.preview(svc)
    except MoveError as e:
        await cb.answer(tr(str(e)), show_alert=True)
        return
    vol = tr("نامحدود") if not gb else ltr(f"{gb:g} GB")
    tm = tr("نامحدود") if not days else tr("{d} روز", d=days)
    await cb.message.answer(tr("📍 انتقال به «{p}»", p=esc(dst["name"])) + "\n" + tr("حجم باقی‌مانده: {v}", v=vol) + "\n" + tr("زمان باقی‌مانده: {t}", t=tm)
                            + "\n\n" + tr("لینک اشتراک قبلی بعد از انتقال کار نمی‌کند و لینک جدید برای شما ارسال می‌شود. ادامه می‌دهید؟"),
                            reply_markup=ikb([[btn(tr("✅ بله، منتقل کن"), f"vpn:mvg:{sid}:{pid}", role="confirm"), btn(tr("❌ انصراف"), f"vpn:rf:{sid}", role="reject")],
                                                  [btn(tr("🔙 بازگشت"), f"vpn:mv:{sid}", role="back")]]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^vpn:mvg:\d+:\d+$"))
async def vpn_move_go(cb: CallbackQuery, shop: Shop, tr):
    _, _, sid, pid = cb.data.split(":")
    await cb.answer(tr("در حال انتقال ..."))
    try:
        row = await shop.vpn.ops.user_move(cb.from_user.id, int(sid), int(pid))
    except MoveError as e:
        await cb.message.answer("❌ " + tr(str(e)))
        return
    await cb.message.answer(tr("✅ سرویس شما به لوکیشن «{p}» منتقل شد؛ حجم و زمان باقی‌مانده حفظ شد.", p=esc(row.get("panel_name") or "")))
    await shop.vpn.send_service(cb.from_user.id, row)


@router.callback_query(F.data.startswith("vpn:rp:"))
async def vpn_report_ask(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    svc = await _own(cb, shop, tr, cb.data.split(":")[2])
    if not svc:
        return
    await state.set_state(VpnSt.report)
    await state.update_data(sid=svc["id"])
    await cb.message.answer(tr("⚠️ مشکل سرویس را کوتاه بنویسید (یا «-» برای ارسال بدون توضیح). برای انصراف /start"))
    await cb.answer()


@router.message(VpnSt.report, F.text)
async def vpn_report_send(m: Message, shop: Shop, state: FSMContext, tr):
    d = await state.get_data()
    await state.clear()
    ok, msg = await shop.vpn.user_report(m.from_user.id, d["sid"], "" if m.text.strip() == "-" else m.text.strip())
    await m.answer(("✅ " if ok else "❌ ") + tr(msg))


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
    head = tr("🧑‍💼 <b>خرید پنل نمایندگی</b>") + "\n" + (tr("پلن مورد نظر را انتخاب کنید؛ پس از پرداخت، پنل شما خودکار ساخته و اطلاعات ورود ارسال می‌شود.") if plans or await shop.vpn.agents.cv_cfg() else tr("در حال حاضر پلنی برای فروش موجود نیست."))
    noop = "vpn:agnoop"
    rows = []
    if plans:
        rows.append([btn(tr("🧑‍💼 پلن پنل نمایندگی را انتخاب کنید"), noop, role="confirm")])
        rows.append([btn(tr("📊 حجم"), noop, role="back"), btn(tr("💲 قیمت"), noop, role="back")])
    for p in plans[:40]:
        gb = tr("نامحدود") if not p["traffic_gb"] else ltr(f"{p['traffic_gb']:g}GB")
        rows.append([btn(gb, f"vpn:agp:{p['id']}", role="action", **plan_btn_kw(p, "s")), btn(f"{fmt(p['user_price'])} {tr('تومان')}", f"vpn:agp:{p['id']}", role="action", **plan_btn_kw(p, "p"))])
    cv = await shop.vpn.agents.cv_cfg()
    if cv:
        rows.append([btn(tr("➕ حجم دلخواه"), "vpn:agc", role="action"), btn(f"{fmt(shop._personal(cv['price'], u))} {tr('تومان')}", "vpn:agc", role="action")])
    rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    await cb.message.answer(head, reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data == "vpn:agnoop")
async def agent_noop(cb: CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "vpn:agc")
async def agent_cv_start(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    cv = await shop.vpn.agents.cv_cfg()
    if not cv:
        await cb.answer(tr("این امکان دیگر در دسترس نیست."), show_alert=True)
        return
    u = await shop.db.get_user(cb.from_user.id)
    await state.set_state(VpnSt.agent_cv)
    await cb.message.answer(tr("➕ <b>خرید پنل نمایندگی با حجم دلخواه</b>\nحداقل حجم: {a} گیگابایت\nحداکثر حجم: {b} گیگابایت\nقیمت هر گیگابایت: {p} تومان\n\nحجم دلخواه را (فقط عدد، به گیگابایت) بفرستید.",
                                a=f"{cv['min']:g}", b=f"{cv['max']:g}", p=fmt(shop._personal(cv["price"], u))),
                            reply_markup=ikb([[btn(tr("🔙 بازگشت"), "vpn:agh", role="back")]]))
    await cb.answer()


async def _cv_finish(m: Message, shop: Shop, state: FSMContext, tr, gb, name=None):
    try:
        oid = await shop.vpn.agents.create_order(m.from_user.id, 0, name, gb=gb)
    except VpnError as e:
        await m.answer(tr(str(e)))
        return
    await state.clear()
    await _offer(m, shop, tr, oid, m.from_user.id)


@router.message(VpnSt.agent_cv, F.text)
async def agent_cv_amount(m: Message, shop: Shop, state: FSMContext, tr):
    t = (m.text or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬٫", "0123456789...")).replace(",", "")
    try:
        gb = float(t)
        if gb <= 0:
            raise ValueError
    except ValueError:
        await m.answer(tr("فقط یک عدد معتبر بفرستید."))
        return
    cv = await shop.vpn.agents.cv_cfg()
    if cv and not cv["min"] <= gb <= cv["max"]:
        await m.answer(tr("مقدار خارج از بازه است؛ حداقل {a} و حداکثر {b} مجاز است.", a=f"{cv['min']:g}", b=f"{cv['max']:g}"))
        return
    if (shop.s.get("vpn_agent_name_mode") or "random") == "ask":
        await state.set_state(VpnSt.agent_cv_name)
        await state.update_data(gb=gb)
        await m.answer(tr("نام کاربری ادمین پنل را به‌صورت انگلیسی بفرستید (حداقل ۴ حرف؛ حروف کوچک انگلیسی، عدد یا _ ، حداکثر ۲۴). برای انصراف /start:"))
        return
    await _cv_finish(m, shop, state, tr, gb)


@router.message(VpnSt.agent_cv_name, F.text)
async def agent_cv_name(m: Message, shop: Shop, state: FSMContext, tr):
    d = await state.get_data()
    await _cv_finish(m, shop, state, tr, d.get("gb"), m.text.strip().lower())


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
                                    tr("نام کاربری ادمین پنل را به‌صورت انگلیسی بفرستید (حداقل ۴ حرف؛ حروف کوچک انگلیسی، عدد یا _ ، حداکثر ۲۴). برای انصراف /start:"))
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
    if time.time() - (ag["last_sync"] or 0) > 3:
        try:
            ag = await shop.vpn.agents.sync(ag, force=False)
            ag = await shop.db.get_agent(ag["id"])
        except Exception:
            pass
    await cb.message.answer(await shop.vpn.agents.agent_text(ag, tr), reply_markup=await shop.vpn.agents.agent_kb(ag, tr), disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^vpn:agm:\d+$"))
async def agent_move_list(cb: CallbackQuery, shop: Shop, tr):
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    ags = shop.vpn.agents
    tg = await ags.move_targets(ag) if ags.can_user_move(ag) else []
    if not tg:
        await cb.answer(tr("تغییر لوکیشن برای این پنل نمایندگی فعال نیست."), show_alert=True)
        return
    rows = [[btn(f"📍 {p['name']}", f"vpn:agmp:{ag['id']}:{p['id']}", role="action")] for p in tg]
    rows.append([btn(tr("🔙 بازگشت"), f"vpn:ag:{ag['id']}", role="back")])
    await cb.message.answer(tr("📍 <b>تغییر لوکیشن</b>") + "\n" + tr("لوکیشن جدید پنل نمایندگی را انتخاب کنید. حجم و زمان باقی‌مانده‌ی شما همان‌طور که هست منتقل می‌شود."), reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^vpn:agmp:\d+:\d+$"))
async def agent_move_confirm(cb: CallbackQuery, shop: Shop, tr):
    _, _, aid, pid = cb.data.split(":")
    ag = await _own_agent(cb, shop, tr, aid)
    if not ag:
        return
    ags = shop.vpn.agents
    dst = next((p for p in await ags.move_targets(ag) if p["id"] == int(pid)), None) if ags.can_user_move(ag) else None
    if not dst:
        await cb.answer(tr("این لوکیشن در دسترس نیست."), show_alert=True)
        return
    await cb.message.answer(tr("📍 انتقال به «{p}»", p=esc(dst["name"])) + "\n\n" + tr("آدرس و رمز پنل نمایندگی شما عوض می‌شود. کاربرانی که قبلاً داخل پنل قبلی ساخته‌اید خودکار منتقل نمی‌شوند و باید برای آن‌ها با پشتیبانی هماهنگ کنید. ادامه می‌دهید؟"),
                            reply_markup=ikb([[btn(tr("✅ بله، منتقل کن"), f"vpn:agmg:{aid}:{pid}", role="confirm"), btn(tr("❌ انصراف"), f"vpn:ag:{aid}", role="reject")],
                                                  [btn(tr("🔙 بازگشت"), f"vpn:agm:{aid}", role="back")]]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^vpn:agmg:\d+:\d+$"))
async def agent_move_go(cb: CallbackQuery, shop: Shop, tr):
    _, _, aid, pid = cb.data.split(":")
    await cb.answer(tr("در حال انتقال ..."))
    try:
        ag = await shop.vpn.agents.move(int(aid), int(pid), actor=f"user:{cb.from_user.id}", user_id=cb.from_user.id)
    except MoveError as e:
        await cb.message.answer("❌ " + tr(str(e)))
        return
    await cb.message.answer(tr("✅ سرویس شما به لوکیشن «{p}» منتقل شد؛ حجم و زمان باقی‌مانده حفظ شد.", p=esc(ag.get("panel_name") or "")))
    await shop.vpn.agents.send_agent(cb.from_user.id, ag)


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
    custom = shop.vpn.agents.is_custom(ag)
    if not ag["plan_id"] and not custom:
        await cb.answer(tr("پلنی برای تمدید موجود نیست."), show_alert=True)
        return
    try:
        oid = await shop.vpn.agents.renew_order(cb.from_user.id, ag["id"], 0)
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
    if d.get("sid"):
        svc = await shop.db.get_vpn_service(d["sid"])
        cfg = shop.vpn.custom_cfg(svc).get(d["kind"]) if svc else None
    else:
        ag = await shop.db.get_agent(d["agent_id"])
        cfg = (await shop.vpn.agents.custom_for(ag)).get(d["kind"]) if ag else None
    if cfg and not cfg["min"] <= v <= cfg["max"]:
        await m.answer(tr("مقدار خارج از بازه است؛ حداقل {a} و حداکثر {b} مجاز است.", a=f"{cfg['min']:g}", b=f"{cfg['max']:g}"))
        return
    try:
        if d.get("sid"):
            oid = await shop.vpn.custom_order(m.from_user.id, d["sid"], d["kind"], v)
        else:
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
    """تمدید: فقط یک پیام تأیید (بدون فهرست پلن)؛ همان حجم پایه‌ی پنل با مدت و قیمت فعلی."""
    ag = await _own_agent(cb, shop, tr, cb.data.split(":")[2])
    if not ag:
        return
    same = await shop.vpn.agents.renew_same(ag, await shop.db.get_user(cb.from_user.id))
    if not same:
        await cb.answer(tr("پلنی برای تمدید موجود نیست."), show_alert=True)
        return
    gb = ltr(f"{same['gb']:g} GB") if same["gb"] else tr("نامحدود")
    days = tr("{d} روز", d=same["days"]) if same["days"] else tr("بدون انقضا")
    await cb.message.answer(tr("🔄 <b>تمدید پنل نمایندگی</b> {n}\n\nآیا مایل به تمدید پنل خود هستید؟\n📦 حجم: {g}\n⏳ مدت: {d}\n💰 مبلغ: {p} تومان", n=f"<code>{esc(ag['username'])}</code>", g=gb, d=days, p=fmt(same["price"]))
                            + ("\n\n" + tr("ℹ️ این حجم روی حجم فعلی پنل شما اضافه می‌شود و مصرف قبلی صفر نمی‌شود.") if same.get("stack") and same["gb"] else ""),
                            reply_markup=ikb([[btn(tr("✅ تایید"), f"vpn:agnp:{ag['id']}:0", role="confirm"), btn(tr("🔙 بازگشت"), f"vpn:ag:{ag['id']}", role="back")]]))
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
