"""تست بخش VPN: درایور مرزبان (سرور جعلی)، سفارش/تحویل/تمدید، پنل دستی، قابلیت‌های هر پنل، تست رایگان و منوهای ادمین.
اجرا:  python tests/test_vpn.py"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp.test_utils import TestServer

import admin_vpn
import admin_vpn_agent
import handlers_vpn
from middlewares import GateMiddleware
from mock_panels import marzban_app
from test_all import build, cbq, feed, msg, new_user, ok
from vpn_service import VpnError, pflag


async def make(tmp):
    db, bot, sess, shop = await build(tmp)
    dp = Dispatcher(storage=MemoryStorage(), shop=shop)
    g = GateMiddleware()
    dp.message.outer_middleware(g)
    dp.callback_query.outer_middleware(g)
    for r in (admin_vpn.router, admin_vpn_agent.router, handlers_vpn.router):
        r._parent_router = None
        dp.include_router(r)
    return db, bot, sess, shop, dp


def texts(sess):
    out = []
    for n, m in sess.calls:
        if n in ("SendMessage", "EditMessageText"):
            out.append(getattr(m, "text", "") or "")
    return out


def all_buttons(sess):
    out = []
    for n, m in sess.calls:
        kb = getattr(m, "reply_markup", None)
        if n in ("SendMessage", "EditMessageText") and kb:
            out += [(b.text, b.callback_data) for r in kb.inline_keyboard for b in r]
    return out


async def run(tmp):
    srv = TestServer(marzban_app())
    await srv.start_server()
    url = str(srv.make_url("")).rstrip("/")
    db, bot, sess, shop, dp = await make(tmp)
    await shop.set_setting("vpn_enabled", "1")
    await new_user(db, 5)
    await db.add_balance(5, 1_000_000, "t")

    pid = await db.add_vpn_panel(name="DE", type="marzban", url=url, username="a", password="pw")
    ok_, info = await shop.vpn.test_panel(await db.get_vpn_panel(pid))
    assert ok_ and info["version"] == "v0.8.4", info
    ok("مرزبان: تست اتصال و گزینه‌ها")
    plan = await db.add_vpn_plan(title="30d", panel_id=pid, days=30, gb=10, price=100000, cost=60000)
    mp = await db.add_vpn_panel(name="Manual", type="manual")
    mplan = await db.add_vpn_plan(title="man", panel_id=mp, days=30, gb=5, price=50000)

    # سفارش و تحویل
    oid = await shop.vpn.create_order(5, plan)
    await db.run("UPDATE orders SET status='delivering' WHERE id=?", (oid,))
    await shop.vpn.fulfil(await db.get_order(oid))
    svc = (await db.list_vpn_services(user_id=5))[0]
    assert svc["status"] == "active" and svc["total_bytes"] == 10 * 1024 ** 3 and svc["sub_url"].startswith(url)
    ok("خرید ← ساخت سرویس روی پنل")

    # تمدید + افزودنی
    oid = await shop.vpn.renew_order(5, svc["id"], plan)
    await db.run("UPDATE orders SET status='delivering' WHERE id=?", (oid,))
    await shop.vpn.fulfil(await db.get_order(oid))
    ok("تمدید")
    ok_, _m = await shop.vpn.admin_action(svc["id"], "add_gb", 2)
    assert ok_
    assert (await db.get_vpn_service(svc["id"]))["total_bytes"] == 12 * 1024 ** 3
    ok("افزودن حجم از ادمین")

    # قابلیت‌های هر پنل
    await dp.feed_update(bot, __import__("aiogram").types.Update(update_id=1, callback_query=cbq(1, f"adm:vpn:pft:{pid}:renew")))
    assert not pflag(await db.get_vpn_panel(pid), "renew")
    try:
        await shop.vpn.renew_order(5, svc["id"], plan)
        raise AssertionError("renew باید بسته باشد")
    except VpnError:
        pass
    ok("خاموش کردن «تمدید» برای یک پنل کار می‌کند")
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:pft:{pid}:show"))
    assert not [p for p in await shop.vpn.plans_for(await db.get_user(5)) if p["panel_id"] == pid]
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:pft:{pid}:show"))
    assert [p for p in await shop.vpn.plans_for(await db.get_user(5)) if p["panel_id"] == pid]
    ok("مخفی/نمایش پنل")

    # مخفی برای یک کاربر
    await feed(dp, bot, message=msg(1, "x"))  # بی‌اثر
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:pha:{pid}"))
    await feed(dp, bot, message=msg(1, "5"))
    assert not [p for p in await shop.vpn.plans_for(await db.get_user(5)) if p["panel_id"] == pid]
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:phr:{pid}:5"))
    assert [p for p in await shop.vpn.plans_for(await db.get_user(5)) if p["panel_id"] == pid]
    ok("مخفی کردن پنل برای یک کاربر و برداشتن آن")

    # اکانت تست + قفل همزمانی
    await shop.set_setting("vpn_test_enabled", "1")
    await new_user(db, 6)
    res = await asyncio.gather(shop.vpn.create_test(6), shop.vpn.create_test(6), return_exceptions=True)
    assert sum(isinstance(r, int) for r in res) == 1, res
    ok("اکانت تست: فقط یکی با دو کلیک همزمان + پنل دستی رد می‌شود")
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:pe:{pid}:test_days"))
    await feed(dp, bot, message=msg(1, "3"))
    assert '"test_days": 3' in (await db.get_vpn_panel(pid))["extra"]
    ok("روز تست مخصوص پنل")

    # پنل دستی: خرید، تمدید، حذف
    oid = await shop.vpn.create_order(5, mplan)
    await db.run("UPDATE orders SET status='delivering' WHERE id=?", (oid,))
    await shop.vpn.fulfil(await db.get_order(oid))
    ms = [s for s in await db.list_vpn_services(user_id=5) if s["panel_id"] == mp][0]
    assert ms["status"] == "pending_manual"
    ok_, _m = await shop.vpn.deliver_manual(ms["id"], "https://sub.example/x")
    assert ok_
    oid = await shop.vpn.renew_order(5, ms["id"], mplan)
    await db.run("UPDATE orders SET status='delivering' WHERE id=?", (oid,))
    await shop.vpn.fulfil(await db.get_order(oid))
    assert (await db.get_order(oid))["status"] == "manual_pending"
    ok_, _m = await shop.vpn.admin_action(ms["id"], "add_days", 5)
    assert ok_
    ok_, _m = await shop.vpn.admin_delete(ms["id"])
    assert ok_
    ok("پنل دستی: تحویل، تمدید دستی، افزودن روز و حذف بدون خطا")

    # صفحه‌های ادمین
    for d in ("adm:vpn", "adm:vpn:ft", "adm:vpn:pl", f"adm:vpn:p:{pid}", f"adm:vpn:pf:{pid}", f"adm:vpn:ph:{pid}", "adm:vpn:pa", "adm:vpn:us:0",
              "adm:vpn:u:5", f"adm:vpn:ps:{pid}", "adm:vpn:pn", "adm:vpn:ad", "adm:vpn:sv:all:0", "adm:vpn:uv:5"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(1, d))
        assert len(sess.calls) > n, d
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:ftg:vpn_allow_addon"))
    assert shop.s["vpn_allow_addon"] == "0"
    ok("همه‌ی صفحه‌های ادمین VPN باز می‌شوند")
    btns = all_buttons(sess)
    assert any("مدیریت" in t for t, _ in btns) and any(t == "وضعیت" for t, _ in btns)
    ok("جدول‌های «عنوان | وضعیت» و لیست کاربران")
    # ویزارد «افزودن محصول» مثل میرزا
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:pna"))
    for t in ("30 روزه 50 گیگ", "n", "/all", "50", "30", "150000", "یادداشت تست"):
        await feed(dp, bot, message=msg(1, t))
    plans = [p for p in await db.list_vpn_plans() if p["title"].startswith("30 روزه")]
    assert len(plans) == 2 and all(p["utype"] == "n" and p["note"] == "یادداشت تست" and p["price"] == 150000 for p in plans), plans
    assert not [p for p in await shop.vpn.plans_for(await db.get_user(5)) if p["utype"] == "n"]
    await db.set_vpn_level(5, "n")
    assert [p for p in await shop.vpn.plans_for(await db.get_user(5)) if p["utype"] == "n"]
    await db.set_vpn_level(5, "f")
    ok("ویزارد محصول: نوع کاربر، همه موقعیت‌ها، یادداشت؛ نمایش فقط برای همان نوع")

    # کارت کاربر و عملیات
    for a_, expect in (("bp", None), ):
        await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:ua:5:{a_}"))
        await feed(dp, bot, message=msg(1, "1000"))
    b0 = (await db.get_user(5))["balance"]
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:ua:5:ban"))
    assert (await db.get_user(5))["banned"]
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:ua:5:unban"))
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:ua:5:lvn"))
    assert (await db.get_user(5))["vpn_level"] == "n"
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:ua:5:agx"))
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:ua:5:disc"))
    await feed(dp, bot, message=msg(1, "10"))
    assert (await db.get_user(5))["personal_discount"] == 10
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:ua:5:b0"))
    assert (await db.get_user(5))["balance"] == 0 and b0 > 0
    for a_ in ("ord", "pay", "on", "off", "test"):
        await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:ua:5:{a_}"))
    ok("کارت کاربر: موجودی، مسدودی، نماینده، تخفیف، سفارش‌ها")

    # QR + مشخصات در یک پیام (عکس با کپشن)
    n = len(sess.calls)
    await shop.vpn.send_service(5, await db.get_vpn_service(svc["id"]), new=True)
    ph = [m for nm, m in sess.calls[n:] if nm == "SendPhoto"]
    assert len(ph) == 1 and "نام کاربری" in (ph[0].caption or "") and ph[0].reply_markup, "QR و مشخصات باید یک پیام باشند"
    ok("QR و مشخصات اشتراک در یک پیام")
    for bg in ("none", "ocean", "sunset"):
        assert (await shop.vpn.qr_png("https://x.example/sub/1", bg))[:4] == b"\x89PNG"
    ok("پس‌زمینه‌های QR")

    # مانیتورینگ
    okk, txt = await shop.vpn.panel_status_text(pid)
    assert okk and "وضعیت سرور" in txt, txt
    ok("مانیتورینگ سرور")
    assert "نودها" in txt and "DE-1" in txt and "NL-2" in txt and "🟢 متصل" in txt and "🔴 خطا" in txt and "connection refused" in txt and "1/2" in txt, txt
    ok("وضعیت هر نود جداگانه")
    await db.close()
    await srv.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(run(tmp))
    print("OK")
