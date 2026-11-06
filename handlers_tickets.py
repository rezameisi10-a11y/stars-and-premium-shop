"""تیکت پشتیبانی: سمت کاربر (داخل «پشتیبانی») و سمت ادمین («🎫 تیکت‌ها» در منوی مدیریت)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from admin_common import back_row, make_router, view
from i18n import N_
from service import Shop
from ui import btn, esc, ikb

user_router = Router()
user_router.message.filter(F.chat.type == "private")
router = make_router()          # سمت ادمین
ICON = {"open": "🟢", "answered": "💬", "closed": "🔒"}
ST_FA = {"open": "باز (منتظر پاسخ ادمین)", "answered": "پاسخ داده شده", "closed": "بسته"}
TEHRAN = timezone(timedelta(hours=3, minutes=30))
MIN_GAP = 20          # ثانیه بین دو پیام تیکتی هر کاربر (ضد اسپم)


class TkSt(StatesGroup):
    new = State()
    reply = State()
    admin_reply = State()


def _stamp(ts) -> str:
    from handlers_admin import jdate
    return f"{jdate(ts)} {datetime.fromtimestamp(ts, TEHRAN):%H:%M}"


def _body(m: Message) -> tuple[str, bool]:
    """(متن/کپشن، آیا فایل دارد). فقط متن، عکس و فایل پذیرفته می‌شود؛ بقیه رد."""
    has = bool(m.photo or m.document or m.video or m.voice)
    txt = (m.text or m.caption or "").strip()
    return txt[:3000], has


async def _notify_admins(shop: Shop, tid: int, who: str, text: str, m: Message | None, new: bool):
    t = f"🎫 <b>{'تیکت جدید' if new else 'پاسخ کاربر'} #{tid}</b> — {esc(who)}\n\n{esc(text[:600]) or '📎 فایل'}"
    mk = ikb([[btn("🎫 مشاهده و پاسخ", f"adm:tkv:{tid}", role="admin")]])
    await shop.notify_admins(t, markup=mk, perm="users_view", topic="alerts")
    if m and (m.photo or m.document or m.video or m.voice):
        for aid in shop.admins_with("users_view"):
            try:
                await m.copy_to(aid)
            except Exception:  # noqa: BLE001
                pass


# ============================================================ کاربر
def support_kb(shop: Shop, tr):
    if shop.s.get("tickets_on", "1") != "1":
        return None
    return ikb([[btn(tr("➕ تیکت جدید"), "tk:n", role="action"), btn(tr("🎫 تیکت‌های من"), "tk:l", role="admin")]])


@user_router.callback_query(F.data == "tk:l")
async def tk_list(cb: CallbackQuery, shop: Shop, tr):
    rows = await shop.db.tk_user_list(cb.from_user.id)
    kb = [[btn(f"{ICON[t['status']]} #{t['id']} {t['subject'][:30]}", f"tk:v:{t['id']}", role="admin")] for t in rows]
    kb.append([btn(tr("➕ تیکت جدید"), "tk:n", role="action")])
    await cb.message.answer(tr("🎫 <b>تیکت‌های من</b>") if rows else tr("هنوز تیکتی نساخته‌اید."), reply_markup=ikb(kb))
    await cb.answer()


@user_router.callback_query(F.data.regexp(r"^tk:v:\d+$"))
async def tk_view(cb: CallbackQuery, shop: Shop, tr):
    t = await shop.db.tk_get(int(cb.data.split(":")[2]))
    if not t or t["user_id"] != cb.from_user.id:
        await cb.answer(tr("پیدا نشد."), show_alert=True)
        return
    msgs = await shop.db.tk_msgs(t["id"])
    L = [f"🎫 <b>#{t['id']}</b> {esc(t['subject'])}", {"open": tr("🟢 در انتظار پاسخ"), "answered": tr("💬 پاسخ داده شد"), "closed": tr("🔒 بسته")}[t["status"]], ""]
    for x in msgs:
        L.append(("👤 " if x["sender"] == "user" else "🛟 ") + esc(x["text"] or "📎"))
    rows = []
    if t["status"] != "closed":
        rows.append([btn(tr("✍️ پاسخ"), f"tk:r:{t['id']}", role="action"), btn(tr("🔒 بستن تیکت"), f"tk:c:{t['id']}", role="reject")])
    rows.append([btn(tr("🔙 تیکت‌های من"), "tk:l", role="back")])
    await cb.message.answer("\n".join(L)[:4000], reply_markup=ikb(rows))
    await cb.answer()


async def _gate(shop: Shop, uid: int, tr, check_open: bool) -> str:
    if shop.s.get("tickets_on", "1") != "1":
        return tr("تیکت فعلاً فعال نیست.")
    if time.time() - await shop.db.tk_last_msg_at(uid) < MIN_GAP:
        return tr("لطفاً چند ثانیه بعد دوباره پیام بفرستید.")
    if check_open:
        mx = int(float(shop.s.get("tickets_max_open", "3") or 3)) or 3
        if await shop.db.tk_open_count(uid) >= mx:
            return tr("چند تیکت باز دارید؛ ابتدا یکی را ببندید یا منتظر پاسخ بمانید.")
    return ""


@user_router.callback_query(F.data == "tk:n")
async def tk_new(cb: CallbackQuery, shop: Shop, tr, state: FSMContext):
    err = await _gate(shop, cb.from_user.id, tr, True)
    if err:
        await cb.answer(err, show_alert=True)
        return
    await state.set_state(TkSt.new)
    await cb.message.answer(tr("✍️ مشکل یا سؤال‌تان را بنویسید (می‌توانید عکس یا فایل هم بفرستید). خط اول، عنوان تیکت می‌شود. برای انصراف /start"))
    await cb.answer()


@user_router.message(TkSt.new)
async def tk_new_msg(m: Message, shop: Shop, tr, state: FSMContext):
    if (m.text or "").startswith("/"):
        await state.clear()
        return
    txt, has = _body(m)
    if not txt and not has:
        await m.answer(tr("فقط متن، عکس یا فایل بفرستید."))
        return
    err = await _gate(shop, m.from_user.id, tr, True)
    if err:
        await state.clear()
        await m.answer(err)
        return
    await state.clear()
    subject = (txt.splitlines()[0] if txt else tr("فایل"))[:60]
    tid = await shop.db.tk_create(m.from_user.id, subject, txt, has)
    await m.answer(tr("✅ تیکت #{n} ثبت شد. پاسخ را همین‌جا برایتان می‌فرستیم.", n=tid))
    u = m.from_user
    await _notify_admins(shop, tid, f"{u.first_name or ''} @{u.username or '-'} ({u.id})", txt, m, True)


@user_router.callback_query(F.data.regexp(r"^tk:r:\d+$"))
async def tk_reply(cb: CallbackQuery, shop: Shop, tr, state: FSMContext):
    t = await shop.db.tk_get(int(cb.data.split(":")[2]))
    if not t or t["user_id"] != cb.from_user.id or t["status"] == "closed":
        await cb.answer(tr("این تیکت بسته است."), show_alert=True)
        return
    await state.set_state(TkSt.reply)
    await state.update_data(tid=t["id"])
    await cb.message.answer(tr("✍️ پاسخ‌تان را بفرستید (برای انصراف /start):"))
    await cb.answer()


@user_router.message(TkSt.reply)
async def tk_reply_msg(m: Message, shop: Shop, tr, state: FSMContext):
    d = await state.get_data()
    if (m.text or "").startswith("/"):
        await state.clear()
        return
    t = await shop.db.tk_get(d.get("tid", 0))
    txt, has = _body(m)
    if not t or t["user_id"] != m.from_user.id or t["status"] == "closed" or (not txt and not has):
        await m.answer(tr("فقط متن، عکس یا فایل بفرستید."))
        return
    err = await _gate(shop, m.from_user.id, tr, False)
    if err:
        await state.clear()
        await m.answer(err)
        return
    await state.clear()
    await shop.db.tk_add(t["id"], "user", txt, has_file=has)
    await m.answer(tr("✅ پیام شما ثبت شد."))
    u = m.from_user
    await _notify_admins(shop, t["id"], f"{u.first_name or ''} @{u.username or '-'} ({u.id})", txt, m, False)


@user_router.callback_query(F.data.regexp(r"^tk:c:\d+$"))
async def tk_close(cb: CallbackQuery, shop: Shop, tr):
    t = await shop.db.tk_get(int(cb.data.split(":")[2]))
    if not t or t["user_id"] != cb.from_user.id:
        await cb.answer(tr("پیدا نشد."), show_alert=True)
        return
    await shop.db.tk_set_status(t["id"], "closed")
    await cb.answer(tr("🔒 تیکت بسته شد."))


# ============================================================ ادمین
async def tk_badge(shop: Shop) -> int:
    c = await shop.db.tk_counts()
    return c["open"]


@router.callback_query(F.data == "adm:tk")
async def adm_hub(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    c = await shop.db.tk_counts()
    rows = [[btn(f"🟢 باز — منتظر پاسخ ({c['open']})", "adm:tkl:open:0", role="admin")],
            [btn(f"💬 پاسخ داده‌شده ({c['answered']})", "adm:tkl:answered:0", role="admin"), btn(f"🔒 بسته ({c['closed']})", "adm:tkl:closed:0", role="admin")],
            back_row()]
    await view(cb, "🎫 <b>تیکت‌های پشتیبانی</b>\nتیکت‌های «باز» منتظر پاسخ شما هستند.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:tkl:(open|answered|closed):\d+$"))
async def adm_list(cb: CallbackQuery, shop: Shop):
    _, _, st, pg = cb.data.split(":")
    pg = int(pg)
    rows = await shop.db.tk_list(st, pg * 10, 11)
    more = len(rows) > 10
    kb = [[btn(f"{ICON[t['status']]} #{t['id']} {t['subject'][:24]} · {t['first_name'] or ''}", f"adm:tkv:{t['id']}", role="admin")] for t in rows[:10]]
    nav = []
    if pg:
        nav.append(btn("◀️", f"adm:tkl:{st}:{pg - 1}", role="back"))
    if more:
        nav.append(btn("▶️", f"adm:tkl:{st}:{pg + 1}", role="back"))
    if nav:
        kb.append(nav)
    kb.append(back_row("adm:tk"))
    await view(cb, f"🎫 <b>{ST_FA[st]}</b>" + ("" if rows else "\n\nتیکتی نیست ✅"), ikb(kb))
    await cb.answer()


async def _thread(shop: Shop, tid: int):
    t = await shop.db.tk_get(tid)
    if not t:
        return None, None
    u = await shop.db.get_user(t["user_id"]) or {}
    L = [f"🎫 <b>#{t['id']}</b> {esc(t['subject'])}", f"👤 {esc(u.get('first_name') or '')} @{esc(u.get('username') or '-')} (<code>{t['user_id']}</code>)",
         f"{ICON[t['status']]} {ST_FA[t['status']]} · {_stamp(t['created_at'])}", ""]
    for x in await shop.db.tk_msgs(tid, 10):
        L.append(f"{'👤' if x['sender'] == 'user' else '🛟'} <i>{_stamp(x['created_at'])}</i>\n{esc(x['text']) or '📎 فایل'}\n")
    rows = []
    if t["status"] != "closed":
        rows.append([btn("✍️ پاسخ", f"adm:tkr:{tid}", role="action"), btn("🔒 بستن", f"adm:tkx:{tid}:c", role="reject")])
    else:
        rows.append([btn("🔓 بازگشایی", f"adm:tkx:{tid}:o", role="action")])
    rows.append([btn("👤 کارت کاربر", f"adm:u:{t['user_id']}", role="admin")])
    rows.append(back_row("adm:tk"))
    return "\n".join(L)[:4000], ikb(rows)


@router.callback_query(F.data.regexp(r"^adm:tkv:\d+$"))
async def adm_view(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    text, kb = await _thread(shop, int(cb.data.split(":")[2]))
    if not text:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, text, kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:tkx:\d+:[co]$"))
async def adm_status(cb: CallbackQuery, shop: Shop):
    _, _, tid, act = cb.data.split(":")
    t = await shop.db.tk_get(int(tid))
    if not t:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await shop.db.tk_set_status(t["id"], "closed" if act == "c" else "open")
    await shop.db.log(f"tg:{cb.from_user.id}", "ticket_close" if act == "c" else "ticket_open", f"#{tid}")
    text, kb = await _thread(shop, t["id"])
    await view(cb, text, kb)
    await cb.answer("انجام شد")


@router.callback_query(F.data.regexp(r"^adm:tkr:\d+$"))
async def adm_reply(cb: CallbackQuery, shop: Shop, state: FSMContext):
    tid = int(cb.data.split(":")[2])
    t = await shop.db.tk_get(tid)
    if not t:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await state.set_state(TkSt.admin_reply)
    await state.update_data(tid=tid)
    await cb.message.answer(f"✍️ پاسخ تیکت #{tid} را بفرستید (متن، عکس یا فایل). برای انصراف /admin")
    await cb.answer()


@router.message(TkSt.admin_reply)
async def adm_reply_msg(m: Message, shop: Shop, state: FSMContext):
    if (m.text or "").startswith("/"):
        return
    d = await state.get_data()
    t = await shop.db.tk_get(d.get("tid", 0))
    txt, has = _body(m)
    if not t or (not txt and not has):
        await m.answer("فقط متن، عکس یا فایل.")
        return
    await state.clear()
    await shop.db.tk_add(t["id"], "admin", txt, admin_id=m.from_user.id, has_file=has)
    await shop.db.log(f"tg:{m.from_user.id}", "ticket_reply", f"#{t['id']}")
    tr = await shop.tr_for(t["user_id"])
    mk = ikb([[btn(tr("🎫 مشاهده"), f"tk:v:{t['id']}", role="admin"), btn(tr("✍️ پاسخ"), f"tk:r:{t['id']}", role="action")]])
    await shop.safe_send(t["user_id"], tr("🛟 <b>پاسخ پشتیبانی به تیکت #{n}</b>", n=t["id"]) + "\n\n" + esc(txt), reply_markup=mk)
    if has:
        try:
            await m.copy_to(t["user_id"])
        except Exception:  # noqa: BLE001
            pass
    text, kb = await _thread(shop, t["id"])
    await m.answer("✅ برای کاربر ارسال شد.\n\n" + text, reply_markup=kb)
