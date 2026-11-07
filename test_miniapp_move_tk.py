"""مینی‌اپ: تغییر لوکیشن + تیکت.  اجرا: (timeout 80 python3 tests/test_miniapp_move_tk.py > /tmp/t.txt 2>&1; true); tail /tmp/t.txt"""
import asyncio, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aiohttp.test_utils import TestServer
from mock_panels import marzban_app
from test_vpn import make
from test_all import new_user, ok as _ok

GB = 1024 ** 3


def ok(c, n):
    assert c, "FAIL: " + n
    _ok(n)


async def run(tmp):
    A, B = marzban_app(), marzban_app()
    sa, sb = TestServer(A), TestServer(B)
    await sa.start_server(); await sb.start_server()
    db, bot, sess, shop, dp = await make(tmp)
    await new_user(db, 1); await new_user(db, 5); await new_user(db, 6)
    pa = await db.add_vpn_panel(name="DE", type="marzban", url=str(sa.make_url("")).rstrip("/"), username="a", password="pw")
    pb = await db.add_vpn_panel(name="NL", type="marzban", url=str(sb.make_url("")).rstrip("/"), username="a", password="pw")
    plan = await db.add_vpn_plan(title="30d", panel_id=pa, days=30, gb=10, price=100000, cost=60000)
    o = await shop.vpn.create_order(5, plan)
    await db.run("UPDATE orders SET status='delivering' WHERE id=?", (o,))
    await shop.vpn.fulfil(await db.get_order(o))
    svc = await db.vpn_service_by_order(o)
    sid = svc["id"]
    import miniapp as ma
    c5 = ma.Ctx(shop, {"id": 5, "first_name": "u", "username": "u5"}, "fa")
    c6 = ma.Ctx(shop, {"id": 6, "first_name": "v"}, "fa")
    ok("vpn_move" in ma.ACTIONS and "tk_new" in ma.ACTIONS, "اکشن‌ها ثبت شدند")
    # --- لوکیشن
    try:
        await ma.a_vpn_move_opts(c5, {"id": sid}); ok(False, "خاموش")
    except ma.ApiError:
        ok(True, "تغییر لوکیشن خاموش: رد شد")
    await shop.set_setting("vpn_allow_move", "1")
    await db.update_vpn_panel(pb, extra=json.dumps({"move_to": 1}))
    await shop.vpn.ops.refresh_dest()
    r = await ma.a_vpn_move_opts(c5, {"id": sid})
    ok([t["id"] for t in r["targets"]] == [pb] and r["service"]["can_move"], "گزینه‌های مقصد + can_move")
    try:
        await ma.a_vpn_move_opts(c6, {"id": sid}); ok(False, "دیگران")
    except ma.ApiError:
        ok(True, "سرویس دیگران رد شد")
    x = await ma.a_vpn_move(c5, {"id": sid, "panel_id": pb})
    ok((await db.get_vpn_service(sid))["panel_id"] == pb and x["message"], "انتقال از مینی‌اپ")
    # --- تیکت
    await shop.set_setting("tickets_on", "1")
    t = await ma.a_tk_new(c5, {"text": "سلام\nمشکل دارم"})
    ok(t["id"], "تیکت ساخته شد")
    ok(len((await ma.a_tk_list(c5, {}))["tickets"]) == 1, "لیست تیکت")
    g = await ma.a_tk_get(c5, {"id": t["id"]})
    ok(g["msgs"][0]["me"] and g["subject"] == "سلام", "نمایش تیکت")
    try:
        await ma.a_tk_get(c6, {"id": t["id"]}); ok(False, "دیگران")
    except ma.ApiError:
        ok(True, "تیکت دیگران دیده نمی‌شود")
    await db.tk_add(t["id"], "admin", "پاسخ", admin_id=1)
    import handlers_tickets as ht; ht.MIN_GAP = 0
    await ma.a_tk_reply(c5, {"id": t["id"], "text": "ممنون"})
    ok(len((await ma.a_tk_get(c5, {"id": t["id"]}))["msgs"]) == 3, "پاسخ کاربر")
    await ma.a_tk_close(c5, {"id": t["id"]})
    ok((await ma.a_tk_get(c5, {"id": t["id"]}))["status"] == "closed", "بستن")
    try:
        await ma.a_tk_reply(c5, {"id": t["id"], "text": "x"}); ok(False, "بسته")
    except ma.ApiError:
        ok(True, "پاسخ به تیکت بسته رد شد")
    await shop.set_setting("tickets_on", "0")
    try:
        await ma.a_tk_list(c5, {}); ok(False, "off")
    except ma.ApiError:
        ok(True, "تیکت خاموش")
    print("MINIAPP MOVE/TK DONE")


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        await run(tmp)
    os._exit(0)

asyncio.run(main())
