"""مدیریت کانال‌های عضویت اجباری در پنل وب: چند کانال، هرکدام با متن، رنگ، لینک دعوت و ایموجی پریمیوم دکمه‌ی خودش."""
from i18n import NW_, _
from service import Shop
from ui import esc as e
from webkit import T, actor, badge, hidden, inp, pform, post, redirect, sel, tbl

STYLES = [("", NW_("⚪ پیش‌فرض (آبی)")), ("primary", NW_("🔵 آبی")), ("success", NW_("🟢 سبز")), ("danger", NW_("🔴 قرمز"))]
BACK = "/settings?g=general"


def _style_sel(cur=""):
    return sel("s", [(k, _(v)) for k, v in STYLES], cur)


def force_card(request, shop: Shop) -> str:
    """کارت مدیریت کانال‌ها (زیر تنظیمات «عمومی»)."""
    items = shop.force_list()
    rows = []
    for i, ch in enumerate(items):
        edit = pform(request, f"/force/{i}/edit",
                     hidden("c", ch["c"]) + inp("t", ch["t"], _("متن دکمه"), "m") + " " + _style_sel(ch["s"]) + " "
                     + inp("l", ch["l"], _("لینک دعوت (اختیاری)"), "m") + " " + inp("e", ch["e"], _("ID ایموجی پریمیوم"), "s")
                     + f" <button class='b sm pri'>{T('ذخیره')}</button>", cls="inline")
        dele = pform(request, f"/force/{i}/delete", f"<button class='b sm err'>{T('حذف')}</button>", confirm=_("این کانال حذف شود؟"))
        rows.append([f"<code>{e(ch['c'])}</code>", edit, dele])
    table = tbl([_("کانال"), _("دکمه (متن · رنگ · لینک · ایموجی)"), ""], rows, _("هنوز کانالی اضافه نشده است."))
    add = pform(request, "/force/add",
                inp("c", "", "@mychannel", "m", required=True) + " " + inp("t", "", _("متن دکمه (مثلاً اطلاع‌رسانی)"), "m") + " " + _style_sel("")
                + " " + inp("l", "", _("لینک دعوت (برای کانال خصوصی)"), "m") + f" <button class='b pri'>➕ {T('افزودن کانال')}</button>", cls="inline")
    on = shop.s.get("force_join") == "1"
    status = badge(_("فعال"), "ok") if on else badge(_("غیرفعال (گزینه‌ی «عضویت اجباری در کانال» را روشن کنید)"), "warn")
    return (f"<div class='card'><h2>📢 {T('کانال‌های عضویت اجباری')} {status}</h2>"
            f"<p class='hint'>{T('کاربر باید در همه‌ی این کانال‌ها عضو باشد. برای هر کانال متن و رنگ دکمه‌ی جداگانه بگذارید. ربات باید ادمین هر کانال باشد.')}</p>"
            f"{table}<h3>{T('افزودن کانال')}</h3>{add}</div>")


def _read(f, old=None):
    return {"c": str(f.get("c", (old or {}).get("c", ""))).strip(), "t": str(f.get("t", "")).strip()[:40], "s": str(f.get("s", "")),
            "l": str(f.get("l", "")).strip(), "e": str(f.get("e", "")).strip()}


@post("/force/add", "settings")
async def force_add(request):
    shop: Shop = request.app["shop"]
    items = shop.force_list() + [_read(await request.post())]
    ok, err = await shop.set_force_list(items, actor(request))
    if not ok:
        raise redirect(BACK, err=_(err))
    raise redirect(BACK, ok=_("کانال اضافه شد."))


@post("/force/{i}/edit", "settings")
async def force_edit(request):
    shop: Shop = request.app["shop"]
    items = shop.force_list()
    i = int(request.match_info["i"]) if request.match_info["i"].isdigit() else -1
    if not 0 <= i < len(items):
        raise redirect(BACK, err=_("کانال پیدا نشد."))
    items[i] = _read(await request.post(), items[i])
    ok, err = await shop.set_force_list(items, actor(request))
    if not ok:
        raise redirect(BACK, err=_(err))
    raise redirect(BACK, ok=_("ذخیره شد."))


@post("/force/{i}/delete", "settings")
async def force_delete(request):
    shop: Shop = request.app["shop"]
    items = shop.force_list()
    i = int(request.match_info["i"]) if request.match_info["i"].isdigit() else -1
    if not 0 <= i < len(items):
        raise redirect(BACK, err=_("کانال پیدا نشد."))
    del items[i]
    ok, err = await shop.set_force_list(items, actor(request))
    if not ok:
        raise redirect(BACK, err=_(err))
    raise redirect(BACK, ok=_("حذف شد."))
