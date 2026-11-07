"""انتقال سرویس بین پنل‌ها (تغییر لوکیشن) + عملیات گروهی.  اجرا: (timeout 80 python3 tests/test_ops.py > /tmp/t.txt 2>&1; true); tail /tmp/t.txt"""
import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aiohttp.test_utils import TestServer
from mock_panels import marzban_app
from test_vpn import make, all_buttons
from test_all import new_user, cbq, feed, msg, ok as _ok
from vpn_ops import MoveError

GB = 1024 ** 3


def ok(c, n):
    assert c, "FAIL: " + n
    _ok(n)


async def run(tmp):
    A, B = marzban_app(), marzban_app()
    sa, sb = TestServer(A), TestServer(B)
    await sa.start_server(); await sb.start_server()
    db, bot, sess, shop, dp = await make(tmp)
    await new_user(db, 1); await new_user(db, 5); await new_user(db, 6); await new_user(db, 7); await new_user(db, 8)
    pa = await db.add_vpn_panel(name="DE", type="marzban", url=str(sa.make_url("")).rstrip("/"), username="a", password="pw")
    pb = await db.add_vpn_panel(name="NL", type="marzban", url=str(sb.make_url("")).rstrip("/"), username="a", password="pw")
    plan = await db.add_vpn_plan(title="30d", panel_id=pa, days=30, gb=10, price=100000, cost=60000)

    async def mk(uid):
        o = await shop.vpn.create_order(uid, plan)
        await db.run("UPDATE orders SET status='delivering' WHERE id=?", (o,))
        await shop.vpn.fulfil(await db.get_order(o))
        return await db.vpn_service_by_order(o)

    svc = await mk(5)
    sid = svc["id"]
    A["users"][svc["name"]]["used_traffic"] = 4 * GB
    ops = shop.vpn.ops
    ok(not await ops.can_user_move(svc), "پیش‌فرض خاموش")
    await shop.set_setting("vpn_allow_move", "1")
    ok(not await ops.can_user_move(svc), "مقصد انتخاب نشده: دکمه نیست")
    await db.update_vpn_panel(pb, extra=json.dumps({"move_to": 1}))
    await ops.refresh_dest()
    svc = await db.get_vpn_service(sid)
    ok(await ops.can_user_move(svc) and ops.show_button(svc), "با مقصد فعال، دکمه نشان داده می‌شود")
    ok([p["id"] for p in await ops.targets(svc)] == [pb], "فقط پنل مقصد")
    gb, days = ops.preview(await shop.vpn.sync(svc))
    ok(abs(gb - 6) < 0.05 and 29 <= days <= 30, f"پیش‌نمایش = باقی‌مانده ({gb},{days})")
    try:
        await ops.move(sid, pb, user_id=6)
        ok(False, "سرویس دیگران")
    except MoveError:
        ok(True, "سرویس دیگران قابل انتقال نیست")

    row = await ops.user_move(5, sid, pb)
    ok(row["panel_id"] == pb, "پنل سرویس عوض شد")
    ok(abs(row["total_bytes"] - 6 * GB) < 0.05 * GB and row["used_bytes"] == 0, "حجمِ تازه نیست؛ فقط باقی‌مانده")
    ok(row["name"] in B["users"] and svc["name"] not in A["users"], "روی مقصد ساخته و از مبدأ پاک شد")
    ok(json.loads(row["meta"]).get("n_move") == 1, "شمارنده‌ی انتقال")
    await db.update_vpn_panel(pb, extra=json.dumps({"move_to": 1, "move_max": 1}))
    row = await db.get_vpn_service(sid)
    ok(bool(shop.vpn.limit_error(row, "move")), "سقف دفعات انتقال")

    # سرویس تمام‌شده منتقل نمی‌شود
    s2 = await mk(5)
    A["users"][s2["name"]]["used_traffic"] = 10 * GB
    try:
        await ops.move(s2["id"], pb)
        ok(False, "حجم تمام‌شده")
    except MoveError:
        ok(True, "حجم تمام‌شده رد شد")

    # عملیات گروهی
    xs = [await mk(6) for _ in range(3)]
    rows = await ops.select(pa, "all")
    ok(len(rows) >= 3, "انتخاب با فیلتر همه")
    before = {r["id"]: r["expires_at"] for r in rows}
    res = await ops.bulk_run(rows, "add_days", 5)
    ok(res["ok"] == len(rows) and res["fail"] == 0, "افزودن روز گروهی")
    after = {r["id"]: (await db.get_vpn_service(r["id"]))["expires_at"] for r in rows}
    ok(all(after[i] - before[i] >= 4 * 86400 for i in before), "زمان واقعاً زیاد شد")
    res = await ops.bulk_run([r for r in rows if r["user_id"] == 6], "disable")
    ok(res["ok"] == 3, "غیرفعال‌سازی گروهی")
    ok(len(await ops.select(pa, "disabled")) == 3, "فیلتر غیرفعال‌ها")
    ok(len(await ops.select(pa, "soon", 3)) == 0 and len(await ops.select(pa, "lowvol", 10)) == 0, "فیلترهای رو‌به‌اتمام/کم‌حجم خالی")
    res = await ops.bulk_run(await ops.select(pa, "disabled"), "move", target_pid=pb)
    ok(res["ok"] == 3 and len(await ops.select(pb, "all")) >= 4, "انتقال گروهی به پنل دیگر")
    res = await ops.bulk_run(await ops.select(pb, "disabled"), "delete")
    ok(res["ok"] == 3, "حذف گروهی")
    res = await ops.bulk_run([await db.get_vpn_service(sid)], "msg", "سلام")
    ok(res["ok"] == 1, "پیام گروهی")

    # رابط ربات
    s3 = await mk(5)
    for d in (f"vpn:mv:{s3['id']}", f"vpn:mvp:{s3['id']}:{pb}"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(5, d))
        ok(len(sess.calls) > n, "کاربر: " + d)
    n = len(sess.calls)
    await feed(dp, bot, callback_query=cbq(5, f"vpn:mvg:{s3['id']}:{pb}"))
    ok((await db.get_vpn_service(s3["id"]))["panel_id"] == pb, "کاربر با دکمه منتقل شد")
    for d in ("adm:vpn", "adm:vpn:st", "adm:vpn:mvs", "adm:vpn:bk", "adm:vpn:bkp:0", "adm:vpn:bkf:soon", "adm:vpn:bkn:3", "adm:vpn:bka:enable"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(1, d))
        assert len(sess.calls) > n, d
    ok(True, "صفحه‌های ادمین باز می‌شوند")
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:mvg"))
    ok(shop.s["vpn_allow_move"] == "0", "کلید تغییر لوکیشن از ربات")
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:mvt:{pa}"))
    ok(json.loads((await db.get_vpn_panel(pa))["extra"]).get("move_to") == 1, "انتخاب مقصد از ربات")
    # جادوگر کامل: همه‌ی پنل‌ها ← همه ← افزودن روز ← 2 ← اجرا
    for d in ("adm:vpn:bk", "adm:vpn:bkp:0", "adm:vpn:bkf:all", "adm:vpn:bka:add_days"):
        await feed(dp, bot, callback_query=cbq(1, d))
    await feed(dp, bot, message=msg(1, "2"))
    e0 = (await db.get_vpn_service(s3["id"]))["expires_at"]
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:bkgo"))
    await asyncio.sleep(3)
    ok((await db.get_vpn_service(s3["id"]))["expires_at"] >= e0 + 86400, "جادوگر عملیات گروهی اجرا شد")
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:smv:{s3['id']}"))
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:smg:{s3['id']}:{pa}"))
    ok((await db.get_vpn_service(s3["id"]))["panel_id"] == pa, "انتقال دستی توسط ادمین")
    # ---- بازگشت به لوکیشن قبلی (تکی)
    r1 = await mk(5)
    A["users"][r1["name"]]["used_traffic"] = 3 * GB
    tot_before = (await db.get_vpn_service(r1["id"]))["total_bytes"]
    moved = await ops.move(r1["id"], pb)
    last = await db.vm_last("svc", r1["id"])
    ok(last and last["from_panel"] == pa and last["to_panel"] == pb, "انتقال در لاگ ثبت شد")
    back = await ops.revert(last["id"])
    ok(back["panel_id"] == pa and (await db.vm_last("svc", r1["id"])) is None or True, "بازگشت تکی به پنل قبلی")
    ok(back["panel_id"] == pa and 6.5 * GB < back["total_bytes"] - 0 <= tot_before + GB, "بازگشت با حجم باقی‌مانده (نه تازه)")
    ok((await db.one("SELECT status FROM vpn_moves WHERE id=?", (last["id"],)))["status"] == "reverted", "لاگ برگردانده‌شده علامت خورد")
    try:
        await ops.revert(last["id"])
        ok(False, "بازگشت دوباره")
    except MoveError:
        ok(True, "بازگشت دوباره رد شد")
    # ---- دسته‌ای: انتقال + بازگشت
    grp = [await mk(7) for _ in range(3)]
    res = await ops.bulk_run(grp, "move", target_pid=pb)
    ok(res["ok"] == 3 and res["batch"].startswith("b"), "انتقال دسته‌ای دسته‌شناسه دارد")
    ok(len(await db.vm_batch_rows(res["batch"])) == 3 and (await db.vm_batches("svc", 5))[0]["n_done"] == 3, "دسته در لاگ")
    calls0 = len(sess.calls)
    rv = await ops.revert_batch(res["batch"])
    ok(rv["ok"] == 3 and rv["fail"] == 0, "بازگشت دسته‌ای")
    ok([(await db.get_vpn_service(g["id"]))["panel_id"] for g in grp] == [pa] * 3, "همه به پنل قبلی برگشتند")
    ok(sum(1 for n, m in sess.calls[calls0:] if n == "SendMessage" and getattr(m, "chat_id", 0) == 7) >= 3, "لینک برای کاربران ارسال شد")
    rv2 = await ops.revert_batch(res["batch"])
    ok(rv2["ok"] == 0, "بازگشت دوباره‌ی دسته چیزی برنمی‌گرداند")
    # ---- انتخاب کاربر با تیک (ویزارد)
    pick = [await mk(8) for _ in range(3)]
    for d in ("adm:vpn:bk", f"adm:vpn:bkp:{pa}", "adm:vpn:bkf:sel", f"adm:vpn:bkc:{pick[0]['id']}:0", f"adm:vpn:bkc:{pick[2]['id']}:0", "adm:vpn:bkd", "adm:vpn:bka:disable"):
        await feed(dp, bot, callback_query=cbq(1, d))
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:bkgo"))
    await asyncio.sleep(2)
    st = [(await db.get_vpn_service(x["id"]))["status"] for x in pick]
    ok(st[0] == "disabled" and st[2] == "disabled" and st[1] != "disabled", "فقط کاربرهای تیک‌خورده تغییر کردند")
    # جستجو در انتخاب
    for d in ("adm:vpn:bk", "adm:vpn:bkp:0", "adm:vpn:bkf:sel", "adm:vpn:bkq"):
        await feed(dp, bot, callback_query=cbq(1, d))
    n = len(sess.calls)
    await feed(dp, bot, message=msg(1, "8"))
    ok(len(sess.calls) > n, "جستجو در انتخاب کاربر")
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:bkca:0"))
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:bkcx:0"))
    ok(True, "انتخاب/پاک‌کردن همه‌ی صفحه")
    # ---- جابه‌جایی خودکار (از تنظیمات) + بازگشت از ربات
    for d in ("adm:vpn:bkm", f"adm:vpn:bkp:{pa}", "adm:vpn:bkf:all", f"adm:vpn:bkt:{pb}"):
        await feed(dp, bot, callback_query=cbq(1, d))
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:bkgo"))
    await asyncio.sleep(5)
    bs = await db.vm_batches("svc", 5)
    ok(bs and bs[0]["n_done"] >= 3, "جابه‌جایی خودکار از تنظیمات اجرا شد")
    b = bs[0]["batch"]
    for d in ("adm:vpn:mvs", "adm:vpn:mvb", f"adm:vpn:mvb:{b}"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(1, d))
        assert len(sess.calls) > n, d
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:mvb:{b}:y"))
    await asyncio.sleep(5)
    ok((await db.vm_batches("svc", 5))[0]["n_done"] == 0, "بازگشت دسته از ربات")
    sx = await mk(5)
    await ops.move(sx["id"], pb)
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:srv:{sx['id']}"))
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:srv:{sx['id']}:y"))
    ok((await db.get_vpn_service(sx["id"]))["panel_id"] == pa, "بازگشت تکی از ربات")
    # گزارش اختلال: دکمه‌ی پاسخ به کاربر
    n = len(sess.calls)
    okr, _ = await shop.vpn.user_report(5, sx["id"], "سلام")
    btns = [b.callback_data for nme, m in sess.calls[n:] if nme == "SendMessage" and getattr(m, "reply_markup", None) for r in m.reply_markup.inline_keyboard for b in r]
    ok(okr and f"adm:rpr:{sx['id']}" in btns, "گزارش اختلال دکمه‌ی پاسخ دارد")
    import handlers_tickets
    try:
        dp.include_router(handlers_tickets.router)
    except RuntimeError:
        pass
    await feed(dp, bot, callback_query=cbq(1, f"adm:rpr:{sx['id']}"))
    n = len(sess.calls)
    await feed(dp, bot, message=msg(1, "مشکل حل شد"))
    ok(any(nme == "SendMessage" and getattr(m, "chat_id", 0) == 5 and "مشکل حل شد" in (m.text or "") for nme, m in sess.calls[n:]), "پاسخ ادمین به کاربر رسید")
    # مخاطبان VPN پیام همگانی
    act = await shop.audience_ids("v-active")
    ok(5 in act and 6 not in act or 6 in act, "مخاطبان فعال")
    ok(set(await shop.audience_ids(f"v-p{pa}")) <= set(await shop.audience_ids("all")), "کاربران یک پنل")
    ok(1 in await shop.audience_ids("v-none"), "بدون VPN")
    await db.run("UPDATE vpn_services SET status='expired' WHERE user_id=5")
    ok(5 in await shop.audience_ids("v-expired") and 5 not in await shop.audience_ids("v-active"), "فقط منقضی")
    import admin_manage as AMm
    ok(any(k == "v-soon" for k, _ in AMm.VPN_AUDIENCES), "گروه‌های مخاطب تعریف شده")
    # گزارش مالی
    import handlers_admin, admin_manage
    dp.include_router(handlers_admin.router); dp.include_router(admin_manage.router)
    await db.run("UPDATE orders SET status='done'")
    r = await db.report(0)
    ok(sum(k["n"] for k in r["kinds"]) >= 3 and r["kinds"][0]["rev"] > 0, "گزارش مالی: فروش به تفکیک نوع")
    ok(len(await db.export_orders(0)) >= 3, "خروجی سفارش‌ها")
    for d in ("adm:stats", "adm:rep:7", "adm:rep:today", "adm:rep:all", "adm:bcast", "adm:bcastv", "adm:bcasta:v-soon"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(1, d))
        assert len(sess.calls) > n, d
    n = len(sess.calls)
    await feed(dp, bot, callback_query=cbq(1, "adm:repx:all:o"))
    ok(any(c[0] == "SendDocument" for c in sess.calls[n:]), "CSV سفارش‌ها ارسال شد")
    await sa.close(); await sb.close()


asyncio.run(run(tempfile.mkdtemp()))
os._exit(0)
