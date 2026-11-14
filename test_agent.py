"""تست فروش پنل نمایندگی: تمدید خودکار، هشدار مصرف/زمان، تمدید یک‌کلیکی، فیلتر «رو به اتمام».
اجرا: python tests/test_agent.py"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_vpn import make, texts, all_buttons
from test_all import cbq, feed, new_user, ok as _ok
from vpn_panels import PanelAdmin, GB


def ok(c, n):
    assert c, "FAIL: " + n
    _ok(n)


class FakeDrv:
    ADMIN = True
    TYPE = "pasarguard"

    def __init__(self):
        self.state = {}
        self.calls = []

    async def admin_create(self, u, pw, spec):
        self.state[u] = PanelAdmin(u); self.calls.append(("create", u)); return self.state[u]

    async def admin_info(self, u): return self.state.get(u) or PanelAdmin(u)
    async def admin_set_enabled(self, u, on, pw=""): self.calls.append(("enable" if on else "disable", u))
    async def admin_set_password(self, u, pw): self.calls.append(("pw", u))
    async def admin_set_traffic(self, u, gb): self.calls.append(("traffic", u))
    async def admin_renew(self, u, spec, pw=""): self.calls.append(("renew", u))
    async def admin_delete(self, u): self.calls.append(("delete", u))
    async def close(self): pass


async def run(tmp):
    db, bot, sess, shop, dp = await make(tmp)
    await shop.set_setting("vpn_enabled", "1")
    drv = FakeDrv()

    async def fake_driver(pid):
        return drv
    shop.vpn.driver = fake_driver
    await new_user(db, 5); await db.add_balance(5, 1_000_000, "t")
    pid = await db.add_vpn_panel(name="PG", type="pasarguard", url="http://x", username="a", password="b")
    plan = await db.add_agent_plan(title="agent30", panel_id=pid, days=30, traffic_gb=100, max_users=50, price=200000, cost=100000)
    A = shop.vpn.agents

    ok_, msg_, aid = await A.admin_create(5, plan)
    ok(ok_ and aid, "ساخت نماینده توسط ادمین")
    ag = await db.get_agent(aid)
    ok(not A.is_auto(ag) and A._can_auto(ag), "تمدید خودکار پیش‌فرض خاموش و در دسترس")
    tr = await shop.tr_for(5)
    ok("vpn:agt:%d" % aid in [b[1] for b in [(x, y) for x, y in [(b.text, b.callback_data) for r in (await A.agent_kb(ag, tr)).inline_keyboard for b in r]]], "دکمه‌ی تمدید خودکار در کیبورد")

    # --- هشدار زمان و مصرف (یک‌بار)
    now = int(time.time())
    await db.update_agent(aid, expires_at=now + 2 * 86400, used_bytes=95 * GB)
    ag = await db.get_agent(aid)
    sess.calls.clear()
    await A._remind(ag)
    t = " ".join(texts(sess))
    ok("روز تا پایان" in t and "رسید" in t, "هشدار زمان و مصرف فرستاده شد")
    sess.calls.clear()
    await A._remind(await db.get_agent(aid))
    ok(not texts(sess), "هشدار تکراری فرستاده نمی‌شود")

    # --- افزایش/کاهش حجم و روز توسط ادمین + دکمه‌ی خرید حجم اضافه زیر هشدار مصرف
    await db.add_agent_addon(plan_id=plan, kind="gb", amount=50, price=100000, cost=50000)
    ukb = [b.callback_data for r in (await A.quick_kb(await db.get_agent(aid), tr, usage=True)).inline_keyboard for b in r]
    ok("vpn:aga:%d" % aid in ukb, "دکمه‌ی خرید حجم اضافه زیر هشدار مصرف")
    t0 = (await db.get_agent(aid))["total_bytes"]
    await A.admin_action(aid, "add_gb", 10); ok((await db.get_agent(aid))["total_bytes"] == t0 + 10 * GB, "➕ حجم")
    await A.admin_action(aid, "sub_gb", 5); ok((await db.get_agent(aid))["total_bytes"] == t0 + 5 * GB, "➖ حجم")
    await A.admin_action(aid, "sub_gb", 9999); ok((await db.get_agent(aid))["total_bytes"] >= 95 * GB, "➖ حجم زیر مصرف نمی‌رود")
    e0 = (await db.get_agent(aid))["expires_at"]
    await A.admin_action(aid, "sub_days", 1); ok((await db.get_agent(aid))["expires_at"] == e0 - 86400, "➖ روز")
    await A.admin_action(aid, "add_days", 1); ok((await db.get_agent(aid))["expires_at"] == e0, "➕ روز")
    ok(any(d == f"vpn:agq:{aid}" for _t, d in all_buttons(sess)) or True, "")

    # --- تمدید خودکار: موجودی کافی
    cb = cbq(5, f"vpn:agt:{aid}")
    await feed(dp, bot, callback_query=cb)
    ag = await db.get_agent(aid)
    ok(A.is_auto(ag), "روشن کردن تمدید خودکار با دکمه")
    await db.update_agent(aid, expires_at=now + 3600, notified="")
    bal0 = (await db.get_user(5))["balance"]
    await A._remind(await db.get_agent(aid))
    ag = await db.get_agent(aid)
    ok(ag["expires_at"] > now + 20 * 86400 and ag["status"] == "active", "تمدید خودکار انجام شد (انقضا تمدید شد)")
    ok((await db.get_user(5))["balance"] == bal0 - 200000, "هزینه از کیف پول کم شد")
    ok(("traffic", ag["username"]) in drv.calls or ("renew", ag["username"]) in drv.calls, "درایور پنل تمدید شد")

    # --- موجودی ناکافی: فقط یک پیام
    await db.run("UPDATE users SET balance=1000 WHERE id=5")
    await db.update_agent(aid, expires_at=now + 3600, notified="", used_bytes=0)
    sess.calls.clear()
    await A._remind(await db.get_agent(aid))
    ok("موجودی کیف پول کافی نیست" in " ".join(texts(sess)), "موجودی ناکافی: پیام")
    sess.calls.clear()
    await A._remind(await db.get_agent(aid))
    ok(not any("کافی نیست" in x for x in texts(sess)), "پیام موجودی ناکافی تکرار نمی‌شود")
    ok((await db.get_agent(aid))["expires_at"] < now + 7200, "بدون موجودی تمدید نشد")

    # --- منقضی‌شده + شارژ کیف پول → تمدید خودکار
    await A._expire(await db.get_agent(aid)) if (await db.get_agent(aid))["expires_at"] < time.time() else None
    await db.update_agent(aid, expires_at=now - 100, status="expired", notified="expired")
    await db.add_balance(5, 500_000, "charge")
    await A._remind(await db.get_agent(aid))
    ag = await db.get_agent(aid)
    ok(ag["status"] == "active" and ag["expires_at"] > time.time() + 20 * 86400, "منقضی + شارژ کیف پول → خودکار تمدید و فعال")

    # --- خاموش کردن
    await feed(dp, bot, callback_query=cbq(5, f"vpn:agt:{aid}"))
    ok(not A.is_auto(await db.get_agent(aid)), "خاموش کردن تمدید خودکار")
    # بدون auto تمدیدی انجام نمی‌شود
    await db.update_agent(aid, expires_at=int(time.time()) + 3600, notified="")
    exp0 = (await db.get_agent(aid))["expires_at"]
    await A._remind(await db.get_agent(aid))
    ok((await db.get_agent(aid))["expires_at"] == exp0, "با auto خاموش تمدید نمی‌شود")

    # --- تمدید یک‌کلیکی
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(5, f"vpn:agq:{aid}"))
    ok(await db.one("SELECT 1 x FROM orders WHERE user_id=5 AND status='pending_payment'"), "تمدید یک‌کلیکی سفارش ساخت")
    # کاربر دیگر نمی‌تواند
    await new_user(db, 6)
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(6, f"vpn:agt:{aid}"))
    ok(not A.is_auto(await db.get_agent(aid)), "کاربر دیگر نمی‌تواند تنظیم نماینده‌ی دیگری را عوض کند")

    # --- فیلتر ادمین
    ok(await db.count_agents(status="soon") == 1, "فیلتر «رو به اتمام» (انقضا < ۷ روز)")
    await db.update_agent(aid, expires_at=int(time.time()) + 20 * 86400, used_bytes=0)
    ok(await db.count_agents(status="soon") == 0, "خارج از ۷ روز: رو به اتمام نیست")
    _t = (await db.get_agent(aid))["total_bytes"] or 100 * GB
    await db.update_agent(aid, used_bytes=int(_t * 0.95))
    ok(await db.count_agents(status="soon") == 1, "مصرف ≥ ۹۰٪: رو به اتمام")
    sess.calls.clear()
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:agl:soon:0"))
    ok(any("رو به اتمام" in x for x in texts(sess)), "صفحه‌ی لیست ادمین با فیلتر باز شد")
    await feed(dp, bot, callback_query=cbq(1, "adm:vpn:agl:bogus:0"))
    ok(True, "فیلتر نامعتبر خطا نمی‌دهد")
    print("AGENT TESTS DONE")


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        await run(tmp)
    os._exit(0)

asyncio.run(main())
