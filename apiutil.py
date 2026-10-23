"""ابزارهای عمومی برای فراخوانی APIهای دلخواه ادمین (نرخ ارز، قیمت فراگمنت، سرویس‌دهنده‌ی ارسال)."""
import asyncio
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit
from decimal import Decimal, InvalidOperation

from aiohttp import ClientSession, ClientTimeout

from settings_schema import render_template

_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬", "01234567890123456789.,")
_TOKEN = re.compile(r"\[(\d+)\]|\[([^\]=\[]+)=([^\]]*)\]|([^.\[\]]+)")


def D(v, default=0) -> Decimal:
    """تبدیل مقاوم به Decimal (ارقام فارسی، جداکننده هزارگان، رشته‌های خالی)."""
    if isinstance(v, Decimal):
        return v
    try:
        s = str(v).translate(_DIG).replace(",", "").strip()
        return Decimal(s) if s else Decimal(default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def jpath(obj, path: str):
    """مسیر ساده در JSON: a.b.0.c یا [0].price یا a[1].b ؛ مسیر خالی = خود شیء.
    انتخاب از لیست بر اساس مقدار یک فیلد: [symbol=TON_IRT].price (بدون حساسیت به بزرگی/کوچکی حروف)."""
    cur = obj
    for m in _TOKEN.finditer((path or "").strip()):
        idx, selk, selv, key = m.group(1), m.group(2), m.group(3), m.group(4)
        if selk is not None:
            if not isinstance(cur, list):
                raise KeyError(f"[{selk}={selv}] needs a list")
            want = selv.strip().lower()
            for el in cur:
                if isinstance(el, dict) and str(el.get(selk.strip(), "")).strip().lower() == want:
                    cur = el
                    break
            else:
                raise KeyError(f"no item with {selk}={selv}")
        elif idx is not None or (key is not None and key.isdigit() and isinstance(cur, list)):
            i = int(idx if idx is not None else key)
            if not isinstance(cur, list) or i >= len(cur):
                raise KeyError(f"index {i}")
            cur = cur[i]
        else:
            if not isinstance(cur, dict) or key not in cur:
                raise KeyError(key)
            cur = cur[key]
    return cur


def parse_headers(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    d = json.loads(text)
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


class BlockedURL(ValueError):
    pass


async def check_public_url(url: str):
    """جلوگیری از SSRF: فقط http(s) و فقط مقصد عمومی (نه localhost، شبکه‌ی داخلی یا متادیتای ابری).
    اگر واقعاً به سرویس داخلی نیاز دارید، در .env مقدار ALLOW_PRIVATE_API=1 بگذارید."""
    if os.getenv("ALLOW_PRIVATE_API", "").strip().lower() in ("1", "true", "yes"):
        return
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BlockedURL("URL must start with http:// or https://")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as e:
        raise BlockedURL(f"cannot resolve host: {e}")
    for inf in infos:
        ip = ipaddress.ip_address(inf[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise BlockedURL("blocked: the address points to a private/internal network")


async def call_api(http: ClientSession, url: str, method="GET", headers="", body="", timeout=15, vars=None):
    """درخواست HTTP؛ خروجی: (status, parsed|text, raw_text). متغیرهای {qty} {months} {username} ... در url و body جایگزین می‌شوند."""
    vars = vars or {}
    url = render_template(url, vars)
    hdr = parse_headers(render_template(headers, vars) if headers else "")
    kw = {"headers": hdr, "timeout": ClientTimeout(total=max(float(timeout), 3))}
    method = (method or "GET").upper()
    if method == "POST" and body.strip():
        data = json.loads(render_template(body, vars))
        kw["json"] = data
    await check_public_url(url)
    kw["allow_redirects"] = False          # ریدایرکت می‌تواند به شبکه‌ی داخلی برود
    async with http.request(method, url, **kw) as r:
        raw = await r.text()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        return r.status, parsed, raw


def snippet(raw: str, n=300) -> str:
    raw = (raw or "").strip().replace("\n", " ")
    return raw if len(raw) <= n else raw[:n] + "…"
