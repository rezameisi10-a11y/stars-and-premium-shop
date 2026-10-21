"""پنل مدیریت تحت وب (aiohttp): ورود امن (مالک + ادمین‌ها با دسترسی جداگانه)، CSRF، چندزبانه، داشبورد و لاگ.
صفحه‌های دیگر در web_users.py، web_shop.py و web_system.py ثبت می‌شوند."""
import asyncio
import hmac
import time
from urllib.parse import quote

from aiohttp import web

import webkit
from i18n import SRC_LANG, _, set_web_lang
from service import Shop, check_pw
from ui import esc as e
from ui import fmt
from webkit import (CSS, NAV, PER_PAGE, ROUTES, actor, badge, bar_chart, can, fdate, first_allowed, get, guarded, layout,
                    make_session, nav_allowed, page_no, pager, post, read_session, redirect, safe_next, set_session, sig, status_badge,
                    tbl, user_link)

PUBLIC_PREFIXES = ("/login", "/auth", "/zarinpal/callback", "/health", "/lang/")
MAX_BODY = 2 * 1024 * 1024
MAX_UPLOAD = 100 * 1024 * 1024
UPLOAD_PATHS = {"/backup/import"}


def lang_info(shop: Shop, code: str):
    for l in shop.langs:
        if l["code"] == code and l["web_enabled"]:
            return l
    return None


@web.middleware
async def security(request: web.Request, handler):
    shop: Shop = request.app["shop"]
    # --- زبان این درخواست (کوکی wl ← پیش‌فرض پنل)
    code = request.cookies.get("wl", "")
    info = lang_info(shop, code)
    if info is None:
        dflt = shop.s.get("web_default_lang") or SRC_LANG
        info = lang_info(shop, dflt) or lang_info(shop, SRC_LANG)
        code = info["code"] if info else SRC_LANG
    set_web_lang(code)
    request["lang"] = code
    request["rtl"] = bool(info["rtl"]) if info else True
    request["who"] = ""
    request["perms"] = None
    request["uid"] = None
    public = request.path == "/login" or request.path.startswith(PUBLIC_PREFIXES) or request.path == "/health"
    if request.method == "POST":
        cl = request.content_length
        limit = 64 * 1024 if public else (MAX_UPLOAD if request.path in UPLOAD_PATHS else MAX_BODY)
        if cl is None or cl > limit:
            return web.Response(status=413, text="Request too large")
    if not public:
        sid = request.cookies.get("sid", "")
        uid = read_session(shop.cfg.web_secret, sid)
        perms = "*" if uid == 0 else shop.admin_perms.get(uid) if uid is not None else None
        if uid is None or perms is None:
            resp = web.HTTPSeeOther("/login")
            if sid:
                resp.del_cookie("sid", path="/")
            raise resp
        request["uid"] = uid
        request["perms"] = perms
        if uid == 0:
            request["who"] = _("مدیر")
        else:
            a = await shop.db.get_admin(uid)
            request["who"] = (a["name"] if a and a["name"] else str(uid))
        request["csrf"] = sig(shop.cfg.web_secret, "csrf" + sid)[:32]
        if request.method == "POST":
            data = await request.post()
            if not hmac.compare_digest(str(data.get("_csrf", "")), request["csrf"]):
                raise web.HTTPForbidden(text="CSRF token invalid")
    try:
        resp = await handler(request)
    except web.HTTPException as ex:
        resp = ex
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:"
    if isinstance(resp, web.HTTPException) and resp.status >= 400:
        raise resp
    return resp


_fails: dict[str, list[float]] = {}


def too_many(ip):
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 600]
    return len(_fails[ip]) >= 8


# ---------------------------------------------------------------- زبان
async def set_lang(request):
    shop: Shop = request.app["shop"]
    code = request.match_info["code"]
    resp = web.HTTPSeeOther(safe_next(request.query.get("next"), "/"))
    if lang_info(shop, code):
        resp.set_cookie("wl", code, max_age=365 * 86400, httponly=True, samesite="Lax", secure=shop.cfg.cookie_secure, path="/")
    return resp


def public_lang_links(request):
    shop: Shop = request.app["shop"]
    langs = [l for l in shop.langs if l["web_enabled"]]
    if len(langs) < 2:
        return ""
    items = "".join(f"<a class='{'on' if l['code'] == request['lang'] else ''}' href='/lang/{e(l['code'])}?next={quote(request.path_qs)}'>{e(l['name'])}</a>" for l in langs)
    return f"<div class='langs' style='justify-content:center;margin-top:14px'>{items}</div>"


# ---------------------------------------------------------------- ورود
async def login_get(request):
    shop: Shop = request.app["shop"]
    err = f"<div class='flash err'>{e(request.query['err'])}</div>" if request.query.get("err") else ""
    owner_pw = bool(shop.cfg.web_password)
    has_admin_login = bool(await shop.db.all("SELECT 1 FROM admins WHERE web_user IS NOT NULL LIMIT 1"))
    if owner_pw or has_admin_login:
        form = (f"<form method='post' action='/login'><label class='f'>{e(_('نام کاربری'))}</label><input class='w' name='u' autocomplete='username'>"
                f"<label class='f'>{e(_('رمز عبور'))}</label><input class='w' type='password' name='p' autocomplete='current-password'>"
                f"<p><button class='b pri' style='width:100%'>{e(_('ورود'))}</button></p></form>")
    else:
        form = f"<p class='hint'>{e(_('ورود با رمز غیرفعال است (WEB_PASSWORD تنظیم نشده).'))}</p>"
    html = f"""<!doctype html><html lang="{e(request['lang'])}" dir="{'rtl' if request['rtl'] else 'ltr'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(_('ورود به پنل'))}</title><style>{CSS}</style></head><body>
<div class="login"><div class="card"><h2>🔐 {e(_('ورود به پنل مدیریت'))}</h2>{err}{form}
<p class="hint">{e(_('یا در ربات دستور {cmd} را بزنید تا لینک ورود یک‌بارمصرف دریافت کنید.', cmd='/panel'))}</p></div>{public_lang_links(request)}</div></body></html>"""
    return web.Response(text=html, content_type="text/html")


async def login_post(request):
    shop: Shop = request.app["shop"]
    ip = request.remote or "?"
    if too_many(ip):
        raise web.HTTPTooManyRequests(text=_("تلاش بیش از حد. ۱۰ دقیقه بعد دوباره امتحان کنید."))
    data = await request.post()
    u, p = str(data.get("u", "")), str(data.get("p", ""))
    uid = None
    pw = shop.cfg.web_password
    if pw and hmac.compare_digest(p.encode(), pw.encode()) and hmac.compare_digest(u.encode(), shop.cfg.web_user.encode()):
        uid = 0
    elif u:
        a = await shop.db.get_admin_by_web_user(u)
        if a and a["web_pw"] and check_pw(p, a["web_pw"]) and a["user_id"] in shop.admin_perms:
            uid = a["user_id"]
    if uid is None:
        _fails.setdefault(ip, []).append(time.time())
        await shop.db.log("web", "login_failed", ip)
        raise redirect("/login", err=_("نام کاربری یا رمز اشتباه است."))
    resp = web.HTTPSeeOther("/")
    set_session(resp, shop, uid)
    await shop.db.log(f"web:{uid}", "login", ip)
    return resp


async def auth_token(request):
    shop: Shop = request.app["shop"]
    uid = shop.consume_login_token(request.query.get("token", ""))
    if uid is None:
        raise redirect("/login", err=_("لینک ورود نامعتبر یا منقضی است. در ربات دوباره /panel بزنید."))
    resp = web.HTTPSeeOther("/")
    set_session(resp, shop, uid)
    await shop.db.log(f"web:{uid}", "login_link")
    return resp


async def logout(request):
    resp = web.HTTPSeeOther("/login")
    resp.del_cookie("sid", path="/")
    return resp


async def health(request):
    return web.Response(text="ok")


# ---------------------------------------------------------------- داشبورد
@get("/")
async def dashboard(request):
    shop: Shop = request.app["shop"]
    if not can(request, "stats"):
        nxt = first_allowed(request)
        if nxt:
            raise web.HTTPSeeOther(nxt)
        return await layout(request, _("داشبورد"), f"<div class='flash warn'>{e(_('هیچ دسترسی‌ای برای شما تعریف نشده است. با مدیر تماس بگیرید.'))}</div>", "/")
    st = await shop.db.stats()
    try:
        bal = await asyncio.wait_for(shop.star_balance(), 4)
    except Exception:  # noqa: BLE001
        bal = None
    snap = shop.pricing.snapshot()
    src = {"auto": ("🟢", _("خودکار")), "manual": ("🟡", _("دستی")), "stale": ("🔴", _("API خراب"))}
    recent = await shop.db.list_orders(limit=8)
    users = await shop.db.search_users("", limit=8)
    prov = ""
    if shop.s.get("prov_balance_url"):
        from provider import fmt_balance
        prov = f"<div class='stat pri'><small>{e(_('موجودی سرویس‌دهنده'))}</small><b>{e(fmt_balance(shop.provider_balance))}</b><small>{e(_('تحویل خودکار'))}</small></div>"
    cards = (f"<div class='grid g4'>"
             f"<div class='stat'><small>{e(_('کل کاربران'))}</small><b>{fmt(st['users'])}</b><small>{e(_('امروز'))} +{st['new_today']}</small></div>"
             f"<div class='stat ok'><small>{e(_('درآمد کل'))}</small><b>{fmt(st['revenue'])}</b><small>{e(_('تومان'))} · {fmt(st['done'])} {e(_('سفارش'))}</small></div>"
             f"<div class='stat ok'><small>{e(_('سود ثبت‌شده'))}</small><b>{fmt(st['profit'])}</b><small>{e(_('امروز'))}: {fmt(st['today_profit'])}</small></div>"
             f"<div class='stat pri'><small>{e(_('فروش امروز'))}</small><b>{fmt(st['today_revenue'])}</b><small>{st['today_orders']} {e(_('سفارش'))}</small></div>"
             f"<div class='stat warn'><small>⭐ {e(_('موجودی ستاره ربات'))}</small><b>{bal if bal is not None else '—'}</b><small>{e(_('برای پریمیوم و گیفت'))}</small></div>"
             f"{prov}"
             f"<div class='stat'><small>💵 {e(_('دلار'))} {src[snap['usd']['source']][0]}</small><b>{fmt(snap['usd']['value'])}</b><small>{e(src[snap['usd']['source']][1])}</small></div>"
             f"<div class='stat'><small>💎 {e(_('تون'))} {src[snap['ton']['source']][0]}</small><b>{fmt(snap['ton']['value'])}</b><small>{e(src[snap['ton']['source']][1])}</small></div>"
             f"<div class='stat err'><small>{e(_('در صف تحویل / خطا'))}</small><b>{st['todo']}</b><a href='/orders?status=manual_pending'>{e(_('بررسی'))} ←</a></div>"
             f"<div class='stat warn'><small>{e(_('رسید منتظر تأیید'))}</small><b>{st['waiting']}</b><a href='/payments?status=waiting_admin'>{e(_('بررسی'))} ←</a></div>"
             f"<div class='stat'><small>{e(_('پرداخت در انتظار'))}</small><b>{st['pending_pay']}</b></div>"
             f"<div class='stat'><small>{e(_('مجموع کیف پول کاربران'))}</small><b>{fmt(st['wallets'])}</b><small>{e(_('تومان'))}</small></div></div>")
    orders_html = tbl([_("#"), _("کاربر"), _("محصول"), _("مبلغ"), _("وضعیت"), _("زمان")],
                      [[o["id"], user_link(o["user_id"], o.get("user_username")), e(o["title"]), fmt(o["amount"]),
                        status_badge(o["status"]), fdate(o["created_at"])] for o in recent])
    users_html = tbl([_("کاربر"), _("نام"), _("موجودی"), _("عضویت")],
                     [[user_link(u["id"], u["username"]), e(u["first_name"]), fmt(u["balance"]), fdate(u["created_at"])] for u in users])
    body = (f"<div class='flash ok'>{e(_('خوش آمدید، {who}', who=request['who']))} 👋</div>{cards}"
            f"<div class='card' style='margin-top:18px'><h2>{e(_('فروش ۱۴ روز اخیر (تومان)'))}</h2>{bar_chart(st['series'])}</div>"
            f"<div class='card'><h2>{e(_('آخرین سفارش‌ها'))} <a class='hint' href='/orders'>{e(_('همه'))} ←</a></h2>{orders_html}</div>"
            f"<div class='card'><h2>{e(_('آخرین کاربران'))} <a class='hint' href='/users'>{e(_('همه'))} ←</a></h2>{users_html}</div>")
    return await layout(request, _("داشبورد"), body, "/")


@get("/logs", "logs")
async def logs_page(request):
    shop: Shop = request.app["shop"]
    p = page_no(request)
    total = await shop.db.count_logs()
    rows = await shop.db.recent_logs(PER_PAGE * 2, (p - 1) * PER_PAGE * 2)
    t = tbl([_("زمان"), _("عامل"), _("عملیات"), _("جزئیات")], [[fdate(r["ts"]), e(r["actor"]), e(r["action"]), e(r["detail"])] for r in rows])
    return await layout(request, _("لاگ فعالیت"), f"<div class='card'>{t}{pager(request, total, p, PER_PAGE * 2)}</div>", "/logs")


def create_web_app(shop: Shop) -> web.Application:
    import web_api  # noqa: F401
    import web_group  # noqa: F401
    import web_marketing  # noqa: F401  (ثبت مسیرها)
    import web_shop  # noqa: F401
    import web_system  # noqa: F401
    import web_users  # noqa: F401
    app = web.Application(middlewares=[security], client_max_size=MAX_UPLOAD + 1024 * 1024)
    app["shop"] = shop
    r = app.router
    r.add_get("/health", health)
    r.add_get("/login", login_get)
    r.add_post("/login", login_post)
    r.add_get("/auth", auth_token)
    r.add_get("/logout", logout)
    r.add_get("/lang/{code}", set_lang)
    r.add_get("/zarinpal/callback", shop.zarinpal_callback)
    for method, path, handler, perm in ROUTES:
        r.add_route(method, path, guarded(handler, perm))
    return app
