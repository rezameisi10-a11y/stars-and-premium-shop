"""پنل وب: کدهای تخفیف پیشرفته، پیام همگانی، چیدمان/رنگ/ایموجی پریمیوم دکمه‌ها."""
import re
import secrets
import time

from aiohttp import web

import ui
from i18n import N_, _
from service import Shop, normalize_url
from ui import ROLE_LABELS, STYLE_LABELS, VALID_STYLES
from ui import esc as e
from ui import fmt
from web_users import FILTER_LABELS, kv
from webkit import (T, actor, badge, can, fdate, get, hidden, inp, layout, pform, post, redirect, sel, tbl, to_int)

KIND_NAMES = {"stars": N_("استارز"), "premium": N_("پریمیوم"), "gift": N_("گیفت"), "vpn": N_("VPN")}
UNITS = [("m", N_("دقیقه")), ("h", N_("ساعت")), ("d", N_("روز"))]
UNIT_SEC = {"m": 60, "h": 3600, "d": 86400}


def dur_sec(num, unit) -> int:
    n = to_int(num, 0) or 0
    return max(0, n) * UNIT_SEC.get(unit, 86400)


def left_text(ts) -> str:
    if not ts:
        return _("بدون انقضا")
    d = int(ts - time.time())
    if d <= 0:
        return _("منقضی شده")
    days, rem = divmod(d, 86400)
    hours, rem = divmod(rem, 3600)
    parts = []
    if days:
        parts.append(_("{n} روز", n=days))
    if hours:
        parts.append(_("{n} ساعت", n=hours))
    if not days:
        parts.append(_("{n} دقیقه", n=rem // 60))
    return " ".join(parts) + " " + _("مانده")


def applies_label(v: str) -> str:
    return _("همه") if v in ("", "all") else _("، ").join(_(KIND_NAMES.get(k, k)) for k in v.split(","))


def applies_checks(cur="all"):
    cur_set = set(cur.split(",")) if cur not in ("", "all") else set(KIND_NAMES)
    return " ".join(f"<label><input type='checkbox' name='ap_{k}' {'checked' if k in cur_set else ''}> {T(v)}</label>" for k, v in KIND_NAMES.items())


def read_applies(d) -> str:
    ks = [k for k in KIND_NAMES if f"ap_{k}" in d]
    return "all" if len(ks) in (0, len(KIND_NAMES)) else ",".join(ks)


async def parse_users(shop: Shop, text: str):
    ids, bad = [], []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        if tok.lstrip("-").isdigit():
            u = await shop.db.get_user(int(tok))
        else:
            u = await shop.db.get_user_by_username(tok)
        (ids if u else bad).append(u["id"] if u else tok)
    return ids, bad


def build_params(d, *, now=None):
    """پارامترهای مشترک ساخت کد از فرم؛ خطا → (None, پیام)."""
    now = now or int(time.time())
    kind = d.get("kind")
    val = to_int(d.get("val"))
    if kind not in ("p", "f") or not val or val < 1 or (kind == "p" and val > 100) or (kind == "f" and val < 1000):
        return None, _("مقدار تخفیف نامعتبر است (درصد ۱ تا ۱۰۰، یا مبلغ ثابت حداقل ۱٬۰۰۰ تومان).")
    start = now + dur_sec(d.get("start_n"), d.get("start_u")) if to_int(d.get("start_n"), 0) else 0
    dur = dur_sec(d.get("dur_n"), d.get("dur_u"))
    exp = (start or now) + dur if dur else 0
    mx, pu, mn, cap = (to_int(d.get(k), 0) or 0 for k in ("max_uses", "per_user", "min_amount", "cap"))
    if min(mx, pu, mn, cap) < 0:
        return None, _("مقادیر نباید منفی باشند.")
    return dict(percent=val if kind == "p" else 0, fixed=val if kind == "f" else 0, max_uses=mx, expires_at=exp, starts_at=start,
                per_user=pu, min_amount=mn, max_discount=cap if kind == "p" else 0, applies_to=read_applies(d),
                first_only=1 if "first_only" in d else 0, note=str(d.get("note", "")).strip()[:200],
                base="profit" if d.get("base") == "profit" else "price"), None


def form_fields(extra_adv=""):
    """فرم ساده: ۴ سؤال اصلی (مقدار، روز، ظرفیت کل، سقف هر کاربر)؛ بقیه‌ی گزینه‌ها داخل «گزینه‌های بیشتر»."""
    simple = (f"<div class='grid g2'>"
              f"<div><label class='f'>1) {T('چقدر تخفیف بدهید؟')}</label>"
              f"<div class='row'>{sel('kind', [('p', _('درصدی (٪)')), ('f', _('مبلغ ثابت (تومان)'))])} {inp('val', '', _('مثلاً ۲۰'), 'm', required=True)}</div>"
              f"<span class='hint'>{T('درصدی: یک عدد از ۱ تا ۱۰۰. مبلغ ثابت: حداقل ۱٬۰۰۰ تومان.')}</span></div>"
              f"<div><label class='f'>{T('تخفیف روی چه چیزی؟')}</label>{sel('base', [('price', _('کل قیمت محصول')), ('profit', _('فقط سود من'))])}"
              f"<br><span class='hint'>{T('«فقط سود»: درصد از سود محصول (قیمت − هزینه) حساب می‌شود و قیمت هرگز زیر هزینه نمی‌آید.')}</span></div>"
              f"<div><label class='f'>2) {T('چند روز معتبر باشد؟')}</label>{inp('dur_n', '7', '', 'm', typ='number', extra='min=0')}"
              f"<input type='hidden' name='dur_u' value='d'><br><span class='hint'>{T('عدد صفر یعنی بدون تاریخ انقضا')}</span></div>"
              f"<div><label class='f'>3) {T('در مجموع چند نفر بتوانند استفاده کنند؟')}</label>{inp('max_uses', '0', '', 'm', typ='number', extra='min=0')}"
              f"<br><span class='hint'>{T('عدد صفر یعنی نامحدود')}</span></div>"
              f"<div><label class='f'>4) {T('هر کاربر چند بار؟')}</label>{inp('per_user', '1', '', 'm', typ='number', extra='min=0')}"
              f"<br><span class='hint'>{T('عدد صفر یعنی نامحدود')}</span></div></div>")
    adv = (f"<details style='margin-top:12px'><summary style='cursor:pointer;color:var(--acc)'>⚙️ {T('گزینه‌های بیشتر (اختیاری)')}</summary>"
           f"<div class='grid g3' style='margin-top:10px'>{extra_adv}"
           f"<div><label class='f'>{T('شروع بعد از (۰ = همین الان)')}</label>{inp('start_n', '0', '', 's')} {sel('start_u', [(k, _(v)) for k, v in UNITS], 'h')}</div>"
           f"<div><label class='f'>{T('حداقل مبلغ سفارش (تومان)')}</label>{inp('min_amount', '0', '', 'm')}</div>"
           f"<div><label class='f'>{T('سقف مبلغ تخفیف برای درصدی (تومان؛ ۰ = بدون سقف)')}</label>{inp('cap', '0', '', 'm')}</div>"
           f"<div><label class='f'>{T('محصولات مجاز')}</label>{applies_checks()}</div>"
           f"<div><label class='f'>{T('یادداشت داخلی')}</label>{inp('note', '', '', 'l')}</div></div>"
           f"<p><label><input type='checkbox' name='first_only'> {T('فقط برای اولین خرید کاربر')}</label></p></details>")
    return simple + adv


# ============================================================ لیست و ساخت
@get("/discounts", "discounts")
async def discounts_page(request):
    shop: Shop = request.app["shop"]
    rows = await shop.db.list_discounts()
    cells = []
    for d in rows:
        val = f"{d['percent']}%" + (f" ({T('سقف')} {fmt(d['max_discount'])})" if d["max_discount"] else "") if d["percent"] else f"{fmt(d['fixed'])} {T('ت')}"
        expired = bool(d["expires_at"] and d["expires_at"] < time.time())
        st = badge(_("منقضی"), "err") if expired else (badge(_("فعال"), "ok") if d["active"] else badge(_("غیرفعال"), "err"))
        cells.append([f"<a href='/discounts/{e(d['code'])}'><code>{e(d['code'])}</code></a>", val, f"{d['used']}/{d['max_uses'] or '∞'}", d["per_user"] or "∞",
                      e(left_text(d["expires_at"])), fmt(d["min_amount"]) if d["min_amount"] else "—", e(applies_label(d["applies_to"])), st,
                      f"<a class='b sm' href='/discounts/{e(d['code'])}'>{T('جزئیات')}</a>"])
    create = (f"<div class='card'><h2>➕ {T('ساخت کد تخفیف')}</h2>"
              + pform(request, "/discounts/add",
                      form_fields(f"<div><label class='f'>{T('نام کد (خالی = خودکار)')}</label>{inp('code', '', 'WELCOME20', 'm')}</div>"
                                  f"<div><label class='f'>{T('محدود به کاربران خاص (آیدی یا یوزرنیم؛ خالی = همه)')}</label><textarea name='users' style='min-height:50px'></textarea></div>")
                      + f"<p><button class='b ok'>{T('ساخت')}</button></p>", cls="") + "</div>")
    bulk = (f"<div class='card'><h2>🎲 {T('ساخت گروهی کد')}</h2><p class='hint'>{T('چند کد یکتا با پیشوند دلخواه و همین تنظیمات ساخته می‌شود (مثلاً برای کمپین یا تقسیم بین کاربران). پیشنهاد: تعداد استفاده‌ی کل = ۱.')}</p>"
            + pform(request, "/discounts/bulk", f"<div class='row'>{inp('prefix', 'PROMO', _('پیشوند'), 'm')}{inp('count', '10', _('تعداد (حداکثر ۵۰۰)'), 'm')}</div>{form_fields()}"
                    f"<p><button class='b ok'>{T('ساخت گروهی')}</button></p>", cls="") + "</div>")
    lst = f"<div class='card'><h2>{T('کدها')} ({len(rows)})</h2>{tbl([_('کد'), _('تخفیف'), _('استفاده'), _('هر کاربر'), _('اعتبار'), _('حداقل سفارش'), _('محصولات'), _('وضعیت'), ''], cells)}</div>"
    return await layout(request, _("کدهای تخفیف"), create + lst + bulk, "/discounts")


@post("/discounts/add", "discounts")
async def discount_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    params, err = build_params(d)
    if err:
        raise redirect("/discounts", err=err)
    code = str(d.get("code", "")).strip().upper() or "SS" + secrets.token_hex(3).upper()
    if not re.fullmatch(r"[A-Z0-9_-]{3,20}", code):
        raise redirect("/discounts", err=_("نام کد: حروف انگلیسی/عدد/خط تیره، ۳ تا ۲۰ نویسه."))
    ids, bad = await parse_users(shop, str(d.get("users", "")))
    if bad:
        raise redirect("/discounts", err=_("کاربر پیدا نشد: {u}", u=" ".join(bad)))
    ok = await shop.db.create_discount(code, params.pop("percent"), params.pop("fixed"), params.pop("max_uses"), params.pop("expires_at"), users=ids, **params)
    await shop.db.log(actor(request), "discount_create", code)
    if not ok:
        raise redirect("/discounts", err=_("این کد از قبل وجود دارد."))
    raise redirect(f"/discounts/{code}", ok=_("کد ساخته شد."))


@post("/discounts/bulk", "discounts")
async def discount_bulk(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    params, err = build_params(d)
    n = to_int(d.get("count"), 0) or 0
    prefix = re.sub(r"[^A-Z0-9]", "", str(d.get("prefix", "")).upper())[:10]
    if err or not 1 <= n <= 500:
        raise redirect("/discounts", err=err or _("تعداد باید بین ۱ تا ۵۰۰ باشد."))
    made = []
    for __ in range(n):
        for ___ in range(6):
            code = f"{prefix}{secrets.token_hex(3).upper()}"
            if await shop.db.create_discount(code, params["percent"], params["fixed"], params["max_uses"], params["expires_at"],
                                             **{k: v for k, v in params.items() if k not in ("percent", "fixed", "max_uses", "expires_at")}):
                made.append(code)
                break
    await shop.db.log(actor(request), "discount_bulk", f"{prefix} n={len(made)}")
    from webkit import csv_response
    return csv_response("discount-codes.csv", ["code"], [[c] for c in made])


# ============================================================ جزئیات
@get("/discounts/{code}", "discounts")
async def discount_detail(request):
    shop: Shop = request.app["shop"]
    code = request.match_info["code"].upper()
    d = await shop.db.get_discount(code)
    if not d:
        raise redirect("/discounts", err=_("کد پیدا نشد."))
    users = await shop.db.discount_users(code)
    saved = await shop.db.discount_savings(code)
    usage = await shop.db.discount_usage(code, 50)
    val = f"{d['percent']}%" + (f" ({T('سقف')} {fmt(d['max_discount'])} {T('ت')})" if d["max_discount"] else "") if d["percent"] else f"{fmt(d['fixed'])} {T('تومان')}"
    info = kv([(T("وضعیت"), badge(_("فعال"), "ok") if d["active"] else badge(_("غیرفعال"), "err")), (T("مقدار"), val),
               (T("استفاده"), f"{d['used']} / {d['max_uses'] or '∞'}"), (T("هر کاربر"), d["per_user"] or "∞"),
               (T("اعتبار"), e(left_text(d["expires_at"])) + (f" ({fdate(d['expires_at'])})" if d["expires_at"] else "")),
               (T("شروع"), fdate(d["starts_at"]) if d["starts_at"] else T("بلافاصله")),
               (T("حداقل سفارش"), fmt(d["min_amount"]) if d["min_amount"] else "—"), (T("محصولات"), e(applies_label(d["applies_to"]))),
               (T("تخفیف روی"), T("فقط سود") if d.get("base") == "profit" else T("کل قیمت محصول")),
               (T("فقط اولین خرید"), T("بله") if d["first_only"] else T("خیر")), (T("مجموع تخفیف داده‌شده"), f"{fmt(saved)} {T('تومان')}"),
               (T("یادداشت"), e(d["note"] or "—"))])
    pre = f"/discounts/{e(code)}"
    edit = (f"<div class='card'><h2>✏️ {T('ویرایش')}</h2>" + pform(request, pre + "/edit",
            f"<div class='grid g3'><div><label class='f'>{T('اعتبار')}</label>"
            f"{sel('exp_mode', [('keep', _('بدون تغییر')), ('none', _('بدون انقضا')), ('extend', _('تمدید (اضافه به انقضا)')), ('from_now', _('تنظیم از همین الان'))], 'keep')}"
            f" {inp('dur_n', '0', '', 's')} {sel('dur_u', [(k, _(v)) for k, v in UNITS], 'd')}</div>"
            f"<div><label class='f'>{T('حداکثر کل استفاده')}</label>{inp('max_uses', d['max_uses'], '', 's')}</div>"
            f"<div><label class='f'>{T('هر کاربر')}</label>{inp('per_user', d['per_user'], '', 's')}</div>"
            f"<div><label class='f'>{T('حداقل مبلغ سفارش')}</label>{inp('min_amount', d['min_amount'], '', 'm')}</div>"
            f"<div><label class='f'>{T('سقف تخفیف')}</label>{inp('cap', d['max_discount'], '', 'm')}</div>"
            f"<div><label class='f'>{T('محصولات مجاز')}</label>{applies_checks(d['applies_to'])}</div>"
            f"<div><label class='f'>{T('تخفیف روی چه چیزی؟')}</label>{sel('base', [('price', _('کل قیمت محصول')), ('profit', _('فقط سود من'))], d.get('base') or 'price')}</div>"
            f"<div><label class='f'>{T('یادداشت')}</label>{inp('note', d['note'], '', 'l')}</div></div>"
            f"<p><label><input type='checkbox' name='first_only' {'checked' if d['first_only'] else ''}> {T('فقط برای اولین خرید')}</label> "
            f"<label><input type='checkbox' name='active' {'checked' if d['active'] else ''}> {T('فعال')}</label></p>"
            f"<p><button class='b pri'>{T('ذخیره')}</button></p>", cls="") + "</div>")
    u_cells = [[user_cell(u), pform(request, f"{pre}/users/{u}/remove", f"<button class='b sm err'>{T('حذف')}</button>")] for u in users]
    u_card = (f"<div class='card'><h2>👥 {T('کاربران مجاز')}</h2><p class='hint'>{T('اگر فهرست خالی باشد همه می‌توانند استفاده کنند؛ در غیر این صورت فقط این کاربران.')}</p>"
              f"{tbl([_('کاربر'), ''], u_cells)}"
              + pform(request, pre + "/users/add", f"{inp('users', '', _('آیدی یا یوزرنیم (چندتا با فاصله)'), 'l')} <button class='b ok sm'>{T('افزودن')}</button>", cls="") + "</div>")
    log = (f"<div class='card'><h2>📜 {T('لاگ استفاده')}</h2>"
           + tbl([_("زمان"), _("کاربر"), _("سفارش"), _("تخفیف (ت)")], [[fdate(x["ts"]), user_cell(x["user_id"]), "#" + str(x["order_id"]), fmt(x["amount"])] for x in usage]) + "</div>")
    danger = (f"<div class='card'>" + pform(request, pre + "/toggle", f"<button class='b'>{T('تغییر وضعیت فعال/غیرفعال')}</button>", cls="")
              + pform(request, pre + "/delete", f"<button class='b err'>{T('🗑 حذف کد')}</button>", cls="", confirm=_("این کد حذف شود؟")) + "</div>")
    return await layout(request, f"🏷 {code}", f"<div class='card'><h2>{e(code)}</h2>{info}</div>{edit}{u_card}{log}{danger}", "/discounts")


def user_cell(uid):
    return f"<a href='/users/{uid}'>{uid}</a>"


async def _disc(request):
    shop: Shop = request.app["shop"]
    code = request.match_info["code"].upper()
    d = await shop.db.get_discount(code)
    if not d:
        raise redirect("/discounts", err=_("کد پیدا نشد."))
    return shop, code, d, await request.post()


@post("/discounts/{code}/edit", "discounts")
async def discount_edit(request):
    shop, code, d, f = await _disc(request)
    fields = dict(max_uses=to_int(f.get("max_uses"), d["max_uses"]), per_user=to_int(f.get("per_user"), d["per_user"]),
                  min_amount=to_int(f.get("min_amount"), 0), max_discount=to_int(f.get("cap"), 0) if d["percent"] else 0,
                  applies_to=read_applies(f), first_only=1 if "first_only" in f else 0, note=str(f.get("note", "")).strip()[:200],
                  active=1 if "active" in f else 0, base="profit" if f.get("base") == "profit" else "price")
    if any((v is not None and v < 0) for k, v in fields.items() if isinstance(v, int)):
        raise redirect(f"/discounts/{code}", err=_("مقادیر نباید منفی باشند."))
    mode = f.get("exp_mode", "keep")
    sec = dur_sec(f.get("dur_n"), f.get("dur_u"))
    if mode == "none":
        fields["expires_at"] = 0
    elif mode == "extend" and sec:
        fields["expires_at"] = int(max(time.time(), d["expires_at"] or 0) + sec)
    elif mode == "from_now" and sec:
        fields["expires_at"] = int(time.time() + sec)
    await shop.db.update_discount(code, **fields)
    await shop.db.log(actor(request), "discount_edit", code)
    raise redirect(f"/discounts/{code}", ok=_("ذخیره شد."))


@post("/discounts/{code}/toggle", "discounts")
async def discount_toggle(request):
    shop, code, d, f = await _disc(request)
    await shop.db.toggle_discount(code)
    await shop.db.log(actor(request), "discount_toggle", code)
    raise redirect(f"/discounts/{code}", ok=_("انجام شد."))


@post("/discounts/{code}/delete", "discounts")
async def discount_delete(request):
    shop, code, d, f = await _disc(request)
    await shop.db.delete_discount(code)
    await shop.db.log(actor(request), "discount_delete", code)
    raise redirect("/discounts", ok=_("حذف شد."))


@post("/discounts/{code}/users/add", "discounts")
async def discount_user_add(request):
    shop, code, d, f = await _disc(request)
    ids, bad = await parse_users(shop, str(f.get("users", "")))
    for i in ids:
        await shop.db.add_discount_user(code, i)
    if bad:
        raise redirect(f"/discounts/{code}", err=_("کاربر پیدا نشد: {u}", u=" ".join(bad)))
    raise redirect(f"/discounts/{code}", ok=_("افزوده شد."))


@post("/discounts/{code}/users/{uid}/remove", "discounts")
async def discount_user_remove(request):
    shop, code, d, f = await _disc(request)
    await shop.db.remove_discount_user(code, to_int(request.match_info["uid"]))
    raise redirect(f"/discounts/{code}", ok=_("حذف شد."))


# ============================================================ پیام همگانی
@get("/broadcast", "broadcast")
async def broadcast_page(request):
    shop: Shop = request.app["shop"]
    auds = [("all", _("همه‌ی کاربران"))] + [(k, _(v)) for k, v in FILTER_LABELS.items() if k not in ("all", "active", "banned")]
    auds += [(f"l-{l['code']}", f"{l['flag']} {l['name']}") for l in shop.bot_langs()]
    counts = []
    for k, lab in auds:
        counts.append((k, f"{lab} ({fmt(len(await shop.audience_ids(k)))})"))
    form = pform(request, "/broadcast",
                 f"<label class='f'>{T('مخاطبان')}</label>{sel('aud', counts)}"
                 f"<label class='f'>{T('متن پیام (HTML ساده مجاز: b, i, code, a href)')}</label><textarea name='text' required style='min-height:140px'></textarea>"
                 f"<div class='row' style='margin-top:10px'><label><input type='radio' name='to' value='test' checked> {T('فقط به ادمین‌ها (آزمایشی)')}</label>"
                 f"<label><input type='radio' name='to' value='send'> {T('ارسال به مخاطبان انتخاب‌شده')}</label><button class='b pri'>📢 {T('ارسال')}</button></div>", cls="",
                 confirm=_("پیام ارسال شود؟"))
    prog = f"<div class='card'><h2>{T('وضعیت ارسال')}</h2><div id='st' class='hint'>—</div></div>"
    js = ("<script>async function poll(){try{const r=await fetch('/broadcast/status');const j=await r.json();"
          "document.getElementById('st').textContent=j.running?(j.sent+j.failed)+' / '+j.total+' (failed: '+j.failed+')':"
          "(j.total?('sent '+j.sent+' | failed '+j.failed+' | total '+j.total):'—')}catch(e){}}"
          "poll();setInterval(poll,2000)</script>")
    note = f"<p class='hint'>{T('برای ارسال عکس/ویدیو/فایل یا فوروارد، از داخل ربات: /admin ← پیام همگانی استفاده کنید.')}</p>"
    return await layout(request, _("پیام همگانی"), f"<div class='card'>{form}{note}</div>{prog}{js}", "/broadcast")


@post("/broadcast", "broadcast")
async def broadcast_send(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    text = str(d.get("text", "")).strip()
    if not text or len(text) > 4000:
        raise redirect("/broadcast", err=_("متن خالی یا بیش از حد طولانی است."))
    admins = sorted(shop.admin_ids)
    probe = request["uid"] or admins[0]
    try:
        await shop.bot.send_message(probe, text)      # اعتبارسنجی HTML
    except Exception as ex:  # noqa: BLE001
        raise redirect("/broadcast", err=_("متن معتبر نیست (HTML): {err}", err=str(ex)[:120]))
    if d.get("to") == "send":
        aud = str(d.get("aud", "all"))
        ids = await shop.audience_ids(aud)
        bot = shop.bot

        async def sender(cid):
            await bot.send_message(cid, text)

        if not await shop.start_broadcast(sender, ids):
            raise redirect("/broadcast", err=_("یک ارسال دیگر در حال انجام است."))
        await shop.db.log(actor(request), "broadcast", f"{aud} n={len(ids)} {text[:40]}")
        raise redirect("/broadcast", ok=_("ارسال همگانی برای {n} نفر شروع شد.", n=len(ids)))
    for a in admins:
        if a != probe:
            await shop.safe_send(a, text)
    raise redirect("/broadcast", ok=_("پیام آزمایشی برای ادمین‌ها ارسال شد."))


@get("/broadcast/status", "broadcast")
async def broadcast_status(request):
    return web.json_response(request.app["shop"].broadcast_state)


# ============================================================ دکمه‌ها، رنگ و ایموجی پریمیوم
def style_select(name, cur):
    return sel(name, [(v, _(STYLE_LABELS[v])) for v in VALID_STYLES], cur)


JS_KEYBOARD = """
function renderPreview(){
  var rows={};
  document.querySelectorAll('tr[data-bid]').forEach(function(tr){
    var b=tr.dataset.bid,q=function(n){return tr.querySelector('[name='+n+'_'+b+']')};
    if(!q('enabled').checked||(q('del')&&q('del').checked))return;
    var r=parseInt(q('row').value)||1,p=parseInt(q('pos').value)||1;
    (rows[r]=rows[r]||[]).push({p:p,label:q('label').value,style:q('style').value,emoji:q('emoji').value.trim()!==''});
  });
  var box=document.getElementById('kbprev');box.innerHTML='';
  Object.keys(rows).map(Number).sort(function(a,b){return a-b}).forEach(function(r){
    var d=document.createElement('div');d.className='kbrow';
    rows[r].sort(function(a,b){return a.p-b.p}).forEach(function(b){
      var s=document.createElement('div');s.className='tgb '+b.style;s.textContent=(b.emoji?'⭐ ':'')+b.label;
      d.appendChild(s)});box.appendChild(d)});
}
document.addEventListener('input',renderPreview);document.addEventListener('change',renderPreview);
window.addEventListener('load',renderPreview);
"""


JS_BTNFILTER = """
(function(){var q=document.getElementById('bq');if(!q)return;
q.addEventListener('input',function(){var v=q.value.trim().toLowerCase();
document.querySelectorAll('tr[data-bk]').forEach(function(tr){tr.style.display=(!v||tr.dataset.bk.toLowerCase().indexOf(v)>-1)?'':'none'})})})();
"""


async def all_buttons_card(request, shop):
    """شخصی‌سازی تک‌تک دکمه‌های ربات (کاربر و ادمین): متن، رنگ و ایموجی پریمیوم برای هر دکمه."""
    cat = shop.button_catalog()
    ovr = shop.button_overrides()
    st_opts = [("", _("— مطابق نوع دکمه —")), ("d", _("⚪ پیش‌فرض")), ("primary", _("🔵 آبی")), ("success", _("🟢 سبز")), ("danger", _("🔴 قرمز"))]
    heads = [_("دکمه"), _("متن پیش‌فرض"), _("متن دلخواه"), _("رنگ"), _("ایموجی پریمیوم (ID)")]
    out = ""
    for scope, title in (("user", _("دکمه‌های کاربران")), ("admin", _("دکمه‌های پنل مدیریت (داخل ربات)"))):
        rows = ""
        for c in [x for x in cat if x["scope"] == scope]:
            k = c["key"]
            o = ovr.get(k, {"s": None, "e": "", "t": ""})
            tin = inp(f"bt_{k}", o["t"], _("ثابت نیست") if c["dyn"] else "", "l", extra="disabled" if c["dyn"] else "maxlength=60")
            cells = [f"<code>{e(k)}</code><input type='hidden' name='bk' value='{e(k)}'>", e(c["text"] or "—") + (f" <span class='hint'>({T('پویا')})</span>" if c["dyn"] else ""),
                     tin, sel(f"bs_{k}", st_opts, o["s"] or ""), inp(f"be_{k}", o["e"], _("شناسه"), "m", extra="inputmode=numeric")]
            rows += f"<tr data-bk='{e(k + ' ' + c['text'])}'>" + "".join(f"<td data-label=\"{e(heads[i])}\">{x}</td>" for i, x in enumerate(cells)) + "</tr>"
        out += (f"<h3>{e(title)}</h3><div class='tw'><table class='rt'><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in heads)
                + f"</tr></thead><tbody>{rows}</tbody></table></div>")
    body = (f"<p class='hint'>{T('برای هر دکمه‌ی ربات می‌توانید جدا متن، رنگ و ایموجی پریمیوم تعیین کنید. خالی = همان تنظیم گروهی/پیش‌فرض. متن دلخواه برای همه‌ی زبان‌ها یکسان اعمال می‌شود؛ برای ترجمه‌ی جداگانه خالی بگذارید و از «زبان‌ها» استفاده کنید. دکمه‌های «پویا» (مثل نام محصول) فقط رنگ و ایموجی می‌گیرند.')}</p>"
            f"<p>{inp('bq', '', _('جستجو در دکمه‌ها...'), 'l', extra='id=bq autocomplete=off')}</p>"
            + pform(request, "/keyboard/all", out + f"<p><button class='b pri'>💾 {T('ذخیره‌ی همه')}</button></p>", cls=""))
    reset = pform(request, "/keyboard/all/reset", f"<button class='b err'>♻️ {T('حذف همه‌ی شخصی‌سازی‌های تک‌تک دکمه‌ها')}</button>", cls="", confirm=_("همه‌ی شخصی‌سازی‌های تک‌تک دکمه‌ها حذف شود؟"))
    return f"<div class='card'><h2>🧩 {T('همه‌ی دکمه‌های ربات (جداگانه)')}</h2>{body}<p>{reset}</p></div>"


@post("/keyboard/all", "buttons")
async def keyboard_all_save(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    items = {}
    for k in d.getall("bk", []):
        items[k] = {"s": str(d.get(f"bs_{k}", "")), "e": str(d.get(f"be_{k}", "")), "t": str(d.get(f"bt_{k}", ""))}
    errs = await shop.replace_button_overrides(items, actor(request))
    if errs:
        raise redirect("/keyboard", err=_("برای این دکمه‌ها مقدار نامعتبر بود و ذخیره نشد: {k}", k="، ".join(errs[:8])))
    raise redirect("/keyboard", ok=_("شخصی‌سازی دکمه‌ها ذخیره شد."))


@post("/keyboard/all/reset", "buttons")
async def keyboard_all_reset(request):
    shop: Shop = request.app["shop"]
    await shop.reset_button_overrides(actor(request))
    raise redirect("/keyboard", ok=_("شخصی‌سازی تک‌تک دکمه‌ها حذف شد."))


@get("/keyboard", "buttons")
async def keyboard_page(request):
    shop: Shop = request.app["shop"]
    items = await shop.db.list_menu()
    s = await shop.settings()
    kinds = {"url": _("لینک"), "text": _("پیام متنی")}
    rows_html = ""
    for it in items:
        b = it["id"]
        kind = kinds.get(it["action"], _("داخلی") + ": " + it["action"])
        extra = inp(f"extra_{b}", it["extra"], "", "m") if it["action"] in ("url", "text") else "<span class='hint'>—</span>"
        dele = f"<label><input type='checkbox' name='del_{b}'> {T('حذف')}</label>" if not it["builtin"] else ""
        hdrs = ["#", _("عنوان"), _("رنگ"), _("ایموجی پریمیوم (ID)"), _("ردیف"), _("جایگاه"), _("فعال"), _("نوع"), _("لینک/متن"), ""]
        cells = [f"{b}<input type='hidden' name='bid' value='{b}'>",
                 inp(f'label_{b}', it['label'], '', 'm', extra='maxlength=40'), style_select(f'style_{b}', it['style']),
                 inp(f'emoji_{b}', it.get('emoji_id') or '', _('شناسه'), 'm', extra='inputmode=numeric'),
                 f"<input class='s' name='row_{b}' type='number' min='1' max='30' value='{it['row_no'] + 1}'>",
                 f"<input class='s' name='pos_{b}' type='number' min='1' max='3' value='{it['pos'] + 1}'>",
                 f"<input type='checkbox' name='enabled_{b}' {'checked' if it['enabled'] else ''}>", e(kind), extra, dele]
        rows_html += f"<tr data-bid='{b}'>" + "".join(f"<td data-label=\"{e(hdrs[i])}\">{c}</td>" for i, c in enumerate(cells)) + "</tr>"
    head = "".join(f"<th>{e(h)}</th>" for h in ["#", _("عنوان"), _("رنگ"), _("ایموجی پریمیوم (ID)"), _("ردیف"), _("جایگاه"), _("فعال"), _("نوع"), _("لینک/متن"), ""])
    editor = (f"<div class='split'><div class='card'><h2>🎨 {T('ویرایشگر منوی اصلی')}</h2>"
              f"<p class='hint'>{T('ردیف و جایگاه را عدد بدهید (ردیف ۱ بالاترین). حداکثر ۳ دکمه در هر ردیف. دکمه «پنل مدیریت» فقط برای ادمین‌ها نمایش داده می‌شود. پیش‌نمایش سمت دیگر زنده است.')}</p>"
              + pform(request, "/keyboard/save", f"<div class='tw'><table class='rt'><thead><tr>{head}</tr></thead><tbody>{rows_html}</tbody></table></div>"
                      f"<p><button class='b pri'>💾 {T('ذخیره چیدمان')}</button></p>", cls="")
              + f"</div><div class='sticky'><div class='card'><h2>👁 {T('پیش‌نمایش')}</h2><div class='phone'>"
                f"<div class='msg'>{e(s.get('welcome_text', ''))}</div><div id='kbprev'></div></div></div></div></div>")
    roles = ""
    for r, label in ROLE_LABELS.items():
        roles += (f"<tr><td data-label=\"{e(_('نوع دکمه'))}\"><span class='dot {ui.STYLES[r]}'></span>{T(label)}</td><td data-label=\"{e(_('رنگ'))}\">{style_select('role_' + r, ui.STYLES[r])}</td>"
                  f"<td data-label=\"{e(_('ایموجی پریمیوم (ID)'))}\">{inp('remoji_' + r, ui.EMOJIS.get(r, ''), _('ایموجی پریمیوم (ID)'), 'm', extra='inputmode=numeric')}</td></tr>")
    on = s.get("premium_emoji_enabled", "1") == "1"
    roles_card = (f"<div class='card'><h2>🎨 {T('رنگ و ایموجی دکمه‌های ثابت ربات')}</h2>"
                  f"<p class='hint'>{T('این تنظیمات روی دکمه‌های داخل صفحه‌ها (محصولات، پرداخت، بازگشت، پنل ادمین و ...) اعمال می‌شود.')}</p>"
                  + pform(request, "/keyboard/roles", f"<div class='tw'><table class='rt'><tbody>{roles}</tbody></table></div>"
                          f"<p><label><input type='checkbox' name='premium_on' {'checked' if on else ''}> {T('فعال بودن ایموجی پریمیوم روی دکمه‌ها')}</label></p>"
                          f"<p><button class='b pri'>💾 {T('ذخیره')}</button></p>", cls="") + "</div>")
    all_card = await all_buttons_card(request, shop)
    add = (f"<div class='card'><h2>➕ {T('افزودن دکمه سفارشی')}</h2>"
           + pform(request, "/keyboard/add",
                   f"<div class='row'>{inp('label', '', _('عنوان دکمه'), 'm', required=True, extra='maxlength=40')}"
                   f"{sel('kind', [('url', _('لینک')), ('text', _('نمایش پیام متنی'))])}{style_select('style', '')}"
                   f"{inp('emoji', '', _('ایموجی پریمیوم (ID)'), 'm', extra='inputmode=numeric')}"
                   f"{inp('extra', '', _('آدرس (https://… یا @username) یا متن پیام'), 'w', required=True)}</div><p><button class='b ok'>{T('افزودن')}</button></p>", cls="") + "</div>")
    help_ = (f"<div class='card'><h2>🌟 {T('راهنمای ایموجی پریمیوم')}</h2><p class='hint'>{T('شناسه‌ی ایموجی پریمیوم را از داخل ربات بگیرید: /admin ← دکمه‌ها ← یک دکمه ← «ایموجی پریمیوم» و یک پیام حاوی ایموجی پریمیوم بفرستید؛ یا شناسه‌ی عددی را اینجا بنویسید. ایموجی قبل از متن دکمه نمایش داده می‌شود. اگر مالک ربات پریمیوم نباشد تلگرام آن را رد می‌کند و ربات خودکار بدون ایموجی ادامه می‌دهد.')}</p></div>")
    reset = pform(request, "/keyboard/reset", f"<button class='b err'>♻️ {T('بازنشانی همه چیدمان و رنگ‌ها')}</button>", cls="", confirm=_("همه دکمه‌ها به حالت پیش‌فرض برگردد و دکمه‌های سفارشی حذف شوند؟"))
    note = f"<div class='card'><p class='hint'>{T('رنگ دکمه‌ها از قابلیت رسمی دکمه‌های رنگی تلگرام (Bot API 9.4+) استفاده می‌کند؛ در برنامه‌های قدیمی دکمه به رنگ پیش‌فرض دیده می‌شود.')}</p></div>"
    return await layout(request, _("دکمه‌ها، رنگ و ایموجی"), editor + roles_card + all_card + add + help_ + f"<div class='card'>{reset}</div>" + note + f"<script>{JS_KEYBOARD}{JS_BTNFILTER}</script>", "/keyboard")


def emoji_ok(v: str) -> bool:
    return v == "" or (v.isdigit() and 5 <= len(v) <= 25)


@post("/keyboard/save", "buttons")
async def keyboard_save(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    errs = []
    for raw in d.getall("bid", []):
        bid = to_int(raw)
        it = await shop.db.get_menu_button(bid) if bid else None
        if not it:
            continue
        if not it["builtin"] and f"del_{bid}" in d:
            await shop.db.delete_menu_button(bid)
            continue
        label = str(d.get(f"label_{bid}", "")).strip()
        style = d.get(f"style_{bid}", "")
        emoji = str(d.get(f"emoji_{bid}", "")).strip()
        if not label or len(label) > 40 or style not in VALID_STYLES or not emoji_ok(emoji):
            errs.append(_("دکمه {n}: عنوان، رنگ یا شناسه‌ی ایموجی نامعتبر", n=bid))
            continue
        fields = dict(label=label, style=style, emoji_id=emoji, enabled=1 if f"enabled_{bid}" in d else 0,
                      row_no=max(0, (to_int(d.get(f"row_{bid}"), it["row_no"] + 1) or 1) - 1),
                      pos=max(0, (to_int(d.get(f"pos_{bid}"), it["pos"] + 1) or 1) - 1))
        if it["action"] in ("url", "text"):
            extra = str(d.get(f"extra_{bid}", "")).strip()
            if it["action"] == "url":
                extra = normalize_url(extra)
                if not extra:
                    errs.append(_("دکمه «{label}»: لینک نامعتبر", label=label))
                    continue
            fields["extra"] = extra[:3500]
        await shop.db.update_menu_button(bid, **fields)
    await shop.db.normalize_menu()
    await shop.db.log(actor(request), "menu_save")
    if errs:
        raise redirect("/keyboard", err=_("؛ ").join(errs))
    raise redirect("/keyboard", ok=_("چیدمان ذخیره شد و برای کاربران اعمال می‌شود."))


@post("/keyboard/roles", "buttons")
async def keyboard_roles(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    bad = []
    for r in ROLE_LABELS:
        v = d.get("role_" + r, "")
        if v in VALID_STYLES:
            await shop.set_style(r, v, actor=actor(request))
        em = str(d.get("remoji_" + r, "")).strip()
        if emoji_ok(em):
            await shop.set_role_emoji(r, em, actor=actor(request))
        else:
            bad.append(r)
    await shop.set_setting("premium_emoji_enabled", "1" if "premium_on" in d else "0", actor(request))
    if bad:
        raise redirect("/keyboard", err=_("شناسه‌ی ایموجی نامعتبر برای: {r}", r=", ".join(bad)))
    raise redirect("/keyboard", ok=_("ذخیره شد."))


@post("/keyboard/add", "buttons")
async def keyboard_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    label, kind, extra, style = str(d.get("label", "")).strip(), d.get("kind"), str(d.get("extra", "")).strip(), d.get("style", "")
    emoji = str(d.get("emoji", "")).strip()
    if not label or len(label) > 40 or style not in VALID_STYLES or kind not in ("url", "text") or not extra or not emoji_ok(emoji):
        raise redirect("/keyboard", err=_("اطلاعات دکمه نامعتبر است."))
    if kind == "url":
        extra = normalize_url(extra)
        if not extra:
            raise redirect("/keyboard", err=_("لینک نامعتبر است."))
    await shop.db.add_menu_button(kind, label, extra[:3500], style, emoji)
    await shop.db.log(actor(request), "menu_add", label)
    raise redirect("/keyboard", ok=_("دکمه اضافه شد (در انتهای منو)."))


@post("/keyboard/reset", "buttons")
async def keyboard_reset(request):
    shop: Shop = request.app["shop"]
    await shop.db.reset_menu()
    await shop.db.log(actor(request), "menu_reset")
    raise redirect("/keyboard", ok=_("بازنشانی شد."))
