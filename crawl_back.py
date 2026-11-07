"""خزش دکمه‌های ربات و گزارش «بازگشت»هایی که به صفحه‌ی والد برنمی‌گردند (گزارش دستی، assert ندارد).
اجرا: (timeout 280 python3 tests/crawl_back.py USER|ADMIN > /tmp/crawl.txt 2>&1; true)"""
import asyncio, json, os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from aiohttp.test_utils import TestServer
from mock_panels import marzban_app
from test_vpn import make
from test_all import new_user, cbq, feed
import admin_manage, admin_settings, admin_vpn, admin_vpn_agent, admin_vpn_abulk, handlers_admin, handlers_ai, handlers_tickets, handlers_user, handlers_vpn

MODE = sys.argv[1] if len(sys.argv) > 1 else "USER"
DENY = re.compile(r"(:dl2|:rfd2|:rv2|:tf2|:agw2|:rnp:|:adc:|:adp:|:tg:|:au:|:qk:|:agt:|:agq:|:agap|:agnp|:mvg|:agmg|:end$|:ok$|:human$|topup|:pay|:buy|:yes|:y$|:go$|:del|:rm|:ban|:wipe|:reset|:run|:send|adm:vpn:abkgo|adm:vpn:bkgo|:revoke)")
BACKW = re.compile(r"(🔙|بازگشت|منوی اصلی|انصراف|◀️ منو)")


def btns(sess):
    out = None
    for n, m in sess.calls:
        kb = getattr(m, "reply_markup", None)
        if n in ("SendMessage", "EditMessageText", "SendPhoto", "EditMessageCaption", "SendDocument") and kb and hasattr(kb, "inline_keyboard"):
            out = [(b.text, b.callback_data) for r in kb.inline_keyboard for b in r if b.callback_data]
    return out


async def run(tmp):
    A, B = marzban_app(), marzban_app()
    sa, sb = TestServer(A), TestServer(B)
    await sa.start_server(); await sb.start_server()
    db, bot, sess, shop, dp = await make(tmp)
    for r in (admin_vpn.router, admin_vpn_agent.router, handlers_vpn.router):   # make() اینها را گذاشته
        pass
    for r in (handlers_admin.router, admin_settings.router, admin_manage.router, admin_vpn_abulk.router, handlers_user.router, handlers_tickets.user_router, handlers_tickets.router, handlers_ai.router):
        r._parent_router = None
        dp.include_router(r)
    await shop.set_setting("vpn_enabled", "1"); await shop.set_setting("ai_enabled", "1"); await shop.set_setting("tickets_on", "1")
    await shop.set_setting("vpn_allow_move", "1"); await shop.set_setting("vpn_allow_addon", "1")
    for u in (1, 5): await new_user(db, u)
    await db.add_balance(5, 5_000_000, "t")
    pa = await db.add_vpn_panel(name="DE", type="marzban", url=str(sa.make_url("")).rstrip("/"), username="a", password="pw")
    pb = await db.add_vpn_panel(name="NL", type="marzban", url=str(sb.make_url("")).rstrip("/"), username="a", password="pw")
    await db.update_vpn_panel(pb, extra=json.dumps({"move_to": 1}))
    plan = await db.add_vpn_plan(title="30d", panel_id=pa, days=30, gb=10, price=100000, cost=60000)
    for _ in range(2):
        o = await shop.vpn.create_order(5, plan)
        await db.run("UPDATE orders SET status='delivering' WHERE id=?", (o,))
        await shop.vpn.fulfil(await db.get_order(o))
    await db.add_agent_plan(title="ag", panel_id=pa, days=30, traffic_gb=100, max_users=5, price=200000, cost=100000)
    await db.run("INSERT INTO vpn_agents(user_id,panel_id,plan_id,username,status,expires_at,total_bytes,used_bytes,users_count,max_users,created_at,updated_at,last_sync) VALUES(5,?,1,'agent_x','active',strftime('%s','now')+86400*9,107374182400,0,0,5,0,0,strftime('%s','now'))", (pa,))
    await db.tk_create(5, "t", "hello")
    uid, start = (5, "menu") if MODE == "USER" else (1, "adm:home")
    seen, queue, bad, total = {}, [(start, None)], [], 0
    while queue and total < (350 if MODE == "USER" else 700):
        cb, parent = queue.pop(0)
        if cb in seen:
            continue
        sess.calls.clear()
        try:
            await asyncio.wait_for(feed(dp, bot, callback_query=cbq(uid, cb)), 20)
        except Exception as e:  # noqa: BLE001
            print("ERR", cb, type(e).__name__); continue
        total += 1
        b = btns(sess)
        if False: print('FIRST', [(n, type(getattr(m,'reply_markup',None)).__name__, (getattr(m,'text','') or '')[:40]) for n, m in sess.calls], b and b[:5])
        seen[cb] = b or []
        if b is None:
            continue
        backs = [(t, c) for t, c in b if BACKW.search(t)]
        if parent and parent != start or parent == start and MODE == "ADMIN":
            tg = [c for _t, c in backs]
            if backs and parent not in tg and not (parent == "menu" and "menu" in tg):
                bad.append((parent, cb, backs))
        if parent and not backs and not cb.startswith("menu"):
            print("NOBACK", parent, "->", cb)
        for t, c in b:
            if not BACKW.search(t) and c not in seen and not DENY.search(c) and not c.startswith(("tk:r", "x:")):
                queue.append((c, cb))
    print(f"screens={total}")
    print("SEEN", " ".join(seen))
    for k, v in seen.items():
        print("BACKS", k, "=>", [c for t, c in v if BACKW.search(t)], "ALL:", [(t, c) for t, c in v][:14] if not [1 for t, c in v if BACKW.search(t)] else "")
    for parent, cb, backs in bad:
        print(f"MISMATCH  {parent}  ->  {cb}   back={[c for _t, c in backs]}")


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        await run(tmp)
    os._exit(0)

asyncio.run(main())
