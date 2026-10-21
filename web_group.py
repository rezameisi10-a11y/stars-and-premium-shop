"""عملیات گروه گزارش‌ها در پنل وب (ساخت تاپیک، گزارش و بکاپ همین حالا، پیام آزمایشی، قطع اتصال)."""
from i18n import _
from service import Shop
from webkit import actor, post, redirect


@post("/group/{act}", "settings")
async def group_action(request):
    shop: Shop = request.app["shop"]
    act = request.match_info["act"]
    g = shop.group
    back = "/settings?g=group"
    if act == "off":
        await g.disconnect()
        await shop.db.log(actor(request), "group_off", "")
        raise redirect(back, ok=_("ارسال به گروه خاموش شد."))
    if g.chat_id() is None:
        raise redirect(back, err=_("هنوز گروهی متصل نشده است؛ ربات را به گروه اضافه و داخل گروه /connect بفرستید."))
    if act == "topics":
        res = await g.ensure_topics()
        if res["error"]:
            raise redirect(back, err=_("خطا: {e}", e=res["error"][:150]))
        raise redirect(back, ok=_("تاپیک‌ها آماده است.") if res["forum"] else _("گروه تاپیک ندارد؛ همه‌چیز در چت اصلی ارسال می‌شود."))
    if not g.enabled():
        raise redirect(back, err=_("ارسال به گروه خاموش است؛ گزینه‌ی «فعال بودن ارسال به گروه» را روشن کنید."))
    if act == "test":
        ok = await g.post("alerts", "🧪 پیام آزمایشی ربات — اگر این را می‌بینید اتصال درست است.") or await g.post("reports", "🧪 پیام آزمایشی ربات.")
    elif act == "report":
        ok = await g.send_report()
    elif act == "backup":
        ok = await g.send_backup()
    else:
        raise redirect(back, err=_("عملیات نامعتبر."))
    await shop.db.log(actor(request), f"group_{act}", "ok" if ok else "fail")
    raise redirect(back, ok=_("ارسال شد.") if ok else _("ارسال نشد؛ دسترسی ربات در گروه را بررسی کنید."))
