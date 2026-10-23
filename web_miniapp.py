"""ویرایش متن‌ها و ایموجی پریمیوم مینی‌اپ در پنل وب (تنظیمات ← مینی‌اپ تلگرام)."""
import miniapp_ui as mu
from i18n import _
from service import Shop
from ui import esc as e
from webkit import T, actor, pform, post, redirect

BACK = "/settings?g=miniapp"


def ui_card(request, shop: Shop) -> str:
    cur = mu.load(shop.s)
    groups: dict[str, list] = {}
    for key, d, ic, area, grp in mu.ELEMENTS:
        v = cur.get(key, {"t": "", "e": ""})
        field = (f"<textarea name='t_{e(key)}' rows='3' maxlength='{mu.MAX_AREA}' placeholder='{e(_(d))}'>{e(v['t'])}</textarea>" if area else
                 f"<input class='m' name='t_{e(key)}' maxlength='{mu.MAX_TEXT}' value='{e(v['t'])}' placeholder='{e(_(d))}'>")
        emo = f"<input class='s' name='e_{e(key)}' value='{e(v['e'])}' placeholder='ID' inputmode='numeric'>" if key != "welcome" else ""
        groups.setdefault(grp, []).append(f"<tr><td>{e(ic)} <code>{e(key)}</code></td><td>{field}</td><td>{emo}</td></tr>")
    body = ""
    for grp, rows in groups.items():
        body += (f"<h3>{e(_(grp))}</h3><div class='tw'><table><thead><tr><th>{T('بخش')}</th><th>{T('متن (خالی = پیش‌فرض)')}</th>"
                 f"<th>{T('ID ایموجی پریمیوم')}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")
    form = pform(request, "/miniapp-ui/save", body + f"<p><button class='b pri'>{T('ذخیره')}</button></p>", cls="")
    reset = pform(request, "/miniapp-ui/reset", f"<button class='b err'>{T('بازگشت به پیش‌فرض')}</button>", confirm=_("همه‌ی متن‌ها و ایموجی‌های مینی‌اپ به پیش‌فرض برگردد؟"))
    return (f"<div class='card'><h2>🎨 {T('متن‌ها و ایموجی‌های مینی‌اپ')}</h2>"
            f"<p class='hint'>{T('هر متنی که اینجا بنویسید جای متن پیش‌فرض می‌نشیند (برای همه‌ی زبان‌ها). خالی بگذارید تا متن پیش‌فرض با زبان کاربر نمایش داده شود. ID ایموجی پریمیوم را از ربات‌هایی مثل @GetEmojiIDBot بگیرید؛ اگر خالی باشد ایموجی معمولی نشان داده می‌شود.')}</p>"
            f"{form}{reset}</div>")


@post("/miniapp-ui/save", "settings")
async def ui_save(request):
    shop: Shop = request.app["shop"]
    await shop.set_setting("miniapp_ui", mu.clean(await request.post()), actor(request))
    raise redirect(BACK, ok=_("ذخیره شد."))


@post("/miniapp-ui/reset", "settings")
async def ui_reset(request):
    shop: Shop = request.app["shop"]
    await shop.set_setting("miniapp_ui", "", actor(request))
    raise redirect(BACK, ok=_("به پیش‌فرض برگشت."))
