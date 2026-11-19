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
    # --- انصراف + استارز برای سفارش غیر VPN
    prem = (await db.all("SELECT * FROM products WHERE kind='premium' LIMIT 1")) or (await db.all("SELECT * FROM products LIMIT 1"))
    prod = prem[0]
    oid = await db.create_order(5, prod, 5, None, 90000)
    if shop.pricing.toman_to_stars(90000) > 0:
        ok(await shop.stars_ok(oid), "استارز برای سفارش غیر VPN فعال")
        ok("stars" in [m["id"] for m in await ma._pay_methods(c, oid)], "stars در روش‌های سفارش")
    await shop.set_setting("pm_card", "1"); await shop.set_setting("card_number", "6037997000000000")
    r = await ma.a_pay(c, {"order_id": oid, "method": "card"})
    ok(r["order_id"] == oid, "a_pay: order_id")
    await ma.a_pay_cancel(c, {"payment_id": r["payment_id"]})
    ok((await db.get_payment(r["payment_id"]))["status"] == "cancelled", "انصراف از پرداخت")
    try:
        await ma.a_pay_cancel(c, {"payment_id": r["payment_id"]}); ok(False, "دوباره")
    except ma.ApiError:
        ok(True, "انصراف دوباره رد شد")
    ok((await db.get_order(oid))["status"] == "pending_payment", "سفارش بعد از انصراف از پرداخت سر جایش")
    r2 = await ma.a_pay(c, {"order_id": oid, "method": "card"})
    await db.update_payment(r2["payment_id"], status="waiting_admin")
    try:
        await ma.a_pay_cancel(c, {"payment_id": r2["payment_id"]}); ok(False, "رسید")
    except ma.ApiError:
        ok(True, "پرداخت با رسیدِ ارسال‌شده قابل انصراف نیست")
    await db.update_payment(r2["payment_id"], status="pending")
    await ma.a_order_cancel(c, {"order_id": oid})
    ok((await db.get_order(oid))["status"] == "cancelled" and (await db.get_payment(r2["payment_id"]))["status"] == "cancelled", "انصراف از سفارش + پرداخت‌هایش")
    c9 = ma.Ctx(shop, {"id": 9, "first_name": "x"}, "fa")
    ok(not await shop.cancel_order(oid, 9) and not await shop.cancel_payment(r["payment_id"], 9), "دیگران نمی‌توانند")
    kb = await shop.pay_methods_kb("t60000", 5, allow_wallet=False)
    ok(any(b.callback_data == "xo:t60000" for row in kb.inline_keyboard for b in row), "دکمه‌ی انصراف در ربات")
    w = await ma.a_wallet(c, {})
    ok(w["limits"]["min"] > 0 and "spent" in w["profile"] and "invited" in w["profile"], "پروفایل کامل + حدود شارژ")
    print("DONE")

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        await run(tmp)
asyncio.run(main())
