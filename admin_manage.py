"""پنل مدیریت داخل ربات (بخش مدیریتی): کدهای تخفیف پیشرفته، زبان‌ها و ترجمه‌ها، ادمین‌ها با دسترسی، پیام همگانی."""
import io
import json
import re
import secrets
import time

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from admin_common import ans, TXT, Adm, back_row, make_router, num, short, view
from i18n import CATALOG, SRC_LANG, valid_lang_code
from service import PERM_PRESETS, PERMS, Shop, hash_pw
from ui import btn, esc, fmt, ikb

router = make_router()
RTL_CODES = {"ar", "fa", "he", "ur", "ps", "ku", "sd", "yi", "dv"}
KIND_NAMES = {"stars": "استارز", "premium": "پریمیوم", "gift": "گیفت", "vpn": "VPN"}


def human_left(ts) -> str:
    if not ts:
        return "بدون انقضا"
    d = int(ts - time.time())
    if d <= 0:
        return "⏹ منقضی شده"
    days, rem = divmod(d, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = ([f"{days} روز"] if days else []) + ([f"{hours} ساعت"] if hours else []) + ([f"{mins} دقیقه"] if mins and not days else [])
    return "⏳ " + " و ".join(parts or ["کمتر از ۱ دقیقه"]) + " مانده"


_DUR = re.compile(r"^\+?\s*(\d+)\s*(d|h|m|روز|ساعت|دقیقه)?$", re.I)


def parse_duration(text: str):
    """'7d' '12h' '90m' '3' (=روز) '0' → ثانیه؛ None = نامعتبر"""
    t = (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).strip().lower()
    m = _DUR.match(t)
    if not m:
        return None
    n, unit = int(m.group(1)), (m.group(2) or "d")
    mult = {"d": 86400, "روز": 86400, "h": 3600, "ساعت": 3600, "m": 60, "دقیقه": 60}[unit]
    return n * mult


# ============================================================ کدهای تخفیف
DPAGE = 8


def disc_line(d) -> str:
    val = f"{d['percent']}٪" if d["percent"] else f"{fmt(d['fixed'])}ت"
    st = "✅" if d["active"] and not (d["expires_at"] and d["expires_at"] < time.time()) else "⏹"
    uses = f"{d['used']}/{d['max_uses']}" if d["max_uses"] else f"{d['used']}/∞"
    return f"{st} {d['code']} · {val} · {uses}"


@router.callback_query(F.data.startswith("adm:disc"))
async def cb_disc(cb: CallbackQuery, shop: Shop, state: FSMContext | None = None):
    if state:
        await state.clear()
    parts = cb.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    rows = await shop.db.list_discounts()
    kb = [[btn(disc_line(d), f"adm:dc:{d['code']}", role="admin")] for d in rows[page * DPAGE:(page + 1) * DPAGE]]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:disc:{page - 1}", role="nav"))
    if (page + 1) * DPAGE < len(rows):
        nav.append(btn("▶️", f"adm:disc:{page + 1}", role="nav"))
    if nav:
        kb.append(nav)
    kb.append([btn("➕ ساخت کد تخفیف", "adm:dnew", role="confirm")])
    kb.append([btn("⚙️ ساخت پیشرفته (همه گزینه‌ها)", "adm:dnewadv", role="action")])
    kb.append(back_row())
    await view(cb, f"🏷 <b>کدهای تخفیف</b> ({len(rows)} کد)\n✅ فعال | ⏹ غیرفعال/منقضی — درصد/مبلغ · استفاده‌شده/ظرفیت", ikb(kb))
    await cb.answer()


async def discount_card(shop: Shop, code):
    d = await shop.db.get_discount(code)
    if not d:
        return None
    users = await shop.db.discount_users(code)
    saved = await shop.db.discount_savings(code)
    applies = "همه" if d["applies_to"] in ("", "all") else "، ".join(KIND_NAMES.get(k, k) for k in d["applies_to"].split(","))
    val = f"{d['percent']}٪" + (f" (سقف {fmt(d['max_discount'])} ت)" if d["max_discount"] else "") if d["percent"] else f"{fmt(d['fixed'])} تومان"
    lines = [f"🏷 <b><code>{d['code']}</code></b> — {'✅ فعال' if d['active'] else '⏹ غیرفعال'}",
             f"مقدار: {val}",
             f"تخفیف روی: {BASE_NAMES.get(d.get('base') or 'price')}",
             f"استفاده: {d['used']} از {d['max_uses'] or '∞'} | هر کاربر: {d['per_user'] or '∞'} بار",
             f"اعتبار: {human_left(d['expires_at'])}",
             f"شروع: {'بلافاصله' if not d['starts_at'] or d['starts_at'] <= time.time() else '⏳ ' + human_left(d['starts_at']).replace(' مانده', ' دیگر')}",
             f"حداقل سفارش: {fmt(d['min_amount']) if d['min_amount'] else 'ندارد'} | محصولات: {applies}",
             f"فقط اولین خرید: {'بله' if d['first_only'] else 'خیر'} | محدود به کاربران خاص: {len(users) if users else 'خیر'}",
             f"💸 مجموع تخفیف داده‌شده: {fmt(saved)} تومان"]
    if d["note"]:
        lines.append(f"📝 {esc(d['note'])}")
    kb = ikb([
        [btn("⏹ غیرفعال کردن" if d["active"] else "✅ فعال کردن", f"adm:dt:{d['code']}", role="reject" if d["active"] else "confirm"),
         btn("⏱ تمدید/تغییر مدت", f"adm:dext:{d['code']}", role="action")],
        [btn("🎯 تغییر مبنا: " + BASE_NAMES["profit" if (d.get("base") or "price") == "price" else "price"], f"adm:ds:cb:{d['code']}", role="action")],
        [btn("📜 لاگ استفاده", f"adm:dlog:{d['code']}", role="admin"), btn("🗑 حذف", f"adm:dx:{d['code']}", role="reject")],
        back_row("adm:disc"),
    ])
    return "\n".join(lines), kb


@router.callback_query(F.data.startswith("adm:dc:"))
async def cb_discount(cb: CallbackQuery, shop: Shop):
    card = await discount_card(shop, cb.data.split(":")[2])
    if not card:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, *card)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:dt:"))
async def cb_disc_toggle(cb: CallbackQuery, shop: Shop):
    code = cb.data.split(":")[2]
    await shop.db.toggle_discount(code)
    await shop.db.log(f"tg:{cb.from_user.id}", "discount_toggle", code)
    card = await discount_card(shop, code)
    if card:
        await view(cb, *card)
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:dx:"))
async def cb_disc_delete(cb: CallbackQuery, shop: Shop):
    parts = cb.data.split(":")
    code = parts[2]
    if len(parts) > 3 and parts[3] == "yes":
        await shop.db.delete_discount(code)
        await shop.db.log(f"tg:{cb.from_user.id}", "discount_delete", code)
        await cb.answer("حذف شد")
        cb2 = cb.model_copy(update={"data": "adm:disc"})
        await cb_disc(cb2, shop, None)
        return
    await view(cb, f"⚠️ کد <code>{esc(code)}</code> حذف شود؟ (لاگ استفاده‌ها پاک نمی‌شود)",
               ikb([[btn("✅ بله، حذف", f"adm:dx:{code}:yes", role="reject"), btn("❌ خیر", f"adm:dc:{code}", role="back")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:dlog:"))
async def cb_disc_log(cb: CallbackQuery, shop: Shop):
    code = cb.data.split(":")[2]
    rows = await shop.db.discount_usage(code, 25)
    lines = [f"{time.strftime('%m-%d %H:%M', time.gmtime(r['ts'] + 12600))} · کاربر {r['user_id']} · سفارش #{r['order_id']} · {fmt(r['amount'])} ت" for r in rows]
    await cb.message.answer(f"📜 استفاده‌های <code>{esc(code)}</code> (۲۵ مورد آخر):\n\n" + ("\n".join(lines) or "هنوز استفاده نشده."),
                            reply_markup=ikb([back_row(f"adm:dc:{code}")]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:dext:"))
async def cb_disc_extend(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.disc_extend)
    await state.update_data(code=cb.data.split(":")[2])
    await cb.message.answer("مدت را بفرستید:\n• <code>+3d</code> یا <code>3d</code> = ۳ روز دیگر به انقضا اضافه شود\n• <code>12h</code> = ۱۲ ساعت • <code>90m</code> = ۹۰ دقیقه\n"
                            "• <code>0</code> = بدون انقضا\n(برای لغو /admin)")
    await cb.answer()


@router.message(Adm.disc_extend, TXT)
async def got_disc_extend(m: Message, shop: Shop, state: FSMContext):
    sec = parse_duration(m.text)
    if sec is None:
        await m.answer("فرمت نامعتبر است. مثال: 3d یا 12h یا 90m یا 0")
        return
    d = await state.get_data()
    await state.clear()
    disc = await shop.db.get_discount(d["code"])
    if not disc:
        await m.answer("کد پیدا نشد.")
        return
    exp = 0 if sec == 0 else int(max(time.time(), disc["expires_at"] or 0) + sec)
    await shop.db.update_discount(d["code"], expires_at=exp)
    await shop.db.log(f"tg:{m.from_user.id}", "discount_extend", f"{d['code']} {m.text}")
    card = await discount_card(shop, d["code"])
    await m.answer("✅ انجام شد.")
    if card:
        await ans(m, card)


# ---------- ویزارد ساخت کد
def _p_code(t):
    t = t.strip()
    if t == "-":
        return True, "SS" + secrets.token_hex(3).upper()
    t = t.upper()
    return (True, t) if re.fullmatch(r"[A-Z0-9_-]{3,20}", t) else (False, "فقط حروف انگلیسی/عدد (۳ تا ۲۰ نویسه) یا «-» برای خودکار.")


def _p_value(t):
    t = t.strip().lower().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).replace(",", "")
    if t.startswith("f"):
        v = num(t[1:])
        return (True, ("fixed", v)) if v and v >= 1000 else (False, "مبلغ ثابت حداقل ۱٬۰۰۰ تومان؛ مثال f50000")
    v = num(t.rstrip("%"))
    return (True, ("percent", v)) if v and 1 <= v <= 100 else (False, "درصد بین ۱ تا ۱۰۰ بفرستید؛ مثال 20 (یا f50000 برای مبلغ ثابت).")


def _p_dur(t):
    v = parse_duration(t)
    return (True, v) if v is not None else (False, "فرمت نامعتبر. مثال: 7d ، 12h ، 90m ، 0")


def _p_int(t):
    v = num(t)
    return (True, v) if v is not None else (False, "فقط عدد بفرستید.")


def _p_applies(t):
    t = t.strip().lower().replace(" ", "")
    if t in ("all", "همه", ""):
        return True, "all"
    parts = [p for p in t.split(",") if p]
    return (True, ",".join(parts)) if parts and all(p in KIND_NAMES for p in parts) else (False, "مقدار all یا ترکیبی از stars,premium,gift,vpn")


def _p_yes(t):
    t = t.strip().lower()
    if t in ("yes", "y", "1", "بله", "آره"):
        return True, 1
    if t in ("no", "n", "0", "خیر", "نه"):
        return True, 0
    return False, "yes یا no"


def _p_note(t):
    return True, "" if t.strip() == "-" else t.strip()[:200]


WIZ = [
    ("code", "۱/۱۰ — نام کد را بفرستید (حروف انگلیسی/عدد) یا «-» برای ساخت خودکار", _p_code, "-", False),
    ("value", "۲/۱۰ — مقدار تخفیف:\n• عدد = درصد (مثلاً <code>20</code>)\n• <code>f50000</code> = مبلغ ثابت به تومان", _p_value, None, True),
    ("duration", "۳/۱۰ — مدت اعتبار (از لحظه‌ی شروع): <code>7d</code> روز، <code>12h</code> ساعت، <code>90m</code> دقیقه؛ <code>0</code> = بدون انقضا", _p_dur, 0, False),
    ("start", "۴/۱۰ — زمان شروع: <code>0</code> = همین الان، یا بعد از مدتی مثل <code>2h</code> / <code>1d</code>", _p_dur, 0, False),
    ("max_uses", "۵/۱۰ — حداکثر تعداد کل استفاده (<code>0</code> = نامحدود)", _p_int, 0, False),
    ("per_user", "۶/۱۰ — هر کاربر چندبار می‌تواند استفاده کند؟ (<code>0</code> = نامحدود)", _p_int, 1, False),
    ("min_amount", "۷/۱۰ — حداقل مبلغ سفارش به تومان (<code>0</code> = بدون حداقل)", _p_int, 0, False),
    ("cap", "۸/۱۰ — سقف مبلغ تخفیف به تومان برای تخفیف درصدی (<code>0</code> = بدون سقف)", _p_int, 0, False),
    ("applies", "۹/۱۰ — برای کدام محصولات؟ <code>all</code> یا ترکیبی از <code>stars,premium,gift,vpn</code>", _p_applies, "all", False),
    ("first_only", "۱۰/۱۰ — فقط برای اولین خرید کاربر؟ <code>yes</code> یا <code>no</code>", _p_yes, 0, False),
    ("note", "یادداشت داخلی (اختیاری؛ «-» = بدون یادداشت)", _p_note, "", False),
]


def wiz_kb(required):
    row = [] if required else [btn("⏭ پیش‌فرض", "adm:dw:skip", role="action")]
    row.append(btn("❌ لغو", "adm:dw:cancel", role="reject"))
    return ikb([row])


async def wiz_ask(target: Message, state: FSMContext, idx: int):
    d = await state.get_data()
    vals = d.get("vals", {})
    # مرحله‌ی سقف فقط برای تخفیف درصدی
    while idx < len(WIZ) and WIZ[idx][0] == "cap" and vals.get("value", ("", 0))[0] != "percent":
        vals["cap"] = 0
        idx += 1
    if idx >= len(WIZ):
        await wiz_summary(target, state, vals)
        return
    await state.update_data(step=idx, vals=vals)
    key, q, _p, default, req = WIZ[idx]
    await target.answer(q + ("" if req else f"\n(پیش‌فرض: {default if default != '' else '—'})"), reply_markup=wiz_kb(req))


BASE_NAMES = {"price": "کل قیمت محصول", "profit": "فقط سود فروشگاه"}


def summary_kb(v, mode="a"):
    rows = [[btn("✅ ساخت کد", "adm:dw:ok", role="confirm"), btn("❌ لغو", "adm:dw:cancel", role="reject")],
            [btn("🎯 تخفیف روی: " + BASE_NAMES[v.get("base") or "price"] + " (تغییر)", "adm:ds:base", role="action")]]
    if mode == "s":
        rows.append([btn("✏️ تغییر نام کد", "adm:ds:name", role="action"), btn("⚙️ گزینه‌های بیشتر", "adm:ds:more", role="action")])
    return ikb(rows)


def wiz_text(v) -> str:
    kind, val = v["value"]
    now = time.time()
    start = int(now + v["start"]) if v["start"] else 0
    exp = int((start or now) + v["duration"]) if v["duration"] else 0
    applies = "همه" if v["applies"] == "all" else "، ".join(KIND_NAMES[k] for k in v["applies"].split(","))
    if kind == "percent":
        value = f"{val}٪" + (f" (سقف {fmt(v['cap'])} ت)" if v["cap"] else "")
    else:
        value = f"{fmt(val)} تومان"
    lines = [f"🏷 <b>تأیید ساخت کد</b>", f"کد: <code>{v['code']}</code>", f"مقدار: {value}",
             f"تخفیف روی: {BASE_NAMES[v.get('base') or 'price']}",
             f"شروع: {'بلافاصله' if not start else human_left(start).replace(' مانده', ' دیگر')} | "
             f"مدت: {human_left(exp).replace(' مانده', '') if exp else 'بدون انقضا'}",
             f"ظرفیت کل: {v['max_uses'] or '∞'} | هر کاربر: {v['per_user'] or '∞'}",
             f"حداقل سفارش: {fmt(v['min_amount']) if v['min_amount'] else 'ندارد'} | محصولات: {applies}",
             f"فقط اولین خرید: {'بله' if v['first_only'] else 'خیر'}"]
    if v["note"]:
        lines.append(f"📝 {esc(v['note'])}")
    return "\n".join(lines)


async def wiz_summary(target: Message, state: FSMContext, vals):
    await state.update_data(vals=vals, step=len(WIZ))
    await target.answer(wiz_text(vals), reply_markup=summary_kb(vals, "a"))


# ---------- ساخت ساده‌ی کد تخفیف (۴ مرحله): مقدار، مدت (روز)، ظرفیت کل، سقف هر کاربر
SIMPLE_STEPS = ("value", "days", "max_uses", "per_user")
SIMPLE_Q = {
    "value": ("۱/۴ — چقدر تخفیف می‌دهید؟\nفقط یک عدد بفرستید؛ مثلاً <code>20</code> یعنی ۲۰٪ تخفیف.\n"
              "اگر مبلغ ثابت (به تومان) می‌خواهید دکمه «💰 مبلغ ثابت» را بزنید.", []),
    "days": ("۲/۴ — کد چند روز معتبر باشد؟\nعدد بفرستید یا یکی از دکمه‌ها را بزنید. <code>0</code> = بدون انقضا.",
             [("۱ روز", "1"), ("۷ روز", "7"), ("۳۰ روز", "30"), ("♾ بدون انقضا", "0")]),
    "max_uses": ("۳/۴ — در مجموع چند نفر می‌توانند از این کد استفاده کنند؟\n<code>0</code> = نامحدود.",
                 [("۱۰ نفر", "10"), ("۵۰ نفر", "50"), ("۱۰۰ نفر", "100"), ("♾ نامحدود", "0")]),
    "per_user": ("۴/۴ — هر کاربر چند بار می‌تواند استفاده کند؟\n<code>0</code> = نامحدود.",
                 [("۱ بار", "1"), ("۲ بار", "2"), ("۵ بار", "5"), ("♾ نامحدود", "0")]),
}
SIMPLE_DEFAULTS = {"start": 0, "min_amount": 0, "cap": 0, "applies": "all", "first_only": 0, "note": "", "base": "price"}


def simple_kb(step: str, kind: str):
    rows = []
    if step == "value":
        rows.append([btn("💰 مبلغ ثابت (تومان)" if kind == "percent" else "٪ درصدی", "adm:ds:kind", role="action")])
    else:
        quick = SIMPLE_Q[step][1]
        rows.append([btn(t, f"adm:ds:q:{v}", role="action") for t, v in quick[:2]])
        rows.append([btn(t, f"adm:ds:q:{v}", role="action") for t, v in quick[2:]])
    rows.append([btn("❌ لغو", "adm:dw:cancel", role="reject")])
    return ikb(rows)


def simple_parse(step: str, kind: str, text: str):
    n = num(text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).rstrip("%٪"))
    if n is None:
        return False, "فقط یک عدد بفرستید."
    if step == "value":
        if kind == "percent":
            return (True, n) if 1 <= n <= 100 else (False, "درصد باید بین ۱ تا ۱۰۰ باشد. برای مبلغ ثابت، دکمه «مبلغ ثابت» را بزنید.")
        return (True, n) if n >= 1000 else (False, "مبلغ ثابت حداقل ۱٬۰۰۰ تومان است.")
    if step == "days":
        return (True, n) if n <= 3650 else (False, "حداکثر ۳۶۵۰ روز.")
    return True, n


async def simple_ask(target: Message, state: FSMContext, i: int):
    d = await state.get_data()
    vals = d.get("vals", {})
    if i >= len(SIMPLE_STEPS):
        await simple_summary(target, state, vals)
        return
    step = SIMPLE_STEPS[i]
    await state.update_data(step=i, vals=vals)
    q = SIMPLE_Q[step][0]
    if step == "value" and vals.get("kind") == "fixed":
        q = "۱/۴ — چند تومان تخفیف می‌دهید؟\nفقط یک عدد بفرستید؛ مثلاً <code>50000</code>. (دکمه «درصدی» برای برگشت به درصد)"
    await target.answer(q, reply_markup=simple_kb(step, vals.get("kind", "percent")))


async def simple_summary(target: Message, state: FSMContext, vals):
    full = {**SIMPLE_DEFAULTS, **vals}
    full["value"] = (vals.get("kind", "percent"), vals["val"])
    full["duration"] = vals["days"] * 86400
    await state.update_data(vals=full, step=len(WIZ), mode="s")
    await target.answer(wiz_text(full), reply_markup=summary_kb(full, "s"))


@router.callback_query(F.data == "adm:dnew")
async def cb_dnew(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.disc_wiz)
    await state.update_data(mode="s", step=0, vals={"kind": "percent", "code": "SS" + secrets.token_hex(3).upper()})
    await simple_ask(cb.message, state, 0)
    await cb.answer()


@router.callback_query(F.data == "adm:dnewadv")
async def cb_dnew_adv(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.disc_wiz)
    await state.update_data(mode="a", step=0, vals={})
    await wiz_ask(cb.message, state, 0)
    await cb.answer()


@router.callback_query(F.data == "adm:ds:kind")
async def cb_ds_kind(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    vals = d.get("vals") or {}
    vals["kind"] = "fixed" if vals.get("kind", "percent") == "percent" else "percent"
    await state.update_data(vals=vals)
    await cb.answer()
    await simple_ask(cb.message, state, 0)


async def simple_take(target: Message, state: FSMContext, text: str):
    d = await state.get_data()
    vals = d.get("vals") or {}
    i = d.get("step", 0)
    if d.get("await") == "name":
        ok, res = _p_code(text)
        if not ok:
            await target.answer(f"❌ {res}")
            return
        vals["code"] = res
        await state.update_data(vals=vals, **{"await": None})
        await simple_summary(target, state, {**vals, "val": vals["value"][1], "days": vals["duration"] // 86400, "kind": vals["value"][0]})
        return
    if i >= len(SIMPLE_STEPS):
        await target.answer("از دکمه‌های بالا برای تأیید یا لغو استفاده کنید.")
        return
    step = SIMPLE_STEPS[i]
    ok, res = simple_parse(step, vals.get("kind", "percent"), text)
    if not ok:
        await target.answer(f"❌ {res}")
        return
    vals["val" if step == "value" else step] = res
    await state.update_data(vals=vals)
    await simple_ask(target, state, i + 1)


@router.callback_query(F.data.startswith("adm:ds:q:"))
async def cb_ds_quick(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await simple_take(cb.message, state, cb.data.split(":")[3])


@router.callback_query(F.data == "adm:ds:base")
async def cb_ds_base(cb: CallbackQuery, state: FSMContext):
    """تغییر مبنای تخفیف (کل قیمت ↔ فقط سود) روی صفحه‌ی تأیید ساخت کد."""
    d = await state.get_data()
    v = d.get("vals") or {}
    if "value" not in v:
        await cb.answer("اول مراحل را کامل کنید.", show_alert=True)
        return
    v["base"] = "profit" if (v.get("base") or "price") == "price" else "price"
    await state.update_data(vals=v)
    try:
        await cb.message.edit_text(wiz_text(v), reply_markup=summary_kb(v, d.get("mode", "a")))
    except Exception:  # noqa: BLE001
        pass
    await cb.answer("تخفیف روی " + BASE_NAMES[v["base"]])


@router.callback_query(F.data.startswith("adm:ds:cb:"))
async def cb_ds_card_base(cb: CallbackQuery, shop: Shop):
    """تغییر مبنای یک کد موجود."""
    code = cb.data.split(":")[3]
    d = await shop.db.get_discount(code)
    if not d:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await shop.db.update_discount(code, base="profit" if (d.get("base") or "price") == "price" else "price")
    await shop.db.log(f"tg:{cb.from_user.id}", "discount_base", code)
    card = await discount_card(shop, code)
    if card:
        await view(cb, *card)
    await cb.answer("انجام شد")


@router.callback_query(F.data == "adm:ds:name")
async def cb_ds_name(cb: CallbackQuery, state: FSMContext):
    await state.update_data(**{"await": "name"})
    await cb.message.answer("نام دلخواه کد را بفرستید (حروف انگلیسی/عدد، ۳ تا ۲۰ نویسه):", reply_markup=ikb([[btn("❌ لغو", "adm:dw:cancel", role="reject")]]))
    await cb.answer()


@router.callback_query(F.data == "adm:ds:more")
async def cb_ds_more(cb: CallbackQuery, state: FSMContext):
    """ادامه‌ی ویزارد کامل از مرحله‌ی «حداقل مبلغ سفارش» با مقادیر همین کد."""
    d = await state.get_data()
    await state.update_data(mode="a")
    await cb.answer()
    await wiz_ask(cb.message, state, next(i for i, w in enumerate(WIZ) if w[0] == "min_amount"))


@router.callback_query(F.data == "adm:dw:cancel")
async def cb_dw_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("لغو شد.", reply_markup=ikb([back_row("adm:disc")]))
    await cb.answer()


@router.callback_query(F.data == "adm:dw:skip")
async def cb_dw_skip(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    idx = d.get("step", 0)
    if idx >= len(WIZ) or WIZ[idx][4]:
        await cb.answer("این مرحله پیش‌فرض ندارد.", show_alert=True)
        return
    vals = d["vals"]
    key, _q, parser, default, _r = WIZ[idx]
    vals[key] = parser(str(default))[1] if key == "code" else default
    await state.update_data(vals=vals)
    await cb.answer()
    await wiz_ask(cb.message, state, idx + 1)


@router.message(Adm.disc_wiz, TXT)
async def got_wiz(m: Message, state: FSMContext):
    d = await state.get_data()
    if d.get("mode") == "s":
        await simple_take(m, state, m.text)
        return
    idx = d.get("step", 0)
    if idx >= len(WIZ):
        await m.answer("از دکمه‌های بالا برای تأیید یا لغو استفاده کنید.")
        return
    key, _q, parser, _default, _r = WIZ[idx]
    ok, res = parser(m.text)
    if not ok:
        await m.answer(f"❌ {res}")
        return
    vals = d["vals"]
    vals[key] = res
    await state.update_data(vals=vals)
    await wiz_ask(m, state, idx + 1)


@router.callback_query(F.data == "adm:dw:ok")
async def cb_dw_ok(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = await state.get_data()
    v = d.get("vals") or {}
    if d.get("step") != len(WIZ) or "value" not in v:
        await cb.answer("اطلاعات ناقص است؛ دوباره شروع کنید.", show_alert=True)
        return
    await state.clear()
    kind, val = v["value"]
    now = int(time.time())
    start = now + v["start"] if v["start"] else 0
    exp = (start or now) + v["duration"] if v["duration"] else 0
    ok = await shop.db.create_discount(v["code"], val if kind == "percent" else 0, val if kind == "fixed" else 0, v["max_uses"], exp,
                                       starts_at=start, per_user=v["per_user"], min_amount=v["min_amount"], max_discount=v["cap"],
                                       applies_to=v["applies"], first_only=v["first_only"], note=v["note"], base=v.get("base") or "price")
    if not ok:
        await cb.message.answer("❌ کدی با این نام از قبل وجود دارد.", reply_markup=ikb([back_row("adm:disc")]))
        await cb.answer()
        return
    await shop.db.log(f"tg:{cb.from_user.id}", "discount_create", v["code"])
    await cb.message.answer(f"✅ کد <code>{v['code']}</code> ساخته شد.", reply_markup=ikb([[btn("🏷 مشاهده", f"adm:dc:{v['code']}", role="admin")], back_row("adm:disc")]))
    await cb.answer()


# ============================================================ زبان‌ها
def cov_text(shop: Shop, code: str) -> str:
    b = CATALOG.coverage("bot", code)
    w = CATALOG.coverage("web", code)
    pct = lambda t: f"{t[0] * 100 // t[1]}٪" if t[1] else "—"  # noqa: E731
    return f"ربات {pct(b)} ({b[0]}/{b[1]}) · پنل وب {pct(w)} ({w[0]}/{w[1]})"


@router.callback_query(F.data == "adm:langs")
async def cb_langs(cb: CallbackQuery, shop: Shop, state: FSMContext | None = None):
    if state:
        await state.clear()
    langs = await shop.db.list_languages()
    dflt, wdflt = shop.default_lang(), shop.s.get("web_default_lang") or SRC_LANG
    kb = [[btn(f"{l['flag']} {l['name']} ({l['code']}){' 🤖' if l['bot_enabled'] else ''}{' 🖥' if l['web_enabled'] else ''}"
               f"{' ⭐' if l['code'] == dflt else ''}", f"adm:lg:{l['code']}", role="admin")] for l in langs]
    kb.append([btn("➕ افزودن زبان جدید", "adm:lnew", role="confirm")])
    kb.append(back_row())
    await view(cb, f"🌐 <b>زبان‌ها</b>\n🤖 = فعال در ربات | 🖥 = فعال در پنل وب | ⭐ = پیش‌فرض ربات\nپیش‌فرض پنل وب: {esc(wdflt)}\n\n"
                   "هر زبان جدیدی را می‌توانید اضافه و همه‌ی متن‌های آن را ترجمه کنید.", ikb(kb))
    await cb.answer()


async def lang_card(shop: Shop, code):
    l = await shop.db.get_language(code)
    if not l:
        return None
    dflt, wdflt = shop.default_lang(), shop.s.get("web_default_lang") or SRC_LANG
    text = (f"{l['flag']} <b>{esc(l['name'])}</b> (<code>{l['code']}</code>)\n"
            f"ربات: {'✅' if l['bot_enabled'] else '⬜'} | پنل وب: {'✅' if l['web_enabled'] else '⬜'} | راست‌به‌چپ: {'بله' if l['rtl'] else 'خیر'}\n"
            f"پیش‌فرض ربات: {'⭐ بله' if code == dflt else 'خیر'} | پیش‌فرض وب: {'⭐ بله' if code == wdflt else 'خیر'}\n"
            f"پیشرفت ترجمه: {cov_text(shop, code)}")
    kb = [[btn(("✅" if l["bot_enabled"] else "⬜") + " ربات", f"adm:lg:{code}:bot", role="admin"),
           btn(("✅" if l["web_enabled"] else "⬜") + " پنل وب", f"adm:lg:{code}:web", role="admin"),
           btn(("✅" if l["rtl"] else "⬜") + " RTL", f"adm:lg:{code}:rtl", role="admin")],
          [btn("⭐ پیش‌فرض ربات", f"adm:lg:{code}:defbot", role="action"), btn("⭐ پیش‌فرض وب", f"adm:lg:{code}:defweb", role="action")]]
    if code != SRC_LANG:
        kb.append([btn("📝 ترجمه‌ی ربات", f"adm:lt:{code}:bot:0", role="action"), btn("📝 ترجمه‌ی وب", f"adm:lt:{code}:web:0", role="action")])
        kb.append([btn("📤 خروجی JSON ربات", f"adm:lt:{code}:exp:bot", role="admin"), btn("📤 خروجی JSON وب", f"adm:lt:{code}:exp:web", role="admin")])
        kb.append([btn("📥 ورود ترجمه (JSON)", f"adm:lt:{code}:imp", role="admin")])
    if not l["builtin"]:
        kb.append([btn("🗑 حذف زبان", f"adm:lg:{code}:del", role="reject")])
    kb.append(back_row("adm:langs"))
    return text, ikb(kb)


@router.callback_query(F.data.startswith("adm:lg:"))
async def cb_lang(cb: CallbackQuery, shop: Shop):
    parts = cb.data.split(":")
    code = parts[2]
    l = await shop.db.get_language(code)
    if not l:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    act = parts[3] if len(parts) > 3 else ""
    actor = f"tg:{cb.from_user.id}"
    if act in ("bot", "web"):
        if act == "bot" and l["bot_enabled"] and code == shop.default_lang():
            await cb.answer("زبان پیش‌فرض را نمی‌توان غیرفعال کرد.", show_alert=True)
            return
        await shop.db.upsert_language(code, l["name"], l["flag"], l["rtl"], int(not l["bot_enabled"]) if act == "bot" else l["bot_enabled"],
                                      int(not l["web_enabled"]) if act == "web" else l["web_enabled"])
    elif act == "rtl":
        await shop.db.upsert_language(code, l["name"], l["flag"], int(not l["rtl"]), l["bot_enabled"], l["web_enabled"])
    elif act in ("defbot", "defweb"):
        if (act == "defbot" and not l["bot_enabled"]) or (act == "defweb" and not l["web_enabled"]):
            await cb.answer("ابتدا این زبان را برای همان بخش فعال کنید.", show_alert=True)
            return
        await shop.set_setting("default_lang" if act == "defbot" else "web_default_lang", code, actor)
    elif act == "del":
        if len(parts) > 4 and parts[4] == "yes":
            ok = await shop.db.delete_language(code)
            await shop.reload_i18n()
            await cb.answer("حذف شد" if ok else "زبان‌های داخلی حذف نمی‌شوند.", show_alert=not ok)
            await cb_langs(cb.model_copy(update={"data": "adm:langs"}), shop, None)
            return
        await view(cb, f"⚠️ زبان <b>{esc(l['name'])}</b> و همه‌ی ترجمه‌های ثبت‌شده‌اش حذف شود؟ (کاربران آن به زبان پیش‌فرض برمی‌گردند)",
                   ikb([[btn("✅ بله", f"adm:lg:{code}:del:yes", role="reject"), btn("❌ خیر", f"adm:lg:{code}", role="back")]]))
        await cb.answer()
        return
    if act:
        await shop.db.log(actor, "language", f"{code} {act}")
        await shop.reload_i18n()
        shop._lang_cache.clear()
    card = await lang_card(shop, code)
    await view(cb, *card)
    await cb.answer("انجام شد" if act else None)


@router.callback_query(F.data == "adm:lnew")
async def cb_lnew(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.lang_new)
    await cb.message.answer("زبان جدید را به این شکل بفرستید:\n<code>کد نام پرچم</code>\nمثال: <code>pt Português 🇧🇷</code> یا <code>pl Polski 🇵🇱</code>\n"
                            "کد = کد دو حرفی زبان (ISO). (برای لغو /admin)")
    await cb.answer()


@router.message(Adm.lang_new, TXT)
async def got_lang_new(m: Message, shop: Shop, state: FSMContext):
    parts = m.text.strip().split()
    if len(parts) < 2 or not valid_lang_code(parts[0]):
        await m.answer("فرمت نادرست. مثال: pt Português 🇧🇷")
        return
    code = parts[0].lower()
    flag = parts[-1] if len(parts) >= 3 and not parts[-1].isalnum() else ""
    name = " ".join(parts[1:-1] if flag else parts[1:])[:30]
    if await shop.db.get_language(code):
        await m.answer("این کد زبان از قبل وجود دارد.")
        return
    await state.clear()
    await shop.db.upsert_language(code, name, flag, 1 if code.split("-")[0] in RTL_CODES else 0, 0, 0)
    await shop.db.log(f"tg:{m.from_user.id}", "language_add", code)
    await shop.reload_i18n()
    await m.answer("✅ زبان اضافه شد. حالا ترجمه‌ها را وارد کنید و بعد آن را برای ربات/وب فعال کنید.")
    await ans(m, (await lang_card(shop, code)))


# ---------- ترجمه‌ها
TPAGE = 8


def src_list(scope):
    CATALOG.load()
    return CATALOG.sources.get(scope, [])


@router.callback_query(F.data.regexp(r"^adm:lt:[^:]+:(bot|web):\d+$"))
async def cb_trans_list(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    _, _, code, scope, page = cb.data.split(":")
    page = int(page)
    srcs = src_list(scope)
    if not srcs:
        await cb.answer("فهرست متن‌ها هنوز ساخته نشده است (tools/extract_strings.py).", show_alert=True)
        return
    # فقط ترجمه‌نشده‌ها
    todo = [(i, s) for i, s in enumerate(srcs) if not CATALOG.lookup(scope, code, s)]
    pages = max(1, (len(todo) + TPAGE - 1) // TPAGE)
    page = max(0, min(page, pages - 1))
    kb = [[btn(short(s, 44), f"adm:lt:{code}:{scope}:e:{i}", role="admin")] for i, s in todo[page * TPAGE:(page + 1) * TPAGE]]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:lt:{code}:{scope}:{page - 1}", role="nav"))
    if page < pages - 1:
        nav.append(btn("▶️", f"adm:lt:{code}:{scope}:{page + 1}", role="nav"))
    if nav:
        kb.append(nav)
    kb.append(back_row(f"adm:lg:{code}"))
    await view(cb, f"📝 ترجمه‌ی {'ربات' if scope == 'bot' else 'پنل وب'} — {code}\nمتن‌های ترجمه‌نشده: <b>{len(todo)}</b> از {len(srcs)}\n"
                   "روی یک مورد بزنید و ترجمه‌اش را بفرستید. (برای ترجمه‌ی گروهی از «خروجی/ورود JSON» استفاده کنید.)", ikb(kb))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:lt:[^:]+:(bot|web):e:\d+$"))
async def cb_trans_edit(cb: CallbackQuery, state: FSMContext):
    _, _, code, scope, _e, idx = cb.data.split(":")
    srcs = src_list(scope)
    if int(idx) >= len(srcs):
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await state.set_state(Adm.lang_text)
    await state.update_data(mode="one", code=code, scope=scope, idx=int(idx))
    await cb.message.answer(f"متن اصلی:\n<code>{esc(srcs[int(idx)])}</code>\n\nترجمه به <b>{code}</b> را بفرستید.\n"
                            "⚠️ جاهای {نام} و تگ‌های HTML مثل &lt;b&gt; را دست‌نخورده نگه دارید. («-» = حذف ترجمه؛ لغو: /admin)")
    await cb.answer()


@router.message(Adm.lang_text, TXT)
async def got_trans(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    if d.get("mode") != "one":
        await m.answer("لطفاً فایل JSON را بفرستید یا /admin.")
        return
    srcs = src_list(d["scope"])
    src = srcs[d["idx"]]
    text = "" if m.text.strip() == "-" else m.html_text.strip()
    miss = set(re.findall(r"\{\w+\}", src)) - set(re.findall(r"\{\w+\}", text))
    if text and miss:
        await m.answer("❌ جاهای خالی زیر در ترجمه نیست: " + " ".join(sorted(miss)) + "\nدوباره بفرستید.")
        return
    await shop.db.set_translations(d["scope"], d["code"], {src: text})
    await shop.reload_i18n()
    await state.clear()
    await m.answer("✅ ذخیره شد.", reply_markup=ikb([[btn("➡️ مورد بعدی", f"adm:lt:{d['code']}:{d['scope']}:0", role="action")], back_row(f"adm:lg:{d['code']}")]))


@router.callback_query(F.data.regexp(r"^adm:lt:[^:]+:exp:(bot|web)$"))
async def cb_trans_export(cb: CallbackQuery, shop: Shop):
    _, _, code, _e, scope = cb.data.split(":")
    srcs = src_list(scope)
    if not srcs:
        await cb.answer("فهرست متن‌ها هنوز ساخته نشده است.", show_alert=True)
        return
    data = {s: (CATALOG.lookup(scope, code, s) or "") for s in srcs}
    await cb.message.answer_document(BufferedInputFile(json.dumps(data, ensure_ascii=False, indent=1).encode(), filename=f"{code}.{scope}.json"),
                                     caption="📤 ترجمه‌ها را در مقدار هر کلید بنویسید (کلیدها را تغییر ندهید) و با «ورود ترجمه» برگردانید.")
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:lt:[^:]+:imp$"))
async def cb_trans_import(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":")[2]
    await state.set_state(Adm.lang_text)
    await state.update_data(mode="import", code=code)
    await cb.message.answer("فایل JSON ترجمه را بفرستید. نام فایل باید <code>%s.bot.json</code> یا <code>%s.web.json</code> باشد تا مشخص شود برای ربات است یا وب." % (code, code))
    await cb.answer()


@router.message(Adm.lang_text, F.document)
async def got_trans_file(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    if d.get("mode") != "import":
        await m.answer("لطفاً متن ترجمه را بفرستید یا /admin.")
        return
    name = (m.document.file_name or "").lower()
    scope = "web" if ".web." in name else "bot"
    if (m.document.file_size or 0) > 2 * 1024 * 1024:
        await m.answer("❌ حجم فایل بیش از ۲ مگابایت است.")
        return
    buf = io.BytesIO()
    await shop.bot.download(m.document, destination=buf)
    try:
        data = json.loads(buf.getvalue().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        await m.answer("❌ فایل JSON معتبر نیست (باید یک شیء {\"متن اصلی\": \"ترجمه\"} باشد).")
        return
    srcs = set(src_list(scope))
    items = {k: v for k, v in data.items() if isinstance(v, str) and (not srcs or k in srcs)}
    await shop.db.set_translations(scope, d["code"], items)
    await shop.reload_i18n()
    await state.clear()
    await shop.db.log(f"tg:{m.from_user.id}", "translations_import", f"{d['code']}.{scope} n={len(items)}")
    await m.answer(f"✅ {len(items)} ترجمه برای «{'ربات' if scope == 'bot' else 'وب'}» ذخیره شد.", reply_markup=ikb([back_row(f"adm:lg:{d['code']}")]))


# ============================================================ ادمین‌ها
def perm_text(shop: Shop, perms) -> str:
    if perms == "*":
        return "👑 دسترسی کامل"
    return "، ".join(PERMS[p] for p in PERMS if p in perms) or "بدون دسترسی"


@router.callback_query(F.data == "adm:admins")
async def cb_admins(cb: CallbackQuery, shop: Shop, state: FSMContext | None = None):
    if state:
        await state.clear()
    kb = []
    lines = []
    for uid in shop.cfg.admin_ids:
        lines.append(f"👑 <code>{uid}</code> — مالک (از فایل .env)")
    for a in await shop.db.list_admins():
        if a["user_id"] in shop.cfg.admin_ids:
            continue
        perms = shop.admin_perms.get(a["user_id"], set())
        nm = a["name"] or str(a["user_id"])
        kb.append([btn(f"{'👑' if perms == '*' else '👮'} {nm} — {len(PERMS) if perms == '*' else len(perms)} دسترسی{' 🖥' if a['web_user'] else ''}", f"adm:ad:{a['user_id']}", role="admin")])
    kb.append([btn("➕ افزودن ادمین", "adm:aadd", role="confirm")])
    kb.append(back_row())
    await view(cb, "👮 <b>ادمین‌ها</b>\n" + "\n".join(lines) + "\n\nبرای هر ادمین می‌توانید دسترسی‌ها را جداگانه (مثلاً فقط بررسی رسیدها) تعیین کنید. 🖥 = ورود به پنل وب دارد.", ikb(kb))
    await cb.answer()


@router.callback_query(F.data == "adm:aadd")
async def cb_aadd(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.admin_id)
    await cb.message.answer("آیدی عددی یا یوزرنیم کاربر را بفرستید (او باید قبلاً ربات را استارت کرده باشد). برای لغو /admin")
    await cb.answer()


@router.message(Adm.admin_id, TXT)
async def got_admin_id(m: Message, shop: Shop, state: FSMContext):
    t = m.text.strip()
    u = await shop.db.get_user(int(t)) if t.isdigit() else await shop.db.get_user_by_username(t)
    if not u:
        await m.answer("کاربری پیدا نشد؛ باید ربات را استارت کرده باشد.")
        return
    await state.clear()
    if u["id"] in shop.cfg.admin_ids:
        await m.answer("این کاربر مالک است و همیشه دسترسی کامل دارد.")
        return
    kb = [[btn(PERM_PRESETS[k][0], f"adm:apre:{u['id']}:{k}", role="admin")] for k in PERM_PRESETS if not (k == "full" and shop.admin_perms.get(m.from_user.id) != "*")]
    kb.append(back_row("adm:admins"))
    await m.answer(f"نقش «{esc(u['first_name'])}» را انتخاب کنید (بعداً دسترسی‌ها را یکی‌یکی می‌توانید تغییر دهید):", reply_markup=ikb(kb))


async def admin_card(shop: Shop, uid):
    a = await shop.db.get_admin(uid)
    if not a:
        return None
    perms = shop.admin_perms.get(uid, set())
    u = await shop.db.get_user(uid)
    text = (f"👮 <b>{esc(a['name'] or (u['first_name'] if u else uid))}</b> (<code>{uid}</code>) @{esc((u or {}).get('username') or '-')}\n"
            f"ورود به پنل وب: {('✅ ' + esc(a['web_user'])) if a['web_user'] else '⬜ ندارد'}\n\n<b>دسترسی‌ها:</b> (برای تغییر بزنید)")
    kb = []
    for k, label in PERMS.items():
        on = perms == "*" or k in perms
        kb.append([btn(f"{'✅' if on else '⬜'} {label}", f"adm:ap:{uid}:{k}", role="admin")])
    kb.append([btn(PERM_PRESETS[k][0], f"adm:apre:{uid}:{k}", role="action") for k in ("receipts", "support")])
    kb.append([btn(PERM_PRESETS[k][0], f"adm:apre:{uid}:{k}", role="action") for k in ("sales", "content", "finance")])
    kb.append([btn("👑 مدیر کامل", f"adm:apre:{uid}:full", role="action")])
    kb.append([btn("✏️ نام", f"adm:an:{uid}", role="action"), btn("🖥 ورود وب", f"adm:aw:{uid}", role="action"), btn("🗑 حذف", f"adm:arm:{uid}", role="reject")])
    kb.append(back_row("adm:admins"))
    return text, ikb(kb)


def is_full(shop: Shop, uid) -> bool:
    return shop.admin_perms.get(uid) == "*"


@router.callback_query(F.data.startswith("adm:ad:"))
async def cb_admin(cb: CallbackQuery, shop: Shop):
    card = await admin_card(shop, int(cb.data.split(":")[2]))
    if not card:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, *card)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:apre:"))
async def cb_admin_preset(cb: CallbackQuery, shop: Shop):
    _, _, uid, key = cb.data.split(":")
    uid = int(uid)
    if key not in PERM_PRESETS or uid in shop.cfg.admin_ids:
        await cb.answer("نامعتبر", show_alert=True)
        return
    perms = PERM_PRESETS[key][1]
    if perms == ["*"] and not is_full(shop, cb.from_user.id):
        await cb.answer("فقط مدیر کامل می‌تواند مدیر کامل بسازد.", show_alert=True)
        return
    if "admins" in perms and not is_full(shop, cb.from_user.id):
        await cb.answer("فقط مدیر کامل می‌تواند دسترسی مدیریت ادمین‌ها بدهد.", show_alert=True)
        return
    u = await shop.db.get_user(uid)
    if not u:
        await cb.answer("کاربر پیدا نشد", show_alert=True)
        return
    val = "*" if perms == ["*"] else ",".join(perms)
    if not await shop.db.add_admin(uid, cb.from_user.id, val, u["first_name"] or ""):
        await shop.db.update_admin(uid, perms=val)
    await shop.db.log(f"tg:{cb.from_user.id}", "admin_set", f"{uid} {key}")
    await shop.refresh_admins()
    await shop.safe_send(uid, "👮 شما ادمین ربات شدید. برای ورود به پنل /admin را بفرستید.")
    await view(cb, *(await admin_card(shop, uid)))
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:ap:"))
async def cb_admin_perm(cb: CallbackQuery, shop: Shop):
    _, _, uid, perm = cb.data.split(":")
    uid = int(uid)
    if perm not in PERMS or uid in shop.cfg.admin_ids:
        await cb.answer("نامعتبر", show_alert=True)
        return
    if perm == "admins" and not is_full(shop, cb.from_user.id):
        await cb.answer("فقط مدیر کامل می‌تواند این دسترسی را بدهد/بگیرد.", show_alert=True)
        return
    cur = shop.admin_perms.get(uid, set())
    if cur == "*":
        if not is_full(shop, cb.from_user.id):
            await cb.answer("تغییر مدیر کامل فقط توسط مدیر کامل ممکن است.", show_alert=True)
            return
        cur = set(PERMS)
    cur = set(cur)
    cur.symmetric_difference_update({perm})
    await shop.db.update_admin(uid, perms=",".join(p for p in PERMS if p in cur))
    await shop.db.log(f"tg:{cb.from_user.id}", "admin_perm", f"{uid} {perm}")
    await shop.refresh_admins()
    await view(cb, *(await admin_card(shop, uid)))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:arm:"))
async def cb_admin_remove(cb: CallbackQuery, shop: Shop):
    parts = cb.data.split(":")
    uid = int(parts[2])
    if uid in shop.cfg.admin_ids:
        await cb.answer("مالک حذف نمی‌شود.", show_alert=True)
        return
    if shop.admin_perms.get(uid) == "*" and not is_full(shop, cb.from_user.id):
        await cb.answer("حذف مدیر کامل فقط توسط مدیر کامل.", show_alert=True)
        return
    if len(parts) > 3 and parts[3] == "yes":
        await shop.db.remove_admin(uid)
        await shop.db.log(f"tg:{cb.from_user.id}", "admin_remove", str(uid))
        await shop.refresh_admins()
        await cb.answer("حذف شد")
        await cb_admins(cb.model_copy(update={"data": "adm:admins"}), shop, None)
        return
    await view(cb, f"⚠️ ادمین <code>{uid}</code> حذف شود؟", ikb([[btn("✅ بله", f"adm:arm:{uid}:yes", role="reject"), btn("❌ خیر", f"adm:ad:{uid}", role="back")]]))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:an:"))
async def cb_admin_name(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.admin_name)
    await state.update_data(uid=int(cb.data.split(":")[2]))
    await cb.message.answer("نام نمایشی ادمین را بفرستید:")
    await cb.answer()


@router.message(Adm.admin_name, TXT)
async def got_admin_name(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await shop.db.update_admin(d["uid"], name=m.text.strip())
    await m.answer("✅ ذخیره شد.")
    card = await admin_card(shop, d["uid"])
    if card:
        await ans(m, card)


@router.callback_query(F.data.startswith("adm:aw:"))
async def cb_admin_web(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.admin_web)
    await state.update_data(uid=int(cb.data.split(":")[2]))
    await cb.message.answer("نام کاربری و رمز ورود به پنل وب را با فاصله بفرستید: <code>username password</code>\n"
                            "(رمز حداقل ۸ نویسه؛ پیام شما بعد از ذخیره پاک می‌شود. برای حذف ورود وب «-» بفرستید.)")
    await cb.answer()


@router.message(Adm.admin_web, TXT)
async def got_admin_web(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    t = m.text.strip()
    if t == "-":
        await shop.db.set_admin_web(d["uid"], None, None)
    else:
        parts = t.split()
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", parts[0]) or len(parts[1]) < 8:
            await m.answer("فرمت نادرست؛ نام کاربری انگلیسی (۳ تا ۲۴) و رمز حداقل ۸ نویسه.")
            return
        other = await shop.db.get_admin_by_web_user(parts[0])
        if other and other["user_id"] != d["uid"]:
            await m.answer("این نام کاربری قبلاً استفاده شده است.")
            return
        await shop.db.set_admin_web(d["uid"], parts[0], hash_pw(parts[1]))
        try:
            await m.delete()
        except TelegramBadRequest:
            pass
    await state.clear()
    await shop.db.log(f"tg:{m.from_user.id}", "admin_web", str(d["uid"]))
    await m.answer("✅ ذخیره شد.")
    card = await admin_card(shop, d["uid"])
    if card:
        await ans(m, card)


# ============================================================ پیام همگانی
AUDIENCES = [("all", "👥 همه"), ("buyers", "🛒 خریداران"), ("nobuy", "بدون خرید"), ("balance", "👛 دارای موجودی"),
             ("referrers", "🤝 معرف‌ها"), ("new7", "🆕 ۷ روز اخیر"), ("inactive30", "😴 غیرفعال ۳۰ روز")]


async def audience_ids(shop: Shop, aud: str):
    return await shop.audience_ids(aud)


@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    st = shop.broadcast_state
    if st["running"]:
        await view(cb, f"📢 یک ارسال در حال انجام است: {st['sent'] + st['failed']}/{st['total']} (موفق {st['sent']}، ناموفق {st['failed']})",
                   ikb([[btn("🔄 بروزرسانی", "adm:bcast", role="action")], back_row()]))
        await cb.answer()
        return
    kb = []
    for i in range(0, len(AUDIENCES), 2):
        kb.append([btn(t, f"adm:bcasta:{k}", role="admin") for k, t in AUDIENCES[i:i + 2]])
    langs = shop.bot_langs()
    if len(langs) > 1:
        for i in range(0, len(langs), 2):
            kb.append([btn(f"{l['flag']} {l['name']}", f"adm:bcasta:l-{l['code']}", role="admin") for l in langs[i:i + 2]])
    kb.append(back_row())
    last = f"\nآخرین ارسال: موفق {st['sent']}، ناموفق {st['failed']} از {st['total']}" if st["total"] else ""
    await view(cb, "📢 <b>پیام همگانی</b>\nمخاطبان را انتخاب کنید:" + last, ikb(kb))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:bcasta:"))
async def cb_bcast_aud(cb: CallbackQuery, shop: Shop, state: FSMContext):
    aud = cb.data.split(":")[2]
    ids = await audience_ids(shop, aud)
    await state.set_state(Adm.bcast)
    await state.update_data(aud=aud)
    await cb.message.answer(f"👥 مخاطبان: <b>{fmt(len(ids))}</b> نفر.\nحالا پیام را بفرستید (متن، عکس، ویدیو، فایل ... با فرمت و ایموجی پریمیوم؛ همان‌طور که هست کپی می‌شود). برای لغو /admin")
    await cb.answer()


@router.message(Adm.bcast)
async def got_bcast(m: Message, shop: Shop, state: FSMContext):
    if (m.text or "").startswith("/"):
        return
    d = await state.get_data()
    ids = await audience_ids(shop, d["aud"])
    await state.update_data(chat=m.chat.id, mid=m.message_id, n=len(ids))
    await m.reply(f"این پیام برای <b>{fmt(len(ids))}</b> نفر ارسال شود؟",
                  reply_markup=ikb([[btn("✅ ارسال", "adm:bgo", role="confirm"), btn("❌ لغو", "adm:home", role="reject")]]))


@router.callback_query(F.data == "adm:bgo")
async def cb_bgo(cb: CallbackQuery, shop: Shop, state: FSMContext):
    d = await state.get_data()
    if not d.get("mid"):
        await cb.answer("پیامی برای ارسال نیست.", show_alert=True)
        return
    await state.clear()
    ids = await audience_ids(shop, d["aud"])
    chat, mid = d["chat"], d["mid"]

    async def sender(cid):
        await shop.bot.copy_message(cid, chat, mid)

    if not await shop.start_broadcast(sender, ids):
        await cb.answer("یک ارسال دیگر در حال انجام است.", show_alert=True)
        return
    await shop.db.log(f"tg:{cb.from_user.id}", "broadcast", f"{d['aud']} n={len(ids)}")
    await cb.message.answer(f"📢 ارسال برای {fmt(len(ids))} نفر شروع شد؛ پایان آن را اعلام می‌کنم.", reply_markup=ikb([back_row()]))
    await cb.answer()
