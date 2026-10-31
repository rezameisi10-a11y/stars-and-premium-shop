"""دیتابیس بخش فروش VPN: پنل‌ها، پلن‌ها، بسته‌های افزودنی (حجم/زمان) و سرویس‌های کاربران. (mixin کلاس DB)"""
import json
import time

VPN_SCHEMA = """
CREATE TABLE IF NOT EXISTS vpn_panels(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, type TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '', password TEXT NOT NULL DEFAULT '', token TEXT NOT NULL DEFAULT '',
  sub_url TEXT NOT NULL DEFAULT '', inbound_ids TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '{}',
  capacity INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100,
  note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS vpn_plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, panel_id INTEGER NOT NULL,
  days INTEGER NOT NULL DEFAULT 30, gb REAL NOT NULL DEFAULT 0, price INTEGER NOT NULL DEFAULT 0, cost INTEGER NOT NULL DEFAULT 0,
  ip_limit INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS vpn_addons(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, gb REAL NOT NULL DEFAULT 0, days INTEGER NOT NULL DEFAULT 0,
  price INTEGER NOT NULL DEFAULT 0, cost INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100);
CREATE TABLE IF NOT EXISTS vpn_agent_addons(
  id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'gb', amount REAL NOT NULL DEFAULT 0,
  price INTEGER NOT NULL DEFAULT 0, cost INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100);
CREATE TABLE IF NOT EXISTS vpn_services(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, panel_id INTEGER NOT NULL, plan_id INTEGER,
  name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', expires_at INTEGER NOT NULL DEFAULT 0,
  total_bytes INTEGER NOT NULL DEFAULT 0, used_bytes INTEGER NOT NULL DEFAULT 0, sub_url TEXT NOT NULL DEFAULT '',
  links TEXT NOT NULL DEFAULT '[]', meta TEXT NOT NULL DEFAULT '{}', order_id INTEGER, is_test INTEGER NOT NULL DEFAULT 0,
  title TEXT NOT NULL DEFAULT '', notified TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, last_sync INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vpn_agent_plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, panel_id INTEGER NOT NULL,
  days INTEGER NOT NULL DEFAULT 30, traffic_gb REAL NOT NULL DEFAULT 0, max_users INTEGER NOT NULL DEFAULT 0,
  role_id TEXT NOT NULL DEFAULT '', service_ids TEXT NOT NULL DEFAULT '', modify_users INTEGER NOT NULL DEFAULT 1,
  data_min_gb REAL NOT NULL DEFAULT 0, data_max_gb REAL NOT NULL DEFAULT 0, expire_min_days INTEGER NOT NULL DEFAULT 0, expire_max_days INTEGER NOT NULL DEFAULT 0,
  price INTEGER NOT NULL DEFAULT 0, cost INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 100,
  note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS vpn_agents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, panel_id INTEGER NOT NULL, plan_id INTEGER,
  username TEXT NOT NULL, password TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  expires_at INTEGER NOT NULL DEFAULT 0, total_bytes INTEGER NOT NULL DEFAULT 0, used_bytes INTEGER NOT NULL DEFAULT 0,
  users_count INTEGER NOT NULL DEFAULT 0, max_users INTEGER NOT NULL DEFAULT 0, order_id INTEGER, title TEXT NOT NULL DEFAULT '',
  notified TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  last_sync INTEGER NOT NULL DEFAULT 0, meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS vpn_stock(
  id INTEGER PRIMARY KEY AUTOINCREMENT, panel_id INTEGER NOT NULL, plan_id INTEGER NOT NULL DEFAULT 0,
  name TEXT NOT NULL DEFAULT '', config TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, service_id INTEGER,
  created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_vpnst_free ON vpn_stock(panel_id, plan_id, used);
CREATE INDEX IF NOT EXISTS ix_vpna_user ON vpn_agents(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_vpna_order ON vpn_agents(order_id) WHERE order_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_vpna_name ON vpn_agents(panel_id, username) WHERE status != 'deleted';
CREATE INDEX IF NOT EXISTS ix_vpns_user ON vpn_services(user_id);
CREATE INDEX IF NOT EXISTS ix_vpns_status ON vpn_services(status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_vpns_order ON vpn_services(order_id) WHERE order_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_vpns_name ON vpn_services(panel_id, name) WHERE status != 'deleted';
"""

PANEL_FIELDS = {"name", "type", "url", "username", "password", "token", "sub_url", "inbound_ids", "extra", "capacity", "enabled", "sort", "note"}
PLAN_FIELDS = {"title", "panel_id", "days", "gb", "price", "cost", "ip_limit", "active", "sort", "note", "utype"}
ADDON_FIELDS = {"title", "gb", "days", "price", "cost", "active", "sort"}
SERVICE_FIELDS = {"panel_id", "plan_id", "name", "status", "expires_at", "total_bytes", "used_bytes", "sub_url", "links", "meta", "title",
                  "notified", "note", "last_sync", "user_id", "is_test"}
AGENT_PLAN_FIELDS = {"title", "panel_id", "days", "traffic_gb", "max_users", "role_id", "service_ids", "modify_users", "data_min_gb", "data_max_gb",
                     "expire_min_days", "expire_max_days", "price", "cost", "active", "sort", "note", "addon_on",
                     "custom_gb_on", "custom_gb_price", "custom_gb_min", "custom_gb_max", "custom_days_on", "custom_days_price", "custom_days_min", "custom_days_max"}
AGENT_ADDON_FIELDS = {"plan_id", "kind", "amount", "price", "cost", "active", "sort"}
AGENT_FIELDS = {"user_id", "panel_id", "plan_id", "username", "password", "status", "expires_at", "total_bytes", "used_bytes", "users_count",
                "max_users", "title", "notified", "note", "last_sync", "meta"}
LIVE = ("active", "disabled", "limited", "expired", "on_hold", "pending_manual")


class VpnDB:
    # ---------- موجودی کانفیگ برای پنل دستی (plan_id: >0 همان پلن، 0 = هر پلن این پنل، -1 = اکانت تست)
    async def add_vpn_stock(self, panel_id: int, plan_id: int, items: list) -> int:
        now = int(time.time())
        n = 0
        async with self.lock:
            for name, config in items:
                await self.conn.execute("INSERT INTO vpn_stock(panel_id,plan_id,name,config,created_at) VALUES(?,?,?,?,?)",
                                        (panel_id, plan_id, (name or "")[:80], config, now))
                n += 1
            await self.conn.commit()
        return n

    async def vpn_stock_free(self, panel_id: int, plan_id: int) -> int:
        """تعداد موجودی قابل فروش برای یک پلن (اختصاصی + عمومی پنل)؛ برای تست (-1) فقط موجودی تست."""
        if plan_id < 0:
            r = await self.one("SELECT COUNT(*) c FROM vpn_stock WHERE panel_id=? AND plan_id=-1 AND used=0", (panel_id,))
        else:
            r = await self.one("SELECT COUNT(*) c FROM vpn_stock WHERE panel_id=? AND plan_id IN (?,0) AND used=0", (panel_id, plan_id))
        return r["c"]

    async def vpn_stock_mode(self, panel_id: int) -> bool:
        """پنل دستی‌ای که تا حالا موجودی ثبت شده دارد، فروشش با موجودی است (تمام‌شدن = ناموجود)."""
        return bool(await self.one("SELECT 1 x FROM vpn_stock WHERE panel_id=? LIMIT 1", (panel_id,)))

    async def vpn_stock_summary(self, panel_id: int) -> list:
        return await self.all(
            "SELECT plan_id, SUM(used=0) AS free, SUM(used=1) AS sold FROM vpn_stock WHERE panel_id=? GROUP BY plan_id ORDER BY plan_id DESC", (panel_id,))

    async def list_vpn_stock(self, panel_id: int, plan_id: int, free_only=True, limit=10, offset=0) -> list:
        w = " AND used=0" if free_only else ""
        return await self.all(f"SELECT * FROM vpn_stock WHERE panel_id=? AND plan_id=?{w} ORDER BY id LIMIT ? OFFSET ?", (panel_id, plan_id, limit, offset))

    async def count_vpn_stock(self, panel_id: int, plan_id: int, free_only=True) -> int:
        w = " AND used=0" if free_only else ""
        return (await self.one(f"SELECT COUNT(*) c FROM vpn_stock WHERE panel_id=? AND plan_id=?{w}", (panel_id, plan_id)))["c"]

    async def get_vpn_stock(self, sid: int):
        return await self.one("SELECT * FROM vpn_stock WHERE id=?", (sid,))

    async def update_vpn_stock(self, sid: int, name: str, config: str) -> bool:
        cur = await self.run("UPDATE vpn_stock SET name=?, config=? WHERE id=? AND used=0", ((name or "")[:80], config, sid))
        return cur.rowcount == 1

    async def delete_vpn_stock(self, sid: int) -> bool:
        cur = await self.run("DELETE FROM vpn_stock WHERE id=? AND used=0", (sid,))
        return cur.rowcount == 1

    async def clear_vpn_stock(self, panel_id: int, plan_id: int) -> int:
        cur = await self.run("DELETE FROM vpn_stock WHERE panel_id=? AND plan_id=? AND used=0", (panel_id, plan_id))
        return cur.rowcount

    async def claim_vpn_stock(self, panel_id: int, plan_id: int):
        """یک کانفیگ آزاد را اتمیک برمی‌دارد (اول موجودی اختصاصی همان پلن، بعد عمومی). خروجی ردیف یا None."""
        async with self.lock:
            if plan_id < 0:
                cur = await self.conn.execute("SELECT id FROM vpn_stock WHERE panel_id=? AND plan_id=-1 AND used=0 ORDER BY id LIMIT 1", (panel_id,))
            else:
                cur = await self.conn.execute(
                    "SELECT id FROM vpn_stock WHERE panel_id=? AND plan_id IN (?,0) AND used=0 ORDER BY (plan_id=?) DESC, id LIMIT 1",
                    (panel_id, plan_id, plan_id))
            r = await cur.fetchone()
            if not r:
                return None
            cur = await self.conn.execute("UPDATE vpn_stock SET used=1 WHERE id=? AND used=0", (r[0],))
            await self.conn.commit()
            if cur.rowcount != 1:
                return None
        return await self.get_vpn_stock(r[0])

    async def release_vpn_stock(self, sid: int, service_id: int | None = None):
        await self.run("UPDATE vpn_stock SET used=?, service_id=? WHERE id=?", (1 if service_id else 0, service_id, sid))

    # ---------- پنل‌ها
    async def add_vpn_panel(self, **f) -> int:
        f = {k: v for k, v in f.items() if k in PANEL_FIELDS}
        f.setdefault("extra", "{}")
        cur = await self.run(f"INSERT INTO vpn_panels({','.join(f)},created_at) VALUES({','.join('?' * len(f))},?)", (*f.values(), int(time.time())))
        return cur.lastrowid

    async def get_vpn_panel(self, pid):
        return await self.one("SELECT * FROM vpn_panels WHERE id=?", (pid,))

    async def list_vpn_panels(self, only_enabled=False):
        w = " WHERE enabled=1" if only_enabled else ""
        return await self.all(f"SELECT p.*, (SELECT COUNT(*) FROM vpn_services s WHERE s.panel_id=p.id AND s.status!='deleted') AS n_services "
                              f"FROM vpn_panels p{w} ORDER BY sort, id")

    async def update_vpn_panel(self, pid, **f):
        f = {k: v for k, v in f.items() if k in PANEL_FIELDS}
        if f:
            await self.run(f"UPDATE vpn_panels SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), pid))

    async def delete_vpn_panel(self, pid) -> bool:
        """پنلِ دارای سرویس حذف نمی‌شود (ادمین می‌تواند غیرفعالش کند)."""
        if (await self.one("SELECT COUNT(*) c FROM vpn_services WHERE panel_id=? AND status!='deleted'", (pid,)))["c"]:
            return False
        if (await self.one("SELECT COUNT(*) c FROM vpn_agents WHERE panel_id=? AND status!='deleted'", (pid,)))["c"]:
            return False
        await self.run("DELETE FROM vpn_plans WHERE panel_id=?", (pid,))
        await self.run("DELETE FROM vpn_agent_plans WHERE panel_id=?", (pid,))
        await self.run("DELETE FROM vpn_panels WHERE id=?", (pid,))
        return True

    async def panel_load(self, pid) -> int:
        return (await self.one("SELECT COUNT(*) c FROM vpn_services WHERE panel_id=? AND status NOT IN ('deleted','expired')", (pid,)))["c"]

    # ---------- پلن‌ها
    async def add_vpn_plan(self, **f) -> int:
        f = {k: v for k, v in f.items() if k in PLAN_FIELDS}
        cur = await self.run(f"INSERT INTO vpn_plans({','.join(f)}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return cur.lastrowid

    async def get_vpn_plan(self, pid):
        return await self.one("SELECT * FROM vpn_plans WHERE id=?", (pid,))

    async def list_vpn_plans(self, active_only=False, panel_id=None):
        w, a = [], []
        if active_only:
            w.append("p.active=1 AND n.enabled=1")
        if panel_id:
            w.append("p.panel_id=?")
            a.append(panel_id)
        return await self.all("SELECT p.*, n.name AS panel_name, n.type AS panel_type FROM vpn_plans p JOIN vpn_panels n ON n.id=p.panel_id"
                              + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY p.sort, p.price, p.id", tuple(a))

    async def update_vpn_plan(self, pid, **f):
        f = {k: v for k, v in f.items() if k in PLAN_FIELDS}
        if f:
            await self.run(f"UPDATE vpn_plans SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), pid))

    async def delete_vpn_plan(self, pid):
        await self.run("DELETE FROM vpn_plans WHERE id=?", (pid,))

    # ---------- بسته‌های افزودنی
    async def add_vpn_addon(self, **f) -> int:
        f = {k: v for k, v in f.items() if k in ADDON_FIELDS}
        cur = await self.run(f"INSERT INTO vpn_addons({','.join(f)}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return cur.lastrowid

    async def get_vpn_addon(self, aid):
        return await self.one("SELECT * FROM vpn_addons WHERE id=?", (aid,))

    async def list_vpn_addons(self, active_only=False):
        return await self.all("SELECT * FROM vpn_addons" + (" WHERE active=1" if active_only else "") + " ORDER BY sort, price, id")

    async def update_vpn_addon(self, aid, **f):
        f = {k: v for k, v in f.items() if k in ADDON_FIELDS}
        if f:
            await self.run(f"UPDATE vpn_addons SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), aid))

    async def delete_vpn_addon(self, aid):
        await self.run("DELETE FROM vpn_addons WHERE id=?", (aid,))

    # ---------- سرویس‌ها
    async def add_vpn_service(self, **f) -> int:
        now = int(time.time())
        f = {k: v for k, v in f.items() if k in SERVICE_FIELDS | {"order_id"}}
        for k in ("links", "meta"):
            if k in f and not isinstance(f[k], str):
                f[k] = json.dumps(f[k], ensure_ascii=False)
        f["created_at"] = f["updated_at"] = now
        cur = await self.run(f"INSERT INTO vpn_services({','.join(f)}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return cur.lastrowid

    async def get_vpn_service(self, sid):
        return await self.one("SELECT s.*, p.name AS panel_name, p.type AS panel_type, p.extra AS panel_extra FROM vpn_services s LEFT JOIN vpn_panels p ON p.id=s.panel_id WHERE s.id=?", (sid,))

    async def vpn_service_by_order(self, oid):
        return await self.one("SELECT s.*, p.name AS panel_name, p.type AS panel_type, p.extra AS panel_extra FROM vpn_services s LEFT JOIN vpn_panels p ON p.id=s.panel_id WHERE s.order_id=?", (oid,))

    async def update_vpn_service(self, sid, **f):
        f = {k: v for k, v in f.items() if k in SERVICE_FIELDS}
        for k in ("links", "meta"):
            if k in f and not isinstance(f[k], str):
                f[k] = json.dumps(f[k], ensure_ascii=False)
        if f:
            f["updated_at"] = int(time.time())
            await self.run(f"UPDATE vpn_services SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), sid))

    def _svc_where(self, user_id=None, status=None, q="", panel_id=None, include_deleted=False):
        w, a = [], []
        if user_id is not None:
            w.append("s.user_id=?")
            a.append(user_id)
        if status == "live":
            w.append("s.status IN ('active','disabled','limited','expired','on_hold','pending_manual')")
        elif status:
            w.append("s.status=?")
            a.append(status)
        elif not include_deleted:
            w.append("s.status!='deleted'")
        if panel_id:
            w.append("s.panel_id=?")
            a.append(panel_id)
        if q:
            qq = f"%{q.strip().lstrip('@')}%"
            w.append("(s.name LIKE ? OR CAST(s.user_id AS TEXT) LIKE ? OR CAST(s.id AS TEXT)=? OR EXISTS(SELECT 1 FROM users u WHERE u.id=s.user_id AND u.username LIKE ?))")
            a += [qq, qq, q.strip(), qq]
        return (" WHERE " + " AND ".join(w)) if w else "", tuple(a)

    async def list_vpn_services(self, user_id=None, status=None, q="", panel_id=None, limit=50, offset=0):
        w, a = self._svc_where(user_id, status, q, panel_id)
        return await self.all("SELECT s.*, p.name AS panel_name, p.type AS panel_type, p.extra AS panel_extra FROM vpn_services s LEFT JOIN vpn_panels p ON p.id=s.panel_id"
                              f"{w} ORDER BY s.id DESC LIMIT ? OFFSET ?", (*a, limit, offset))

    async def count_vpn_services(self, user_id=None, status=None, q="", panel_id=None):
        w, a = self._svc_where(user_id, status, q, panel_id)
        return (await self.one(f"SELECT COUNT(*) c FROM vpn_services s{w}", a))["c"]

    async def set_vpn_level(self, uid, level):
        if level in ("f", "n", "n2"):
            await self.run("UPDATE users SET vpn_level=? WHERE id=?", (level, uid))

    async def vpn_users(self, limit=10, offset=0):
        return await self.all("SELECT u.id, u.username, u.first_name, COUNT(s.id) AS n FROM users u JOIN vpn_services s ON s.user_id=u.id AND s.status!='deleted' "
                              "GROUP BY u.id ORDER BY MAX(s.id) DESC LIMIT ? OFFSET ?", (limit, offset))

    async def count_vpn_users(self):
        return (await self.one("SELECT COUNT(DISTINCT user_id) c FROM vpn_services WHERE status!='deleted'"))["c"]

    async def vpn_has_test(self, user_id) -> bool:
        return bool(await self.one("SELECT 1 x FROM vpn_services WHERE user_id=? AND is_test=1", (user_id,)))

    async def vpn_syncable(self, older_than: int, limit=200):
        """سرویس‌های زنده‌ای که مدتی است با پنل همگام نشده‌اند."""
        return await self.all("SELECT * FROM vpn_services WHERE status IN ('active','disabled','limited','on_hold','expired') AND last_sync<? "
                              "ORDER BY last_sync LIMIT ?", (older_than, limit))

    async def vpn_stats(self) -> dict:
        r = await self.one("SELECT COUNT(*) total, COALESCE(SUM(status='active'),0) active, COALESCE(SUM(status='expired'),0) expired, "
                           "COALESCE(SUM(is_test),0) tests FROM vpn_services WHERE status!='deleted'")
        rev = await self.one("SELECT COUNT(*) n, COALESCE(SUM(amount),0) revenue, COALESCE(SUM(amount-cost),0) profit FROM orders WHERE kind='vpn' AND status='done'")
        return {**dict(r), "orders": rev["n"], "revenue": rev["revenue"], "profit": rev["profit"]}

    async def create_vpn_order(self, user_id, title, days, ref, amount, cost=0) -> int:
        now = int(time.time())
        cur = await self.run(
            "INSERT INTO orders(user_id,product_id,kind,title,qty,ref,target_id,target_username,amount,orig_amount,cost,created_at,updated_at) "
            "VALUES(?,NULL,'vpn',?,?,?,?,NULL,?,?,?,?,?)", (user_id, title, days, ref, user_id, amount, amount, cost, now, now))
        return cur.lastrowid

    # ---------- پلن‌های نمایندگی
    async def add_agent_plan(self, **f) -> int:
        f = {k: v for k, v in f.items() if k in AGENT_PLAN_FIELDS}
        cur = await self.run(f"INSERT INTO vpn_agent_plans({','.join(f)}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return cur.lastrowid

    async def get_agent_plan(self, pid):
        return await self.one("SELECT * FROM vpn_agent_plans WHERE id=?", (pid,))

    async def list_agent_plans(self, active_only=False, panel_id=None):
        w, a = [], []
        if active_only:
            w.append("p.active=1 AND n.enabled=1")
        if panel_id:
            w.append("p.panel_id=?")
            a.append(panel_id)
        return await self.all("SELECT p.*, n.name AS panel_name, n.type AS panel_type FROM vpn_agent_plans p JOIN vpn_panels n ON n.id=p.panel_id"
                              + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY p.sort, p.price, p.id", tuple(a))

    async def update_agent_plan(self, pid, **f):
        f = {k: v for k, v in f.items() if k in AGENT_PLAN_FIELDS}
        if f:
            await self.run(f"UPDATE vpn_agent_plans SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), pid))

    async def delete_agent_plan(self, pid):
        await self.run("DELETE FROM vpn_agent_addons WHERE plan_id=?", (pid,))
        await self.run("DELETE FROM vpn_agent_plans WHERE id=?", (pid,))

    # ---------- بسته‌های افزودنی پلن نمایندگی (حجم / زمان)
    async def add_agent_addon(self, **f) -> int:
        f = {k: v for k, v in f.items() if k in AGENT_ADDON_FIELDS}
        cur = await self.run(f"INSERT INTO vpn_agent_addons({','.join(f)}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return cur.lastrowid

    async def get_agent_addon(self, aid):
        return await self.one("SELECT * FROM vpn_agent_addons WHERE id=?", (aid,))

    async def list_agent_addons(self, plan_id: int, active_only=False):
        return await self.all("SELECT * FROM vpn_agent_addons WHERE plan_id=?" + (" AND active=1" if active_only else "") + " ORDER BY kind, amount, id", (plan_id,))

    async def update_agent_addon(self, aid, **f):
        f = {k: v for k, v in f.items() if k in AGENT_ADDON_FIELDS}
        if f:
            await self.run(f"UPDATE vpn_agent_addons SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), aid))

    async def delete_agent_addon(self, aid):
        await self.run("DELETE FROM vpn_agent_addons WHERE id=?", (aid,))

    # ---------- نمایندگان (ادمین‌های ساخته‌شده در پنل)
    async def add_agent(self, **f) -> int:
        now = int(time.time())
        f = {k: v for k, v in f.items() if k in AGENT_FIELDS | {"order_id"}}
        if "meta" in f and not isinstance(f["meta"], str):
            f["meta"] = json.dumps(f["meta"], ensure_ascii=False)
        f["created_at"] = f["updated_at"] = now
        cur = await self.run(f"INSERT INTO vpn_agents({','.join(f)}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return cur.lastrowid

    async def get_agent(self, aid):
        return await self.one("SELECT a.*, p.name AS panel_name, p.type AS panel_type, p.url AS panel_url, p.extra AS panel_extra FROM vpn_agents a LEFT JOIN vpn_panels p ON p.id=a.panel_id WHERE a.id=?", (aid,))

    async def agent_by_order(self, oid):
        return await self.one("SELECT * FROM vpn_agents WHERE order_id=?", (oid,))

    async def update_agent(self, aid, **f):
        f = {k: v for k, v in f.items() if k in AGENT_FIELDS}
        if "meta" in f and not isinstance(f["meta"], str):
            f["meta"] = json.dumps(f["meta"], ensure_ascii=False)
        if f:
            f["updated_at"] = int(time.time())
            await self.run(f"UPDATE vpn_agents SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), aid))

    def _agent_where(self, user_id=None, status=None, q="", panel_id=None):
        w, a = [], []
        if user_id is not None:
            w.append("a.user_id=?")
            a.append(user_id)
        if status == "soon":                                   # رو به اتمام: انقضا تا ۷ روز دیگر یا مصرف ≥ ۹۰٪
            w.append("a.status IN ('active','limited') AND ((a.expires_at>0 AND a.expires_at<?) OR (a.total_bytes>0 AND a.used_bytes*100>=a.total_bytes*90))")
            a.append(int(time.time()) + 7 * 86400)
        elif status:
            w.append("a.status=?")
            a.append(status)
        else:
            w.append("a.status!='deleted'")
        if panel_id:
            w.append("a.panel_id=?")
            a.append(panel_id)
        if q:
            qq = f"%{q.strip().lstrip('@')}%"
            w.append("(a.username LIKE ? OR CAST(a.user_id AS TEXT) LIKE ? OR CAST(a.id AS TEXT)=? OR EXISTS(SELECT 1 FROM users u WHERE u.id=a.user_id AND u.username LIKE ?))")
            a += [qq, qq, q.strip(), qq]
        return " WHERE " + " AND ".join(w), tuple(a)

    async def list_agents(self, user_id=None, status=None, q="", panel_id=None, limit=50, offset=0):
        w, a = self._agent_where(user_id, status, q, panel_id)
        return await self.all("SELECT a.*, p.name AS panel_name, p.type AS panel_type, p.url AS panel_url, p.extra AS panel_extra FROM vpn_agents a LEFT JOIN vpn_panels p ON p.id=a.panel_id"
                              f"{w} ORDER BY a.id DESC LIMIT ? OFFSET ?", (*a, limit, offset))

    async def count_agents(self, user_id=None, status=None, q="", panel_id=None):
        w, a = self._agent_where(user_id, status, q, panel_id)
        return (await self.one(f"SELECT COUNT(*) c FROM vpn_agents a{w}", a))["c"]

    async def agents_syncable(self, older_than: int, limit=100):
        return await self.all("SELECT * FROM vpn_agents WHERE status IN ('active','disabled','limited','expired') AND last_sync<? ORDER BY last_sync LIMIT ?", (older_than, limit))
