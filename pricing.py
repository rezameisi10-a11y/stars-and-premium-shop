"""نرخ‌های لحظه‌ای تون/دلار از API و موتور قیمت‌گذاری (هزینه‌ی تمام‌شده + سود مخفی).

- قیمت فروش به کاربر = هزینه × (۱ + سود٪) + کارمزد ثابت، گردشده‌ی بالا؛ کاربر هیچ‌وقت سود را نمی‌بیند.
- هزینه از چند روش: دستی (تومان/ستاره)، دلاری، تونی ثابت، یا API فراگمنت/تلگرام (قیمت به تون).
- نرخ تون و دلار از APIی که ادمین می‌دهد گرفته می‌شود و محصولات خودکار بازقیمت می‌شوند.
"""
import asyncio
import logging
import time
from decimal import ROUND_CEILING, Decimal

import marketapp
from apiutil import D, call_api, jpath, snippet

log = logging.getLogger("pricing")

PREMIUM_STARS = {3: 1000, 6: 1500, 12: 2500}   # هزینه‌ی ثابت پریمیوم هدیه‌ای در Bot API
GRACE_MIN = 15
API_MODES = ("ton_api", "marketapp")      # روش‌هایی که قیمت را زنده از API می‌گیرند


class Pricing:
    def __init__(self, shop):
        self.shop = shop
        self.rates: dict[str, dict] = {}        # 'usd' | 'ton' -> {raw, unit, ts, ok, err}
        self.costs: dict[tuple, dict] = {}      # (kind, qty) -> {cost, ts, ok, err}
        self._last_rates_run = 0.0
        self._last_costs_run = 0.0
        self._alert_ts: dict[str, float] = {}

    # ---------- دسترسی به تنظیمات ----------
    @property
    def s(self) -> dict:
        return self.shop._settings_cache or {}

    def _age_ok(self, ts, max_age_sec):
        return ts and (time.time() - ts) <= max_age_sec

    # ---------- نرخ‌ها ----------
    def _max_age(self):
        return max(int(D(self.s.get("rate_max_age_min"), 30)), 1) * 60

    def usd_toman(self) -> Decimal:
        s = self.s
        manual = D(s.get("usdt_toman"), 0)
        r = self.rates.get("usd")
        if s.get("rate_auto") == "1" and r and r["ok"] and self._age_ok(r["ts"], self._max_age()):
            v = r["raw"] / (10 if r["unit"] == "rial" else 1)
            return v * (1 + D(s.get("usd_spread_percent"), 0) / 100)
        return manual

    def ton_toman(self) -> Decimal:
        s = self.s
        manual = D(s.get("ton_toman"), 0)
        r = self.rates.get("ton")
        if s.get("rate_auto") == "1" and r and r["ok"] and self._age_ok(r["ts"], self._max_age()):
            if r["unit"] == "usd":
                v = r["raw"] * self.usd_toman()
            else:
                v = r["raw"] / (10 if r["unit"] == "rial" else 1)
            return v * (1 + D(s.get("ton_spread_percent"), 0) / 100)
        return manual

    def rate_source(self, key) -> str:
        """auto | manual | stale (API تنظیم است ولی جواب نمی‌دهد)"""
        s = self.s
        if s.get("rate_auto") != "1" or not s.get(f"{key}_api_url"):
            return "manual"
        r = self.rates.get(key)
        return "auto" if r and r["ok"] and self._age_ok(r["ts"], self._max_age()) else "stale"

    async def _fetch_rate(self, key: str, memo: dict):
        s = self.s
        url = s.get(f"{key}_api_url", "")
        if not url:
            return
        method, body = s.get(f"{key}_api_method", "GET"), s.get(f"{key}_api_body", "")
        sig = (url, method, body, s.get(f"{key}_api_headers", ""))
        try:
            if sig not in memo:
                memo[sig] = await call_api(self.shop.http, url, method, s.get(f"{key}_api_headers", ""), body, timeout=15)
            status, parsed, raw = memo[sig]
            if status >= 400:
                raise ValueError(f"HTTP {status}")
            val = D(jpath(parsed, s.get(f"{key}_api_path", "")), -1)
            if val <= 0:
                raise ValueError("value not positive/number")
            self.rates[key] = {"raw": val, "unit": s.get(f"{key}_api_unit", "toman"), "ts": time.time(), "ok": True, "err": ""}
        except Exception as e:  # noqa: BLE001
            old = self.rates.get(key, {})
            self.rates[key] = {"raw": old.get("raw", Decimal(0)), "unit": s.get(f"{key}_api_unit", "toman"),
                               "ts": old.get("ts", 0), "ok": False, "err": str(e)[:200]}
            log.warning("rate %s failed: %s", key, e)

    async def refresh_rates(self):
        memo: dict = {}
        await self._fetch_rate("usd", memo)
        await self._fetch_rate("ton", memo)
        self._last_rates_run = time.time()
        for key, fn in (("usd", self.usd_toman), ("ton", self.ton_toman)):
            v = fn()
            if v > 0 and self.rates.get(key, {}).get("ok"):
                hist = self.shop._rate_last.get(key)
                if hist is None or abs(float(v) - hist) > 1e-9:
                    self.shop._rate_last[key] = float(v)
                    await self.shop.db.add_rate_point(key, float(v))
        await self._alert_stale()

    async def _alert_stale(self):
        s = self.s
        if s.get("rate_auto") != "1":
            return
        for key, label in (("usd", "دلار/تتر"), ("ton", "تون")):
            if s.get(f"{key}_api_url") and self.rate_source(key) == "stale":
                if time.time() - self._alert_ts.get(key, 0) > 6 * 3600:
                    self._alert_ts[key] = time.time()
                    err = self.rates.get(key, {}).get("err", "")
                    await self.shop.notify_admins(
                        f"⚠️ API نرخ {label} پاسخ درست نمی‌دهد؛ فعلاً از نرخ دستی استفاده می‌شود.\n{err}", perm="pricing")

    async def probe(self, url, method, headers, body, path, vars=None):
        """آزمایش یک API (دکمه‌ی «تست» در پنل): خروجی dict با ok/status/value/preview/error."""
        out = {"ok": False, "status": None, "value": None, "preview": "", "error": ""}
        try:
            status, parsed, raw = await call_api(self.shop.http, url, method, headers, body, 15, vars)
            out["status"], out["preview"] = status, snippet(raw)
            if status >= 400:
                out["error"] = f"HTTP {status}"
                return out
            if path is not None and path != "__none__":
                v = jpath(parsed, path)
                out["value"] = str(v)
                d = D(v, -1)
                out["ok"] = d > 0 or isinstance(v, (bool, str))
                if out["ok"] and d > 0:
                    out["value"] = str(d)
            else:
                out["ok"] = True
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return out

    # ---------- هزینه‌ی تمام‌شده ----------
    def _api_to_toman(self, raw: Decimal, unit: str) -> Decimal:
        if unit == "nano":
            return raw / Decimal(10 ** 9) * self.ton_toman()
        if unit == "ton":
            return raw * self.ton_toman()
        if unit == "usd":
            return raw * self.usd_toman()
        return raw

    async def fetch_api_cost(self, kind: str, qty: int):
        """قیمت یک (kind, qty) از API فراگمنت/تلگرام؛ kind: stars | premium. خروجی Decimal تومان یا None."""
        s = self.s
        mode = s.get("price_mode_premium" if kind == "premium" else "price_mode_stars")
        if mode == "marketapp":
            return await self.fetch_marketapp_cost(kind, qty)
        p = f"frag_{kind}_"
        url = s.get(p + "url", "")
        if not url:
            return None
        vars_ = {"qty": qty} if kind == "stars" else {"months": qty, "qty": qty}
        try:
            status, parsed, raw = await call_api(self.shop.http, url, s.get(p + "method", "GET"), s.get(p + "headers", ""),
                                                 s.get(p + "body", ""), 15, vars_)
            if status >= 400:
                raise ValueError(f"HTTP {status}")
            val = D(jpath(parsed, s.get(p + "path", "")), -1)
            if val <= 0:
                raise ValueError("bad value")
            cost = self._api_to_toman(val, s.get(p + "unit", "ton"))
            self.costs[(kind, qty)] = {"cost": cost, "raw": val, "unit": s.get(p + "unit", "ton"), "ts": time.time(), "ok": True, "err": ""}
            return cost
        except Exception as e:  # noqa: BLE001
            old = self.costs.get((kind, qty), {})
            self.costs[(kind, qty)] = {**old, "ok": False, "err": str(e)[:200], "ts": old.get("ts", 0)}
            log.warning("api cost %s/%s failed: %s", kind, qty, e)
            return None

    def _store_mapp(self, key, quote: dict, ts: float):
        """ذخیره‌ی قیمت Marketapp؛ هزینه با نرخ‌های فعلی دلار/تون به تومان تبدیل می‌شود."""
        field = self.s.get("mapp_price_field", "usd")
        field = field if field in ("usd", "ton") else "usd"
        raw = quote.get(field) or Decimal(0)
        if raw <= 0:     # فیلد انتخابی خالی بود؛ از فیلد دیگر استفاده می‌کنیم
            field = "ton" if field == "usd" else "usd"
            raw = quote.get(field) or Decimal(0)
        if raw <= 0:
            raise ValueError("price missing")
        self.costs[key] = {"cost": self._api_to_toman(raw, field), "raw": raw, "unit": field, "ts": ts, "ok": True, "err": "",
                           "usd": quote.get("usd", Decimal(0)), "ton": quote.get("ton", Decimal(0))}

    async def fetch_marketapp_cost(self, kind: str, qty: int):
        """قیمت زنده از Marketapp (fragment/stars/price یا fragment/premium/price)؛ خروجی Decimal تومان یا None."""
        s = self.s
        try:
            now = time.time()
            if kind == "premium":
                prices = await marketapp.premium_prices(self.shop, s)
                for m, q in prices.items():
                    self._store_mapp(("premium", m), q, now)
                if qty not in prices:
                    raise ValueError(f"no premium price for {qty} months")
            else:
                if qty < marketapp.MIN_STARS:
                    raise ValueError(f"Fragment minimum is {marketapp.MIN_STARS} stars")
                self._store_mapp((kind, qty), await marketapp.stars_price(self.shop, s, qty), now)
            return self.costs[(kind, qty)]["cost"]
        except Exception as e:  # noqa: BLE001
            old = self.costs.get((kind, qty), {})
            self.costs[(kind, qty)] = {**old, "ok": False, "err": str(e)[:200], "ts": old.get("ts", 0)}
            log.warning("marketapp cost %s/%s failed: %s", kind, qty, e)
            return None

    def _api_cost(self, kind, qty):
        c = self.costs.get((kind, qty))
        if c and c.get("ok") and c.get("cost"):
            ttl = max(int(D(self.s.get("price_cache_sec"), 120)) * 5, 900)
            if self._age_ok(c["ts"], ttl):
                # قیمت API به واحد ton/nano/usd است؛ با نرخ‌های جدید دوباره به تومان تبدیل می‌شود
                return self._api_to_toman(c["raw"], c["unit"])
        return None

    def _star_unit_cost(self, mode) -> Decimal:
        s = self.s
        if mode == "usd":
            return D(s.get("star_usd_price")) * self.usd_toman()
        if mode == "ton_fixed":
            return D(s.get("star_ton_price")) * self.ton_toman()
        return D(s.get("star_buy_rate"), 2000)

    def stars_cost(self, n: int) -> Decimal:
        s = self.s
        mode = s.get("price_mode_stars", "manual")
        if mode in API_MODES:
            c = self._api_cost("stars", n)
            if c is not None:
                return c
            mode = "ton_fixed" if D(s.get("star_ton_price")) > 0 else "manual"
        if mode == "ton_fixed" and D(s.get("star_ton_price")) <= 0:
            mode = "manual"
        if mode == "usd" and D(s.get("star_usd_price")) <= 0:
            mode = "manual"
        return n * self._star_unit_cost(mode)

    def premium_cost(self, months: int) -> Decimal:
        s = self.s
        mode = s.get("price_mode_premium", "stars")
        if mode == "usd" and D(s.get(f"premium_usd_{months}")) > 0:
            return D(s[f"premium_usd_{months}"]) * self.usd_toman()
        if mode == "ton_fixed" and D(s.get(f"premium_ton_{months}")) > 0:
            return D(s[f"premium_ton_{months}"]) * self.ton_toman()
        if mode in API_MODES:
            c = self._api_cost("premium", months)
            if c is not None:
                return c
        return self.stars_cost(PREMIUM_STARS.get(months, months * 330))

    def cost_for(self, product: dict) -> Decimal:
        k = product["kind"]
        if k == "premium":
            return self.premium_cost(product["qty"])
        return self.stars_cost(product["qty"])      # استارز و گیفت (qty = تعداد ستاره)

    def margin_for(self, product: dict) -> Decimal:
        if product.get("margin_override") is not None:
            return D(product["margin_override"])
        s = self.s
        v = s.get(f"margin_{product['kind']}", "")
        return D(v) if str(v).strip() != "" else D(s.get("margin_percent"), 10)

    def quote(self, product: dict):
        """(قیمت فروش به تومان, هزینه‌ی تمام‌شده به تومان) — هر دو عدد صحیح."""
        s = self.s
        cost = self.cost_for(product)
        if product.get("price_override"):
            return int(product["price_override"]), int(cost.to_integral_value(rounding=ROUND_CEILING))
        raw = cost * (1 + self.margin_for(product) / 100) + D(s.get("fee_fixed"), 0)
        step = max(D(s.get("round_step"), 1000), Decimal(1))
        price = int((raw / step).to_integral_value(rounding=ROUND_CEILING) * step)
        price = max(price, int(D(s.get("min_price"), 1000)))
        return price, int(cost.to_integral_value(rounding=ROUND_CEILING))

    def _api_pairs(self, product: dict):
        s = self.s
        ms, mp = s.get("price_mode_stars"), s.get("price_mode_premium")

        def stars_ok(q):
            return ms in API_MODES and not (ms == "marketapp" and q < marketapp.MIN_STARS)
        if product["kind"] == "premium":
            if mp in API_MODES:
                return [("premium", product["qty"])]
            if mp == "stars" and stars_ok(PREMIUM_STARS.get(product["qty"], 0)):
                return [("stars", PREMIUM_STARS.get(product["qty"], 0))]
            return []
        return [("stars", product["qty"])] if stars_ok(product["qty"]) else []

    async def quote_fresh(self, product: dict):
        """مثل quote ولی اگر قیمت API کهنه است اول تازه می‌گیرد (هنگام ساخت سفارش)."""
        ttl = max(int(D(self.s.get("price_cache_sec"), 120)), 30)
        for pair in self._api_pairs(product):
            c = self.costs.get(pair)
            if not c or not c.get("ok") or time.time() - c["ts"] > ttl:
                await self.fetch_api_cost(*pair)
        return self.quote(product)

    async def refresh_costs(self):
        need: set[tuple] = set()
        for kind in ("stars", "premium", "gift"):
            for p in await self.shop.db.list_products(kind):
                need.update(self._api_pairs(p))
        if self.s.get("price_mode_premium") == "marketapp":      # یک درخواست قیمت همه‌ی ماه‌های پریمیوم را می‌دهد
            prem = sorted(p for p in need if p[0] == "premium")
            need -= set(prem[1:])
        sem = asyncio.Semaphore(4)

        async def one(pair):
            async with sem:
                await self.fetch_api_cost(*pair)

        if need:
            await asyncio.gather(*(one(p) for p in need))
        self._last_costs_run = time.time()

    async def tick(self):
        """از حلقه‌ی پس‌زمینه هر ~۳۰ ثانیه صدا زده می‌شود."""
        s = self.s
        now = time.time()
        iv = max(int(D(s.get("rate_interval_sec"), 120)), 30)
        if s.get("rate_auto") == "1" and now - self._last_rates_run >= iv:
            await self.refresh_rates()
        civ = max(int(D(s.get("price_cache_sec"), 120)), 30)
        if (s.get("price_mode_stars") in API_MODES or s.get("price_mode_premium") in API_MODES) and now - self._last_costs_run >= civ:
            await self.refresh_costs()

    # ---------- نمای کلی برای پنل ----------
    def snapshot(self) -> dict:
        out = {}
        for key in ("usd", "ton"):
            r = self.rates.get(key, {})
            out[key] = {"value": self.usd_toman() if key == "usd" else self.ton_toman(), "source": self.rate_source(key),
                        "ts": r.get("ts", 0), "err": r.get("err", ""), "ok": r.get("ok", False)}
        return out
