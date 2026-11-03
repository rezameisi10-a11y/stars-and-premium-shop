"""وایرگارد (و هر پروتکل «فایلی») در سرویس‌های پاسارگارد: تبدیل لینک wireguard:// به فایل .conf برای اپ WireGuard.

پاسارگارد برای وایرگارد سه خروجی دارد: لینک wireguard://، QR و فایل .conf. ربات هنگام تحویل خودش تشخیص می‌دهد
که سرویس وایرگارد دارد و فایل + QR + راهنما می‌فرستد؛ نیازی به تنظیم دستی نیست."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

LRM = "⁦"
PDI = "⁩"


def is_wg_uri(s: str) -> bool:
    return (s or "").strip().lower().startswith(("wireguard://", "wg://"))


def looks_like_conf(text: str) -> bool:
    t = text or ""
    return "[Interface]" in t and "[Peer]" in t and "PrivateKey" in t


def _first(q: dict, *names) -> str:
    for n in names:
        v = q.get(n)
        if v and v[0]:
            return v[0]
    return ""


def parse_wg_uri(uri: str) -> dict | None:
    """wireguard://PRIVATEKEY@host:port?address=…&publickey=…&mtu=…#name → دیکشنری فیلدهای .conf؛ اگر کلیدهای لازم نبود None."""
    try:
        body = uri.strip().split("://", 1)[1]
        body, _, frag = body.partition("#")
        auth, _, query = body.partition("?")
        if "@" not in auth:
            return None
        priv, hostport = auth.rsplit("@", 1)          # کلید base64 ممکن است «/» یا «+» داشته باشد؛ پس با urlsplit جدا نمی‌کنیم
        priv = unquote(priv)
        hostport = hostport.rstrip("/")
        if hostport.startswith("["):                    # IPv6
            host, _, rest = hostport[1:].partition("]")
            port = int(rest.lstrip(":") or 51820)
        else:
            host, _, p = hostport.partition(":")
            port = int(p or 51820)
        q = {k.lower(): v for k, v in parse_qs(query, keep_blank_values=False).items()}
    except (ValueError, IndexError):
        return None
    pub = _first(q, "publickey", "peer_public_key", "peerpublickey", "public_key")
    addr = _first(q, "address", "ip", "addr", "localaddress")
    if not (priv and pub and host and addr):
        return None
    allowed = _first(q, "allowedips", "allowed_ips", "allowedip") or "0.0.0.0/0, ::/0"
    return {"private": priv, "address": addr.replace(" ", ""), "dns": _first(q, "dns"), "mtu": _first(q, "mtu"),
            "public": pub, "psk": _first(q, "presharedkey", "psk", "pre_shared_key"), "allowed": allowed,
            "endpoint": f"[{host}]:{port}" if ":" in host else f"{host}:{port}",
            "keepalive": _first(q, "keepalive", "persistentkeepalive", "persistent_keepalive"),
            "name": unquote(frag or "")}


def render_conf(d: dict) -> str:
    lines = ["[Interface]", f"PrivateKey = {d['private']}", f"Address = {d['address']}"]
    if d.get("dns"):
        lines.append(f"DNS = {d['dns']}")
    if d.get("mtu"):
        lines.append(f"MTU = {d['mtu']}")
    lines += ["", "[Peer]", f"PublicKey = {d['public']}"]
    if d.get("psk"):
        lines.append(f"PresharedKey = {d['psk']}")
    lines += [f"AllowedIPs = {d['allowed']}", f"Endpoint = {d['endpoint']}"]
    lines.append(f"PersistentKeepalive = {d.get('keepalive') or 25}")
    return "\n".join(lines) + "\n"


def conf_from_uri(uri: str) -> str | None:
    d = parse_wg_uri(uri)
    return render_conf(d) if d else None


def safe_filename(name: str, fallback="wireguard") -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._") or fallback
    return s[:40]


def guide_text(tr, name: str) -> str:
    """راهنمای ساده و مرتب (حروف انگلیسی بین جداکننده‌ی چپ‌به‌راست تا در متن فارسی به‌هم نریزد)."""
    return "\n".join([
        tr("📲 <b>راهنمای اتصال با WireGuard</b>"),
        "",
        tr("این سرویس از نوع {wg} است و با لینک اشتراک معمولی کار نمی‌کند؛ فایل بالا را فقط در برنامه‌ی {wg} باز کنید.", wg=f"{LRM}WireGuard{PDI}"),
        "",
        tr("۱) برنامه‌ی {wg} را نصب کنید (اندروید، آیفون، ویندوز و مک).", wg=f"{LRM}WireGuard{PDI}"),
        tr("۲) داخل برنامه روی دکمه‌ی {plus} بزنید.", plus=f"{LRM}+{PDI}"),
        tr("۳) یکی از دو راه را انتخاب کنید: «{a}» برای فایل بالا، یا «{b}» برای عکس QR.", a=f"{LRM}Import from file{PDI}", b=f"{LRM}Scan from QR code{PDI}"),
        tr("۴) یک نام دلخواه بدهید و سوییچ را روشن کنید."),
        "",
        tr("نکته: برای تغییر دستگاه، همین فایل را روی دستگاه جدید هم وارد کنید. اگر وصل نشد، یک بار «بروزرسانی» را بزنید و فایل تازه را بگیرید."),
    ])
