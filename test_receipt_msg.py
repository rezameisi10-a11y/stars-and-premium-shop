"""پیام رسید برای ادمین (فرمت جدید) + دکمه‌ی بلاک.  اجرا: python tests/test_receipt_msg.py"""
import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aiohttp.test_utils import TestServer
from mock_panels import marzban_app
from test_vpn import make
from test_all import new_user, cbq, feed, ok as _ok


def ok(c, n):
    assert c, "FAIL: " + n
    _ok(n)


async def run(tmp):
    srv = TestServer(marzban_app()); await srv.start_server()
    db, bot, sess, shop, dp = await make(tmp)
    import handlers_admin
    handlers_admin.router._parent_router = None
    dp.include_router(handlers_admin.router)
    await shop.set_setting("vpn_enabled", "1")
    for u in (1, 5): await new_user(db, u)
    pa = await db.add_vpn_panel(name="DE", type="marzban", url=str(srv.make_url("")).rstrip("/"), username="a", password="pw")
    plan = await db.add_vpn_plan(title="40 گیگ 30 روز", panel_id=pa, days=30, gb=40, price=400000, cost=200000)
    o = await shop.vpn.create_order(5, plan, name="myacc")
    p = await db.get_payment(await db.create_payment(5, o, "card", 400000))
    cap = await shop.receipt_caption(p)
    ok("یک پرداخت جدید انجام شده است" in cap and "🔴🔴🔴🔴🔴" in cap, "سرتیتر")
    ok("خرید سرویس جدید" in cap and "myacc" in cap and "حجم محصول : 40 گیگ" in cap and "زمان محصول : 30 روز" in cap, "سرویس و محصول و حجم/زمان")
    ok("شناسه کاربر : <code>5</code>" in cap and "موجودی فعلی کاربر" in cap and f"P{p['id']}" in cap and "مبلغ پرداختی" in cap and "400,000" in cap, "مشخصات کاربر و پرداخت")
    ok(cap.rstrip().endswith("تایید نمایید."), "جمله‌ی پایانی")
    kb = shop.receipt_kb(p)
    cds = [b.callback_data for r in kb.inline_keyboard for b in r]
    ok(cds == [f"padm:ok:{p['id']}", f"padm:no:{p['id']}", "adm:ub:5:a", f"padm:bn:{p['id']}"], "دکمه‌ها: تایید/رد/موجودی دستی/بلاک")
    c2 = await shop.receipt_caption(await db.get_payment(await db.create_payment(5, None, "card", 50000)))
    ok("شارژ کیف پول" in c2, "شارژ کیف پول")
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(1, f"padm:bn:{p['id']}"))
    ok(not (await db.get_user(5)).get("banned"), "بلاک اول فقط تأیید می‌خواهد")
    await feed(dp, bot, callback_query=cbq(1, f"padm:bny:{p['id']}"))
    ok((await db.get_user(5)).get("banned"), "کاربر بلاک شد")
    await feed(dp, bot, callback_query=cbq(1, f"padm:nx:{p['id']}"))
    ok(True, "انصراف: کیبورد برگشت")
    print("RECEIPT MSG DONE")


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        await run(tmp)
    os._exit(0)

asyncio.run(main())
