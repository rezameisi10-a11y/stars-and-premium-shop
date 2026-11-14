"""امنیت تحویل استارز/پریمیوم: ضد ارسال تکراری، نتیجه‌ی نامشخص، سقف تلاش، سقف کاربر/تکراری، مدارشکن، بازیابی.
اجرا: (timeout 100 python3 tests/test_delivery_safety.py > /tmp/t.txt 2>&1; true); tail /tmp/t.txt"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_vpn import make
from test_all import new_user, ok as _ok
import provider


def ok(c, n):
    assert c, "FAIL: " + n
    _ok(n)


async def run(tmp):
    db, bot, sess, shop, dp = await make(tmp)
    calls = []
    plan = {"res": "ok"}

    async def fake_deliver(shop_, kind, order):
        calls.append(order["id"])
        r = plan["res"]
        if r == "raise":
            raise RuntimeError("boom")
        return r, "x", "REF%d" % order["id"]
    provider.deliver = fake_deliver
    provider.provider_enabled = lambda s, kind: True
    for k, v in (("deliver_user_day_max", "0"), ("deliver_dup_min", "0"), ("deliver_breaker_n", "0"), ("deliver_max_attempts", "2")):
        await shop.set_setting(k, v)

    async def mk(uid, qty=50, tgt="bob"):
        await new_user(db, uid)
        o = await db.create_order(uid, {"id": 1, "kind": "stars", "title": "s", "qty": qty, "ref": ""}, 0, tgt, 1000)
        await db.transition(o, ["pending_payment"], "paid")
        return o

    # 1) موفق = یک ارسال؛ اجرای دوباره (حتی با وضعیت دستکاری‌شده) ارسال دوباره نمی‌کند
    o = await mk(9001)
    await shop.fulfil(o)
    ok((await db.get_order(o))["status"] == "done" and calls == [o], "موفق → done و یک ارسال")
    await db.run("UPDATE orders SET status='paid' WHERE id=?", (o,))
    await shop.fulfil(o)
    ok(calls == [o] and (await db.get_order(o))["status"] == "done", "اجرای دوباره: ارسال تکراری نشد (بازیابی از لاگ)")

    # 2) نامشخص → manual UNKNOWN؛ retry عادی رد؛ retryf فقط با تأیید
    plan["res"] = "unknown"
    o2 = await mk(9002)
    await shop.fulfil(o2)
    od = await db.get_order(o2)
    ok(od["status"] == "manual_pending" and od["note"].startswith("UNKNOWN:"), "نامشخص → صف دستی UNKNOWN")
    n = len(calls)
    msg = await shop.admin_order_action("retry", o2)
    ok(len(calls) == n and "نامشخص" in msg, "تلاش مجدد عادی بعد از نامشخص رد شد")
    ok(await shop.retry_queue(include_failed=True) == 0 and len(calls) == n, "retry_queue نامشخص را نمی‌زند")
    plan["res"] = "ok"
    await shop.admin_order_action("retryf", o2)
    ok((await db.get_order(o2))["status"] == "done" and len(calls) == n + 1, "retryf با تأیید ادمین ارسال کرد")

    # 3) شکست قطعی: سقف تلاش ۲ برای خودکار؛ ادمین می‌تواند
    plan["res"] = "fail"
    o3 = await mk(9003)
    await shop.fulfil(o3)
    ok((await db.get_order(o3))["status"] == "failed", "fail → failed")
    await db.run("UPDATE delivery_log SET finished=finished-600, started=started-600")
    await shop.fulfil(o3)
    await db.run("UPDATE delivery_log SET finished=finished-600, started=started-600")
    n = len(calls)
    await shop.fulfil(o3)
    od = await db.get_order(o3)
    ok(len(calls) == n and od["status"] == "manual_pending" and od["note"].startswith("HOLD:"), "بعد از ۲ شکست: HOLD و ارسال سوم انجام نشد")
    plan["res"] = "ok"
    await shop.admin_order_action("retry", o3)
    ok((await db.get_order(o3))["status"] == "done", "ادمین HOLD را override کرد")

    # 4) استثنای غیرمنتظره داخل provider = نامشخص (نه failed)
    plan["res"] = "raise"
    o4 = await mk(9004)
    await shop.fulfil(o4)
    ok((await db.get_order(o4))["note"].startswith("UNKNOWN:"), "استثنا حین ارسال = UNKNOWN")
    plan["res"] = "ok"

    # 5) سقف هر کاربر + ضد تکراری
    await shop.set_setting("deliver_user_day_max", "1")
    u5 = 9005
    a = await mk(u5, 50, "carl"); await shop.fulfil(a)
    b = await mk(u5, 100, "carl2"); n = len(calls); await shop.fulfil(b)
    ok(len(calls) == n and (await db.get_order(b))["note"].startswith("HOLD:"), "سقف روزانه‌ی کاربر: سفارش دوم نگه داشته شد")
    await shop.set_setting("deliver_user_day_max", "0"); await shop.set_setting("deliver_dup_min", "10")
    c = await mk(9006, 50, "dave"); await shop.fulfil(c)
    d = await mk(9007, 50, "dave"); n = len(calls); await shop.fulfil(d)
    ok(len(calls) == n and "تکراری" in (await db.get_order(d))["note"], "سفارش تکراریِ هم‌گیرنده نگه داشته شد")
    await shop.set_setting("deliver_dup_min", "0")

    # 6) سقف ستاره‌ی هر سفارش
    await shop.set_setting("deliver_max_stars", "100")
    e = await mk(9008, 500, "erin"); n = len(calls); await shop.fulfil(e)
    ok(len(calls) == n and (await db.get_order(e))["status"] == "manual_pending", "بیش از سقف ستاره → دستی")
    await shop.set_setting("deliver_max_stars", "0")

    # 7) مدارشکن
    await shop.set_setting("deliver_breaker_n", "2")
    plan["res"] = "fail"
    f1 = await mk(9009, 50, "f1"); await shop.fulfil(f1)
    f2 = await mk(9010, 50, "f2"); await shop.fulfil(f2)
    plan["res"] = "ok"
    f3 = await mk(9011, 50, "f3"); n = len(calls); await shop.fulfil(f3)
    ok(len(calls) == n and "متوقف" in (await db.get_order(f3))["note"], "مدارشکن: بعد از ۲ شکست سفارش‌های جدید ارسال نشد")
    await shop.resume_delivery()
    await shop.admin_order_action("retry", f3)
    ok((await db.get_order(f3))["status"] == "done", "بعد از از‌سرگیری ارسال شد")

    # 8) بازیابی سفارش نیمه‌کار
    g = await mk(9012, 50, "g1")
    await db.transition(g, ["paid"], "delivering")
    await db.dl_claim(g, "stars", "api")
    await db.run("UPDATE orders SET updated_at=updated_at-3600 WHERE id=?", (g,))
    await shop.recover_stuck(minutes=10)
    od = await db.get_order(g)
    ok(od["status"] == "manual_pending" and od["note"].startswith("UNKNOWN:"), "ریستارت حین ارسال → UNKNOWN (بدون ارسال مجدد)")
    print("DELIVERY SAFETY TESTS DONE")


asyncio.run(run(tempfile.mkdtemp()))
os._exit(0)
