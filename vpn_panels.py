"""درایورهای پنل‌های VPN: 3x-ui (ثنایی) و x-ui قدیمی (علیرضا)، مرزبان، پاسارگاد، مرزنشین، هیدیفای و «فروش دستی».

هر درایور همین رابط را دارد (همه async):
    test() -> dict            بررسی اتصال/ورود + نسخه
    options() -> list[dict]   اینباندها/گروه‌ها/سرویس‌های پنل برای انتخاب ادمین: [{id, name}]
    create(name, days, gb, note, tg_id, limit_ip) -> (PanelUser, meta)
    info(name, meta) -> PanelUser
    renew(name, meta, days, gb, stack) -> PanelUser      تمدید: ریست مصرف + حجم و زمان جدید
    add_traffic(name, meta, gb) / add_days(name, meta, days)
    reset_usage(name, meta) / set_enabled(name, meta, on) / delete(name, meta)
    revoke(name, meta) -> (sub_url, meta)                لینک اشتراک جدید
meta یک dict کوچک است که همراه سرویس در دیتابیس ذخیره می‌شود (مثلاً شناسه‌ی کلاینت در 3x-ui).
days=0 یعنی بدون انقضا و gb=0 یعنی حجم نامحدود.

نکته: APIها از روی مستندات عمومی پنل‌ها نوشته شده‌اند؛ دکمه‌ی «تست اتصال» در پنل وب برای هر پنل ثبت‌شده، درستی را نشان می‌دهد."""
import base64
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit

import aiohttp

GB = 1024 ** 3
TIMEOUT = aiohttp.ClientTimeout(total=25)


class PanelError(Exception):
    """خطای پنل (پیام برای ادمین؛ برای کاربر نشان داده نمی‌شود)."""


@dataclass
class PanelUser:
    username: str
    status: str = "active"          # active | disabled | limited | expired | on_hold
    used: int = 0                   # بایت
    total: int = 0                  # بایت؛ ۰ = نامحدود
    expire: int = 0                 # unix؛ ۰ = بدون انقضا
    sub_url: str = ""
    links: list = field(default_factory=list)
    online_at: int = 0


@dataclass
class PanelAdmin:
    username: str
    status: str = "active"          # active | disabled | limited
    used: int = 0                   # مصرف کل کاربران ادمین (بایت)
    total: int = 0                  # سقف حجم ادمین (بایت)؛ ۰ = نامحدود
    users: int = 0                  # تعداد کاربران ادمین
    max_users: int = 0              # ۰ = نامحدود


def gen_password(n: int = 16) -> str:
    """رمز قوی که قوانین همه‌ی پنل‌ها (پاسارگاد: ≥۱۲ حرف، ۲ رقم، ۲ بزرگ، ۲ کوچک، ۱ نماد، بدون ") را رعایت کند."""
    rnd = secrets.SystemRandom()
    chars = ([rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3)] + [rnd.choice("abcdefghijkmnpqrstuvwxyz") for _ in range(n - 8)] +
             [rnd.choice("23456789") for _ in range(3)] + [rnd.choice("@#%-_=+") for _ in range(2)])
    rnd.shuffle(chars)
    return "".join(chars)


def gb_bytes(gb) -> int:
    return int(float(gb or 0) * GB)


def exp_ts(days) -> int:
    return int(time.time()) + int(days) * 86400 if days and int(days) > 0 else 0


def to_ts(v) -> int:
    """زمان به شکل unix / میلی‌ثانیه / رشته‌ی ISO → unix ثانیه (۰ = نامحدود/نامشخص)."""
    if v in (None, "", 0, "0"):
        return 0
    if isinstance(v, (int, float)):
        v = int(v)
        return v // 1000 if v > 10 ** 11 else max(v, 0)
    s = str(v).strip()
    if s.isdigit():
        return to_ts(int(s))
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except ValueError:
        return 0


def iso(ts: int):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def derive_status(enabled: bool, used: int, total: int, expire: int) -> str:
    if not enabled:
        return "disabled"
    if expire and expire < time.time():
        return "expired"
    if total and used >= total:
        return "limited"
    return "active"


def new_expiry(cur: int, days: int, stack: bool) -> int:
    """انقضای جدید پس از تمدید: روی انقضای فعلی (اگر هنوز نگذشته و stack روشن) یا از همین الان."""
    if not days or int(days) <= 0:
        return 0
    base = cur if (stack and cur and cur > time.time()) else int(time.time())
    return base + int(days) * 86400


class BasePanel:
    TYPE = ""
    LABEL = ""
    NEEDS = ("url",)                       # فیلدهای لازم برای ثبت پنل
    OPTION_LABEL = ""                      # نام گزینه‌ها (اینباند/گروه/سرویس)
    HELP = ""
    manual = False                         # True فقط برای «فروش دستی» (بدون API)

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.url = (cfg.get("url") or "").rstrip("/")
        self.ex = cfg.get("extra") or {}
        self._http: aiohttp.ClientSession | None = None

    # ---- HTTP
    def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            conn = aiohttp.TCPConnector(ssl=False) if self.ex.get("insecure", True) else aiohttp.TCPConnector()
            self._http = aiohttp.ClientSession(timeout=TIMEOUT, connector=conn, cookie_jar=aiohttp.CookieJar(unsafe=True))
        return self._http

    async def close(self):
        if self._http and not self._http.closed:
            await self._http.close()

    async def _raw(self, method, url, **kw):
        try:
            async with self._session().request(method, url, **kw) as r:
                text = await r.text()
                try:
                    data = json.loads(text) if text else {}
                except ValueError:
                    data = text
                return r.status, data
        except aiohttp.ClientError as e:
            raise PanelError(f"اتصال به پنل ناموفق بود: {e}")
        except TimeoutError:
            raise PanelError("پاسخ پنل دیر رسید (timeout)")

    @staticmethod
    def _err(status, data) -> str:
        if isinstance(data, dict):
            d = data.get("detail") or data.get("msg") or data.get("message") or data
            return f"HTTP {status}: {str(d)[:300]}"
        return f"HTTP {status}: {str(data)[:200]}"

    def _abs(self, link: str) -> str:
        """لینک اشتراک نسبی (/sub/xxx) را با آدرس اشتراک یا آدرس پنل کامل می‌کند."""
        link = (link or "").strip()
        if not link or link.startswith(("http://", "https://")):
            return link
        base = (self.cfg.get("sub_url") or self.url).rstrip("/")
        u = urlsplit(base)
        if link.startswith("/"):
            return f"{u.scheme}://{u.netloc}{link}"
        return f"{base}/{link}"

    # ---- رابط
    async def test(self) -> dict:
        raise NotImplementedError

    async def options(self) -> list:
        return []

    async def status(self) -> dict:
        """وضعیت سرور برای مانیتورینگ؛ کلیدهای دلخواه: cpu cores cpu_mhz load[3] mem_used mem_total disk_used disk_total up_speed down_speed
        sent recv tcp udp xray_state xray_version version uptime ipv4 ipv6 users users_active (بایت/ثانیه، بایت)"""
        raise PanelError("این پنل مانیتورینگ سرور ندارد")

    async def create(self, name, days, gb, note="", tg_id=0, limit_ip=0):
        raise NotImplementedError

    async def info(self, name, meta) -> PanelUser:
        raise NotImplementedError

    async def renew(self, name, meta, days, gb, stack=True) -> PanelUser:
        raise NotImplementedError

    async def add_traffic(self, name, meta, gb) -> PanelUser:
        u = await self.info(name, meta)
        if not u.total:
            raise PanelError("حجم این سرویس نامحدود است")
        return await self._set_limits(name, meta, total=u.total + gb_bytes(gb), expire=u.expire, revive=True)

    async def add_days(self, name, meta, days) -> PanelUser:
        u = await self.info(name, meta)
        if not u.expire:
            raise PanelError("این سرویس تاریخ انقضا ندارد")
        return await self._set_limits(name, meta, total=u.total, expire=new_expiry(u.expire, int(days), True), revive=True)

    async def _set_limits(self, name, meta, total, expire, revive=False) -> PanelUser:
        raise NotImplementedError

    async def reset_usage(self, name, meta):
        raise NotImplementedError

    async def set_enabled(self, name, meta, on: bool):
        raise NotImplementedError

    async def delete(self, name, meta):
        raise NotImplementedError

    async def revoke(self, name, meta):
        raise PanelError("این پنل از ساخت لینک جدید پشتیبانی نمی‌کند")

    # ---- نمایندگی (ساخت ادمین در پنل)؛ فقط پنل‌هایی که ADMIN=True دارند
    ADMIN = False

    def panel_url(self) -> str:
        return self.url + "/dashboard/"

    def _no_admin(self):
        raise PanelError("این نوع پنل از ساخت ادمین/نماینده پشتیبانی نمی‌کند")

    async def admin_role_options(self) -> list:
        return []

    async def admin_create(self, username, password, spec: dict) -> PanelAdmin:
        self._no_admin()

    async def admin_info(self, username) -> PanelAdmin:
        self._no_admin()

    async def admin_set_enabled(self, username, on: bool, password: str = ""):
        self._no_admin()

    async def admin_set_password(self, username, password: str):
        self._no_admin()

    async def admin_renew(self, username, spec: dict, password: str = ""):
        self._no_admin()

    async def admin_set_traffic(self, username, gb: float):
        raise PanelError("این پنل سقف حجم برای ادمین ندارد")

    async def admin_delete(self, username):
        self._no_admin()



# ======================================================================= مرزبان
class Marzban(BasePanel):
    TYPE = "marzban"
    LABEL = "مرزبان ⁦Marzban⁩"
    NEEDS = ("url", "username", "password")
    OPTION_LABEL = "اینباند (tag)"
    HELP = "آدرس پنل را مثل https://panel.example.com:8000 بفرستید. انتخاب اینباند اختیاری است."
    API = "/api"
    TOKEN = "/admin/token"
    USER = "/user"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._token = ""

    async def _auth(self):
        st, d = await self._raw("POST", self.url + self.API + self.TOKEN,
                                data={"username": self.cfg.get("username", ""), "password": self.cfg.get("password", ""), "grant_type": "password"})
        if st != 200 or not isinstance(d, dict) or not d.get("access_token"):
            raise PanelError("ورود به پنل ناموفق بود (نام کاربری/رمز؟) " + self._err(st, d))
        self._token = d["access_token"]

    async def _call(self, method, path, ok=(200, 201), retry=True, **kw):
        if not self._token:
            await self._auth()
        st, d = await self._raw(method, self.url + self.API + path, headers={"Authorization": f"Bearer {self._token}"}, **kw)
        if st == 401 and retry:
            self._token = ""
            return await self._call(method, path, ok, False, **kw)
        if st not in ok:
            raise PanelError(self._err(st, d))
        return d

    # ---- ساخت بدنه
    async def _inbounds_raw(self) -> dict:
        d = await self._call("GET", "/inbounds")
        return d if isinstance(d, dict) else {}

    async def options(self):
        out = []
        for proto, lst in (await self._inbounds_raw()).items():
            for it in lst or []:
                out.append({"id": it.get("tag"), "name": f"{it.get('tag')} ({proto}/{it.get('network', '')}/{it.get('tls', '')})"})
        return out

    async def test(self):
        await self._auth()
        info = {}
        try:
            info = await self._call("GET", "/system")
        except PanelError:
            pass
        n = len(await self.options())
        return {"ok": True, "version": (info or {}).get("version", "") if isinstance(info, dict) else "", "users": (info or {}).get("total_user", "") if isinstance(info, dict) else "",
                "options": n}

    async def status(self):
        d = await self._call("GET", "/system")
        if not isinstance(d, dict):
            raise PanelError("پاسخ نامعتبر")
        out = {"version": d.get("version", ""), "cores": d.get("cpu_cores"), "cpu": d.get("cpu_usage"), "mem_used": d.get("mem_used"), "mem_total": d.get("mem_total"),
               "up_speed": d.get("outgoing_bandwidth_speed"), "down_speed": d.get("incoming_bandwidth_speed"), "sent": d.get("outgoing_bandwidth"),
               "recv": d.get("incoming_bandwidth"), "users": d.get("total_user"), "users_active": d.get("users_active") if d.get("users_active") is not None else d.get("online_users")}
        try:
            out["up_speed"], out["down_speed"] = int(out["up_speed"] or 0), int(out["down_speed"] or 0)
        except (TypeError, ValueError):
            pass
        return {k: v for k, v in out.items() if v not in (None, "")}

    async def _create_body(self, name, days, gb, note):
        raw = await self._inbounds_raw()
        want = {str(x) for x in (self.cfg.get("inbound_ids") or [])}
        proxies, inbounds = {}, {}
        for proto, lst in raw.items():
            tags = [it["tag"] for it in lst or [] if not want or str(it.get("tag")) in want]
            if tags:
                proxies[proto] = {"flow": self.ex["flow"]} if proto == "vless" and self.ex.get("flow") else {}
                inbounds[proto] = tags
        if not proxies:
            raise PanelError("هیچ اینباندی برای ساخت کاربر پیدا نشد (در تنظیم پنل اینباندها را بررسی کنید)")
        return {"username": name, "proxies": proxies, "inbounds": inbounds, "expire": exp_ts(days), "data_limit": gb_bytes(gb),
                "data_limit_reset_strategy": "no_reset", "status": "active", "note": note}

    def _parse(self, d) -> PanelUser:
        st = d.get("status") or "active"
        if st not in ("active", "disabled", "limited", "expired", "on_hold"):
            st = "active"
        used, total, exp = int(d.get("used_traffic") or 0), int(d.get("data_limit") or 0), to_ts(d.get("expire"))
        if st == "active":                 # پنل با کمی تأخیر وضعیت را به‌روز می‌کند؛ خودمان هم از روی انقضا/حجم محاسبه می‌کنیم
            st = derive_status(True, used, total, exp)
        return PanelUser(d.get("username", ""), st, used, total, exp,
                         self._abs(d.get("subscription_url") or ""), list(d.get("links") or []), to_ts(d.get("online_at")))

    async def create(self, name, days, gb, note="", tg_id=0, limit_ip=0):
        d = await self._call("POST", self.USER, json=await self._create_body(name, days, gb, note))
        return self._parse(d), {}

    async def info(self, name, meta):
        return self._parse(await self._call("GET", f"{self.USER}/{quote(name)}"))

    async def _put(self, name, body):
        return self._parse(await self._call("PUT", f"{self.USER}/{quote(name)}", json=body))

    def _limits_body(self, total, expire, revive):
        b = {"data_limit": int(total), "expire": self._expire_value(expire)}
        if revive:
            b["status"] = "active"
        return b

    def _expire_value(self, ts):
        return int(ts or 0)

    async def _set_limits(self, name, meta, total, expire, revive=False):
        return await self._put(name, self._limits_body(total, expire, revive))

    async def renew(self, name, meta, days, gb, stack=True):
        cur = await self.info(name, meta)
        await self._call("POST", f"{self.USER}/{quote(name)}/reset")
        return await self._set_limits(name, meta, gb_bytes(gb), new_expiry(cur.expire, days, stack), True)

    async def reset_usage(self, name, meta):
        await self._call("POST", f"{self.USER}/{quote(name)}/reset")

    async def set_enabled(self, name, meta, on):
        await self._put(name, {"status": "active" if on else "disabled"})

    async def delete(self, name, meta):
        await self._call("DELETE", f"{self.USER}/{quote(name)}", ok=(200, 204, 404))

    async def revoke(self, name, meta):
        d = await self._call("POST", f"{self.USER}/{quote(name)}/revoke_sub")
        return self._parse(d).sub_url, meta

    # ---- نمایندگی
    ADMIN = True
    ADMIN_PATH = "/admin"

    def _admin_parse(self, d) -> PanelAdmin:
        return PanelAdmin(d.get("username", ""), "active", int(d.get("users_usage") or 0), 0, 0, 0)

    async def admin_create(self, username, password, spec):
        body = {"username": username, "password": password, "is_sudo": False}
        if spec.get("telegram_id"):
            body["telegram_id"] = int(spec["telegram_id"])
        d = await self._call("POST", self.ADMIN_PATH, json=body)
        return self._admin_parse(d if isinstance(d, dict) else {"username": username})

    async def admin_info(self, username):
        d = await self._call("GET", "/admins", params={"username": username})
        items = d.get("admins") if isinstance(d, dict) else d
        for it in items or []:
            if it.get("username") == username:
                return self._admin_parse(it)
        raise PanelError("ادمین در پنل پیدا نشد")

    async def admin_set_password(self, username, password):
        await self._call("PUT", f"{self.ADMIN_PATH}/{quote(username)}", json={"password": password, "is_sudo": False})

    async def admin_set_enabled(self, username, on, password=""):
        # مرزبان وضعیت ادمین ندارد: خاموش = رمز تصادفی (ورود ممکن نیست) + غیرفعال‌کردن کاربران؛ روشن = بازگرداندن رمز + فعال‌سازی کاربران
        if on:
            if password:
                await self.admin_set_password(username, password)
            await self._call("POST", f"{self.ADMIN_PATH}/{quote(username)}/users/activate", ok=(200, 201, 404))
        else:
            await self.admin_set_password(username, gen_password(24))
            await self._call("POST", f"{self.ADMIN_PATH}/{quote(username)}/users/disable", ok=(200, 201, 404))

    async def admin_renew(self, username, spec, password=""):
        await self._call("POST", f"{self.ADMIN_PATH}/usage/reset/{quote(username)}", ok=(200, 201, 404))
        await self.admin_set_enabled(username, True, password)

    async def admin_delete(self, username):
        await self._call("DELETE", f"{self.ADMIN_PATH}/{quote(username)}", ok=(200, 204, 404))


# ======================================================================= پاسارگاد
class PasarGuard(Marzban):
    TYPE = "pasarguard"
    LABEL = "پاسارگاد ⁦PasarGuard⁩"
    OPTION_LABEL = "گروه (id)"
    HELP = "آدرس پنل را مثل https://panel.example.com:8000 بفرستید و گروه‌ها را انتخاب کنید."

    async def options(self):
        d = await self._call("GET", "/groups")
        items = d.get("groups") if isinstance(d, dict) else d
        return [{"id": g.get("id"), "name": f"{g.get('name')} (#{g.get('id')})"} for g in (items or [])]

    async def test(self):
        await self._auth()
        n = len(await self.options())
        ver = ""
        try:
            s = await self._call("GET", "/system")
            ver = s.get("version", "") if isinstance(s, dict) else ""
        except PanelError:
            pass
        return {"ok": True, "version": ver, "options": n}

    def _expire_value(self, ts):
        return iso(ts) if ts else None

    async def _create_body(self, name, days, gb, note):
        ids = [int(x) for x in (self.cfg.get("inbound_ids") or []) if str(x).isdigit()]
        if not ids:
            raise PanelError("حداقل یک گروه (group id) در تنظیم پنل انتخاب کنید")
        return {"username": name, "status": "active", "expire": iso(exp_ts(days)), "data_limit": gb_bytes(gb),
                "data_limit_reset_strategy": "no_reset", "group_ids": ids, "note": note}

    async def _call(self, method, path, ok=(200, 201), retry=True, **kw):
        try:
            return await super()._call(method, path, ok, retry, **kw)
        except PanelError as e:
            # بعضی نسخه‌ها expire را عددی می‌خواهند؛ یک‌بار با عدد تلاش می‌کنیم
            if "422" in str(e) and isinstance(kw.get("json"), dict) and "expire" in kw["json"] and isinstance(kw["json"]["expire"], str):
                body = dict(kw["json"])
                body["expire"] = to_ts(body["expire"])
                kw = {**kw, "json": body}
                return await super()._call(method, path, ok, retry, **kw)
            raise

    # ---- نمایندگی (نقش‌ها، سقف حجم و تعداد کاربر)
    async def admin_role_options(self):
        d = await self._call("GET", "/admin-roles/simple")
        items = d.get("roles") if isinstance(d, dict) else d
        return [{"id": r.get("id"), "name": f"{r.get('name')} (#{r.get('id')})"} for r in (items or []) if not r.get("is_owner")]

    @staticmethod
    def _overrides(spec) -> dict:
        o = {}
        if spec.get("max_users"):
            o["max_users"] = int(spec["max_users"])
        for k, f in (("data_min_gb", "data_limit_min"), ("data_max_gb", "data_limit_max")):
            if spec.get(k):
                o[f] = gb_bytes(spec[k])
        for k, f in (("expire_min_days", "expire_min"), ("expire_max_days", "expire_max")):
            if spec.get(k):
                o[f] = int(spec[k]) * 86400
        return o

    def _admin_parse(self, d) -> PanelAdmin:
        lim = ((d.get("permission_overrides") or {}).get("max_users")) or ((d.get("role") or {}).get("limits") or {}).get("max_users") or 0
        st = str(d.get("status") or ("disabled" if d.get("is_disabled") else "active"))   # نسخه‌های قدیمی: is_disabled
        return PanelAdmin(d.get("username", ""), st, int(d.get("used_traffic") or 0), int(d.get("data_limit") or 0),
                          int(d.get("total_users") or 0), int(lim or 0))

    async def admin_create(self, username, password, spec):
        body = {"username": username, "password": password, "note": spec.get("note") or ""}
        if spec.get("role_id"):
            body["role_id"] = int(spec["role_id"])
            ov = self._overrides(spec)
            if ov:
                body["permission_overrides"] = ov
        else:
            body["is_sudo"] = False               # نسخه‌های قدیمی بدون نقش
        if spec.get("traffic_gb"):
            body["data_limit"] = gb_bytes(spec["traffic_gb"])
        if spec.get("telegram_id"):
            body["telegram_id"] = int(spec["telegram_id"])
        d = await self._call("POST", self.ADMIN_PATH, json=body)
        return self._admin_parse(d if isinstance(d, dict) else {"username": username})

    async def admin_set_password(self, username, password):
        await self._call("PUT", f"{self.ADMIN_PATH}/{quote(username)}", json={"password": password, "is_sudo": False})      # is_sudo: نسخه‌های قدیمی اجباری؛ نسخه‌ی جدید نادیده می‌گیرد

    async def admin_set_enabled(self, username, on, password=""):
        await self._call("PUT", f"{self.ADMIN_PATH}/{quote(username)}", json={"status": "active" if on else "disabled", "is_disabled": not on, "is_sudo": False})

    async def admin_set_traffic(self, username, gb):
        await self._call("PUT", f"{self.ADMIN_PATH}/{quote(username)}", json={"data_limit": gb_bytes(gb), "is_sudo": False})

    async def admin_renew(self, username, spec, password=""):
        await self._call("POST", f"{self.ADMIN_PATH}/{quote(username)}/reset", ok=(200, 201))
        body = {"status": "active", "is_disabled": False, "is_sudo": False, "data_limit": gb_bytes(spec.get("traffic_gb") or 0)}
        await self._call("PUT", f"{self.ADMIN_PATH}/{quote(username)}", json=body)


# ======================================================================= مرزنشین
class Marzneshin(Marzban):
    TYPE = "marzneshin"
    LABEL = "مرزنشین ⁦Marzneshin⁩"
    OPTION_LABEL = "سرویس (id)"
    TOKEN = "/admins/token"
    USER = "/users"

    async def options(self):
        d = await self._call("GET", "/services")
        items = d.get("items") if isinstance(d, dict) else d
        return [{"id": s.get("id"), "name": f"{s.get('name')} (#{s.get('id')})"} for s in (items or [])]

    async def test(self):
        await self._auth()
        return {"ok": True, "version": "", "options": len(await self.options())}

    async def _create_body(self, name, days, gb, note):
        ids = [int(x) for x in (self.cfg.get("inbound_ids") or []) if str(x).isdigit()]
        if not ids:
            raise PanelError("حداقل یک سرویس (service id) در تنظیم پنل انتخاب کنید")
        b = {"username": name, "data_limit": gb_bytes(gb), "data_limit_reset_strategy": "no_reset", "service_ids": ids, "note": note}
        if days and int(days) > 0:
            b.update({"expire_strategy": "fixed_date", "expire_date": iso(exp_ts(days))})
        else:
            b.update({"expire_strategy": "never"})
        return b

    def _parse(self, d) -> PanelUser:
        exp = to_ts(d.get("expire_date"))
        used = int(d.get("used_traffic") or 0)
        total = int(d.get("data_limit") or 0)
        enabled = d.get("enabled", True)
        st = derive_status(bool(enabled), used, total, exp)
        if d.get("expired"):
            st = "expired"
        if d.get("data_limit_reached"):
            st = "limited"
        return PanelUser(d.get("username", ""), st, used, total, exp, self._abs(d.get("subscription_url") or ""), [], to_ts(d.get("online_at")))

    def _limits_body(self, total, expire, revive):
        b = {"data_limit": int(total)}
        if expire:
            b.update({"expire_strategy": "fixed_date", "expire_date": iso(expire)})
        else:
            b.update({"expire_strategy": "never"})
        return b

    async def _set_limits(self, name, meta, total, expire, revive=False):
        u = await self._put(name, self._limits_body(total, expire, revive))
        if revive:
            try:
                await self._call("POST", f"{self.USER}/{quote(name)}/enable")
            except PanelError:
                pass
        return u

    async def _put(self, name, body):
        cur = await self._call("GET", f"{self.USER}/{quote(name)}")
        merged = {k: cur.get(k) for k in ("data_limit", "data_limit_reset_strategy", "expire_strategy", "expire_date", "service_ids", "note") if k in cur}
        merged.update(body)
        merged["username"] = name                    # در UserModify مرزنشین username اجباری است
        return self._parse(await self._call("PUT", f"{self.USER}/{quote(name)}", json=merged))

    async def set_enabled(self, name, meta, on):
        await self._call("POST", f"{self.USER}/{quote(name)}/{'enable' if on else 'disable'}")

    async def revoke(self, name, meta):
        d = await self._call("POST", f"{self.USER}/{quote(name)}/revoke_sub")
        return self._parse(d).sub_url, meta

    # ---- نمایندگی (دسترسی به سرویس‌ها؛ مرزنشین سقف حجم/تعداد برای ادمین ندارد)
    ADMIN_PATH = "/admins"

    async def admin_role_options(self):
        return await self.options()

    def _admin_parse(self, d) -> PanelAdmin:
        return PanelAdmin(d.get("username", ""), "active" if d.get("enabled", True) else "disabled", int(d.get("users_data_usage") or 0), 0, 0, 0)

    async def admin_create(self, username, password, spec):
        ids = [int(x) for x in (spec.get("service_ids") or []) if str(x).isdigit()]
        body = {"username": username, "password": password, "is_sudo": False, "enabled": True,
                "all_services_access": bool(spec.get("all_services")) or not ids, "modify_users_access": bool(spec.get("modify_users", True)),
                "service_ids": ids}
        d = await self._call("POST", self.ADMIN_PATH, json=body)
        return self._admin_parse(d if isinstance(d, dict) else {"username": username})

    async def admin_info(self, username):
        return self._admin_parse(await self._call("GET", f"{self.ADMIN_PATH}/{quote(username)}"))

    async def admin_set_password(self, username, password):
        await self._call("PUT", f"{self.ADMIN_PATH}/{quote(username)}", json={"password": password})

    async def admin_set_enabled(self, username, on, password=""):
        await self._call("PUT", f"{self.ADMIN_PATH}/{quote(username)}", json={"enabled": bool(on)})
        await self._call("POST", f"{self.ADMIN_PATH}/{quote(username)}/{'enable_users' if on else 'disable_users'}", ok=(200, 201, 404))

    async def admin_renew(self, username, spec, password=""):
        await self.admin_set_enabled(username, True, password)


# ======================================================================= 3x-ui / x-ui
class XUI(BasePanel):
    TYPE = "xui"
    LABEL = "ثنایی ⁦3x-ui⁩"
    NEEDS = ("url", "inbound_ids")
    OPTION_LABEL = "اینباند (id)"
    HELP = ("آدرس پنل را با مسیر مخفی آن بفرستید، مثل https://host:2053/abc/. "
            "برای ورود، «توکن API» یا نام کاربری و رمز را بدهید. آدرس اشتراک اختیاری است.")
    PREFIXES = ["/panel/api/inbounds", "/xui/API/inbounds", "/xui/inbound"]

    def __init__(self, cfg):
        super().__init__(cfg)
        self._logged = False
        self._prefix = None
        self._csrf = ""
        self._v3 = None

    def _hdr(self) -> dict:
        h = {}
        if self.cfg.get("token"):
            h["Authorization"] = "Bearer " + str(self.cfg["token"]).strip()
        elif self._csrf:
            h["X-CSRF-Token"] = self._csrf
        return h

    async def _raw(self, method, url, **kw):
        h = dict(kw.pop("headers", None) or {})
        h.update(self._hdr())
        return await super()._raw(method, url, headers=h, **kw)

    async def _login(self):
        if self.cfg.get("token"):                     # توکن API (نسخه‌ی جدید): نشست و CSRF لازم نیست
            self._logged = True
            return
        if not self.cfg.get("username") or not self.cfg.get("password"):
            raise PanelError("توکن API یا نام کاربری و رمز پنل را وارد کنید")
        self._csrf = ""
        st, d = await self._raw("GET", self.url + "/csrf-token")      # نسخه‌ی جدید؛ در نسخه‌ی قدیمی ۴۰۴ است
        if st == 200 and isinstance(d, dict) and d.get("success") and d.get("obj"):
            self._csrf = str(d["obj"])
        st, d = await self._raw("POST", self.url + "/login", data={"username": self.cfg.get("username", ""), "password": self.cfg.get("password", ""),
                                                                   **({"twoFactorCode": self.ex["2fa"]} if self.ex.get("2fa") else {})})
        if st != 200 or not isinstance(d, dict) or not d.get("success"):
            raise PanelError("ورود به پنل ناموفق بود (آدرس/مسیر، نام کاربری و رمز را بررسی کنید) " + self._err(st, d))
        if self._csrf:                                 # توکن CSRF نشست پس از ورود
            st, d = await self._raw("GET", self.url + "/csrf-token")
            if st == 200 and isinstance(d, dict) and d.get("obj"):
                self._csrf = str(d["obj"])
        self._logged = True

    async def _api(self, method, tail, ok_key=True, **kw):
        """tail مثل /list یا /addClient. پیشوند درست API خودکار پیدا و به خاطر سپرده می‌شود؛ نشست منقضی خودکار تازه می‌شود
        (3x-ui برای نشست منقضی ۴۰۴ برمی‌گرداند، پس ۴۰۴ هم یک‌بار با لاگین مجدد تکرار می‌شود)."""
        if not self._logged:
            await self._login()
        last = None
        for pre in ([self._prefix] if self._prefix else self.PREFIXES):
            st, d = await self._raw(method, self.url + pre + tail, **kw)
            if st in (401, 404):
                await self._login()
                st, d = await self._raw(method, self.url + pre + tail, **kw)
            if st == 404 and not self._prefix:
                last = (st, d)
                continue
            if st != 200:
                raise PanelError(self._err(st, d))
            if ok_key and isinstance(d, dict) and d.get("success") is False:
                raise PanelError(str(d.get("msg") or d)[:300])
            self._prefix = pre
            return d
        raise PanelError("API پنل پیدا نشد (مسیر پنل را بررسی کنید) " + (self._err(*last) if last else ""))

    async def _inbounds(self) -> list:
        d = await self._api("GET", "/list")
        return d.get("obj", []) if isinstance(d, dict) else []

    async def test(self):
        await self._login()
        items = await self._inbounds()
        return {"ok": True, "version": "API v3 (clients)" if await self._is3() else "API v2 (legacy)", "options": len(items)}

    async def options(self):
        return [{"id": i.get("id"), "name": f"#{i.get('id')} {i.get('remark', '')} ({i.get('protocol')}:{i.get('port')})"} for i in await self._inbounds()]

    async def status(self):
        if not self._logged:
            await self._login()
        obj, err = None, None
        for path in ("/panel/api/server/status", "/server/status", "/panel/server/status"):
            for method in ("POST", "GET"):
                st, d = await self._raw(method, self.url + path)
                if st in (401, 404, 405):
                    continue
                if st == 200 and isinstance(d, dict) and isinstance(d.get("obj"), dict):
                    obj = d["obj"]
                    break
                err = self._err(st, d)
            if obj:
                break
        if not obj:
            raise PanelError(err or "مسیر وضعیت سرور در پنل پیدا نشد")
        mem, disk, xray = obj.get("mem") or {}, obj.get("disk") or {}, obj.get("xray") or {}
        net, traffic, ip = obj.get("netIO") or {}, obj.get("netTraffic") or {}, obj.get("publicIP") or {}
        out = {"cpu": obj.get("cpu"), "cores": obj.get("cpuCores"), "logical": obj.get("logicalPro"), "cpu_mhz": obj.get("cpuSpeedMhz"), "load": obj.get("loads"),
               "mem_used": mem.get("current"), "mem_total": mem.get("total"), "disk_used": disk.get("current"), "disk_total": disk.get("total"),
               "up_speed": net.get("up"), "down_speed": net.get("down"), "sent": traffic.get("sent"), "recv": traffic.get("recv"),
               "tcp": obj.get("tcpCount"), "udp": obj.get("udpCount"), "xray_state": xray.get("state"), "xray_version": xray.get("version"),
               "uptime": obj.get("uptime"), "ipv4": ip.get("ipv4"), "ipv6": ip.get("ipv6")}
        try:
            out["version"] = ((await self._raw("GET", self.url + "/panel/api/server/getPanelUpdateInfo"))[1] or {}).get("obj", {}).get("currentVersion", "")
        except Exception:  # noqa: BLE001
            pass
        return {k: v for k, v in out.items() if v not in (None, "")}

    def _inbound_id(self) -> int:
        ids = [x for x in (self.cfg.get("inbound_ids") or []) if str(x).isdigit()]
        if not ids:
            raise PanelError("شناسه‌ی اینباند در تنظیم پنل انتخاب نشده است")
        return int(ids[0])

    @staticmethod
    def _client_obj(meta, email, total, expire_ms, enable, limit_ip, tg_id=0, comment=""):
        c = {"email": email, "limitIp": int(limit_ip or 0), "totalGB": int(total), "expiryTime": int(expire_ms), "enable": bool(enable),
             "tgId": str(tg_id or ""), "subId": meta["sub"], "comment": comment, "reset": 0}
        if meta["proto"] in ("vmess", "vless"):
            c["id"] = meta["cid"]
            if meta["proto"] == "vless" and meta.get("flow"):
                c["flow"] = meta["flow"]
            if meta["proto"] == "vmess":
                c["security"] = "auto"
        else:
            c["password"] = meta["cid"]
        return c

    async def _push(self, path_tail, meta, client, name):
        form = {"id": str(meta["inbound"]), "settings": json.dumps({"clients": [client]})}
        await self._api("POST", path_tail, data=form)

    async def create_v1(self, name, days, gb, note="", tg_id=0, limit_ip=0):
        iid = self._inbound_id()
        ib = next((i for i in await self._inbounds() if int(i.get("id")) == iid), None)
        if not ib:
            raise PanelError(f"اینباند {iid} در پنل پیدا نشد")
        proto = ib.get("protocol", "vless")
        meta = {"inbound": iid, "proto": proto, "cid": str(uuid.uuid4()) if proto in ("vmess", "vless") else secrets.token_urlsafe(9),
                "sub": secrets.token_hex(8), "flow": self.ex.get("flow", "")}
        client = self._client_obj(meta, name, gb_bytes(gb), exp_ts(days) * 1000, True, limit_ip or self.ex.get("limit_ip", 0), tg_id, note)
        await self._push("/addClient", meta, client, name)
        user = PanelUser(name, "active", 0, gb_bytes(gb), exp_ts(days), self._sub_url(meta), self._links(ib, meta, name))
        return user, meta

    def _sub_url(self, meta) -> str:
        base = (self.cfg.get("sub_url") or "").rstrip("/")
        return f"{base}/{meta['sub']}" if base else ""

    async def _traffic(self, name) -> dict:
        d = await self._api("GET", f"/getClientTraffics/{quote(name)}")
        obj = d.get("obj") if isinstance(d, dict) else None
        if not obj:
            raise PanelError("کلاینت در پنل پیدا نشد")
        return obj

    async def info_v1(self, name, meta):
        t = await self._traffic(name)
        used = int(t.get("up") or 0) + int(t.get("down") or 0)
        total = int(t.get("total") or 0)
        exp = to_ts(t.get("expiryTime"))
        links = []
        try:
            ib = next((i for i in await self._inbounds() if int(i.get("id")) == int(meta.get("inbound", 0))), None)
            if ib and not self.cfg.get("sub_url"):
                links = self._links(ib, meta, name)
        except PanelError:
            pass
        return PanelUser(name, derive_status(bool(t.get("enable", True)), used, total, exp), used, total, exp, self._sub_url(meta), links)

    async def _update(self, name, meta, total, expire, enable=True, limit_ip=None):
        client = self._client_obj(meta, name, total, int(expire) * 1000 if expire else 0, enable, limit_ip if limit_ip is not None else self.ex.get("limit_ip", 0))
        await self._push(f"/updateClient/{quote(meta['cid'], safe='')}", meta, client, name)

    async def _set_limits_v1(self, name, meta, total, expire, revive=False):
        cur = await self.info_v1(name, meta)
        await self._update(name, meta, total, expire, enable=cur.status != "disabled" or revive)
        return await self.info_v1(name, meta)

    async def renew_v1(self, name, meta, days, gb, stack=True):
        cur = await self.info_v1(name, meta)
        await self._api("POST", f"/{meta['inbound']}/resetClientTraffic/{quote(name)}")
        await self._update(name, meta, gb_bytes(gb), new_expiry(cur.expire, days, stack), True)
        return await self.info_v1(name, meta)

    async def reset_usage_v1(self, name, meta):
        await self._api("POST", f"/{meta['inbound']}/resetClientTraffic/{quote(name)}")

    async def set_enabled_v1(self, name, meta, on):
        cur = await self.info_v1(name, meta)
        await self._update(name, meta, cur.total, cur.expire, on)

    async def delete_v1(self, name, meta):
        try:
            await self._api("POST", f"/{meta['inbound']}/delClient/{quote(meta['cid'], safe='')}")
        except PanelError as e:
            if "not" not in str(e).lower() and "404" not in str(e):
                raise

    async def revoke_v1(self, name, meta):
        cur = await self.info_v1(name, meta)
        meta = {**meta, "sub": secrets.token_hex(8)}
        await self._update(name, meta, cur.total, cur.expire, cur.status != "disabled")
        return self._sub_url(meta), meta

    # ---- نسخه‌ی جدید (API کلاینت‌ها: /panel/api/clients/*؛ کلاینت با ایمیل شناخته می‌شود و می‌تواند چند اینباند داشته باشد)
    async def _is3(self) -> bool:
        if self._v3 is None:
            if not self._logged:
                await self._login()
            st, d = await self._raw("GET", self.url + "/panel/api/clients/list")
            self._v3 = st == 200 and isinstance(d, dict) and d.get("success") is not False
        return self._v3

    async def _c3(self, method, path, **kw):
        if not self._logged:
            await self._login()
        st, d = await self._raw(method, self.url + "/panel/api" + path, **kw)
        if st in (401, 403, 404) and not self.cfg.get("token"):
            await self._login()
            st, d = await self._raw(method, self.url + "/panel/api" + path, **kw)
        if st != 200:
            raise PanelError(self._err(st, d))
        if isinstance(d, dict) and d.get("success") is False:
            raise PanelError(str(d.get("msg") or d)[:300])
        return d

    def _inbound_ids(self) -> list:
        ids = [int(x) for x in (self.cfg.get("inbound_ids") or []) if str(x).isdigit()]
        if not ids:
            raise PanelError("شناسه‌ی اینباند در تنظیم پنل انتخاب نشده است")
        return ids

    async def _get3(self, name) -> dict:
        d = await self._c3("GET", f"/clients/get/{quote(name, safe='')}")
        obj = d.get("obj") if isinstance(d, dict) else None
        if not obj or not obj.get("client"):
            raise PanelError("کلاینت در پنل پیدا نشد")
        return obj

    async def _user3(self, obj, meta, name) -> PanelUser:
        c = obj["client"]
        used = int(obj.get("usedTraffic") or 0)
        total, exp = int(c.get("totalGB") or 0), to_ts(c.get("expiryTime"))
        sub = self._sub_url({"sub": c.get("subId") or meta.get("sub", "")}) if (c.get("subId") or meta.get("sub")) else ""
        links = []
        if not self.cfg.get("sub_url"):
            try:
                d = await self._c3("GET", f"/clients/links/{quote(name, safe='')}")
                links = [x for x in (d.get("obj") or []) if isinstance(x, str)]
            except PanelError:
                pass
        return PanelUser(name, derive_status(bool(c.get("enable", True)), used, total, exp), used, total, exp, sub, links)

    _KEEP3 = ("email", "subId", "password", "auth", "flow", "security", "limitIp", "limitHwid", "totalGB", "expiryTime", "enable", "tgId",
              "group", "comment", "reset", "resetDay", "resetWeekday", "resetMax")

    async def _update3(self, name, **changes):
        obj = await self._get3(name)
        c = {k: obj["client"][k] for k in self._KEEP3 if k in obj["client"]}
        c["id"] = obj["client"].get("uuid") or obj["client"].get("id") or ""
        c.update(changes)
        await self._c3("POST", f"/clients/update/{quote(name, safe='')}", json=c)

    async def _create3(self, name, days, gb, note, tg_id, limit_ip):
        ids = self._inbound_ids()
        have = {int(i.get("id")) for i in await self._inbounds()}
        missing = [i for i in ids if i not in have]
        if missing:
            raise PanelError(f"اینباند {missing[0]} در پنل پیدا نشد")
        meta = {"v": 3, "sub": secrets.token_hex(8), "email": name}
        client = {"email": name, "subId": meta["sub"], "totalGB": gb_bytes(gb), "expiryTime": exp_ts(days) * 1000, "enable": True,
                  "limitIp": int(limit_ip or self.ex.get("limit_ip", 0) or 0), "tgId": int(tg_id or 0), "comment": note or ""}
        if self.ex.get("flow"):
            client["flow"] = self.ex["flow"]
        await self._c3("POST", "/clients/add", json={"client": client, "inboundIds": ids})
        return await self._user3(await self._get3(name), meta, name), meta

    # ---- رابط عمومی (بسته به نسخه‌ی پنل)
    async def create(self, name, days, gb, note="", tg_id=0, limit_ip=0):
        if await self._is3():
            return await self._create3(name, days, gb, note, tg_id, limit_ip)
        return await self.create_v1(name, days, gb, note, tg_id, limit_ip)

    async def info(self, name, meta):
        if await self._is3():
            return await self._user3(await self._get3(name), meta, name)
        return await self.info_v1(name, meta)

    async def _set_limits(self, name, meta, total, expire, revive=False):
        if await self._is3():
            cur = await self._get3(name)
            await self._update3(name, totalGB=int(total), expiryTime=int(expire) * 1000 if expire else 0,
                                enable=True if revive else bool(cur["client"].get("enable", True)))
            return await self.info(name, meta)
        return await self._set_limits_v1(name, meta, total, expire, revive)

    async def renew(self, name, meta, days, gb, stack=True):
        if await self._is3():
            cur = await self.info(name, meta)
            await self._c3("POST", f"/clients/resetTraffic/{quote(name, safe='')}")
            await self._update3(name, totalGB=gb_bytes(gb), expiryTime=new_expiry(cur.expire, days, stack) * 1000, enable=True)
            return await self.info(name, meta)
        return await self.renew_v1(name, meta, days, gb, stack)

    async def reset_usage(self, name, meta):
        if await self._is3():
            await self._c3("POST", f"/clients/resetTraffic/{quote(name, safe='')}")
            return
        await self.reset_usage_v1(name, meta)

    async def set_enabled(self, name, meta, on):
        if await self._is3():
            await self._update3(name, enable=bool(on))
            return
        await self.set_enabled_v1(name, meta, on)

    async def delete(self, name, meta):
        if await self._is3():
            try:
                await self._c3("POST", f"/clients/del/{quote(name, safe='')}")
            except PanelError as e:
                if "not found" not in str(e).lower() and "404" not in str(e) and "record" not in str(e).lower():
                    raise
            return
        await self.delete_v1(name, meta)

    async def revoke(self, name, meta):
        if await self._is3():
            meta = {**meta, "sub": secrets.token_hex(8)}
            await self._update3(name, subId=meta["sub"])
            return self._sub_url(meta), meta
        return await self.revoke_v1(name, meta)

    # ---- ساخت لینک مستقیم کانفیگ
    def _host(self) -> str:
        return (self.ex.get("address") or urlsplit(self.url).hostname or "").strip()

    def _links(self, ib: dict, meta: dict, name: str) -> list:
        try:
            ss = json.loads(ib.get("streamSettings") or "{}")
        except ValueError:
            ss = {}
        proto, port, host = ib.get("protocol"), ib.get("port"), self._host()
        net = ss.get("network", "tcp")
        sec = ss.get("security", "none")
        q = {"type": net, "security": sec}
        if net == "ws":
            w = ss.get("wsSettings", {})
            q["path"] = w.get("path", "/")
            h = (w.get("headers") or {}).get("Host") or w.get("host")
            if h:
                q["host"] = h
        elif net == "grpc":
            g = ss.get("grpcSettings", {})
            q["serviceName"] = g.get("serviceName", "")
            if g.get("multiMode"):
                q["mode"] = "multi"
        elif net in ("httpupgrade", "xhttp", "splithttp"):
            w = ss.get(f"{net}Settings", {})
            q["path"] = w.get("path", "/")
            if w.get("host"):
                q["host"] = w["host"]
        elif net == "tcp":
            hdr = (ss.get("tcpSettings") or {}).get("header") or {}
            if hdr.get("type") == "http":
                q["headerType"] = "http"
                req = hdr.get("request") or {}
                if req.get("path"):
                    q["path"] = req["path"][0]
        if sec == "tls":
            t = ss.get("tlsSettings", {})
            q["sni"] = t.get("serverName") or ""
            if t.get("alpn"):
                q["alpn"] = ",".join(t["alpn"])
            fp = (t.get("settings") or {}).get("fingerprint") or t.get("fingerprint")
            if fp:
                q["fp"] = fp
        elif sec == "reality":
            r = ss.get("realitySettings", {})
            rs = r.get("settings") or {}
            q.update({"pbk": rs.get("publicKey") or r.get("publicKey", ""), "fp": rs.get("fingerprint") or r.get("fingerprint", "chrome"),
                      "sni": (r.get("serverNames") or [""])[0], "sid": (r.get("shortIds") or [""])[0], "spx": rs.get("spiderX") or r.get("spiderX", "")})
        remark = quote(f"{ib.get('remark', '')}-{name}")
        if proto == "vless":
            if meta.get("flow") and sec in ("tls", "reality"):
                q["flow"] = meta["flow"]
            q["encryption"] = "none"
            return [f"vless://{meta['cid']}@{host}:{port}?{urlencode({k: v for k, v in q.items() if v != ''}, quote_via=quote)}#{remark}"]
        if proto == "trojan":
            return [f"trojan://{quote(meta['cid'])}@{host}:{port}?{urlencode({k: v for k, v in q.items() if v != ''}, quote_via=quote)}#{remark}"]
        if proto == "vmess":
            o = {"v": "2", "ps": f"{ib.get('remark', '')}-{name}", "add": host, "port": str(port), "id": meta["cid"], "aid": "0", "scy": "auto", "net": net,
                 "type": q.get("headerType", "none"), "host": q.get("host", ""), "path": q.get("path", q.get("serviceName", "")), "tls": "tls" if sec == "tls" else "",
                 "sni": q.get("sni", ""), "alpn": q.get("alpn", ""), "fp": q.get("fp", "")}
            return ["vmess://" + base64.b64encode(json.dumps(o).encode()).decode()]
        if proto == "shadowsocks":
            try:
                st = json.loads(ib.get("settings") or "{}")
            except ValueError:
                st = {}
            cred = base64.b64encode(f"{st.get('method', 'aes-256-gcm')}:{meta['cid']}".encode()).decode().rstrip("=")
            return [f"ss://{cred}@{host}:{port}#{remark}"]
        return []


# ======================================================================= هیدیفای
class Hiddify(BasePanel):
    TYPE = "hiddify"
    LABEL = "هیدیفای ⁦Hiddify⁩"
    NEEDS = ("url", "token", "sub_url")
    HELP = ("آدرس مدیریت را مثل https://domain/مسیر-ادمین بفرستید. توکن، همان کلید API ادمین است. "
            "آدرس اشتراک را هم از صفحه‌ی کاربران بردارید.")

    async def status(self):
        d = await self._call("GET", "/server_status/")
        sysd = (d.get("stats") or {}).get("system") if isinstance(d, dict) else None
        if not isinstance(sysd, dict):
            raise PanelError("پاسخ وضعیت سرور نامعتبر است")
        out = {"cpu": sysd.get("cpu_percent"), "mem_used": sysd.get("ram_used"), "mem_total": sysd.get("ram_total"), "disk_used": sysd.get("disk_used"),
               "disk_total": sysd.get("disk_total"), "up_speed": sysd.get("bytes_sent_cumulative"), "sent": sysd.get("bytes_sent_cumulative"),
               "recv": sysd.get("bytes_recv_cumulative"), "load": [sysd.get(k) for k in ("load_avg_1min", "load_avg_5min", "load_avg_15min") if sysd.get(k) is not None] or None}
        out.pop("up_speed", None)
        return {k: v for k, v in out.items() if v not in (None, "")}

    async def _call(self, method, path, ok=(200, 201), **kw):
        st, d = await self._raw(method, f"{self.url}/api/v2/admin{path}", headers={"Hiddify-API-Key": self.cfg.get("token", ""), "Accept": "application/json"}, **kw)
        if st not in ok:
            raise PanelError(self._err(st, d))
        return d

    async def test(self):
        d = await self._call("GET", "/me/")
        return {"ok": True, "version": "", "options": 0, "name": d.get("name", "") if isinstance(d, dict) else ""}

    def _sub(self, uid) -> str:
        return f"{(self.cfg.get('sub_url') or self.url).rstrip('/')}/{uid}/"

    def _parse(self, d) -> PanelUser:
        days = int(d.get("package_days") or 0)
        start = to_ts(d.get("start_date"))
        exp = (start or int(time.time())) + days * 86400 if days else 0
        used = int(float(d.get("current_usage_GB") or 0) * GB)
        total = int(float(d.get("usage_limit_GB") or 0) * GB)
        enabled = d.get("enable", True) is not False
        st = derive_status(enabled, used, total, exp if start else 0)
        if d.get("is_active") is False and st == "active" and start:
            st = "expired" if exp and exp < time.time() else "limited"
        return PanelUser(d.get("name", ""), st, used, total, exp if start else 0, self._sub(d.get("uuid", "")), [], to_ts(d.get("last_online")))

    async def create(self, name, days, gb, note="", tg_id=0, limit_ip=0):
        body = {"name": name, "package_days": int(days or 0) or 3650, "usage_limit_GB": float(gb or 0), "mode": "no_reset", "comment": note,
                "enable": True, "lang": "fa", "start_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), **({"telegram_id": int(tg_id)} if tg_id else {})}
        d = await self._call("POST", "/user/", json=body)
        uid = d.get("uuid") if isinstance(d, dict) else None
        if not uid:
            raise PanelError("پاسخ پنل UUID کاربر را نداشت")
        u = self._parse({**d, "start_date": d.get("start_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")})
        u.username = name
        if not days:
            u.expire = 0
        return u, {"uuid": uid, "unlimited_days": not days}

    async def info(self, name, meta):
        d = await self._call("GET", f"/user/{meta['uuid']}/")
        u = self._parse(d)
        if meta.get("unlimited_days"):
            u.expire = 0
        u.username = name
        return u

    async def _patch(self, meta, body):
        await self._call("PATCH", f"/user/{meta['uuid']}/", json=body)

    async def renew(self, name, meta, days, gb, stack=True):
        cur = await self.info(name, meta)
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        pkg = 3650
        if days:
            midnight = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            pkg = max(1, -(-(new_expiry(cur.expire, days, stack) - midnight) // 86400))     # سقف تقسیم
        await self._patch(meta, {"package_days": pkg, "usage_limit_GB": float(gb or 0), "current_usage_GB": 0, "start_date": today, "enable": True})
        meta["unlimited_days"] = not days
        return await self.info(name, meta)

    async def _set_limits(self, name, meta, total, expire, revive=False):
        body = {"usage_limit_GB": round(total / GB, 3)}
        if expire:
            cur = await self._call("GET", f"/user/{meta['uuid']}/")
            start = to_ts(cur.get("start_date")) or int(time.time())
            body["package_days"] = max(1, int((expire - start) / 86400 + 0.999))
        if revive:
            body["enable"] = True
        await self._patch(meta, body)
        return await self.info(name, meta)

    async def reset_usage(self, name, meta):
        await self._patch(meta, {"current_usage_GB": 0})

    async def set_enabled(self, name, meta, on):
        await self._patch(meta, {"enable": bool(on)})

    async def delete(self, name, meta):
        await self._call("DELETE", f"/user/{meta['uuid']}/", ok=(200, 204, 404))


# ======================================================================= فروش دستی
class Manual(BasePanel):
    TYPE = "manual"
    LABEL = "فروش دستی"
    NEEDS = ()
    HELP = "بدون اتصال به پنل. بعد از پرداخت، شما کانفیگ را خودتان برای کاربر می‌فرستید."
    manual = True

    async def test(self):
        return {"ok": True, "version": "manual", "options": 0}

    async def create(self, *a, **k):
        raise PanelError("manual")

    async def info(self, name, meta):
        return PanelUser(name, "active", 0, 0, 0, "", [])


DRIVERS = {c.TYPE: c for c in (XUI, Marzban, PasarGuard, Marzneshin, Hiddify, Manual)}
TYPES = [(c.TYPE, c.LABEL) for c in DRIVERS.values()]
# پنل‌هایی که در میرزا هست ولی هنوز درایور ندارند (فقط برای نمایش؛ انتخابشان پیام «به‌زودی» می‌دهد)
L, P = "\u2066", "\u2069"
SOON = [("wgdashboard", f"وایرگارد {L}WGDashboard{P}"), ("sui", f"اس‌یو‌آی {L}S-UI{P}"), ("ibsng", f"آی‌بی‌اس {L}IBSng{P}"),
        ("mikrotik", f"میکروتیک {L}Mikrotik{P}")]


def make_driver(panel: dict) -> BasePanel:
    cls = DRIVERS.get(panel.get("type"))
    if not cls:
        raise PanelError(f"نوع پنل ناشناخته: {panel.get('type')}")
    cfg = dict(panel)
    try:
        cfg["extra"] = json.loads(panel.get("extra") or "{}") if isinstance(panel.get("extra"), str) else (panel.get("extra") or {})
    except ValueError:
        cfg["extra"] = {}
    ids = panel.get("inbound_ids") or ""
    if isinstance(ids, str):
        cfg["inbound_ids"] = [x.strip() for x in re.split(r"[,\s]+", ids) if x.strip()]
    return cls(cfg)
