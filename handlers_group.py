"""فرمان‌های داخل گروه: اتصال ربات به گروه گزارش‌ها (/connect) و قطع اتصال (/disconnect)؛ فقط برای ادمین‌های ربات."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from groupfeed import TOPICS
from service import Shop

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


def _summary(res: dict) -> str:
    lines = ["✅ این گروه به‌عنوان «گروه گزارش‌ها» متصل شد."]
    if res.get("forum"):
        if res["created"]:
            lines.append("🗂 تاپیک‌های ساخته‌شده: " + "، ".join(TOPICS[k] for k in res["created"]))
        else:
            lines.append("🗂 همه‌ی تاپیک‌ها از قبل موجودند.")
    else:
        lines.append("ℹ️ این گروه «تاپیک» ندارد؛ همه‌ی پیام‌ها در چت اصلی ارسال می‌شود. برای تفکیک، در تنظیمات گروه گزینه‌ی Topics را روشن کنید و دوباره /connect بفرستید.")
    if res.get("error"):
        lines.append(f"⚠️ {res['error']}\nربات باید ادمین گروه با دسترسی «Manage Topics» و «Post Messages» باشد.")
    lines.append("از این پس رویدادها، گزارش شبانه و بکاپ دوره‌ای اینجا ارسال می‌شود. تنظیمات: پنل وب یا ربات ← تنظیمات ← گروه گزارش‌ها.")
    return "\n".join(lines)


@router.message(Command("connect"))
async def cmd_connect(m: Message, shop: Shop):
    if not m.from_user or not shop.is_admin(m.from_user.id):
        return
    res = await shop.group.connect(m.chat)
    await m.reply(_summary(res))
    await shop.db.log(f"tg:{m.from_user.id}", "group_connect", str(m.chat.id))


@router.message(Command("disconnect"))
async def cmd_disconnect(m: Message, shop: Shop):
    if not m.from_user or not shop.is_admin(m.from_user.id):
        return
    await shop.group.disconnect()
    await m.reply("🔌 ارسال به گروه خاموش شد. برای اتصال دوباره /connect بفرستید.")


@router.message(Command("groupstatus"))
async def cmd_status(m: Message, shop: Shop):
    if not m.from_user or not shop.is_admin(m.from_user.id):
        return
    g = shop.group
    await m.reply(f"وضعیت: {'✅ روشن' if g.enabled() else '⛔ خاموش'}\nگروه متصل: <code>{g.chat_id() or '—'}</code>\n"
                  f"تاپیک‌ها: {len(g.topics())}/{len(TOPICS)}\nاین گروه: <code>{m.chat.id}</code>")


@router.my_chat_member()
async def on_added(ev: ChatMemberUpdated, shop: Shop):
    if ev.chat.type not in ("group", "supergroup"):
        return
    if ev.new_chat_member.status in ("member", "administrator") and ev.from_user and shop.is_admin(ev.from_user.id):
        await shop.bot.send_message(ev.chat.id, "سلام! 👋 برای اتصال این گروه به‌عنوان «گروه گزارش‌ها» یکی از ادمین‌های ربات دستور /connect را بفرستد.\n"
                                                "پیشنهاد: ربات را ادمین کنید (Manage Topics + Post Messages) و گروه را خصوصی نگه دارید.")
