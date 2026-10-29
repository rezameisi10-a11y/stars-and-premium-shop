"""پشتیبان هوشمند برای کاربر: دکمه‌ی منو ← گفتگو با AI ← ارجاع به پشتیبان انسانی."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ai_support import AiError
from i18n import N_
from service import Shop
from ui import btn, esc, ikb

router = Router()
router.message.filter(F.chat.type == "private")

LANG_NAMES = {"fa": "Persian", "en": "English", "ar": "Arabic", "ru": "Russian", "tr": "Turkish", "de": "German",
              "es": "Spanish", "fr": "French", "hi": "Hindi", "id": "Indonesian", "zh": "Chinese"}


class AiSt(StatesGroup):
    chat = State()


def _kb(tr, human=False):
    rows = []
    h = btn(tr("👤 صحبت با پشتیبان انسانی"), "ai:human", role="action")
    rows.append([h] if human else [])
    rows.append([btn(tr("✅ پایان گفتگو"), "ai:end", role="back")])
    return ikb([r for r in rows if r])


@router.callback_query(F.data == "ai:start")
async def ai_start(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    if not shop.ai.enabled():
        await cb.answer(tr("پشتیبان هوشمند فعال نیست."), show_alert=True)
        return
    await state.clear()
    await state.set_state(AiSt.chat)
    shop.ai.reset(cb.from_user.id)
    await cb.message.answer(tr("🤖 <b>پشتیبان هوشمند</b>\nسؤال خود را بنویسید؛ درباره‌ی محصولات، قیمت‌ها، آموزش اتصال، وضعیت سرویس‌های شما و هر چیز دیگر راهنمایی می‌کنم.\nبرای پایان، «✅ پایان گفتگو» را بزنید."),
                            reply_markup=_kb(tr))
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
    await m.answer(text, parse_mode=None, reply_markup=_kb(tr, human=human), disable_web_page_preview=True)


@router.message(AiSt.chat, ~F.text)
async def ai_nontext(m: Message, tr):
    await m.answer(tr("لطفاً سؤال خود را به‌صورت متن بنویسید."), reply_markup=_kb(tr))


@router.callback_query(F.data == "ai:human")
async def ai_human(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    u = cb.from_user
    d = await state.get_data()
    sup = (shop.s.get("support_username") or "").lstrip("@")
    if shop.warn_once(f"ai_human:{u.id}", 0.1):
        uname = f"@{u.username}" if u.username else "-"
        last = esc(d.get("ai_last", ""))
        await shop.notify_admins(f"🙋 <b>درخواست پشتیبانی انسانی (از پشتیبان هوشمند)</b>\nکاربر: <code>{u.id}</code> | {esc(u.full_name)} | {uname}\n"
                                 f"آخرین پیام: {last or '-'}", perm="orders")
    msg = tr("✅ درخواست شما برای پشتیبان انسانی ثبت شد.")
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
