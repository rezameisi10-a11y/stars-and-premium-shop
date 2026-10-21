"""پنل وب: محصولات و قیمت، نرخ‌ها و سود، ستاره و تحویل خودکار، تنظیمات (همه‌ی گروه‌ها) و تست API."""
import time

from aiogram.types import LabeledPrice
from aiohttp import web

import provider
from groupfeed import TOPICS as GROUP_TOPICS
from apiutil import D
from i18n import NW_, _
from service import KIND_FA, Shop
from settings_schema import FIELDS, GROUP_IDS, GROUPS, SAMPLE_VARS, render_template, select_options
from ui import esc as e
from ui import fmt
from webkit import (T, actor, badge, can, fdate, get, hidden, inp, layout, line_chart, need, pform, post, redirect, sel,
                    status_badge, tbl, to_float, to_int, user_link)
from web_users import kv

SECRET_WORDS = ("headers", "merchant", "_key")


def is_secret(key: str) -> bool:
    return FIELDS[key][1] == "secret" or any(w in key for w in SECRET_WORDS)


SRC_BADGE = {"auto": ("ok", NW_("🟢 خودکار (API)")), "manual": ("warn", NW_("🟡 دستی")),
             "stale": ("err", NW_("🔴 API پاسخ نمی‌دهد؛ نرخ دستی فعال است"))}


def ago(ts) -> str:
    if not ts:
        return "—"
    d = int(time.time() - ts)
    if d < 90:
        return _("{n} ثانیه پیش", n=d)
    if d < 5400:
        return _("{n} دقیقه پیش", n=d // 60)
    if d < 172800:
        return _("{n} ساعت پیش", n=d // 3600)
    return _("{n} روز پیش", n=d // 86400)


# ============================================================ محصولات
@get("/products", "products")
async def products_page(request):
    shop: Shop = request.app["shop"]
    pr = shop.pricing
    sections = ""
    for kind in ("stars", "premium", "gift"):
        prods = await shop.db.list_products(kind, only_active=False)
        cells = []
        for p in prods:
            price, cost = pr.quote(p)
            margin = pr.margin_for(p)
            inner = (f"<label class='mini'>{T('عنوان')}{inp('title', p['title'], '', 'l')}</label> "
                     f"<label class='mini'>{T('قیمت دستی')}{inp('price', p['price_override'] or '', _('خودکار'), 'm')}</label> "
                     f"<label class='mini'>{T('سود مخصوص ٪')}{inp('margin', '' if p['margin_override'] is None else p['margin_override'], _('پایه'), 'm')}</label> "
                     f"<label><input type='checkbox' name='active' {'checked' if p['active'] else ''}> {T('فعال')}</label> "
                     f"<button class='b sm pri'>{T('ذخیره')}</button>")
            profit = price - cost
            pct = f"{profit * 100 / cost:.1f}%" if cost else "—"
            eq = shop.equivalents(price)
            cells.append([p["id"], pform(request, f"/products/{p['id']}", inner, cls=""), f"<b>{fmt(price)}</b>" + (" ✏️" if p["price_override"] else "")
                          + f"<br><span class='hint'>{fmt(eq['rial'])} {T('ریال')} · " + (f"{eq['usdt']:,.2f} USDT" if eq["usdt"] is not None else "—")
                          + " · " + (f"{eq['ton']:,.3f} TON" if eq["ton"] is not None else "—") + "</span>",
                          fmt(cost), f"{fmt(profit)} <span class='hint'>({pct})</span>", f"{margin}%" + (" ★" if p["margin_override"] is not None else "")])
        extra = ""
        if kind == "stars":
            extra = pform(request, "/products/add", f"{inp('qty', '', _('تعداد ستاره پکیج جدید'), 'm', required=True)} <button class='b ok sm'>{T('➕ افزودن پکیج')}</button>", cls="")
        if kind == "gift":
            extra = pform(request, "/products/sync", f"<button class='b sm'>{T('🔄 همگام‌سازی گیفت‌ها از تلگرام')}</button>", cls="")
        sections += (f"<div class='card'><h2>{T(KIND_FA[kind])}</h2>"
                     f"{tbl([_('#'), _('ویرایش'), _('قیمت فروش (ت)'), _('هزینه‌ی تمام‌شده'), _('سود (ت)'), _('درصد سود')], cells)}<p>{extra}</p></div>")
    tools = pform(request, "/products/refresh", f"<button class='b'>{T('🔄 بروزرسانی قیمت‌ها از API')}</button>", cls="")
    hint = (f"<div class='card'><p class='hint'>{T('قیمت فروش = هزینه‌ی تمام‌شده × (۱ + درصد سود) + کارمزد ثابت، گردشده. هزینه‌ی تمام‌شده از نرخ تون/دلار (API) یا قیمت فراگمنت/تلگرام محاسبه می‌شود. کاربر فقط قیمت نهایی را می‌بیند؛ سود و هزینه فقط اینجا نمایش داده می‌شود.')}</p>"
            f"<p class='hint'>{T('قیمت دستی روی همان محصول اولویت دارد (خالی = خودکار). «سود مخصوص» فقط برای همان محصول جایگزین درصد سود پایه می‌شود (★).')}</p>"
            f"<p><a class='b' href='/rates'>💹 {T('نرخ‌ها و سود')}</a> {tools}</p></div>")
    return await layout(request, _("محصولات و قیمت"), hint + sections, "/products")


@post("/products/refresh", "products")
async def products_refresh(request):
    shop: Shop = request.app["shop"]
    await shop.pricing.refresh_rates()
    await shop.pricing.refresh_costs()
    raise redirect("/products", ok=_("قیمت‌ها بروزرسانی شد."))


@post("/products/add", "products")
async def product_add(request):
    shop: Shop = request.app["shop"]
    q = to_int((await request.post()).get("qty"))
    if not q or q < 1 or q > 1_000_000:
        raise redirect("/products", err=_("تعداد نامعتبر است."))
    ok = await shop.db.add_stars_pack(q)
    raise redirect("/products", **({"ok": _("پکیج اضافه شد.")} if ok else {"err": _("این پکیج از قبل وجود دارد.")}))


@post("/products/sync", "products")
async def product_sync(request):
    shop: Shop = request.app["shop"]
    try:
        n = await shop.sync_gifts()
    except Exception as ex:  # noqa: BLE001
        raise redirect("/products", err=_("خطا در همگام‌سازی: {err}", err=str(ex)[:120]))
    raise redirect("/products", ok=_("{n} گیفت همگام شد.", n=n))


@post("/products/{pid}", "products")
async def product_save(request):
    shop: Shop = request.app["shop"]
    pid = to_int(request.match_info["pid"])
    d = await request.post()
    p = await shop.db.get_product(pid)
    if not p:
        raise redirect("/products", err=_("محصول پیدا نشد."))
    title = str(d.get("title", "")).strip()[:60]
    if title:
        await shop.db.set_product_title(pid, title)
    price = to_int(d.get("price"))
    if str(d.get("price", "")).strip() and (price is None or price < 1000):
        raise redirect("/products", err=_("قیمت دستی باید عدد و حداقل ۱٬۰۰۰ تومان باشد."))
    margin_raw = str(d.get("margin", "")).strip()
    margin = to_float(margin_raw) if margin_raw else None
    if margin_raw and (margin is None or margin < 0 or margin > 1000):
        raise redirect("/products", err=_("درصد سود نامعتبر است."))
    await shop.db.set_price_override(pid, price)
    await shop.db.set_margin_override(pid, margin)
    if bool(p["active"]) != ("active" in d):
        await shop.db.toggle_product(pid)
    await shop.db.log(actor(request), "product_edit", f"{pid} price={price} margin={margin}")
    raise redirect("/products", ok=_("ذخیره شد."))


# ============================================================ نرخ‌ها و سود
@get("/rates", "pricing")
async def rates_page(request):
    shop: Shop = request.app["shop"]
    pr = shop.pricing
    s = await shop.settings()
    snap = pr.snapshot()
    cards = ""
    for key, title, icon in (("usd", _("دلار / تتر"), "💵"), ("ton", _("تون (TON)"), "💎")):
        v = snap[key]
        kind, lab = SRC_BADGE[v["source"]]
        hist = await shop.db.rate_history(key, 80)
        chart = line_chart([(h["ts"], h["value"]) for h in hist], _("روند نرخ"))
        err = f"<p class='hint' style='color:var(--err)'>⚠️ {e(v['err'])}</p>" if v["err"] else ""
        cards += (f"<div class='card'><h2>{icon} {e(title)}</h2><p style='font-size:26px;margin:4px 0'><b>{fmt(v['value'])}</b> <small>{T('تومان')}</small></p>"
                  f"<p>{badge(_(lab), kind)} <span class='hint'>{T('آخرین دریافت موفق')}: {ago(v['ts'])}</span></p>{err}{chart}"
                  + "<p>" + pform(request, "/test/" + key, "<button class='b sm'>🧪 " + T("تست API") + "</button>", cls="") + "</p></div>")
    quick_fields = ["rate_auto", "usdt_toman", "ton_toman", "margin_percent", "margin_stars", "margin_premium", "margin_gift", "fee_fixed", "round_step", "min_price"]
    inner = ""
    for k in quick_fields:
        label, typ, hlp = FIELDS[k]
        if typ == "bool":
            inner += f"<p><label><input type='checkbox' name='{k}' {'checked' if s.get(k) == '1' else ''}> {T(label)}</label></p>"
        else:
            inner += (f"<div><label class='f'>{T(label)}</label>{inp(k, s.get(k, ''), '', 'l')}"
                      + (f"<div class='hint'>{T(hlp)}</div>" if hlp else "") + "</div>")
    quick = (f"<div class='card'><h2>⚡ {T('تنظیم سریع نرخ و سود')}</h2>"
             + pform(request, "/rates/quick", f"<div class='grid g3'>{inner}</div><p><button class='b pri'>{T('ذخیره')}</button></p>", cls="") + "</div>")
    actions = ("<div class='card row'>" + pform(request, "/rates/refresh", "<button class='b ok'>🔄 " + T("بروزرسانی نرخ‌ها اکنون") + "</button>", cls="")
               + f"<a class='b' href='/rates/presets'>📋 {T('پیش‌تنظیم API ریالی')}</a><a class='b' href='/settings?g=rates'>⚙️ {T('API نرخ‌ها')}</a><a class='b' href='/settings?g=pricing'>⚙️ {T('قیمت‌گذاری و سود')}</a>"
               f"<a class='b' href='/settings?g=delivery'>🚚 {T('تحویل خودکار')}</a></div>")
    # جدول پیش‌نمایش قیمت
    rows = []
    for kind in ("stars", "premium", "gift"):
        for p in await shop.db.list_products(kind):
            price, cost = pr.quote(p)
            profit = price - cost
            eq = shop.equivalents(price)
            rows.append([e(_(KIND_FA[kind])), e(p["title"]), fmt(cost), f"{pr.margin_for(p)}%", f"<b>{fmt(price)}</b>", fmt(eq["rial"]),
                         f"{eq['usdt']:,.2f}" if eq["usdt"] is not None else "—", f"{eq['ton']:,.3f}" if eq["ton"] is not None else "—",
                         fmt(profit), f"{profit * 100 / cost:.1f}%" if cost else "—"])
    prev = (f"<div class='card'><h2>{T('پیش‌نمایش قیمت نهایی')}</h2>"
            f"{tbl([_('گروه'), _('محصول'), _('هزینه‌ی تمام‌شده'), _('سود'), _('قیمت فروش به کاربر'), _('ریال'), 'USDT', 'TON', _('سود (ت)'), _('درصد')], rows)}"
            f"<p class='hint'>{T('کاربر فقط «قیمت فروش» را می‌بیند.')}</p></div>")
    mode = (f"<div class='card'><h2>{T('وضعیت روش‌های محاسبه')}</h2>" + kv([
        (T("روش قیمت استارز"), f"<code>{e(s.get('price_mode_stars'))}</code>"), (T("روش قیمت پریمیوم"), f"<code>{e(s.get('price_mode_premium'))}</code>"),
        (T("بروزرسانی خودکار نرخ"), badge(_("روشن"), "ok") if s.get("rate_auto") == "1" else badge(_("خاموش"), "warn")),
        (T("فاصله‌ی بروزرسانی"), f"{s.get('rate_interval_sec', '120')} {T('ثانیه')}")]) + "</div>")
    return await layout(request, _("نرخ‌ها و سود"), actions + f"<div class='grid g2'>{cards}</div>" + quick + mode + prev, "/rates")


@post("/rates/refresh", "pricing")
async def rates_refresh(request):
    shop: Shop = request.app["shop"]
    await shop.pricing.refresh_rates()
    await shop.pricing.refresh_costs()
    raise redirect("/rates", ok=_("نرخ‌ها بروزرسانی شد."))


@post("/rates/quick", "pricing")
async def rates_quick(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    s = await shop.settings()
    errs, n = [], 0
    for k in ("rate_auto", "usdt_toman", "ton_toman", "margin_percent", "margin_stars", "margin_premium", "margin_gift", "fee_fixed", "round_step", "min_price"):
        raw = ("1" if k in d else "0") if FIELDS[k][1] == "bool" else str(d.get(k, ""))
        ok, res = await _validated(k, raw)
        if not ok:
            errs.append(f"{_(FIELDS[k][0])}: {_(res)}")
        elif res != s.get(k, ""):
            await shop.set_setting(k, res, actor=actor(request))
            n += 1
    if errs:
        raise redirect("/rates", err=_("؛ ").join(errs))
    raise redirect("/rates", ok=_("ذخیره شد ({n} تغییر).", n=n))


async def _validated(key, raw):
    from settings_schema import validate
    return validate(key, raw)


# ============================================================ ستاره و تحویل خودکار
@get("/stars", "stars")
async def stars_page(request):
    shop: Shop = request.app["shop"]
    s = await shop.settings()
    try:
        bal = await shop.star_balance()
    except Exception:  # noqa: BLE001
        bal = None
    if s.get("prov_balance_url"):
        await shop._check_provider_balance(force=True)
    prov_on = {k: provider.provider_enabled(s, k) for k in ("stars", "premium")}
    mode_lab = {"manual": _("دستی (صف ادمین)"), "api": _("خودکار با API سرویس‌دهنده"), "bot": _("خودکار از موجودی ستاره ربات")}
    st = kv([
        (T("موجودی ستاره ربات"), f"<b>{bal if bal is not None else '—'}</b> ⭐"),
        (T("موجودی سرویس‌دهنده"), e(provider.fmt_balance(shop.provider_balance)) if s.get("prov_balance_url") else "—"),
        (T("تحویل استارز"), e(mode_lab.get(s.get("deliver_stars_mode"), "—")) + (" " + badge(_("آماده"), "ok") if prov_on["stars"] else "")),
        (T("تحویل پریمیوم"), e(mode_lab.get(s.get("deliver_premium_mode"), "—"))),
        (T("هشدار کمبود"), f"{s.get('star_alert_threshold', '0')} ⭐ / {s.get('prov_balance_low', '0')}")])
    note = (f"<p class='hint'>{T('توجه: Bot API رسمی تلگرام امکان «ارسال استارز به کاربر» ندارد. تحویل خودکار استارز با API یک سرویس‌دهنده (که در «تنظیمات ← تحویل خودکار» وارد می‌کنید) انجام می‌شود؛ پریمیوم و گیفت از موجودی ستاره خود ربات هم قابل ارسال است.')}</p>")
    topup = pform(request, "/stars/invoice", f"{inp('n', '', _('تعداد ستاره'), 'm', required=True)} <button class='b ok'>{T('ساخت لینک پرداخت')}</button>", cls="")
    topup_card = (f"<div class='card'><h2>➕ {T('شارژ موجودی ستاره ربات')}</h2><p class='hint'>{T('لینک پرداخت با ستاره ساخته می‌شود؛ باید با اکانت تلگرام یکی از ادمین‌ها پرداخت شود.')}</p>{topup}"
                  + (f"<p>{T('لینک')}: <a href='{e(request.query.get('link'))}' target='_blank'>{e(request.query.get('link'))}</a></p>" if request.query.get("link", "").startswith("https://t.me/") else "") + "</div>")
    queue = await shop.db.queue_orders(50)
    q_cells = [[o["id"], user_link(o["user_id"]), e(o["title"]), fmt(o["amount"]), status_badge(o["status"]),
                f"<span class='hint'>{e((o.get('note') or '')[:90])}</span>"] for o in queue]
    q_card = (f"<div class='card'><h2>🧾 {T('صف تحویل / خطا')}</h2>{tbl([_('#'), _('کاربر'), _('محصول'), _('مبلغ'), _('وضعیت'), _('یادداشت')], q_cells)}"
              + pform(request, "/stars/retry", f"<button class='b'>{T('🔁 تلاش مجدد همه (به‌جز «نتیجه نامشخص»)')}</button>", cls="") + "</div>")
    logs = await shop.db.all("SELECT * FROM audit_log WHERE actor='provider' ORDER BY id DESC LIMIT 15")
    l_card = f"<div class='card'><h2>{T('آخرین فراخوانی‌های سرویس‌دهنده')}</h2>{tbl([_('زمان'), _('نتیجه'), _('جزئیات')], [[fdate(x['ts']), e(x['action']), e(x['detail'])] for x in logs])}</div>"
    links = f"<p><a class='b' href='/settings?g=delivery'>⚙️ {T('تنظیمات تحویل خودکار')}</a></p>"
    return await layout(request, _("ستاره و تحویل خودکار"), f"<div class='card'><h2>⭐ {T('وضعیت')}</h2>{st}{note}{links}</div>{topup_card}{q_card}{l_card}", "/stars")


@post("/stars/invoice", "stars")
async def stars_invoice(request):
    shop: Shop = request.app["shop"]
    n = to_int((await request.post()).get("n"))
    if not n or not 1 <= n <= 100_000:
        raise redirect("/stars", err=_("تعداد باید بین ۱ تا ۱۰۰٬۰۰۰ باشد."))
    try:
        link = await shop.bot.create_invoice_link(title="Bot stars top-up", description=f"{n} Stars", payload="botstars", currency="XTR",
                                                  prices=[LabeledPrice(label="Stars", amount=n)])
    except Exception as ex:  # noqa: BLE001
        raise redirect("/stars", err=str(ex)[:160])
    await shop.db.log(actor(request), "stars_invoice", str(n))
    from urllib.parse import quote
    raise web.HTTPSeeOther(f"/stars?link={quote(link, safe='')}&ok={quote(_('لینک پرداخت ساخته شد.'))}")


@post("/stars/retry", "stars")
async def stars_retry(request):
    shop: Shop = request.app["shop"]
    n = await shop.retry_queue(include_failed=True)
    raise redirect("/stars", ok=_("{n} سفارش دوباره تلاش شد.", n=n))


# ============================================================ تست API
def result_card(title, r, extra=""):
    if r["ok"]:
        head = f"<div class='flash ok'>✅ {e(title)}: {T('موفق')} — {T('مقدار خوانده‌شده')}: <code>{e(r['value'])}</code></div>"
    else:
        head = f"<div class='flash err'>❌ {e(title)}: {T('ناموفق')} — {e(r['error'] or _('مقدار در مسیر JSON پیدا نشد'))}</div>"
    prev = f"<h3>{T('پاسخ API (خلاصه)')}</h3><pre class='pv'>{e(r['preview'])}</pre>" if r["preview"] else ""
    return f"<div class='card'>{head}{extra}{prev}</div>"


@post("/test/{what}", "pricing")
async def api_test(request):
    shop: Shop = request.app["shop"]
    what = request.match_info["what"]
    s = await shop.settings()
    pr = shop.pricing
    back = {"usd": "rates", "ton": "rates", "fs": "pricing", "fp": "pricing", "pb": "delivery", "ds": "delivery", "dp": "delivery"}.get(what, "rates")
    if what in ("usd", "ton"):
        if not s.get(f"{what}_api_url"):
            raise redirect(f"/settings?g={back}", err=_("آدرس API تنظیم نشده است."))
        r = await pr.probe(s[f"{what}_api_url"], s.get(f"{what}_api_method", "GET"), s.get(f"{what}_api_headers", ""), s.get(f"{what}_api_body", ""), s.get(f"{what}_api_path", ""))
        title = _("تست API دلار") if what == "usd" else _("تست API تون")
        extra = ""
        if r["ok"]:
            raw = D(r["value"])
            tom = pr._api_to_toman(raw, s.get(f"{what}_api_unit", "toman") if what == "ton" else "toman")
            if what == "usd" and s.get("usd_api_unit") == "rial":
                tom = raw / 10
            extra = f"<p>{T('معادل تومان')}: <b>{fmt(tom)}</b></p>"
        body = result_card(title, r, extra)
    elif what in ("fs", "fp"):
        kind = "stars" if what == "fs" else "premium"
        p = f"frag_{kind}_"
        if not s.get(p + "url"):
            raise redirect(f"/settings?g={back}", err=_("آدرس API تنظیم نشده است."))
        qty = 50 if kind == "stars" else 3
        vars_ = {"qty": qty} if kind == "stars" else {"months": qty, "qty": qty}
        r = await pr.probe(s[p + "url"], s.get(p + "method", "GET"), s.get(p + "headers", ""), s.get(p + "body", ""), s.get(p + "path", ""), vars_)
        title = _("تست API استارز (۵۰ عدد)") if kind == "stars" else _("تست API پریمیوم (۳ ماه)")
        extra = ""
        if r["ok"]:
            unit = s.get(p + "unit", "ton")
            tom = pr._api_to_toman(D(r["value"]), unit)
            extra = f"<p>{T('معادل تومان با نرخ‌های فعلی')}: <b>{fmt(tom)}</b> ({e(unit)})</p>"
        body = result_card(title, r, extra)
    elif what == "pb":
        if not s.get("prov_balance_url"):
            raise redirect(f"/settings?g={back}", err=_("آدرس API تنظیم نشده است."))
        r = await pr.probe(s["prov_balance_url"], "GET", s.get("prov_balance_headers", ""), None, s.get("prov_balance_path", ""))
        body = result_card(_("تست API موجودی سرویس‌دهنده"), r)
    elif what in ("ds", "dp"):
        kind = "stars" if what == "ds" else "premium"
        p = f"prov_{kind}_"
        vars_ = dict(SAMPLE_VARS)
        txt = (f"{s.get(p + 'method', 'POST')} {render_template(s.get(p + 'url', ''), vars_)}\n\nHeaders: "
               f"{'(set)' if s.get(p + 'headers') else '-'}\n\nBody:\n{render_template(s.get(p + 'body', ''), vars_)}")
        body = (f"<div class='card'><div class='flash warn'>{T('این فقط پیش‌نمایش درخواست با داده‌ی نمونه است؛ هیچ درخواستی ارسال نشد (برای جلوگیری از تحویل واقعی).')}</div>"
                f"<pre class='pv'>{e(txt)}</pre></div>")
    else:
        raise web.HTTPNotFound()
    await shop.db.log(actor(request), "api_test", what)
    return await layout(request, _("نتیجه‌ی تست API"), body + f"<p><a class='b' href='/settings?g={back}'>← {T('بازگشت به تنظیمات')}</a></p>", "/settings")


# ============================================================ تنظیمات
def allowed_groups(request):
    return [g for g in GROUPS if can(request, g[2])]


def field_html(shop: Shop, request, key, label, typ, hlp, val):
    h = f"<div class='hint'>{T(hlp)}</div>" if hlp else ""
    lab = T(label)
    if typ == "bool":
        return f"<p><label><input type='checkbox' name='{key}' {'checked' if val == '1' else ''}> {lab}</label>{h}</p>"
    if key in ("default_lang", "web_default_lang"):
        field = "web_enabled" if key == "web_default_lang" else "bot_enabled"
        opts = [(l["code"], f"{l['name']} ({l['code']})") for l in shop.langs if l[field]]
        return f"<div><label class='f'>{lab}</label>{sel(key, opts, val)}{h}</div>"
    if typ.startswith("select:"):
        return f"<div><label class='f'>{lab}</label>{sel(key, [(o, o) for o in select_options(typ)], val)}{h}</div>"
    if typ == "textarea" or typ == "json":
        mono = " class='mono'" if typ == "json" else ""
        if is_secret(key) and val:
            return (f"<div><label class='f'>{lab} <span class='hint'>({T('تنظیم شده؛ برای تغییر مقدار جدید را بنویسید')})</span></label>"
                    f"<textarea name='{key}'{mono} placeholder='●●●●'></textarea><label class='hint'><input type='checkbox' name='clear_{key}'> {T('پاک کردن مقدار')}</label>{h}</div>")
        return f"<div><label class='f'>{lab}</label><textarea name='{key}'{mono} style='min-height:70px'>{e(val)}</textarea>{h}</div>"
    if is_secret(key):
        st = f" <span class='hint'>({T('تنظیم شده؛ برای تغییر مقدار جدید را بنویسید')})</span>" if val else ""
        clr = f"<label class='hint'><input type='checkbox' name='clear_{key}'> {T('پاک کردن مقدار')}</label>" if val else ""
        return f"<div><label class='f'>{lab}{st}</label><input class='w' type='password' name='{key}' value='' placeholder='{'●●●●' if val else ''}' autocomplete='new-password'>{clr}{h}</div>"
    return f"<div><label class='f'>{lab}</label><input class='w' name='{key}' value='{e(val)}'>{h}</div>"


def group_tools(request, gid):
    tools = {"rates": [("usd", _("تست API دلار")), ("ton", _("تست API تون"))],
             "pricing": [("fs", _("تست API استارز")), ("fp", _("تست API پریمیوم"))],
             "delivery": [("pb", _("تست API موجودی")), ("ds", _("پیش‌نمایش درخواست استارز")), ("dp", _("پیش‌نمایش درخواست پریمیوم"))]}.get(gid, [])
    out = "".join(pform(request, f"/test/{w}", f"<button class='b sm'>🧪 {e(lab)}</button>") for w, lab in tools)
    if gid == "group":
        acts = [("topics", _("🗂 ساخت/بازسازی تاپیک‌ها")), ("report", _("📊 ارسال گزارش همین حالا")), ("backup", _("💾 ارسال بکاپ همین حالا")),
                ("test", _("🧪 پیام آزمایشی")), ("off", _("🔌 قطع اتصال"))]
        out = "".join(pform(request, f"/group/{a}", f"<button class='b sm'>{e(lab)}</button>") for a, lab in acts)
    return out


@get("/settings")
async def settings_page(request):
    shop: Shop = request.app["shop"]
    groups = allowed_groups(request)
    if not groups:
        raise redirect("/", err=_("شما به این بخش دسترسی ندارید."))
    gid = request.query.get("g", groups[0][0])
    if gid not in GROUP_IDS or not can(request, GROUP_IDS[gid][2]):
        gid = groups[0][0]
    s = await shop.settings()
    tabs = "<div class='tabs'>" + "".join(f"<a class='{'on' if g[0] == gid else ''}' href='/settings?g={g[0]}'>{T(g[1])}</a>" for g in groups) + "</div>"
    g = GROUP_IDS[gid]
    inner = "".join(field_html(shop, request, k, label, typ, hlp, s.get(k, "")) for k, label, typ, hlp in g[3])
    extra = ""
    if gid == "group":
        grp = shop.group
        extra = (f"<div class='card'><h2>{T('وضعیت اتصال')}</h2>"
                 + kv([(T("وضعیت"), badge(_("فعال"), "ok") if grp.enabled() else badge(_("غیرفعال"), "warn")),
                       (T("گروه متصل"), f"<code>{e(grp.chat_id() or '—')}</code>"),
                       (T("تاپیک‌ها"), f"{len(grp.topics())}/{len(GROUP_TOPICS)}")])
                 + f"<h3>{T('راهنمای اتصال')}</h3><ol class='hint'><li>{T('یک گروه (بهتر: سوپرگروه با Topics روشن و خصوصی) بسازید.')}</li>"
                   f"<li>{T('ربات را به گروه اضافه و ادمین کنید (Manage Topics و Post Messages).')}</li>"
                   f"<li>{T('داخل گروه دستور /connect را بفرستید (فقط ادمین‌های ربات). ربات تاپیک‌ها را خودش می‌سازد.')}</li></ol>"
                 + f"<p class='hint'>⚠️ {T('فایل بکاپ شامل اطلاعات کاربران است؛ فقط ادمین‌های مورد اعتماد عضو گروه باشند.')}</p></div>")
    if gid == "gateways":
        c = shop.cfg
        env_rows = [(_("آدرس عمومی (PUBLIC_URL)"), c.public_url), (_("زرین‌پال (.env)"), c.zarinpal_merchant), (_("کیف پول TON (.env)"), c.ton_wallet), (_("کیف پول USDT (.env)"), c.usdt_trc20_wallet)]
        extra = (f"<div class='card'><h2>{T('وضعیت اتصال‌ها')}</h2>"
                 + kv([(e(k), badge(_("تنظیم شده"), "ok") if v else badge(_("تنظیم نشده"), "warn")) for k, v in env_rows])
                 + f"<p class='hint'>{T('مقادیر بالا از فایل .env قدیمی هستند؛ اگر در همین صفحه مقدار وارد کنید همان اولویت دارد.')}</p></div>")
    tools = group_tools(request, gid)
    form = pform(request, f"/settings/{gid}", f"<div class='grid g2'>{inner}</div><p><button class='b pri'>💾 {T('ذخیره')}</button></p>", cls="")
    body = (tabs + f"<div class='card'><h2>{T(g[1])}</h2>{form}"
            + (f"<h3>{T('ابزار آزمایش')}</h3><div class='row'>{tools}</div><p class='hint'>{T('ابتدا تغییرات را ذخیره کنید، سپس تست بزنید.')}</p>" if tools else "") + "</div>" + extra)
    return await layout(request, _("تنظیمات"), body, "/settings")


@post("/settings/{gid}")
async def settings_save(request):
    shop: Shop = request.app["shop"]
    gid = request.match_info["gid"]
    if gid not in GROUP_IDS:
        raise web.HTTPNotFound()
    need(request, GROUP_IDS[gid][2])
    d = await request.post()
    s = await shop.settings()
    errs, changed = [], 0
    for key, label, typ, _h in GROUP_IDS[gid][3]:
        if typ == "bool":
            raw = "1" if key in d else "0"
        else:
            raw = str(d.get(key, ""))
            if is_secret(key):
                if f"clear_{key}" in d:
                    raw = ""
                elif not raw.strip():
                    continue            # خالی = بدون تغییر
        ok, res = await shop.set_setting_validated(key, raw, actor(request)) if raw != s.get(key, "") else (True, raw)
        if not ok:
            errs.append(f"{_(label)}: {_(res)}")
        elif raw != s.get(key, ""):
            changed += 1
    if errs:
        raise redirect(f"/settings?g={gid}", err=_("برخی مقادیر ذخیره نشد") + " — " + _("؛ ").join(errs))
    raise redirect(f"/settings?g={gid}", ok=_("ذخیره شد ({n} تغییر).", n=changed))
