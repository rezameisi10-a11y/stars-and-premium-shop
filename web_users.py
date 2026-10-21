"""پنل وب: کاربران (حرفه‌ای)، سفارش‌ها، پرداخت‌ها، کیف پول، زیرمجموعه‌ها و جایزه‌ها."""
import io
import logging

from aiohttp import web

from db import USER_FILTERS, USER_SORTS
from i18n import N_, _
from service import METHOD_FA, PAY_STATUS_FA, STATUS_FA, Shop
from ui import esc as e
from ui import fmt
from webkit import (PER_PAGE, T, actor, badge, can, csv_response, csrf, fdate, get, hidden, inp, need, page_no, pager, pform, post,
                    redirect, safe_next, sel, status_badge, tbl, to_float, to_int, user_link)

FILTER_LABELS = {"all": N_("همه"), "active": N_("فعال"), "banned": N_("مسدود"), "balance": N_("دارای موجودی"), "buyers": N_("خریدار"),
                 "nobuy": N_("بدون خرید"), "referrers": N_("معرف"), "vip": N_("تخفیف شخصی"), "new7": N_("۷ روز اخیر"),
                 "inactive30": N_("غیرفعال ۳۰ روز")}
SORT_LABELS = {"new": N_("جدیدترین"), "old": N_("قدیمی‌ترین"), "seen": N_("آخرین فعالیت"), "balance": N_("بیشترین موجودی"),
               "spent": N_("بیشترین خرید"), "orders": N_("بیشترین سفارش")}
BULK_INLINE_MAX = 50
log = logging.getLogger("web_users")

SELECT_ALL_JS = ("<script>function selAll(c){document.querySelectorAll('input.ck').forEach(function(x){x.checked=c.checked});}"
                 "function bulkOp(s){document.querySelectorAll('.bo').forEach(function(d){d.style.display=d.dataset.op===s.value?'flex':'none'})}</script>")


def flt_of(request):
    f = request.query.get("flt", "all")
    return f if f in USER_FILTERS else "all"


def sort_of(request):
    s = request.query.get("sort", "new")
    return s if s in USER_SORTS else "new"


# ============================================================ لیست کاربران
@get("/users", "users_view")
async def users_list(request):
    shop: Shop = request.app["shop"]
    q = request.query.get("q", "").strip()
    flt, srt = flt_of(request), sort_of(request)
    p = page_no(request)
    total = await shop.db.count_users(q, flt)
    rows = await shop.db.search_users(q, PER_PAGE, (p - 1) * PER_PAGE, flt, srt)
    edit = can(request, "users_edit")
    cells = []
    for u in rows:
        ck = f"<input type='checkbox' class='ck' name='ids' value='{u['id']}'>" if edit else ""
        cells.append([ck, user_link(u["id"], u["username"]), e(u["first_name"]), e(u.get("lang") or "—"), fmt(u["balance"]),
                      f"{u['orders_n']} / {fmt(u['spent'])}",
                      badge(_("مسدود"), "err") if u["banned"] else badge(_("فعال"), "ok"),
                      (badge(f"{u['personal_discount']}%", "pri") if u.get("personal_discount") else ""), fdate(u["created_at"]),
                      f"<a class='b sm' href='/users/{u['id']}'>{T('مدیریت')}</a>"])
    heads = ["<input type='checkbox' onclick='selAll(this)'>" if edit else "", _("کاربر"), _("نام"), _("زبان"), _("موجودی (ت)"),
             _("خرید (تعداد / مبلغ)"), _("وضعیت"), "", _("عضویت"), ""]
    filt_form = (f"<form class='row' method='get'><input class='l' name='q' value='{e(q)}' placeholder='{T('آیدی / یوزرنیم / نام / یادداشت')}'>"
                 f"{sel('flt', [(k, _(v)) for k, v in FILTER_LABELS.items()], flt)}{sel('sort', [(k, _(v)) for k, v in SORT_LABELS.items()], srt)}"
                 f"<button class='b pri'>{T('اعمال')}</button><a class='b' href='/users/export?{e(request.query_string)}'>⬇️ CSV</a>"
                 f"<span class='hint'>{T('{n} کاربر', n=fmt(total))}</span></form>")
    table = tbl(heads, cells)
    bulk = ""
    if edit and rows:
        langs = [(l["code"], f"{l['name']} ({l['code']})") for l in shop.bot_langs()]
        bulk = (f"<div class='card'><h2>{T('عملیات گروهی')}</h2>"
                f"<p class='hint'>{T('روی کاربران انتخاب‌شده در همین صفحه، یا روی «همه‌ی نتایج این فیلتر» اعمال می‌شود. ادمین‌ها هرگز مسدود نمی‌شوند.')}</p>"
                f"<div class='row'>{sel('scope', [('selected', _('کاربران تیک‌خورده')), ('filtered', _('همه‌ی {n} نتیجه‌ی این فیلتر', n=fmt(total)))])}"
                f"<select name='op' onchange='bulkOp(this)'>"
                f"<option value='msg'>{T('✉️ ارسال پیام')}</option><option value='add'>{T('➕ افزایش موجودی')}</option><option value='sub'>{T('➖ کاهش موجودی')}</option>"
                f"<option value='ban'>{T('⛔ مسدود کردن')}</option><option value='unban'>{T('✅ رفع مسدودی')}</option>"
                f"<option value='pdisc'>{T('🎁 تخفیف شخصی (٪)')}</option><option value='grant'>{T('🏷 فعال‌سازی کد تخفیف')}</option>"
                f"<option value='lang'>{T('🌐 تغییر زبان')}</option></select></div>"
                f"<div class='bo row' data-op='msg' style='margin-top:8px'><textarea name='text' style='min-height:70px' placeholder='{T('متن پیام')}'></textarea></div>"
                f"<div class='bo row' data-op='add' style='display:none;margin-top:8px'>{inp('amount', '', _('مبلغ (تومان)'))}{inp('reason', '', _('دلیل (اختیاری)'))}</div>"
                f"<div class='bo row' data-op='sub' style='display:none;margin-top:8px'></div>"
                f"<div class='bo row' data-op='ban' style='display:none;margin-top:8px'>{inp('ban_reason', '', _('دلیل (اختیاری)'))}</div>"
                f"<div class='bo row' data-op='unban' style='display:none'></div>"
                f"<div class='bo row' data-op='pdisc' style='display:none;margin-top:8px'>{inp('pct', '', _('درصد ۰ تا ۹۰'), 'm')}</div>"
                f"<div class='bo row' data-op='grant' style='display:none;margin-top:8px'>{inp('code', '', _('کد تخفیف موجود'))}</div>"
                f"<div class='bo row' data-op='lang' style='display:none;margin-top:8px'>{sel('lang', langs)}</div>"
                f"<p><button class='b pri'>{T('اجرا')}</button></p></div>")
    hidden_filters = hidden("q", q) + hidden("flt", flt)
    body = (f"<div class='card'>{filt_form}</div>"
            + (f"<form method='post' action='/users/bulk' data-confirm=\"{e(_('این عملیات روی کاربران انتخاب‌شده اجرا شود؟'))}\">{csrf(request)}{hidden_filters}"
               f"<div class='card'>{table}{pager(request, total, p)}</div>{bulk}</form>" if edit else f"<div class='card'>{table}{pager(request, total, p)}</div>")
            + SELECT_ALL_JS)
    from webkit import layout
    return await layout(request, _("کاربران"), body, "/users")


@get("/users/export", "users_view")
async def users_export(request):
    shop: Shop = request.app["shop"]
    q, flt, srt = request.query.get("q", "").strip(), flt_of(request), sort_of(request)
    rows = await shop.db.search_users(q, 100000, 0, flt, srt)
    data = [[u["id"], u["username"] or "", u["first_name"] or "", u.get("lang") or "", u["balance"], u["orders_n"], u["spent"],
             1 if u["banned"] else 0, fdate(u["created_at"]), fdate(u["last_seen"]), u.get("note") or ""] for u in rows]
    await shop.db.log(actor(request), "users_export", f"{flt} n={len(rows)}")
    return csv_response("users.csv", ["id", "username", "name", "lang", "balance", "orders", "spent", "banned", "joined", "last_seen", "note"], data)


# ============================================================ عملیات گروهی
@post("/users/bulk", "users_edit")
async def users_bulk(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    op = str(d.get("op", ""))
    if d.get("scope") == "filtered":
        ids = await shop.db.user_ids_where(str(d.get("q", "")).strip(), d.get("flt", "all") if d.get("flt", "all") in USER_FILTERS else "all")
    else:
        ids = [i for i in (to_int(x) for x in d.getall("ids", [])) if i]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise redirect("/users", err=_("هیچ کاربری انتخاب نشده است."))
    who = actor(request)
    text = str(d.get("text", "")).strip()
    amount = to_int(d.get("amount"), 0) or 0
    reason = str(d.get("reason", "")).strip()[:100]
    pct = to_int(d.get("pct"), -1)
    code = str(d.get("code", "")).strip().upper()
    lang = str(d.get("lang", ""))
    if op == "msg":
        if not text or len(text) > 4000:
            raise redirect("/users", err=_("متن خالی یا بیش از حد طولانی است."))

        async def sender(cid):
            await shop.bot.send_message(cid, e(text))

        if not await shop.start_broadcast(sender, ids):
            raise redirect("/users", err=_("یک ارسال دیگر در حال انجام است."))
        await shop.db.log(who, "bulk_msg", f"n={len(ids)}")
        raise redirect("/users", ok=_("ارسال پیام برای {n} کاربر شروع شد.", n=len(ids)))
    if op in ("add", "sub") and (amount <= 0 or amount > 10_000_000_000):
        raise redirect("/users", err=_("مبلغ نامعتبر است."))
    if op == "pdisc" and not 0 <= pct <= 90:
        raise redirect("/users", err=_("درصد باید بین ۰ تا ۹۰ باشد."))
    if op == "grant" and not await shop.db.get_discount(code):
        raise redirect("/users", err=_("این کد تخفیف وجود ندارد."))
    if op == "lang" and not shop.resolve_bot_lang(lang):
        raise redirect("/users", err=_("زبان نامعتبر است."))
    if op not in ("add", "sub", "ban", "unban", "pdisc", "grant", "lang"):
        raise redirect("/users", err=_("عملیات نامعتبر است."))

    async def run():
        ok = 0
        for uid in ids:
            try:
                if op == "add":
                    r = await shop.admin_adjust_balance(uid, "add", amount, reason, who)
                elif op == "sub":
                    r = await shop.admin_adjust_balance(uid, "sub", amount, reason, who)
                elif op == "ban":
                    r = await shop.admin_set_ban(uid, True, str(d.get("ban_reason", ""))[:200], who)
                elif op == "unban":
                    r = await shop.admin_set_ban(uid, False, "", who)
                elif op == "pdisc":
                    r = await shop.admin_set_personal_discount(uid, pct, who)
                elif op == "grant":
                    r = await shop.admin_grant_code(uid, code, who)
                else:
                    await shop.set_user_lang(uid, lang)
                    r = (True, "")
                ok += 1 if r[0] else 0
            except Exception as ex:  # noqa: BLE001
                log.warning("bulk %s failed for %s: %s", op, uid, ex)
        await shop.db.log(who, f"bulk_{op}", f"n={len(ids)} ok={ok}")
        if len(ids) > BULK_INLINE_MAX:
            await shop.notify_admins(f"✅ bulk {op}: {ok}/{len(ids)}", perm="users_edit")
        return ok

    if len(ids) <= BULK_INLINE_MAX:
        ok = await run()
        raise redirect("/users", ok=_("انجام شد: {ok} از {n} کاربر.", ok=ok, n=len(ids)))
    shop.spawn(run())
    raise redirect("/users", ok=_("عملیات برای {n} کاربر در پس‌زمینه شروع شد؛ پایان آن به ادمین‌ها اعلام می‌شود.", n=len(ids)))


# ============================================================ صفحه‌ی کاربر
def kv(pairs):
    return "<dl class='kv'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in pairs) + "</dl>"


@get("/users/{uid}", "users_view")
async def user_detail(request):
    from webkit import layout
    shop: Shop = request.app["shop"]
    uid = to_int(request.match_info["uid"])
    u = await shop.db.get_user(uid) if uid else None
    if not u:
        raise redirect("/users", err=_("کاربر پیدا نشد."))
    st = await shop.db.user_stats(uid)
    ref = await shop.db.referral_stats(uid)
    me = await shop.db.get_referral(uid)
    orders = await shop.db.list_orders(user_id=uid, limit=15)
    pays = await shop.db.user_payments(uid, 15)
    txs = await shop.db.wallet_history(uid, 15)
    codes = await shop.db.user_discounts(uid)
    usage = await shop.db.user_discount_usage(uid)
    subs = await shop.db.list_referrals(uid, 20)
    edit = can(request, "users_edit")
    status = badge(_("مسدود"), "err") if u["banned"] else badge(_("فعال"), "ok")
    info = kv([
        (T("نام"), f"{e(u['first_name'])} @{e(u['username'] or '-')}"), (T("آیدی"), f"<code>{uid}</code>"),
        (T("زبان"), e(u.get("lang") or "—")), (T("عضویت"), fdate(u["created_at"])), (T("آخرین فعالیت"), fdate(u["last_seen"])),
        (T("موجودی"), f"<b>{fmt(u['balance'])}</b> {T('تومان')}"),
        (T("خرید"), f"{st['done']} / {st['orders']} · {fmt(st['spent'])} {T('تومان')} · {T('سود')}: {fmt(st['profit'])}"),
        (T("وضعیت"), status + (f" {e(u['ban_reason'])}" if u.get("ban_reason") else "")),
        (T("تخفیف شخصی"), f"{u['personal_discount']}%" if u.get("personal_discount") else "—"),
        (T("معرف"), (user_link(me["referrer_id"]) + f" {badge(me['status'])}") if me else "—"),
        (T("زیرمجموعه‌ها"), f"{ref['count']} ({T('تأییدشده')} {ref['active']} · {T('در انتظار')} {ref['pending']} · {T('ترک‌کرده')} {ref['left']}) · {T('پورسانت')}: {fmt(ref['earned'])} · {T('در انتظار واریز')}: {fmt(ref['held'])}"),
        (T("یادداشت"), e(u.get("note") or "—")),
    ])
    forms = ""
    if edit:
        langs = [("", "—")] + [(l["code"], f"{l['name']} ({l['code']})") for l in shop.bot_langs()]
        code_opts = [("", "—")] + [(c["code"], c["code"]) for c in await shop.db.list_discounts()]
        pre = f"/users/{uid}"
        forms = (
            "<div class='grid g3'>"
            f"<div class='card'><h2>👛 {T('موجودی')}</h2>"
            + pform(request, pre + "/balance", f"<div class='row'>{sel('op', [('add', _('➕ افزایش')), ('sub', _('➖ کاهش')), ('set', _('🟰 تنظیم دقیق'))])}"
                    f"{inp('amount', '', _('مبلغ (تومان)'), required=True)}</div><div class='row' style='margin-top:6px'>{inp('reason', '', _('دلیل (اختیاری)'), 'l')}"
                    f"<button class='b pri'>{T('ثبت')}</button></div>", cls="") + "</div>"
            f"<div class='card'><h2>⛔ {T('مسدودسازی')}</h2>"
            + pform(request, pre + "/ban", (f"<button class='b ok'>{T('✅ رفع مسدودی')}</button>" if u["banned"] else
                                           f"<div class='row'>{inp('reason', '', _('دلیل (به کاربر نمایش داده می‌شود)'), 'l')}<button class='b err'>{T('⛔ مسدود کردن')}</button></div>"), cls="") + "</div>"
            f"<div class='card'><h2>✉️ {T('ارسال پیام')}</h2>"
            + pform(request, pre + "/msg", f"<textarea name='text' placeholder='{T('متن پیام به کاربر')}' required style='min-height:60px'></textarea><p><button class='b'>{T('ارسال')}</button></p>", cls="") + "</div>"
            f"<div class='card'><h2>🎁 {T('تخفیف شخصی')}</h2><p class='hint'>{T('روی همه‌ی خریدهای این کاربر خودکار اعمال می‌شود (۰ = حذف).')}</p>"
            + pform(request, pre + "/pdisc", f"<div class='row'>{inp('pct', u.get('personal_discount') or 0, '%', 's', required=True)}<button class='b pri'>{T('ذخیره')}</button></div>", cls="") + "</div>"
            f"<div class='card'><h2>🏷 {T('کد تخفیف')}</h2>"
            + pform(request, pre + "/grant", f"<div class='row'>{sel('code', code_opts)}<button class='b pri'>{T('فعال‌سازی برای کاربر')}</button></div>", cls="")
            + f"<h3>{T('ساخت کد اختصاصی یک‌بارمصرف')}</h3>"
            + pform(request, pre + "/pcode", f"<div class='row'>{sel('kind', [('p', _('درصد')), ('f', _('مبلغ ثابت (تومان)'))])}{inp('val', '', _('مقدار'), 's', required=True)}"
                    f"{inp('hours', '48', _('اعتبار (ساعت)'), 'm')}<button class='b ok'>{T('ساخت و ارسال')}</button></div>", cls="") + "</div>"
            f"<div class='card'><h2>📝 {T('یادداشت و زبان')}</h2>"
            + pform(request, pre + "/note", f"<textarea name='note' style='min-height:50px'>{e(u.get('note') or '')}</textarea><p><button class='b'>{T('ذخیره یادداشت')}</button></p>", cls="")
            + pform(request, pre + "/lang", f"<div class='row'>{sel('lang', langs, u.get('lang') or '')}<button class='b'>{T('تغییر زبان')}</button></div>", cls="") + "</div>"
            "</div>")
    o_html = tbl([_("#"), _("محصول"), _("مبلغ"), _("سود"), _("وضعیت"), _("زمان")],
                 [[o["id"], e(o["title"]), fmt(o["amount"]), fmt(o["amount"] - o["cost"]) if o.get("cost") else "—", status_badge(o["status"]), fdate(o["created_at"])] for o in orders])
    p_html = tbl([_("کد"), _("روش"), _("مبلغ"), _("وضعیت"), _("زمان")],
                 [[f"P{p['id']}", e(_(METHOD_FA.get(p["method"], p["method"]))), fmt(p["amount"]), status_badge(p["status"], pay=True), fdate(p["created_at"])] for p in pays])
    t_html = tbl([_("مبلغ"), _("شرح"), _("زمان")], [[("+" if t["amount"] > 0 else "") + fmt(t["amount"]), e(t["reason"]), fdate(t["created_at"])] for t in txs])
    c_html = tbl([_("کد"), _("مقدار"), _("استفاده‌شده"), _("انقضا")],
                 [[f"<code>{e(c['code'])}</code>", f"{c['percent']}%" if c["percent"] else fmt(c["fixed"]), c["used"], fdate(c["expires_at"]) if c["expires_at"] else "—"] for c in codes],
                 empty=_("کد اختصاصی ندارد."))
    use_html = tbl([_("کد"), _("سفارش"), _("تخفیف"), _("زمان")], [[f"<code>{e(x['code'])}</code>", f"#{x['order_id']}", fmt(x["amount"]), fdate(x["ts"])] for x in usage], empty=_("استفاده‌ای ثبت نشده."))
    lab = {"pending": _("منتظر عضویت"), "active": _("فعال"), "left": _("ترک‌کرده"), "rejected": _("ردشده")}
    s_html = tbl([_("کاربر"), _("وضعیت"), _("تاریخ")], [[user_link(r["referred_id"], r["username"]), badge(lab.get(r["status"], r["status"])), fdate(r["created_at"])] for r in subs])
    body = (f"<div class='card'><h2>👤 {e(u['first_name'])}</h2>{info}</div>{forms}"
            f"<div class='grid g2'><div class='card'><h2>{T('سفارش‌های اخیر')}</h2>{o_html}</div><div class='card'><h2>{T('پرداخت‌های اخیر')}</h2>{p_html}</div>"
            f"<div class='card'><h2>{T('تراکنش‌های کیف پول')}</h2>{t_html}</div><div class='card'><h2>{T('زیرمجموعه‌ها')}</h2>{s_html}</div>"
            f"<div class='card'><h2>{T('کدهای اختصاصی')}</h2>{c_html}</div><div class='card'><h2>{T('استفاده از کد تخفیف')}</h2>{use_html}</div></div>")
    return await layout(request, _("کاربر {uid}", uid=uid), body, "/users")


async def _user(request):
    shop: Shop = request.app["shop"]
    uid = to_int(request.match_info["uid"])
    if not uid or not await shop.db.get_user(uid):
        raise redirect("/users", err=_("کاربر پیدا نشد."))
    return shop, uid, await request.post()


@post("/users/{uid}/balance", "users_edit")
async def user_balance(request):
    shop, uid, d = await _user(request)
    amt = to_int(d.get("amount"))
    op = d.get("op")
    if amt is None or amt < 0 or amt > 10_000_000_000 or (amt == 0 and op != "set") or op not in ("add", "sub", "set"):
        raise redirect(f"/users/{uid}", err=_("مبلغ نامعتبر است."))
    ok, msg = await shop.admin_adjust_balance(uid, op, amt, str(d.get("reason", "")).strip()[:100], actor(request))
    raise redirect(f"/users/{uid}", **({"ok": _(msg)} if ok else {"err": _(msg)}))


@post("/users/{uid}/ban", "users_edit")
async def user_ban(request):
    shop, uid, d = await _user(request)
    u = await shop.db.get_user(uid)
    ok, msg = await shop.admin_set_ban(uid, not u["banned"], str(d.get("reason", "")).strip()[:200], actor(request))
    raise redirect(f"/users/{uid}", **({"ok": _(msg)} if ok else {"err": _(msg)}))


@post("/users/{uid}/msg", "users_edit")
async def user_msg(request):
    shop, uid, d = await _user(request)
    r = await shop.safe_send(uid, f"📩 {e(d.get('text', ''))}")
    await shop.db.log(actor(request), "user_msg", str(uid))
    raise redirect(f"/users/{uid}", **({"ok": _("ارسال شد.")} if r else {"err": _("ارسال نشد (کاربر ربات را بلاک کرده).")}))


@post("/users/{uid}/note", "users_edit")
async def user_note(request):
    shop, uid, d = await _user(request)
    await shop.db.set_user_note(uid, str(d.get("note", "")).strip()[:500])
    await shop.db.log(actor(request), "user_note", str(uid))
    raise redirect(f"/users/{uid}", ok=_("ذخیره شد."))


@post("/users/{uid}/pdisc", "users_edit")
async def user_pdisc(request):
    shop, uid, d = await _user(request)
    pct = to_int(d.get("pct"), -1)
    if not 0 <= pct <= 90:
        raise redirect(f"/users/{uid}", err=_("درصد باید بین ۰ تا ۹۰ باشد."))
    ok, msg = await shop.admin_set_personal_discount(uid, pct, actor(request))
    raise redirect(f"/users/{uid}", **({"ok": _(msg)} if ok else {"err": _(msg)}))


@post("/users/{uid}/grant", "users_edit")
async def user_grant(request):
    shop, uid, d = await _user(request)
    ok, msg = await shop.admin_grant_code(uid, str(d.get("code", "")).strip(), actor(request))
    raise redirect(f"/users/{uid}", **({"ok": _(msg)} if ok else {"err": _(msg)}))


@post("/users/{uid}/pcode", "users_edit")
async def user_pcode(request):
    shop, uid, d = await _user(request)
    val = to_int(d.get("val"))
    hours = to_int(d.get("hours"), 0) or 0
    fixed = d.get("kind") == "f"
    if not val or val < 1 or (not fixed and val > 100) or (fixed and val < 1000) or hours < 0:
        raise redirect(f"/users/{uid}", err=_("مقدار نامعتبر است."))
    ok, msg, code = await shop.admin_personal_code(uid, 0 if fixed else val, val if fixed else 0, hours, actor(request))
    raise redirect(f"/users/{uid}", **({"ok": _(msg) + (f" {code}" if code else "")} if ok else {"err": _(msg)}))


@post("/users/{uid}/lang", "users_edit")
async def user_lang(request):
    shop, uid, d = await _user(request)
    code = str(d.get("lang", ""))
    if code and not shop.resolve_bot_lang(code):
        raise redirect(f"/users/{uid}", err=_("زبان نامعتبر است."))
    await shop.set_user_lang(uid, code or None)
    await shop.db.log(actor(request), "user_lang", f"{uid}={code}")
    raise redirect(f"/users/{uid}", ok=_("ذخیره شد."))


# ============================================================ سفارش‌ها
@get("/orders", "orders")
async def orders_list(request):
    from webkit import layout
    shop: Shop = request.app["shop"]
    status = request.query.get("status", "")
    uid = to_int(request.query.get("uid"))
    p = page_no(request)
    total = await shop.db.count_orders(status or None, uid)
    rows = await shop.db.list_orders(status or None, uid, limit=PER_PAGE, offset=(p - 1) * PER_PAGE)
    nxt = e(request.path_qs)
    cells = []
    for o in rows:
        act = ""
        if o["status"] in ("manual_pending", "failed", "paid"):
            for a, lab, cls, conf in (("done", _("✅ تحویل شد"), "ok", None), ("auto", _("🎁 ارسال خودکار با ستاره ربات"), "", None), ("retry", _("🔁 تلاش مجدد"), "", None),
                                      ("refund", _("↩️ بازگشت وجه"), "err", _("مبلغ به کیف پول کاربر برگردانده شود؟"))):
                if a == "auto" and o["kind"] not in ("gift", "premium"):
                    continue
                if a == "retry" and o["kind"] == "stars" and not shop.s.get("prov_stars_url"):
                    continue
                act += pform(request, f"/orders/{o['id']}/action", hidden("act", a) + hidden("next", request.path_qs) + f"<button class='b sm {cls}'>{e(lab)}</button>", confirm=conf)
        tgt = ("@" + e(o["target_username"])) if o["target_username"] else (str(o["target_id"]) if o["target_id"] else "-")
        profit = fmt(o["amount"] - o["cost"]) if o.get("cost") else "—"
        note = f"<div class='hint'>{e(o['note'])}</div>" if o.get("note") else ""
        cells.append([o["id"], user_link(o["user_id"], o.get("user_username")), e(o["title"]), tgt,
                      fmt(o["amount"]) + (f" <span class='hint'>({e(o['discount_code'])})</span>" if o["discount_code"] else ""), profit,
                      status_badge(o["status"]) + note, fdate(o["created_at"]), act])
    opts = [("", _("همه"))] + [(k, _(v)) for k, v in STATUS_FA.items()]
    body = (f"<div class='card'><form class='row' method='get'>{sel('status', opts, status)}{inp('uid', uid or '', _('آیدی کاربر'), 'm')}"
            f"<button class='b pri'>{T('فیلتر')}</button><span class='hint'>{T('{n} سفارش', n=fmt(total))}</span></form></div>"
            f"<div class='card'>{tbl([_('#'), _('کاربر'), _('محصول'), _('گیرنده'), _('مبلغ (ت)'), _('سود (ت)'), _('وضعیت'), _('زمان'), _('عملیات')], cells)}{pager(request, total, p)}</div>")
    return await layout(request, _("سفارش‌ها"), body, "/orders")


@post("/orders/{oid}/action", "orders")
async def order_action(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    res = await shop.admin_order_action(d.get("act", ""), to_int(request.match_info["oid"]), actor=actor(request))
    raise redirect(safe_next(d.get("next"), "/orders"), ok=_(res))


# ============================================================ پرداخت‌ها
@get("/payments", "receipts")
async def payments_list(request):
    from webkit import layout
    shop: Shop = request.app["shop"]
    status = request.query.get("status", "")
    p = page_no(request)
    total = await shop.db.count_payments(status or None)
    rows = await shop.db.list_payments(status or None, PER_PAGE, (p - 1) * PER_PAGE)
    cells = []
    for r in rows:
        act = ""
        if r["status"] in ("pending", "waiting_admin"):
            nx = hidden("next", request.path_qs)
            act = (pform(request, f"/payments/{r['id']}/action", hidden("act", "ok") + nx + f"<button class='b sm ok'>{T('✅ تأیید')}</button>", confirm=_("این پرداخت تأیید و سفارش پردازش شود؟"))
                   + pform(request, f"/payments/{r['id']}/action", hidden("act", "no") + nx + f"<button class='b sm err'>{T('❌ رد')}</button>", confirm=_("رد شود؟")))
        rec = f"<a href='/payments/{r['id']}/receipt' target='_blank'>🧾 {T('رسید')}</a>" if r["receipt_file_id"] else ""
        crypto = f" <span class='hint'>{r['crypto_amount']}</span>" if r["crypto_amount"] else ""
        cells.append([f"P{r['id']}", user_link(r["user_id"]), (_("سفارش #{n}", n=r["order_id"]) if r["order_id"] else _("شارژ کیف پول")),
                      e(_(METHOD_FA.get(r["method"], r["method"]))), fmt(r["amount"]) + crypto, status_badge(r["status"], pay=True),
                      fdate(r["created_at"]), rec + " " + act])
    opts = [("", _("همه"))] + [(k, _(v)) for k, v in PAY_STATUS_FA.items()]
    body = (f"<div class='card'><form class='row' method='get'>{sel('status', opts, status)}<button class='b pri'>{T('فیلتر')}</button>"
            f"<span class='hint'>{T('{n} پرداخت', n=fmt(total))}</span></form></div>"
            f"<div class='card'>{tbl([_('کد'), _('کاربر'), _('بابت'), _('روش'), _('مبلغ (ت)'), _('وضعیت'), _('زمان'), _('عملیات')], cells)}{pager(request, total, p)}</div>")
    return await layout(request, _("پرداخت‌ها و رسیدها"), body, "/payments")


@post("/payments/{pid}/action", "receipts")
async def payment_action(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    res = await shop.admin_payment_action(d.get("act", ""), to_int(request.match_info["pid"]), actor=actor(request))
    raise redirect(safe_next(d.get("next"), "/payments"), ok=_(res))


@get("/payments/{pid}/receipt", "receipts")
async def payment_receipt(request):
    shop: Shop = request.app["shop"]
    p = await shop.db.get_payment(to_int(request.match_info["pid"]))
    if not p or not p["receipt_file_id"]:
        raise web.HTTPNotFound()
    try:
        f = await shop.bot.get_file(p["receipt_file_id"])
        buf = io.BytesIO()
        await shop.bot.download_file(f.file_path, buf)
    except Exception:  # noqa: BLE001
        raise web.HTTPNotFound(text=_("دریافت رسید از تلگرام ممکن نشد."))
    return web.Response(body=buf.getvalue(), content_type="image/jpeg")


# ============================================================ کیف پول
@get("/wallet", "wallet")
async def wallet_list(request):
    from webkit import layout
    shop: Shop = request.app["shop"]
    p = page_no(request)
    uid = to_int(request.query.get("uid"))
    total = await shop.db.count_wallet_tx() if not uid else (await shop.db.one("SELECT COUNT(*) c FROM wallet_tx WHERE user_id=?", (uid,)))["c"]
    rows = await shop.db.wallet_history(uid, PER_PAGE, (p - 1) * PER_PAGE)
    t = tbl([_("#"), _("کاربر"), _("مبلغ (ت)"), _("شرح"), _("زمان")],
            [[r["id"], user_link(r["user_id"]), ("+" if r["amount"] > 0 else "") + fmt(r["amount"]), e(r["reason"]), fdate(r["created_at"])] for r in rows])
    f = f"<form class='row' method='get'>{inp('uid', uid or '', _('آیدی کاربر'))}<button class='b pri'>{T('فیلتر')}</button></form>"
    return await layout(request, _("تراکنش‌های کیف پول"), f"<div class='card'>{f}</div><div class='card'>{t}{pager(request, total, p)}</div>", "/wallet")


# ============================================================ زیرمجموعه‌ها و جایزه‌ها (ضدتقلب)
REF_LABEL = {"pending": N_("منتظر عضویت"), "active": N_("فعال"), "left": N_("ترک‌کرده"), "rejected": N_("ردشده")}
REF_KIND = {"pending": "warn", "active": "ok", "left": "err", "rejected": "err"}
RW_LABEL = {"held": N_("در انتظار واریز"), "paid": N_("پرداخت‌شده"), "void": N_("باطل‌شده")}
RW_KIND = {"held": "warn", "paid": "ok", "void": "err"}


@get("/referrals", "users_view")
async def referrals_page(request):
    from webkit import layout
    shop: Shop = request.app["shop"]
    status = request.query.get("status", "")
    status = status if status in REF_LABEL else ""
    rstatus = request.query.get("rstatus", "")
    rstatus = rstatus if rstatus in RW_LABEL else ""
    p = page_no(request)
    edit = can(request, "users_edit")
    total = await shop.db.count_referrals_all(status or None)
    rows = await shop.db.list_referrals_all(status or None, PER_PAGE, (p - 1) * PER_PAGE)
    tot = await shop.db.reward_totals()
    cards = "<div class='grid g4'>" + "".join(
        f"<div class='stat {RW_KIND[k]}'><small>{T(RW_LABEL[k])}</small><b>{fmt(tot.get(k, (0, 0))[1])}</b><small>{tot.get(k, (0, 0))[0]} {T('مورد')}</small></div>" for k in RW_LABEL) + "</div>"
    cells = []
    for r in rows:
        act = ""
        if edit and r["status"] in ("pending", "active"):
            act = pform(request, f"/referrals/{r['referred_id']}/reject", hidden("next", request.path_qs) + f"<button class='b sm err'>{T('ردِ زیرمجموعه')}</button>", confirm=_("این زیرمجموعه رد و جایزه‌های در انتظارش باطل شود؟"))
        cells.append([user_link(r["referrer_id"], r["referrer_username"]), user_link(r["referred_id"], r["referred_username"]), badge(_(REF_LABEL.get(r["status"], r["status"])), REF_KIND.get(r["status"], "")),
                      fdate(r["created_at"]), fdate(r.get("activated_at")), e(r.get("note") or ""), act])
    tabs = "<div class='tabs'>" + "".join(f"<a class='{'on' if status == k else ''}' href='/referrals?status={k}'>{e(_(v) if k else _('همه'))}</a>" for k, v in [("", ""), *REF_LABEL.items()]) + "</div>"
    rw_total = await shop.db.count_rewards(rstatus or None)
    rw_p = max(1, to_int(request.query.get("rp"), 1) or 1)
    rws = await shop.db.list_rewards(PER_PAGE, (rw_p - 1) * PER_PAGE, rstatus or None)
    rw_cells = []
    for w in rws:
        act = ""
        if edit and w["status"] == "held":
            act = (pform(request, f"/referrals/reward/{w['id']}", hidden("act", "release") + hidden("next", request.path_qs) + f"<button class='b sm ok'>{T('واریز اکنون')}</button>", confirm=_("جایزه همین حالا (با بررسی عضویت) واریز شود؟"))
                   + pform(request, f"/referrals/reward/{w['id']}", hidden("act", "void") + hidden("next", request.path_qs) + f"<button class='b sm err'>{T('باطل')}</button>", confirm=_("جایزه باطل شود؟")))
        rw_cells.append([w["id"], user_link(w["referrer_id"]), user_link(w["referred_id"]), _("پورسانت سفارش #{n}", n=w["order_id"]) if w["kind"] == "commission" else _("جایزه‌ی عضویت"),
                         fmt(w["amount"]), badge(_(RW_LABEL.get(w["status"], w["status"])), RW_KIND.get(w["status"], "")), fdate(w["release_at"]) if w["release_at"] else "—", e(w.get("note") or ""), act])
    rtabs = "<div class='tabs'>" + "".join(f"<a class='{'on' if rstatus == k else ''}' href='/referrals?rstatus={k}'>{e(_(v) if k else _('همه'))}</a>" for k, v in [("", ""), *RW_LABEL.items()]) + "</div>"
    note = f"<p class='hint'>{T('ضدتقلب: جایزه فقط وقتی فعال می‌شود که زیرمجموعه واقعاً در کانال اجباری عضو شده باشد؛ هنگام واریز هم دوباره عضویت بررسی می‌شود و اگر کانال را ترک کرده باشد جایزه باطل می‌شود.')}</p>"
    body = (f"{cards}<div class='card' style='margin-top:14px'><h2>{T('زیرمجموعه‌ها')}</h2>{note}{tabs}"
            f"{tbl([_('معرف'), _('زیرمجموعه'), _('وضعیت'), _('تاریخ ثبت'), _('تاریخ فعال‌سازی'), _('یادداشت'), ''], cells)}{pager(request, total, p)}</div>"
            f"<div class='card'><h2>{T('جایزه‌ها و پورسانت‌ها')}</h2>{rtabs}"
            f"{tbl([_('#'), _('معرف'), _('زیرمجموعه'), _('نوع'), _('مبلغ'), _('وضعیت'), _('زمان واریز'), _('یادداشت'), ''], rw_cells)}"
            f"{pager(request, rw_total, rw_p, PER_PAGE, key='rp')}</div>")
    return await layout(request, _("زیرمجموعه‌ها و جایزه‌ها"), body, "/referrals")


@post("/referrals/{rid}/reject", "users_edit")
async def referral_reject(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    rid = to_int(request.match_info["rid"])
    await shop.db.set_referral_status(rid, "rejected", "admin")
    await shop.db.run("UPDATE referral_rewards SET status='void', note='admin rejected' WHERE referred_id=? AND status='held'", (rid,))
    await shop.db.log(actor(request), "referral_reject", str(rid))
    raise redirect(safe_next(d.get("next"), "/referrals"), ok=_("انجام شد."))


@post("/referrals/reward/{rid}", "users_edit")
async def reward_action(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    rid = to_int(request.match_info["rid"])
    rw = await shop.db.get_reward(rid)
    if not rw or rw["status"] != "held":
        raise redirect("/referrals", err=_("این جایزه دیگر در انتظار نیست."))
    if d.get("act") == "void":
        await shop.db.set_reward_status(rid, "void", "admin")
    else:
        await shop._pay_reward(rw)
    await shop.db.log(actor(request), f"reward_{d.get('act')}", str(rid))
    raise redirect(safe_next(d.get("next"), "/referrals"), ok=_("انجام شد."))
