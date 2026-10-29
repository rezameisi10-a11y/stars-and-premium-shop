import asyncio
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import aiosqlite

from db_vpn import VPN_SCHEMA, VpnDB
from i18n import BUILTIN_LANGS, N_
from settings_schema import DEFAULTS as NEW_DEFAULTS

TEHRAN = timezone(timedelta(hours=3, minutes=30))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
  balance INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
  referrer_id INTEGER, created_at INTEGER NOT NULL, last_seen INTEGER,
  lang TEXT, note TEXT, ban_reason TEXT, personal_discount INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_usage(
  user_id INTEGER NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, day));
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL,
  qty INTEGER NOT NULL, ref TEXT NOT NULL DEFAULT '', price_override INTEGER,
  active INTEGER NOT NULL DEFAULT 1, margin_override REAL, UNIQUE(kind, qty, ref));
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER,
  kind TEXT NOT NULL, title TEXT NOT NULL, qty INTEGER NOT NULL, ref TEXT NOT NULL DEFAULT '',
  target_id INTEGER, target_username TEXT, amount INTEGER NOT NULL,
  orig_amount INTEGER, discount_code TEXT, cost INTEGER NOT NULL DEFAULT 0, provider_ref TEXT,
  status TEXT NOT NULL DEFAULT 'pending_payment', note TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, order_id INTEGER,
  method TEXT NOT NULL, amount INTEGER NOT NULL, crypto_amount REAL,
  status TEXT NOT NULL DEFAULT 'pending', ref TEXT, receipt_file_id TEXT,
  created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pay_ref ON payments(method, ref);
CREATE TABLE IF NOT EXISTS wallet_tx(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '', ref_key TEXT, created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wtx_ref ON wallet_tx(ref_key);
CREATE INDEX IF NOT EXISTS ix_wtx_user ON wallet_tx(user_id);
CREATE TABLE IF NOT EXISTS menu_buttons(
  id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, label TEXT NOT NULL,
  style TEXT NOT NULL DEFAULT '', row_no INTEGER NOT NULL DEFAULT 0, pos INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1, extra TEXT NOT NULL DEFAULT '', builtin INTEGER NOT NULL DEFAULT 1,
  emoji_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS discounts(
  code TEXT PRIMARY KEY, percent INTEGER NOT NULL DEFAULT 0, fixed INTEGER NOT NULL DEFAULT 0,
  max_uses INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
  starts_at INTEGER NOT NULL DEFAULT 0, per_user INTEGER NOT NULL DEFAULT 1, min_amount INTEGER NOT NULL DEFAULT 0,
  max_discount INTEGER NOT NULL DEFAULT 0, applies_to TEXT NOT NULL DEFAULT 'all',
  first_only INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS discount_uses(
  code TEXT NOT NULL, user_id INTEGER NOT NULL, order_id INTEGER, PRIMARY KEY(code, user_id));
CREATE TABLE IF NOT EXISTS discount_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, user_id INTEGER NOT NULL, order_id INTEGER,
  amount INTEGER NOT NULL DEFAULT 0, ts INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_dlog_code ON discount_log(code, user_id);
CREATE TABLE IF NOT EXISTS discount_users(code TEXT NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(code, user_id));
CREATE TABLE IF NOT EXISTS admins(
  user_id INTEGER PRIMARY KEY, added_by INTEGER, created_at INTEGER NOT NULL,
  perms TEXT NOT NULL DEFAULT '*', name TEXT NOT NULL DEFAULT '', web_user TEXT, web_pw TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS languages(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, flag TEXT NOT NULL DEFAULT '', rtl INTEGER NOT NULL DEFAULT 0,
  bot_enabled INTEGER NOT NULL DEFAULT 1, web_enabled INTEGER NOT NULL DEFAULT 1,
  sort INTEGER NOT NULL DEFAULT 100, builtin INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS translations(
  scope TEXT NOT NULL, lang TEXT NOT NULL, src TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY(scope, lang, src));
CREATE TABLE IF NOT EXISTS rate_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, key TEXT NOT NULL, value REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_rate_key ON rate_history(key, ts);
CREATE TABLE IF NOT EXISTS referrals(
  referred_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  created_at INTEGER NOT NULL, activated_at INTEGER, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_ref_referrer ON referrals(referrer_id);
CREATE TABLE IF NOT EXISTS referral_rewards(
  id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referred_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL, amount INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'held', release_at INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '', UNIQUE(kind, referred_id, order_id));
CREATE INDEX IF NOT EXISTS ix_rw_status ON referral_rewards(status, release_at);
"""

# ستون‌های اضافه‌شده در نسخه‌های جدید؛ برای دیتابیس‌های قدیمی
MIGRATIONS = [
    ("users", "referrer_id", "INTEGER"),
    ("users", "last_seen", "INTEGER"),
    ("users", "lang", "TEXT"),
    ("users", "note", "TEXT"),
    ("users", "ban_reason", "TEXT"),
    ("users", "personal_discount", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "vpn_level", "TEXT NOT NULL DEFAULT 'f'"),
    ("vpn_plans", "utype", "TEXT NOT NULL DEFAULT 'f'"),
    ("orders", "orig_amount", "INTEGER"),
    ("orders", "discount_code", "TEXT"),
    ("orders", "cost", "INTEGER NOT NULL DEFAULT 0"),
    ("orders", "provider_ref", "TEXT"),
    ("products", "margin_override", "REAL"),
    ("menu_buttons", "emoji_id", "TEXT NOT NULL DEFAULT ''"),
    ("discounts", "starts_at", "INTEGER NOT NULL DEFAULT 0"),
    ("discounts", "per_user", "INTEGER NOT NULL DEFAULT 1"),
    ("discounts", "min_amount", "INTEGER NOT NULL DEFAULT 0"),
    ("discounts", "max_discount", "INTEGER NOT NULL DEFAULT 0"),
    ("discounts", "applies_to", "TEXT NOT NULL DEFAULT 'all'"),
    ("discounts", "first_only", "INTEGER NOT NULL DEFAULT 0"),
    ("discounts", "note", "TEXT NOT NULL DEFAULT ''"),
    ("discounts", "base", "TEXT NOT NULL DEFAULT 'price'"),
    ("admins", "perms", "TEXT NOT NULL DEFAULT '*'"),
    ("admins", "name", "TEXT NOT NULL DEFAULT ''"),
    ("admins", "web_user", "TEXT"),
    ("admins", "web_pw", "TEXT"),
]

# نرخ‌ها فقط نمونه‌اند و باید از پنل با نرخ روز تنظیم شوند.
DEFAULT_SETTINGS = {
    "shop_name": "فروشگاه استارز",
    "support_username": "",
    "maintenance": "0",
    "force_join": "0",
    "force_channel": "",
    "referral_percent": "3",
    "admin_notify_orders": "1",
    "star_buy_rate": "2000",
    "margin_percent": "10",
    "usdt_toman": "100000",
    "ton_toman": "300000",
    "min_topup": "50000",
    "max_topup": "500000000",
    "star_alert_threshold": "500",
    "pm_wallet": "1", "pm_card": "1", "pm_zarinpal": "1", "pm_ton": "1", "pm_usdt": "1",
    "card_number": "", "card_owner": "", "card_note": "",
    "pay_timeout_min": "30",
    "welcome_text": N_("به فروشگاه استارز و گیفت تلگرام خوش آمدید 🌟\nیکی از گزینه‌های زیر را انتخاب کنید."),
    "rules_text": N_("📜 <b>قوانین</b>\n۱) سفارش پس از تحویل قابل بازگشت نیست.\n۲) یوزرنیم/گیرنده را با دقت وارد کنید.\n۳) در صورت مشکل با پشتیبانی در ارتباط باشید."),
    "support_text": "",
    "maintenance_text": N_("🛠 ربات در حال بروزرسانی است. کمی بعد دوباره تلاش کنید."),
    "join_text": N_("برای استفاده از ربات ابتدا در کانال ما عضو شوید و سپس «بررسی عضویت» را بزنید."),
    "success_text": N_("ممنون از خرید شما 🌟"),
}
DEFAULT_SETTINGS.update(NEW_DEFAULTS)

STARS_PACKS = [50, 100, 250, 500, 1000, 2500]
PREMIUM_MONTHS = [3, 6, 12]

# عنوان پیش‌فرض محصولات (برای ترجمه‌ی خودکار عنوان‌های پیش‌فرض)
def default_title(kind, qty):
    return {"stars": f"⭐ {qty} استارز", "premium": f"👑 پریمیوم {qty} ماهه", "gift": f"🎁 گیفت {qty} ستاره‌ای"}[kind]


# (action, label, style, row, pos)
DEFAULT_MENU = [
    ("stars", N_("⭐ خرید استارز"), "success", 0, 0),
    ("premium", N_("👑 پریمیوم"), "primary", 0, 1),
    ("gift", N_("🎁 گیفت تلگرام"), "", 1, 0),
    ("vpn", N_("🛡 خرید VPN"), "success", 1, 1),
    ("wallet", N_("👛 کیف پول + شارژ"), "", 2, 0),
    ("orders", N_("🧾 سفارش‌های من"), "", 2, 1),
    ("vpn_my", N_("📡 سرویس‌های VPN من"), "", 3, 0),
    ("referral", N_("🤝 زیرمجموعه‌گیری"), "", 4, 0),
    ("rules", N_("📜 قوانین"), "", 4, 1),
    ("ai", N_("🤖 پشتیبان هوشمند"), "primary", 5, 2),
    ("support", N_("☎️ پشتیبانی"), "danger", 5, 0),
    ("lang", N_("🌐 زبان / Language"), "", 5, 1),
    ("admin", N_("⚙️ پنل مدیریت"), "primary", 6, 0),
]
BUILTIN_ACTIONS = {a for a, *_ in DEFAULT_MENU}

# فیلترها و مرتب‌سازی لیست کاربران
USER_FILTERS = ("all", "active", "banned", "balance", "buyers", "nobuy", "referrers", "vip", "new7", "inactive30")
USER_SORTS = {"new": "u.created_at DESC", "old": "u.created_at ASC", "seen": "u.last_seen DESC",
              "balance": "u.balance DESC", "spent": "spent DESC", "orders": "orders_n DESC"}


class DB(VpnDB):
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()   # جلوگیری از تداخل تراکنش‌ها روی یک اتصال

    async def connect(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.executescript(VPN_SCHEMA)
        for table, col, ddl in MIGRATIONS:
            cols = [r["name"] for r in await self.all(f"PRAGMA table_info({table})")]
            if col not in cols:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        for k, v in DEFAULT_SETTINGS.items():
            await self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        for q in STARS_PACKS:
            await self.conn.execute("INSERT OR IGNORE INTO products(kind,title,qty) VALUES('stars',?,?)", (default_title("stars", q), q))
        for m in PREMIUM_MONTHS:
            await self.conn.execute("INSERT OR IGNORE INTO products(kind,title,qty) VALUES('premium',?,?)", (default_title("premium", m), m))
        if (await self.one("SELECT COUNT(*) c FROM menu_buttons"))["c"] == 0:
            await self._seed_menu()
        elif not await self.one("SELECT 1 x FROM menu_buttons WHERE action='lang'"):
            row = (await self.one("SELECT COALESCE(MAX(row_no),-1)+1 r FROM menu_buttons"))["r"]
            await self.conn.execute(
                "INSERT INTO menu_buttons(action,label,style,row_no,pos,builtin) VALUES('lang',?,'',?,0,1)",
                ("🌐 زبان / Language", row))
        if (await self.one("SELECT COUNT(*) c FROM menu_buttons"))["c"] > 0:      # مهاجرت: دکمه‌های VPN برای نصب‌های قبلی
            missing = [(a, lb, st) for a, lb, st, _r, _p in DEFAULT_MENU if a in ("vpn", "vpn_my", "ai")
                       and not await self.one("SELECT 1 x FROM menu_buttons WHERE action=?", (a,))]
            if missing:
                row = (await self.one("SELECT COALESCE(MAX(row_no),-1)+1 r FROM menu_buttons"))["r"]
                for pos, (a, lb, st) in enumerate(missing):
                    await self.conn.execute("INSERT INTO menu_buttons(action,label,style,row_no,pos,builtin) VALUES(?,?,?,?,?,1)", (a, lb, st, row, pos))
        for i, (code, name, flag, rtl) in enumerate(BUILTIN_LANGS):
            await self.conn.execute(
                "INSERT OR IGNORE INTO languages(code,name,flag,rtl,sort,builtin) VALUES(?,?,?,?,?,1)", (code, name, flag, rtl, i))
        # انتقال یک‌باره‌ی استفاده‌های قدیمی از کد تخفیف به دفتر جدید
        if not await self.one("SELECT 1 x FROM settings WHERE key='migr_disc_log'"):
            await self.conn.execute(
                "INSERT INTO discount_log(code,user_id,order_id,amount,ts) SELECT code,user_id,order_id,0,? FROM discount_uses",
                (int(time.time()),))
            await self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('migr_disc_log','1')")
        # مرجع معرف‌های قدیمی را به جدول referrals منتقل می‌کنیم (وضعیت: فعال)
        await self.conn.execute(
            "INSERT OR IGNORE INTO referrals(referred_id,referrer_id,status,created_at,activated_at,note) "
            "SELECT id, referrer_id, 'active', created_at, created_at, 'legacy' FROM users WHERE referrer_id IS NOT NULL")
        await self.conn.commit()

    async def _seed_menu(self):
        for a, label, style, r, p in DEFAULT_MENU:
            await self.conn.execute(
                "INSERT INTO menu_buttons(action,label,style,row_no,pos,builtin) VALUES(?,?,?,?,?,1)", (a, label, style, r, p))

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def replace_file(self, src_path: str):
        """جایگزینی کامل دیتابیس با فایل دیگر (برای «وارد کردن بکاپ»). اتصال بسته و دوباره باز و مهاجرت‌ها اجرا می‌شود."""
        async with self.lock:
            await self.close()
            for suf in ("-wal", "-shm"):
                try:
                    os.remove(self.path + suf)
                except OSError:
                    pass
            shutil.copyfile(src_path, self.path)
            await self.connect()

    # ---------- پایه ----------
    async def all(self, sql, args=()):
        cur = await self.conn.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

    async def one(self, sql, args=()):
        cur = await self.conn.execute(sql, args)
        r = await cur.fetchone()
        return dict(r) if r else None

    async def run(self, sql, args=()):
        async with self.lock:
            try:
                cur = await self.conn.execute(sql, args)
                await self.conn.commit()
                return cur
            except Exception:
                await self.conn.rollback()
                raise

    # ---------- تنظیمات ----------
    async def settings(self) -> dict:
        return {r["key"]: r["value"] for r in await self.all("SELECT key,value FROM settings")}

    async def set_setting(self, key, value):
        await self.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, str(value)))

    # ---------- زبان‌ها و ترجمه‌ها ----------
    async def list_languages(self, bot_only=False, web_only=False):
        sql = "SELECT * FROM languages"
        if bot_only:
            sql += " WHERE bot_enabled=1"
        elif web_only:
            sql += " WHERE web_enabled=1"
        return await self.all(sql + " ORDER BY sort, code")

    async def get_language(self, code):
        return await self.one("SELECT * FROM languages WHERE code=?", (code,))

    async def upsert_language(self, code, name, flag="", rtl=0, bot_enabled=1, web_enabled=1):
        await self.run(
            "INSERT INTO languages(code,name,flag,rtl,bot_enabled,web_enabled,sort) VALUES(?,?,?,?,?,?,"
            "(SELECT COALESCE(MAX(sort),0)+1 FROM languages)) ON CONFLICT(code) DO UPDATE SET "
            "name=excluded.name, flag=excluded.flag, rtl=excluded.rtl, bot_enabled=excluded.bot_enabled, web_enabled=excluded.web_enabled",
            (code, name, flag, rtl, bot_enabled, web_enabled))

    async def delete_language(self, code) -> bool:
        async with self.lock:
            cur = await self.conn.execute("DELETE FROM languages WHERE code=? AND builtin=0", (code,))
            if cur.rowcount:
                await self.conn.execute("DELETE FROM translations WHERE lang=?", (code,))
                await self.conn.execute("UPDATE users SET lang=NULL WHERE lang=?", (code,))
            await self.conn.commit()
            return cur.rowcount == 1

    async def all_translations(self):
        return await self.all("SELECT scope,lang,src,text FROM translations")

    async def set_translations(self, scope, lang, items: dict):
        """items: {src: text}. متن خالی = حذف ویرایش (بازگشت به بسته‌ی آماده)."""
        async with self.lock:
            for src, text in items.items():
                if text and text.strip():
                    await self.conn.execute(
                        "INSERT INTO translations(scope,lang,src,text) VALUES(?,?,?,?) "
                        "ON CONFLICT(scope,lang,src) DO UPDATE SET text=excluded.text", (scope, lang, src, text))
                else:
                    await self.conn.execute("DELETE FROM translations WHERE scope=? AND lang=? AND src=?", (scope, lang, src))
            await self.conn.commit()

    async def lang_translations(self, scope, lang):
        return {r["src"]: r["text"] for r in await self.all(
            "SELECT src,text FROM translations WHERE scope=? AND lang=?", (scope, lang))}

    # ---------- کاربران ----------
    async def upsert_user(self, uid, username, first_name) -> bool:
        """کاربر را ثبت/به‌روز می‌کند؛ اگر کاربر جدید باشد True برمی‌گرداند."""
        now = int(time.time())
        async with self.lock:
            cur = await self.conn.execute(
                "INSERT OR IGNORE INTO users(id,username,first_name,created_at,last_seen) VALUES(?,?,?,?,?)",
                (uid, username, first_name, now, now))
            is_new = cur.rowcount == 1
            if not is_new:
                await self.conn.execute("UPDATE users SET username=?, first_name=?, last_seen=? WHERE id=?",
                                        (username, first_name, now, uid))
            await self.conn.commit()
        return is_new

    async def get_user(self, uid):
        return await self.one("SELECT * FROM users WHERE id=?", (uid,))

    async def get_user_by_username(self, username):
        return await self.one("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username.lstrip("@"),))

    async def set_user_lang(self, uid, lang):
        await self.run("UPDATE users SET lang=? WHERE id=?", (lang, uid))

    async def set_user_note(self, uid, note):
        await self.run("UPDATE users SET note=? WHERE id=?", (note[:1000], uid))

    async def set_personal_discount(self, uid, pct):
        await self.run("UPDATE users SET personal_discount=? WHERE id=?", (max(0, min(int(pct), 90)), uid))

    async def add_balance(self, uid, amount, reason="", ref_key=None) -> bool:
        """افزایش (یا با مقدار منفی کاهش بدون بررسی موجودی) کیف پول همراه ثبت در دفتر تراکنش‌ها.
        اگر ref_key تکراری باشد، هیچ تغییری اعمال نمی‌شود (idempotent)."""
        async with self.lock:
            try:
                cur = await self.conn.execute(
                    "INSERT OR IGNORE INTO wallet_tx(user_id,amount,reason,ref_key,created_at) VALUES(?,?,?,?,?)",
                    (uid, amount, reason, ref_key, int(time.time())))
                if cur.rowcount == 0:
                    await self.conn.rollback()
                    return False
                await self.conn.execute("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise

    async def deduct_balance(self, uid, amount, reason="") -> bool:
        async with self.lock:
            try:
                cur = await self.conn.execute("UPDATE users SET balance=balance-? WHERE id=? AND balance>=?",
                                              (amount, uid, amount))
                if cur.rowcount != 1:
                    await self.conn.rollback()
                    return False
                await self.conn.execute("INSERT INTO wallet_tx(user_id,amount,reason,created_at) VALUES(?,?,?,?)",
                                        (uid, -amount, reason, int(time.time())))
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise

    async def set_balance_exact(self, uid, target, reason="") -> int | None:
        """موجودی را دقیقاً روی target می‌گذارد و تفاوت را در دفتر ثبت می‌کند. خروجی: تفاوت (delta) یا None اگر کاربر نباشد."""
        async with self.lock:
            try:
                row = await (await self.conn.execute("SELECT balance FROM users WHERE id=?", (uid,))).fetchone()
                if not row:
                    return None
                delta = int(target) - int(row["balance"])
                if delta != 0:
                    await self.conn.execute("INSERT INTO wallet_tx(user_id,amount,reason,created_at) VALUES(?,?,?,?)",
                                            (uid, delta, reason, int(time.time())))
                    await self.conn.execute("UPDATE users SET balance=? WHERE id=?", (int(target), uid))
                await self.conn.commit()
                return delta
            except Exception:
                await self.conn.rollback()
                raise

    async def set_banned(self, uid, banned: bool, reason=""):
        await self.run("UPDATE users SET banned=?, ban_reason=? WHERE id=?", (1 if banned else 0, reason[:300] if banned else "", uid))

    async def all_user_ids(self, lang=None):
        if lang:
            return [r["id"] for r in await self.all("SELECT id FROM users WHERE banned=0 AND COALESCE(lang,'')=?", (lang,))]
        return [r["id"] for r in await self.all("SELECT id FROM users WHERE banned=0")]

    @staticmethod
    def _user_where(q, flt="all"):
        w, a = [], []
        q = (q or "").strip()
        if q:
            if q.lstrip("-").isdigit():
                w.append("u.id=?")
                a.append(int(q))
            else:
                like = f"%{q.lstrip('@')}%"
                w.append("(u.username LIKE ? OR u.first_name LIKE ? OR u.note LIKE ?)")
                a += [like, like, like]
        now = int(time.time())
        if flt == "active":
            w.append("u.banned=0")
        elif flt == "banned":
            w.append("u.banned=1")
        elif flt == "balance":
            w.append("u.balance>0")
        elif flt == "buyers":
            w.append("EXISTS(SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.status='done')")
        elif flt == "nobuy":
            w.append("NOT EXISTS(SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.status='done')")
        elif flt == "referrers":
            w.append("EXISTS(SELECT 1 FROM referrals r WHERE r.referrer_id=u.id)")
        elif flt == "vip":
            w.append("u.personal_discount>0")
        elif flt == "new7":
            w.append("u.created_at>?")
            a.append(now - 7 * 86400)
        elif flt == "inactive30":
            w.append("COALESCE(u.last_seen,u.created_at)<?")
            a.append(now - 30 * 86400)
        return (" WHERE " + " AND ".join(w)) if w else "", tuple(a)

    async def search_users(self, q="", limit=20, offset=0, flt="all", sort="new"):
        w, a = self._user_where(q, flt)
        order = USER_SORTS.get(sort, USER_SORTS["new"])
        return await self.all(
            "SELECT u.*, "
            "(SELECT COALESCE(SUM(amount),0) FROM orders o WHERE o.user_id=u.id AND o.status='done') AS spent, "
            "(SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id AND o.status='done') AS orders_n "
            f"FROM users u{w} ORDER BY {order} LIMIT ? OFFSET ?", (*a, limit, offset))

    async def count_users(self, q="", flt="all"):
        w, a = self._user_where(q, flt)
        return (await self.one(f"SELECT COUNT(*) c FROM users u{w}", a))["c"]

    async def user_ids_where(self, q="", flt="all", limit=100000):
        w, a = self._user_where(q, flt)
        return [r["id"] for r in await self.all(f"SELECT u.id FROM users u{w} LIMIT ?", (*a, limit))]

    async def user_stats(self, uid):
        o = await self.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s, MAX(created_at) last FROM orders WHERE user_id=? AND status='done'", (uid,))
        tot = (await self.one("SELECT COUNT(*) c FROM orders WHERE user_id=?", (uid,)))["c"]
        pays = (await self.one("SELECT COUNT(*) c FROM payments WHERE user_id=?", (uid,)))["c"]
        txs = (await self.one("SELECT COUNT(*) c FROM wallet_tx WHERE user_id=?", (uid,)))["c"]
        profit = (await self.one("SELECT COALESCE(SUM(amount-cost),0) s FROM orders WHERE user_id=? AND status='done' AND cost>0", (uid,)))["s"]
        return {"done": o["c"], "spent": o["s"], "last_order": o["last"], "orders": tot, "payments": pays, "wallet_tx": txs, "profit": profit}

    async def wallet_history(self, uid=None, limit=30, offset=0):
        if uid:
            return await self.all("SELECT * FROM wallet_tx WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (uid, limit, offset))
        return await self.all("SELECT * FROM wallet_tx ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))

    async def count_wallet_tx(self):
        return (await self.one("SELECT COUNT(*) c FROM wallet_tx"))["c"]

    async def user_payments(self, uid, limit=15):
        return await self.all("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))

    # ---------- زیرمجموعه‌گیری ----------
    async def create_referral(self, referred, referrer, status="pending") -> bool:
        async with self.lock:
            cur = await self.conn.execute(
                "INSERT OR IGNORE INTO referrals(referred_id,referrer_id,status,created_at) VALUES(?,?,?,?)",
                (referred, referrer, status, int(time.time())))
            if cur.rowcount:
                await self.conn.execute("UPDATE users SET referrer_id=? WHERE id=? AND referrer_id IS NULL", (referrer, referred))
            await self.conn.commit()
            return cur.rowcount == 1

    async def get_referral(self, referred):
        return await self.one("SELECT * FROM referrals WHERE referred_id=?", (referred,))

    async def set_referral_status(self, referred, status, note=""):
        act = ", activated_at=? " if status == "active" else " "
        args = [status, note]
        if status == "active":
            args.append(int(time.time()))
        args.append(referred)
        await self.run(f"UPDATE referrals SET status=?, note=?{act}WHERE referred_id=?", args)

    async def delete_referral(self, referred):
        async with self.lock:
            await self.conn.execute("DELETE FROM referrals WHERE referred_id=?", (referred,))
            await self.conn.execute("UPDATE users SET referrer_id=NULL WHERE id=?", (referred,))
            await self.conn.commit()

    async def list_referrals(self, referrer, limit=50):
        return await self.all(
            "SELECT r.*, u.username, u.first_name FROM referrals r LEFT JOIN users u ON u.id=r.referred_id "
            "WHERE r.referrer_id=? ORDER BY r.created_at DESC LIMIT ?", (referrer, limit))

    async def count_referrals_today(self, referrer):
        since = int(time.time()) - 86400
        return (await self.one("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND created_at>?", (referrer, since)))["c"]

    async def referral_stats(self, uid):
        rows = await self.all("SELECT status, COUNT(*) c FROM referrals WHERE referrer_id=? GROUP BY status", (uid,))
        by = {r["status"]: r["c"] for r in rows}
        earned = (await self.one("SELECT COALESCE(SUM(amount),0) s FROM referral_rewards WHERE referrer_id=? AND status='paid'", (uid,)))["s"]
        legacy = (await self.one("SELECT COALESCE(SUM(amount),0) s FROM wallet_tx WHERE user_id=? AND reason LIKE 'referral%' "
                                 "AND ref_key NOT LIKE 'rw:%'", (uid,)))["s"]
        held = (await self.one("SELECT COALESCE(SUM(amount),0) s FROM referral_rewards WHERE referrer_id=? AND status='held'", (uid,)))["s"]
        return {"count": sum(by.values()), "active": by.get("active", 0), "pending": by.get("pending", 0),
                "left": by.get("left", 0), "rejected": by.get("rejected", 0), "earned": earned + legacy, "held": held}

    async def add_reward(self, referrer, referred, order_id, kind, amount, status, release_at, note="") -> int | None:
        cur = await self.run(
            "INSERT OR IGNORE INTO referral_rewards(referrer_id,referred_id,order_id,kind,amount,status,release_at,created_at,note) "
            "VALUES(?,?,?,?,?,?,?,?,?)", (referrer, referred, order_id, kind, amount, status, release_at, int(time.time()), note))
        return cur.lastrowid if cur.rowcount else None

    async def due_rewards(self, now):
        return await self.all("SELECT * FROM referral_rewards WHERE status='held' AND release_at<=? ORDER BY id LIMIT 100", (now,))

    async def set_reward_status(self, rid, status, note="") -> bool:
        cur = await self.run("UPDATE referral_rewards SET status=?, note=? WHERE id=? AND status='held'", (status, note, rid))
        return cur.rowcount == 1

    async def get_reward(self, rid):
        return await self.one("SELECT * FROM referral_rewards WHERE id=?", (rid,))

    async def count_rewards(self, status=None):
        if status:
            return (await self.one("SELECT COUNT(*) c FROM referral_rewards WHERE status=?", (status,)))["c"]
        return (await self.one("SELECT COUNT(*) c FROM referral_rewards"))["c"]

    async def reward_totals(self):
        rows = await self.all("SELECT status, COUNT(*) c, COALESCE(SUM(amount),0) s FROM referral_rewards GROUP BY status")
        return {r["status"]: (r["c"], r["s"]) for r in rows}

    async def list_referrals_all(self, status=None, limit=25, offset=0):
        w = " WHERE r.status=?" if status else ""
        a = ((status,) if status else ()) + (limit, offset)
        return await self.all(
            "SELECT r.*, ur.username AS referrer_username, ud.username AS referred_username, ud.first_name AS referred_name "
            "FROM referrals r LEFT JOIN users ur ON ur.id=r.referrer_id LEFT JOIN users ud ON ud.id=r.referred_id"
            f"{w} ORDER BY r.created_at DESC LIMIT ? OFFSET ?", a)

    async def count_referrals_all(self, status=None):
        if status:
            return (await self.one("SELECT COUNT(*) c FROM referrals WHERE status=?", (status,)))["c"]
        return (await self.one("SELECT COUNT(*) c FROM referrals"))["c"]

    async def user_discount_usage(self, uid, limit=20):
        return await self.all("SELECT * FROM discount_log WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))

    async def list_rewards(self, limit=100, offset=0, status=None):
        sql = "SELECT * FROM referral_rewards" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ? OFFSET ?"
        return await self.all(sql, ((status,) if status else ()) + (limit, offset))

    # ---------- محصولات ----------
    async def ai_usage_get(self, uid, day):
        r = await self.one("SELECT n, tokens FROM ai_usage WHERE user_id=? AND day=?", (uid, day))
        return (r["n"], r["tokens"]) if r else (0, 0)

    async def ai_usage_total(self, day) -> int:
        return (await self.one("SELECT COALESCE(SUM(n),0) c FROM ai_usage WHERE day=?", (day,)))["c"]

    async def ai_usage_add(self, uid, day, tokens=0):
        await self.run("INSERT INTO ai_usage(user_id,day,n,tokens) VALUES(?,?,1,?) "
                       "ON CONFLICT(user_id,day) DO UPDATE SET n=n+1, tokens=tokens+excluded.tokens", (uid, day, int(tokens or 0)))
        await self.run("DELETE FROM ai_usage WHERE day < date('now','-60 day')")

    async def list_products(self, kind, only_active=True):
        sql = "SELECT * FROM products WHERE kind=?" + (" AND active=1" if only_active else "") + " ORDER BY qty, id"
        return await self.all(sql, (kind,))

    async def get_product(self, pid):
        return await self.one("SELECT * FROM products WHERE id=?", (pid,))

    async def toggle_product(self, pid):
        await self.run("UPDATE products SET active=1-active WHERE id=?", (pid,))

    async def set_price_override(self, pid, price):
        await self.run("UPDATE products SET price_override=? WHERE id=?", (price, pid))

    async def set_margin_override(self, pid, pct):
        await self.run("UPDATE products SET margin_override=? WHERE id=?", (pct, pid))

    async def set_product_title(self, pid, title):
        await self.run("UPDATE products SET title=? WHERE id=?", (title, pid))

    async def add_stars_pack(self, qty):
        cur = await self.run("INSERT OR IGNORE INTO products(kind,title,qty) VALUES('stars',?,?)", (default_title("stars", qty), qty))
        return cur.rowcount == 1

    async def sync_gifts(self, items):
        """items: لیست (gift_id, star_count, title, available)"""
        async with self.lock:
            seen = set()
            for gid, stars, title, avail in items:
                seen.add(gid)
                await self.conn.execute(
                    "INSERT INTO products(kind,title,qty,ref,active) VALUES('gift',?,?,?,?) "
                    "ON CONFLICT(kind,qty,ref) DO UPDATE SET "
                    "active=CASE WHEN ?=0 THEN 0 ELSE products.active END",
                    (title, stars, gid, 1 if avail else 0, 1 if avail else 0))
            for r in await self.all("SELECT id,ref FROM products WHERE kind='gift'"):
                if r["ref"] not in seen:
                    await self.conn.execute("UPDATE products SET active=0 WHERE id=?", (r["id"],))
            await self.conn.commit()

    # ---------- سفارش‌ها ----------
    async def create_order(self, user_id, product, target_id, target_username, amount, cost=0) -> int:
        now = int(time.time())
        cur = await self.run(
            "INSERT INTO orders(user_id,product_id,kind,title,qty,ref,target_id,target_username,amount,orig_amount,cost,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, product["id"], product["kind"], product["title"], product["qty"], product["ref"],
             target_id, target_username, amount, amount, cost, now, now))
        return cur.lastrowid

    async def get_order(self, oid):
        return await self.one("SELECT * FROM orders WHERE id=?", (oid,))

    async def transition(self, oid, from_statuses, to_status, note=None) -> bool:
        marks = ",".join("?" * len(from_statuses))
        cur = await self.run(
            f"UPDATE orders SET status=?, note=COALESCE(?,note), updated_at=? WHERE id=? AND status IN ({marks})",
            (to_status, note, int(time.time()), oid, *from_statuses))
        return cur.rowcount == 1

    async def set_order_discount(self, oid, code, new_amount) -> bool:
        cur = await self.run("UPDATE orders SET amount=?, discount_code=? WHERE id=? AND status='pending_payment'",
                             (new_amount, code, oid))
        return cur.rowcount == 1

    async def set_order_provider_ref(self, oid, ref):
        await self.run("UPDATE orders SET provider_ref=? WHERE id=?", (str(ref)[:200], oid))

    async def user_orders(self, uid, limit=10):
        return await self.all("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))

    async def user_done_orders(self, uid):
        return (await self.one("SELECT COUNT(*) c FROM orders WHERE user_id=? AND status IN ('done','paid','delivering','manual_pending')", (uid,)))["c"]

    @staticmethod
    def _order_where(status, user_id):
        w, a = [], []
        if status:
            w.append("o.status=?"); a.append(status)
        if user_id:
            w.append("o.user_id=?"); a.append(user_id)
        return (" WHERE " + " AND ".join(w)) if w else "", a

    async def list_orders(self, status=None, user_id=None, limit=25, offset=0):
        w, a = self._order_where(status, user_id)
        return await self.all(
            f"SELECT o.*, u.username AS user_username FROM orders o LEFT JOIN users u ON u.id=o.user_id{w} "
            f"ORDER BY o.id DESC LIMIT ? OFFSET ?", (*a, limit, offset))

    async def count_orders(self, status=None, user_id=None):
        w, a = self._order_where(status, user_id)
        return (await self.one(f"SELECT COUNT(*) c FROM orders o{w}", a))["c"]

    async def queue_orders(self, limit=20):
        return await self.all("SELECT * FROM orders WHERE status IN ('manual_pending','failed') ORDER BY id LIMIT ?", (limit,))

    async def expire_orders(self, older_than_ts):
        cur = await self.run("UPDATE orders SET status='cancelled', updated_at=? WHERE status='pending_payment' AND created_at<?",
                             (int(time.time()), older_than_ts))
        return cur.rowcount

    # ---------- پرداخت‌ها ----------
    async def create_payment(self, user_id, order_id, method, amount, crypto_amount=None) -> int:
        cur = await self.run(
            "INSERT INTO payments(user_id,order_id,method,amount,crypto_amount,created_at) VALUES(?,?,?,?,?,?)",
            (user_id, order_id, method, amount, crypto_amount, int(time.time())))
        return cur.lastrowid

    async def get_payment(self, pid):
        return await self.one("SELECT * FROM payments WHERE id=?", (pid,))

    async def update_payment(self, pid, **fields):
        cols = ",".join(f"{k}=?" for k in fields)
        await self.run(f"UPDATE payments SET {cols} WHERE id=?", (*fields.values(), pid))

    async def claim_payment(self, pid, ref=None) -> bool:
        """تأیید اتمیک پرداخت؛ فقط یک بار موفق می‌شود و ref تکراری (مثلا تراکنش یکسان) رد می‌شود."""
        async with self.lock:
            try:
                cur = await self.conn.execute(
                    "UPDATE payments SET status='confirmed', ref=COALESCE(?,ref) "
                    "WHERE id=? AND status IN ('pending','waiting_admin')", (ref, pid))
                await self.conn.commit()
                return cur.rowcount == 1
            except sqlite3.IntegrityError:
                await self.conn.rollback()
                return False

    async def reject_payment(self, pid) -> bool:
        cur = await self.run("UPDATE payments SET status='rejected' WHERE id=? AND status IN ('pending','waiting_admin')", (pid,))
        return cur.rowcount == 1

    async def pending_payments(self, method=None, status="pending"):
        if method:
            return await self.all("SELECT * FROM payments WHERE status=? AND method=? ORDER BY id", (status, method))
        return await self.all("SELECT * FROM payments WHERE status=? ORDER BY id", (status,))

    async def list_payments(self, status=None, limit=25, offset=0):
        if status:
            return await self.all("SELECT * FROM payments WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?", (status, limit, offset))
        return await self.all("SELECT * FROM payments ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))

    async def count_payments(self, status=None):
        if status:
            return (await self.one("SELECT COUNT(*) c FROM payments WHERE status=?", (status,)))["c"]
        return (await self.one("SELECT COUNT(*) c FROM payments"))["c"]

    async def expire_payments(self, older_than_ts):
        cur = await self.run("UPDATE payments SET status='expired' WHERE status='pending' AND created_at<?", (older_than_ts,))
        return cur.rowcount

    # ---------- منوی اصلی (چیدمان و رنگ دکمه‌ها) ----------
    async def list_menu(self, only_enabled=False):
        sql = "SELECT * FROM menu_buttons" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY row_no,pos,id"
        return await self.all(sql)

    async def get_menu_button(self, bid):
        return await self.one("SELECT * FROM menu_buttons WHERE id=?", (bid,))

    async def update_menu_button(self, bid, **fields):
        allowed = {"label", "style", "enabled", "extra", "row_no", "pos", "emoji_id"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        cols = ",".join(f"{k}=?" for k in fields)
        await self.run(f"UPDATE menu_buttons SET {cols} WHERE id=?", (*fields.values(), bid))

    async def add_menu_button(self, action, label, extra, style="", emoji_id=""):
        row = (await self.one("SELECT COALESCE(MAX(row_no),-1)+1 r FROM menu_buttons"))["r"]
        cur = await self.run("INSERT INTO menu_buttons(action,label,style,row_no,pos,extra,builtin,emoji_id) VALUES(?,?,?,?,0,?,0,?)",
                             (action, label, style, row, extra, emoji_id))
        return cur.lastrowid

    async def delete_menu_button(self, bid) -> bool:
        cur = await self.run("DELETE FROM menu_buttons WHERE id=? AND builtin=0", (bid,))
        await self.normalize_menu()
        return cur.rowcount == 1

    async def reset_menu(self):
        async with self.lock:
            await self.conn.execute("DELETE FROM menu_buttons")
            await self._seed_menu()
            await self.conn.commit()

    async def _grid(self):
        grid, cur_row = [], None
        for it in await self.list_menu():
            if it["row_no"] != cur_row:
                grid.append([])
                cur_row = it["row_no"]
            grid[-1].append(it)
        return grid

    async def _save_grid(self, grid):
        async with self.lock:
            for r, row in enumerate(g for g in grid if g):
                for p, it in enumerate(row):
                    await self.conn.execute("UPDATE menu_buttons SET row_no=?, pos=? WHERE id=?", (r, p, it["id"]))
            await self.conn.commit()

    async def normalize_menu(self):
        await self._save_grid(await self._grid())

    async def move_menu_button(self, bid, op) -> bool:
        """op: left | right (جابجایی در ردیف) · rowup | rowdown (جابجایی ردیف) ·
        join_up (انتقال به ردیف بالا) · split (جدا کردن در ردیف جدید)"""
        grid = await self._grid()
        r = c = None
        for i, row in enumerate(grid):
            for j, it in enumerate(row):
                if it["id"] == bid:
                    r, c = i, j
        if r is None:
            return False
        row = grid[r]
        if op == "left" and c > 0:
            row[c - 1], row[c] = row[c], row[c - 1]
        elif op == "right" and c < len(row) - 1:
            row[c + 1], row[c] = row[c], row[c + 1]
        elif op == "rowup" and r > 0:
            grid[r - 1], grid[r] = grid[r], grid[r - 1]
        elif op == "rowdown" and r < len(grid) - 1:
            grid[r + 1], grid[r] = grid[r], grid[r + 1]
        elif op == "join_up" and r > 0 and len(grid[r - 1]) < 3:
            grid[r - 1].append(row.pop(c))
        elif op == "split" and len(row) > 1:
            grid.insert(r + 1, [row.pop(c)])
        else:
            return False
        await self._save_grid(grid)
        return True

    # ---------- کدهای تخفیف ----------
    async def create_discount(self, code, percent, fixed, max_uses, expires_at, *, starts_at=0, per_user=1, min_amount=0,
                              max_discount=0, applies_to="all", first_only=0, note="", users=(), base="price"):
        code = code.strip().upper()
        cur = await self.run(
            "INSERT OR IGNORE INTO discounts(code,percent,fixed,max_uses,expires_at,created_at,starts_at,per_user,min_amount,"
            "max_discount,applies_to,first_only,note,base) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, percent, fixed, max_uses, expires_at, int(time.time()), starts_at, per_user, min_amount,
             max_discount, applies_to or "all", 1 if first_only else 0, note[:200], "profit" if base == "profit" else "price"))
        if cur.rowcount == 1:
            for uid in users:
                await self.add_discount_user(code, uid)
        return cur.rowcount == 1

    async def update_discount(self, code, **fields):
        allowed = {"percent", "fixed", "max_uses", "expires_at", "starts_at", "per_user", "min_amount", "max_discount",
                   "applies_to", "first_only", "note", "active", "base"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            cols = ",".join(f"{k}=?" for k in fields)
            await self.run(f"UPDATE discounts SET {cols} WHERE code=?", (*fields.values(), code.upper()))

    async def get_discount(self, code):
        return await self.one("SELECT * FROM discounts WHERE code=?", (code.strip().upper(),))

    async def list_discounts(self):
        return await self.all("SELECT * FROM discounts ORDER BY created_at DESC")

    async def toggle_discount(self, code):
        await self.run("UPDATE discounts SET active=1-active WHERE code=?", (code.upper(),))

    async def delete_discount(self, code):
        async with self.lock:
            await self.conn.execute("DELETE FROM discounts WHERE code=?", (code.upper(),))
            await self.conn.execute("DELETE FROM discount_users WHERE code=?", (code.upper(),))
            await self.conn.commit()

    async def add_discount_user(self, code, uid):
        await self.run("INSERT OR IGNORE INTO discount_users(code,user_id) VALUES(?,?)", (code.upper(), uid))

    async def remove_discount_user(self, code, uid):
        await self.run("DELETE FROM discount_users WHERE code=? AND user_id=?", (code.upper(), uid))

    async def discount_users(self, code):
        return [r["user_id"] for r in await self.all("SELECT user_id FROM discount_users WHERE code=?", (code.upper(),))]

    async def user_discounts(self, uid):
        """کدهای اختصاصی که برای این کاربر فعال شده‌اند."""
        return await self.all(
            "SELECT d.* FROM discounts d JOIN discount_users du ON du.code=d.code WHERE du.user_id=? ORDER BY d.created_at DESC", (uid,))

    async def discount_usage(self, code, limit=100):
        return await self.all("SELECT * FROM discount_log WHERE code=? ORDER BY id DESC LIMIT ?", (code.upper(), limit))

    async def discount_savings(self, code):
        return (await self.one("SELECT COALESCE(SUM(amount),0) s FROM discount_log WHERE code=?", (code.upper(),)))["s"]

    async def check_discount(self, code, user_id, amount, kind="stars", cost=0):
        """(ok, دلیل، مبلغ تخفیف). دلیل: ok | invalid | not_started | expired | exhausted | not_allowed | used | kind | min_amount | first_only | no_profit | unusable
        مبلغ نهایی هرگز کمتر از ۱٬۰۰۰ تومان نمی‌شود. اگر مبنای کد «سود» باشد، درصد/مبلغ فقط از سود (قیمت − هزینه) حساب می‌شود
        و قیمت نهایی هیچ‌وقت از هزینه‌ی تمام‌شده کمتر نخواهد شد."""
        code = (code or "").strip().upper()
        d = await self.one("SELECT * FROM discounts WHERE code=?", (code,))
        if not d or not d["active"]:
            return False, "invalid", 0
        now = time.time()
        if d["starts_at"] and d["starts_at"] > now:
            return False, "not_started", 0
        if d["expires_at"] and d["expires_at"] < now:
            return False, "expired", 0
        if d["max_uses"] and d["used"] >= d["max_uses"]:
            return False, "exhausted", 0
        allowed = await self.discount_users(code)
        if allowed and user_id not in allowed:
            return False, "not_allowed", 0
        if d["per_user"]:
            n = (await self.one("SELECT COUNT(*) c FROM discount_log WHERE code=? AND user_id=?", (code, user_id)))["c"]
            if n >= d["per_user"]:
                return False, "used", 0
        if d["applies_to"] not in ("", "all") and kind not in d["applies_to"].split(","):
            return False, "kind", 0
        if d["min_amount"] and amount < d["min_amount"]:
            return False, "min_amount", 0
        if d["first_only"] and await self.user_done_orders(user_id) > 0:
            return False, "first_only", 0
        pool = amount
        if d.get("base") == "profit":
            pool = amount - int(cost or 0)
            if int(cost or 0) <= 0 or pool <= 0:
                return False, "no_profit", 0
        disc = pool * d["percent"] // 100 if d["percent"] else d["fixed"]
        if d["percent"] and d["max_discount"]:
            disc = min(disc, d["max_discount"])
        disc = max(0, min(disc, pool, amount - 1000))
        if disc <= 0:
            return False, "unusable", 0
        return True, "ok", disc

    async def use_discount(self, code, user_id, order_id, amount=0):
        async with self.lock:
            cur = await self.conn.execute("SELECT 1 FROM discount_log WHERE code=? AND order_id=?", (code, order_id))
            if await cur.fetchone():
                return
            await self.conn.execute("INSERT INTO discount_log(code,user_id,order_id,amount,ts) VALUES(?,?,?,?,?)",
                                    (code, user_id, order_id, amount, int(time.time())))
            await self.conn.execute("UPDATE discounts SET used=used+1 WHERE code=?", (code,))
            await self.conn.commit()

    # ---------- ادمین‌ها و لاگ ----------
    async def list_admins(self):
        return await self.all("SELECT * FROM admins ORDER BY created_at")

    async def get_admin(self, uid):
        return await self.one("SELECT * FROM admins WHERE user_id=?", (uid,))

    async def get_admin_by_web_user(self, web_user):
        return await self.one("SELECT * FROM admins WHERE LOWER(web_user)=LOWER(?)", (web_user,))

    async def add_admin(self, uid, by, perms="*", name=""):
        cur = await self.run("INSERT OR IGNORE INTO admins(user_id,added_by,created_at,perms,name) VALUES(?,?,?,?,?)",
                             (uid, by, int(time.time()), perms, name))
        return cur.rowcount == 1

    async def update_admin(self, uid, perms=None, name=None):
        if perms is not None:
            await self.run("UPDATE admins SET perms=? WHERE user_id=?", (perms, uid))
        if name is not None:
            await self.run("UPDATE admins SET name=? WHERE user_id=?", (name[:40], uid))

    async def set_admin_web(self, uid, web_user, pw_hash):
        await self.run("UPDATE admins SET web_user=?, web_pw=? WHERE user_id=?", (web_user or None, pw_hash or None, uid))

    async def remove_admin(self, uid):
        cur = await self.run("DELETE FROM admins WHERE user_id=?", (uid,))
        return cur.rowcount == 1

    async def log(self, actor, action, detail=""):
        await self.run("INSERT INTO audit_log(ts,actor,action,detail) VALUES(?,?,?,?)", (int(time.time()), str(actor), action, detail[:500]))

    async def recent_logs(self, limit=100, offset=0):
        return await self.all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))

    async def count_logs(self):
        return (await self.one("SELECT COUNT(*) c FROM audit_log"))["c"]

    # ---------- نرخ‌ها ----------
    async def add_rate_point(self, key, value):
        async with self.lock:
            await self.conn.execute("INSERT INTO rate_history(ts,key,value) VALUES(?,?,?)", (int(time.time()), key, float(value)))
            await self.conn.execute(
                "DELETE FROM rate_history WHERE key=? AND id NOT IN (SELECT id FROM rate_history WHERE key=? ORDER BY id DESC LIMIT 3000)",
                (key, key))
            await self.conn.commit()

    async def rate_history(self, key, limit=60):
        rows = await self.all("SELECT ts,value FROM rate_history WHERE key=? ORDER BY id DESC LIMIT ?", (key, limit))
        return list(reversed(rows))

    # ---------- آمار ----------
    async def stats(self):
        now = datetime.now(TEHRAN)
        day_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        users = (await self.one("SELECT COUNT(*) c FROM users"))["c"]
        new_today = (await self.one("SELECT COUNT(*) c FROM users WHERE created_at>=?", (day_start,)))["c"]
        done = await self.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s, COALESCE(SUM(CASE WHEN cost>0 THEN amount-cost ELSE 0 END),0) p "
                              "FROM orders WHERE status='done'")
        today = await self.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s, COALESCE(SUM(CASE WHEN cost>0 THEN amount-cost ELSE 0 END),0) p "
                               "FROM orders WHERE status='done' AND updated_at>=?", (day_start,))
        todo = (await self.one("SELECT COUNT(*) c FROM orders WHERE status IN ('manual_pending','failed')"))["c"]
        waiting = (await self.one("SELECT COUNT(*) c FROM payments WHERE status='waiting_admin'"))["c"]
        pending = (await self.one("SELECT COUNT(*) c FROM payments WHERE status='pending'"))["c"]
        wallets = (await self.one("SELECT COALESCE(SUM(balance),0) s FROM users"))["s"]
        since = day_start - 13 * 86400
        rows = await self.all(
            "SELECT strftime('%Y-%m-%d', updated_at+12600, 'unixepoch') d, SUM(amount) s, COUNT(*) c "
            "FROM orders WHERE status='done' AND updated_at>=? GROUP BY d", (since,))
        by_day = {r["d"]: r for r in rows}
        series = []
        for i in range(13, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            series.append((d[5:], by_day.get(d, {}).get("s", 0) or 0, by_day.get(d, {}).get("c", 0) or 0))
        return {"users": users, "new_today": new_today, "done": done["c"], "revenue": done["s"], "profit": done["p"],
                "today_orders": today["c"], "today_revenue": today["s"], "today_profit": today["p"], "todo": todo,
                "waiting": waiting, "pending_pay": pending, "wallets": wallets, "series": series}

    async def backup_to(self, path):
        async with self.lock:
            await self.conn.execute("VACUUM INTO ?", (path,))
