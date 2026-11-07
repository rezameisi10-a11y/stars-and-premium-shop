"""پشتیبان هوشمند برای کاربر: دکمه‌ی منو ← گفتگو با AI ← ارجاع به پشتیبان انسانی."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ai_support import MAX_IMG, AiError
from i18n import N_
from service import Shop
from ui import btn, esc, ikb

router = Router()
router.message.filter(F.chat.type == "private")

LANG_NAMES = {"fa": "Persian", "en": "English", "ar": "Arabic", "ru": "Russian", "tr": "Turkish", "de": "German",
              "es": "Spanish", "fr": "French", "hi": "Hindi", "id": "Indonesian", "zh": "Chinese"}


class AiSt(StatesGroup):
    chat = State()


def _kb(tr, human=False, fb=False, pick=False):
    """fb: بعد از پاسخ AI؛ «حل شد / حل نشد» (حل نشد = وصل به پشتیبان انسانی). pick: چند اشتراک دارد؛ تعویض سرویس."""
    rows = []
    if fb:
        rows.append([btn(tr("✅ مشکلم حل شد"), "ai:ok", role="confirm"), btn(tr("❌ حل نشد؛ وصلم کن به پشتیبانی"), "ai:human", role="action")])
    elif human:
        rows.append([btn(tr("👤 صحبت با پشتیبان انسانی"), "ai:human", role="action")])
    if pick:
        rows.append([btn(tr("🔁 انتخاب سرویس دیگر"), "ai:pick", role="admin")])
    rows.append([btn(tr("✅ پایان گفتگو"), "ai:end", role="back")])
    return ikb(rows)


ST = {"active": "🟢", "disabled": "⏸", "expired": "🔴", "limited": "🟠", "pending_manual": "⏳"}


async def _pick_kb(shop: Shop, uid: int, tr):
    """همه‌ی اشتراک‌های VPN و پنل‌های نمایندگیِ کاربر به‌صورت دکمه؛ None اگر بیشتر از یکی نیست."""
    items = await shop.ai.items(uid)
    if len(items) < 2:
        return None
    rows = []
    for it in items[:24]:
        st = ST.get(it["row"]["status"], "⚪️")
        nm = it["title"] or it["name"]
        label = (f"{st} 🧑‍💼 {nm}" if it["kind"] == "a" else f"{st} 📡 {nm}")[:60]
        rows.append([btn(label, f"ai:{it['kind']}:{it['id']}", role="admin")])
    rows.append([btn(tr("🔹 مشکل دیگری دارم (مربوط به سرویس نیست)"), "ai:s:0", role="back")])
    return ikb(rows)


async def _pick_prompt(msg: Message, shop: Shop, uid: int, tr) -> bool:
    kb = await _pick_kb(shop, uid, tr)
    if kb is None:
        return False
    await msg.answer(tr("🔧 کدام سرویس مشکل دارد؟ از لیست زیر انتخاب کنید:"), reply_markup=kb)
    return True


@router.callback_query(F.data == "ai:start")
async def ai_start(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    if not shop.ai.enabled():
        await cb.answer(tr("پشتیبان هوشمند فعال نیست."), show_alert=True)
        return
    await state.clear()
    await state.set_state(AiSt.chat)
    shop.ai.reset(cb.from_user.id)
    await cb.message.answer(tr("🤖 <b>پشتیبان هوشمند</b>\nسؤال خود را بنویسید؛ درباره‌ی محصولات، قیمت‌ها، آموزش اتصال، وضعیت سرویس‌های شما و هر چیز دیگر راهنمایی می‌کنم.\n📷 اگر مشکلی دارید، عکس (اسکرین‌شات) خطا را بفرستید تا تحلیل کنم و دلایل احتمالی و راه‌حل را بگویم.\nبرای پایان، «✅ پایان گفتگو» را بزنید."),
                            reply_markup=_kb(tr))
    await _pick_prompt(cb.message, shop, cb.from_user.id, tr)
    await cb.answer()


@router.callback_query(F.data == "ai:pick")
async def ai_pick(cb: CallbackQuery, shop: Shop, tr):
    if not await _pick_prompt(cb.message, shop, cb.from_user.id, tr):
        await cb.answer(tr("پشتیبان هوشمند فعال نیست.") if not shop.ai.enabled() else "—", show_alert=True)
        return
    await cb.answer()


@router.callback_query(F.data.regexp(r"^ai:[sa]:\d+$"))
async def ai_choose(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    if not shop.ai.enabled():
        await cb.answer(tr("پشتیبان هوشمند فعال نیست."), show_alert=True)
        return
    _x, kind, sid = cb.data.split(":")
    uid, sid = cb.from_user.id, int(sid)
    await state.set_state(AiSt.chat)
    if sid == 0:
        shop.ai.set_focus(uid, "s", 0)
        await cb.message.answer(tr("چه مشکلی دارید؟ توضیح بدهید یا عکس (اسکرین‌شات) خطا را بفرستید."), reply_markup=_kb(tr, pick=True))
        await cb.answer()
        return
    it = next((i for i in await shop.ai.items(uid) if i["kind"] == kind and i["id"] == sid), None)      # فقط اشتراک‌های خودِ کاربر
    if not it:
        await cb.answer(tr("پیدا نشد."), show_alert=True)
        return
    shop.ai.set_focus(uid, kind, sid)
    await state.update_data(ai_svc=it["name"])
    await cb.message.answer(tr("✅ «{n}» انتخاب شد. چه مشکلی دارید؟ توضیح بدهید یا عکس (اسکرین‌شات) خطا را بفرستید.", n=esc(it["title"] or it["name"])),
                            reply_markup=_kb(tr, pick=True))
    await cb.answer()


@router.callback_query(F.data == "ai:ok")
async def ai_ok(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    await state.clear()
    shop.ai.reset(cb.from_user.id)
    await cb.message.answer(tr("🎉 خوشحالیم که حل شد! هر وقت سؤالی داشتید دوباره بپرسید."), reply_markup=await shop.main_menu_kb(cb.from_user.id))
    await cb.answer()


@router.message(AiSt.chat, F.text, ~F.text.startswith("/"))
async def ai_msg(m: Message, shop: Shop, state: FSMContext, tr, lang):
    uid = m.from_user.id
    try:
        await shop.bot.send_chat_action(m.chat.id, "typing")
    except Exception:  # noqa: BLE001
        pass
    try:
        text, human = await shop.ai.ask(uid, m.text, LANG_NAMES.get(lang, "Persian"))
    except AiError as e:
        await m.answer(tr(str(e)), reply_markup=_kb(tr, human=True))
        return
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("ai").exception("ai ask failed")
        await m.answer(tr("پشتیبان هوشمند موقتاً در دسترس نیست؛ کمی بعد دوباره تلاش کنید."), reply_markup=_kb(tr, human=True))
        return
    await state.update_data(ai_last=m.text[:300])
    pick = len(await shop.ai.items(uid)) > 1
    await m.answer(text, parse_mode=None, reply_markup=_kb(tr, human=human, fb=True, pick=pick), disable_web_page_preview=True)


@router.message(AiSt.chat, F.photo)
async def ai_photo(m: Message, shop: Shop, state: FSMContext, tr, lang):
    """عکس مشکل (اسکرین‌شات) ← تحلیل توسط مدل؛ کپشن عکس به‌عنوان توضیح کاربر استفاده می‌شود."""
    uid = m.from_user.id
    if not shop.ai.vision_on():
        await m.answer(tr("عکس پذیرفته نشد؛ مشکل را به‌صورت متن بنویسید."), reply_markup=_kb(tr))
        return
    try:
        await shop.bot.send_chat_action(m.chat.id, "typing")
    except Exception:  # noqa: BLE001
        pass
    img = None
    try:
        ph = [p for p in m.photo if (p.file_size or 0) <= MAX_IMG] or []
        if ph:
            buf = await shop.bot.download(ph[-1])
            img = (buf.read(), "image/jpeg") if buf else None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("ai").exception("ai photo download failed")
    if not img:
        await m.answer(tr("عکس دریافت نشد؛ دوباره بفرستید یا مشکل را بنویسید."), reply_markup=_kb(tr))
        return
    try:
        text, human = await shop.ai.ask(uid, m.caption or "", LANG_NAMES.get(lang, "Persian"), image=img)
    except AiError as e:
        await m.answer(tr(str(e)), reply_markup=_kb(tr, human=True))
        return
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("ai").exception("ai photo ask failed")
        await m.answer(tr("پشتیبان هوشمند موقتاً در دسترس نیست؛ کمی بعد دوباره تلاش کنید."), reply_markup=_kb(tr, human=True))
        return
    await state.update_data(ai_last=(m.caption or "📷")[:300], ai_photo=m.photo[-1].file_id)
    pick = len(await shop.ai.items(uid)) > 1
    await m.answer(text, parse_mode=None, reply_markup=_kb(tr, human=human, fb=True, pick=pick), disable_web_page_preview=True)


@router.message(AiSt.chat, ~F.text)
async def ai_nontext(m: Message, tr):
    await m.answer(tr("لطفاً سؤال خود را به‌صورت متن بنویسید."), reply_markup=_kb(tr))


@router.callback_query(F.data == "ai:human")
async def ai_human(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    u = cb.from_user
    d = await state.get_data()
    sup = (shop.s.get("support_username") or "").lstrip("@")
    tid = 0
    if shop.s.get("tickets_on", "1") == "1" and shop.warn_once(f"ai_human_tk:{u.id}", 0.1):       # گفتگو + سرویس انتخاب‌شده به‌صورت تیکت برای پشتیبان
        from handlers_tickets import _notify_admins
        tr_txt = shop.ai.transcript(u.id) or (d.get("ai_last") or "-")
        txt = ("🤖 ارجاع از پشتیبان هوشمند" + (f" — سرویس: {d['ai_svc']}" if d.get("ai_svc") else "") + "\n\n" + tr_txt)[:3000]
        tid = await shop.db.tk_create(u.id, "پشتیبان هوشمند: " + (d.get("ai_svc") or (d.get("ai_last") or "")[:40] or "-"), txt, bool(d.get("ai_photo")))
        await _notify_admins(shop, tid, f"{u.full_name} @{u.username or '-'} ({u.id})", txt, None, True)
        if d.get("ai_photo"):
            for aid in shop.admins_with("users_view"):
                try:
                    await shop.bot.send_photo(aid, d["ai_photo"], caption=f"📷 {u.id} / #{tid}")
                except Exception:  # noqa: BLE001
                    pass
    if not tid and shop.warn_once(f"ai_human:{u.id}", 0.1):
        uname = f"@{u.username}" if u.username else "-"
        last = esc(d.get("ai_last", ""))
        await shop.notify_admins(f"🙋 <b>درخواست پشتیبانی انسانی (از پشتیبان هوشمند)</b>\nکاربر: <code>{u.id}</code> | {esc(u.full_name)} | {uname}\n"
                                 f"آخرین پیام: {last or '-'}", perm="orders")
        if d.get("ai_photo"):                                   # عکس مشکل را هم برای پشتیبان انسانی بفرست
            for aid in shop.admins_with("orders"):
                try:
                    await shop.bot.send_photo(aid, d["ai_photo"], caption=f"📷 {u.id}")
                except Exception:  # noqa: BLE001
                    pass
    msg = tr("✅ درخواست شما برای پشتیبان انسانی ثبت شد.")
    if tid:
        msg += "\n" + tr("🎫 تیکت شما #{n} ساخته شد؛ پاسخ پشتیبانی همین‌جا در ربات می‌آید.", n=tid)
    if sup:
        msg += "\n" + tr("☎️ پشتیبانی: {u}", u="@" + esc(sup))
    await cb.message.answer(msg)
    await cb.answer()


@router.callback_query(F.data == "ai:end")
async def ai_end(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    await state.clear()
    shop.ai.reset(cb.from_user.id)
    await cb.message.answer(tr("گفتگو پایان یافت. هر وقت سؤالی داشتید دوباره از منو «🤖 پشتیبان هوشمند» را بزنید."),
                            reply_markup=await shop.main_menu_kb(cb.from_user.id))
    await cb.answer()
