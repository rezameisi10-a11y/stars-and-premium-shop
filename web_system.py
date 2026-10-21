"""پنل وب: زبان‌ها و ترجمه‌ها، ادمین‌ها با دسترسی، بکاپ و وارد کردن بکاپ."""
import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime

from aiohttp import web

import backup as bk
from i18n import CATALOG, N_, SRC_LANG, _, valid_lang_code
from service import PERM_PRESETS, PERMS, Shop, hash_pw
from ui import esc as e
from ui import fmt
from web_users import kv
from webkit import T, actor, badge, can, fdate, get, hidden, inp, layout, page_no, pager, pform, post, redirect, sel, tbl, to_int

RTL_CODES = {"ar", "fa", "he", "ur", "ps", "ku", "sd", "yi", "dv"}
TPAGE = 25


def pct_bar(done, total):
    p = done * 100 // total if total else 0
    return f"<div class='bar-h' title='{done}/{total}'><i style='width:{p}%'></i></div><span class='hint'>{p}% ({done}/{total})</span>"


# ============================================================ زبان‌ها
@get("/languages", "languages")
async def languages_page(request):
    shop: Shop = request.app["shop"]
    langs = await shop.db.list_languages()
    dflt, wdflt = shop.default_lang(), shop.s.get("web_default_lang") or SRC_LANG
    cells = []
    for l in langs:
        b, w = CATALOG.coverage("bot", l["code"]), CATALOG.coverage("web", l["code"])
        tags = ""
        if l["code"] == dflt:
            tags += badge(_("پیش‌فرض ربات"), "pri") + " "
        if l["code"] == wdflt:
            tags += badge(_("پیش‌فرض وب"), "pri")
        cells.append([e(l["flag"]), f"<b>{e(l['name'])}</b> <code>{e(l['code'])}</code>", badge(_("فعال"), "ok") if l["bot_enabled"] else badge(_("غیرفعال")),
                      badge(_("فعال"), "ok") if l["web_enabled"] else badge(_("غیرفعال")), _("بله") if l["rtl"] else _("خیر"),
                      pct_bar(*b), pct_bar(*w), tags, f"<a class='b sm' href='/languages/{e(l['code'])}'>{T('مدیریت و ترجمه')}</a>"])
    add = (f"<div class='card'><h2>➕ {T('افزودن زبان جدید')}</h2><p class='hint'>{T('هر زبانی را می‌توانید اضافه کنید؛ سپس همه‌ی متن‌های ربات و پنل وب را ترجمه کنید (دستی، یا با خروجی/ورود فایل JSON). تا ترجمه نشده باشد متن انگلیسی/فارسی نمایش داده می‌شود.')}</p>"
           + pform(request, "/languages/add",
                   f"<div class='row'>{inp('code', '', _('کد زبان (مثلاً pt)'), 'm', required=True)}{inp('name', '', _('نام زبان (به خود زبان)'), 'm', required=True)}"
                   f"{inp('flag', '', _('پرچم'), 's')}<label><input type='checkbox' name='rtl'> {T('راست‌به‌چپ')}</label>"
                   f"<label><input type='checkbox' name='bot' checked> {T('ربات')}</label><label><input type='checkbox' name='web' checked> {T('پنل وب')}</label>"
                   f"<button class='b ok'>{T('افزودن')}</button></div>", cls="") + "</div>")
    lst = f"<div class='card'><h2>{T('زبان‌ها')}</h2>{tbl(['', _('زبان'), _('ربات'), _('پنل وب'), _('راست‌به‌چپ'), _('ترجمه‌ی ربات'), _('ترجمه‌ی پنل وب'), '', ''], cells)}</div>"
    return await layout(request, _("زبان‌ها و ترجمه‌ها"), lst + add, "/languages")


@post("/languages/add", "languages")
async def language_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    code = str(d.get("code", "")).strip().lower()
    name = str(d.get("name", "")).strip()[:30]
    if not valid_lang_code(code) or not name:
        raise redirect("/languages", err=_("کد یا نام زبان نامعتبر است."))
    if await shop.db.get_language(code):
        raise redirect("/languages", err=_("این کد زبان از قبل وجود دارد."))
    rtl = 1 if "rtl" in d or code.split("-")[0] in RTL_CODES else 0
    await shop.db.upsert_language(code, name, str(d.get("flag", "")).strip()[:8], rtl, 1 if "bot" in d else 0, 1 if "web" in d else 0)
    await shop.reload_i18n()
    await shop.db.log(actor(request), "language_add", code)
    raise redirect(f"/languages/{code}", ok=_("زبان اضافه شد؛ حالا ترجمه‌ها را وارد کنید."))


async def _lang(request):
    shop: Shop = request.app["shop"]
    code = request.match_info["code"]
    l = await shop.db.get_language(code)
    if not l:
        raise redirect("/languages", err=_("زبان پیدا نشد."))
    return shop, code, l


@get("/languages/{code}", "languages")
async def language_detail(request):
    shop, code, l = await _lang(request)
    dflt, wdflt = shop.default_lang(), shop.s.get("web_default_lang") or SRC_LANG
    pre = f"/languages/{e(code)}"
    meta = (f"<div class='card'><h2>{e(l['flag'])} {e(l['name'])} <code>{e(code)}</code></h2>"
            + pform(request, pre + "/meta",
                    f"<div class='row'>{inp('name', l['name'], _('نام زبان'), 'm', required=True)}{inp('flag', l['flag'], _('پرچم'), 's')}"
                    f"<label><input type='checkbox' name='rtl' {'checked' if l['rtl'] else ''}> {T('راست‌به‌چپ')}</label>"
                    f"<label><input type='checkbox' name='bot' {'checked' if l['bot_enabled'] else ''}> {T('فعال در ربات')}</label>"
                    f"<label><input type='checkbox' name='web' {'checked' if l['web_enabled'] else ''}> {T('فعال در پنل وب')}</label>"
                    f"<button class='b pri'>{T('ذخیره')}</button></div>", cls="")
            + "<div class='row' style='margin-top:10px'>"
            + pform(request, pre + "/default", hidden("which", "bot") + f"<button class='b'>{T('⭐ پیش‌فرض ربات')}</button>" + (" " + badge(_("فعلی"), "ok") if code == dflt else ""))
            + pform(request, pre + "/default", hidden("which", "web") + f"<button class='b'>{T('⭐ پیش‌فرض پنل وب')}</button>" + (" " + badge(_("فعلی"), "ok") if code == wdflt else ""))
            + (pform(request, pre + "/delete", f"<button class='b err'>{T('🗑 حذف زبان')}</button>", confirm=_("این زبان و همه‌ی ترجمه‌هایش حذف شود؟")) if not l["builtin"] else "")
            + "</div></div>")
    cards = ""
    for scope, title in (("bot", _("متن‌های ربات")), ("web", _("متن‌های پنل وب"))):
        done, total = CATALOG.coverage(scope, code)
        cards += (f"<div class='card'><h2>{e(title)}</h2>{pct_bar(done, total)}"
                  f"<p class='row' style='margin-top:10px'><a class='b pri' href='{pre}/edit?scope={scope}&flt=missing'>📝 {T('ترجمه‌ی موارد باقی‌مانده')}</a>"
                  f"<a class='b' href='{pre}/edit?scope={scope}&flt=all'>{T('همه‌ی متن‌ها')}</a>"
                  f"<a class='b' href='{pre}/export?scope={scope}'>📤 {T('خروجی JSON')}</a></p></div>")
    if code != SRC_LANG:
        cards += (f"<div class='card'><h2>🛍 {T('محتوای سفارشی')}</h2><p class='hint'>{T('متن خوش‌آمد، قوانین، برچسب دکمه‌های منو و عنوان محصولاتی که خودتان نوشته‌اید را برای این زبان ترجمه کنید.')}</p>"
                  f"<p class='row'><a class='b pri' href='{pre}/content'>✏️ {T('ویرایش محتوای سفارشی')}</a></p></div>")
    imp = (f"<div class='card'><h2>📥 {T('ورود ترجمه (JSON)')}</h2><p class='hint'>{T('فایل خروجی را ترجمه کنید (کلیدها = متن اصلی، مقدار = ترجمه) و اینجا بارگذاری کنید. جاهای خالی {name} و تگ‌ها باید دست‌نخورده بمانند؛ مواردی که ناسازگار باشند رد می‌شوند.')}</p>"
           + pform(request, pre + "/import", f"{sel('scope', [('bot', _('ربات')), ('web', _('پنل وب'))])} <input type='file' name='file' accept='.json,application/json' required> <button class='b ok'>{T('بارگذاری')}</button>", cls="", multipart=True) + "</div>")
    return await layout(request, f"🌐 {l['name']}", meta + f"<div class='grid g2'>{cards}</div>" + imp, "/languages")


@post("/languages/{code}/meta", "languages")
async def language_meta(request):
    shop, code, l = await _lang(request)
    d = await request.post()
    name = str(d.get("name", "")).strip()[:30]
    if not name:
        raise redirect(f"/languages/{code}", err=_("نام زبان نامعتبر است."))
    bot_on, web_on = 1 if "bot" in d else 0, 1 if "web" in d else 0
    if (not bot_on and code == shop.default_lang()) or (not web_on and code == (shop.s.get("web_default_lang") or SRC_LANG)):
        raise redirect(f"/languages/{code}", err=_("زبان پیش‌فرض را نمی‌توان غیرفعال کرد؛ ابتدا پیش‌فرض دیگری انتخاب کنید."))
    await shop.db.upsert_language(code, name, str(d.get("flag", "")).strip()[:8], 1 if "rtl" in d else 0, bot_on, web_on)
    await shop.reload_i18n()
    shop._lang_cache.clear()
    await shop.db.log(actor(request), "language_meta", code)
    raise redirect(f"/languages/{code}", ok=_("ذخیره شد."))


@post("/languages/{code}/default", "languages")
async def language_default(request):
    shop, code, l = await _lang(request)
    d = await request.post()
    which = d.get("which")
    if which == "bot" and l["bot_enabled"]:
        await shop.set_setting("default_lang", code, actor(request))
    elif which == "web" and l["web_enabled"]:
        await shop.set_setting("web_default_lang", code, actor(request))
    else:
        raise redirect(f"/languages/{code}", err=_("ابتدا این زبان را برای همان بخش فعال کنید."))
    raise redirect(f"/languages/{code}", ok=_("ذخیره شد."))


@post("/languages/{code}/delete", "languages")
async def language_delete(request):
    shop, code, l = await _lang(request)
    if code in (shop.default_lang(), shop.s.get("web_default_lang")):
        raise redirect(f"/languages/{code}", err=_("زبان پیش‌فرض حذف نمی‌شود."))
    ok = await shop.db.delete_language(code)
    await shop.reload_i18n()
    shop._lang_cache.clear()
    await shop.db.log(actor(request), "language_delete", code)
    raise redirect("/languages", **({"ok": _("حذف شد.")} if ok else {"err": _("زبان‌های داخلی حذف نمی‌شوند.")}))


def src_list(scope):
    CATALOG.load()
    return CATALOG.sources.get(scope, [])


@get("/languages/{code}/export", "languages")
async def language_export(request):
    shop, code, l = await _lang(request)
    scope = "web" if request.query.get("scope") == "web" else "bot"
    srcs = src_list(scope)
    data = {s: ((CATALOG.over.get((scope, code), {}).get(s) if code == SRC_LANG else CATALOG.lookup(scope, code, s)) or "") for s in srcs}
    return web.Response(body=json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"), content_type="application/json",
                        headers={"Content-Disposition": f"attachment; filename={code}.{scope}.json"})


PH_RE = re.compile(r"\{\w+\}")
TAG_RE = re.compile(r"</?\w+")


def compatible(src: str, text: str) -> bool:
    return sorted(PH_RE.findall(src)) == sorted(PH_RE.findall(text)) and sorted(TAG_RE.findall(src)) == sorted(TAG_RE.findall(text))


@post("/languages/{code}/import", "languages")
async def language_import(request):
    shop, code, l = await _lang(request)
    d = await request.post()
    scope = "web" if d.get("scope") == "web" else "bot"
    f = d.get("file")
    if f is None or not hasattr(f, "file"):
        raise redirect(f"/languages/{code}", err=_("فایلی انتخاب نشده است."))
    raw = f.file.read(3 * 1024 * 1024 + 1)
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        raise redirect(f"/languages/{code}", err=_("فایل JSON معتبر نیست."))
    srcs = set(src_list(scope))
    ok_items, rejected = {}, 0
    for k, v in data.items():
        if not isinstance(v, str) or not isinstance(k, str) or (srcs and k not in srcs):
            rejected += 1
            continue
        if v.strip() and not compatible(k, v):
            rejected += 1
            continue
        ok_items[k] = v
    await shop.db.set_translations(scope, code, ok_items)
    await shop.reload_i18n()
    await shop.db.log(actor(request), "translations_import", f"{code}.{scope} ok={len(ok_items)} rejected={rejected}")
    raise redirect(f"/languages/{code}", ok=_("{ok} ترجمه ذخیره شد؛ {bad} مورد رد شد.", ok=len(ok_items), bad=rejected))


@get("/languages/{code}/edit", "languages")
async def language_edit(request):
    shop, code, l = await _lang(request)
    scope = "web" if request.query.get("scope") == "web" else "bot"
    flt = request.query.get("flt", "missing")
    q = request.query.get("q", "").strip().lower()
    p = page_no(request)
    srcs = src_list(scope)
    items = [(i, s) for i, s in enumerate(srcs)]
    cur = lambda s: (CATALOG.over.get((scope, code), {}).get(s) if code == SRC_LANG else CATALOG.lookup(scope, code, s)) or ""  # noqa: E731
    if flt == "missing" and code != SRC_LANG:
        items = [(i, s) for i, s in items if not cur(s)]
    if q:
        items = [(i, s) for i, s in items if q in s.lower() or q in cur(s).lower()]
    total = len(items)
    page_items = items[(p - 1) * TPAGE:p * TPAGE]
    rows = ""
    for i, s in page_items:
        rows += (f"<div class='card' style='padding:10px'><div class='hint'>#{i}</div><pre class='pv' style='max-height:none;direction:auto;text-align:start'>{e(s)}</pre>"
                 f"<input type='hidden' name='k{i}' value='{e(s)}'><textarea name='v{i}' style='min-height:54px' dir='auto'>{e(cur(s))}</textarea></div>")
    pre = f"/languages/{e(code)}"
    search = (f"<form class='row' method='get'><input type='hidden' name='scope' value='{scope}'>{sel('flt', [('missing', _('فقط ترجمه‌نشده‌ها')), ('all', _('همه'))], flt)}"
              f"{inp('q', q, _('جستجو'), 'l')}<button class='b pri'>{T('اعمال')}</button><span class='hint'>{T('{n} مورد', n=fmt(total))}</span></form>")
    form = pform(request, pre + "/save", hidden("scope", scope) + hidden("back", request.path_qs) + rows + f"<p><button class='b pri'>💾 {T('ذخیره‌ی این صفحه')}</button></p>", cls="") if rows else f"<p class='hint'>{T('موردی برای نمایش نیست.')}</p>"
    note = f"<p class='hint'>{T('جاهای خالی مثل {name} و تگ‌های HTML باید دقیقاً مثل متن اصلی بمانند. خالی گذاشتن = بازگشت به ترجمه‌ی پیش‌فرض.')}</p>"
    title = _("ترجمه") + f": {l['name']} — " + (_("ربات") if scope == "bot" else _("پنل وب"))
    return await layout(request, title, f"<div class='card'>{search}{note}</div>{form}<div class='card'>{pager(request, total, p, TPAGE)}</div>", "/languages")


@post("/languages/{code}/save", "languages")
async def language_save(request):
    shop, code, l = await _lang(request)
    d = await request.post()
    scope = "web" if d.get("scope") == "web" else "bot"
    items, bad = {}, []
    for key in d.keys():
        if key.startswith("k") and key[1:].isdigit():
            n = key[1:]
            src = str(d[key])
            val = str(d.get(f"v{n}", ""))
            if val.strip() and not compatible(src, val):
                bad.append(n)
                continue
            items[src] = val.strip()
    srcs = set(src_list(scope))
    items = {k: v for k, v in items.items() if not srcs or k in srcs}
    await shop.db.set_translations(scope, code, items)
    await shop.reload_i18n()
    await shop.db.log(actor(request), "translations_save", f"{code}.{scope} n={len(items)}")
    back = str(d.get("back", ""))
    dest = back if back.startswith(f"/languages/{code}/edit") else f"/languages/{code}"
    if bad:
        raise redirect(dest, err=_("{n} مورد به‌خاطر ناسازگاری جاهای خالی/تگ‌ها ذخیره نشد (#{ids}).", n=len(bad), ids=", ".join(bad[:10])))
    raise redirect(dest, ok=_("ذخیره شد."))


@get("/languages/{code}/content", "languages")
async def language_content(request):
    shop, code, l = await _lang(request)
    if code == SRC_LANG:
        raise redirect(f"/languages/{code}", err=_("زبان اصلی نیازی به ترجمه ندارد."))
    q = request.query.get("q", "").strip().lower()
    p = page_no(request)
    items = await shop.dynamic_items()
    ov = CATALOG.over.get(("bot", code), {})
    if q:
        items = [x for x in items if q in x[2].lower() or q in ov.get(x[0], "").lower()]
    total = len(items)
    rows = ""
    for n, (key, label, base) in enumerate(items[(p - 1) * TPAGE:p * TPAGE]):
        eff = shop.effective_content(key, base, code)
        rows += (f"<div class='card' style='padding:10px'><div class='hint'>{T(label)}</div>"
                 f"<pre class='pv' style='max-height:none;direction:auto;text-align:start'>{e(base)}</pre>"
                 f"<input type='hidden' name='k{n}' value='{e(key)}'>"
                 f"<textarea name='v{n}' style='min-height:54px' dir='auto' placeholder='{e(eff if eff != base else '')}'>{e(ov.get(key, ''))}</textarea></div>")
    pre = f"/languages/{e(code)}"
    search = (f"<form class='row' method='get'>{inp('q', q, _('جستجو'), 'l')}<button class='b pri'>{T('اعمال')}</button>"
              f"<span class='hint'>{T('{n} مورد', n=fmt(total))}</span></form>")
    note = f"<p class='hint'>{T('جاهای خالی مثل {name} و تگ‌های HTML باید دقیقاً مثل متن اصلی بمانند. خالی گذاشتن = بازگشت به ترجمه‌ی پیش‌فرض.')}</p>"
    form = (pform(request, pre + "/content/save", hidden("back", request.path_qs) + rows
                  + f"<p><button class='b pri'>💾 {T('ذخیره‌ی این صفحه')}</button></p>", cls="")
            if rows else f"<p class='hint'>{T('موردی برای نمایش نیست.')}</p>")
    title = _("محتوای سفارشی") + f": {l['name']}"
    return await layout(request, title, f"<div class='card'>{search}{note}</div>{form}<div class='card'>{pager(request, total, p, TPAGE)}</div>", "/languages")


@post("/languages/{code}/content/save", "languages")
async def language_content_save(request):
    shop, code, l = await _lang(request)
    if code == SRC_LANG:
        raise redirect(f"/languages/{code}", err=_("زبان اصلی نیازی به ترجمه ندارد."))
    d = await request.post()
    base_of = {k: b for k, _lab, b in await shop.dynamic_items()}
    items, bad = {}, []
    for key in d.keys():
        if key.startswith("k") and key[1:].isdigit():
            n = key[1:]
            k = str(d[key])
            val = str(d.get(f"v{n}", "")).strip()
            if k not in base_of:
                continue
            if val and not compatible(base_of[k], val):
                bad.append(n)
                continue
            items[k] = val
    await shop.db.set_translations("bot", code, items)
    await shop.reload_i18n()
    await shop.db.log(actor(request), "content_translations", f"{code} n={len(items)}")
    back = str(d.get("back", ""))
    dest = back if back.startswith(f"/languages/{code}/content") else f"/languages/{code}/content"
    if bad:
        raise redirect(dest, err=_("{n} مورد به‌خاطر ناسازگاری جاهای خالی/تگ‌ها ذخیره نشد (#{ids}).", n=len(bad), ids=", ".join(bad[:10])))
    raise redirect(dest, ok=_("ذخیره شد."))


# ============================================================ ادمین‌ها
def is_full(request) -> bool:
    return request.get("perms") == "*"


def perms_summary(perms) -> str:
    if perms == "*":
        return _("دسترسی کامل")
    return _("، ").join(_(PERMS[p]) for p in PERMS if p in perms) or _("بدون دسترسی")


@get("/admins", "admins")
async def admins_page(request):
    shop: Shop = request.app["shop"]
    rows = [[f"<code>{a}</code>", _("مالک (فایل .env)"), badge(_("دسترسی کامل"), "pri"), "—", "—"] for a in shop.cfg.admin_ids]
    for a in await shop.db.list_admins():
        if a["user_id"] in shop.cfg.admin_ids:
            continue
        perms = shop.admin_perms.get(a["user_id"], set())
        rows.append([f"<a href='/admins/{a['user_id']}'><code>{a['user_id']}</code></a>", e(a["name"] or "—"), e(perms_summary(perms)),
                     (badge(_("دارد"), "ok") + f" <code>{e(a['web_user'])}</code>") if a["web_user"] else "—",
                     f"<a class='b sm' href='/admins/{a['user_id']}'>{T('ویرایش')}</a>"])
    presets = [(k, _(v[0])) for k, v in PERM_PRESETS.items() if not (k == "full" and not is_full(request))]
    form = (f"<div class='card'><h2>➕ {T('افزودن ادمین')}</h2>"
            + pform(request, "/admins/add", f"<div class='row'>{inp('uid', '', _('آیدی عددی یا یوزرنیم'), 'm', required=True)}{sel('preset', presets, 'receipts')}<button class='b ok'>{T('افزودن')}</button></div>"
                    f"<p class='hint'>{T('کاربر باید قبلاً ربات را استارت کرده باشد. بعد از افزودن می‌توانید دسترسی‌ها را یکی‌یکی تغییر دهید یا برای او ورود به پنل وب بسازید.')}</p>", cls="") + "</div>")
    pdesc = "".join(f"<li><b>{T(v[0])}</b>: " + (T("دسترسی کامل") if v[1] == ["*"] else e(_("، ").join(_(PERMS[p]) for p in v[1]))) + "</li>" for v in PERM_PRESETS.values())
    info = f"<div class='card'><h2>{T('نقش‌های آماده')}</h2><ul class='hint'>{pdesc}</ul></div>"
    return await layout(request, _("ادمین‌ها و دسترسی‌ها"), form + f"<div class='card'>{tbl([_('آیدی'), _('نام / نوع'), _('دسترسی‌ها'), _('ورود وب'), ''], rows)}</div>" + info, "/admins")


async def resolve_user(shop: Shop, text: str):
    t = text.strip()
    return await shop.db.get_user(int(t)) if t.lstrip("-").isdigit() else await shop.db.get_user_by_username(t)


@post("/admins/add", "admins")
async def admin_add(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    u = await resolve_user(shop, str(d.get("uid", "")))
    preset = d.get("preset")
    if not u:
        raise redirect("/admins", err=_("کاربر پیدا نشد (باید ربات را استارت کرده باشد)."))
    if preset not in PERM_PRESETS or u["id"] in shop.cfg.admin_ids:
        raise redirect("/admins", err=_("نقش یا کاربر نامعتبر است."))
    perms = PERM_PRESETS[preset][1]
    if (perms == ["*"] or "admins" in perms) and not is_full(request):
        raise redirect("/admins", err=_("فقط مدیر کامل می‌تواند مدیر کامل بسازد."))
    val = "*" if perms == ["*"] else ",".join(perms)
    if not await shop.db.add_admin(u["id"], request["uid"] or 0, val, u["first_name"] or ""):
        await shop.db.update_admin(u["id"], perms=val)
    await shop.refresh_admins()
    await shop.db.log(actor(request), "admin_set", f"{u['id']} {preset}")
    await shop.say(u["id"], "👮 شما به‌عنوان ادمین ربات تعیین شدید. برای ورود به پنل /admin را بزنید.")
    raise redirect(f"/admins/{u['id']}", ok=_("ادمین افزوده شد."))


@get("/admins/{uid}", "admins")
async def admin_detail(request):
    shop: Shop = request.app["shop"]
    uid = to_int(request.match_info["uid"])
    a = await shop.db.get_admin(uid) if uid else None
    if not a or uid in shop.cfg.admin_ids:
        raise redirect("/admins", err=_("ادمین پیدا نشد."))
    perms = shop.admin_perms.get(uid, set())
    full = perms == "*"
    pre = f"/admins/{uid}"
    boxes = "".join(f"<label class='perm'><input type='checkbox' name='p_{k}' {'checked' if full or k in perms else ''} {'disabled' if (k == 'admins' and not is_full(request)) else ''}> {T(v)}</label>" for k, v in PERMS.items())
    presets = " ".join(pform(request, pre + "/preset", hidden("preset", k) + f"<button class='b sm'>{T(v[0])}</button>") for k, v in PERM_PRESETS.items() if not (k == "full" and not is_full(request)))
    edit = (f"<div class='card'><h2>👮 {e(a['name'] or uid)} <code>{uid}</code></h2>"
            + pform(request, pre + "/perms", f"<div class='row'>{inp('name', a['name'] or '', _('نام نمایشی'), 'm')}</div><h3>{T('دسترسی‌ها')}</h3>{'<p class=hint>' + T('این ادمین دسترسی کامل دارد؛ با ذخیره، دسترسی‌ها بر اساس تیک‌ها تنظیم می‌شود.') + '</p>' if full else ''}"
                    f"<div>{boxes}</div><p><button class='b pri'>{T('ذخیره')}</button></p>", cls="")
            + f"<h3>{T('اعمال نقش آماده')}</h3><div class='row'>{presets}</div></div>")
    web_ = (f"<div class='card'><h2>🖥 {T('ورود به پنل وب')}</h2><p class='hint'>{T('این ادمین با نام کاربری و رمز خودش وارد می‌شود و فقط بخش‌های مجاز را می‌بیند.')}</p>"
            + pform(request, pre + "/web", f"<div class='row'>{inp('web_user', a['web_user'] or '', _('نام کاربری'), 'm', required=True)}"
                    f"<input class='m' type='password' name='web_pw' placeholder='{T('رمز جدید (حداقل ۸ نویسه)')}' autocomplete='new-password'><button class='b pri'>{T('ذخیره')}</button></div>", cls="")
            + (pform(request, pre + "/webclear", f"<button class='b err'>{T('حذف ورود وب')}</button>", confirm=_("ورود وب این ادمین حذف شود؟")) if a["web_user"] else "") + "</div>")
    danger = (f"<div class='card'>" + pform(request, pre + "/remove", f"<button class='b err'>🗑 {T('حذف ادمین')}</button>", cls="", confirm=_("این ادمین حذف شود؟")) + "</div>")
    return await layout(request, _("ادمین {uid}", uid=uid), edit + web_ + danger, "/admins")


async def _adm(request):
    shop: Shop = request.app["shop"]
    uid = to_int(request.match_info["uid"])
    a = await shop.db.get_admin(uid) if uid else None
    if not a or uid in shop.cfg.admin_ids:
        raise redirect("/admins", err=_("ادمین پیدا نشد."))
    if shop.admin_perms.get(uid) == "*" and not is_full(request):
        raise redirect("/admins", err=_("تغییر مدیر کامل فقط توسط مدیر کامل ممکن است."))
    return shop, uid, a, await request.post()


@post("/admins/{uid}/perms", "admins")
async def admin_perms(request):
    shop, uid, a, d = await _adm(request)
    cur = shop.admin_perms.get(uid, set())
    perms = [k for k in PERMS if f"p_{k}" in d]
    if "admins" in perms and not is_full(request) and not (cur == "*" or "admins" in cur):
        raise redirect(f"/admins/{uid}", err=_("فقط مدیر کامل می‌تواند دسترسی مدیریت ادمین‌ها بدهد."))
    if "admins" not in perms and (cur == "*" or "admins" in cur) and not is_full(request):
        raise redirect(f"/admins/{uid}", err=_("فقط مدیر کامل می‌تواند این دسترسی را بگیرد."))
    await shop.db.update_admin(uid, perms=",".join(perms), name=str(d.get("name", "")).strip()[:40])
    await shop.refresh_admins()
    await shop.db.log(actor(request), "admin_perms", f"{uid} {','.join(perms)}")
    raise redirect(f"/admins/{uid}", ok=_("ذخیره شد."))


@post("/admins/{uid}/preset", "admins")
async def admin_preset(request):
    shop, uid, a, d = await _adm(request)
    key = d.get("preset")
    if key not in PERM_PRESETS:
        raise redirect(f"/admins/{uid}", err=_("نقش نامعتبر است."))
    perms = PERM_PRESETS[key][1]
    if (perms == ["*"] or "admins" in perms) and not is_full(request):
        raise redirect(f"/admins/{uid}", err=_("فقط مدیر کامل می‌تواند مدیر کامل بسازد."))
    await shop.db.update_admin(uid, perms="*" if perms == ["*"] else ",".join(perms))
    await shop.refresh_admins()
    await shop.db.log(actor(request), "admin_preset", f"{uid} {key}")
    raise redirect(f"/admins/{uid}", ok=_("نقش اعمال شد."))


@post("/admins/{uid}/web", "admins")
async def admin_web(request):
    shop, uid, a, d = await _adm(request)
    user = str(d.get("web_user", "")).strip()
    pw = str(d.get("web_pw", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,24}", user):
        raise redirect(f"/admins/{uid}", err=_("نام کاربری: حروف انگلیسی/عدد، ۳ تا ۲۴ نویسه."))
    other = await shop.db.get_admin_by_web_user(user)
    if (other and other["user_id"] != uid) or user.lower() == shop.cfg.web_user.lower():
        raise redirect(f"/admins/{uid}", err=_("این نام کاربری قبلاً استفاده شده است."))
    if pw:
        if len(pw) < 8:
            raise redirect(f"/admins/{uid}", err=_("رمز باید حداقل ۸ نویسه باشد."))
        await shop.db.set_admin_web(uid, user, hash_pw(pw))
    elif a["web_pw"]:
        await shop.db.set_admin_web(uid, user, a["web_pw"])
    else:
        raise redirect(f"/admins/{uid}", err=_("برای ورود جدید رمز را هم وارد کنید."))
    await shop.db.log(actor(request), "admin_web", str(uid))
    raise redirect(f"/admins/{uid}", ok=_("ذخیره شد."))


@post("/admins/{uid}/webclear", "admins")
async def admin_webclear(request):
    shop, uid, a, d = await _adm(request)
    await shop.db.set_admin_web(uid, None, None)
    await shop.db.log(actor(request), "admin_webclear", str(uid))
    raise redirect(f"/admins/{uid}", ok=_("حذف شد."))


@post("/admins/{uid}/remove", "admins")
async def admin_remove(request):
    shop, uid, a, d = await _adm(request)
    await shop.db.remove_admin(uid)
    await shop.refresh_admins()
    await shop.db.log(actor(request), "admin_remove", str(uid))
    raise redirect("/admins", ok=_("حذف شد."))


# ============================================================ بکاپ و وارد کردن بکاپ
PENDING: dict[str, dict] = {}
PENDING_TTL = 900


def _cleanup_pending():
    now = time.time()
    for tok in [t for t, v in PENDING.items() if v["exp"] < now]:
        try:
            os.remove(PENDING[tok]["path"])
        except OSError:
            pass
        PENDING.pop(tok, None)


def safety_files(shop: Shop):
    d = os.path.join(os.path.dirname(os.path.abspath(shop.db.path)), "backups")
    if not os.path.isdir(d):
        return d, []
    files = sorted((f for f in os.listdir(d) if f.startswith("pre-restore-") and f.endswith(".db")), reverse=True)
    return d, files[:10]


@get("/backup", "backup")
async def backup_page(request):
    shop: Shop = request.app["shop"]
    full = is_full(request)
    dl = (f"<div class='card'><h2>⬇️ {T('دانلود بکاپ')}</h2><p class='hint'>{T('بسته‌ی zip شامل دیتابیس کامل (کاربران، سفارش‌ها، تنظیمات، ترجمه‌ها، دکمه‌ها، کدهای تخفیف و ...) است و می‌تواند در «وارد کردن بکاپ» همین پنل (یا ربات) استفاده شود.')}</p>"
          f"<p class='row'><a class='b ok' href='/backup/download'>📦 {T('بکاپ کامل (zip)')}</a>"
          + (f"<a class='b' href='/backup/download?env=1' data-confirm-link=\"{e(_('این فایل شامل توکن ربات و رمزها است؛ امن نگه دارید. ادامه؟'))}\">🔐 {T('بکاپ با فایل .env (شامل توکن)')}</a>" if full else "") + "</p></div>")
    imp = (f"<div class='card'><h2>⬆️ {T('وارد کردن بکاپ')}</h2><p class='hint'>{T('فایل بکاپ (.zip، .db یا .tar.gz نصب‌کننده) را بارگذاری کنید. اول فایل بررسی و خلاصه‌اش نشان داده می‌شود؛ بعد از تأیید، دیتابیس فعلی جایگزین می‌شود و ربات با داده‌های آن بکاپ بالا می‌آید. قبل از جایگزینی یک بکاپ ایمنی از وضعیت فعلی گرفته می‌شود.')}</p>"
           + pform(request, "/backup/import", f"<input type='file' name='file' accept='.zip,.db,.gz,.tgz,.sqlite' required> <button class='b pri'>{T('بارگذاری و بررسی')}</button>", cls="", multipart=True) + "</div>")
    d, files = safety_files(shop)
    safety = ""
    if files and full:
        safety = (f"<div class='card'><h2>🛟 {T('بکاپ‌های ایمنی (قبل از هر بازگردانی)')}</h2>"
                  + tbl([_("فایل"), _("حجم"), _("زمان"), ""], [[f"<code>{e(f)}</code>", f"{os.path.getsize(os.path.join(d, f)) // 1024} KB",
                                                                 fdate(os.path.getmtime(os.path.join(d, f))), f"<a class='b sm' href='/backup/file/{e(f)}'>⬇️</a>"] for f in files]) + "</div>")
    return await layout(request, _("بکاپ و وارد کردن بکاپ"), dl + imp + safety, "/backup")


@get("/backup/download", "backup")
async def backup_download(request):
    shop: Shop = request.app["shop"]
    inc = request.query.get("env") == "1"
    if inc and not is_full(request):
        raise redirect("/backup", err=_("فقط مدیر کامل می‌تواند بکاپ شامل .env بگیرد."))
    data = await bk.make_bundle(shop, include_env=inc)
    await shop.db.log(actor(request), "backup", "env" if inc else "db")
    return web.Response(body=data, content_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename=starshop-backup-{datetime.now():%Y%m%d-%H%M}.zip"})


@get("/backup/file/{name}", "backup")
async def backup_file(request):
    shop: Shop = request.app["shop"]
    if not is_full(request):
        raise redirect("/backup", err=_("شما به این بخش دسترسی ندارید."))
    name = request.match_info["name"]
    d, files = safety_files(shop)
    if name not in files:
        raise web.HTTPNotFound()
    with open(os.path.join(d, name), "rb") as f:
        data = f.read()
    return web.Response(body=data, content_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename={name}"})


@post("/backup/import", "backup")
async def backup_import(request):
    shop: Shop = request.app["shop"]
    _cleanup_pending()
    d = await request.post()
    f = d.get("file")
    if f is None or not hasattr(f, "file"):
        raise redirect("/backup", err=_("فایلی انتخاب نشده است."))
    raw = f.file.read()
    try:
        db_bytes, env_text, meta = bk.extract_upload(raw)
        path, info = bk.stage_db(db_bytes)
    except ValueError as ex:
        raise redirect("/backup", err=_("فایل بکاپ معتبر نیست: {err}", err=str(ex)[:160]))
    tok = secrets.token_urlsafe(16)
    PENDING[tok] = {"path": path, "env": env_text, "info": info, "meta": meta, "exp": time.time() + PENDING_TTL, "uid": request["uid"]}
    await shop.db.log(actor(request), "backup_upload", f"users={info['users']} orders={info['orders']}")
    created = ""
    if meta and meta.get("created"):
        created = fdate(meta["created"]) if isinstance(meta["created"], (int, float)) else e(str(meta["created"]))
    envbox = ""
    if env_text:
        envbox = (f"<p><label><input type='checkbox' name='with_env' {'disabled' if not is_full(request) else ''}> {T('فایل .env داخل بکاپ هم جایگزین شود (توکن ربات و تنظیمات سرور؛ ربات بعد از آن خودکار راه‌اندازی مجدد می‌شود)')}</label></p>"
                  + ("" if is_full(request) else f"<p class='hint'>{T('فقط مدیر کامل می‌تواند .env را بازگردانی کند.')}</p>"))
    summary = kv([(T("نام فروشگاه"), e(info["shop_name"] or "—")), (T("کاربران"), fmt(info["users"])), (T("سفارش‌ها"), fmt(info["orders"])),
                  (T("پرداخت‌ها"), fmt(info["payments"])), (T("محصولات"), fmt(info["products"])), (T("کدهای تخفیف"), fmt(info["discounts"])),
                  (T("حجم"), f"{info['size'] // 1024} KB"), (T("زمان بکاپ"), created or "—")])
    body = (f"<div class='card'><h2>✅ {T('فایل بکاپ معتبر است')}</h2>{summary}"
            f"<div class='flash warn' style='margin-top:12px'>{T('هشدار: دیتابیس فعلی (کاربران، سفارش‌ها، تنظیمات ...) با این فایل جایگزین می‌شود. از وضعیت فعلی یک بکاپ ایمنی گرفته می‌شود.')}</div>"
            + pform(request, "/backup/apply", hidden("token", tok) + envbox + f"<p><button class='b err'>♻️ {T('جایگزین کن و بالا بیاور')}</button> <a class='b' href='/backup'>{T('انصراف')}</a></p>", cls="") + "</div>")
    return await layout(request, _("وارد کردن بکاپ"), body, "/backup")


@post("/backup/apply", "backup")
async def backup_apply(request):
    shop: Shop = request.app["shop"]
    d = await request.post()
    _cleanup_pending()
    item = PENDING.pop(str(d.get("token", "")), None)
    if not item or not os.path.exists(item["path"]):
        raise redirect("/backup", err=_("مهلت تأیید تمام شده؛ دوباره فایل را بارگذاری کنید."))
    with_env = "with_env" in d and is_full(request) and item["env"]
    try:
        res = await bk.apply_restore(shop, item["path"], item["env"] if with_env else None)
    except Exception as ex:  # noqa: BLE001
        raise redirect("/backup", err=_("بازگردانی ناموفق: {err}", err=str(ex)[:160]))
    await shop.db.log(actor(request), "restore", f"users={res['info']['users']} env={bool(with_env)}")
    if res["restart"]:
        asyncio.get_running_loop().call_later(2, shop.request_restart)
    note = _("ربات برای اعمال .env ظرف چند ثانیه دوباره راه‌اندازی می‌شود (اگر با سرویس systemd/Docker اجرا نمی‌شود، دستی اجرا کنید).") if res["restart"] else _("ربات همین حالا با داده‌های جدید کار می‌کند.")
    body = (f"<div class='card'><div class='flash ok'>✅ {T('بکاپ بازگردانی شد.')}</div><p>{e(note)}</p>"
            f"<p class='hint'>{T('بکاپ ایمنی')}: <code>{e(os.path.basename(res['safety']))}</code></p><p><a class='b pri' href='/'>{T('رفتن به داشبورد')}</a></p></div>")
    return await layout(request, _("وارد کردن بکاپ"), body, "/backup")
