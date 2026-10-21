"""Marketapp API (https://api.marketapp.org) — قیمت و خرید خودکار استارز/پریمیوم از Fragment.

مستندات: Swagger «Marketapp API 1.0.0» (بخش fragment، پلن رایگان). احراز هویت: هدر `Authorization: <token>` (بدون Bearer).
همه‌ی درخواست‌ها POST/JSON هستند:
  /v1/fragment/stars/price/       {"quantity": N}                     → {"ton":x,"gram":x,"usd":x}
  /v1/fragment/stars/recipient/   {"username": "..."}                  → بررسی وجود گیرنده
  /v1/fragment/stars/buy/         {"username","quantity","currency"}   → {"transaction":{"validUntil","messages":[{address,amount,payload,stateInit}]}}
  /v1/fragment/premium/price/     (بدون بدنه)                          → {"months3":{ton,gram,usd},"months6":…,"months12":…}
  /v1/fragment/premium/recipient/ , /v1/fragment/premium/buy/ {"username","months","currency"}
تراکنش خرید را باید خودمان امضا و به بلاک‌چین TON بفرستیم (tonwallet.py). اگر ارز USDT باشد باید تراکنش از همان کیف پولی
فرستاده شود که توکن API با آن ساخته شده است.

ایمنی: (۱) فقط وقتی تراکنش امضا می‌شود که مبلغ آن از سقف ایمنی و از قیمت اعلام‌شده‌ی API بیشتر نباشد؛ (۲) ارسال‌ها پشت
یک قفل سریالی انجام می‌شوند تا seqno کیف پول تداخل نکند؛ (۳) نتیجه‌ی نامشخص هیچ‌وقت خودکار دوباره ارسال نمی‌شود.
"""
import asyncio
import json
import logging
from decimal import Decimal

from aiohttp import ClientTimeout

import tonwallet
from apiutil import D, snippet

log = logging.getLogger("marketapp")

DEFAULT_BASE = "https://api.marketapp.org"
PATHS = {
    "stars_price": "/v1/fragment/stars/price/", "stars_recipient": "/v1/fragment/stars/recipient/", "stars_buy": "/v1/fragment/stars/buy/",
    "premium_price": "/v1/fragment/premium/price/", "premium_recipient": "/v1/fragment/premium/recipient/", "premium_buy": "/v1/fragment/premium/buy/",
}
MIN_STARS = 50          # حداقل تعداد ستاره در Fragment
_lock = asyncio.Lock()  # ارسال‌های تراکنش پشت‌سرهم (seqno)


class MappError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def configured(s: dict) -> bool:
    return bool((s.get("mapp_token") or "").strip())


def wallet_configured(s: dict) -> bool:
    return bool((s.get("mapp_wallet_mnemonic") or "").strip())


def ready_for_delivery(s: dict) -> bool:
    return configured(s) and wallet_configured(s)


async def _post(shop, s: dict, path: str, body=None, timeout=25):
    """خروجی: (status, parsed|text, raw). خطای شبکه → MappError(status=None)."""
    if not configured(s):
        raise MappError("Marketapp API token is not set")
    base = (s.get("mapp_base_url") or DEFAULT_BASE).rstrip("/")
    kw = {"headers": {"Authorization": s["mapp_token"].strip(), "Accept": "application/json"},
          "timeout": ClientTimeout(total=timeout)}
    if body is not None:
        kw["json"] = body
    try:
        async with shop.http.post(base + path, **kw) as r:
            raw = await r.text()
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            return r.status, parsed, raw
    except (asyncio.TimeoutError, OSError) as e:
        raise MappError(f"network: {type(e).__name__}") from e


def _err_text(status, parsed, raw) -> str:
    if isinstance(parsed, dict):
        d = parsed.get("detail") or parsed.get("message") or parsed.get("error")
        if isinstance(d, list) and d and isinstance(d[0], dict):
            d = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg', '')}" for e in d[:3])
        if d:
            return f"HTTP {status}: {str(d)[:200]}"
    return f"HTTP {status}: {snippet(raw, 160)}"


def _price_obj(d) -> dict:
    if not isinstance(d, dict):
        raise MappError("unexpected price response")
    out = {k: D(d.get(k), -1) for k in ("ton", "gram", "usd")}
    if out["usd"] <= 0 and out["ton"] <= 0:
        raise MappError("price fields missing/zero")
    return out


async def stars_price(shop, s: dict, qty: int) -> dict:
    """{ton, gram, usd} برای qty ستاره (Decimal)."""
    status, parsed, raw = await _post(shop, s, PATHS["stars_price"], {"quantity": int(qty)})
    if status >= 400:
        raise MappError(_err_text(status, parsed, raw), status)
    return _price_obj(parsed)


async def premium_prices(shop, s: dict) -> dict:
    """{3: {...}, 6: {...}, 12: {...}}"""
    status, parsed, raw = await _post(shop, s, PATHS["premium_price"])
    if status >= 400:
        raise MappError(_err_text(status, parsed, raw), status)
    out = {}
    for m in (3, 6, 12):
        try:
            out[m] = _price_obj(parsed[f"months{m}"])
        except (KeyError, TypeError, MappError):
            continue
    if not out:
        raise MappError("premium prices missing in response")
    return out


async def check_recipient(shop, s: dict, kind: str, username: str, qty: int = 0):
    """(ok, توضیح). ok=False یعنی گیرنده پیدا نشد/نامعتبر است (هنوز هیچ پولی جابه‌جا نشده)."""
    path = PATHS["stars_recipient" if kind == "stars" else "premium_recipient"]
    body = {"username": username.lstrip("@")}
    for _try in range(2):
        status, parsed, raw = await _post(shop, s, path, body)
        if status == 422 and _try == 0 and isinstance(parsed, dict):
            # فیلدهای اجباری دیگر (quantity/months) را بر اساس پیام اعتبارسنجی اضافه می‌کنیم
            need = {str(e.get("loc", [""])[-1]) for e in parsed.get("detail", []) if isinstance(e, dict)}
            extra = {}
            if "quantity" in need:
                extra["quantity"] = max(int(qty or 0), MIN_STARS)
            if "months" in need:
                extra["months"] = int(qty or 3)
            if "currency" in need:
                extra["currency"] = s.get("mapp_currency", "GRAM")
            if extra:
                body = {**body, **extra}
                continue
        break
    if status == 404:
        return False, "recipient not found"
    if status >= 400:
        if 400 <= status < 500:
            return False, _err_text(status, parsed, raw)
        raise MappError(_err_text(status, parsed, raw), status)
    return True, snippet(raw, 160)


async def buy(shop, s: dict, kind: str, username: str, qty: int) -> dict:
    """تراکنشی که باید به بلاک‌چین فرستاد (هنوز پولی کم نشده است)."""
    cur = s.get("mapp_currency", "GRAM")
    if kind == "stars":
        body = {"username": username.lstrip("@"), "quantity": int(qty), "currency": cur}
    else:
        body = {"username": username.lstrip("@"), "months": int(qty), "currency": cur}
    status, parsed, raw = await _post(shop, s, PATHS["stars_buy" if kind == "stars" else "premium_buy"], body, timeout=40)
    if status >= 400:
        raise MappError(_err_text(status, parsed, raw), status)
    tx = parsed.get("transaction") if isinstance(parsed, dict) else None
    if not isinstance(tx, dict):
        raise MappError("buy response has no transaction")
    return tx


def tx_summary(tx: dict) -> dict:
    vu, msgs = tonwallet.parse_transaction(tx)
    return {"valid_until": vu, "messages": len(msgs), "total_ton": Decimal(tonwallet.total_nano(msgs)) / Decimal(tonwallet.NANO),
            "to": [m["address"] for m in msgs]}


def _guard(s: dict, quote: dict | None, total_ton: Decimal):
    """None = مجاز؛ وگرنه متن دلیل رد."""
    cap = D(s.get("mapp_max_ton"), 0)
    if cap > 0 and total_ton > cap:
        return f"tx amount {total_ton:.3f} TON > safety cap {cap} TON"
    cur = (s.get("mapp_currency") or "GRAM").upper()
    if quote and cur in ("TON", "GRAM"):
        ref = max(quote.get("ton", Decimal(0)), quote.get("gram", Decimal(0)))
        if ref > 0 and total_ton > ref * Decimal("1.3") + Decimal("0.5"):
            return f"tx amount {total_ton:.3f} TON is much higher than quoted price {ref:.3f}"
    return None


async def deliver(shop, kind: str, order: dict):
    """تحویل سفارش استارز/پریمیوم با Marketapp + امضای تراکنش. خروجی: (ok|fail|unknown, توضیح, ref)."""
    s = await shop.settings()
    if not ready_for_delivery(s):
        return "fail", "Marketapp token or wallet mnemonic is not configured", ""
    if not tonwallet.AVAILABLE:
        return "fail", "pytoniq-core is not installed (pip install pytoniq-core)", ""
    username = (order.get("target_username") or "").lstrip("@")
    if not username:
        return "fail", "recipient username missing (Marketapp needs a @username)", ""
    qty = int(order["qty"])
    if kind == "stars" and qty < MIN_STARS:
        return "fail", f"Fragment minimum is {MIN_STARS} stars", ""
    try:
        if s.get("mapp_check_recipient", "1") == "1":
            ok, why = await check_recipient(shop, s, kind, username, qty)
            if not ok:
                return "fail", f"recipient check: {why}", ""
        quote = None
        try:
            quote = await stars_price(shop, s, qty) if kind == "stars" else (await premium_prices(shop, s)).get(qty)
        except MappError:
            quote = None
        tx = await buy(shop, s, kind, username, qty)
        summ = tx_summary(tx)
        why = _guard(s, quote, summ["total_ton"])
        if why:
            return "fail", f"safety guard: {why}", ""
    except MappError as e:
        return "fail", f"marketapp: {e}"[:250], ""        # تا اینجا پولی خرج نشده
    except tonwallet.WalletError as e:
        return "fail", f"bad transaction: {e}"[:250], ""
    async with _lock:
        res, info, ref = await tonwallet.send_transaction(shop, s, tx)
    return res, info, ref


# ---------- ابزار تست برای پنل ----------
async def test_all(shop, s: dict, qty: int = 50) -> dict:
    """نتیجه‌ی تست قیمت استارز/پریمیوم (بدون هزینه). خروجی dict با کلیدهای stars, premium (هرکدام ok/error/data)."""
    out = {"stars": {"ok": False}, "premium": {"ok": False}}
    try:
        out["stars"] = {"ok": True, "data": await stars_price(shop, s, qty), "qty": qty}
    except MappError as e:
        out["stars"] = {"ok": False, "error": str(e)}
    try:
        out["premium"] = {"ok": True, "data": await premium_prices(shop, s)}
    except MappError as e:
        out["premium"] = {"ok": False, "error": str(e)}
    return out


async def dry_run(shop, s: dict, kind: str, username: str, qty: int) -> dict:
    """گرفتن تراکنش خرید بدون امضا/ارسال (هیچ پولی خرج نمی‌شود) و ارزیابی نگهبان‌های ایمنی."""
    out = {"ok": False}
    try:
        ok, why = await check_recipient(shop, s, kind, username, qty)
        out["recipient"] = {"ok": ok, "info": why}
        quote = await stars_price(shop, s, qty) if kind == "stars" else (await premium_prices(shop, s)).get(qty)
        out["quote"] = quote
        tx = await buy(shop, s, kind, username, qty)
        out["tx"] = tx_summary(tx)
        out["guard"] = _guard(s, quote, out["tx"]["total_ton"])
        out["ok"] = bool(ok) and not out["guard"]
    except MappError as e:
        out["error"] = str(e)
    except tonwallet.WalletError as e:
        out["error"] = f"bad transaction: {e}"
    return out
