"""پنل وب: گزارش‌ها و آمار — فروش روزانه، سود هر محصول/پنل، روش‌های پرداخت، بهترین مشتری‌ها، مشتری‌های در خطر ریزش، خروجی CSV (اکسل)."""
import json
import time

from i18n import N_, _
from service import METHOD_FA, Shop
from ui import esc as e
from ui import fmt
from webkit import T, badge, bar_chart, csv_response, fdate, get, layout, tbl, to_int, user_link

KIND_FA = {"stars": N_("استارز"), "premium": N_("پریمیوم"), "gift": N_("گیفت"), "vpn": N_("VPN"), "agent": N_("نمایندگی")}
TZ = 12600           # تهران UTC+3:30 (برای گروه‌بندی روزانه)
DAYS = (7, 30, 90, 365)
EXPORTS = {"orders": N_("سفارش‌ها"), "payments": N_("پرداخت‌ها"), "users": N_("کاربران"), "vpn": N_("سرویس‌های VPN")}


def _days(request) -> int:
    d = to_int(request.query.get("days"), 30) or 30
    return d if d in DAYS else 30


async def collect(shop: Shop, days: int) -> dict:
    db = shop.db
    now = int(time.time())
    since = now - days * 86400
    q = db.all
    o = await db.one("SELECT COUNT(*) n, COALESCE(SUM(amount),0) rev, COALESCE(SUM(amount-cost),0) prof FROM orders WHERE status='done' AND created_at>=?", (since,))
    prev = await db.one("SELECT COALESCE(SUM(amount),0) rev FROM orders WHERE status='done' AND created_at>=? AND created_at<?", (since - days * 86400, since))
    newu = (await db.one("SELECT COUNT(*) c FROM users WHERE created_at>=?", (since,)))["c"]
    daily = await q("SELECT date(created_at+?, 'unixepoch') d, COUNT(*) n, SUM(amount) rev, SUM(amount-cost) prof FROM orders "
                    "WHERE status='done' AND created_at>=? GROUP BY d ORDER BY d", (TZ, since))
    kinds = await q("SELECT kind, COUNT(*) n, SUM(amount) rev, SUM(amount-cost) prof FROM orders WHERE status='done' AND created_at>=? GROUP BY kind ORDER BY rev DESC", (since,))
    methods = await q("SELECT method, COUNT(*) n, SUM(amount) rev FROM payments WHERE status='confirmed' AND created_at>=? GROUP BY method ORDER BY rev DESC", (since,))
    top = await q("SELECT o.user_id, u.username, COUNT(*) n, SUM(o.amount) rev FROM orders o LEFT JOIN users u ON u.id=o.user_id "
                  "WHERE o.status='done' AND o.created_at>=? GROUP BY o.user_id ORDER BY rev DESC LIMIT 10", (since,))
    # سود/فروش هر پنل VPN (از روی ref سفارش: vpn:new:<plan> یا vpn:renew:<sid>:<plan>)
    plan_panel = {p["id"]: p["panel_id"] for p in await q("SELECT id, panel_id FROM vpn_plans")}
    pname = {p["id"]: p["name"] for p in await q("SELECT id, name FROM vpn_panels")}
    panels: dict = {}
    for r in await q("SELECT ref, amount, cost FROM orders WHERE kind='vpn' AND status='done' AND created_at>=?", (since,)):
        parts = (r["ref"] or "").split(":")
        try:
            plan = int(parts[2]) if parts[1] == "new" else int(parts[3])
        except (IndexError, ValueError):
            continue
        pid = plan_panel.get(plan)
        if pid is None:
            continue
        x = panels.setdefault(pid, {"name": pname.get(pid, f"#{pid}"), "n": 0, "rev": 0, "prof": 0})
        x["n"] += 1
        x["rev"] += r["amount"]
        x["prof"] += r["amount"] - (r["cost"] or 0)
    # مشتری‌های در خطر ریزش
    spent = {r["user_id"]: r["s"] for r in await q("SELECT user_id, SUM(amount) s FROM orders WHERE status='done' GROUP BY user_id")}
    expired = await q("SELECT s.id, s.user_id, s.name, s.expires_at, u.username FROM vpn_services s LEFT JOIN users u ON u.id=s.user_id "
                      "WHERE s.status='expired' AND s.is_test=0 AND s.expires_at>=? ORDER BY s.expires_at DESC LIMIT 30", (now - 30 * 86400,))
    soon = []
    for s in await q("SELECT s.id, s.user_id, s.name, s.expires_at, s.meta, u.username FROM vpn_services s LEFT JOIN users u ON u.id=s.user_id "
                     "WHERE s.status='active' AND s.is_test=0 AND s.expires_at>? AND s.expires_at<? ORDER BY s.expires_at LIMIT 60", (now, now + 3 * 86400)):
        try:
            auto = bool(json.loads(s["meta"] or "{}").get("auto"))
        except ValueError:
            auto = False
        if not auto:
            soon.append(s)
    lapsed = await q("SELECT o.user_id, u.username, MAX(o.created_at) last, COUNT(*) n, SUM(o.amount) rev FROM orders o LEFT JOIN users u ON u.id=o.user_id "
                     "WHERE o.status='done' GROUP BY o.user_id HAVING last<? AND last>=? ORDER BY rev DESC LIMIT 20", (now - 30 * 86400, now - 120 * 86400))
    return {"now": now, "o": o, "prev": prev["rev"], "newu": newu, "daily": daily, "kinds": kinds, "methods": methods, "top": top,
            "panels": sorted(panels.values(), key=lambda x: -x["rev"]), "expired": expired, "soon": soon, "lapsed": lapsed, "spent": spent}


@get("/reports", "stats")
async def reports(request):
    shop: Shop = request.app["shop"]
    days = _days(request)
    d = await collect(shop, days)
    o = d["o"]
    delta = ""
    if d["prev"]:
        pc = (o["rev"] - d["prev"]) * 100 / d["prev"]
        delta = f"<small>{'▲' if pc >= 0 else '▼'} {abs(pc):.0f}٪ {e(_('نسبت به دوره‌ی قبل'))}</small>"
    periods = " ".join(f"<a class='b sm {'pri' if x == days else ''}' href='/reports?days={x}'>{x} {e(_('روز'))}</a>" for x in DAYS)
    cards = (f"<div class='grid g4'><div class='stat ok'><small>{T('فروش')}</small><b>{fmt(o['rev'])}</b><small>{T('تومان')}</small>{delta}</div>"
             f"<div class='stat ok'><small>{T('سود')}</small><b>{fmt(o['prof'])}</b><small>{T('تومان')}</small></div>"
             f"<div class='stat'><small>{T('سفارش موفق')}</small><b>{fmt(o['n'])}</b></div>"
             f"<div class='stat'><small>{T('کاربر جدید')}</small><b>{fmt(d['newu'])}</b></div></div>")
    series = [(x["d"][5:], x["rev"] or 0, f"{x['n']}") for x in d["daily"]]
    chart = bar_chart(series) if series else f"<p class='hint'>{T('در این بازه فروشی ثبت نشده است.')}</p>"
    kinds = tbl([_("محصول"), _("تعداد"), _("فروش"), _("سود")],
                [[e(_(KIND_FA.get(k["kind"], k["kind"]))), k["n"], fmt(k["rev"]), fmt(k["prof"])] for k in d["kinds"]])
    panels = tbl([_("پنل VPN"), _("سفارش"), _("فروش"), _("سود")], [[e(p["name"]), p["n"], fmt(p["rev"]), fmt(p["prof"])] for p in d["panels"]],
                 _("فروش VPN در این بازه ثبت نشده است."))
    methods = tbl([_("روش پرداخت"), _("تعداد"), _("مبلغ")], [[e(_(METHOD_FA.get(m["method"], m["method"]))), m["n"], fmt(m["rev"])] for m in d["methods"]])
    top = tbl([_("کاربر"), _("سفارش"), _("مجموع خرید")], [[user_link(t["user_id"], t["username"]), t["n"], fmt(t["rev"])] for t in d["top"]])
    exp = tbl([_("کاربر"), _("سرویس"), _("منقضی شده"), _("مجموع خرید این کاربر")],
              [[user_link(s["user_id"], s["username"]), f"<code>{e(s['name'])}</code>", fdate(s["expires_at"]), fmt(d["spent"].get(s["user_id"], 0))] for s in d["expired"]],
              _("سرویس منقضی‌شده‌ی تمدیدنشده‌ای در ۳۰ روز اخیر نیست."))
    soon = tbl([_("کاربر"), _("سرویس"), _("پایان"), _("مجموع خرید این کاربر")],
               [[user_link(s["user_id"], s["username"]), f"<code>{e(s['name'])}</code>", fdate(s["expires_at"]), fmt(d["spent"].get(s["user_id"], 0))] for s in d["soon"]],
               _("سرویسی بدون تمدید خودکار در ۳ روز آینده تمام نمی‌شود."))
    lapsed = tbl([_("کاربر"), _("آخرین خرید"), _("تعداد کل"), _("مجموع خرید")],
                 [[user_link(s["user_id"], s["username"]), fdate(s["last"]), s["n"], fmt(s["rev"])] for s in d["lapsed"]],
                 _("مشتری ۳۰ تا ۱۲۰ روز غیرفعالی وجود ندارد."))
    exports = " ".join(f"<a class='b sm' href='/reports/export?what={k}&days={days}'>⬇️ {e(_(l))} (CSV)</a>" for k, l in EXPORTS.items())
    body = (f"<div class='row' style='margin-bottom:14px'>{periods}</div>{cards}"
            f"<div class='card' style='margin-top:18px'><h2>📈 {T('فروش روزانه (تومان)')}</h2>{chart}</div>"
            f"<div class='grid g2'><div class='card'><h2>📦 {T('فروش و سود به تفکیک محصول')}</h2>{kinds}</div>"
            f"<div class='card'><h2>🛡 {T('فروش و سود هر پنل VPN')}</h2>{panels}</div></div>"
            f"<div class='grid g2'><div class='card'><h2>💳 {T('روش‌های پرداخت')}</h2>{methods}</div>"
            f"<div class='card'><h2>🏆 {T('بهترین مشتری‌ها')}</h2>{top}</div></div>"
            f"<div class='card'><h2>⚠️ {T('در خطر ریزش')} {badge(str(len(d['expired']) + len(d['soon']) + len(d['lapsed'])), 'warn')}</h2>"
            f"<h3>{T('سرویس منقضی‌شده و تمدیدنشده (۳۰ روز اخیر)')}</h3>{exp}"
            f"<h3>{T('سرویس‌هایی که تا ۳ روز دیگر تمام می‌شوند (بدون تمدید خودکار)')}</h3>{soon}"
            f"<h3>{T('مشتری‌هایی که ۳۰ تا ۱۲۰ روز است خرید نکرده‌اند')}</h3>{lapsed}</div>"
            f"<div class='card'><h2>⬇️ {T('خروجی اکسل (CSV)')}</h2><p class='hint'>{T('فایل با اکسل و Google Sheets باز می‌شود. بازه: همان بازه‌ی بالا (کاربران و سرویس‌ها: همه).')}</p><p>{exports}</p></div>")
    return await layout(request, _("گزارش‌ها و آمار"), body, "/reports")


@get("/reports/export", "stats")
async def export(request):
    shop: Shop = request.app["shop"]
    what = request.query.get("what", "")
    if what not in EXPORTS:
        return await reports(request)
    days = _days(request)
    since = int(time.time()) - days * 86400
    db = shop.db
    stamp = time.strftime("%Y%m%d")
    iso = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.gmtime((ts or 0) + TZ)) if ts else ""  # noqa: E731
    if what == "orders":
        rows = await db.all("SELECT * FROM orders WHERE created_at>=? ORDER BY id DESC LIMIT 200000", (since,))
        return csv_response(f"orders-{stamp}.csv", ["id", "user_id", "kind", "title", "qty", "amount", "cost", "profit", "status", "discount_code", "created_at"],
                            [[r["id"], r["user_id"], r["kind"], r["title"], r["qty"], r["amount"], r["cost"], r["amount"] - (r["cost"] or 0), r["status"], r["discount_code"] or "", iso(r["created_at"])] for r in rows])
    if what == "payments":
        rows = await db.all("SELECT * FROM payments WHERE created_at>=? ORDER BY id DESC LIMIT 200000", (since,))
        return csv_response(f"payments-{stamp}.csv", ["id", "user_id", "order_id", "method", "amount", "crypto_amount", "status", "ref", "created_at"],
                            [[r["id"], r["user_id"], r["order_id"] or "", r["method"], r["amount"], r["crypto_amount"] or "", r["status"], r["ref"] or "", iso(r["created_at"])] for r in rows])
    if what == "users":
        rows = await db.all("SELECT u.*, (SELECT COALESCE(SUM(amount),0) FROM orders WHERE user_id=u.id AND status='done') spent FROM users u ORDER BY u.id LIMIT 200000")
        return csv_response(f"users-{stamp}.csv", ["id", "username", "first_name", "balance", "banned", "total_spent", "created_at", "last_seen"],
                            [[r["id"], r["username"] or "", r["first_name"] or "", r["balance"], r["banned"], r["spent"], iso(r["created_at"]), iso(r["last_seen"])] for r in rows])
    rows = await db.all("SELECT s.*, p.name panel FROM vpn_services s LEFT JOIN vpn_panels p ON p.id=s.panel_id WHERE s.status!='deleted' ORDER BY s.id LIMIT 200000")
    return csv_response(f"vpn-services-{stamp}.csv", ["id", "user_id", "panel", "name", "title", "status", "expires_at", "total_gb", "used_gb", "is_test", "created_at"],
                        [[r["id"], r["user_id"], r["panel"] or "", r["name"], r["title"], r["status"], iso(r["expires_at"]), round((r["total_bytes"] or 0) / 1024 ** 3, 2),
                          round((r["used_bytes"] or 0) / 1024 ** 3, 2), r["is_test"], iso(r["created_at"])] for r in rows])
