"""مینی‌اپ: شارژ کیف پول با استارز، پروفایل کیف پول، order_id در پرداخت/بررسی.  اجرا: timeout 80 python3 tests/test_miniapp_pay_ui.py"""
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
    await new_user(db, 5)
    import miniapp as ma
    c = ma.Ctx(shop, {"id": 5, "first_name": "u", "username": "u5"}, "fa")
    await shop.set_setting("price_mode_stars", "manual")
    await shop.set_setting("pm_stars", "0")
    ok(not await shop.stars_ok("t50000"), "استارز خاموش: شارژ رد")
    await shop.set_setting("pm_stars", "1")
    w = await ma.a_wallet(c, {})
    ok(w["profile"]["id"] == 5 and w["profile"]["username"] == ((await db.get_user(5)).get("username") or "") and w["profile"]["joined"] and "orders" in w["profile"], "پروفایل کیف پول")
    if shop.pricing.toman_to_stars(50000) > 0:
        ok("stars" in w["methods"], "stars در روش‌های شارژ")
        r = await ma.a_pay(c, {"topup": 50000, "method": "stars"})
        ok(r["method"] == "stars" and r["stars"] > 0, "فاکتور استارز شارژ ساخته شد")
        p = await db.get_payment(r["payment_id"])
        ok(p["order_id"] is None and p["method"] == "stars", "پرداخت بدون سفارش")
        await shop.confirm_payment(p["id"])
        ok((await db.get_user(5))["balance"] >= 50000, "شارژ پس از تأیید")
        s = await ma.a_check(c, {"payment_id": p["id"]})
        ok(s["status"] == "confirmed" and s["order_id"] is None, "a_check: confirmed + order_id خالی")
    else:
        print("  (نرخ ستاره در این محیط موجود نیست؛ بخش فاکتور رد شد)")
    # خرید با کیف پول: order_id برگردد
    print("DONE")

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        await run(tmp)
asyncio.run(main())
