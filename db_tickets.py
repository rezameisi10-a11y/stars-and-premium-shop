"""دیتابیس تیکت پشتیبانی. (mixin کلاس DB)"""
import time

TICKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, subject TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open', last_from TEXT NOT NULL DEFAULT 'user',
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_tickets_user ON tickets(user_id);
CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets(status);
CREATE TABLE IF NOT EXISTS ticket_msgs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, sender TEXT NOT NULL, admin_id INTEGER,
  text TEXT NOT NULL DEFAULT '', has_file INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_tkm_ticket ON ticket_msgs(ticket_id);
"""


class TicketDB:
    async def tk_create(self, user_id: int, subject: str, text: str, has_file=False) -> int:
        now = int(time.time())
        cur = await self.run("INSERT INTO tickets(user_id,subject,status,last_from,created_at,updated_at) VALUES(?,?,'open','user',?,?)", (user_id, subject[:80], now, now))
        await self.run("INSERT INTO ticket_msgs(ticket_id,sender,text,has_file,created_at) VALUES(?,?,?,?,?)", (cur.lastrowid, "user", text, int(has_file), now))
        return cur.lastrowid

    async def tk_add(self, tid: int, sender: str, text: str, admin_id=None, has_file=False):
        """پیام تازه؛ وضعیت: پاسخ کاربر → open، پاسخ ادمین → answered."""
        now = int(time.time())
        await self.run("INSERT INTO ticket_msgs(ticket_id,sender,admin_id,text,has_file,created_at) VALUES(?,?,?,?,?,?)", (tid, sender, admin_id, text, int(has_file), now))
        await self.run("UPDATE tickets SET status=?, last_from=?, updated_at=? WHERE id=?", ("open" if sender == "user" else "answered", sender, now, tid))

    async def tk_get(self, tid: int):
        return await self.one("SELECT * FROM tickets WHERE id=?", (tid,))

    async def tk_msgs(self, tid: int, limit=8):
        rows = await self.all("SELECT * FROM ticket_msgs WHERE ticket_id=? ORDER BY id DESC LIMIT ?", (tid, limit))
        return rows[::-1]

    async def tk_set_status(self, tid: int, status: str):
        await self.run("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, int(time.time()), tid))

    async def tk_user_list(self, user_id: int, limit=10):
        return await self.all("SELECT * FROM tickets WHERE user_id=? ORDER BY updated_at DESC LIMIT ?", (user_id, limit))

    async def tk_open_count(self, user_id: int) -> int:
        return (await self.one("SELECT COUNT(*) c FROM tickets WHERE user_id=? AND status!='closed'", (user_id,)))["c"]

    async def tk_last_msg_at(self, user_id: int) -> int:
        r = await self.one("SELECT MAX(m.created_at) t FROM ticket_msgs m JOIN tickets t ON t.id=m.ticket_id WHERE t.user_id=? AND m.sender='user'", (user_id,))
        return (r or {}).get("t") or 0

    async def tk_counts(self) -> dict:
        rows = await self.all("SELECT status, COUNT(*) c FROM tickets GROUP BY status")
        out = {"open": 0, "answered": 0, "closed": 0}
        out.update({r["status"]: r["c"] for r in rows})
        return out

    async def tk_list(self, status: str, offset=0, limit=10):
        return await self.all("SELECT t.*, u.first_name, u.username FROM tickets t LEFT JOIN users u ON u.id=t.user_id WHERE t.status=? "
                              "ORDER BY t.updated_at DESC LIMIT ? OFFSET ?", (status, limit, offset))
