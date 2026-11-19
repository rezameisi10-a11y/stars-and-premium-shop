"""پرداخت با استارز برای سفارش VPN.  اجرا: (timeout 80 python3 tests/test_stars_pay.py > /tmp/t.txt 2>&1; true); tail /tmp/t.txt"""
import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_vpn import make
from test_all import new_user, ok as _ok


def ok(c, n):
    assert c, "FAIL: " + n
    _ok(n)


async def run(tmp):
    db, bot, sess, shop, dp = await make(tmp)
    u = await new_user(db, 7001)
    await shop.set_setting("star_buy_rate", "2000"); await shop.set_setting("price_mode_stars", "manual")
    pv = {"id": 1, "kind": "vpn", "title": "t", "qty": 1, "ref": "vpn:new:1"}
    oid = await db.create_order(7001, pv, None, None, 100000)
    shop.pricing.rates.clear()
    ok(not await shop.stars_ok(oid), "پیش‌فرض خاموش")
    await shop.set_setting("pm_stars", "1")
    ok(await shop.stars_ok(oid), "روشن + سفارش vpn")
    ok(shop.pricing.toman_to_stars(100000) == 50, "۱۰۰هزار ت ÷ ۲۰۰۰ = ۵۰ ستاره")
    kb = await shop.pay_methods_kb(str(oid), 7001, True, step="methods")
    ok(any(b.callback_data == f"pay:stars:{oid}" for r in kb.inline_keyboard for b in r), "دکمه استارز")
    o2 = await db.create_order(7001, {**pv, "ref": "stars:5", "kind": "stars"}, None, None, 1000)
    ok(await shop.stars_ok(o2), "سفارش غیر VPN هم استارز دارد (ادغام)")
    pid, text, _ = await shop.create_payment("stars", 7001, 100000, oid)
    ok(any(n == "SendInvoice" and m.currency == "XTR" and m.prices[0].amount == 50 and m.payload == f"pay:{pid}" for n, m in sess.calls), "فاکتور XTR ۵۰")
    ok(await shop.confirm_payment(pid, "xtr:a"), "تأیید")
    ok(not await shop.confirm_payment(pid, "xtr:a"), "تأیید دوباره رد")
    ok((await db.get_order(oid))["status"] != "pending_payment", "سفارش پرداخت شد")


asyncio.run(run(tempfile.mkdtemp()))
os._exit(0)
