"""پنل وب: فروش VPN — پنل‌ها (با تست اتصال)، پلن‌ها، بسته‌های افزودنی، سرویس‌ها و عملیات آن‌ها، ساخت/تحویل دستی."""
import json
import time

from aiohttp import web

import vpn_panels
from i18n import NW_, _
from service import Shop
from ui import esc as e
from ui import fmt
from vpn_service import STATUS_FA, bar, days_left, fmt_gb, ltr
from webkit import (T, actor, badge, fdate, get, hidden, inp, layout, page_no, pager, pform, post, redirect, sel, tbl, to_float,
                    to_int, user_link)

PER = 20
PANEL_FIELDS = ("name", "url", "username", "password", "token", "sub_url", "inbound_ids", "capacity")
FIELD_LABEL = {"name": NW_("نام نمایشی"), "url": NW_("آدرس پنل"), "username": NW_("نام کاربری"), "password": NW_("رمز عبور"),
               "token": NW_("توکن / API key"), "sub_url": NW_("آدرس پایه‌ی لینک اشتراک"), "inbound_ids": NW_("اینباند/گروه/سرویس (با کاما)"),
               "capacity": NW_("ظرفیت (۰=نامحدود)")}
ST_KIND = {"active": "ok", "disabled": "err", "limited": "warn", "expired": "err", "on_hold": "warn", "pending_manual": "warn", "deleted": ""}


def gb_txt(v) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return "0"


def tabs(active: str) -> str:
    items = [("/vpn", NW_("پنل‌ها"), "panels"), ("/vpn/plans", NW_("پلن‌ها و بسته‌ها"), "plans"), ("/vpn/services", NW_("سرویس‌ها"), "services"),
             ("/settings?g=vpn", NW_("تنظیمات VPN"), "settings")]
    return "<div class='row' style='margin-bottom:14px'>" + " ".join(
        f"<a class='b sm {'pri' if k == active else ''}' href='{h}'>{e(_(l))}</a>" for h, l, k in items) + "</div>"


def st_badge(s):
    return badge(_(STATUS_FA.get(s, s)), ST_KIND.get(s, ""))


# ============================================================ پنل‌ها
def panel_fields_form(p=None) -> str:
    p = p or {}
    out = ""
    for k in PANEL_FIELDS:
        typ = "password" if k in ("password", "token") else ("number" if k == "capacity" else "text")
        ph = _(FIELD_LABEL[k]) + (f" ({_('خالی = بدون تغییر')})" if p and k in ("password", "token") else "")
        val = "" if k in ("password", "token") else p.get(k, "")
        out += f"<label class='mini'>{e(_(FIELD_LABEL[k]))}{inp(k, val, ph, 'm', typ)}</label> "
    return out


def type_help() -> str:
    rows = "".join(f"<li><b>{e(c.LABEL)}</b>: {e(c.HELP or '—')}</li>" for c in vpn_panels.DRIVERS.values())
    return f"<ul class='hint'>{rows}</ul>"


@get("/vpn", "vpn")
async def vpn_home(request):
    shop: Shop = request.app["shop"]
    st = await shop.db.vpn_stats()
    pend = await shop.db.count_vpn_services(status="pending_manual")
    cards = (f"<div class='grid g4'><div class='stat'><small>{e(_('سرویس‌ها'))}</small><b>{fmt(st['total'])}</b><small>{e(_('فعال'))}: {st['active']} · {e(_('منقضی'))}: {st['expired']}</small></div>"
             f"<div class='stat ok'><small>{e(_('فروش VPN'))}</small><b>{fmt(st['revenue'])}</b><small>{e(_('تومان'))} · {st['orders']} {e(_('سفارش'))}</small></div>"
             f"<div class='stat ok'><small>{e(_('سود VPN'))}</small><b>{fmt(st['profit'])}</b><small>{e(_('تومان'))}</small></div>"
             f"<div class='stat {'warn' if pend else ''}'><small>{e(_('منتظر تحویل دستی'))}</small><b>{pend}</b>"
             f"<a href='/vpn/services?status=pending_manual'>{e(_('بررسی'))} ←</a></div></div>")
    rows = []
    for p in await shop.db.list_vpn_panels():
        edit = pform(request, f"/vpn/panels/{p['id']}/edit", panel_fields_form(p) + f"<button class='b sm pri'>{T('ذخیره')}</button>", cls="")
        acts = (pform(request, f"/vpn/panels/{p['id']}/test", f"<button class='b sm'>🔌 {T('تست اتصال')}</button>")
                + pform(request, f"/vpn/panels/{p['id']}/toggle", f"<button class='b sm'>{T('غیرفعال') if p['enabled'] else T('فعال')}</button>")
                + pform(request, f"/vpn/panels/{p['id']}/delete", f"<button class='b sm err'>{T('حذف')}</button>", confirm=_("پنل و پلن‌های آن حذف شود؟")))
        rows.append([p["id"], f"<b>{e(p['name'])}</b><br><span class='hint'>{e(dict(vpn_panels.TYPES).get(p['type'], p['type']))}</span>",
                     badge(_("فعال"), "ok") if p["enabled"] else badge(_("غیرفعال"), ""), p["n_services"], f"<details><summary>{T('ویرایش')}</summary>{edit}</details>", acts])
    add = pform(request, "/vpn/panels/add", f"<label class='mini'>{T('نوع پنل')}{sel('type', [(k, l) for k, l in vpn_panels.TYPES])}</label> " + panel_fields_form()
                + f"<button class='b pri'>➕ {T('افزودن پنل')}</button> <button class='b' formaction='/vpn/panels/probe'>🔌 {T('تست بدون ذخیره')}</button>", cls="")
    body = (tabs("panels") + cards
            + f"<div class='card'><h2>🖧 {T('پنل‌های VPN')}</h2>{tbl([_('#'), _('پنل'), _('وضعیت'), _('سرویس‌ها'), _('ویرایش'), _('عملیات')], rows, _('هنوز پنلی ثبت نشده است.'))}</div>"
            f"<div class='card'><h2>➕ {T('افزودن پنل')}</h2>{add}"
            f"<p class='hint'>{T('بعد از ثبت حتماً «تست اتصال» بزنید؛ نتیجه‌ی تست، اینباندها/گروه‌های پنل را هم نشان می‌دهد تا شناسه‌ی درست را وارد کنید. برای 3x-ui دقیقاً یک اینباند وارد کنید.')}</p>{type_help()}</div>")
    return await layout(request, _("فروش VPN"), body, "/vpn")


def _read_panel(d, old=None) -> dict:
    out = {}
    for k in PANEL_FIELDS:
        v = str(d.get(k, "")).strip()
        if k == "capacity":
            out[k] = max(to_int(v, 0) or 0, 0)
        elif k in ("password", "token") and old is not None and not v:
            continue
        elif k == "inbound_ids":
            out[k] = v.replace("،", ",").replace(" ", "")
        else:
            out[k] = v
    out["url"] = out.get("url", "").rstrip("/")
    return out


@post("/vpn/panels/add", "vpn")
async def panel_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    ptype = d.get("type", "")
    if ptype not in vpn_panels.DRIVERS:
        raise redirect("/vpn", err=_("نوع پنل نامعتبر است."))
    f = _read_panel(d)
    f["type"] = ptype
    if not f["name"]:
        raise redirect("/vpn", err=_("نام پنل را وارد کنید."))
    miss = shop.vpn.panel_missing(ptype, f)
    if miss:
        raise redirect("/vpn", err=_("فیلدهای لازم: {f}", f="، ".join(_(FIELD_LABEL.get(k, k)) for k in miss)))
    if f["url"] and not f["url"].startswith(("http://", "https://")):
        raise redirect("/vpn", err=_("آدرس باید با http:// یا https:// شروع شود."))
    pid = await shop.db.add_vpn_panel(**f)
    await shop.db.log(actor(request), "vpn_panel_add", f"#{pid} {ptype}")
    raise redirect("/vpn", ok=_("پنل ثبت شد؛ «تست اتصال» را بزنید."))


@post("/vpn/panels/{pid}/edit", "vpn")
async def panel_edit(request):
    shop: Shop = request.app["shop"]
    pid = to_int(request.match_info["pid"])
    p = await shop.db.get_vpn_panel(pid)
    if not p:
        raise redirect("/vpn", err=_("پنل پیدا نشد."))
    f = _read_panel(await request.post(), old=p)
    await shop.db.update_vpn_panel(pid, **f)
    await shop.vpn.forget(pid)
    await shop.db.log(actor(request), "vpn_panel_edit", f"#{pid}")
    raise redirect("/vpn", ok=_("ذخیره شد."))


@post("/vpn/panels/{pid}/toggle", "vpn")
async def panel_toggle(request):
    shop: Shop = request.app["shop"]
    pid = to_int(request.match_info["pid"])
    p = await shop.db.get_vpn_panel(pid)
    if p:
        await shop.db.update_vpn_panel(pid, enabled=0 if p["enabled"] else 1)
    raise redirect("/vpn", ok=_("انجام شد."))


@post("/vpn/panels/{pid}/delete", "vpn")
async def panel_delete(request):
    shop: Shop = request.app["shop"]
    pid = to_int(request.match_info["pid"])
    if await shop.db.delete_vpn_panel(pid):
        await shop.vpn.forget(pid)
        await shop.db.log(actor(request), "vpn_panel_delete", f"#{pid}")
        raise redirect("/vpn", ok=_("حذف شد."))
    raise redirect("/vpn", err=_("این پنل سرویس دارد؛ به‌جای حذف، غیرفعالش کنید."))


async def _test_page(request, row: dict, title: str):
    shop: Shop = request.app["shop"]
    ok, info = await shop.vpn.test_panel(row)
    if not ok:
        body = f"<div class='flash err'>{e(_('اتصال ناموفق'))}</div><div class='card'><pre style='white-space:pre-wrap'>{e(str(info))}</pre></div>"
    else:
        opts = tbl([_("شناسه"), _("نام")], [[f"<code>{e(str(o['id']))}</code>", e(str(o["name"]))] for o in info.get("options_list", [])],
                   _("گزینه‌ای برنگشت (اینباند/گروهی تعریف نشده یا دسترسی ندارد)."))
        extra = f"<p class='hint'>{e(info['options_error'])}</p>" if info.get("options_error") else ""
        body = (f"<div class='flash ok'>{e(_('اتصال موفق'))}" + (f" — {e(_('نسخه'))}: {e(str(info['version']))}" if info.get("version") else "") + "</div>"
                f"<div class='card'><h2>{e(_('اینباند / گروه / سرویس‌های پنل'))}</h2>{opts}{extra}</div>")
    return await layout(request, title, tabs("panels") + body + f"<p><a class='b' href='/vpn'>← {T('بازگشت')}</a></p>", "/vpn")


@post("/vpn/panels/{pid}/test", "vpn")
async def panel_test(request):
    shop: Shop = request.app["shop"]
    p = await shop.db.get_vpn_panel(to_int(request.match_info["pid"]))
    if not p:
        raise redirect("/vpn", err=_("پنل پیدا نشد."))
    return await _test_page(request, p, _("تست اتصال"))


@post("/vpn/panels/probe", "vpn")
async def panel_probe(request):
    d = await request.post()
    ptype = d.get("type", "")
    f = _read_panel(d)
    f["type"] = ptype
    return await _test_page(request, f, _("تست اتصال"))


# ============================================================ پلن‌ها و بسته‌ها
def plan_inputs(p=None) -> str:
    p = p or {}
    return (f"<label class='mini'>{T('عنوان')}{inp('title', p.get('title', ''), '', 'm', required=True)}</label> "
            f"<label class='mini'>{T('مدت (روز)')}{inp('days', p.get('days', 30), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('حجم (GB، ۰=نامحدود)')}{inp('gb', gb_txt(p.get('gb', 50)), '', 's', 'text')}</label> "
            f"<label class='mini'>{T('قیمت (ت)')}{inp('price', p.get('price', ''), '', 'm', 'number')}</label> "
            f"<label class='mini'>{T('هزینه (ت)')}{inp('cost', p.get('cost', 0), '', 'm', 'number')}</label> "
            f"<label class='mini'>{T('محدودیت IP')}{inp('ip_limit', p.get('ip_limit', 0), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('ترتیب')}{inp('sort', p.get('sort', 100), '', 's', 'number')}</label>")


def _read_plan(d) -> dict:
    return {"title": str(d.get("title", "")).strip()[:80], "days": max(to_int(d.get("days"), 0) or 0, 0), "gb": max(to_float(d.get("gb"), 0) or 0, 0),
            "price": max(to_int(d.get("price"), 0) or 0, 0), "cost": max(to_int(d.get("cost"), 0) or 0, 0),
            "ip_limit": max(to_int(d.get("ip_limit"), 0) or 0, 0), "sort": to_int(d.get("sort"), 100) or 100}


@get("/vpn/plans", "vpn")
async def plans_page(request):
    shop: Shop = request.app["shop"]
    panels = await shop.db.list_vpn_panels()
    rows = []
    for p in await shop.db.list_vpn_plans():
        edit = pform(request, f"/vpn/plans/{p['id']}/edit", plan_inputs(p) + f"<button class='b sm pri'>{T('ذخیره')}</button>", cls="")
        acts = (pform(request, f"/vpn/plans/{p['id']}/toggle", f"<button class='b sm'>{T('غیرفعال') if p['active'] else T('فعال')}</button>")
                + pform(request, f"/vpn/plans/{p['id']}/delete", f"<button class='b sm err'>{T('حذف')}</button>", confirm=_("پلن حذف شود؟")))
        rows.append([p["id"], f"<b>{e(p['title'])}</b><br><span class='hint'>{e(p['panel_name'])}</span>", f"{p['days'] or '∞'} {T('روز')} · {ltr(gb_txt(p['gb']) + ' GB')}",
                     f"{fmt(p['price'])}<br><span class='hint'>{T('سود')}: {fmt(p['price'] - p['cost'])}</span>",
                     badge(_("فعال"), "ok") if p["active"] else badge(_("غیرفعال"), ""), f"<details><summary>{T('ویرایش')}</summary>{edit}</details>", acts])
    add = (pform(request, "/vpn/plans/add", f"<label class='mini'>{T('پنل')}{sel('panel_id', [(p['id'], p['name']) for p in panels])}</label> " + plan_inputs()
                 + f"<button class='b pri'>➕ {T('افزودن پلن')}</button>", cls="") if panels else f"<p class='hint'>{T('ابتدا یک پنل اضافه کنید.')}</p>")
    arows = []
    for a in await shop.db.list_vpn_addons():
        edit = pform(request, f"/vpn/addons/{a['id']}/edit", addon_inputs(a) + f"<button class='b sm pri'>{T('ذخیره')}</button>", cls="")
        acts = (pform(request, f"/vpn/addons/{a['id']}/toggle", f"<button class='b sm'>{T('غیرفعال') if a['active'] else T('فعال')}</button>")
                + pform(request, f"/vpn/addons/{a['id']}/delete", f"<button class='b sm err'>{T('حذف')}</button>", confirm=_("بسته حذف شود؟")))
        arows.append([a["id"], f"<b>{e(a['title'])}</b>", f"{ltr(gb_txt(a['gb']) + ' GB')} · {a['days']} {T('روز')}", fmt(a["price"]),
                      badge(_("فعال"), "ok") if a["active"] else badge(_("غیرفعال"), ""), f"<details><summary>{T('ویرایش')}</summary>{edit}</details>", acts])
    aadd = pform(request, "/vpn/addons/add", addon_inputs() + f"<button class='b pri'>➕ {T('افزودن بسته')}</button>", cls="")
    body = (tabs("plans")
            + f"<div class='card'><h2>📦 {T('پلن‌ها')}</h2>{tbl([_('#'), _('پلن'), _('مدت و حجم'), _('قیمت (ت)'), _('وضعیت'), _('ویرایش'), _('عملیات')], rows, _('هنوز پلنی ثبت نشده است.'))}"
              f"<h3>{T('افزودن پلن')}</h3>{add}</div>"
            f"<div class='card'><h2>➕ {T('بسته‌های افزودنی (حجم/زمان اضافه)')}</h2>"
            f"{tbl([_('#'), _('بسته'), _('مقدار'), _('قیمت (ت)'), _('وضعیت'), _('ویرایش'), _('عملیات')], arows, _('هنوز بسته‌ای ثبت نشده است.'))}"
            f"<h3>{T('افزودن بسته')}</h3>{aadd}<p class='hint'>{T('کاربر می‌تواند روی سرویس فعال خودش این بسته‌ها را بخرد. حجم یا زمان (یا هر دو) باید بیشتر از صفر باشد.')}</p></div>")
    return await layout(request, _("پلن‌های VPN"), body, "/vpn")


def addon_inputs(a=None) -> str:
    a = a or {}
    return (f"<label class='mini'>{T('عنوان')}{inp('title', a.get('title', ''), '', 'm', required=True)}</label> "
            f"<label class='mini'>{T('حجم (GB)')}{inp('gb', gb_txt(a.get('gb', 0)), '', 's', 'text')}</label> "
            f"<label class='mini'>{T('زمان (روز)')}{inp('days', a.get('days', 0), '', 's', 'number')}</label> "
            f"<label class='mini'>{T('قیمت (ت)')}{inp('price', a.get('price', ''), '', 'm', 'number')}</label> "
            f"<label class='mini'>{T('هزینه (ت)')}{inp('cost', a.get('cost', 0), '', 'm', 'number')}</label> "
            f"<label class='mini'>{T('ترتیب')}{inp('sort', a.get('sort', 100), '', 's', 'number')}</label>")


def _read_addon(d) -> dict:
    return {"title": str(d.get("title", "")).strip()[:80], "gb": max(to_float(d.get("gb"), 0) or 0, 0), "days": max(to_int(d.get("days"), 0) or 0, 0),
            "price": max(to_int(d.get("price"), 0) or 0, 0), "cost": max(to_int(d.get("cost"), 0) or 0, 0), "sort": to_int(d.get("sort"), 100) or 100}


@post("/vpn/plans/add", "vpn")
async def plan_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    f = _read_plan(d)
    pid = to_int(d.get("panel_id"))
    if not f["title"] or not f["price"] or not await shop.db.get_vpn_panel(pid):
        raise redirect("/vpn/plans", err=_("پنل، عنوان و قیمت را وارد کنید."))
    nid = await shop.db.add_vpn_plan(panel_id=pid, **f)
    await shop.db.log(actor(request), "vpn_plan_add", f"#{nid}")
    raise redirect("/vpn/plans", ok=_("پلن ثبت شد."))


@post("/vpn/plans/{pid}/edit", "vpn")
async def plan_edit(request):
    shop: Shop = request.app["shop"]
    f = _read_plan(await request.post())
    if not f["title"]:
        raise redirect("/vpn/plans", err=_("عنوان را وارد کنید."))
    await shop.db.update_vpn_plan(to_int(request.match_info["pid"]), **f)
    raise redirect("/vpn/plans", ok=_("ذخیره شد."))


@post("/vpn/plans/{pid}/toggle", "vpn")
async def plan_toggle(request):
    shop: Shop = request.app["shop"]
    pid = to_int(request.match_info["pid"])
    p = await shop.db.get_vpn_plan(pid)
    if p:
        await shop.db.update_vpn_plan(pid, active=0 if p["active"] else 1)
    raise redirect("/vpn/plans", ok=_("انجام شد."))


@post("/vpn/plans/{pid}/delete", "vpn")
async def plan_delete(request):
    shop: Shop = request.app["shop"]
    await shop.db.delete_vpn_plan(to_int(request.match_info["pid"]))
    raise redirect("/vpn/plans", ok=_("حذف شد."))


@post("/vpn/addons/add", "vpn")
async def addon_add(request):
    shop: Shop = request.app["shop"]
    f = _read_addon(await request.post())
    if not f["title"] or not f["price"] or not (f["gb"] or f["days"]):
        raise redirect("/vpn/plans", err=_("عنوان، قیمت و حداقل یکی از حجم/زمان را وارد کنید."))
    await shop.db.add_vpn_addon(**f)
    raise redirect("/vpn/plans", ok=_("بسته ثبت شد."))


@post("/vpn/addons/{aid}/edit", "vpn")
async def addon_edit(request):
    shop: Shop = request.app["shop"]
    f = _read_addon(await request.post())
    if not f["title"] or not (f["gb"] or f["days"]):
        raise redirect("/vpn/plans", err=_("عنوان و حداقل یکی از حجم/زمان را وارد کنید."))
    await shop.db.update_vpn_addon(to_int(request.match_info["aid"]), **f)
    raise redirect("/vpn/plans", ok=_("ذخیره شد."))


@post("/vpn/addons/{aid}/toggle", "vpn")
async def addon_toggle(request):
    shop: Shop = request.app["shop"]
    aid = to_int(request.match_info["aid"])
    a = await shop.db.get_vpn_addon(aid)
    if a:
        await shop.db.update_vpn_addon(aid, active=0 if a["active"] else 1)
    raise redirect("/vpn/plans", ok=_("انجام شد."))


@post("/vpn/addons/{aid}/delete", "vpn")
async def addon_delete(request):
    shop: Shop = request.app["shop"]
    await shop.db.delete_vpn_addon(to_int(request.match_info["aid"]))
    raise redirect("/vpn/plans", ok=_("حذف شد."))


# ============================================================ سرویس‌ها
@get("/vpn/services", "vpn")
async def services_page(request):
    shop: Shop = request.app["shop"]
    status = request.query.get("status", "")
    q = request.query.get("q", "").strip()
    panel_id = to_int(request.query.get("panel"))
    p = page_no(request)
    total = await shop.db.count_vpn_services(status=status or None, q=q, panel_id=panel_id)
    rows_db = await shop.db.list_vpn_services(status=status or None, q=q, panel_id=panel_id, limit=PER, offset=(p - 1) * PER)
    rows = []
    for s in rows_db:
        left = days_left(s["expires_at"])
        rows.append([s["id"], f"<a href='/vpn/services/{s['id']}'><b>{e(s['name'])}</b></a>" + (" 🎁" if s["is_test"] else "") + f"<br><span class='hint'>{e(s.get('panel_name') or '')}</span>",
                     user_link(s["user_id"]), st_badge(s["status"]),
                     f"{ltr(fmt_gb(s['used_bytes']) + ' / ' + (fmt_gb(s['total_bytes']) if s['total_bytes'] else T('نامحدود')))}<br><span class='hint'>{bar(s['used_bytes'], s['total_bytes'])}</span>",
                     T("نامحدود") if left is None else f"{left} {T('روز')}", f"<a class='b sm' href='/vpn/services/{s['id']}'>{T('مدیریت')}</a>"])
    panels = await shop.db.list_vpn_panels()
    opts = [("", _("همه"))] + [(k, _(v)) for k, v in STATUS_FA.items() if k != "deleted"] + [("deleted", _(STATUS_FA["deleted"]))]
    flt = (f"<form class='row' method='get'>{sel('status', opts, status)}{sel('panel', [('', _('همه‌ی پنل‌ها'))] + [(x['id'], x['name']) for x in panels], panel_id or '')}"
           f"{inp('q', q, _('نام سرویس / آیدی یا یوزرنیم کاربر'), 'm')}<button class='b pri'>{T('فیلتر')}</button><span class='hint'>{T('{n} سرویس', n=fmt(total))}</span></form>")
    plans = await shop.db.list_vpn_plans()
    plan_opts = [(x["id"], x["title"] + " · " + x["panel_name"]) for x in plans]
    create = (pform(request, "/vpn/services/create", f"<label class='mini'>{T('آیدی عددی کاربر')}{inp('user_id', '', '', 'm', required=True)}</label> "
                    f"<label class='mini'>{T('پلن')}{sel('plan_id', plan_opts)}</label> "
                    f"<label class='mini'>{T('نام کاربری (اختیاری)')}{inp('name', '', '', 'm')}</label> <button class='b pri'>🆕 {T('ساخت سرویس')}</button>", cls="")
              if plans else "")
    body = (tabs("services") + f"<div class='card'>{flt}</div><div class='card'>{tbl([_('#'), _('سرویس'), _('کاربر'), _('وضعیت'), _('مصرف'), _('باقی‌مانده'), ''], rows, _('سرویسی یافت نشد.'))}{pager(request, total, p, PER)}</div>"
            f"<div class='card'><h2>🆕 {T('ساخت سرویس برای کاربر (بدون پرداخت)')}</h2>{create}</div>")
    return await layout(request, _("سرویس‌های VPN"), body, "/vpn")


@post("/vpn/services/create", "vpn")
async def service_create(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    ok, msg, sid = await shop.vpn.admin_create(to_int(d.get("user_id"), 0) or 0, to_int(d.get("plan_id"), 0) or 0, (d.get("name") or "").strip() or None, actor=actor(request))
    if ok and sid:
        raise redirect(f"/vpn/services/{sid}", ok=_(msg))
    raise redirect("/vpn/services", err=_(msg))


@get("/vpn/services/{sid}", "vpn")
async def service_page(request):
    shop: Shop = request.app["shop"]
    sid = to_int(request.match_info["sid"])
    s = await shop.db.get_vpn_service(sid)
    if not s:
        raise redirect("/vpn/services", err=_("سرویس پیدا نشد."))
    left = days_left(s["expires_at"])
    links = json.loads(s["links"] or "[]")
    order = await shop.db.get_order(s["order_id"]) if s["order_id"] else None
    info = (f"<div class='grid g4'><div class='stat'><small>{e(_('وضعیت'))}</small><b>{st_badge(s['status'])}</b></div>"
            f"<div class='stat'><small>{e(_('مصرف'))}</small><b>{ltr(fmt_gb(s['used_bytes']))}</b><small>/ {ltr(fmt_gb(s['total_bytes'])) if s['total_bytes'] else e(_('نامحدود'))}</small></div>"
            f"<div class='stat'><small>{e(_('باقی‌مانده'))}</small><b>{e(_('نامحدود')) if left is None else left}</b><small>{e(_('روز')) if left is not None else ''}</small></div>"
            f"<div class='stat'><small>{e(_('کاربر'))}</small><b>{user_link(s['user_id'])}</b></div></div>")
    det = (f"<div class='card'><h2>📡 {e(s['name'])} <span class='hint'>#{s['id']}</span></h2>"
           f"<p>{T('پنل')}: <b>{e(s.get('panel_name') or '-')}</b> · {T('پلن')}: {e(s['title'] or '-')} · {T('ساخته‌شده')}: {fdate(s['created_at'])}"
           + (f" · {T('انقضا')}: {fdate(s['expires_at'])}" if s["expires_at"] else "")
           + (f" · {T('سفارش')}: <a href='/orders?uid={s['user_id']}'>#{order['id']}</a> ({fmt(order['amount'])})" if order else "") + "</p>"
           + (f"<p>{T('لینک اشتراک')}: <code style='word-break:break-all'>{e(s['sub_url'])}</code></p>" if s["sub_url"] else "")
           + (f"<p>{T('کانفیگ‌ها')}:<br>" + "<br>".join(f"<code style='word-break:break-all'>{e(x)}</code>" for x in links[:10]) + "</p>" if links else "")
           + (f"<p class='hint'>⚠️ {e(s['note'])}</p>" if s["note"] else "") + "</div>")

    def act(a, label, cls="", extra="", conf=None):
        return pform(request, f"/vpn/services/{sid}/action", hidden("act", a) + extra + f"<button class='b sm {cls}'>{label}</button>", confirm=conf)

    if s["status"] == "pending_manual":
        deliver = pform(request, f"/vpn/services/{sid}/deliver",
                        f"<label class='mini'>{T('لینک اشتراک')}{inp('sub_url', '', '', 'l')}</label> "
                        f"<label class='mini'>{T('مدت (روز)')}{inp('days', '', '', 's', 'number')}</label> <label class='mini'>{T('حجم (GB)')}{inp('gb', '', '', 's')}</label><br>"
                        f"<label class='mini'>{T('کانفیگ‌های مستقیم (هر خط یکی)')}<textarea name='links' rows='4' style='width:100%'></textarea></label>"
                        f"<button class='b ok'>📤 {T('ثبت و ارسال برای کاربر')}</button>", cls="")
        actions = f"<div class='card'><h2>📝 {T('تحویل دستی')}</h2>{deliver}</div>"
    else:
        actions = (f"<div class='card'><h2>{T('عملیات')}</h2><div class='row'>"
                   + act("add_days", "➕ " + _("روز"), "", inp("value", "", _("تعداد روز"), "s", "number", required=True))
                   + act("add_gb", "➕ " + _("حجم (GB)"), "", inp("value", "", "GB", "s", required=True))
                   + act("reset", "♻️ " + _("ریست مصرف"), "", "", _("مصرف سرویس صفر شود؟"))
                   + act("sync", "🔄 " + _("همگام‌سازی"))
                   + (act("enable", "▶️ " + _("فعال‌سازی"), "ok") if s["status"] == "disabled" else act("disable", "⏸ " + _("غیرفعال‌سازی"), "", "", _("سرویس غیرفعال شود؟")))
                   + act("revoke", "🔑 " + _("لینک اشتراک جدید"), "", "", _("لینک فعلی دیگر کار نخواهد کرد. ادامه؟"))
                   + act("resend", "📤 " + _("ارسال مجدد برای کاربر")) + "</div></div>")
    danger = (f"<div class='card'><div class='row'>" + act("delete", "🗑 " + _("حذف سرویس"), "err", "", _("سرویس از پنل VPN هم حذف می‌شود. مطمئنید؟"))
              + (act("refund", "💸 " + _("حذف + بازگشت وجه"), "err", "", _("سرویس حذف و مبلغ سفارش به کیف پول کاربر برگردانده شود؟")) if order else "")
              + pform(request, f"/vpn/services/{sid}/action", hidden("act", "note") + inp("value", s["note"], _("یادداشت"), "l") + f"<button class='b sm'>{T('ثبت یادداشت')}</button>")
              + "</div></div>")
    return await layout(request, _("سرویس {n}", n=s["name"]), tabs("services") + info + det + actions + danger + f"<p><a class='b' href='/vpn/services'>← {T('بازگشت')}</a></p>", "/vpn")


@post("/vpn/services/{sid}/action", "vpn")
async def service_action(request):
    shop: Shop = request.app["shop"]
    sid = to_int(request.match_info["sid"])
    d = await request.post()
    act = d.get("act", "")
    back = f"/vpn/services/{sid}"
    who = actor(request)
    if act == "resend":
        s = await shop.db.get_vpn_service(sid)
        if s and s["status"] != "pending_manual":
            await shop.vpn.send_service(s["user_id"], s)
            raise redirect(back, ok=_("ارسال شد."))
        raise redirect(back, err=_("سرویس پیدا نشد."))
    if act in ("delete", "refund"):
        ok, msg = await shop.vpn.admin_delete(sid, refund=(act == "refund"), actor=who)
        raise redirect("/vpn/services" if ok else back, **({"ok": _(msg)} if ok else {"err": _(msg)}))
    val = d.get("value")
    if act == "add_days":
        val = to_int(val, 0)
    elif act == "add_gb":
        val = to_float(val, 0)
    if act in ("add_days", "add_gb") and not val:
        raise redirect(back, err=_("مقدار نامعتبر است."))
    ok, msg = await shop.vpn.admin_action(sid, act, val, actor=who)
    raise redirect(back, **({"ok": _(msg)} if ok else {"err": _(msg)}))


@post("/vpn/services/{sid}/deliver", "vpn")
async def service_deliver(request):
    shop: Shop = request.app["shop"]
    sid = to_int(request.match_info["sid"])
    d = await request.post()
    ok, msg = await shop.vpn.deliver_manual(sid, (d.get("sub_url") or "").strip(), d.get("links") or "", to_int(d.get("days"), 0) or 0, to_float(d.get("gb"), 0) or 0, actor=actor(request))
    raise redirect(f"/vpn/services/{sid}", **({"ok": _(msg)} if ok else {"err": _(msg)}))
