"""ویرایش متن‌ها و ایموجی پریمیوم مینی‌اپ در پنل وب (تنظیمات ← مینی‌اپ تلگرام)."""
import miniapp_ui as mu
from i18n import _
from service import Shop
from ui import esc as e
from webkit import T, actor, pform, post, redirect

BACK = "/settings?g=miniapp"


LAYOUT_UI_KEY = {"stars": "kind_stars", "premium": "kind_premium", "gift": "kind_gift", "vpn": "kind_vpn", "agent": "kind_agent",
                 "vpn_my": "vpn_my", "agent_my": "agent_my"}
DEFAULT_COLOR = "#6d7cff"


def _color_cell(key, v) -> str:
    c = v.get("c") or ""
    return (f"<input type='color' name='c_{e(key)}' value='{e(c or DEFAULT_COLOR)}' oninput=\"this.form['cu_{e(key)}'].checked=true\"> "
            f"<label class='hint'><input type='checkbox' name='cu_{e(key)}' value='1' {'checked' if c else ''}> {T('رنگ سفارشی')}</label>")


def layout_card(request, shop: Shop) -> str:
    order, hidden = mu.load_layout(shop.s)
    cur = mu.load(shop.s)
    meta = {k: (t, ic, kind) for k, t, ic, kind in mu.LAYOUT_ITEMS}
    rows = ""
    for i, k in enumerate(order, 1):
        t, ic, kind = meta[k]
        uk = LAYOUT_UI_KEY[k]
        v = cur.get(uk, {"t": "", "e": "", "c": ""})
        rows += (f"<tr><td>{e(ic)} <b>{e(_(t))}</b><br><span class='hint'>{T('کادر بزرگ در صفحه‌ی خانه و محصولات') if kind == 'tile' else T('ردیف زیر کادرها در صفحه‌ی خانه')}</span></td>"
                 f"<td><input class='s' type='number' name='lo_{e(k)}' value='{i}' min='1' max='99'></td>"
                 f"<td><label><input type='checkbox' name='lv_{e(k)}' value='1' {'' if k in hidden else 'checked'}> {T('نمایش')}</label></td>"
                 f"<td><input class='m' name='t_{e(uk)}' maxlength='{mu.MAX_TEXT}' value='{e(v['t'])}' placeholder='{e(_(t))}'></td>"
                 f"<td><input class='s' name='e_{e(uk)}' value='{e(v['e'])}' placeholder='ID' inputmode='numeric'></td>"
                 f"<td>{_color_cell(uk, v)}</td></tr>")
    body = (f"<div class='tw'><table><thead><tr><th>{T('دکمه')}</th><th>{T('ترتیب')}</th><th>{T('وضعیت')}</th><th>{T('نوشته‌ی دکمه')}</th>"
            f"<th>{T('ID ایموجی پریمیوم')}</th><th>{T('رنگ')}</th></tr></thead><tbody>{rows}</tbody></table></div>"
            f"<p><button class='b pri'>{T('ذخیره')}</button></p>")
    reset = pform(request, "/miniapp-layout/reset", f"<button class='b err'>{T('بازگشت به پیش‌فرض')}</button>")
    help_ = (f"<ol class='hint'><li>{T('ترتیب: به هر دکمه یک عدد بدهید؛ عدد کوچک‌تر بالاتر نمایش داده می‌شود (۱ = اولین دکمه).')}</li>"
             f"<li>{T('نمایش: تیک را بردارید تا دکمه در مینی‌اپ دیده نشود.')}</li>"
             f"<li>{T('نوشته‌ی دکمه: خالی = متن پیش‌فرض (با زبان کاربر ترجمه می‌شود)؛ اگر بنویسید برای همه‌ی زبان‌ها همان نمایش داده می‌شود.')}</li>"
             f"<li>{T('ایموجی پریمیوم: شناسه‌ی عددی ایموجی را از ربات @GetEmojiIDBot بگیرید و اینجا بگذارید؛ خالی = ایموجی معمولی.')}</li>"
             f"<li>{T('رنگ: تیک «رنگ سفارشی» را بزنید و رنگ را انتخاب کنید؛ بدون تیک رنگ پیش‌فرض می‌ماند.')}</li>"
             f"<li>{T('دکمه‌ای که محصولی برایش ثبت نشده یا فروشش خاموش است، حتی با تیک نمایش، دیده نمی‌شود.')}</li></ol>")
    return (f"<div class='card'><h2>🧩 {T('دکمه‌های اصلی مینی‌اپ (چیدمان، ایموجی، رنگ)')}</h2>{help_}"
            f"{pform(request, '/miniapp-layout/save', body, cls='')}{reset}</div>")


def ui_card(request, shop: Shop) -> str:
    cur = mu.load(shop.s)
    skip = set(LAYOUT_UI_KEY.values())
    groups: dict[str, list] = {}
    for key, d, ic, area, grp in mu.ELEMENTS:
        if key in skip:
            continue
        v = cur.get(key, {"t": "", "e": "", "c": ""})
        field = (f"<textarea name='t_{e(key)}' rows='3' maxlength='{mu.MAX_AREA}' placeholder='{e(_(d))}'>{e(v['t'])}</textarea>" if area else
                 f"<input class='m' name='t_{e(key)}' maxlength='{mu.MAX_TEXT}' value='{e(v['t'])}' placeholder='{e(_(d))}'>")
        emo = f"<input class='s' name='e_{e(key)}' value='{e(v['e'])}' placeholder='ID' inputmode='numeric'>" if key != "welcome" else ""
        col = _color_cell(key, v) if key != "welcome" else ""
        groups.setdefault(grp, []).append(f"<tr><td>{e(ic)} <code>{e(key)}</code></td><td>{field}</td><td>{emo}</td><td>{col}</td></tr>")
    body = ""
    for grp, rows in groups.items():
        body += (f"<h3>{e(_(grp))}</h3><div class='tw'><table><thead><tr><th>{T('بخش')}</th><th>{T('متن (خالی = پیش‌فرض)')}</th>"
                 f"<th>{T('ID ایموجی پریمیوم')}</th><th>{T('رنگ')}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")
    form = pform(request, "/miniapp-ui/save", body + f"<p><button class='b pri'>{T('ذخیره')}</button></p>", cls="")
    reset = pform(request, "/miniapp-ui/reset", f"<button class='b err'>{T('بازگشت به پیش‌فرض')}</button>", confirm=_("همه‌ی متن‌ها و ایموجی‌های مینی‌اپ به پیش‌فرض برگردد؟"))
    return layout_card(request, shop) + (f"<div class='card'><h2>🎨 {T('متن، ایموجی و رنگ بقیه‌ی بخش‌های مینی‌اپ')}</h2>"
            f"<p class='hint'>{T('کادرهای رنگی آمار، دکمه‌های خرید و افزایش موجودی، منوی پایین و مشخصات. هر چه اینجا بنویسید جای متن پیش‌فرض می‌نشیند (برای همه‌ی زبان‌ها)؛ خالی بگذارید تا متن پیش‌فرض با زبان کاربر نمایش داده شود. ID ایموجی پریمیوم را از @GetEmojiIDBot بگیرید؛ اگر خالی باشد ایموجی معمولی نشان داده می‌شود. برای تغییر رنگ، تیک «رنگ سفارشی» را بزنید.')}</p>"
            f"{form}{reset}</div>")


@post("/miniapp-layout/save", "settings")
async def layout_save(request):
    shop: Shop = request.app["shop"]
    form = await request.post()
    await shop.set_setting("miniapp_layout", mu.clean_layout(form), actor(request))
    await shop.set_setting("miniapp_ui", mu.clean(form, mu.load(shop.s)), actor(request))
    raise redirect(BACK, ok=_("ذخیره شد."))


@post("/miniapp-layout/reset", "settings")
async def layout_reset(request):
    shop: Shop = request.app["shop"]
    await shop.set_setting("miniapp_layout", "", actor(request))
    raise redirect(BACK, ok=_("به پیش‌فرض برگشت."))


@post("/miniapp-ui/save", "settings")
async def ui_save(request):
    shop: Shop = request.app["shop"]
    await shop.set_setting("miniapp_ui", mu.clean(await request.post(), mu.load(shop.s)), actor(request))
    raise redirect(BACK, ok=_("ذخیره شد."))


@post("/miniapp-ui/reset", "settings")
async def ui_reset(request):
    shop: Shop = request.app["shop"]
    await shop.set_setting("miniapp_ui", "", actor(request))
    raise redirect(BACK, ok=_("به پیش‌فرض برگشت."))
