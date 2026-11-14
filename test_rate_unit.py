"""تست تشخیص خودکار واحد نرخ (ریال/تومان/دلار) و محافظ جهش قیمت. اجرا: python tests/test_rate_unit.py"""
import asyncio, os, sys
from decimal import Decimal as D
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pricing


def ok(c, n):
    assert c, "FAIL: " + n
    print("  ✓", n)


class P(pricing.Pricing):
    def __init__(self): self.rates = {}
    def usd_toman(self): return D(180000)


p = P()
r = p.resolve_unit
ok(r("usd", D(1800000), "toman", "https://api.nobitex.ir/market/stats", '{"dstCurrency":"rls"}', "stats.usdt-rls.latest") == "rial", "نوبیتکس با rls حتی اگر toman انتخاب شده → ریال")
ok(r("usd", D(1800000), "auto", "https://x", "", "price") == "rial", "دلار ۱٫۸م خودکار → ریال")
ok(r("usd", D(180000), "auto", "https://x", "", "price") == "toman", "دلار ۱۸۰هزار خودکار → تومان")
ok(r("ton", D(5400000), "auto", "https://x", "", "p") == "rial", "تون ۵٫۴م → ریال")
ok(r("ton", D(540000), "auto", "https://x", "", "p") == "toman", "تون ۵۴۰هزار → تومان")
ok(r("ton", D("3.2"), "toman", "https://coingecko", "", "ton.usd") == "usd", "تون ۳٫۲ (دلار) → usd")
ok(r("ton", D(540000), "auto", "https://api.wallex.ir", "", "result.symbols.TONTMN.stats.lastPrice") == "toman", "والکس TMN → تومان")
ok(p._rate_toman("usd", D(1800000), "rial") == D(180000), "ریال به تومان ÷۱۰")
print("RATE UNIT TESTS DONE")
