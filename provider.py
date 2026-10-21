"""تحویل خودکار از طریق API سرویس‌دهنده (ارسال استارز/پریمیوم) + موجودی حساب سرویس‌دهنده.

Bot API تلگرام ارسال «استارز» به کاربران دیگر را ندارد؛ پس برای تحویل خودکار استارز باید یک سرویس‌دهنده/ابزار
(که با موجودی پیش‌پرداخت شما کار می‌کند) از طریق API وصل شود. تنظیمات کاملاً عمومی است: آدرس، متد، هدرها،
قالب بدنه با متغیرهای {username} {user_id} {qty} {months} {order_id} و شرط موفقیت پاسخ.
نتیجه سه‌حالته است تا ارسال تکراری رخ ندهد:
  ok      — سرویس‌دهنده صراحتاً موفقیت را اعلام کرد
  fail    — خطای قطعی (مثلاً 4xx یا پاسخ ناموفق)؛ ارسال انجام نشده
  unknown — مهلت/خطای 5xx/قطع ارتباط؛ ممکن است ارسال شده باشد ← تحویل دستی/بررسی ادمین (تلاش مجدد خودکار نمی‌شود)
"""
import asyncio
import logging
from decimal import Decimal

import marketapp
from apiutil import D, call_api, jpath, snippet

log = logging.getLogger("provider")

_FALSY = {"", "false", "0", "no", "error", "fail", "failed", "none", "null"}


def provider_enabled(s: dict, kind: str) -> bool:
    if kind == "stars":
        m = s.get("deliver_stars_mode")
        return (m == "api" and bool(s.get("prov_stars_url"))) or (m == "marketapp" and marketapp.ready_for_delivery(s))
    if kind == "premium":
        m = s.get("deliver_premium_mode")
        return (m == "api" and bool(s.get("prov_premium_url"))) or (m == "marketapp" and marketapp.ready_for_delivery(s))
    return False


def build_vars(order: dict) -> dict:
    return {"username": (order.get("target_username") or "").lstrip("@"), "user_id": order.get("target_id") or "",
            "qty": order["qty"], "months": order["qty"], "order_id": order["id"]}


def judge(status: int, parsed, okpath: str, okvalue: str):
    """(نتیجه ok|fail|unknown, توضیح)"""
    if status >= 500:
        return "unknown", f"HTTP {status}"
    if status >= 400:
        return "fail", f"HTTP {status}"
    if okpath:
        try:
            v = jpath(parsed, okpath)
        except (KeyError, TypeError):
            return "fail", f"path '{okpath}' not found in response"
        if okvalue:
            return ("ok", "") if str(v).strip().lower() == okvalue.strip().lower() else ("fail", f"{okpath}={v}")
        return ("ok", "") if str(v).strip().lower() not in _FALSY else ("fail", f"{okpath}={v}")
    return "ok", ""


def _find_ref(parsed):
    if isinstance(parsed, dict):
        for k in ("id", "order_id", "orderId", "tx", "tx_id", "transaction_id", "ref", "reference"):
            if k in parsed and isinstance(parsed[k], (str, int)):
                return str(parsed[k])
        for v in parsed.values():
            if isinstance(v, dict):
                r = _find_ref(v)
                if r:
                    return r
    return ""


async def deliver(shop, kind: str, order: dict):
    """kind: stars | premium. خروجی: (result, info, ref)"""
    s = await shop.settings()
    if s.get(f"deliver_{kind}_mode") == "marketapp":
        return await marketapp.deliver(shop, kind, order)
    p = f"prov_{kind}_"
    vars_ = build_vars(order)
    if not vars_["username"] and not vars_["user_id"]:
        return "fail", "recipient missing", ""
    try:
        status, parsed, raw = await call_api(shop.http, s[p + "url"], s.get(p + "method", "POST"), s.get(p + "headers", ""),
                                             s.get(p + "body", ""), D(s.get("prov_timeout"), 30), vars_)
    except (asyncio.TimeoutError, OSError) as e:
        return "unknown", f"{type(e).__name__}: {str(e)[:120]}", ""
    except ValueError as e:      # JSON قالب نامعتبر؛ هنوز چیزی ارسال نشده
        return "fail", f"bad template: {e}", ""
    except Exception as e:  # noqa: BLE001  خطای شبکه‌ی غیرمنتظره: نتیجه نامشخص
        log.exception("provider call error")
        return "unknown", f"{type(e).__name__}: {str(e)[:120]}", ""
    res, why = judge(status, parsed, s.get(p + "okpath", ""), s.get(p + "okvalue", ""))
    ref = _find_ref(parsed)
    info = why or snippet(raw, 160)
    return res, info, ref


async def get_balance(shop):
    """موجودی حساب سرویس‌دهنده (Decimal) یا None."""
    s = await shop.settings()
    if not s.get("prov_balance_url"):
        return None
    try:
        status, parsed, _ = await call_api(shop.http, s["prov_balance_url"], "GET", s.get("prov_balance_headers", ""), timeout=15)
        if status >= 400:
            return None
        v = D(jpath(parsed, s.get("prov_balance_path", "")), -1)
        return v if v >= 0 else None
    except Exception as e:  # noqa: BLE001
        log.warning("provider balance failed: %s", e)
        return None


def fmt_balance(v: Decimal | None) -> str:
    return "—" if v is None else f"{v:,.2f}".rstrip("0").rstrip(".")
