"""مینی‌اپ تلگرام (Telegram Mini App) برای مشتریان و ادمین‌ها.

- صفحه‌ی برنامه: GET /app  (فایل miniapp.html؛ متن‌ها با t("متن فارسی") و ترجمه‌ی سمت سرور)
- API: POST /app/api/<action>  با هدر X-Init-Data (initData تلگرام؛ اعتبارسنجی HMAC با توکن ربات)
- ادمین: POST /app/api/admin_login  ← کوکی نشست پنل وب را می‌گذارد و کاربر وارد پنل کامل مدیریت می‌شود.
همه‌ی منطق (قیمت، تخفیف، پرداخت، تحویل) همان توابع Shop است که ربات هم استفاده می‌کند."""
import base64
import hashlib
import io
import hmac
import json
import logging
import os
import re
import time
from urllib.parse import parse_qsl

from aiogram.types import BufferedInputFile
from aiohttp import web

import miniapp_ui
from i18n import CATALOG, SRC_LANG
from service import KIND_FA, METHOD_FA, PAY_STATUS_FA, STATUS_FA, Shop, normalize_url
from ui import fmt
from vpn_service import STATUS_FA as VPN_STATUS_FA
from vpn_service import VpnError, bar, days_left, fmt_gb
from webkit import set_session

log = logging.getLogger("miniapp")

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniapp.html")
INITDATA_TTL = 24 * 3600
USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")
MAX_RECEIPT = 6 * 1024 * 1024
_UI_RE = re.compile(r'\bt\("((?:[^"\\]|\\.)*)"\s*[,)]')
_hits: dict[int, list[float]] = {}


def ui_strings() -> list[str]:
    """همه‌ی متن‌های t("…") فایل miniapp.html (کلید ترجمه = متن فارسی)."""
    try:
        with open(HTML_PATH, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return []
    return sorted({json.loads('"' + m + '"') for m in _UI_RE.findall(src)})


def verify_init_data(init_data: str, bot_token: str, now: float | None = None):
    """اعتبارسنجی initData طبق مستندات تلگرام. خروجی: dict پارامترها (user به‌صورت dict) یا None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", "")
    if not got:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(got, want):
        return None
    try:
        if (now or time.time()) - int(pairs.get("auth_date", "0")) > INITDATA_TTL:
            return None
        pairs["user"] = json.loads(pairs.get("user", "null"))
    except ValueError:
        return None
    return pairs if isinstance(pairs["user"], dict) and pairs["user"].get("id") else None


class ApiError(Exception):
    def __init__(self, msg, status=400, code="", extra=None):
        super().__init__(msg)
        self.msg, self.status, self.code, self.extra = msg, status, code, extra or {}


def _rate_limited(uid: int) -> bool:
    now = time.time()
    if len(_hits) > 5000:
        for k in [k for k, v in _hits.items() if not v or now - v[-1] > 10]:
            _hits.pop(k, None)
    h = [t for t in _hits.get(uid, []) if now - t < 10]
    h.append(now)
    _hits[uid] = h
    return len(h) > 40


class Ctx:
    """اطلاعات کاربر در یک درخواست: شناسه، زبان، مترجم."""

    def __init__(self, shop: Shop, user: dict, lang: str, start_param: str = ""):
        self.shop, self.uid, self.tg, self.lang, self.start_param = shop, user["id"], user, lang, start_param
        self.tr = shop.tr_lang(lang)


async def _gate(shop: Shop, tg: dict, start_param: str) -> Ctx:
    """ثبت کاربر + زبان اولیه + معرف + مسدودی/تعمیر/عضویت اجباری (مثل GateMiddleware ربات)."""
    uid = tg["id"]
    is_new = await shop.db.upsert_user(uid, tg.get("username"), tg.get("first_name"))
    if is_new:
        guess = shop.resolve_bot_lang(tg.get("language_code"))
        if guess:
            await shop.set_user_lang(uid, guess)
        if start_param:
            await shop.register_referral(uid, start_param, True)
        from ui import esc
        uname = f"@{esc(tg['username'])}" if tg.get("username") else "—"
        await shop.group.post("users", f"👤 <b>کاربر جدید</b>\n{esc(tg.get('first_name') or '')} | {uname} | <code>{uid}</code>")
    lang = await shop.user_lang(uid)
    ctx = Ctx(shop, tg, lang, start_param)
    if shop.is_admin(uid):
        return ctx
    u = await shop.db.get_user(uid)
    if u and u["banned"]:
        raise ApiError(ctx.tr("⛔ حساب شما مسدود شده است.") + (f"\n{(u.get('ban_reason') or '').strip()}" if u.get("ban_reason") else ""), 403, "banned")
    s = await shop.settings()
    if s.get("maintenance") == "1":
        raise ApiError(shop.text("maintenance_text", lang) or ctx.tr("ربات موقتاً در دسترس نیست."), 503, "maintenance")
    if not await shop.is_member(uid):
        chans = await shop.join_buttons(uid, ctx.tr)
        raise ApiError(shop.text("join_text", lang) or ctx.tr("ابتدا در کانال عضو شوید."), 403, "join:", {"channels": chans})
    await shop.on_gate_pass(uid)
    return ctx


def _tr_text(ctx: Ctx, s: str) -> str:
    return ctx.tr(s)


def _otitle(ctx: Ctx, o: dict) -> str:
    return ctx.shop.product_title({"id": o.get("product_id"), "kind": o["kind"], "qty": o["qty"], "title": o["title"]}, ctx.lang)


def _money(ctx: Ctx, amount) -> dict:
    shop = ctx.shop
    return {"amount": int(amount), "text": shop.money(amount, ctx.tr), "short": shop.money(amount, ctx.tr, short=True),
            "crypto": shop.crypto_line(amount)}


def _order_dict(ctx: Ctx, o: dict) -> dict:
    who = "" if o["kind"] == "vpn" else ("@" + o["target_username"]) if o["target_username"] else (str(o["target_id"]) if o["target_id"] else "")
    disc = bool(o.get("discount_code") and o.get("orig_amount") and o["orig_amount"] != o["amount"])
    return {"id": o["id"], "kind": o["kind"], "title": _otitle(ctx, o), "target": who, "status": o["status"],
            "status_text": ctx.tr(STATUS_FA.get(o["status"], o["status"])), "created_at": o["created_at"],
            "price": _money(ctx, o["amount"]),
            "orig": _money(ctx, o["orig_amount"]) if disc else None, "code": o.get("discount_code") if disc else None}


async def _pay_methods(ctx: Ctx, order_id=None, allow_wallet=True) -> list[dict]:
    """روش‌های پرداخت فعال (همان شرط‌های pay_methods_kb ربات)."""
    shop = ctx.shop
    s = await shop.settings()
    out = []
    if allow_wallet and s["pm_wallet"] == "1":
        u = await shop.db.get_user(ctx.uid)
        out.append({"id": "wallet", "label": ctx.tr(METHOD_FA["wallet"]), "balance": u["balance"]})
    if s["pm_card"] == "1" and s["card_number"]:
        out.append({"id": "card", "label": ctx.tr(METHOD_FA["card"])})
    if s["pm_zarinpal"] == "1" and shop.gw("zarinpal_merchant") and shop.cfg.public_url:
        out.append({"id": "zarinpal", "label": ctx.tr(METHOD_FA["zarinpal"])})
    if s["pm_ton"] == "1" and shop.gw("ton_wallet"):
        out.append({"id": "ton", "label": ctx.tr(METHOD_FA["ton"])})
    if s["pm_usdt"] == "1" and shop.gw("usdt_wallet"):
        out.append({"id": "usdt", "label": ctx.tr(METHOD_FA["usdt"])})
    return out


async def _own_pending(ctx: Ctx, oid) -> dict:
    o = await ctx.shop.db.get_order(int(oid or 0))
    if not o or o["user_id"] != ctx.uid or o["status"] != "pending_payment":
        raise ApiError(ctx.tr("این سفارش دیگر قابل پرداخت نیست."), 409)
    return o


# ======================================================================= اکشن‌ها
async def a_init(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    s = await shop.settings()
    u = await shop.db.get_user(ctx.uid)
    lang_row = next((l for l in shop.langs if l["code"] == ctx.lang), None)
    langs = [{"code": l["code"], "name": l["name"], "flag": l["flag"], "rtl": bool(l["rtl"])} for l in shop.bot_langs()]
    items = []
    for it in await shop.db.list_menu(only_enabled=True):
        if it["action"] in ("url", "text"):
            items.append({"id": it["id"], "action": it["action"], "label": shop.menu_label(it, ctx.lang),
                          "url": normalize_url(it["extra"]) if it["action"] == "url" else "",
                          "text": shop.button_text(it, ctx.lang) if it["action"] == "text" else ""})
    kinds = []
    for k in ("stars", "premium", "gift"):
        if await shop.db.list_products(k):
            kinds.append(k)
    vpn_on = shop.vpn.enabled() and bool(await shop.db.list_vpn_plans(active_only=True))
    vpn_n = await shop.db.count_vpn_services(user_id=ctx.uid) if shop.vpn.enabled() else 0
    support = s["support_username"].lstrip("@")
    ui = {k: ctx.tr(k) for k in ui_strings()}
    mui = miniapp_ui.load(s)
    st = await shop.db.one("SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) done, "
                           "COALESCE(SUM(CASE WHEN status='done' THEN amount ELSE 0 END),0) spent FROM orders WHERE user_id=?", (ctx.uid,))
    inv = (await shop.db.one("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (ctx.uid,)))["c"]
    photo = str(ctx.tg.get("photo_url") or "")
    return {
        "mui": mui, "photo": photo if photo.startswith("https://") else "",
        "stats": {"orders": st["n"], "done": st["done"], "spent": _money(ctx, st["spent"]), "invited": inv},
        "user": {"id": ctx.uid, "name": tg_name(ctx.tg), "username": ctx.tg.get("username") or "", "balance": u["balance"],
                 "discount": u.get("personal_discount") or 0, "is_admin": shop.is_admin(ctx.uid)},
        "lang": ctx.lang, "rtl": bool(lang_row["rtl"]) if lang_row else True, "langs": langs if len(langs) > 1 else [],
        "ui": ui, "shop_name": s.get("shop_name") or "", "kinds": kinds,
        "kind_titles": {k: ctx.tr(KIND_TITLE[k]) for k in KIND_TITLE},
        "texts": {"welcome": (mui.get("welcome") or {}).get("t") or shop.text("welcome_text", ctx.lang), "rules": shop.text("rules_text", ctx.lang),
                  "support": shop.text("support_text", ctx.lang)},
        "support": support, "items": items, "bot": shop.bot_username,
        "vpn": {"on": vpn_on, "mine": vpn_n, "test": await shop.vpn.can_test(ctx.uid)},
        "limits": {"min_topup": int(float(s["min_topup"])), "max_topup": int(float(s["max_topup"]))},
        "pay_step": s.get("pay_step", "1") == "1",
        "methods": [m["id"] for m in await _pay_methods(ctx)],
    }


KIND_TITLE = {"stars": "⭐ خرید استارز", "premium": "👑 اشتراک پریمیوم", "gift": "🎁 گیفت تلگرام"}


def tg_name(tg: dict) -> str:
    return " ".join(x for x in (tg.get("first_name"), tg.get("last_name")) if x) or (tg.get("username") or str(tg["id"]))


async def a_catalog(ctx: Ctx, d: dict) -> dict:
    kind = d.get("kind")
    if kind not in ("stars", "premium", "gift"):
        raise ApiError("bad kind")
    shop = ctx.shop
    u = await shop.db.get_user(ctx.uid)
    prods = await shop.db.list_products(kind)
    return {"kind": kind, "discount": (u or {}).get("personal_discount") or 0,
            "products": [{"id": p["id"], "title": shop.product_title(p, ctx.lang), "price": _money(ctx, shop.price_u(p, u))} for p in prods]}


async def a_order_create(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    p = await shop.db.get_product(int(d.get("product_id") or 0))
    if not p or not p["active"]:
        raise ApiError(ctx.tr("این محصول در دسترس نیست."), 404)
    who = d.get("who")
    uname = None
    target_id = None
    if who == "me":
        if p["kind"] == "stars":
            if not ctx.tg.get("username"):
                raise ApiError(ctx.tr("شما یوزرنیم ندارید؛ یوزرنیم گیرنده را تایپ کنید."))
            uname = ctx.tg["username"]
        else:
            target_id = ctx.uid
    else:
        m = USERNAME_RE.match(str(d.get("username") or "").strip())
        if not m:
            raise ApiError(ctx.tr("یوزرنیم نامعتبر است. مثال: @username"))
        uname = m.group(1)
        if p["kind"] != "stars":
            # ارسال پریمیوم/گیفت با Bot API به شناسه‌ی عددی نیاز دارد؛ فقط کاربرانی که ربات را استارت کرده‌اند
            tu = await shop.db.get_user_by_username(uname)
            if not tu:
                raise ApiError(ctx.tr("این کاربر هنوز ربات را استارت نکرده است. از او بخواهید ربات را باز کند."))
            target_id = tu["id"]
    u = await shop.db.get_user(ctx.uid)
    price, cost = await shop.quote_order(p, u)
    oid = await shop.db.create_order(ctx.uid, p, target_id, uname, price, cost)
    return await a_order_get(ctx, {"order_id": oid})


async def a_order_get(ctx: Ctx, d: dict) -> dict:
    o = await ctx.shop.db.get_order(int(d.get("order_id") or 0))
    if not o or o["user_id"] != ctx.uid:
        raise ApiError(ctx.tr("سفارش پیدا نشد."), 404)
    out = _order_dict(ctx, o)
    out["methods"] = await _pay_methods(ctx, o["id"]) if o["status"] == "pending_payment" else []
    return out


async def a_discount(ctx: Ctx, d: dict) -> dict:
    o = await _own_pending(ctx, d.get("order_id"))
    ok, msg, _new = await ctx.shop.apply_discount(o["id"], ctx.uid, str(d.get("code") or ""), ctx.tr)
    if not ok:
        raise ApiError(msg)
    out = await a_order_get(ctx, {"order_id": o["id"]})
    out["message"] = msg
    return out


async def a_pay(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    method = d.get("method")
    if method not in METHOD_FA:
        raise ApiError(ctx.tr("روش پرداخت نامعتبر است."))
    s = await shop.settings()
    order = None
    if d.get("topup"):
        try:
            amount = int(d["topup"])
        except (TypeError, ValueError):
            raise ApiError(ctx.tr("مبلغ نامعتبر است."))
        lo, hi = int(float(s["min_topup"])), int(float(s["max_topup"]))
        if amount < lo or amount > hi:
            raise ApiError(ctx.tr("مبلغ نامعتبر است. بین {lo} تا {hi} تومان، فقط عدد.", lo=fmt(lo), hi=fmt(hi)))
        if method == "wallet":
            raise ApiError(ctx.tr("برای شارژ نمی‌توان از کیف پول استفاده کرد."))
    else:
        order = await _own_pending(ctx, d.get("order_id"))
        amount = order["amount"]
    if method not in [m["id"] for m in await _pay_methods(ctx, allow_wallet=order is not None)]:
        raise ApiError(ctx.tr("روش پرداخت نامعتبر است."))
    if method == "wallet":
        if not await shop.pay_with_wallet(order["id"], ctx.uid):
            raise ApiError(ctx.tr("موجودی کیف پول کافی نیست."))
        return {"done": True, "kind": order["kind"], "message": ctx.tr("✅ پرداخت از کیف پول انجام شد؛ سفارش #{oid} در حال تحویل است.", oid=order["id"])}
    try:
        pid, _text, kb = await shop.create_payment(method, ctx.uid, amount, order["id"] if order else None, ctx.tr)
    except ValueError as e:
        raise ApiError(str(e))
    p = await shop.db.get_payment(pid)
    out = {"payment_id": pid, "method": method, "amount": _money(ctx, amount), "code": f"P{pid}"}
    if method == "card":
        out.update(card=s["card_number"], owner=s["card_owner"] or "", note=s.get("card_note") or "")
    elif method == "zarinpal":
        out["url"] = f"https://payment.zarinpal.com/pg/StartPay/{p['ref']}"
    elif method == "ton":
        out.update(address=shop.gw("ton_wallet"), crypto=p["crypto_amount"], memo=f"P{pid}")
    elif method == "usdt":
        out.update(address=shop.gw("usdt_wallet"), crypto=round(p["crypto_amount"], 3))
    return out


async def a_receipt(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    p = await shop.db.get_payment(int(d.get("payment_id") or 0))
    if not p or p["user_id"] != ctx.uid or p["status"] != "pending" or p["method"] != "card":
        raise ApiError(ctx.tr("این پرداخت دیگر فعال نیست. دوباره از منو اقدام کنید."), 409)
    raw = str(d.get("image") or "")
    if "," in raw[:80]:
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw, validate=False)
    except ValueError:
        raise ApiError(ctx.tr("لطفاً فقط عکس رسید را ارسال کنید. برای خروج /start را بزنید."))
    if not blob or len(blob) > MAX_RECEIPT or not (blob[:3] == b"\xff\xd8\xff" or blob[:8] == b"\x89PNG\r\n\x1a\n" or blob[:4] == b"RIFF"):
        raise ApiError(ctx.tr("لطفاً فقط عکس رسید را ارسال کنید. برای خروج /start را بزنید."))
    # عکس را برای خود کاربر می‌فرستیم تا file_id معتبر بگیریم (همان رسیدی که در ربات می‌فرستاد)
    try:
        msg = await shop.bot.send_photo(ctx.uid, BufferedInputFile(blob, "receipt.jpg"), caption=ctx.tr("🧾 رسید دریافت شد. پس از بررسی ادمین، نتیجه همین‌جا اعلام می‌شود."))
    except Exception as e:  # noqa: BLE001
        log.warning("receipt upload failed: %s", e)
        raise ApiError(ctx.tr("لطفاً فقط عکس رسید را ارسال کنید. برای خروج /start را بزنید."))
    await shop.submit_receipt(p["id"], msg.photo[-1].file_id)
    return {"ok": True, "message": ctx.tr("🧾 رسید دریافت شد. پس از بررسی ادمین، نتیجه همین‌جا اعلام می‌شود.")}


async def a_check(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    p = await shop.db.get_payment(int(d.get("payment_id") or 0))
    if not p or p["user_id"] != ctx.uid:
        raise ApiError(ctx.tr("پرداخت پیدا نشد."), 404)
    if p["status"] == "pending":
        await shop.check_crypto()
        p = await shop.db.get_payment(p["id"])
    texts = {"confirmed": ctx.tr("✅ پرداخت شما تأیید شده است."), "pending": ctx.tr("⏳ هنوز پرداختی دریافت نشده است."),
             "expired": ctx.tr("⌛ مهلت این پرداخت تمام شده است."), "rejected": ctx.tr("❌ پرداخت رد شده است.")}
    return {"status": p["status"], "message": texts.get(p["status"], ctx.tr(PAY_STATUS_FA.get(p["status"], p["status"])))}


async def a_wallet(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    u = await shop.db.get_user(ctx.uid)
    hist = await shop.db.wallet_history(ctx.uid, limit=20)
    return {"balance": u["balance"], "methods": [m["id"] for m in await _pay_methods(ctx, allow_wallet=False)],
            "history": [{"amount": t["amount"], "reason": t["reason"].split(" ")[0].split(":")[0], "ts": t["created_at"]} for t in hist]}


async def a_orders(ctx: Ctx, d: dict) -> dict:
    rows = await ctx.shop.db.user_orders(ctx.uid, 30)
    return {"orders": [_order_dict(ctx, o) for o in rows]}


async def a_referral(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    s = await shop.settings()
    st = await shop.db.referral_stats(ctx.uid)
    return {"link": f"https://t.me/{shop.bot_username}?start=ref_{ctx.uid}", "percent": s.get("referral_percent", "0"),
            "bonus": int(float(s.get("referral_join_bonus") or 0)), "need_join": bool(shop._need_join()), "stats": st}


async def a_set_lang(ctx: Ctx, d: dict) -> dict:
    code = ctx.shop.resolve_bot_lang(str(d.get("lang") or ""))
    if not code:
        raise ApiError("bad lang")
    await ctx.shop.set_user_lang(ctx.uid, code)
    return {"ok": True}


# ======================================================================= VPN
def _vpn_err(ctx: Ctx, e: VpnError) -> ApiError:
    return ApiError(ctx.tr(str(e)))


def _plan_dict(ctx: Ctx, p: dict) -> dict:
    return {"id": p["id"], "title": p["title"], "days": p["days"], "gb": p["gb"], "panel": p.get("panel_name", ""), "panel_id": p["panel_id"],
            "price": _money(ctx, p["user_price"]), "note": p.get("note") or ""}


def _svc_dict(ctx: Ctx, sv: dict, full=False) -> dict:
    left = days_left(sv["expires_at"])
    out = {"id": sv["id"], "name": sv["name"], "title": sv["title"] or "", "status": sv["status"], "status_text": ctx.tr(VPN_STATUS_FA.get(sv["status"], sv["status"])),
           "used": sv["used_bytes"], "total": sv["total_bytes"], "used_text": fmt_gb(sv["used_bytes"]), "total_text": fmt_gb(sv["total_bytes"]) if sv["total_bytes"] else "",
           "percent": min(100, int(sv["used_bytes"] * 100 / sv["total_bytes"])) if sv["total_bytes"] else 0, "days_left": left,
           "expires_at": sv["expires_at"], "is_test": bool(sv["is_test"]), "panel_id": sv["panel_id"], "pending": sv["status"] == "pending_manual"}
    if full:
        out.update(sub_url=sv["sub_url"], links=json.loads(sv["links"] or "[]")[:6], help=(ctx.shop.s.get("vpn_help_text") or "").strip(),
                   can_addon=ctx.shop.s.get("vpn_allow_addon", "1") == "1" and not sv["is_test"], can_revoke=ctx.shop.s.get("vpn_allow_revoke", "1") == "1" and bool(sv["sub_url"]),
                   can_renew=not sv["is_test"] and sv["status"] != "pending_manual")
    return out


async def _own_svc(ctx: Ctx, sid) -> dict:
    sv = await ctx.shop.db.get_vpn_service(int(sid or 0))
    if not sv or sv["user_id"] != ctx.uid or sv["status"] == "deleted":
        raise ApiError(ctx.tr("سرویس پیدا نشد."), 404)
    return sv


async def a_vpn_plans(ctx: Ctx, d: dict) -> dict:
    shop = ctx.shop
    if not shop.vpn.enabled():
        raise ApiError(ctx.tr("فروش VPN فعال نیست."), 403)
    u = await shop.db.get_user(ctx.uid)
    plans = await shop.vpn.plans_for(u)
    return {"plans": [_plan_dict(ctx, p) for p in plans], "test": await shop.vpn.can_test(ctx.uid), "ask_name": (shop.s.get("vpn_name_mode") or "random") == "ask",
            "discount": (u or {}).get("personal_discount") or 0}


async def a_vpn_order(ctx: Ctx, d: dict) -> dict:
    try:
        oid = await ctx.shop.vpn.create_order(ctx.uid, int(d.get("plan_id") or 0), str(d.get("name") or "").strip().lower() or None)
    except VpnError as e:
        raise _vpn_err(ctx, e)
    return await a_order_get(ctx, {"order_id": oid})


async def a_vpn_test(ctx: Ctx, d: dict) -> dict:
    try:
        sid = await ctx.shop.vpn.create_test(ctx.uid)
    except VpnError as e:
        raise _vpn_err(ctx, e)
    sv = await ctx.shop.db.get_vpn_service(sid)
    try:
        await ctx.shop.vpn.send_service(ctx.uid, sv, new=True)
    except Exception:  # noqa: BLE001
        pass
    return {"id": sid}


async def a_vpn_services(ctx: Ctx, d: dict) -> dict:
    rows = await ctx.shop.db.list_vpn_services(user_id=ctx.uid, limit=50)
    return {"services": [_svc_dict(ctx, r) for r in rows], "test": await ctx.shop.vpn.can_test(ctx.uid)}


async def a_vpn_service(ctx: Ctx, d: dict) -> dict:
    sv = await _own_svc(ctx, d.get("id"))
    if d.get("refresh") or time.time() - (sv["last_sync"] or 0) > 60:
        try:
            sv = await ctx.shop.vpn.sync(sv, force=bool(d.get("refresh")))
        except Exception:  # noqa: BLE001
            if d.get("refresh"):
                raise ApiError(ctx.tr("ارتباط با سرور برقرار نشد؛ کمی بعد دوباره تلاش کنید."))
    out = _svc_dict(ctx, sv, full=True)
    data = sv["sub_url"] or (out["links"][0] if out["links"] else "")
    png = ctx.shop.vpn.qr_png(data) if data else None
    out["qr"] = "data:image/png;base64," + base64.b64encode(png).decode() if png else ""
    return out


async def a_vpn_renew_plans(ctx: Ctx, d: dict) -> dict:
    sv = await _own_svc(ctx, d.get("id"))
    u = await ctx.shop.db.get_user(ctx.uid)
    plans = [p for p in await ctx.shop.vpn.plans_for(u) if p["panel_id"] == sv["panel_id"]]
    return {"service": _svc_dict(ctx, sv), "plans": [_plan_dict(ctx, p) for p in plans]}


async def a_vpn_renew(ctx: Ctx, d: dict) -> dict:
    try:
        oid = await ctx.shop.vpn.renew_order(ctx.uid, int(d.get("id") or 0), int(d.get("plan_id") or 0))
    except VpnError as e:
        raise _vpn_err(ctx, e)
    return await a_order_get(ctx, {"order_id": oid})


async def a_vpn_addons(ctx: Ctx, d: dict) -> dict:
    sv = await _own_svc(ctx, d.get("id"))
    shop = ctx.shop
    if shop.s.get("vpn_allow_addon", "1") != "1":
        raise ApiError(ctx.tr("خرید بسته‌ی اضافه فعال نیست."))
    u = await shop.db.get_user(ctx.uid)
    return {"service": _svc_dict(ctx, sv), "addons": [{"id": a["id"], "title": a["title"], "gb": a["gb"], "days": a["days"], "price": _money(ctx, shop._personal(a["price"], u))}
                                                     for a in await shop.db.list_vpn_addons(active_only=True)]}


async def a_vpn_addon(ctx: Ctx, d: dict) -> dict:
    try:
        oid = await ctx.shop.vpn.addon_order(ctx.uid, int(d.get("id") or 0), int(d.get("addon_id") or 0))
    except VpnError as e:
        raise _vpn_err(ctx, e)
    return await a_order_get(ctx, {"order_id": oid})


async def a_vpn_revoke(ctx: Ctx, d: dict) -> dict:
    sv = await _own_svc(ctx, d.get("id"))
    if ctx.shop.s.get("vpn_allow_revoke", "1") != "1":
        raise ApiError(ctx.tr("این قابلیت غیرفعال است."), 403)
    ok, _m = await ctx.shop.vpn.admin_action(sv["id"], "revoke", actor=f"user:{ctx.uid}")
    if not ok:
        raise ApiError(ctx.tr("انجام نشد؛ بعداً دوباره تلاش کنید."))
    return await a_vpn_service(ctx, {"id": sv["id"]})


async def a_vpn_send(ctx: Ctx, d: dict) -> dict:
    """ارسال سرویس (لینک و QR) به چت ربات کاربر."""
    sv = await _own_svc(ctx, d.get("id"))
    if sv["status"] == "pending_manual":
        raise ApiError(ctx.tr("سرویس شما پس از آماده‌سازی توسط پشتیبانی ارسال می‌شود."))
    await ctx.shop.vpn.send_service(ctx.uid, sv, new=False)
    await ctx.shop.vpn.send_qr(ctx.uid, sv)
    return {"ok": True, "message": ctx.tr("در چت ربات ارسال شد.")}


ACTIONS = {"vpn_plans": a_vpn_plans, "vpn_order": a_vpn_order, "vpn_test": a_vpn_test, "vpn_services": a_vpn_services, "vpn_service": a_vpn_service,
           "vpn_renew_plans": a_vpn_renew_plans, "vpn_renew": a_vpn_renew, "vpn_addons": a_vpn_addons, "vpn_addon": a_vpn_addon,
           "vpn_revoke": a_vpn_revoke, "vpn_send": a_vpn_send,
           "init": a_init, "catalog": a_catalog, "order_create": a_order_create, "order_get": a_order_get, "discount": a_discount,
           "pay": a_pay, "receipt": a_receipt, "check": a_check, "wallet": a_wallet, "orders": a_orders,
           "referral": a_referral, "set_lang": a_set_lang}


# ======================================================================= HTTP
def _json(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


def _auth(shop: Shop, request: web.Request):
    ip = verify_init_data(request.headers.get("X-Init-Data", ""), shop.bot.token)
    if not ip:
        raise ApiError("auth", 401, "auth")
    return ip


async def api(request: web.Request):
    shop: Shop = request.app["shop"]
    action = request.match_info["action"]
    try:
        if shop.s.get("miniapp_enabled", "1") != "1":
            raise ApiError("disabled", 403, "disabled")
        ip = _auth(shop, request)
        uid = ip["user"]["id"]
        if _rate_limited(uid):
            raise ApiError("rate", 429)
        try:
            body = await request.json() if request.can_read_body else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if action == "admin_login":
            if not shop.is_admin(uid):
                raise ApiError("forbidden", 403)
            resp = _json({"ok": True, "url": "/"})
            set_session(resp, shop, uid)
            await shop.db.log(f"web:{uid}", "login_miniapp")
            return resp
        fn = ACTIONS.get(action)
        if fn is None:
            raise ApiError("unknown action", 404)
        ctx = await _gate(shop, ip["user"], ip.get("start_param", "") if action == "init" else "")
        return _json(await fn(ctx, body))
    except ApiError as e:
        return _json({"error": e.msg, "code": e.code, **e.extra}, e.status)
    except web.HTTPException:
        raise
    except Exception:  # noqa: BLE001
        log.exception("miniapp action %s failed", action)
        return _json({"error": "server error", "code": "server"}, 500)


async def page(request: web.Request):
    shop: Shop = request.app["shop"]
    if shop.s.get("miniapp_enabled", "1") != "1":
        raise web.HTTPNotFound()
    try:
        with open(HTML_PATH, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        raise web.HTTPNotFound()
    return web.Response(text=html, content_type="text/html")


_EMOJI: dict[str, tuple[str, bytes]] = {}


async def emoji(request: web.Request):
    """تصویر ایموجی پریمیوم برای نمایش در مینی‌اپ (فقط شناسه‌هایی که ادمین در تنظیمات مینی‌اپ گذاشته)."""
    shop: Shop = request.app["shop"]
    eid = request.match_info["eid"]
    if not eid.isdigit() or eid not in miniapp_ui.emoji_ids(shop.s):
        raise web.HTTPNotFound()
    hit = _EMOJI.get(eid)
    if hit is None:
        try:
            st = (await shop.bot.get_custom_emoji_stickers([eid]))[0]
            if st.is_animated:               # tgs را مرورگر نشان نمی‌دهد → پیش‌نمایش ثابت
                if not st.thumbnail:
                    raise ValueError("no thumbnail")
                fid, ctype = st.thumbnail.file_id, "image/webp"
            else:
                fid, ctype = st.file_id, ("video/webm" if st.is_video else "image/webp")
            f = await shop.bot.get_file(fid)
            buf = io.BytesIO()
            await shop.bot.download_file(f.file_path, buf)
            hit = (ctype, buf.getvalue())
        except Exception as e:  # noqa: BLE001
            log.warning("emoji %s failed: %s", eid, e)
            raise web.HTTPNotFound()
        if len(_EMOJI) > 120:
            _EMOJI.clear()
        _EMOJI[eid] = hit
    return web.Response(body=hit[1], content_type=hit[0], headers={"Cache-Control": "public, max-age=86400"})


def register(app: web.Application):
    app.router.add_get("/app", page)
    app.router.add_get("/app/emoji/{eid}", emoji)
    app.router.add_post("/app/api/{action}", api)
