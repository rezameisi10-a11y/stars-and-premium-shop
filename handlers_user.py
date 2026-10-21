import re

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, KeyboardButton, KeyboardButtonRequestUsers, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from i18n import CATALOG, N_
from service import STATUS_FA, Shop
from ui import btn, esc, fmt, ikb

router = Router()
router.message.filter(F.chat.type == "private")

PER_PAGE = 8
CAT_TITLE = {"stars": N_("⭐ خرید استارز"), "premium": N_("👑 اشتراک پریمیوم"), "gift": N_("🎁 گیفت تلگرام")}
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")


def txt_is(src):
    """فیلتر متن دکمه‌ی کیبورد پاسخ در همه‌ی زبان‌ها."""
    return F.text.func(lambda t, s=src: t in CATALOG.variants("bot", s))


class Buy(StatesGroup):
    target = State()
    receipt = State()
    topup = State()
    discount = State()


def to_num(text: str) -> str:
    return text.translate(DIGITS).replace(",", "").replace("٬", "").strip()


def otitle(shop: Shop, o: dict, lang: str) -> str:
    return shop.product_title({"id": o.get("product_id"), "kind": o["kind"], "qty": o["qty"], "title": o["title"]}, lang)


async def show_menu(target, shop: Shop, lang: str, edit=False):
    uid = target.from_user.id
    kb = await shop.main_menu_kb(uid)
    text = shop.text("welcome_text", lang)
    if edit and isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    msg = target.message if isinstance(target, CallbackQuery) else target
    await msg.answer(text, reply_markup=kb)


def lang_kb(shop: Shop, cur: str):
    rows, row = [], []
    for l in shop.bot_langs():
        mark = "✅ " if l["code"] == cur else ""
        row.append(btn(f"{mark}{l['flag']} {l['name']}".strip(), f"setlang:{l['code']}", style=""))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ikb(rows)


@router.message(CommandStart())
async def start(m: Message, command: CommandObject, shop: Shop, state: FSMContext, lang: str, tr, is_new: bool = False):
    await state.clear()
    s = await shop.settings()
    if is_new and s.get("lang_prompt") == "1" and len(shop.bot_langs()) > 1:
        await m.answer("🌐 Please choose your language / زبان خود را انتخاب کنید", reply_markup=lang_kb(shop, lang))
        return
    await show_menu(m, shop, lang)


@router.message(F.text == "/language")
async def cmd_language(m: Message, shop: Shop, lang: str):
    await m.answer("🌐 Please choose your language / زبان خود را انتخاب کنید", reply_markup=lang_kb(shop, lang))


@router.callback_query(F.data == "lang")
async def lang_menu(cb: CallbackQuery, shop: Shop, lang: str):
    await cb.message.answer("🌐 Please choose your language / زبان خود را انتخاب کنید", reply_markup=lang_kb(shop, lang))
    await cb.answer()


@router.callback_query(F.data.startswith("setlang:"))
async def set_lang(cb: CallbackQuery, shop: Shop, state: FSMContext):
    code = cb.data.split(":", 1)[1]
    if not shop.resolve_bot_lang(code):
        await cb.answer("—", show_alert=True)
        return
    await state.clear()
    await shop.set_user_lang(cb.from_user.id, code)
    tr = shop.tr_lang(code)
    await cb.answer(tr("✅ زبان تغییر کرد."))
    try:
        await cb.message.delete()
    except Exception:
        pass
    await show_menu(cb, shop, code)


@router.callback_query(F.data == "menu")
async def menu(cb: CallbackQuery, shop: Shop, state: FSMContext, lang: str):
    await state.clear()
    await show_menu(cb, shop, lang, edit=True)
    await cb.answer()


@router.callback_query(F.data == "jchk")
async def join_check(cb: CallbackQuery, shop: Shop, lang: str, tr):
    shop._join_cache.pop(cb.from_user.id, None)
    if await shop.is_member(cb.from_user.id):
        await shop.on_gate_pass(cb.from_user.id)
        await cb.answer(tr("✅ عضویت تأیید شد"))
        await show_menu(cb, shop, lang)
    else:
        await cb.answer(tr("هنوز عضو کانال نشده‌اید."), show_alert=True)


@router.callback_query(F.data == "support")
async def support(cb: CallbackQuery, shop: Shop, lang: str, tr):
    s = await shop.settings()
    u = s["support_username"].lstrip("@")
    text = shop.text("support_text", lang) + (("\n\n" + tr("☎️ پشتیبانی: {u}", u="@" + esc(u))) if u else "")
    await cb.message.answer(text.strip() or tr("☎️ پشتیبانی هنوز تنظیم نشده است."))
    await cb.answer()


@router.callback_query(F.data == "rules")
async def rules(cb: CallbackQuery, shop: Shop, lang: str, tr):
    await cb.message.answer(shop.text("rules_text", lang) or tr("قوانینی ثبت نشده است."))
    await cb.answer()


@router.callback_query(F.data.startswith("txt:"))
async def custom_text(cb: CallbackQuery, shop: Shop, lang: str):
    b = await shop.db.get_menu_button(int(cb.data.split(":")[1]))
    if b and b["action"] == "text" and b["enabled"]:
        await cb.message.answer(shop.button_text(b, lang) or "—")
    await cb.answer()


@router.callback_query(F.data == "referral")
async def referral(cb: CallbackQuery, shop: Shop, tr):
    s = await shop.settings()
    st = await shop.db.referral_stats(cb.from_user.id)
    link = f"https://t.me/{shop.bot_username}?start=ref_{cb.from_user.id}"
    pct = s.get("referral_percent", "0")
    bonus = int(float(s.get("referral_join_bonus") or 0))
    need = shop._need_join()
    text = (tr("🤝 <b>زیرمجموعه‌گیری</b>") + "\n"
            + tr("با لینک اختصاصی خود دوستانتان را دعوت کنید؛ از هر سفارش تحویل‌شده‌ی آن‌ها <b>{pct}٪</b> پورسانت به کیف پولتان اضافه می‌شود.", pct=esc(pct)))
    if bonus:
        text += "\n" + tr("🎁 به‌ازای هر دوست تأییدشده <b>{b}</b> تومان جایزه می‌گیرید.", b=fmt(bonus))
    if need:
        text += "\n" + tr("⚠️ جایزه وقتی فعال می‌شود که دوست شما در کانال ما عضو شود و عضو بماند.")
    text += ("\n\n" + tr("🔗 لینک شما:") + f"\n<code>{link}</code>\n\n"
             + tr("👥 تعداد دعوت‌شده‌ها: <b>{n}</b>", n=st["count"]) + "\n"
             + tr("✅ تأییدشده: <b>{n}</b>", n=st["active"]) + (("  |  " + tr("⏳ در انتظار عضویت: <b>{n}</b>", n=st["pending"])) if st["pending"] else "") + "\n"
             + tr("💰 مجموع پورسانت: <b>{n}</b> تومان", n=fmt(st["earned"])))
    if st["held"]:
        text += "\n" + tr("🕓 در انتظار واریز: <b>{n}</b> تومان", n=fmt(st["held"]))
    await cb.message.answer(text, reply_markup=ikb([[btn(tr("🔙 منوی اصلی"), "menu", role="back")]]))
    await cb.answer()


# ---------- کاتالوگ ----------
@router.callback_query(F.data.startswith("cat:"))
async def catalog(cb: CallbackQuery, shop: Shop, lang: str, tr):
    _, kind, page = cb.data.split(":")
    page = int(page)
    prods = await shop.db.list_products(kind)
    if not prods:
        await cb.answer(tr("فعلاً موردی برای فروش موجود نیست."), show_alert=True)
        return
    u = await shop.db.get_user(cb.from_user.id)
    chunk = prods[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = [[btn(f"{shop.product_title(p, lang)} — {shop.money(shop.price_u(p, u), tr, short=True)}", f"buy:{p['id']}", role="product")] for p in chunk]
    nav = []
    if page > 0:
        nav.append(btn(tr("◀️ قبلی"), f"cat:{kind}:{page - 1}", role="nav"))
    if (page + 1) * PER_PAGE < len(prods):
        nav.append(btn(tr("بعدی ▶️"), f"cat:{kind}:{page + 1}", role="nav"))
    if nav:
        rows.append(nav)
    rows.append([btn(tr("🔙 منوی اصلی"), "menu", role="back")])
    text = tr(CAT_TITLE[kind]) + "\n" + tr("محصول مورد نظر را انتخاب کنید:")
    if u and u.get("personal_discount"):
        text += "\n" + tr("🎁 تخفیف اختصاصی شما ({p}٪) در قیمت‌ها اعمال شده است.", p=u["personal_discount"])
    try:
        await cb.message.edit_text(text, reply_markup=ikb(rows))
    except Exception:
        await cb.message.answer(text, reply_markup=ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy(cb: CallbackQuery, shop: Shop, state: FSMContext, lang: str, tr):
    p = await shop.db.get_product(int(cb.data.split(":")[1]))
    if not p or not p["active"]:
        await cb.answer(tr("این محصول در دسترس نیست."), show_alert=True)
        return
    await state.set_state(Buy.target)
    await state.update_data(product_id=p["id"])
    title = esc(shop.product_title(p, lang))
    if p["kind"] == "stars":
        rows = []
        if cb.from_user.username:
            rows.append([KeyboardButton(text=tr("🙋 برای خودم"))])
        rows.append([KeyboardButton(text=tr("❌ انصراف"))])
        await cb.message.answer(
            f"{title}\n" + tr("یوزرنیم تلگرام گیرنده را بفرستید (مثال: @username):"),
            reply_markup=ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True))
    else:
        rows = [[KeyboardButton(text=tr("👤 انتخاب گیرنده"), request_users=KeyboardButtonRequestUsers(
                    request_id=1, user_is_bot=False, max_quantity=1))],
                [KeyboardButton(text=tr("🙋 برای خودم"))], [KeyboardButton(text=tr("❌ انصراف"))]]
        await cb.message.answer(f"{title}\n" + tr("گیرنده را انتخاب کنید:"),
                                reply_markup=ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True))
    await cb.answer()


def order_text(o, tr, title, shop=None) -> str:
    who = f"@{esc(o['target_username'])}" if o["target_username"] else f"<code>{o['target_id']}</code>"
    disc = ""
    if o.get("discount_code") and o.get("orig_amount") and o["orig_amount"] != o["amount"]:
        disc = "\n" + tr("قیمت اصلی: {orig} | کد {code}", orig=f"<s>{fmt(o['orig_amount'])}</s>", code=esc(o["discount_code"]))
    return (tr("🧾 سفارش #{oid}", oid=o["id"]) + f"\n{esc(title)}\n" + tr("گیرنده: {who}", who=who) + f"{disc}\n"
            + tr("مبلغ: {amount}", amount=f"<b>{shop.money(o['amount'], tr) if shop else fmt(o['amount'])}</b>")
            + (("\n" + shop.crypto_line(o["amount"])) if shop and shop.crypto_line(o["amount"]) else "")
            + "\n\n" + tr("روش پرداخت را انتخاب کنید:"))


async def _create_and_offer(m: Message, shop: Shop, state: FSMContext, lang: str, tr, target_id, target_username):
    data = await state.get_data()
    p = await shop.db.get_product(data["product_id"])
    await state.clear()
    if not p or not p["active"]:
        await m.answer(tr("این محصول دیگر در دسترس نیست."), reply_markup=ReplyKeyboardRemove())
        return
    u = await shop.db.get_user(m.from_user.id)
    price, cost = await shop.quote_order(p, u)
    oid = await shop.db.create_order(m.from_user.id, p, target_id, target_username, price, cost)
    o = await shop.db.get_order(oid)
    await m.answer(tr("✅ گیرنده ثبت شد."), reply_markup=ReplyKeyboardRemove())
    await m.answer(order_text(o, tr, shop.product_title(p, lang), shop),
                   reply_markup=await shop.pay_methods_kb(str(oid), m.from_user.id, allow_wallet=True, tr=tr))


@router.message(Buy.target, txt_is("❌ انصراف"))
async def cancel_target(m: Message, shop: Shop, state: FSMContext, lang: str, tr):
    await state.clear()
    await m.answer(tr("لغو شد."), reply_markup=ReplyKeyboardRemove())
    await show_menu(m, shop, lang)


@router.message(Buy.target, F.users_shared)
async def got_shared(m: Message, shop: Shop, state: FSMContext, lang: str, tr):
    data = await state.get_data()
    p = await shop.db.get_product(data["product_id"])
    if p and p["kind"] == "stars":
        await m.answer(tr("برای استارز، یوزرنیم گیرنده را تایپ کنید."))
        return
    await _create_and_offer(m, shop, state, lang, tr, m.users_shared.users[0].user_id, None)


@router.message(Buy.target, txt_is("🙋 برای خودم"))
async def for_me(m: Message, shop: Shop, state: FSMContext, lang: str, tr):
    data = await state.get_data()
    p = await shop.db.get_product(data["product_id"])
    if p and p["kind"] == "stars":
        if not m.from_user.username:
            await m.answer(tr("شما یوزرنیم ندارید؛ یوزرنیم گیرنده را تایپ کنید."))
            return
        await _create_and_offer(m, shop, state, lang, tr, m.from_user.id, m.from_user.username)
    else:
        await _create_and_offer(m, shop, state, lang, tr, m.from_user.id, None)


@router.message(Buy.target, F.text)
async def got_username(m: Message, shop: Shop, state: FSMContext, lang: str, tr):
    data = await state.get_data()
    p = await shop.db.get_product(data["product_id"])
    if p and p["kind"] != "stars":
        await m.answer(tr("از دکمه‌های پایین صفحه برای انتخاب گیرنده استفاده کنید."))
        return
    mt = USERNAME_RE.match(m.text.strip())
    if not mt:
        await m.answer(tr("یوزرنیم نامعتبر است. مثال: @username"))
        return
    await _create_and_offer(m, shop, state, lang, tr, None, mt.group(1))


# ---------- کد تخفیف ----------
@router.callback_query(F.data.startswith("disc:"))
async def ask_discount(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    o = await shop.db.get_order(int(cb.data.split(":")[1]))
    if not o or o["user_id"] != cb.from_user.id or o["status"] != "pending_payment":
        await cb.answer(tr("این سفارش دیگر قابل ویرایش نیست."), show_alert=True)
        return
    await state.set_state(Buy.discount)
    await state.update_data(order_id=o["id"])
    await cb.message.answer(tr("🏷 کد تخفیف را بفرستید (برای انصراف /start):"))
    await cb.answer()


@router.message(Buy.discount, F.text)
async def got_discount(m: Message, shop: Shop, state: FSMContext, lang: str, tr):
    data = await state.get_data()
    ok, msg, new_amount = await shop.apply_discount(data["order_id"], m.from_user.id, m.text, tr)
    await m.answer(msg)
    if ok:
        await state.clear()
        o = await shop.db.get_order(data["order_id"])
        await m.answer(order_text(o, tr, otitle(shop, o, lang), shop),
                       reply_markup=await shop.pay_methods_kb(str(o["id"]), m.from_user.id, allow_wallet=True, tr=tr))


# ---------- مرحله‌ی «پرداخت»: انتخاب روش ----------
async def _own_pending(cb, shop, tr, oid):
    o = await shop.db.get_order(int(oid))
    if not o or o["user_id"] != cb.from_user.id or o["status"] != "pending_payment":
        await cb.answer(tr("این سفارش دیگر قابل پرداخت نیست."), show_alert=True)
        return None
    return o


@router.callback_query(F.data.startswith("pm:"))
async def pay_methods(cb: CallbackQuery, shop: Shop, lang: str, tr):
    o = await _own_pending(cb, shop, tr, cb.data.split(":")[1])
    if o:
        await cb.message.answer(order_text(o, tr, otitle(shop, o, lang), shop),
                                reply_markup=await shop.pay_methods_kb(str(o["id"]), cb.from_user.id, True, tr, step="methods"))
        await cb.answer()


@router.callback_query(F.data.startswith("ord:"))
async def back_to_order(cb: CallbackQuery, shop: Shop, lang: str, tr):
    o = await _own_pending(cb, shop, tr, cb.data.split(":")[1])
    if o:
        await cb.message.answer(order_text(o, tr, otitle(shop, o, lang), shop),
                                reply_markup=await shop.pay_methods_kb(str(o["id"]), cb.from_user.id, True, tr))
        await cb.answer()


# ---------- پرداخت ----------
@router.callback_query(F.data.startswith("pay:"))
async def pay(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    _, method, ctx = cb.data.split(":")
    uid = cb.from_user.id
    order = None
    if ctx.startswith("t"):
        amount = int(ctx[1:])
        if method == "wallet":
            await cb.answer(tr("برای شارژ نمی‌توان از کیف پول استفاده کرد."), show_alert=True)
            return
    else:
        order = await shop.db.get_order(int(ctx))
        if not order or order["user_id"] != uid or order["status"] != "pending_payment":
            await cb.answer(tr("این سفارش دیگر قابل پرداخت نیست."), show_alert=True)
            return
        amount = order["amount"]

    if method == "wallet":
        if await shop.pay_with_wallet(order["id"], uid):
            await cb.message.answer(tr("✅ پرداخت از کیف پول انجام شد؛ سفارش #{oid} در حال تحویل است.", oid=order["id"]))
            await cb.answer()
        else:
            await cb.answer(tr("موجودی کیف پول کافی نیست."), show_alert=True)
        return

    try:
        pid, text, kb = await shop.create_payment(method, uid, amount, order["id"] if order else None, tr)
    except ValueError as e:
        await cb.answer(str(e), show_alert=True)
        return
    if method == "card":
        await state.set_state(Buy.receipt)
        await state.update_data(payment_id=pid)
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.message(Buy.receipt, F.photo)
async def receipt(m: Message, shop: Shop, state: FSMContext, tr):
    data = await state.get_data()
    p = await shop.db.get_payment(data["payment_id"])
    if not p or p["status"] != "pending":
        await state.clear()
        await m.answer(tr("این پرداخت دیگر فعال نیست. دوباره از منو اقدام کنید."))
        return
    await shop.submit_receipt(p["id"], m.photo[-1].file_id)
    await state.clear()
    await m.answer(tr("🧾 رسید دریافت شد. پس از بررسی ادمین، نتیجه همین‌جا اعلام می‌شود."))


@router.message(Buy.receipt)
async def receipt_wrong(m: Message, tr):
    await m.answer(tr("لطفاً فقط عکس رسید را ارسال کنید. برای خروج /start را بزنید."))


@router.callback_query(F.data.startswith("chk:"))
async def check_payment(cb: CallbackQuery, shop: Shop, tr):
    pid = int(cb.data.split(":")[1])
    p = await shop.db.get_payment(pid)
    if not p or p["user_id"] != cb.from_user.id:
        await cb.answer(tr("پرداخت پیدا نشد."), show_alert=True)
        return
    if p["status"] == "pending":
        await shop.check_crypto()
        p = await shop.db.get_payment(pid)
    texts = {"confirmed": tr("✅ پرداخت شما تأیید شده است."), "pending": tr("⏳ هنوز پرداختی دریافت نشده است."),
             "expired": tr("⌛ مهلت این پرداخت تمام شده است."), "rejected": tr("❌ پرداخت رد شده است.")}
    await cb.answer(texts.get(p["status"], p["status"]), show_alert=True)


# ---------- کیف پول ----------
@router.callback_query(F.data == "wallet")
async def wallet(cb: CallbackQuery, shop: Shop, tr):
    u = await shop.db.get_user(cb.from_user.id)
    hist = await shop.db.wallet_history(cb.from_user.id, limit=5)
    lines = "\n".join(f"{'➕' if t['amount'] > 0 else '➖'} {fmt(abs(t['amount']))} {tr('ت')} — {esc(t['reason'].split(' ')[0].split(':')[0])}" for t in hist)
    await cb.message.answer(
        tr("👛 موجودی کیف پول شما: <b>{b}</b> تومان", b=fmt(u["balance"])) + (("\n\n" + tr("آخرین تراکنش‌ها:") + f"\n{lines}") if lines else ""),
        reply_markup=ikb([[btn(tr("➕ شارژ کیف پول"), "topup", role="action")], [btn(tr("🔙 منوی اصلی"), "menu", role="back")]]))
    await cb.answer()


@router.callback_query(F.data == "topup")
async def topup(cb: CallbackQuery, shop: Shop, state: FSMContext, tr):
    s = await shop.settings()
    await state.set_state(Buy.topup)
    await cb.message.answer(tr("مبلغ شارژ را به تومان بفرستید (حداقل {lo}):", lo=fmt(s["min_topup"])))
    await cb.answer()


@router.message(Buy.topup, F.text)
async def topup_amount(m: Message, shop: Shop, state: FSMContext, tr):
    s = await shop.settings()
    raw = to_num(m.text)
    lo, hi = int(float(s["min_topup"])), int(float(s["max_topup"]))
    if not raw.isdigit() or int(raw) < lo or int(raw) > hi:
        await m.answer(tr("مبلغ نامعتبر است. بین {lo} تا {hi} تومان، فقط عدد.", lo=fmt(lo), hi=fmt(hi)))
        return
    amount = int(raw)
    await state.clear()
    kb = await shop.pay_methods_kb(f"t{amount}", m.from_user.id, allow_wallet=False, tr=tr)
    await m.answer(tr("شارژ کیف پول به مبلغ <b>{amount}</b> تومان", amount=fmt(amount)) + "\n" + tr("روش پرداخت را انتخاب کنید:"), reply_markup=kb)


# ---------- سفارش‌ها ----------
@router.callback_query(F.data == "orders")
async def my_orders(cb: CallbackQuery, shop: Shop, lang: str, tr):
    rows = await shop.db.user_orders(cb.from_user.id)
    if not rows:
        await cb.answer(tr("هنوز سفارشی ندارید."), show_alert=True)
        return
    lines = [f"#{o['id']} | {esc(otitle(shop, o, lang))} | {fmt(o['amount'])} {tr('ت')} | {tr(STATUS_FA.get(o['status'], o['status']))}" for o in rows]
    await cb.message.answer(tr("🧾 آخرین سفارش‌های شما:") + "\n\n" + "\n".join(lines),
                            reply_markup=ikb([[btn(tr("🔙 منوی اصلی"), "menu", role="back")]]))
    await cb.answer()
