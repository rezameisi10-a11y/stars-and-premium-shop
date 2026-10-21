"""گیت‌های عمومی: ثبت کاربر، زبان، معرف، مسدودی، حالت تعمیر و عضویت اجباری."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from ui import btn, esc, ikb


class GateMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        shop = data["shop"]
        chat = event.chat if isinstance(event, Message) else (event.message.chat if getattr(event, "message", None) else None)
        if user is None or user.is_bot or (chat is not None and chat.type != "private"):
            return await handler(event, data)

        is_new = await shop.db.upsert_user(user.id, user.username, user.first_name)
        data["is_new"] = is_new
        if is_new:
            # زبان تلگرام کاربر را (اگر پشتیبانی شود) به‌عنوان زبان اولیه می‌گذاریم
            guess = shop.resolve_bot_lang(getattr(user, "language_code", None))
            if guess:
                await shop.set_user_lang(user.id, guess)
            if isinstance(event, Message) and (event.text or "").startswith("/start "):
                await shop.register_referral(user.id, event.text.split(maxsplit=1)[1].strip(), True)
            uname = f"@{esc(user.username)}" if user.username else "—"
            await shop.group.post("users", f"👤 <b>کاربر جدید</b>\n{esc(user.first_name or '')} | {uname} | <code>{user.id}</code>")
        lang = await shop.user_lang(user.id)
        tr = shop.tr_lang(lang)
        data["lang"], data["tr"] = lang, tr

        if shop.is_admin(user.id):
            return await handler(event, data)

        u = await shop.db.get_user(user.id)
        if u and u["banned"]:
            reason = (u.get("ban_reason") or "").strip()
            await self._say(event, tr("⛔ حساب شما مسدود شده است.") + (f"\n{reason}" if reason else ""))
            return
        s = await shop.settings()
        if s.get("maintenance") == "1":
            await self._say(event, shop.text("maintenance_text", lang) or tr("ربات موقتاً در دسترس نیست."))
            return
        is_check = isinstance(event, CallbackQuery) and event.data == "jchk"
        if not is_check and not await shop.is_member(user.id):
            ch = s.get("force_channel", "")
            rows = [[btn(tr("📢 عضویت در کانال"), url=f"https://t.me/{ch.lstrip('@')}", role="action")],
                    [btn(tr("✅ بررسی عضویت"), "jchk", role="confirm")]]
            msg = event if isinstance(event, Message) else event.message
            await msg.answer(esc(shop.text("join_text", lang) or tr("ابتدا در کانال عضو شوید.")), reply_markup=ikb(rows))
            if isinstance(event, CallbackQuery):
                await event.answer()
            return
        if not is_check:
            await shop.on_gate_pass(user.id)
        return await handler(event, data)

    @staticmethod
    async def _say(event, text):
        if isinstance(event, CallbackQuery):
            await event.answer(text[:190], show_alert=True)
        else:
            await event.answer(text)
