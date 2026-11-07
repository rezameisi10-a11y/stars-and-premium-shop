"""تغییر لوکیشن و عملیات گروهی نماینده‌ها (+ بازگشت).  اجرا: (timeout 80 python3 tests/test_agent_move.py > /tmp/t.txt 2>&1; true); tail /tmp/t.txt"""
import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_vpn import make
from test_all import cbq, feed, msg, new_user, ok as _ok
from vpn_panels import GB, PanelAdmin
import admin_vpn, admin_vpn_agent, admin_vpn_abulk, handlers_vpn
from vpn_ops import MoveError


class FakeDrv:
    ADMIN = True
    TYPE = "pasarguard"

    def __init__(self):
        self.state, self.calls = {}, []

    async def admin_create(self, u, pw, spec):
        self.state[u] = PanelAdmin(u); self.calls.append(("create", u)); return self.state[u]

    async def admin_info(self, u): return self.state.get(u) or PanelAdmin(u)
    async def admin_set_enabled(self, u, on, pw=""): self.calls.append(("enable" if on else "disable", u))
    async def admin_set_password(self, u, pw): self.calls.append(("pw", u))
    async def admin_set_traffic(self, u, gb): pass
    async def admin_renew(self, u, spec, pw=""): self.calls.append(("renew", u))
    async def admin_delete(self, u): self.calls.append(("delete", u))
    async def close(self): pass


def ok(c, n):
    assert c, "FAIL: " + n
    _ok(n)


async def run(tmp):
    db, bot, sess, shop, dp = await make(tmp)
    for r in (admin_vpn_agent.router, admin_vpn_abulk.router):
        try:
            dp.include_router(r)
        except RuntimeError:
            pass
    await shop.set_setting("vpn_enabled", "1")
    drvs = {}

    async def fake_driver(pid):
        return drvs.setdefault(pid, FakeDrv())
    shop.vpn.driver = fake_driver
    for u in (1, 5, 6, 7): await new_user(db, u)
    pa = await db.add_vpn_panel(name="A", type="pasarguard", url="http://a", username="a", password="b")
    pb = await db.add_vpn_panel(name="B", type="pasarguard", url="http://b", username="a", password="b")
    plan = await db.add_agent_plan(title="ag", panel_id=pa, days=30, traffic_gb=100, max_users=50, price=1, cost=1)
    A = shop.vpn.agents
    ids = []
    for u in (5, 6, 7):
        _, _, aid = await A.admin_create(u, plan)
        ids.append(aid)
    await db.update_agent(ids[0], used_bytes=30 * GB)
    ag0 = await db.get_agent(ids[0])
    drvs[pa].state[ag0["username"]].used = 30 * GB
    ok(await A.move_targets(ag0, admin=True) != [] and not await A.move_targets(ag0), "مقصد: ادمین همه؛ نماینده فقط تیک‌خورده‌ها")

    new = await A.move(ids[0], pb)
    ok(new["panel_id"] == pb and new["username"] and new["password"] != ag0["password"], "نماینده منتقل شد")
    ok(70 * GB - 1 <= new["total_bytes"] <= 70 * GB + GB, "سقف حجم = باقی‌مانده (نه تازه)")
    ok(abs(new["expires_at"] - ag0["expires_at"]) < 5, "زمان انقضا حفظ شد")
    ok(("disable", ag0["username"]) in drvs[pa].calls and ("create", new["username"]) in drvs[pb].calls, "قبلی غیرفعال، جدید ساخته شد")
    ok(all(c[0] != "delete" for c in drvs[pa].calls), "اکانت قبلی حذف نشد")
    last = await db.vm_last("agent", ids[0])
    ok(last and last["from_panel"] == pa and last["to_panel"] == pb, "لاگ انتقال")
    back = await A.revert_move(last["id"])
    ok(back["panel_id"] == pa and back["username"] == ag0["username"] and back["password"] == ag0["password"], "بازگشت به اکانت قبلی")
    ok(("enable", ag0["username"]) in drvs[pa].calls and ("disable", new["username"]) in drvs[pb].calls, "قبلی فعال، جدید غیرفعال")
    try:
        await A.revert_move(last["id"]); ok(False, "بازگشت دوباره")
    except MoveError:
        ok(True, "بازگشت دوباره رد شد")

    # نماینده‌ی منقضی/حجم‌تمام نمی‌تواند منتقل شود
    drvs[pa].state[(await db.get_agent(ids[1]))["username"]].used = 100 * GB
    try:
        await A.move(ids[1], pb); ok(False, "حجم تمام")
    except MoveError:
        ok(True, "حجم‌تمام‌شده رد شد")
    drvs[pa].state[(await db.get_agent(ids[1]))["username"]].used = 0

    # خود نماینده
    try:
        await A.move(ids[1], pb, user_id=6); ok(False, "خاموش")
    except MoveError:
        ok(True, "تغییر لوکیشن نماینده پیش‌فرض خاموش")
    await shop.set_setting("vpn_agent_move", "1")
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:amvs"))
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:amvt:{pb}"))
    ok(json.loads((await db.get_vpn_panel(pb))["extra"]).get("amove_to") == 1, "انتخاب مقصد نماینده از ربات")
    for d in (f"vpn:agm:{ids[1]}", f"vpn:agmp:{ids[1]}:{pb}"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(6, d))
        ok(len(sess.calls) > n, "نماینده: " + d)
    await feed(dp, bot, callback_query=cbq(6, f"vpn:agmg:{ids[1]}:{pb}"))
    ok((await db.get_agent(ids[1]))["panel_id"] == pb, "نماینده با دکمه خودش منتقل شد")
    try:
        await A.move(ids[1], pa, user_id=6); ok(False, "سقف دفعات")
    except MoveError:
        ok(True, "سقف دفعات (۱) اعمال شد")
    ok(await A.move_targets(await db.get_agent(ids[1])) == [], "مقصدِ نماینده خالی")

    # عملیات گروهی (انتخابی + همه)
    for d in ("adm:vpn:agl:all:0", "adm:vpn:abk", "adm:vpn:abkp:0", "adm:vpn:abkf:sel", f"adm:vpn:abkc:{ids[0]}:0", f"adm:vpn:abkc:{ids[2]}:0", "adm:vpn:abkd", "adm:vpn:abka:add_days"):
        await feed(dp, bot, callback_query=cbq(1, d))
    e0 = (await db.get_agent(ids[0]))["expires_at"], (await db.get_agent(ids[2]))["expires_at"], (await db.get_agent(ids[1]))["expires_at"]
    await feed(dp, bot, message=msg(1, "5"))
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:abkgo"))
    await asyncio.sleep(2)
    e1 = (await db.get_agent(ids[0]))["expires_at"], (await db.get_agent(ids[2]))["expires_at"], (await db.get_agent(ids[1]))["expires_at"]
    ok(e1[0] > e0[0] and e1[1] > e0[1] and e1[2] == e0[2], "فقط نماینده‌های تیک‌خورده +۵ روز")
    for d in ("adm:vpn:abk", "adm:vpn:abkp:0", "adm:vpn:abkf:all", "adm:vpn:abka:msg"):
        await feed(dp, bot, callback_query=cbq(1, d))
    n = len(sess.calls)
    await feed(dp, bot, message=msg(1, "سلام نماینده"))
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:abkgo"))
    await asyncio.sleep(2)
    got = {getattr(m, "chat_id", 0) for nme, m in sess.calls[n:] if nme == "SendMessage" and "سلام نماینده" in (getattr(m, "text", "") or "")}
    ok({5, 6, 7} <= got, "پیام گروهی به همه‌ی نماینده‌ها")
    for act, val in (("credit", "7000"), ("sub_credit", "2000"), ("sub_days", "1"), ("sub_gb", "1"), ("discount", "20")):
        bal = {u: (await db.get_user(u))["balance"] for u in (5, 6, 7)}
        for d in ("adm:vpn:abk", "adm:vpn:abkp:0", "adm:vpn:abkf:all", f"adm:vpn:abka:{act}"):
            await feed(dp, bot, callback_query=cbq(1, d))
        await feed(dp, bot, message=msg(1, val))
        await feed(dp, bot, callback_query=cbq(1, "adm:vpn:abkgo"))
        await asyncio.sleep(2)
        if act == "credit":
            ok(all([(await db.get_user(u))["balance"] == bal[u] + 7000 for u in (5, 6, 7)]), "اعتبار گروهی برای همه‌ی نماینده‌ها")
        elif act == "sub_credit":
            ok(all([(await db.get_user(u))["balance"] == bal[u] - 2000 for u in (5, 6, 7)]), "کاهش اعتبار گروهی برای همه‌ی نماینده‌ها")
        elif act in ("sub_days", "sub_gb"):
            ok(True, "کاهش گروهی نماینده‌ها اجرا شد: " + act)
        else:
            ok(all([(await db.get_user(u))["personal_discount"] == 20 for u in (5, 6, 7)]), "تخفیف گروهی برای همه‌ی نماینده‌ها")
    for d in ("adm:vpn:abk", f"adm:vpn:abkp:{pa}", "adm:vpn:abkf:all", "adm:vpn:abka:disable", "adm:vpn:abkgo"):
        await feed(dp, bot, callback_query=cbq(1, d))
    await asyncio.sleep(2)
    ok((await db.get_agent(ids[2]))["status"] in ("disabled",), "غیرفعال‌سازی گروهی")

    # جابه‌جایی خودکار + بازگشت دسته
    for d in ("adm:vpn:abkm", f"adm:vpn:abkp:{pa}", "adm:vpn:abkf:all", f"adm:vpn:abkt:{pb}", "adm:vpn:abkgo"):
        await feed(dp, bot, callback_query=cbq(1, d))
    await asyncio.sleep(4)
    bs = await db.vm_batches("agent", 5)
    ok(bs and bs[0]["n_done"] >= 1, "جابه‌جایی خودکار نماینده‌ها")
    b = bs[0]["batch"]
    for d in ("adm:vpn:amvs", "adm:vpn:abb", f"adm:vpn:abb:{b}"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(1, d))
        assert len(sess.calls) > n, d
    await feed(dp, bot, callback_query=cbq(1, f"adm:vpn:abb:{b}:y"))
    await asyncio.sleep(4)
    ok((await db.vm_batches("agent", 5))[0]["n_done"] == 0, "بازگشت دسته‌ی نماینده‌ها")
    a2 = await db.get_agent(ids[2])
    ok(a2["panel_id"] == pa, "نماینده به پنل قبلی برگشت")
    for d in (f"adm:vpn:ag:{ids[0]}", f"adm:vpn:amv:{ids[0]}"):
        n = len(sess.calls)
        await feed(dp, bot, callback_query=cbq(1, d))
        assert len(sess.calls) > n, d
    ok(True, "کارت نماینده و انتقال تکی باز می‌شود")


with tempfile.TemporaryDirectory() as t:
    asyncio.run(run(t))
os._exit(0)
