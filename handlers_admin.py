"""پنل مدیریت داخل ربات (بخش اصلی): خانه، آمار، سفارش‌ها، پرداخت‌ها، کاربران، محصولات، ستاره، بکاپ و وارد کردن بکاپ.
بخش‌های دیگر در admin_settings.py و admin_manage.py هستند. دسترسی هر بخش با مجوز ادمین کنترل می‌شود."""
import io
import os
import tempfile
import time
from datetime import datetime

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import backup as bk
import provider
from admin_common import TXT, Adm, back_row, make_router, num, view, with_data
from apiutil import D
from db import USER_FILTERS
from service import KIND_FA, METHOD_FA, PAY_STATUS_FA, STATUS_FA, Shop, describe_order
from ui import btn, esc, fmt, ikb

router = make_router()

FILTER_LABELS = {"all": "همه", "active": "فعال", "banned": "⛔ مسدود", "balance": "👛 دارای موجودی", "buyers": "🛒 خریدار",
                 "nobuy": "بدون خرید", "referrers": "🤝 معرف", "vip": "🎁 تخفیف شخصی", "new7": "🆕 ۷ روز اخیر", "inactive30": "😴 غیرفعال ۳۰ روز"}
UPER = 8


# ============================================================ خانه
def home_kb(shop: Shop, uid: int):
    c = lambda p: shop.can(uid, p)  # noqa: E731
    rows = []

    def add(*items):
        r = [btn(t, d, role="admin") for t, d, ok in items if ok]
        if r:
            rows.append(r)

    add(("📊 آمار", "adm:stats", c("stats")), ("🧾 صف تحویل", "adm:queue", c("orders")))
    add(("💳 رسیدهای منتظر تأیید", "adm:pays", c("receipts")), ("👥 کاربران", "adm:users", c("users_view")))
    add(("📦 محصولات و قیمت", "adm:prod", c("products")), ("💹 نرخ‌ها و قیمت‌گذاری", "adm:rates", c("pricing")))
    add(("⚙️ تنظیمات", "adm:set", c("settings") or c("pricing")), ("🎨 دکمه‌ها و رنگ‌ها", "adm:btn", c("buttons")))
    add(("🏷 کدهای تخفیف", "adm:disc", c("discounts")), ("📢 پیام همگانی", "adm:bcast", c("broadcast")))
    add(("🌐 زبان‌ها", "adm:langs", c("languages")), ("👮 ادمین‌ها", "adm:admins", c("admins")))
    add(("⭐ موجودی ستاره ربات", "adm:stars", c("stars")), ("💾 بکاپ / وارد کردن", "adm:backup", c("backup")))
    add(("🛡 فروش VPN", "adm:vpn", c("vpn")), ("🖥 پنل وب", "adm:web", True))
    rows.append([btn("🔙 منوی کاربر", "menu", role="back")])
    return ikb(rows)


async def home_text(shop: Shop) -> str:
    st = await shop.db.stats()
    return (f"⚙️ <b>پنل مدیریت</b>\n\n👥 کاربران: {fmt(st['users'])} | 🧾 در صف تحویل: {st['todo']} | "
            f"💳 رسید منتظر تأیید: {st['waiting']}\n💰 فروش امروز: {fmt(st['today_revenue'])} تومان")


@router.message(Command("admin"))
async def cmd_admin(m: Message, shop: Shop, state: FSMContext):
    await state.clear()
    await m.answer(await home_text(shop), reply_markup=home_kb(shop, m.from_user.id))


@router.callback_query(F.data == "adm:home")
async def cb_home(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await view(cb, await home_text(shop), home_kb(shop, cb.from_user.id))
    await cb.answer()


@router.message(Command("panel"))
async def cmd_panel(m: Message, shop: Shop):
    await send_web_link(m, shop)


async def send_web_link(t, shop: Shop):
    uid = t.from_user.id
    link = shop.make_login_link(uid)
    if not link:
        txt = "🌐 برای فعال‌سازی پنل وب، مقدار <code>PUBLIC_URL</code> را در فایل .env تنظیم کنید (مثلاً https://panel.example.com) و ربات را ریستارت کنید."
    else:
        txt = (f"🌐 <b>ورود به پنل وب</b>\nلینک یک‌بارمصرف (۵ دقیقه اعتبار):\n{esc(link)}\n\n"
               "این لینک را به کسی ندهید." + ("" if shop.cfg.public_url.startswith("https") else
                                              "\n⚠️ آدرس شما HTTPS نیست؛ برای امنیت بیشتر پشت SSL قرار دهید."))
    msg = t.message if isinstance(t, CallbackQuery) else t
    await msg.answer(txt, reply_markup=ikb([back_row()]), disable_web_page_preview=True)
    if isinstance(t, CallbackQuery):
        await t.answer()


@router.callback_query(F.data == "adm:web")
async def cb_web(cb: CallbackQuery, shop: Shop):
    await send_web_link(cb, shop)


@router.callback_query(F.data == "adm:noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


# ============================================================ آمار
@router.callback_query(F.data == "adm:stats")
async def cb_stats(cb: CallbackQuery, shop: Shop):
    st = await shop.db.stats()
    bal = await shop.star_balance()
    text = (f"📊 <b>آمار</b>\n\n👥 کل کاربران: <b>{fmt(st['users'])}</b> (امروز +{st['new_today']})\n"
            f"✅ سفارش‌های تحویل‌شده: <b>{fmt(st['done'])}</b>\n💰 کل فروش: <b>{fmt(st['revenue'])}</b> تومان\n"
            f"📈 سود ثبت‌شده: <b>{fmt(st['profit'])}</b> تومان (امروز {fmt(st['today_profit'])})\n"
            f"📅 امروز: {st['today_orders']} سفارش | {fmt(st['today_revenue'])} تومان\n"
            f"🧾 در صف تحویل/خطا: <b>{st['todo']}</b>\n💳 رسید منتظر تأیید: <b>{st['waiting']}</b>\n"
            f"⏳ پرداخت در انتظار: {st['pending_pay']}\n👛 مجموع کیف پول کاربران: {fmt(st['wallets'])} تومان\n"
            f"⭐ موجودی ستاره ربات: <b>{bal if bal is not None else 'نامشخص'}</b>")
    await view(cb, text, ikb([[btn("🔄 بروزرسانی", "adm:stats", role="action")], back_row()]))
    await cb.answer()


# ============================================================ صف تحویل
@router.callback_query(F.data == "adm:queue")
async def cb_queue(cb: CallbackQuery, shop: Shop):
    rows = await shop.db.queue_orders(20)
    if not rows:
        await view(cb, "🧾 سفارشی در صف تحویل نیست ✅", ikb([back_row()]))
        await cb.answer()
        return
    kb = [[btn(f"#{o['id']} {o['title']} | {STATUS_FA.get(o['status'], '')[:2]}", f"adm:ord:{o['id']}", role="admin")] for o in rows]
    kb.append([btn("🔁 تلاش مجدد همه", "adm:retryall", role="action")])
    kb.append(back_row())
    await view(cb, f"🧾 <b>صف تحویل</b> ({len(rows)} مورد)", ikb(kb))
    await cb.answer()


@router.callback_query(F.data == "adm:retryall")
async def cb_retryall(cb: CallbackQuery, shop: Shop):
    n = await shop.retry_queue(include_failed=True)
    await cb.answer(f"{n} سفارش دوباره تلاش شد.", show_alert=True)


@router.callback_query(F.data.startswith("adm:ord:"))
async def cb_order(cb: CallbackQuery, shop: Shop):
    o = await shop.db.get_order(int(cb.data.split(":")[2]))
    if not o:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    kb = shop.order_admin_kb(o) if o["status"] in ("manual_pending", "failed", "paid") else ikb([back_row("adm:queue")])
    await view(cb, describe_order(o), kb)
    await cb.answer()


@router.callback_query(F.data.startswith("oadm:"))
async def cb_order_action(cb: CallbackQuery, shop: Shop):
    _, act, oid = cb.data.split(":")
    res = await shop.admin_order_action(act, int(oid), actor=f"tg:{cb.from_user.id}")
    await cb.answer(res, show_alert=True)
    o = await shop.db.get_order(int(oid))
    if o:
        try:
            await cb.message.edit_text(describe_order(o), reply_markup=shop.order_admin_kb(o) if o["status"] in ("manual_pending", "failed", "paid") else None)
        except TelegramBadRequest:
            pass


# ============================================================ پرداخت‌ها
@router.callback_query(F.data == "adm:pays")
async def cb_pays(cb: CallbackQuery, shop: Shop):
    rows = await shop.db.pending_payments(status="waiting_admin")
    if not rows:
        await view(cb, "💳 رسیدی منتظر تأیید نیست ✅", ikb([back_row()]))
        await cb.answer()
        return
    await cb.answer()
    await cb.message.answer(f"💳 {len(rows)} رسید منتظر تأیید (۱۰ مورد آخر در ادامه):")
    for p in rows[-10:]:
        await shop.send_receipt_to(cb.from_user.id, p)
    await cb.message.answer("—", reply_markup=ikb([back_row()]))


@router.callback_query(F.data.startswith("padm:"))
async def cb_pay_action(cb: CallbackQuery, shop: Shop):
    _, act, pid = cb.data.split(":")
    res = await shop.admin_payment_action(act, int(pid), actor=f"tg:{cb.from_user.id}")
    await cb.answer(res, show_alert=True)
    try:
        await cb.message.edit_caption(caption=(cb.message.caption or "") + f"\n\n{res} — توسط {esc(cb.from_user.full_name)}", reply_markup=None)
    except TelegramBadRequest:
        pass


# ============================================================ کاربران
def users_menu_kb():
    rows = [[btn("🔎 جستجوی کاربر (آیدی/یوزرنیم/نام)", "adm:users:q", role="action")]]
    items = list(FILTER_LABELS.items())
    for i in range(0, len(items), 2):
        rows.append([btn(lab, f"adm:ul:{k}:0", role="admin") for k, lab in items[i:i + 2]])
    rows.append(back_row())
    return ikb(rows)


@router.callback_query(F.data == "adm:users")
async def cb_users(cb: CallbackQuery, state: FSMContext, shop: Shop):
    await state.clear()
    n = await shop.db.count_users()
    await view(cb, f"👥 <b>کاربران</b> ({fmt(n)} نفر)\nیک فیلتر انتخاب کنید یا جستجو کنید:", users_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:users:q")
async def cb_users_q(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.user_q)
    await cb.message.answer("👥 آیدی عددی یا یوزرنیم یا نام کاربر را بفرستید (برای لغو /admin):")
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ul:"))
async def cb_user_list(cb: CallbackQuery, shop: Shop):
    _, _, flt, page = cb.data.split(":")
    page = int(page)
    if flt not in USER_FILTERS:
        flt = "all"
    total = await shop.db.count_users("", flt)
    users = await shop.db.search_users("", UPER, page * UPER, flt)
    kb = [[btn(f"{'⛔' if u['banned'] else '👤'} {u['first_name'] or ''} @{u['username'] or '-'} · {fmt(u['balance'])}ت", f"adm:u:{u['id']}", role="admin")] for u in users]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:ul:{flt}:{page - 1}", role="nav"))
    if (page + 1) * UPER < total:
        nav.append(btn("▶️", f"adm:ul:{flt}:{page + 1}", role="nav"))
    if nav:
        kb.append(nav)
    kb.append(back_row("adm:users"))
    await view(cb, f"👥 {FILTER_LABELS.get(flt, flt)} — {fmt(total)} کاربر (صفحه {page + 1})", ikb(kb))
    await cb.answer()


@router.message(Adm.user_q, TXT)
async def got_user_q(m: Message, shop: Shop, state: FSMContext):
    users = await shop.db.search_users(m.text.strip(), limit=10)
    if not users:
        await m.answer("کاربری پیدا نشد. دوباره بفرستید یا /admin.")
        return
    await state.clear()
    if len(users) == 1:
        await show_user(m, shop, users[0]["id"])
        return
    kb = [[btn(f"{u['first_name'] or ''} @{u['username'] or '-'} ({u['id']})", f"adm:u:{u['id']}", role="admin")] for u in users]
    kb.append(back_row("adm:users"))
    await m.answer("نتایج:", reply_markup=ikb(kb))


async def user_card(shop: Shop, uid) -> tuple[str, object] | None:
    u = await shop.db.get_user(uid)
    if not u:
        return None
    st = await shop.db.user_stats(uid)
    ref = await shop.db.referral_stats(uid)
    me = await shop.db.get_referral(uid)
    codes = await shop.db.user_discounts(uid)
    lines = [f"👤 <b>{esc(u['first_name'])}</b> @{esc(u['username'] or '-')}",
             f"آیدی: <code>{uid}</code> · زبان: {esc(u.get('lang') or '-')}",
             f"👛 موجودی: <b>{fmt(u['balance'])}</b> تومان",
             f"🧾 سفارش‌ها: {st['orders']} (تحویل‌شده {st['done']}) · مجموع خرید: {fmt(st['spent'])} ت · سود از او: {fmt(st['profit'])} ت",
             f"🤝 زیرمجموعه: {ref['count']} (تأییدشده {ref['active']}، در انتظار {ref['pending']})",
             f"وضعیت: {'⛔ مسدود' + (' — ' + esc(u['ban_reason']) if u.get('ban_reason') else '') if u['banned'] else '✅ فعال'}"]
    if me:
        lines.append(f"معرف: <code>{me['referrer_id']}</code> ({me['status']})")
    if u.get("personal_discount"):
        lines.append(f"🎁 تخفیف شخصی: {u['personal_discount']}٪")
    if codes:
        lines.append("🏷 کدهای اختصاصی: " + ", ".join(f"<code>{c['code']}</code>" for c in codes[:6]))
    if u.get("note"):
        lines.append(f"📝 {esc(u['note'])}")
    kb = ikb([
        [btn("➕ افزایش", f"adm:ub:{uid}:a", role="confirm"), btn("➖ کاهش", f"adm:ub:{uid}:s", role="reject"),
         btn("🟰 تنظیم دقیق", f"adm:ub:{uid}:t", role="action")],
        [btn("✅ رفع مسدودی" if u["banned"] else "⛔ مسدود کردن", f"adm:ubn:{uid}", role="confirm" if u["banned"] else "reject"),
         btn("✉️ پیام", f"adm:um:{uid}", role="action")],
        [btn("🎁 تخفیف شخصی ٪", f"adm:upd:{uid}", role="action"), btn("🏷 فعال‌سازی کد", f"adm:ucd:{uid}", role="action")],
        [btn("🎟 ساخت کد اختصاصی", f"adm:upc:{uid}", role="action"), btn("📝 یادداشت", f"adm:unote:{uid}", role="action")],
        [btn("🌐 زبان", f"adm:ulg:{uid}", role="admin"), btn("🤝 زیرمجموعه‌ها", f"adm:ur:{uid}", role="admin")],
        [btn("🧾 سفارش‌ها", f"adm:uo:{uid}", role="admin"), btn("💳 پرداخت‌ها", f"adm:up:{uid}", role="admin"),
         btn("👛 کیف پول", f"adm:uw:{uid}", role="admin")],
        back_row("adm:users"),
    ])
    return "\n".join(lines), kb


async def show_user(t, shop: Shop, uid):
    msg = t.message if isinstance(t, CallbackQuery) else t
    card = await user_card(shop, uid)
    if not card:
        await msg.answer("کاربر پیدا نشد.")
        return
    await msg.answer(card[0], reply_markup=card[1])


@router.callback_query(F.data.startswith("adm:u:"))
async def cb_user(cb: CallbackQuery, shop: Shop):
    card = await user_card(shop, int(cb.data.split(":")[2]))
    if not card:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, card[0], card[1])
    await cb.answer()


@router.callback_query(F.data.startswith("adm:uo:"))
async def cb_user_orders(cb: CallbackQuery, shop: Shop):
    uid = int(cb.data.split(":")[2])
    rows = await shop.db.user_orders(uid, 15)
    text = "\n".join(f"#{o['id']} | {esc(o['title'])} | {fmt(o['amount'])} ت | {STATUS_FA.get(o['status'], o['status'])}" for o in rows) or "سفارشی ندارد."
    await cb.message.answer(f"🧾 سفارش‌های کاربر {uid}:\n\n{text}", reply_markup=ikb([back_row(f"adm:u:{uid}")]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:up:"))
async def cb_user_pays(cb: CallbackQuery, shop: Shop):
    uid = int(cb.data.split(":")[2])
    rows = await shop.db.user_payments(uid, 15)
    text = "\n".join(f"P{p['id']} | {METHOD_FA.get(p['method'], p['method'])} | {fmt(p['amount'])} ت | {PAY_STATUS_FA.get(p['status'], p['status'])}" for p in rows) or "پرداختی ندارد."
    await cb.message.answer(f"💳 پرداخت‌های کاربر {uid}:\n\n{text}", reply_markup=ikb([back_row(f"adm:u:{uid}")]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:uw:"))
async def cb_user_wallet(cb: CallbackQuery, shop: Shop):
    uid = int(cb.data.split(":")[2])
    rows = await shop.db.wallet_history(uid, 15)
    text = "\n".join(f"{'+' if t['amount'] > 0 else ''}{fmt(t['amount'])} | {esc(t['reason'][:40])}" for t in rows) or "تراکنشی ندارد."
    await cb.message.answer(f"👛 تراکنش‌های کیف پول {uid}:\n\n{text}", reply_markup=ikb([back_row(f"adm:u:{uid}")]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ur:"))
async def cb_user_refs(cb: CallbackQuery, shop: Shop):
    uid = int(cb.data.split(":")[2])
    rows = await shop.db.list_referrals(uid, 30)
    st = await shop.db.referral_stats(uid)
    label = {"pending": "⏳ منتظر عضویت", "active": "✅ فعال", "left": "🚪 کانال را ترک کرد", "rejected": "❌ رد"}
    text = "\n".join(f"{r['referred_id']} @{r['username'] or '-'} — {label.get(r['status'], r['status'])}" for r in rows) or "ندارد."
    await cb.message.answer(f"🤝 زیرمجموعه‌های {uid}\nپورسانت پرداخت‌شده: {fmt(st['earned'])} ت | در انتظار واریز: {fmt(st['held'])} ت\n\n{text}",
                            reply_markup=ikb([back_row(f"adm:u:{uid}")]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ubn:"))
async def cb_ban(cb: CallbackQuery, shop: Shop, state: FSMContext):
    uid = int(cb.data.split(":")[2])
    u = await shop.db.get_user(uid)
    if not u:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    if shop.is_admin(uid):
        await cb.answer("ادمین را نمی‌توان مسدود کرد.", show_alert=True)
        return
    if u["banned"]:
        ok, msg = await shop.admin_set_ban(uid, False, "", f"tg:{cb.from_user.id}")
        await cb.answer(msg)
        card = await user_card(shop, uid)
        await view(cb, card[0], card[1])
        return
    await state.set_state(Adm.ban_reason)
    await state.update_data(uid=uid)
    await cb.message.answer("دلیل مسدودی را بفرستید (برای بدون دلیل «-» بفرستید؛ برای لغو /admin):")
    await cb.answer()


@router.message(Adm.ban_reason, TXT)
async def got_ban_reason(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    reason = "" if m.text.strip() == "-" else m.text.strip()
    ok, msg = await shop.admin_set_ban(d["uid"], True, reason, f"tg:{m.from_user.id}")
    await m.answer(("✅ " if ok else "❌ ") + msg)
    await show_user(m, shop, d["uid"])


@router.callback_query(F.data.startswith("adm:ub:"))
async def cb_bal(cb: CallbackQuery, state: FSMContext):
    _, _, uid, op = cb.data.split(":")
    await state.set_state(Adm.bal)
    await state.update_data(uid=int(uid), op=op)
    what = {"a": "مبلغ افزایش", "s": "مبلغ کاهش", "t": "موجودی جدید (دقیق)"}[op]
    await cb.message.answer(f"{what} را به تومان بفرستید؛ اگر خواستید دلیل هم بنویسید بعد از عدد، مثلاً:\n<code>50000 جبران خسارت</code>\n(برای لغو /admin)")
    await cb.answer()


@router.message(Adm.bal, TXT)
async def got_bal(m: Message, shop: Shop, state: FSMContext):
    parts = m.text.strip().split(maxsplit=1)
    amt = num(parts[0]) if parts else None
    if amt is None or amt > 10_000_000_000 or (amt == 0 and (await state.get_data())["op"] != "t"):
        await m.answer("فقط یک عدد معتبر (و اختیاری دلیل) بفرستید.")
        return
    d = await state.get_data()
    await state.clear()
    op = {"a": "add", "s": "sub", "t": "set"}[d["op"]]
    ok, msg = await shop.admin_adjust_balance(d["uid"], op, amt, parts[1] if len(parts) > 1 else "", f"tg:{m.from_user.id}")
    await m.answer(("✅ " if ok else "❌ ") + msg)
    await show_user(m, shop, d["uid"])


@router.callback_query(F.data.startswith("adm:um:"))
async def cb_msg_user(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.msg_user)
    await state.update_data(uid=int(cb.data.split(":")[2]))
    await cb.message.answer("متن پیام را بفرستید (برای لغو /admin):")
    await cb.answer()


@router.message(Adm.msg_user, TXT)
async def got_msg_user(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    r = await shop.safe_send(d["uid"], f"📩 {esc(m.text)}")
    await m.answer("✅ ارسال شد." if r else "❌ ارسال نشد (کاربر ربات را بلاک کرده).")


@router.callback_query(F.data.startswith("adm:upd:"))
async def cb_pdisc(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.user_pdisc)
    await state.update_data(uid=int(cb.data.split(":")[2]))
    await cb.message.answer("درصد تخفیف شخصی را بفرستید (۰ تا ۹۰؛ ۰ = حذف). این تخفیف روی همه‌ی خریدهای این کاربر خودکار اعمال می‌شود:")
    await cb.answer()


@router.message(Adm.user_pdisc, TXT)
async def got_pdisc(m: Message, shop: Shop, state: FSMContext):
    v = num(m.text)
    if v is None or v > 90:
        await m.answer("عددی بین ۰ تا ۹۰ بفرستید.")
        return
    d = await state.get_data()
    await state.clear()
    ok, msg = await shop.admin_set_personal_discount(d["uid"], v, f"tg:{m.from_user.id}")
    await m.answer(("✅ " if ok else "❌ ") + msg)
    await show_user(m, shop, d["uid"])


@router.callback_query(F.data.startswith("adm:ucd:"))
async def cb_ucode(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.user_code)
    await state.update_data(uid=int(cb.data.split(":")[2]))
    await cb.message.answer("نام یک کد تخفیف موجود را بفرستید تا برای این کاربر فعال شود (اگر کد برای کاربران مشخصی محدود است، به او هم دسترسی داده می‌شود):")
    await cb.answer()


@router.message(Adm.user_code, TXT)
async def got_ucode(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    ok, msg = await shop.admin_grant_code(d["uid"], m.text.strip(), f"tg:{m.from_user.id}")
    await m.answer(("✅ " if ok else "❌ ") + msg)
    await show_user(m, shop, d["uid"])


@router.callback_query(F.data.startswith("adm:upc:"))
async def cb_upcode(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.user_pcode)
    await state.update_data(uid=int(cb.data.split(":")[2]))
    await cb.message.answer("کد اختصاصی یک‌بارمصرف بساز. فرمت: <code>درصد ساعت-اعتبار</code> مثلاً <code>20 48</code> (۲۰٪ با ۴۸ ساعت اعتبار)\n"
                            "یا مبلغ ثابت: <code>f50000 24</code>. ساعت ۰ = بدون انقضا.")
    await cb.answer()


@router.message(Adm.user_pcode, TXT)
async def got_upcode(m: Message, shop: Shop, state: FSMContext):
    parts = m.text.strip().split()
    try:
        fixed = parts[0].lower().startswith("f")
        val = num(parts[0][1:] if fixed else parts[0])
        hours = num(parts[1]) if len(parts) > 1 else 0
    except IndexError:
        val = hours = None
    if not val or hours is None or (not fixed and val > 100):
        await m.answer("فرمت نامعتبر است. مثال: 20 48")
        return
    d = await state.get_data()
    await state.clear()
    ok, msg, code = await shop.admin_personal_code(d["uid"], 0 if fixed else val, val if fixed else 0, hours, f"tg:{m.from_user.id}")
    await m.answer(("✅ " if ok else "❌ ") + msg + (f"\n<code>{code}</code>" if code else ""))
    await show_user(m, shop, d["uid"])


@router.callback_query(F.data.startswith("adm:unote:"))
async def cb_unote(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.user_note)
    await state.update_data(uid=int(cb.data.split(":")[2]))
    await cb.message.answer("یادداشت مدیریتی را بفرستید («-» برای حذف). کاربر آن را نمی‌بیند:")
    await cb.answer()


@router.message(Adm.user_note, TXT)
async def got_unote(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await shop.db.set_user_note(d["uid"], "" if m.text.strip() == "-" else m.text.strip())
    await shop.db.log(f"tg:{m.from_user.id}", "user_note", str(d["uid"]))
    await m.answer("✅ ذخیره شد.")
    await show_user(m, shop, d["uid"])


@router.callback_query(F.data.startswith("adm:ulg:"))
async def cb_ulang(cb: CallbackQuery, shop: Shop):
    uid = int(cb.data.split(":")[2])
    rows = [[btn(f"{l['flag']} {l['name']}", f"adm:ulang:{uid}:{l['code']}", role="admin")] for l in shop.bot_langs()]
    rows.append(back_row(f"adm:u:{uid}"))
    await view(cb, f"🌐 زبان کاربر {uid} را انتخاب کنید:", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ulang:"))
async def cb_ulang_set(cb: CallbackQuery, shop: Shop):
    _, _, uid, code = cb.data.split(":")
    if shop.resolve_bot_lang(code):
        await shop.set_user_lang(int(uid), code)
        await shop.db.log(f"tg:{cb.from_user.id}", "user_lang", f"{uid}={code}")
    await cb.answer("انجام شد")
    await cb_user(with_data(cb, f"adm:u:{uid}"), shop)


# ============================================================ محصولات
@router.callback_query(F.data == "adm:prod")
async def cb_prod(cb: CallbackQuery, shop: Shop):
    kb = [[btn(t, f"adm:pk:{k}", role="admin")] for k, t in (("stars", "⭐ استارز"), ("premium", "👑 پریمیوم"), ("gift", "🎁 گیفت‌ها"))]
    if shop.can(cb.from_user.id, "pricing"):
        kb.append([btn("💹 نرخ‌ها و سود", "adm:rates", role="admin")])
    kb.append(back_row())
    await view(cb, "📦 <b>محصولات و قیمت</b>", ikb(kb))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:pk:"))
async def cb_prod_kind(cb: CallbackQuery, shop: Shop):
    kind = cb.data.split(":")[2]
    prods = await shop.db.list_products(kind, only_active=False)
    kb = [[btn(f"{'✅' if p['active'] else '🚫'} {p['title']} — {fmt(shop.price(p))} ت{' ✏️' if p['price_override'] else ''}",
               f"adm:p:{p['id']}", role="product")] for p in prods[:40]]
    if kind == "stars":
        kb.append([btn("➕ پکیج جدید", "adm:pnew", role="action")])
    if kind == "gift":
        kb.append([btn("🔄 همگام‌سازی از تلگرام", "adm:gsync", role="action")])
    kb.append(back_row("adm:prod"))
    await view(cb, f"📦 {KIND_FA[kind]} ({len(prods)} مورد)\n✏️ = قیمت دستی", ikb(kb))
    await cb.answer()


@router.callback_query(F.data == "adm:gsync")
async def cb_gsync(cb: CallbackQuery, shop: Shop):
    try:
        n = await shop.sync_gifts()
        await cb.answer(f"{n} گیفت همگام شد.", show_alert=True)
    except Exception as e:
        await cb.answer(f"خطا: {str(e)[:150]}", show_alert=True)


async def product_card(shop: Shop, pid):
    p = await shop.db.get_product(pid)
    if not p:
        return None
    price, cost = shop.pricing.quote(p)
    margin = shop.pricing.margin_for(p)
    text = (f"📦 <b>{esc(p['title'])}</b>\nنوع: {KIND_FA[p['kind']]} | تعداد/ماه/ستاره: {p['qty']}\n"
            f"قیمت فروش: <b>{fmt(price)}</b> تومان {'(دستی)' if p['price_override'] else '(محاسبه‌ی خودکار)'}\n"
            f"🔒 هزینه‌ی تمام‌شده: {fmt(cost)} | سود: {fmt(price - cost)} ({margin}٪{' مخصوص این محصول' if p['margin_override'] is not None else ''})\n"
            f"وضعیت: {'✅ فعال' if p['active'] else '🚫 غیرفعال'}")
    kb = ikb([
        [btn("🚫 غیرفعال کردن" if p["active"] else "✅ فعال کردن", f"adm:pt:{pid}", role="reject" if p["active"] else "confirm")],
        [btn("💰 قیمت دستی", f"adm:pp:{pid}", role="action"), btn("🧮 قیمت خودکار", f"adm:pc:{pid}", role="action")],
        [btn("📈 سود مخصوص", f"adm:pm:{pid}", role="action"), btn("↩️ سود پایه", f"adm:pmc:{pid}", role="action")],
        [btn("✏️ تغییر عنوان", f"adm:ptt:{pid}", role="action")],
        back_row(f"adm:pk:{p['kind']}"),
    ])
    return text, kb


@router.callback_query(F.data.startswith("adm:p:"))
async def cb_product(cb: CallbackQuery, shop: Shop):
    card = await product_card(shop, int(cb.data.split(":")[2]))
    if not card:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, *card)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:pt:"))
async def cb_ptoggle(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[2])
    await shop.db.toggle_product(pid)
    await shop.db.log(f"tg:{cb.from_user.id}", "product_toggle", str(pid))
    await cb_product(with_data(cb, f"adm:p:{pid}"), shop)


@router.callback_query(F.data.startswith("adm:pc:"))
async def cb_pclear(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[2])
    await shop.db.set_price_override(pid, None)
    await cb_product(with_data(cb, f"adm:p:{pid}"), shop)


@router.callback_query(F.data.startswith("adm:pmc:"))
async def cb_pmclear(cb: CallbackQuery, shop: Shop):
    pid = int(cb.data.split(":")[2])
    await shop.db.set_margin_override(pid, None)
    await cb_product(with_data(cb, f"adm:p:{pid}"), shop)


@router.callback_query(F.data.startswith("adm:pp:"))
async def cb_pprice(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.price)
    await state.update_data(pid=int(cb.data.split(":")[2]))
    await cb.message.answer("قیمت جدید را به تومان بفرستید (برای لغو /admin):")
    await cb.answer()


@router.message(Adm.price, TXT)
async def got_price(m: Message, shop: Shop, state: FSMContext):
    v = num(m.text)
    if not v or v < 1000:
        await m.answer("عدد معتبر (حداقل ۱٬۰۰۰ تومان) بفرستید.")
        return
    d = await state.get_data()
    await shop.db.set_price_override(d["pid"], v)
    await shop.db.log(f"tg:{m.from_user.id}", "product_price", f"{d['pid']}={v}")
    await state.clear()
    await m.answer(f"✅ قیمت دستی روی {fmt(v)} تومان تنظیم شد.", reply_markup=ikb([back_row(f"adm:p:{d['pid']}")]))


@router.callback_query(F.data.startswith("adm:pm:"))
async def cb_pmargin(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.margin)
    await state.update_data(pid=int(cb.data.split(":")[2]))
    await cb.message.answer("درصد سود مخصوص این محصول را بفرستید (مثلاً 12.5). کاربر آن را نمی‌بیند:")
    await cb.answer()


@router.message(Adm.margin, TXT)
async def got_margin(m: Message, shop: Shop, state: FSMContext):
    v = D(m.text, -1)
    if v < 0 or v > 1000:
        await m.answer("عدد معتبر بفرستید.")
        return
    d = await state.get_data()
    await shop.db.set_margin_override(d["pid"], float(v))
    await shop.db.log(f"tg:{m.from_user.id}", "product_margin", f"{d['pid']}={v}")
    await state.clear()
    await m.answer(f"✅ سود این محصول {v}٪ شد.", reply_markup=ikb([back_row(f"adm:p:{d['pid']}")]))


@router.callback_query(F.data.startswith("adm:ptt:"))
async def cb_ptitle(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.title)
    await state.update_data(pid=int(cb.data.split(":")[2]))
    await cb.message.answer("عنوان جدید محصول را بفرستید (برای لغو /admin):")
    await cb.answer()


@router.message(Adm.title, TXT)
async def got_title(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await shop.db.set_product_title(d["pid"], m.text.strip()[:60])
    await state.clear()
    await m.answer("✅ عنوان تغییر کرد.", reply_markup=ikb([back_row(f"adm:p:{d['pid']}")]))


@router.callback_query(F.data == "adm:pnew")
async def cb_pnew(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.new_pack)
    await cb.message.answer("تعداد ستاره پکیج جدید را بفرستید (مثلاً 750):")
    await cb.answer()


@router.message(Adm.new_pack, TXT)
async def got_new_pack(m: Message, shop: Shop, state: FSMContext):
    q = num(m.text)
    if not q or q < 1 or q > 1_000_000:
        await m.answer("عدد معتبر بفرستید.")
        return
    await state.clear()
    ok = await shop.db.add_stars_pack(q)
    await m.answer("✅ پکیج اضافه شد." if ok else "این پکیج از قبل وجود دارد.", reply_markup=ikb([back_row("adm:pk:stars")]))


# ============================================================ ستاره‌های ربات
@router.callback_query(F.data == "adm:stars")
async def cb_stars(cb: CallbackQuery, shop: Shop):
    bal = await shop.star_balance()
    extra = ""
    s = await shop.settings()
    if s.get("prov_balance_url"):
        await shop._check_provider_balance(force=True)
        extra = f"\n🔌 موجودی حساب سرویس‌دهنده: <b>{provider.fmt_balance(shop.provider_balance)}</b>"
    await view(cb, f"⭐ <b>موجودی ستاره ربات:</b> {bal if bal is not None else 'نامشخص'}{extra}\n\n"
                   "پریمیوم و گیفت‌ها (در حالت «bot») از همین موجودی تحویل داده می‌شوند. می‌توانید با ستاره‌های خودتان ربات را شارژ کنید.",
               ikb([[btn("➕ شارژ با ستاره", "adm:stopup", role="confirm"), btn("🔁 تلاش مجدد صف", "adm:retryall", role="action")],
                    [btn("🔄 بروزرسانی", "adm:stars", role="action")], back_row()]))
    await cb.answer()


@router.callback_query(F.data == "adm:stopup")
async def cb_stopup(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.topup)
    await cb.message.answer("تعداد ستاره برای شارژ ربات را بفرستید (برای لغو /admin):")
    await cb.answer()


@router.message(Adm.topup, TXT)
async def got_topup(m: Message, shop: Shop, state: FSMContext):
    n = num(m.text)
    if not n or not 1 <= n <= 100_000:
        await m.answer("عدد معتبر (۱ تا ۱۰۰٬۰۰۰) بفرستید.")
        return
    await state.clear()
    await shop.bot.send_invoice(m.chat.id, title="شارژ موجودی ستاره ربات", description=f"{n} ستاره به موجودی ربات اضافه می‌شود",
                                payload="botstars", currency="XTR", prices=[LabeledPrice(label="Stars", amount=n)])


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, shop: Shop):
    await q.answer(ok=(q.invoice_payload == "botstars" and shop.can(q.from_user.id, "stars")), error_message="درخواست نامعتبر")


@router.message(F.successful_payment)
async def paid_stars(m: Message, shop: Shop):
    sp = m.successful_payment
    if sp.invoice_payload != "botstars":
        return
    await shop.db.log(f"tg:{m.from_user.id}", "bot_stars_topup", str(sp.total_amount))
    n = await shop.retry_queue()
    await m.answer(f"✅ {sp.total_amount} ستاره به ربات اضافه شد." + (f"\n🔁 {n} سفارش صف دوباره تلاش شد." if n else ""))


# ============================================================ بکاپ و وارد کردن بکاپ
@router.callback_query(F.data == "adm:backup")
async def cb_backup_menu(cb: CallbackQuery):
    await view(cb, "💾 <b>بکاپ و وارد کردن بکاپ</b>\n\n• «بکاپ کامل» فایل zip شامل دیتابیس (کاربران، سفارش‌ها، تنظیمات، ترجمه‌ها، دکمه‌ها ...) است.\n"
                   "• «با .env» توکن ربات و رمزها هم داخل فایل می‌رود؛ نگهداری امن!\n"
                   "• «وارد کردن بکاپ»: فایل .zip / .db / .tar.gz را بفرستید تا ربات از روی آن بالا بیاید (تا ۲۰ مگابایت؛ برای فایل بزرگ‌تر از پنل وب استفاده کنید).",
         ikb([[btn("📦 بکاپ کامل (zip)", "adm:backup:zip", role="confirm"), btn("🔐 با .env", "adm:backup:env", role="action")],
              [btn("📥 وارد کردن بکاپ", "adm:restore", role="reject")], back_row()]))
    await cb.answer()


@router.callback_query(F.data.in_({"adm:backup:zip", "adm:backup:env"}))
async def cb_backup(cb: CallbackQuery, shop: Shop):
    inc = cb.data.endswith("env")
    if inc and shop.admin_perms.get(cb.from_user.id) != "*":
        await cb.answer("فقط مدیر کامل می‌تواند بکاپ شامل .env و توکن بگیرد.", show_alert=True)
        return
    data = await bk.make_bundle(shop, include_env=inc)
    await cb.message.answer_document(BufferedInputFile(data, filename=f"starshop-backup-{datetime.now():%Y%m%d-%H%M}.zip"),
                                     caption="💾 بکاپ کامل" + (" (شامل .env و توکن)" if inc else "") + " — شامل اطلاعات حساس؛ نگهداری امن")
    await shop.db.log(f"tg:{cb.from_user.id}", "backup", "env" if inc else "db")
    await cb.answer()


@router.callback_query(F.data == "adm:restore")
async def cb_restore(cb: CallbackQuery, state: FSMContext, shop: Shop):
    if shop.admin_perms.get(cb.from_user.id) != "*":     # دیتابیس شامل جدول ادمین‌هاست
        await cb.answer("فقط مدیر کامل می‌تواند بکاپ را بازگردانی کند.", show_alert=True)
        return
    await state.set_state(Adm.restore)
    await cb.message.answer("📥 فایل بکاپ (.zip یا .db یا .tar.gz) را همین‌جا به‌صورت «فایل» بفرستید. (برای لغو /admin)\n"
                            "⚠️ قبل از جایگزینی، از وضعیت فعلی یک بکاپ ایمنی کنار دیتابیس گرفته می‌شود.")
    await cb.answer()


@router.message(Adm.restore, F.document)
async def got_restore_file(m: Message, shop: Shop, state: FSMContext):
    if shop.admin_perms.get(m.from_user.id) != "*":
        await state.clear()
        return
    if (m.document.file_size or 0) > 20 * 1024 * 1024:
        await m.answer("❌ حجم فایل بیش از ۲۰ مگابایت است؛ از «پنل وب ← وارد کردن بکاپ» استفاده کنید.")
        return
    buf = io.BytesIO()
    try:
        await shop.bot.download(m.document, destination=buf)
        db_bytes, env, meta = bk.extract_upload(buf.getvalue())
        path, info = bk.stage_db(db_bytes)
    except ValueError as e:
        await m.answer(f"❌ {esc(str(e))}")
        return
    except Exception as e:  # noqa: BLE001
        await m.answer(f"❌ دریافت فایل ممکن نشد: {esc(str(e)[:120])}")
        return
    await state.update_data(path=path, env=env)
    kb = [[btn("✅ جایگزین کن", "adm:restore:ok", role="reject")]]
    if env:
        kb = [[btn("✅ جایگزین کن (فقط دیتابیس)", "adm:restore:ok", role="reject")],
              [btn("✅ دیتابیس + .env", "adm:restore:env", role="reject")]]
    kb.append([btn("❌ انصراف", "adm:home", role="back")])
    await m.answer(f"📥 فایل معتبر است:\nنام فروشگاه: {esc(info['shop_name'] or '-')}\nکاربران: {fmt(info['users'])} | سفارش‌ها: {fmt(info['orders'])} | "
                   f"محصولات: {info['products']}\nحجم: {info['size'] // 1024} KB\n\n⚠️ دیتابیس فعلی با این فایل جایگزین می‌شود. ادامه می‌دهید؟",
                   reply_markup=ikb(kb))


@router.message(Adm.restore)
async def restore_wrong(m: Message):
    await m.answer("لطفاً فایل بکاپ را به‌صورت «سند/فایل» بفرستید یا /admin بزنید.")


@router.callback_query(F.data.in_({"adm:restore:ok", "adm:restore:env"}))
async def cb_restore_go(cb: CallbackQuery, shop: Shop, state: FSMContext):
    if shop.admin_perms.get(cb.from_user.id) != "*":
        await cb.answer("فقط مدیر کامل.", show_alert=True)
        return
    d = await state.get_data()
    await state.clear()
    if not d.get("path") or not os.path.exists(d["path"]):
        await cb.answer("فایلی در انتظار نیست؛ دوباره شروع کنید.", show_alert=True)
        return
    env = d.get("env") if cb.data.endswith("env") else None
    try:
        res = await bk.apply_restore(shop, d["path"], env)
    except Exception as e:  # noqa: BLE001
        await cb.message.answer(f"❌ بازگردانی ناموفق: {esc(str(e)[:200])}")
        await cb.answer()
        return
    await shop.db.log(f"tg:{cb.from_user.id}", "restore", f"users={res['info']['users']}")
    await cb.message.answer("✅ بکاپ بازگردانی شد." + ("\n🔄 ربات برای اعمال .env دوباره راه‌اندازی می‌شود (اگر سرویس خودکار نیست دستی اجرا کنید)." if res["restart"] else "")
                            + f"\nبکاپ ایمنی: <code>{esc(os.path.basename(res['safety']))}</code>")
    await cb.answer()
    if res["restart"]:
        shop.request_restart()
