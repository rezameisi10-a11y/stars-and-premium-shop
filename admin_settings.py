"""پنل مدیریت داخل ربات (بخش تنظیمات): گروه‌های تنظیمات، نرخ‌ها و قیمت‌گذاری، تست API، چیدمان/رنگ/ایموجی پریمیوم دکمه‌ها."""
import time

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from admin_common import ans, C2S, TXT, Adm, back_row, make_router, short, view
from apiutil import D
from service import Shop
from settings_schema import FIELDS, GROUP_IDS, GROUPS, SAMPLE_VARS, TEXT_KEYS, select_options
from ui import ROLE_LABELS, STYLE_DOTS, btn, esc, fmt, hint_tg, ikb

router = make_router()
PAGE = 8
SECRET_WORDS = ("headers", "merchant", "_key")
S2C = {v: k for k, v in C2S.items()}
FROM_RATES: set[int] = set()   # ادمین‌هایی که گروه تنظیمات را از «نرخ‌ها و قیمت‌گذاری» باز کرده‌اند (بازگشت به همان منو)
STYLE_ORDER = [("d", "⚪"), ("p", "🔵"), ("s", "🟢"), ("x", "🔴")]


def ago(ts) -> str:
    if not ts:
        return "—"
    d = int(time.time() - ts)
    if d < 90:
        return f"{d} ثانیه پیش"
    if d < 5400:
        return f"{d // 60} دقیقه پیش"
    if d < 172800:
        return f"{d // 3600} ساعت پیش"
    return f"{d // 86400} روز پیش"


def is_secret(key: str) -> bool:
    typ = FIELDS[key][1]
    return typ == "secret" or any(w in key for w in SECRET_WORDS)


def shown(key: str, val) -> str:
    val = "" if val is None else str(val)
    if not val:
        return "—"
    if is_secret(key):
        return "●●●●" + val[-3:] if len(val) > 8 else "●●●●"
    typ = FIELDS[key][1]
    if typ == "bool":
        return "✅" if val == "1" else "⬜"
    one = val.replace("\n", " ")
    return one if len(one) <= 22 else one[:20] + "…"


def page_of(gid: str, key: str) -> int:
    keys = [f[0] for f in GROUP_IDS[gid][3]]
    return keys.index(key) // PAGE if key in keys else 0


# ============================================================ تنظیمات
def allowed_groups(shop: Shop, uid: int):
    return [g for g in GROUPS if shop.can(uid, g[2])]


@router.callback_query(F.data == "adm:set")
async def cb_settings(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    FROM_RATES.discard(cb.from_user.id)
    groups = allowed_groups(shop, cb.from_user.id)
    kb = [[btn(g[1], f"adm:sg:{g[0]}:0", role="admin")] for g in groups]
    kb.append(back_row())
    await view(cb, "⚙️ <b>تنظیمات</b>\nیک بخش را انتخاب کنید:", ikb(kb))
    await cb.answer()


EXTRA_BUTTONS = {
    "general": [("📢 کانال‌های عضویت اجباری", "adm:fj")],
    "rates": [("🧪 تست API دلار", "adm:rates:t:usd"), ("🧪 تست API تون", "adm:rates:t:ton"), ("🔄 بروزرسانی نرخ", "adm:ratesnow")],
    "pricing": [("🧪 تست API استارز", "adm:rates:t:fs"), ("🧪 تست API پریمیوم", "adm:rates:t:fp"), ("💹 نمونه قیمت‌ها", "adm:rates")],
    "delivery": [("🧪 تست API موجودی", "adm:rates:t:pb"), ("⭐ موجودی ربات", "adm:stars")],
    "group": [("🗂 ساخت تاپیک‌ها", "adm:grp:topics"), ("📊 گزارش همین حالا", "adm:grp:report"), ("💾 بکاپ همین حالا", "adm:grp:backup"),
              ("🧪 پیام تست", "adm:grp:test"), ("ℹ️ راهنمای اتصال", "adm:grp:help"), ("🔌 قطع اتصال", "adm:grp:off")],
}


def group_view(shop: Shop, gid: str, page: int, uid: int = 0):
    g = GROUP_IDS[gid]
    fields = g[3]
    pages = max(1, (len(fields) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    s = shop.s
    kb = []
    lines = []
    for i, (key, label, typ, _h) in enumerate(fields[page * PAGE:(page + 1) * PAGE], 1):
        val = s.get(key, "")
        if typ == "bool":
            kb.append([btn(f"{'✅' if val == '1' else '⬜'} {short(label, 30)}", f"adm:sf:{key}", role="admin")])
            lines.append(f"{i}. {esc(label)}: {'✅' if val == '1' else '⬜'}")
        else:
            kb.append([btn(f"{i}. {short(label, 20)} ‹{short(shown(key, val), 10)}›", f"adm:sf:{key}", role="admin")])
            lines.append(f"{i}. {esc(label)}: <code>{esc(shown(key, val))}</code>")
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:sg:{gid}:{page - 1}", role="nav"))
    if pages > 1:
        nav.append(btn(f"{page + 1}/{pages}", "adm:noop", role="nav"))
    if page < pages - 1:
        nav.append(btn("▶️", f"adm:sg:{gid}:{page + 1}", role="nav"))
    if nav:
        kb.append(nav)
    extra = EXTRA_BUTTONS.get(gid, [])
    for i in range(0, len(extra), 2):
        kb.append([btn(t, d, role="action") for t, d in extra[i:i + 2]])
    kb.append(back_row("adm:rates" if uid in FROM_RATES else "adm:set"))
    return f"⚙️ <b>{esc(g[1])}</b>\n\n" + "\n".join(lines) + "\n\nروی هر مورد بزنید تا تغییر کند.", ikb(kb)


@router.callback_query(F.data.startswith("adm:sg:"))
async def cb_group(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    parts = cb.data.split(":")
    gid = parts[2]
    if gid not in GROUP_IDS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    if len(parts) > 4 and parts[4] == "r":
        FROM_RATES.add(cb.from_user.id)
    await view(cb, *group_view(shop, gid, page, cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:sf:"))
async def cb_field(cb: CallbackQuery, shop: Shop, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in FIELDS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    label, typ, hlp = FIELDS[key]
    gid = next(g[0] for g in GROUPS if key in [f[0] for f in g[3]])
    s = await shop.settings()
    if typ == "bool":
        new = "0" if s.get(key) == "1" else "1"
        ok, res = await shop.set_setting_validated(key, new, f"tg:{cb.from_user.id}")
        if not ok:
            await cb.answer(res, show_alert=True)
            return
        await view(cb, *group_view(shop, gid, page_of(gid, key), cb.from_user.id))
        await cb.answer("انجام شد")
        return
    if typ.startswith("select:"):
        opts = select_options(typ)
        kb = [[btn(("✔ " if s.get(key) == o else "") + o, f"adm:sv:{key}:{i}", role="admin")] for i, o in enumerate(opts)]
        kb.append(back_row(f"adm:sg:{gid}:{page_of(gid, key)}"))
        await view(cb, f"<b>{esc(label)}</b>\n{hint_tg(hlp)}\n\nمقدار فعلی: <code>{esc(s.get(key, '') or '—')}</code>", ikb(kb))
        await cb.answer()
        return
    await state.set_state(Adm.set_value)
    await state.update_data(key=key, gid=gid)
    cur = shown(key, s.get(key, ""))
    extra = ""
    if key in TEXT_KEYS:
        extra = "\n💡 می‌توانید متن را با فرمت (ضخیم، ایموجی پریمیوم و ...) بفرستید؛ همان‌طور که هست ذخیره می‌شود."
    kb = ikb([[btn("🗑 پاک کردن مقدار", f"adm:sx:{key}", role="reject"), btn("❌ انصراف", f"adm:sg:{gid}:{page_of(gid, key)}", role="back")]])
    await cb.message.answer(f"<b>{esc(label)}</b>\n{hint_tg(hlp)}\n\nمقدار فعلی: <code>{esc(cur)}</code>\n\nمقدار جدید را بفرستید:" + extra
                            + ("\n🔒 پیام شما بعد از ذخیره پاک می‌شود." if is_secret(key) else ""), reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:sx:"))
async def cb_field_clear(cb: CallbackQuery, shop: Shop, state: FSMContext):
    key = cb.data.split(":")[2]
    await state.clear()
    if key not in FIELDS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    ok, res = await shop.set_setting_validated(key, "", f"tg:{cb.from_user.id}")
    await cb.answer("پاک شد" if ok else res, show_alert=not ok)


@router.callback_query(F.data.startswith("adm:sv:"))
async def cb_select_val(cb: CallbackQuery, shop: Shop):
    _, _, key, idx = cb.data.split(":")
    if key not in FIELDS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    opts = select_options(FIELDS[key][1])
    if not idx.isdigit() or int(idx) >= len(opts):
        await cb.answer("نامعتبر", show_alert=True)
        return
    ok, res = await shop.set_setting_validated(key, opts[int(idx)], f"tg:{cb.from_user.id}")
    if not ok:
        await cb.answer(res, show_alert=True)
        return
    gid = next(g[0] for g in GROUPS if key in [f[0] for f in g[3]])
    await view(cb, *group_view(shop, gid, page_of(gid, key), cb.from_user.id))
    await cb.answer("ذخیره شد")


@router.message(Adm.set_value, TXT)
async def got_value(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    key = d["key"]
    raw = m.html_text if key in TEXT_KEYS else m.text
    ok, res = await shop.set_setting_validated(key, raw, f"tg:{m.from_user.id}")
    if not ok:
        await m.answer(f"❌ {esc(res)}\nدوباره بفرستید یا /admin.")
        return
    await state.clear()
    if is_secret(key):
        try:
            await m.delete()
        except Exception:  # noqa: BLE001
            pass
    await m.answer("✅ ذخیره شد.", reply_markup=ikb([[btn("🔙 بازگشت به تنظیمات", f"adm:sg:{d['gid']}:{page_of(d['gid'], key)}", role="back")]]))


# ============================================================ کانال‌های عضویت اجباری
FJ_STYLES = ["", "primary", "success", "danger"]
FJ_DOT = {"": "🔵", "primary": "🔵", "success": "🟢", "danger": "🔴"}
FJ_FIELDS = {"c": "آیدی کانال (@mychannel)", "t": "متن دکمه", "l": "لینک دعوت (https://t.me/...)", "e": "آیدی ایموجی پریمیوم (فقط عدد)"}


def fj_list_view(shop: Shop):
    items = shop.force_list()
    on = shop.s.get("force_join") == "1"
    lines = [f"{i}. {FJ_DOT[c['s']]} <b>{esc(c['t'] or 'بدون متن (پیش‌فرض)')}</b> — <code>{esc(c['c'])}</code>" for i, c in enumerate(items, 1)]
    text = ("📢 <b>کانال‌های عضویت اجباری</b>\n"
            + ("وضعیت: ✅ فعال" if on else "وضعیت: ⏹ غیرفعال (از «تنظیمات ← عمومی» گزینه‌ی عضویت اجباری را روشن کنید)")
            + "\nکاربر باید در همه‌ی کانال‌ها عضو باشد؛ ربات باید ادمین هر کانال باشد.\n\n"
            + ("\n".join(lines) if lines else "هنوز کانالی اضافه نشده است."))
    kb = [[btn(f"{i}. {FJ_DOT[c['s']]} {short(c['t'] or c['c'], 22)}", f"adm:fjc:{i - 1}", role="admin")] for i, c in enumerate(items, 1)]
    if len(items) < shop.FORCE_MAX:
        kb.append([btn("➕ افزودن کانال", "adm:fja", role="confirm")])
    kb.append(back_row("adm:sg:general:0"))
    return text, ikb(kb)


def fj_card_view(shop: Shop, i: int):
    items = shop.force_list()
    if not 0 <= i < len(items):
        return None
    c = items[i]
    text = (f"📢 <b>کانال {i + 1}</b>: <code>{esc(c['c'])}</code>\n"
            f"متن دکمه: {esc(c['t']) or '— (پیش‌فرض: «📢 عضویت در کانال»)'}\n"
            f"رنگ دکمه: {FJ_DOT[c['s']]} {STYLE_NAMES[c['s']]}\n"
            f"لینک دعوت: {esc(c['l']) or '— (از آیدی کانال ساخته می‌شود)'}\n"
            f"ایموجی پریمیوم: {esc(c['e']) or '—'}")
    kb = ikb([[btn("✏️ متن دکمه", f"adm:fje:{i}:t", role="action"), btn("🎨 رنگ دکمه", f"adm:fjs:{i}", role="action")],
              [btn("🔗 لینک دعوت", f"adm:fje:{i}:l", role="action"), btn("✨ ایموجی", f"adm:fje:{i}:e", role="action")],
              [btn("🗑 حذف کانال", f"adm:fjd:{i}", role="reject")], back_row("adm:fj")])
    return text, kb


STYLE_NAMES = {"": "پیش‌فرض (آبی)", "primary": "آبی", "success": "سبز", "danger": "قرمز"}


@router.callback_query(F.data == "adm:fj")
async def cb_fj(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await view(cb, *fj_list_view(shop))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:fjc:"))
async def cb_fj_card(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    card = fj_card_view(shop, int(cb.data.split(":")[2]))
    if not card:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, *card)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:fjs:"))
async def cb_fj_style(cb: CallbackQuery, shop: Shop):
    i = int(cb.data.split(":")[2])
    items = shop.force_list()
    if not 0 <= i < len(items):
        await cb.answer("پیدا نشد", show_alert=True)
        return
    items[i]["s"] = FJ_STYLES[(FJ_STYLES.index(items[i]["s"]) + 1) % len(FJ_STYLES)]
    ok, err = await shop.set_force_list(items, f"tg:{cb.from_user.id}")
    if not ok:
        await cb.answer(err, show_alert=True)
        return
    await view(cb, *fj_card_view(shop, i))
    await cb.answer("رنگ عوض شد")


@router.callback_query(F.data.startswith("adm:fje:"))
async def cb_fj_edit(cb: CallbackQuery, shop: Shop, state: FSMContext):
    _, _, _, i, field = cb.data.split(":")
    await state.set_state(Adm.fj_val)
    await state.update_data(i=int(i), field=field)
    await cb.message.answer(f"✏️ <b>{FJ_FIELDS[field]}</b> را بفرستید.\nبرای خالی کردن «-» بفرستید. (لغو: /admin)",
                            reply_markup=ikb([back_row(f"adm:fjc:{i}")]))
    await cb.answer()


@router.callback_query(F.data == "adm:fja")
async def cb_fj_add(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.fj_val)
    await state.update_data(i=-1, field="c", new={})
    await cb.message.answer("➕ ۱/۲ — آیدی یا لینک کانال را بفرستید (مثال: <code>@mychannel</code> یا <code>t.me/mychannel</code>).\n"
                            "برای کانال خصوصی: شناسه‌ی <code>-100…</code>؛ بعدش لینک دعوت را هم می‌پرسم.",
                            reply_markup=ikb([back_row("adm:fj")]))
    await cb.answer()


@router.message(Adm.fj_val, TXT)
async def got_fj(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    i, field, text = d["i"], d["field"], m.text.strip()
    items = shop.force_list()
    uid = f"tg:{m.from_user.id}"
    if i == -1:        # ویزارد افزودن: کانال ← متن دکمه ← (لینک برای خصوصی)
        new = d.get("new") or {}
        if field == "c":
            ok, v = shop.norm_channel(text)
            if not ok:
                await m.answer(f"❌ {esc(v)}")
                return
            new["c"] = v
            await state.update_data(new=new, field="t")
            await m.answer("۲/۲ — متن دکمه را بفرستید (مثلاً <code>اطلاع‌رسانی</code> یا <code>تخفیف‌ها</code>). برای متن پیش‌فرض «-» بفرستید.")
            return
        if field == "t":
            new["t"] = "" if text == "-" else text[:40]
            if new["c"].startswith("-100"):
                await state.update_data(new=new, field="l")
                await m.answer("این کانال خصوصی است؛ لینک دعوت را بفرستید (https://t.me/+...):")
                return
        elif field == "l":
            new["l"] = text
        ok, err = await shop.set_force_list(items + [new], uid)
        if not ok:
            await m.answer(f"❌ {esc(err)}")
            return
        await state.clear()
        await m.answer("✅ کانال اضافه شد. رنگ دکمه را از کارت کانال انتخاب کنید.", reply_markup=ikb([[btn("📢 مشاهده", f"adm:fjc:{len(items)}", role="admin")], back_row("adm:fj")]))
        return
    if not 0 <= i < len(items):
        await state.clear()
        await m.answer("کانال پیدا نشد.")
        return
    items[i][field] = "" if text == "-" else text
    ok, err = await shop.set_force_list(items, uid)
    if not ok:
        await m.answer(f"❌ {esc(err)}\nدوباره بفرستید یا /admin.")
        return
    await state.clear()
    await m.answer("✅ ذخیره شد.", reply_markup=ikb([[btn("📢 مشاهده", f"adm:fjc:{i}", role="admin")], back_row("adm:fj")]))


@router.callback_query(F.data.startswith("adm:fjd:"))
async def cb_fj_del(cb: CallbackQuery, shop: Shop):
    parts = cb.data.split(":")
    i = int(parts[2])
    items = shop.force_list()
    if not 0 <= i < len(items):
        await cb.answer("پیدا نشد", show_alert=True)
        return
    if len(parts) < 4:
        await view(cb, f"🗑 کانال <code>{esc(items[i]['c'])}</code> حذف شود؟",
                   ikb([[btn("✅ بله، حذف کن", f"adm:fjd:{i}:y", role="confirm"), btn("❌ نه", f"adm:fjc:{i}", role="back")]]))
        await cb.answer()
        return
    del items[i]
    await shop.set_force_list(items, f"tg:{cb.from_user.id}")
    await view(cb, *fj_list_view(shop))
    await cb.answer("حذف شد")


# ============================================================ نرخ‌ها و قیمت‌گذاری
SRC_LABEL = {"auto": "🟢 خودکار (API)", "manual": "🟡 دستی", "stale": "🔴 API پاسخ نمی‌دهد؛ نرخ دستی فعال است"}


async def rates_text(shop: Shop) -> str:
    snap = shop.pricing.snapshot()
    s = shop.s
    u, t = snap["usd"], snap["ton"]
    lines = ["💹 <b>نرخ‌ها و قیمت‌گذاری</b>\n",
             f"💵 دلار/تتر: <b>{fmt(u['value'])}</b> تومان — {SRC_LABEL[u['source']]}" + (f"\n    آخرین دریافت: {ago(u['ts'])}" if u["ts"] else "")
             + (f"\n    ⚠️ {esc(u['err'])}" if u["err"] else ""),
             f"💎 تون: <b>{fmt(t['value'])}</b> تومان — {SRC_LABEL[t['source']]}" + (f"\n    آخرین دریافت: {ago(t['ts'])}" if t["ts"] else "")
             + (f"\n    ⚠️ {esc(t['err'])}" if t["err"] else ""),
             "", f"روش قیمت استارز: <code>{esc(s.get('price_mode_stars'))}</code> | پریمیوم: <code>{esc(s.get('price_mode_premium'))}</code>",
             f"سود پایه (مخفی از کاربر): <b>{esc(s.get('margin_percent', '10'))}٪</b> | کارمزد ثابت: {fmt(D(s.get('fee_fixed'), 0))} ت | گرد کردن: {fmt(D(s.get('round_step'), 1000))}",
             "", "<b>نمونه‌ی قیمت‌ها</b> (فروش / هزینه / سود):"]
    for kind in ("stars", "premium"):
        for p in (await shop.db.list_products(kind))[:6]:
            price, cost = shop.pricing.quote(p)
            eq = shop.equivalents(price)
            ex = (f" ≈ {eq['usdt']:,.2f}$" if eq["usdt"] is not None else "") + (f" | {eq['ton']:,.2f}TON" if eq["ton"] is not None else "")
            lines.append(f"• {esc(p['title'])}: {fmt(price)} ({fmt(eq['rial'])} ریال{ex}) / {fmt(cost)} / {fmt(price - cost)}")
    return "\n".join(lines)


def rates_kb():
    return ikb([
        [btn("🔄 بروزرسانی همین حالا", "adm:ratesnow", role="confirm")],
        [btn("✏️ نرخ دستی دلار", "adm:sf:usdt_toman", role="action"), btn("✏️ نرخ دستی تون", "adm:sf:ton_toman", role="action")],
        [btn("📈 درصد سود پایه", "adm:sf:margin_percent", role="action"), btn("💸 کارمزد ثابت", "adm:sf:fee_fixed", role="action")],
        [btn("⚙️ API نرخ‌ها", "adm:sg:rates:0:r", role="admin"), btn("⚙️ قیمت‌گذاری", "adm:sg:pricing:0:r", role="admin")],
        [btn("🚚 تحویل خودکار", "adm:sg:delivery:0:r", role="admin"), btn("🛰 Marketapp", "adm:sg:marketapp:0:r", role="admin")],
        [btn("📋 پیش‌تنظیم API ریالی", "adm:ratespre", role="action")],
        [btn("🧪 تست قیمت Marketapp", "adm:ratesmapp:price", role="action"), btn("🧪 تست کیف پول TON", "adm:ratesmapp:wallet", role="action")],
        back_row(),
    ])


@router.callback_query(F.data == "adm:rates")
async def cb_rates(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await view(cb, await rates_text(shop), rates_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:ratesnow")
async def cb_rates_now(cb: CallbackQuery, shop: Shop):
    await cb.answer("در حال دریافت ...")
    await shop.pricing.refresh_rates()
    await shop.pricing.refresh_costs()
    await view(cb, await rates_text(shop), rates_kb())


@router.callback_query(F.data == "adm:ratespre")
async def cb_presets(cb: CallbackQuery):
    from rate_presets import PRESETS
    rows = [[btn(v[0][:40], f"adm:ratespre:{k}", role="action")] for k, v in PRESETS.items()]
    rows.append(back_row("adm:rates"))
    await view(cb, "📋 یکی را انتخاب کنید تا فیلدهای API نرخ (آدرس، متد، مسیر، واحد) خودکار پر شود؛ سپس از «API نرخ‌ها» تست کنید.\nاین آدرس‌ها پیشنهادی‌اند و ممکن است تغییر کنند.", ikb(rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ratespre:"))
async def cb_preset_apply(cb: CallbackQuery, shop: Shop):
    from rate_presets import PRESETS, preset_settings
    pid = cb.data.split(":", 2)[2]
    if pid not in PRESETS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    for k, v in preset_settings(pid).items():
        await shop.set_setting(k, v, actor=f"tg:{cb.from_user.id}")
    await shop.pricing.refresh_rates()
    await cb.answer("اعمال شد")
    await view(cb, await rates_text(shop), rates_kb())


@router.callback_query(F.data.startswith("adm:ratesmapp:"))
async def cb_mapp_test(cb: CallbackQuery, shop: Shop):
    import marketapp
    import tonwallet
    what = cb.data.split(":")[2]
    s = await shop.settings()
    await cb.answer("در حال آزمایش ...")
    if not marketapp.configured(s):
        await cb.message.answer("⚠️ توکن Marketapp تنظیم نشده است (Marketapp از تنظیمات).")
        return
    if what == "price":
        r = await marketapp.test_all(shop, s, 50)
        txt = "🧪 <b>Marketapp</b>\n"
        for k in ("stars", "premium"):
            x = r[k]
            txt += (f"✅ {k}: <code>{esc(str(x['data'])[:300])}</code>\n" if x["ok"] else f"❌ {k}: {esc(x['error'])}\n")
        await cb.message.answer(txt)
        return
    try:
        i = await tonwallet.get_info(shop, s)
        await cb.message.answer(f"👛 <code>{esc(i['address'])}</code> ({i['version']})\nموجودی: {i['balance'] / 1e9:.4f} TON | seqno {i['seqno']} | {esc(i['state'])}\n"
                                f"تطبیق با آدرس تنظیم‌شده: {i['matches']}")
    except Exception as ex:  # noqa: BLE001
        await cb.message.answer(f"❌ {esc(str(ex)[:300])}")


@router.callback_query(F.data.startswith("adm:rates:t:"))
async def cb_rates_test(cb: CallbackQuery, shop: Shop):
    what = cb.data.split(":")[3]
    s = await shop.settings()
    pr = shop.pricing
    await cb.answer("در حال آزمایش ...")
    if what in ("usd", "ton"):
        if not s.get(f"{what}_api_url"):
            await cb.message.answer("⚠️ آدرس API تنظیم نشده است.")
            return
        r = await pr.probe(s[f"{what}_api_url"], s.get(f"{what}_api_method", "GET"), s.get(f"{what}_api_headers", ""), s.get(f"{what}_api_body", ""), s.get(f"{what}_api_path", ""))
        title = "دلار" if what == "usd" else "تون"
    elif what in ("fs", "fp"):
        kind = "stars" if what == "fs" else "premium"
        p = f"frag_{kind}_"
        if not s.get(p + "url"):
            await cb.message.answer("⚠️ آدرس API تنظیم نشده است.")
            return
        qty = 50 if kind == "stars" else 3
        vars_ = {"qty": qty} if kind == "stars" else {"months": qty, "qty": qty}
        r = await pr.probe(s[p + "url"], s.get(p + "method", "GET"), s.get(p + "headers", ""), s.get(p + "body", ""), s.get(p + "path", ""), vars_)
        title = f"{'استارز (۵۰ عدد)' if kind == 'stars' else 'پریمیوم (۳ ماه)'}"
        if r["ok"] and r["value"]:
            try:
                tom = pr._api_to_toman(D(r["value"]), s.get(p + "unit", "ton"))
                r["value"] += f" {s.get(p + 'unit', 'ton')} ≈ {fmt(tom)} تومان"
            except Exception:  # noqa: BLE001
                pass
    else:
        if not s.get("prov_balance_url"):
            await cb.message.answer("⚠️ آدرس API موجودی تنظیم نشده است.")
            return
        r = await pr.probe(s["prov_balance_url"], "GET", s.get("prov_balance_headers", ""), None, s.get("prov_balance_path", ""))
        title = "موجودی سرویس‌دهنده"
    if r["ok"]:
        txt = f"✅ <b>تست {title}</b> موفق\nمقدار خوانده‌شده: <code>{esc(r['value'])}</code>"
    else:
        txt = f"❌ <b>تست {title}</b> ناموفق\n{esc(r['error'] or 'مقدار در مسیر JSON پیدا نشد')}"
    if r["preview"]:
        txt += f"\n\nپاسخ API (خلاصه):\n<code>{esc(r['preview'][:700])}</code>"
    await cb.message.answer(txt)


# ============================================================ دکمه‌های منو: چیدمان، رنگ، ایموجی پریمیوم
def extract_emoji_id(m: Message):
    for e in (m.entities or []):
        if e.type == "custom_emoji" and e.custom_emoji_id:
            return e.custom_emoji_id
    st = m.sticker
    if st is not None and getattr(st, "custom_emoji_id", None):
        return st.custom_emoji_id
    t = (m.text or "").strip()
    return t if t.isdigit() and 5 <= len(t) <= 25 else None


EMOJI_HELP = ("🌟 یک پیام که <b>ایموجی پریمیوم</b> (ایموجی سفارشی) دارد بفرستید؛ ربات شناسه‌ی آن را می‌خواند.\n"
              "• اگر اکانت شما پریمیوم است، ایموجی را از کیبورد ایموجی بفرستید.\n"
              "• اگر نه، پیامی که آن ایموجی را دارد از یک چت یا کانال برای ربات فوروارد کنید.\n"
              "• یا خود شناسه‌ی عددی را بفرستید.\n"
              "برای لغو /admin")


def style_row(prefix: str, cur: str):
    return [btn(("✔" if S2C.get(cur, "d") == c else "") + dot, f"{prefix}:{c}", style="") for c, dot in STYLE_ORDER]


async def buttons_view(shop: Shop):
    items = await shop.db.list_menu()
    rows, cur, last = [], None, None
    for it in items:
        if it["row_no"] != last:
            rows.append([])
            last = it["row_no"]
        mark = STYLE_DOTS.get(it["style"], "⚪") + ("🌟" if it.get("emoji_id") else "")
        rows[-1].append(btn(f"{mark} {short(it['label'], 18)}{'' if it['enabled'] else ' 🚫'}", f"adm:bt:{it['id']}", style=""))
    s = shop.s
    on = s.get("premium_emoji_enabled", "1") == "1"
    kb = rows + [
        [btn("➕ دکمه‌ی جدید (لینک/متن)", "adm:badd", role="confirm")],
        [btn("🎨 رنگ و ایموجی گروه‌های دکمه", "adm:roles", role="action")],
        [btn("🧩 همه‌ی دکمه‌های ربات (جداگانه)", "adm:ba:user:0", role="action")],
        [btn(f"{'✅' if on else '⬜'} ایموجی پریمیوم روی دکمه‌ها", "adm:rem:_t", role="action")],
        [btn("♻️ بازگردانی چیدمان پیش‌فرض", "adm:breset", role="reject")],
        back_row(),
    ]
    text = ("🎨 <b>دکمه‌های منوی اصلی</b>\nروی هر دکمه بزنید تا رنگ، ایموجی پریمیوم، متن، جایگاه و ... را تغییر دهید.\n"
            "چیدمان همان چیزی است که کاربر می‌بیند. 🌟 = دارای ایموجی پریمیوم | 🚫 = غیرفعال")
    return text, ikb(kb)


@router.callback_query(F.data == "adm:btn")
async def cb_buttons(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    await view(cb, *(await buttons_view(shop)))
    await cb.answer()


ACTION_FA = {"stars": "خرید استارز", "premium": "پریمیوم", "gift": "گیفت", "wallet": "کیف پول", "orders": "سفارش‌ها", "referral": "زیرمجموعه‌گیری",
             "rules": "قوانین", "support": "پشتیبانی", "lang": "زبان", "admin": "پنل مدیریت", "url": "لینک", "text": "پیام متنی"}


async def button_card(shop: Shop, bid):
    b = await shop.db.get_menu_button(bid)
    if not b:
        return None
    cur = b["style"] or ""
    text = (f"🎛 <b>{esc(b['label'])}</b>\nنوع: {ACTION_FA.get(b['action'], b['action'])}"
            + (f"\nمقصد/متن: {esc((b['extra'] or '')[:150])}" if b["action"] in ("url", "text") else "")
            + f"\nرنگ: {STYLE_DOTS.get(cur, '⚪')} | وضعیت: {'✅ فعال' if b['enabled'] else '🚫 غیرفعال'}\n"
            + (f"🌟 ایموجی پریمیوم: <code>{b['emoji_id']}</code>" if b.get("emoji_id") else "🌟 ایموجی پریمیوم: ندارد"))
    kb = [style_row(f"adm:bcol:{bid}", cur),
          [btn("🌟 ایموجی پریمیوم", f"adm:bem:{bid}", role="action"), btn("✖️ حذف ایموجی", f"adm:bem:{bid}:clr", role="reject")],
          [btn("🚫 غیرفعال" if b["enabled"] else "✅ فعال", f"adm:bena:{bid}", role="admin"), btn("✏️ متن دکمه", f"adm:bren:{bid}", role="action")]]
    if b["action"] in ("url", "text"):
        kb.append([btn("🔗 ویرایش لینک" if b["action"] == "url" else "📝 ویرایش متن پیام", f"adm:bext:{bid}", role="action")])
    kb.append([btn("◀️", f"adm:bmv:{bid}:left", role="nav"), btn("▶️", f"adm:bmv:{bid}:right", role="nav"),
               btn("🔼 ردیف", f"adm:bmv:{bid}:rowup", role="nav"), btn("🔽 ردیف", f"adm:bmv:{bid}:rowdown", role="nav")])
    kb.append([btn("↗️ ادغام با ردیف بالا", f"adm:bmv:{bid}:join_up", role="nav"), btn("↙️ جدا کردن", f"adm:bmv:{bid}:split", role="nav")])
    if not b["builtin"]:
        kb.append([btn("🗑 حذف دکمه", f"adm:bdel:{bid}", role="reject")])
    kb.append(back_row("adm:btn"))
    return text, ikb(kb)


@router.callback_query(F.data.startswith("adm:bt:"))
async def cb_button(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    card = await button_card(shop, int(cb.data.split(":")[2]))
    if not card:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, *card)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:bcol:"))
async def cb_bcolor(cb: CallbackQuery, shop: Shop):
    _, _, bid, c = cb.data.split(":")
    if c in C2S:
        await shop.db.update_menu_button(int(bid), style=C2S[c])
        await shop.db.log(f"tg:{cb.from_user.id}", "button_color", f"{bid}={C2S[c]}")
    card = await button_card(shop, int(bid))
    if card:
        await view(cb, *card)
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:bena:"))
async def cb_benable(cb: CallbackQuery, shop: Shop):
    bid = int(cb.data.split(":")[2])
    b = await shop.db.get_menu_button(bid)
    if b:
        await shop.db.update_menu_button(bid, enabled=0 if b["enabled"] else 1)
    card = await button_card(shop, bid)
    if card:
        await view(cb, *card)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:bmv:"))
async def cb_bmove(cb: CallbackQuery, shop: Shop):
    _, _, bid, op = cb.data.split(":")
    ok = await shop.db.move_menu_button(int(bid), op)
    if not ok:
        await cb.answer("این جابجایی ممکن نیست.")
        return
    card = await button_card(shop, int(bid))
    if card:
        await view(cb, *card)
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:bdel:"))
async def cb_bdelete(cb: CallbackQuery, shop: Shop):
    ok = await shop.db.delete_menu_button(int(cb.data.split(":")[2]))
    await cb.answer("حذف شد" if ok else "دکمه‌های اصلی حذف نمی‌شوند؛ غیرفعالشان کنید.", show_alert=not ok)
    await view(cb, *(await buttons_view(shop)))


@router.callback_query(F.data == "adm:breset")
async def cb_breset(cb: CallbackQuery, shop: Shop):
    await view(cb, "⚠️ چیدمان، رنگ‌ها، ایموجی و دکمه‌های سفارشی به حالت پیش‌فرض برمی‌گردد. مطمئنید؟",
               ikb([[btn("✅ بله، بازگردانی", "adm:breset:go", role="reject"), btn("❌ خیر", "adm:btn", role="back")]]))
    await cb.answer()


@router.callback_query(F.data == "adm:breset:go")
async def cb_breset_go(cb: CallbackQuery, shop: Shop):
    await shop.db.reset_menu()
    await shop.db.log(f"tg:{cb.from_user.id}", "menu_reset", "")
    await view(cb, *(await buttons_view(shop)))
    await cb.answer("بازگردانی شد")


@router.callback_query(F.data.startswith("adm:bren:"))
async def cb_brename(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.btn_label)
    await state.update_data(bid=int(cb.data.split(":")[2]))
    await cb.message.answer("متن جدید دکمه را بفرستید (حداکثر ۴۰ حرف؛ برای لغو /admin).\n"
                            "این همان متن زبان پیش‌فرض است؛ ترجمه‌ی سایر زبان‌ها از بخش «زبان‌ها» قابل ویرایش است.")
    await cb.answer()


@router.message(Adm.btn_label, TXT)
async def got_btn_label(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    await shop.db.update_menu_button(d["bid"], label=m.text.strip()[:40])
    await m.answer("✅ ذخیره شد.")
    card = await button_card(shop, d["bid"])
    if card:
        await ans(m, card)


@router.callback_query(F.data.startswith("adm:bext:"))
async def cb_bextra(cb: CallbackQuery, shop: Shop, state: FSMContext):
    bid = int(cb.data.split(":")[2])
    b = await shop.db.get_menu_button(bid)
    await state.set_state(Adm.badd_extra)
    await state.update_data(bid=bid, edit=1, action=b["action"] if b else "url")
    await cb.message.answer("🔗 لینک (https://... یا @username) را بفرستید:" if b and b["action"] == "url" else "📝 متن پیامی که با زدن دکمه نمایش داده شود را بفرستید:")
    await cb.answer()


@router.callback_query(F.data.startswith("adm:bem:"))
async def cb_bemoji(cb: CallbackQuery, shop: Shop, state: FSMContext):
    parts = cb.data.split(":")
    bid = int(parts[2])
    if len(parts) > 3 and parts[3] == "clr":
        await shop.db.update_menu_button(bid, emoji_id="")
        card = await button_card(shop, bid)
        if card:
            await view(cb, *card)
        await cb.answer("حذف شد")
        return
    await state.set_state(Adm.btn_emoji)
    await state.update_data(bid=bid)
    await cb.message.answer(EMOJI_HELP)
    await cb.answer()


@router.message(Adm.btn_emoji, ~F.text.startswith("/"))
async def got_btn_emoji(m: Message, shop: Shop, state: FSMContext):
    eid = extract_emoji_id(m)
    if not eid:
        await m.answer("ایموجی پریمیوم در پیام پیدا نشد. یک پیام با ایموجی پریمیوم بفرستید یا شناسه‌ی عددی را بفرستید. (لغو: /admin)")
        return
    d = await state.get_data()
    await state.clear()
    await shop.db.update_menu_button(d["bid"], emoji_id=eid)
    await shop.db.log(f"tg:{m.from_user.id}", "button_emoji", f"{d['bid']}={eid}")
    await m.answer(f"✅ ایموجی پریمیوم ثبت شد (<code>{eid}</code>).\n"
                   "اگر مالک ربات تلگرام پریمیوم نباشد تلگرام آن را نشان نمی‌دهد و ربات خودکار بدون ایموجی ادامه می‌دهد.")
    card = await button_card(shop, d["bid"])
    if card:
        await ans(m, card)


# ---------- افزودن دکمه‌ی سفارشی
@router.callback_query(F.data == "adm:badd")
async def cb_badd(cb: CallbackQuery):
    await view(cb, "نوع دکمه‌ی جدید:", ikb([[btn("🔗 لینک (کانال، سایت، ...)", "adm:badd:url", role="action"),
                                           btn("📝 پیام متنی", "adm:badd:text", role="action")], back_row("adm:btn")]))
    await cb.answer()


@router.callback_query(F.data.in_({"adm:badd:url", "adm:badd:text"}))
async def cb_badd_type(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.badd_label)
    await state.update_data(action=cb.data.split(":")[2])
    await cb.message.answer("متن دکمه را بفرستید (مثلاً «📣 کانال ما»):")
    await cb.answer()


@router.message(Adm.badd_label, TXT)
async def got_badd_label(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.update_data(label=m.text.strip()[:40])
    await state.set_state(Adm.badd_extra)
    await m.answer("🔗 لینک (https://... یا @username) را بفرستید:" if d["action"] == "url" else "📝 متن پیامی که با زدن دکمه نمایش داده شود را بفرستید:")


@router.message(Adm.badd_extra, TXT)
async def got_badd_extra(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    extra = m.html_text if d["action"] == "text" else m.text.strip()
    if d["action"] == "url":
        from service import normalize_url
        extra = normalize_url(extra)
        if not extra:
            await m.answer("لینک نامعتبر است. با https:// یا @username بفرستید.")
            return
    await state.clear()
    if d.get("edit"):
        await shop.db.update_menu_button(d["bid"], extra=extra)
        bid = d["bid"]
    else:
        bid = await shop.db.add_menu_button(d["action"], d["label"], extra)
        await shop.db.log(f"tg:{m.from_user.id}", "button_add", d["label"])
    await m.answer("✅ ذخیره شد.")
    card = await button_card(shop, bid)
    if card:
        await ans(m, card)


# ---------- رنگ و ایموجی گروه‌های دکمه
async def roles_view(shop: Shop):
    from ui import EMOJIS, STYLES
    kb = [[btn(f"{STYLE_DOTS.get(STYLES.get(r, ''), '⚪')}{'🌟' if EMOJIS.get(r) else ''} {lab}", f"adm:ro:{r}", style="")] for r, lab in ROLE_LABELS.items()]
    on = shop.s.get("premium_emoji_enabled", "1") == "1"
    kb.append([btn(f"{'✅' if on else '⬜'} ایموجی پریمیوم روی دکمه‌ها", "adm:rem:_t", role="action")])
    kb.append(back_row("adm:btn"))
    return "🎨 <b>رنگ و ایموجی گروه‌های دکمه</b>\nدکمه‌های داخل ربات (بازگشت، تأیید، پرداخت، ...) به‌صورت گروهی رنگ و ایموجی می‌گیرند:", ikb(kb)


@router.callback_query(F.data == "adm:roles")
async def cb_roles(cb: CallbackQuery, shop: Shop):
    await view(cb, *(await roles_view(shop)))
    await cb.answer()


async def role_card(role):
    from ui import EMOJIS, STYLES
    cur = STYLES.get(role, "")
    text = (f"🎨 <b>{ROLE_LABELS[role]}</b>\nرنگ فعلی: {STYLE_DOTS.get(cur, '⚪')}\n"
            + (f"🌟 ایموجی پریمیوم: <code>{EMOJIS[role]}</code>" if EMOJIS.get(role) else "🌟 ایموجی پریمیوم: ندارد"))
    kb = ikb([style_row(f"adm:rc:{role}", cur),
              [btn("🌟 تنظیم ایموجی", f"adm:rem:{role}", role="action"), btn("✖️ حذف ایموجی", f"adm:rem:{role}:clr", role="reject")],
              back_row("adm:roles")])
    return text, kb


@router.callback_query(F.data.startswith("adm:ro:"))
async def cb_role(cb: CallbackQuery):
    role = cb.data.split(":")[2]
    if role not in ROLE_LABELS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    await view(cb, *(await role_card(role)))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:rc:"))
async def cb_role_color(cb: CallbackQuery, shop: Shop):
    _, _, role, c = cb.data.split(":")
    if role in ROLE_LABELS and c in C2S:
        await shop.set_setting("style_" + role, C2S[c], f"tg:{cb.from_user.id}")
    await view(cb, *(await role_card(role)))
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:rem:"))
async def cb_role_emoji(cb: CallbackQuery, shop: Shop, state: FSMContext):
    parts = cb.data.split(":")
    role = parts[2]
    if role == "_t":
        new = "0" if shop.s.get("premium_emoji_enabled", "1") == "1" else "1"
        await shop.set_setting("premium_emoji_enabled", new, f"tg:{cb.from_user.id}")
        await view(cb, *(await buttons_view(shop)))
        await cb.answer("انجام شد")
        return
    if role not in ROLE_LABELS:
        await cb.answer("نامعتبر", show_alert=True)
        return
    if len(parts) > 3 and parts[3] == "clr":
        await shop.set_setting("emoji_" + role, "", f"tg:{cb.from_user.id}")
        await view(cb, *(await role_card(role)))
        await cb.answer("حذف شد")
        return
    await state.set_state(Adm.role_emoji)
    await state.update_data(role=role)
    await cb.message.answer(EMOJI_HELP)
    await cb.answer()


@router.message(Adm.role_emoji, ~F.text.startswith("/"))
async def got_role_emoji(m: Message, shop: Shop, state: FSMContext):
    eid = extract_emoji_id(m)
    if not eid:
        await m.answer("ایموجی پریمیوم در پیام پیدا نشد. دوباره بفرستید یا /admin.")
        return
    d = await state.get_data()
    await state.clear()
    await shop.set_setting("emoji_" + d["role"], eid, f"tg:{m.from_user.id}")
    await m.answer(f"✅ ثبت شد (<code>{eid}</code>).")
    await ans(m, (await role_card(d["role"])))


# ---------- شخصی‌سازی تک‌تک دکمه‌ها (همه‌ی دکمه‌های ربات)
BAPAGE = 8


def _cat(shop: Shop, scope: str):
    return [(i, c) for i, c in enumerate(shop.button_catalog()) if c["scope"] == scope]


def _bk_mark(shop: Shop, key: str) -> str:
    o = shop.button_overrides().get(key)
    if not o:
        return ""
    st = STYLE_DOTS.get("" if o["s"] == "d" else o["s"], "") if o["s"] else ""
    return st + ("🌟" if o["e"] else "") + ("✏️" if o["t"] else "")


async def all_buttons_view(shop: Shop, scope: str, page: int):
    items = _cat(shop, scope)
    pages = max(1, (len(items) + BAPAGE - 1) // BAPAGE)
    page = max(0, min(page, pages - 1))
    kb = []
    for i, c in items[page * BAPAGE:(page + 1) * BAPAGE]:
        mark = _bk_mark(shop, c["key"])
        kb.append([btn(f"{mark + ' ' if mark else ''}{short(c['text'] or c['key'], 26)}", f"adm:bk:{i}", style="")])
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:ba:{scope}:{page - 1}", role="nav"))
    nav.append(btn(f"{page + 1}/{pages}", "adm:noop", role="nav"))
    if page < pages - 1:
        nav.append(btn("▶️", f"adm:ba:{scope}:{page + 1}", role="nav"))
    kb.append(nav)
    other = "admin" if scope == "user" else "user"
    kb.append([btn("🛠 دکمه‌های پنل مدیریت" if scope == "user" else "👤 دکمه‌های کاربران", f"adm:ba:{other}:0", role="action")])
    kb.append(back_row("adm:btn"))
    title = "دکمه‌های کاربران" if scope == "user" else "دکمه‌های پنل مدیریت (داخل ربات)"
    return (f"🧩 <b>{title}</b>\nروی هر دکمه بزنید تا متن، رنگ یا ایموجی پریمیوم همان دکمه را جدا تنظیم کنید.\n"
            "نشانه‌ها: 🔵🟢🔴⚪ = رنگ اختصاصی | 🌟 = ایموجی | ✏️ = متن دلخواه"), ikb(kb)


@router.callback_query(F.data.startswith("adm:ba:"))
async def cb_all_buttons(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    parts = cb.data.split(":")
    scope = parts[2] if len(parts) > 2 and parts[2] in ("user", "admin") else "user"
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    await view(cb, *(await all_buttons_view(shop, scope, page)))
    await cb.answer()


def _bk_item(shop: Shop, idx):
    cat = shop.button_catalog()
    return cat[idx] if isinstance(idx, int) and 0 <= idx < len(cat) else None


async def bk_card(shop: Shop, idx: int):
    c = _bk_item(shop, idx)
    if not c:
        return None
    o = shop.button_overrides().get(c["key"], {"s": None, "e": "", "t": ""})
    cur = "" if o["s"] in (None, "d") else o["s"]
    st = "بدون تغییر (مطابق نوع دکمه)" if not o["s"] else ("⚪ پیش‌فرض" if o["s"] == "d" else STYLE_DOTS.get(cur, "⚪"))
    text = (f"🧩 <b>{esc(c['text'] or c['key'])}</b>\nکلید: <code>{esc(c['key'])}</code>"
            + ("\n(متن این دکمه پویاست؛ فقط رنگ و ایموجی تغییر می‌کند.)" if c["dyn"] else "")
            + f"\nرنگ: {st}\n🌟 ایموجی پریمیوم: " + (f"<code>{o['e']}</code>" if o["e"] else "ندارد")
            + ("\n✏️ متن دلخواه: " + esc(o["t"]) if o["t"] else ""))
    kb = [style_row(f"adm:bkc:{idx}", cur) if o["s"] else [btn(dot, f"adm:bkc:{idx}:{c}", style="") for c, dot in STYLE_ORDER],
          [btn("♻️ رنگ: مطابق نوع دکمه", f"adm:bkc:{idx}:n", role="action")],
          [btn("🌟 ایموجی پریمیوم", f"adm:bke:{idx}", role="action"), btn("✖️ حذف ایموجی", f"adm:bke:{idx}:clr", role="reject")]]
    if not c["dyn"]:
        kb.append([btn("✏️ متن دکمه", f"adm:bkt:{idx}", role="action"), btn("✖️ حذف متن", f"adm:bkt:{idx}:clr", role="reject")])
    kb.append(back_row(f"adm:ba:{c['scope']}:0"))
    return text, ikb(kb)


@router.callback_query(F.data.regexp(r"^adm:bk:\d+$"))
async def cb_bk(cb: CallbackQuery, shop: Shop, state: FSMContext):
    await state.clear()
    card = await bk_card(shop, int(cb.data.split(":")[2]))
    if not card:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    await view(cb, *card)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:bkc:"))
async def cb_bk_color(cb: CallbackQuery, shop: Shop):
    parts = cb.data.split(":")
    idx, c = int(parts[2]), parts[3]
    c_ = _bk_item(shop, idx)
    if not c_:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    if c == "n":
        await shop.set_button_override(c_["key"], s="", actor=f"tg:{cb.from_user.id}")
    elif c in C2S:
        await shop.set_button_override(c_["key"], s=("d" if C2S[c] == "" else C2S[c]), actor=f"tg:{cb.from_user.id}")
    await view(cb, *(await bk_card(shop, idx)))
    await cb.answer("انجام شد")


@router.callback_query(F.data.startswith("adm:bke:"))
async def cb_bk_emoji(cb: CallbackQuery, shop: Shop, state: FSMContext):
    parts = cb.data.split(":")
    idx = int(parts[2])
    c_ = _bk_item(shop, idx)
    if not c_:
        await cb.answer("پیدا نشد", show_alert=True)
        return
    if len(parts) > 3 and parts[3] == "clr":
        await shop.set_button_override(c_["key"], e="", actor=f"tg:{cb.from_user.id}")
        await view(cb, *(await bk_card(shop, idx)))
        await cb.answer("حذف شد")
        return
    await state.set_state(Adm.bk_emoji)
    await state.update_data(idx=idx)
    await cb.message.answer(EMOJI_HELP)
    await cb.answer()


@router.message(Adm.bk_emoji, ~F.text.startswith("/"))
async def got_bk_emoji(m: Message, shop: Shop, state: FSMContext):
    eid = extract_emoji_id(m)
    if not eid:
        await m.answer("ایموجی پریمیوم در پیام پیدا نشد. دوباره بفرستید یا /admin.")
        return
    d = await state.get_data()
    await state.clear()
    c_ = _bk_item(shop, d["idx"])
    ok, err = await shop.set_button_override(c_["key"], e=eid, actor=f"tg:{m.from_user.id}")
    await m.answer(f"✅ ثبت شد (<code>{eid}</code>)." if ok else f"❌ {err}")
    await ans(m, (await bk_card(shop, d["idx"])))


@router.callback_query(F.data.startswith("adm:bkt:"))
async def cb_bk_text(cb: CallbackQuery, shop: Shop, state: FSMContext):
    parts = cb.data.split(":")
    idx = int(parts[2])
    c_ = _bk_item(shop, idx)
    if not c_ or c_["dyn"]:
        await cb.answer("متن این دکمه قابل تغییر نیست.", show_alert=True)
        return
    if len(parts) > 3 and parts[3] == "clr":
        await shop.set_button_override(c_["key"], t="", actor=f"tg:{cb.from_user.id}")
        await view(cb, *(await bk_card(shop, idx)))
        await cb.answer("حذف شد")
        return
    await state.set_state(Adm.bk_text)
    await state.update_data(idx=idx)
    await cb.message.answer("متن جدید دکمه را بفرستید (حداکثر ۶۰ حرف؛ برای لغو /admin).\nاین متن برای همه‌ی زبان‌ها اعمال می‌شود؛ برای برگشت به ترجمه‌ها «حذف متن» را بزنید.")
    await cb.answer()


@router.message(Adm.bk_text, TXT)
async def got_bk_text(m: Message, shop: Shop, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    c_ = _bk_item(shop, d["idx"])
    ok, err = await shop.set_button_override(c_["key"], t=m.text.strip()[:60], actor=f"tg:{m.from_user.id}")
    await m.answer("✅ ذخیره شد." if ok else f"❌ {err}")
    await ans(m, (await bk_card(shop, d["idx"])))


# ---------- گروه گزارش‌ها
GRP_HELP = ("🗂 <b>اتصال ربات به گروه</b>\n"
            "۱) یک گروه (بهتر: سوپرگروه با گزینه‌ی <b>Topics</b> روشن و <b>خصوصی</b>) بسازید.\n"
            "۲) ربات را به گروه اضافه و <b>ادمین</b> کنید (دسترسی‌ها: Manage Topics و Post Messages).\n"
            "۳) داخل گروه دستور <code>/connect</code> را بفرستید (فقط ادمین‌های ربات).\n"
            "ربات خودش تاپیک‌های رسیدها، فروش، کاربران جدید، هشدارها، گزارش شبانه و بکاپ را می‌سازد.\n\n"
            "• ساعت گزارش شبانه و فاصله‌ی بکاپ و روشن/خاموش‌کردن هر رویداد همین‌جا تنظیم می‌شود.\n"
            "• اگر گروه تاپیک نداشت، همه‌چیز در چت اصلی گروه می‌آید.\n"
            "• ⚠️ فایل بکاپ شامل اطلاعات کاربران است؛ فقط ادمین‌های مورد اعتماد عضو گروه باشند.")


@router.callback_query(F.data.startswith("adm:grp:"))
async def cb_group_action(cb: CallbackQuery, shop: Shop):
    act = cb.data.split(":")[2]
    g = shop.group
    if act == "help":
        await cb.message.answer(GRP_HELP)
        await cb.answer()
        return
    if act == "off":
        await g.disconnect()
        await view(cb, *group_view(shop, "group", 0, cb.from_user.id))
        await cb.answer("ارسال به گروه خاموش شد")
        return
    if g.chat_id() is None:
        await cb.answer("هنوز گروهی متصل نشده است. راهنمای اتصال را بزنید.", show_alert=True)
        return
    if act == "topics":
        res = await g.ensure_topics()
        if res["error"]:
            await cb.answer("خطا: " + res["error"][:150], show_alert=True)
        else:
            await cb.answer("تاپیک‌ها آماده است" if res["forum"] else "گروه تاپیک ندارد؛ همه‌چیز در چت اصلی می‌آید", show_alert=True)
        return
    if not g.enabled():
        await cb.answer("ارسال به گروه خاموش است (گزینه‌ی «فعال بودن ارسال به گروه»).", show_alert=True)
        return
    if act == "test":
        ok = await g.post("alerts", "🧪 پیام آزمایشی ربات — اگر این را می‌بینید اتصال درست است.") or await g.post("reports", "🧪 پیام آزمایشی ربات.")
    elif act == "report":
        ok = await g.send_report()
    elif act == "backup":
        ok = await g.send_backup()
    else:
        await cb.answer("نامعتبر", show_alert=True)
        return
    await cb.answer("✅ ارسال شد" if ok else "❌ ارسال نشد؛ دسترسی ربات در گروه را بررسی کنید.", show_alert=not ok)
