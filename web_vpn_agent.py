"""پنل وب: فروش پنل نمایندگی — پلن‌های نمایندگی، فهرست نماینده‌ها، عملیات (روز/حجم/ریست/فعال/رمز/حذف)، ساخت برای کاربر."""
from i18n import NW_, _
from service import Shop
from ui import esc as e
from ui import fmt
from vpn_service import bar, days_left, fmt_gb, ltr
from web_vpn import ST_KIND, gb_txt, st_badge, tabs
import vpn_panels
from webkit import (T, actor, badge, fdate, get, hidden, inp, layout, page_no, pager, pform, post, redirect, sel, tbl, to_float,
                    to_int, user_link)

PER = 20


def _admin_panels(panels):
    return [p for p in panels if getattr(vpn_panels.DRIVERS.get(p["type"]), "ADMIN", False)]


def plan_inputs(p=None) -> str:
    p = p or {}
    return (f"<label class='mini'>{T('عنوان')}{inp('title', p.get('title', ''), '', 'm', required=True)}</label> "
            f"<label class='mini'>{T('مدت (روز، ۰=بدون انقضا)')}{inp('days', p.get('days', 30), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('سقف حجم کل (GB، ۰=نامحدود)')}{inp('traffic_gb', gb_txt(p.get('traffic_gb', 0)), '', 's', 'text')}</label> "
            f"<label class='mini'>{T('سقف تعداد کاربر (۰=نامحدود)')}{inp('max_users', p.get('max_users', 0), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('شناسه‌ی نقش (پاسارگاد)')}{inp('role_id', p.get('role_id', ''), '', 's')}</label> "
            f"<label class='mini'>{T('شناسه‌ی سرویس‌ها (مرزنشین؛ خالی=همه)')}{inp('service_ids', p.get('service_ids', ''), '', 'm')}</label> "
            f"<label class='mini'>{T('اجازه‌ی ویرایش کاربران (مرزنشین)')}<select name='modify_users'><option value='1' {'selected' if p.get('modify_users', 1) else ''}>{T('بله')}</option>"
            f"<option value='0' {'' if p.get('modify_users', 1) else 'selected'}>{T('خیر')}</option></select></label> "
            f"<label class='mini'>{T('حداقل حجم هر کاربر (GB)')}{inp('data_min_gb', gb_txt(p.get('data_min_gb', 0)), '', 's')}</label> "
            f"<label class='mini'>{T('حداکثر حجم هر کاربر (GB)')}{inp('data_max_gb', gb_txt(p.get('data_max_gb', 0)), '', 's')}</label> "
            f"<label class='mini'>{T('حداقل مدت کاربر (روز)')}{inp('expire_min_days', p.get('expire_min_days', 0), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('حداکثر مدت کاربر (روز)')}{inp('expire_max_days', p.get('expire_max_days', 0), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('قیمت (ت)')}{inp('price', p.get('price', ''), '', 'm', 'number')}</label> "
            f"<label class='mini'>{T('ترتیب')}{inp('sort', p.get('sort', 100), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('حجم/زمان اضافه')}{sel('addon_on', [('1', _('روشن')), ('0', _('خاموش'))], str(p.get('addon_on', 1)))}</label> "
            + "".join(
                f"<label class='mini'>{T(nm + ' دلخواه')}{sel('custom_%s_on' % k, [('1', _('روشن')), ('0', _('خاموش'))], str(p.get('custom_%s_on' % k, 0)))}</label> "
                f"<label class='mini'>{T('قیمت هر ' + un + ' (ت)')}{inp('custom_%s_price' % k, p.get('custom_%s_price' % k, 0), '', 's', 'number')}</label> "
                f"<label class='mini'>{T('حداقل ' + nm)}{inp('custom_%s_min' % k, p.get('custom_%s_min' % k, 0), '', 's', 'text')}</label> "
                f"<label class='mini'>{T('حداکثر ' + nm)}{inp('custom_%s_max' % k, p.get('custom_%s_max' % k, 0), '', 's', 'text')}</label> "
                for k, nm, un in (("gb", "حجم", "گیگابایت"), ("days", "زمان", "روز")))
            + f"<label class='mini'>{T('توضیح برای خریدار')}{inp('note', p.get('note', ''), '', 'l')}</label>")


def _ids(v) -> str:
    return ",".join(x for x in str(v or "").replace(" ", "").split(",") if x.isdigit())


def _read_plan(d) -> dict:
    return {"title": str(d.get("title", "")).strip()[:80], "days": max(to_int(d.get("days"), 0) or 0, 0),
            "traffic_gb": max(to_float(d.get("traffic_gb"), 0) or 0, 0), "max_users": max(to_int(d.get("max_users"), 0) or 0, 0),
            "role_id": "".join(c for c in str(d.get("role_id", "")) if c.isdigit())[:6], "service_ids": _ids(d.get("service_ids")),
            "modify_users": 0 if str(d.get("modify_users", "1")) == "0" else 1,
            "data_min_gb": max(to_float(d.get("data_min_gb"), 0) or 0, 0), "data_max_gb": max(to_float(d.get("data_max_gb"), 0) or 0, 0),
            "expire_min_days": max(to_int(d.get("expire_min_days"), 0) or 0, 0), "expire_max_days": max(to_int(d.get("expire_max_days"), 0) or 0, 0),
            "price": max(to_int(d.get("price"), 0) or 0, 0), "sort": to_int(d.get("sort"), 100) or 100,
            "addon_on": 0 if str(d.get("addon_on", "1")) == "0" else 1,
            **{f"custom_{k}_{f}": (to_float(d.get(f"custom_{k}_{f}"), 0) if (k == "gb" and f != "price") else to_int(d.get(f"custom_{k}_{f}"), 0)) or 0
               for k in ("gb", "days") for f in ("price", "min", "max")},
            "custom_gb_on": 1 if str(d.get("custom_gb_on", "0")) == "1" else 0, "custom_days_on": 1 if str(d.get("custom_days_on", "0")) == "1" else 0,
            "note": str(d.get("note", "")).strip()[:200]}


# ============================================================ پلن‌های نمایندگی
@get("/vpn/agent-plans", "vpn")
async def plans_page(request):
    shop: Shop = request.app["shop"]
    panels = _admin_panels(await shop.db.list_vpn_panels())
    rows = []
    for p in await shop.db.list_agent_plans():
        edit = pform(request, f"/vpn/agent-plans/{p['id']}/edit", plan_inputs(p) + f"<button class='b sm pri'>{T('ذخیره')}</button>", cls="")
        acts = (pform(request, f"/vpn/agent-plans/{p['id']}/toggle", f"<button class='b sm'>{T('غیرفعال') if p['active'] else T('فعال')}</button>")
                + pform(request, f"/vpn/agent-plans/{p['id']}/delete", f"<button class='b sm err'>{T('حذف')}</button>", confirm=_("پلن حذف شود؟")))
        adds = await shop.db.list_agent_addons(p["id"])
        arows = "".join(
            f"<tr><td>{'+' + gb_txt(a['amount']) + ' GB' if a['kind'] == 'gb' else '+' + str(int(a['amount'])) + ' ' + T('روز')}</td><td>{fmt(a['price'])}</td><td>"
            + pform(request, f"/vpn/agent-addons/{a['id']}/edit", f"{inp('amount', gb_txt(a['amount']), '', 's')}{inp('price', a['price'], '', 's', 'number')}<button class='b sm pri'>{T('ذخیره')}</button>", cls="")
            + pform(request, f"/vpn/agent-addons/{a['id']}/toggle", f"<button class='b sm'>{T('غیرفعال') if a['active'] else T('فعال')}</button>")
            + pform(request, f"/vpn/agent-addons/{a['id']}/delete", f"<button class='b sm err'>{T('حذف')}</button>", confirm=_("بسته حذف شود؟"))
            + "</td></tr>" for a in adds)
        aadd = pform(request, f"/vpn/agent-plans/{p['id']}/addon-add",
                     f"<label class='mini'>{T('نوع')}{sel('kind', [('gb', _('حجم (GB)')), ('days', _('زمان (روز)'))])}</label> "
                     f"<label class='mini'>{T('مقدار')}{inp('amount', '', '', 's', required=True)}</label> "
                     f"<label class='mini'>{T('قیمت (ت)')}{inp('price', '', '', 'm', 'number', required=True)}</label> "
                     f"<button class='b sm pri'>➕ {T('افزودن بسته')}</button>", cls="")
        packs = f"<details><summary>{T('بسته‌های افزودنی')} ({len(adds)})</summary><table>{arows}</table>{aadd}</details>"
        feats = (f"{p['days'] or '∞'} {T('روز')} · " + (ltr(gb_txt(p["traffic_gb"]) + " GB") if p["traffic_gb"] else T("نامحدود")) +
                 f" · {T('سقف کاربر')}: {p['max_users'] or '∞'}")
        rows.append([p["id"], f"<b>{e(p['title'])}</b><br><span class='hint'>{e(p['panel_name'])} · {e(p['panel_type'])}</span>", feats,
                     fmt(p['price']),
                     badge(_("فعال"), "ok") if p["active"] else badge(_("غیرفعال"), ""), f"<details><summary>{T('ویرایش')}</summary>{edit}</details>" + packs, acts])
    add = (pform(request, "/vpn/agent-plans/add", f"<label class='mini'>{T('پنل')}{sel('panel_id', [(p['id'], p['name'] + ' · ' + p['type']) for p in panels])}</label> " + plan_inputs()
                 + f"<button class='b pri'>➕ {T('افزودن پلن نمایندگی')}</button>", cls="")
           if panels else f"<p class='hint'>{T('ابتدا یک پنل پاسارگاد، مرزبان یا مرزنشین اضافه کنید.')}</p>")
    probe = (pform(request, "/vpn/agent-plans/options", f"<label class='mini'>{T('پنل')}{sel('panel_id', [(p['id'], p['name']) for p in panels])}</label>"
                   f"<button class='b'>🔎 {T('نمایش نقش‌ها / سرویس‌های پنل')}</button>", cls="") if panels else "")
    body = (tabs("aplans")
            + f"<div class='card'><h2>🧑‍💼 {T('پلن‌های پنل نمایندگی')}</h2>"
              f"{tbl([_('#'), _('پلن'), _('قابلیت‌ها'), _('قیمت (ت)'), _('وضعیت'), _('ویرایش'), _('عملیات')], rows, _('هنوز پلنی ثبت نشده است.'))}"
              f"<h3>{T('افزودن پلن نمایندگی')}</h3>{add}</div>"
            f"<div class='card'><h2>🔎 {T('شناسه‌ی نقش‌ها و سرویس‌ها')}</h2>{probe}"
            f"<p class='hint'>{T('پاسارگاد: شناسه‌ی نقش (role id) و سقف‌ها در پلن اعمال می‌شود. مرزنشین: شناسه‌ی سرویس‌هایی که نماینده به آن‌ها دسترسی دارد. مرزبان: ادمین ساده ساخته می‌شود و سقف تعداد کاربر ندارد؛ مدت اعتبار و سقف حجم کل را ربات اجرا می‌کند (در صورت اتمام، ادمین غیرفعال می‌شود).')}</p>"
            f"<p class='hint'>{T('ربات با خرید، ادمین را در پنل می‌سازد و آدرس پنل، نام کاربری و رمز را برای خریدار می‌فرستد. انقضای نماینده را ربات اجرا می‌کند (ادمین غیرفعال می‌شود).')}</p></div>")
    return await layout(request, _("پلن‌های نمایندگی"), body, "/vpn")


@post("/vpn/agent-plans/options", "vpn")
async def plan_options(request):
    shop: Shop = request.app["shop"]
    pid = to_int((await request.post()).get("panel_id"))
    panel = await shop.db.get_vpn_panel(pid)
    if not panel:
        raise redirect("/vpn/agent-plans", err=_("پنل پیدا نشد."))
    try:
        opts = await (await shop.vpn.driver(pid)).admin_role_options()
        body = (f"<div class='card'><h2>{e(panel['name'])}</h2>"
                + tbl([_("شناسه"), _("نام")], [[f"<code>{e(str(o['id']))}</code>", e(str(o["name"]))] for o in opts], _("گزینه‌ای برنگشت (این پنل نقش/سرویس ندارد یا دسترسی کافی نیست)."))
                + "</div>")
    except Exception as ex:
        body = f"<div class='flash err'>{e(_('اتصال ناموفق'))}</div><div class='card'><pre style='white-space:pre-wrap'>{e(str(ex))}</pre></div>"
    return await layout(request, _("نقش‌ها / سرویس‌ها"), tabs("aplans") + body + f"<p><a class='b' href='/vpn/agent-plans'>← {T('بازگشت')}</a></p>", "/vpn")


@post("/vpn/agent-plans/add", "vpn")
async def plan_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    f = _read_plan(d)
    pid = to_int(d.get("panel_id"))
    panel = await shop.db.get_vpn_panel(pid)
    if not f["title"] or not f["price"] or not panel or not getattr(vpn_panels.DRIVERS.get(panel["type"]), "ADMIN", False):
        raise redirect("/vpn/agent-plans", err=_("پنل (پاسارگاد/مرزبان/مرزنشین)، عنوان و قیمت را وارد کنید."))
    nid = await shop.db.add_agent_plan(panel_id=pid, **f)
    await shop.db.log(actor(request), "agent_plan_add", f"#{nid}")
    raise redirect("/vpn/agent-plans", ok=_("پلن ثبت شد."))


@post("/vpn/agent-plans/{pid}/edit", "vpn")
async def plan_edit(request):
    shop: Shop = request.app["shop"]
    f = _read_plan(await request.post())
    if not f["title"]:
        raise redirect("/vpn/agent-plans", err=_("عنوان را وارد کنید."))
    await shop.db.update_agent_plan(to_int(request.match_info["pid"]), **f)
    raise redirect("/vpn/agent-plans", ok=_("ذخیره شد."))


@post("/vpn/agent-plans/{pid}/addon-add", "vpn")
async def addon_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    kind = "days" if d.get("kind") == "days" else "gb"
    amount = to_float(d.get("amount"), 0) or 0
    price = to_int(d.get("price"), 0) or 0
    if amount <= 0 or price <= 0 or not await shop.db.get_agent_plan(to_int(request.match_info["pid"])):
        raise redirect("/vpn/agent-plans", err=_("مقدار و قیمت را وارد کنید."))
    await shop.db.add_agent_addon(plan_id=to_int(request.match_info["pid"]), kind=kind, amount=amount, price=price)
    raise redirect("/vpn/agent-plans", ok=_("بسته ثبت شد."))


@post("/vpn/agent-addons/{aid}/edit", "vpn")
async def addon_edit(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    amount, price = to_float(d.get("amount"), 0) or 0, to_int(d.get("price"), 0) or 0
    if amount <= 0 or price <= 0:
        raise redirect("/vpn/agent-plans", err=_("مقدار و قیمت را وارد کنید."))
    await shop.db.update_agent_addon(to_int(request.match_info["aid"]), amount=amount, price=price)
    raise redirect("/vpn/agent-plans", ok=_("ذخیره شد."))


@post("/vpn/agent-addons/{aid}/toggle", "vpn")
async def addon_toggle(request):
    shop: Shop = request.app["shop"]
    aid = to_int(request.match_info["aid"])
    a = await shop.db.get_agent_addon(aid)
    if a:
        await shop.db.update_agent_addon(aid, active=0 if a["active"] else 1)
    raise redirect("/vpn/agent-plans", ok=_("انجام شد."))


@post("/vpn/agent-addons/{aid}/delete", "vpn")
async def addon_delete(request):
    shop: Shop = request.app["shop"]
    await shop.db.delete_agent_addon(to_int(request.match_info["aid"]))
    raise redirect("/vpn/agent-plans", ok=_("حذف شد."))


@post("/vpn/agent-plans/{pid}/toggle", "vpn")
async def plan_toggle(request):
    shop: Shop = request.app["shop"]
    pid = to_int(request.match_info["pid"])
    p = await shop.db.get_agent_plan(pid)
    if p:
        await shop.db.update_agent_plan(pid, active=0 if p["active"] else 1)
    raise redirect("/vpn/agent-plans", ok=_("انجام شد."))


@post("/vpn/agent-plans/{pid}/delete", "vpn")
async def plan_delete(request):
    shop: Shop = request.app["shop"]
    await shop.db.delete_agent_plan(to_int(request.match_info["pid"]))
    raise redirect("/vpn/agent-plans", ok=_("حذف شد."))


# ============================================================ نماینده‌ها
@get("/vpn/agents", "vpn")
async def agents_page(request):
    shop: Shop = request.app["shop"]
    status = request.query.get("status", "")
    q = request.query.get("q", "").strip()
    p = page_no(request)
    total = await shop.db.count_agents(status=status or None, q=q)
    rows = []
    for a in await shop.db.list_agents(status=status or None, q=q, limit=PER, offset=(p - 1) * PER):
        left = days_left(a["expires_at"])
        rows.append([a["id"], f"<a href='/vpn/agents/{a['id']}'><b>{e(a['username'])}</b></a><br><span class='hint'>{e(a.get('panel_name') or '')}</span>", user_link(a["user_id"]),
                     st_badge(a["status"]),
                     f"{ltr(fmt_gb(a['used_bytes']) + ' / ' + (fmt_gb(a['total_bytes']) if a['total_bytes'] else T('نامحدود')))}<br><span class='hint'>{T('کاربران')}: {a['users_count']}</span>",
                     T("نامحدود") if left is None else f"{left} {T('روز')}", f"<a class='b sm' href='/vpn/agents/{a['id']}'>{T('مدیریت')}</a>"])
    opts = [("", _("همه")), ("active", _("فعال")), ("disabled", _("غیرفعال")), ("limited", _("حجم تمام شده")), ("expired", _("منقضی")), ("deleted", _("حذف شده"))]
    flt = (f"<form class='row' method='get'>{sel('status', opts, status)}{inp('q', q, _('نام ادمین / آیدی یا یوزرنیم خریدار'), 'm')}"
           f"<button class='b pri'>{T('فیلتر')}</button><span class='hint'>{T('{n} نماینده', n=fmt(total))}</span></form>")
    plans = await shop.db.list_agent_plans()
    create = (pform(request, "/vpn/agents/create", f"<label class='mini'>{T('آیدی عددی کاربر')}{inp('user_id', '', '', 'm', required=True)}</label> "
                    f"<label class='mini'>{T('پلن')}{sel('plan_id', [(x['id'], x['title'] + ' · ' + x['panel_name']) for x in plans])}</label> "
                    f"<label class='mini'>{T('نام کاربری (اختیاری)')}{inp('name', '', '', 'm')}</label> <button class='b pri'>🆕 {T('ساخت نماینده')}</button>", cls="")
              if plans else f"<p class='hint'>{T('ابتدا یک پلن نمایندگی بسازید.')}</p>")
    body = (tabs("agents") + f"<div class='card'>{flt}</div><div class='card'>{tbl([_('#'), _('ادمین پنل'), _('خریدار'), _('وضعیت'), _('مصرف'), _('باقی‌مانده'), ''], rows, _('نماینده‌ای یافت نشد.'))}{pager(request, total, p, PER)}</div>"
            f"<div class='card'><h2>🆕 {T('ساخت نماینده برای کاربر (بدون پرداخت)')}</h2>{create}</div>")
    return await layout(request, _("نماینده‌ها"), body, "/vpn")


@post("/vpn/agents/create", "vpn")
async def agent_create(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    ok, msg, aid = await shop.vpn.agents.admin_create(to_int(d.get("user_id"), 0) or 0, to_int(d.get("plan_id"), 0) or 0, (d.get("name") or "").strip() or None, actor=actor(request))
    if ok and aid:
        raise redirect(f"/vpn/agents/{aid}", ok=_(msg))
    raise redirect("/vpn/agents", err=_(msg))


@get("/vpn/agents/{aid}", "vpn")
async def agent_page(request):
    shop: Shop = request.app["shop"]
    aid = to_int(request.match_info["aid"])
    a = await shop.db.get_agent(aid)
    if not a:
        raise redirect("/vpn/agents", err=_("نماینده پیدا نشد."))
    left = days_left(a["expires_at"])
    order = await shop.db.get_order(a["order_id"]) if a["order_id"] else None
    link = shop.vpn.agents.panel_link(a)
    info = (f"<div class='grid g4'><div class='stat'><small>{e(_('وضعیت'))}</small><b>{st_badge(a['status'])}</b></div>"
            f"<div class='stat'><small>{e(_('مصرف'))}</small><b>{ltr(fmt_gb(a['used_bytes']))}</b><small>/ {ltr(fmt_gb(a['total_bytes'])) if a['total_bytes'] else e(_('نامحدود'))}</small></div>"
            f"<div class='stat'><small>{e(_('باقی‌مانده'))}</small><b>{e(_('نامحدود')) if left is None else left}</b><small>{e(_('روز')) if left is not None else ''}</small></div>"
            f"<div class='stat'><small>{e(_('کاربران'))}</small><b>{a['users_count']}</b><small>{('/ ' + str(a['max_users'])) if a['max_users'] else ''}</small></div></div>")
    det = (f"<div class='card'><h2>🧑‍💼 {e(a['username'])} <span class='hint'>#{a['id']}</span></h2>"
           f"<p>{T('خریدار')}: {user_link(a['user_id'])} · {T('پنل')}: <b>{e(a.get('panel_name') or '-')}</b> · {T('پلن')}: {e(a['title'] or '-')} · {T('ساخته‌شده')}: {fdate(a['created_at'])}"
           + (f" · {T('انقضا')}: {fdate(a['expires_at'])}" if a["expires_at"] else "")
           + (f" · {T('سفارش')}: <a href='/orders?uid={a['user_id']}'>#{order['id']}</a> ({fmt(order['amount'])})" if order else "") + "</p>"
           f"<p>{T('آدرس پنل')}: <code style='word-break:break-all'>{e(link)}</code> · {T('نام کاربری')}: <code>{e(a['username'])}</code> · {T('رمز')}: <code>{e(a['password'])}</code></p>"
           + (f"<p class='hint'>⚠️ {e(a['note'])}</p>" if a["note"] else "") + "</div>")

    def act(x, label, cls="", extra="", conf=None):
        return pform(request, f"/vpn/agents/{aid}/action", hidden("act", x) + extra + f"<button class='b sm {cls}'>{label}</button>", confirm=conf)

    actions = (f"<div class='card'><h2>{T('عملیات')}</h2><div class='row'>"
               + act("add_days", "➕ " + _("روز"), "", inp("value", "", _("تعداد روز"), "s", "number", required=True))
               + act("set_traffic", "📦 " + _("سقف حجم (GB)"), "", inp("value", "", "GB", "s", required=True))
               + act("reset", "♻️ " + _("ریست مصرف"), "", "", _("مصرف ادمین صفر شود؟"))
               + act("sync", "🔄 " + _("همگام‌سازی"))
               + (act("enable", "▶️ " + _("فعال‌سازی"), "ok") if a["status"] in ("disabled", "expired") else act("disable", "⏸ " + _("غیرفعال‌سازی"), "", "", _("ادمین غیرفعال شود؟")))
               + act("password", "🔑 " + _("رمز جدید (ارسال برای خریدار)"), "", "", _("رمز فعلی تغییر می‌کند. ادامه؟"))
               + act("resend", "📤 " + _("ارسال مجدد برای خریدار")) + "</div></div>")
    danger = (f"<div class='card'><div class='row'>" + act("delete", "🗑 " + _("حذف نماینده"), "err", "", _("ادمین از پنل هم حذف می‌شود. مطمئنید؟"))
              + (act("refund", "💸 " + _("حذف + بازگشت وجه"), "err", "", _("نماینده حذف و مبلغ سفارش به کیف پول کاربر برگردانده شود؟")) if order else "")
              + pform(request, f"/vpn/agents/{aid}/action", hidden("act", "note") + inp("value", a["note"], _("یادداشت"), "l") + f"<button class='b sm'>{T('ثبت یادداشت')}</button>")
              + "</div></div>")
    return await layout(request, _("نماینده {n}", n=a["username"]), tabs("agents") + info + det + actions + danger + f"<p><a class='b' href='/vpn/agents'>← {T('بازگشت')}</a></p>", "/vpn")


@post("/vpn/agents/{aid}/action", "vpn")
async def agent_action(request):
    shop: Shop = request.app["shop"]
    aid = to_int(request.match_info["aid"])
    d = await request.post()
    act = d.get("act", "")
    back = f"/vpn/agents/{aid}"
    who = actor(request)
    ag = shop.vpn.agents
    if act == "resend":
        a = await shop.db.get_agent(aid)
        if a and a["status"] != "deleted":
            await ag.send_agent(a["user_id"], a)
            raise redirect(back, ok=_("ارسال شد."))
        raise redirect(back, err=_("نماینده پیدا نشد."))
    if act in ("delete", "refund"):
        ok, msg = await ag.admin_delete(aid, refund=(act == "refund"), actor=who)
        raise redirect("/vpn/agents" if ok else back, **({"ok": _(msg)} if ok else {"err": _(msg)}))
    val = d.get("value")
    if act == "add_days":
        val = to_int(val, 0)
    elif act == "set_traffic":
        val = to_float(val, 0)
    if act == "add_days" and not val:
        raise redirect(back, err=_("مقدار نامعتبر است."))
    ok, msg = await ag.admin_action(aid, act, val, actor=who)
    if ok and act == "password":
        a = await shop.db.get_agent(aid)
        if a:
            await ag.send_agent(a["user_id"], a)
    raise redirect(back, **({"ok": _(msg)} if ok else {"err": _(msg)}))
