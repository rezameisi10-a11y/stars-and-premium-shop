"""اتصال ربات به یک گروه (ترجیحاً سوپرگروه با «تاپیک‌ها»):
- ربات تاپیک‌های رسیدها / فروش / کاربران جدید / هشدارها / گزارش شبانه / بکاپ را خودش می‌سازد؛
- رویدادها (رسید، خرید، کاربر جدید، خطا) در تاپیک مربوط فرستاده می‌شود؛
- گزارش شبانه (ساعت قابل تنظیم، وقت تهران) و بکاپ هر N ساعت در همان گروه ارسال می‌شود.
اگر گروه تاپیک نداشته باشد همه‌چیز در چت اصلی گروه ارسال می‌شود. هیچ خطایی از اینجا به جریان اصلی نمی‌رسد."""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat
from aiogram.types import BufferedInputFile

from i18n import N_

log = logging.getLogger("groupfeed")
TEHRAN = timezone(timedelta(hours=3, minutes=30))

# کلید تاپیک ← عنوان
TOPICS = {
    "receipts": N_("🧾 رسیدها"),
    "sales": N_("🛒 فروش"),
    "users": N_("👤 کاربران جدید"),
    "alerts": N_("⚠️ هشدارها و خطاها"),
    "reports": N_("📊 گزارش شبانه"),
    "backups": N_("💾 بکاپ"),
}
EVENT_SETTING = {"receipts": "grp_ev_receipts", "sales": "grp_ev_sales", "users": "grp_ev_users", "alerts": "grp_ev_alerts"}
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_chat_id(raw) -> int | None:
    t = str(raw or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    return int(t) if re.fullmatch(r"-?\d{5,20}", t) else None


class GroupFeed:
    def __init__(self, shop):
        self.shop = shop
        self._fail_ts = 0.0

    # ---------- وضعیت ----------
    @property
    def s(self) -> dict:
        return self.shop.s

    def chat_id(self) -> int | None:
        return parse_chat_id(self.s.get("grp_chat_id"))

    def enabled(self) -> bool:
        return self.s.get("grp_enabled") == "1" and self.chat_id() is not None

    def topics(self) -> dict:
        try:
            d = json.loads(self.s.get("grp_topics") or "{}")
            return {k: int(v) for k, v in d.items() if k in TOPICS and str(v).lstrip("-").isdigit()} if isinstance(d, dict) else {}
        except ValueError:
            return {}

    async def _save(self, key, value):
        await self.shop.db.set_setting(key, value)
        await self.shop._reload_settings()

    # ---------- ساخت تاپیک‌ها ----------
    async def ensure_topics(self, force=False) -> dict:
        """تاپیک‌های ناموجود را می‌سازد. خروجی: {"forum": bool, "created": [...], "error": str}"""
        cid = self.chat_id()
        out = {"forum": False, "created": [], "error": ""}
        if cid is None:
            out["error"] = "no_chat"
            return out
        try:
            chat = await self.shop.bot.get_chat(cid)
        except TelegramMigrateToChat as e:
            await self._save("grp_chat_id", str(e.migrate_to_chat_id))
            return await self.ensure_topics(force)
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)[:200]
            return out
        out["forum"] = bool(getattr(chat, "is_forum", False))
        if not out["forum"]:
            return out
        have = {} if force else self.topics()
        for key, title in TOPICS.items():
            if key in have:
                continue
            try:
                t = await self.shop.bot.create_forum_topic(cid, title)
                have[key] = t.message_thread_id
                out["created"].append(key)
            except Exception as e:  # noqa: BLE001
                out["error"] = str(e)[:200]
                break
        await self._save("grp_topics", json.dumps(have))
        return out

    async def connect(self, chat) -> dict:
        """از داخل گروه (فرمان /connect) صدا زده می‌شود."""
        await self.shop.db.set_setting("grp_chat_id", str(chat.id))
        await self.shop.db.set_setting("grp_enabled", "1")
        await self.shop.db.set_setting("grp_topics", "")
        await self.shop._reload_settings()
        res = await self.ensure_topics(force=True)
        res["chat_id"] = chat.id
        return res

    async def disconnect(self):
        await self.shop.db.set_setting("grp_enabled", "0")
        await self.shop._reload_settings()

    # ---------- ارسال ----------
    async def _send(self, topic, fn, *, retry=True):
        cid = self.chat_id()
        tid = self.topics().get(topic)
        try:
            return await fn(cid, tid)
        except TelegramMigrateToChat as e:
            await self._save("grp_chat_id", str(e.migrate_to_chat_id))
            return await self._send(topic, fn, retry=False) if retry else None
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if retry and tid and ("thread" in msg or "topic" in msg):
                have = self.topics()
                have.pop(topic, None)
                await self._save("grp_topics", json.dumps(have))
                await self.ensure_topics()
                return await self._send(topic, fn, retry=False)
            self._warn(f"group send failed: {e}")
        except Exception as e:  # noqa: BLE001
            self._warn(f"group send failed: {e}")
        return None

    def _warn(self, msg):
        if time.time() - self._fail_ts > 600:
            self._fail_ts = time.time()
            log.warning(msg)

    def allowed(self, topic) -> bool:
        if not self.enabled():
            return False
        key = EVENT_SETTING.get(topic)
        return not key or self.s.get(key, "1") == "1"

    async def post(self, topic, text, markup=None) -> bool:
        if not self.allowed(topic):
            return False
        return bool(await self._send(topic, lambda cid, tid: self.shop.bot.send_message(
            cid, text, message_thread_id=tid, reply_markup=markup, disable_notification=topic in ("users", "reports", "backups"))))

    async def post_photo(self, topic, file_id, caption, markup=None) -> bool:
        if not self.allowed(topic):
            return False
        return bool(await self._send(topic, lambda cid, tid: self.shop.bot.send_photo(
            cid, file_id, caption=caption, message_thread_id=tid, reply_markup=markup)))

    async def post_file(self, topic, data: bytes, filename: str, caption: str = "") -> bool:
        if not self.allowed(topic):
            return False
        return bool(await self._send(topic, lambda cid, tid: self.shop.bot.send_document(
            cid, BufferedInputFile(data, filename), caption=caption, message_thread_id=tid, disable_notification=True)))

    # ---------- گزارش ----------
    async def report_text(self, t0: int, t1: int, label: str) -> str:
        from ui import fmt
        db = self.shop.db
        done = await db.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s, COALESCE(SUM(CASE WHEN cost>0 THEN amount-cost ELSE 0 END),0) p, "
                            "COALESCE(SUM(CASE WHEN cost>0 THEN 0 ELSE 1 END),0) nc FROM orders WHERE status='done' AND updated_at>=? AND updated_at<?", (t0, t1))
        kinds = await db.all("SELECT kind, COUNT(*) c, COALESCE(SUM(amount),0) s FROM orders WHERE status='done' AND updated_at>=? AND updated_at<? GROUP BY kind", (t0, t1))
        new_users = (await db.one("SELECT COUNT(*) c FROM users WHERE created_at>=? AND created_at<?", (t0, t1)))["c"]
        pays = await db.all("SELECT method, COUNT(*) c, COALESCE(SUM(amount),0) s FROM payments WHERE status='confirmed' AND created_at>=? AND created_at<? GROUP BY method", (t0, t1))
        topup = await db.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM wallet_tx WHERE amount>0 AND reason LIKE 'topup:%' AND created_at>=? AND created_at<?", (t0, t1))
        refunds = await db.one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM orders WHERE status='refunded' AND updated_at>=? AND updated_at<?", (t0, t1))
        queue = (await db.one("SELECT COUNT(*) c FROM orders WHERE status IN ('manual_pending','failed')"))["c"]
        waiting = (await db.one("SELECT COUNT(*) c FROM payments WHERE status='waiting_admin'"))["c"]
        kfa = {"stars": "⭐ استارز", "premium": "👑 پریمیوم", "gift": "🎁 گیفت"}
        mfa = {"wallet": "کیف پول", "card": "کارت به کارت", "zarinpal": "درگاه بانکی", "ton": "TON", "usdt": "USDT"}
        lines = [f"📊 <b>گزارش {label}</b>", "",
                 f"💰 فروش (سفارش‌های تحویل‌شده): <b>{fmt(done['s'])}</b> تومان در <b>{done['c']}</b> سفارش",
                 f"📈 سود خالص: <b>{fmt(done['p'])}</b> تومان"
                 + (f"\n   <i>({done['nc']} سفارش بدون هزینه‌ی ثبت‌شده در سود حساب نشد)</i>" if done["nc"] else ""),
                 f"👤 کاربر جدید: <b>{new_users}</b>"]
        if kinds:
            lines += ["", "<b>به تفکیک محصول:</b>"] + [f"• {kfa.get(k['kind'], k['kind'])}: {k['c']} سفارش — {fmt(k['s'])} ت" for k in kinds]
        if pays or topup["c"]:
            lines += ["", "<b>پرداخت‌های تأییدشده:</b>"] + [f"• {mfa.get(p['method'], p['method'])}: {p['c']} — {fmt(p['s'])} ت" for p in pays]
            if topup["c"]:
                lines.append(f"• شارژ کیف پول: {topup['c']} بار — {fmt(topup['s'])} ت")
        if refunds["c"]:
            lines.append(f"\n↩️ بازگشت وجه: {refunds['c']} سفارش — {fmt(refunds['s'])} ت")
        lines += ["", f"🕓 صف تحویل/خطا: {queue} | 🧾 رسید منتظر تأیید: {waiting}"]
        return "\n".join(lines)

    async def send_report(self, hours: float | None = None) -> bool:
        """گزارش «امروز» (از ۰۰:۰۰ تهران تا الان) یا اگر hours بدهید N ساعت اخیر."""
        now = datetime.now(TEHRAN)
        if hours:
            t0, label = int(now.timestamp() - hours * 3600), f"{int(hours)} ساعت اخیر"
        else:
            t0, label = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()), f"روز {now.strftime('%Y-%m-%d')}"
        text = await self.report_text(t0, int(now.timestamp()) + 1, label)
        return await self.post("reports", text)

    async def send_backup(self) -> bool:
        import backup as bk
        data = await bk.make_bundle(self.shop, include_env=self.s.get("grp_backup_env") == "1")
        name = f"backup-{datetime.now(TEHRAN).strftime('%Y%m%d-%H%M')}.zip"
        return await self.post_file("backups", data, name, f"💾 بکاپ خودکار — {datetime.now(TEHRAN).strftime('%Y-%m-%d %H:%M')} (تهران)")

    # ---------- زمان‌بندی (هر ۳۰ ثانیه از background_loop) ----------
    async def tick(self):
        if not self.enabled():
            return
        s = self.s
        now = datetime.now(TEHRAN)
        if s.get("grp_report_on") == "1":
            m = HHMM.match(s.get("grp_report_time") or "23:55")
            hh, mm = (int(m.group(1)), int(m.group(2))) if m else (23, 55)
            today = now.strftime("%Y-%m-%d")
            if s.get("grp_last_report") != today and (now.hour, now.minute) >= (hh, mm):
                await self._save("grp_last_report", today)      # اول ثبت می‌کنیم تا در خطا هر ۳۰ ثانیه تکرار نشود
                await self.send_report()
        hrs = int(float(s.get("grp_backup_hours") or 0) or 0)
        if hrs > 0 and time.time() - float(s.get("grp_last_backup") or 0) >= hrs * 3600:
            await self._save("grp_last_backup", str(int(time.time())))
            await self.send_backup()
