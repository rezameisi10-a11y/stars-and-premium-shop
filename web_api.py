"""پنل وب: مدیریت Marketapp (قیمت و خرید از Fragment) و پیش‌تنظیم API نرخ ریالی تتر/تون (بیت‌پین، نوبیتکس، ...)."""
from decimal import Decimal

import marketapp
import tonwallet
from i18n import NW_, _
from rate_presets import PRESETS, preset_settings
from service import Shop
from ui import esc as e
from ui import fmt
from web_users import kv
from webkit import T, actor, badge, get, hidden, inp, layout, pform, post, redirect, sel, tbl, to_int

MODE_KEYS = [("price_mode_stars", "marketapp", "manual", NW_("قیمت استارز از Marketapp")),
             ("price_mode_premium", "marketapp", "stars", NW_("قیمت پریمیوم از Marketapp")),
             ("deliver_stars_mode", "marketapp", "manual", NW_("تحویل خودکار استارز با Marketapp")),
             ("deliver_premium_mode", "marketapp", "bot", NW_("تحویل خودکار پریمیوم با Marketapp"))]


def result_box(ok, title, body=""):
    return f"<div class='card'><div class='flash {'ok' if ok else 'err'}'>{'✅' if ok else '❌'} {e(title)}</div>{body}</div>"


@get("/marketapp", "pricing")
async def marketapp_page(request):
    shop: Shop = request.app["shop"]
    s = await shop.settings()
    addr = "—"
    if tonwallet.AVAILABLE and marketapp.wallet_configured(s):
        try:
            addr = tonwallet.TonWallet(s["mapp_wallet_mnemonic"], s.get("mapp_wallet_version", "v5r1")).address_str()
        except tonwallet.WalletError as ex:
            addr = f"⚠️ {ex}"
    on = lambda b: badge(_("بله"), "ok") if b else badge(_("خیر"), "warn")  # noqa: E731
    st = kv([(T("توکن API"), on(marketapp.configured(s))), (T("کیف پول TON"), on(marketapp.wallet_configured(s))),
             (T("کتابخانه pytoniq-core"), on(tonwallet.AVAILABLE)), (T("آدرس کیف پول مشتق‌شده"), f"<code>{e(addr)}</code>"),
             (T("ارز خرید"), e(s.get("mapp_currency", ""))), (T("سقف ایمنی هر تراکنش"), f"{e(s.get('mapp_max_ton', '0'))} TON")])
    sw = ""
    for key, on_v, _off, label in MODE_KEYS:
        sw += f"<p><label><input type='checkbox' name='{key}' {'checked' if s.get(key) == on_v else ''}> {T(label)}</label></p>"
    sw_card = (f"<div class='card'><h2>🔀 {T('استفاده از Marketapp')}</h2>"
               + pform(request, "/marketapp/enable", sw + f"<button class='b pri'>{T('ذخیره')}</button>", cls="")
               + f"<p><a class='b' href='/settings?g=marketapp'>⚙️ {T('تنظیمات Marketapp')}</a></p></div>")
    rows = []
    for (kind, qty), c in sorted(shop.pricing.costs.items()):
        if c.get("usd") is not None:
            rows.append([e(kind), qty, e(str(c.get("usd"))), e(str(c.get("ton"))), fmt(c.get("cost", 0)), "✅" if c.get("ok") else "⚠️"])
    q_card = f"<div class='card'><h2>{T('آخرین قیمت‌های دریافتی')}</h2>{tbl([_('نوع'), _('تعداد/ماه'), 'USD', 'TON', _('هزینه (تومان)'), ''], rows)}</div>"
    tools = (f"<div class='card'><h2>🧪 {T('آزمایش (بدون هزینه)')}</h2><div class='row'>"
             + pform(request, "/marketapp/test/price", f"{inp('qty', '50', _('تعداد ستاره'), 'm')} <button class='b'>{T('تست قیمت')}</button>")
             + pform(request, "/marketapp/test/wallet", f"<button class='b'>{T('تست کیف پول')}</button>") + "</div>"
             + f"<h3>{T('شبیه‌سازی خرید (فقط تراکنش گرفته می‌شود، امضا و ارسال نمی‌شود)')}</h3>"
             + pform(request, "/marketapp/test/dry", f"{sel('kind', [('stars', 'stars'), ('premium', 'premium')])} {inp('username', '', 'username', 'm', required=True)} "
                     f"{inp('qty', '50', _('تعداد/ماه'), 'm')} <button class='b'>{T('شبیه‌سازی')}</button>") + "</div>")
    note = f"<div class='card'><p class='hint'>{T('خرید خودکار تراکنش TON را با عبارت بازیابی کیف پول داغ شما امضا و ارسال می‌کند. فقط یک کیف پول جدا با موجودی کم استفاده کنید و بکاپ‌ها را خصوصی نگه دارید.')}</p></div>"
    return await layout(request, "Marketapp", f"<div class='card'><h2>{T('وضعیت')}</h2>{st}</div>{sw_card}{tools}{q_card}{note}", "/marketapp")


@post("/marketapp/enable", "pricing")
async def marketapp_enable(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    for key, on_v, off_v, _l in MODE_KEYS:
        want = on_v if key in d else (off_v if (await shop.settings()).get(key) == on_v else None)
        if want:
            await shop.set_setting(key, want, actor=actor(request))
    raise redirect("/marketapp", ok=_("ذخیره شد."))


@post("/marketapp/test/{what}", "pricing")
async def marketapp_test(request):
    shop: Shop = request.app["shop"]
    what = request.match_info["what"]
    s = await shop.settings()
    d = await request.post()
    qty = to_int(d.get("qty"), 50) or 50
    if not marketapp.configured(s):
        raise redirect("/marketapp", err=_("توکن API تنظیم نشده است."))
    if what == "price":
        r = await marketapp.test_all(shop, s, qty)
        body = ""
        for k in ("stars", "premium"):
            x = r[k]
            if x["ok"]:
                data = x["data"] if k == "premium" else {qty: x["data"]}
                body += result_box(True, k, "<pre class='pv'>" + e("\n".join(f"{a}: {', '.join(f'{f}={v}' for f, v in b.items())}" for a, b in data.items())) + "</pre>")
            else:
                body += result_box(False, k, f"<pre class='pv'>{e(x['error'])}</pre>")
    elif what == "wallet":
        try:
            i = await tonwallet.get_info(shop, s)
            m = {True: _("مطابق"), False: _("نامطابق ⚠️"), None: "—"}[i["matches"]]
            body = result_box(True, _("کیف پول"), kv([(T("آدرس"), f"<code>{e(i['address'])}</code>"), (T("نسخه"), e(i["version"])),
                                                   (T("موجودی"), f"{Decimal(i['balance']) / tonwallet.NANO:.4f} TON"), ("seqno", i["seqno"]),
                                                   (T("وضعیت"), e(i["state"])), (T("تطبیق با آدرس تنظیم‌شده"), m)]))
        except Exception as ex:  # noqa: BLE001
            body = result_box(False, _("کیف پول"), f"<pre class='pv'>{e(str(ex)[:300])}</pre>")
    elif what == "dry":
        kind = "premium" if d.get("kind") == "premium" else "stars"
        user = str(d.get("username", "")).strip()
        r = await marketapp.dry_run(shop, s, kind, user, qty)
        lines = [f"{k}: {v}" for k, v in r.items()]
        body = result_box(bool(r.get("ok")), _("شبیه‌سازی خرید"), "<pre class='pv'>" + e("\n".join(lines)) + "</pre>")
    else:
        raise redirect("/marketapp")
    await shop.db.log(actor(request), "marketapp_test", what)
    return await layout(request, "Marketapp", body + f"<p><a class='b' href='/marketapp'>← {T('بازگشت')}</a></p>", "/marketapp")


@get("/rates/presets", "pricing")
async def presets_page(request):
    cards = ""
    for pid, (lab, key, url, method, body, path, unit) in PRESETS.items():
        cards += (f"<div class='card'><h3>{e(_(lab) if lab[0] != lab[0].upper() else lab)}</h3>"
                  f"<p class='hint'>{e(method)} {e(url)}<br>path: <code>{e(path)}</code> · {T('واحد')}: {e(unit)} · → {T('نرخ')} {'USDT' if key == 'usd' else 'TON'}</p>"
                  + pform(request, f"/rates/preset/{pid}", f"<button class='b pri'>{T('اعمال')}</button>", cls="") + "</div>")
    note = f"<div class='card'><p class='hint'>{T('این آدرس‌ها پیشنهادی‌اند و ممکن است تغییر کنند. بعد از اعمال، در «نرخ‌ها و سود» دکمه‌ی «تست API» را بزنید؛ برای سرویس دیگر، همه‌ی فیلدها را در تنظیمات ← نرخ لحظه‌ای دستی وارد کنید.')}</p></div>"
    return await layout(request, _("پیش‌تنظیم API نرخ ریالی"), note + f"<div class='grid g2'>{cards}</div>", "/rates")


@post("/rates/preset/{pid}", "pricing")
async def preset_apply(request):
    shop: Shop = request.app["shop"]
    pid = request.match_info["pid"]
    if pid not in PRESETS:
        raise redirect("/rates/presets", err=_("پیش‌تنظیم پیدا نشد."))
    for k, v in preset_settings(pid).items():
        await shop.set_setting(k, v, actor=actor(request))
    await shop.pricing.refresh_rates()
    raise redirect("/rates", ok=_("اعمال شد؛ برای اطمینان «تست API» را بزنید."))
