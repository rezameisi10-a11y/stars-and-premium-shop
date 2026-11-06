"""تأیید خودکار رسید کارت‌به‌کارت با بازبینی بعدی ادمین.

جریان: رسید که رسید و شرایط ایمن بود → پرداخت بلافاصله تأیید و سرویس تحویل می‌شود (rc_state='review').
ادمین بعداً هر رسید را «واقعی» (rc_state='verified') یا «جعلی» (rc_state='fake') می‌کند؛
جعلی = پرداخت ردشده، سرویس از پنل و ربات حذف، یا (شارژ کیف پول) کسر موجودی باقی‌مانده.
فقط خرید «جدید» VPN و (اختیاری) شارژ کیف پول خودکار می‌شوند؛ تمدید، حجم اضافه، استارز، پریمیوم و گیفت همیشه دستی‌اند
چون برگشت‌پذیر نیستند.
"""
from __future__ import annotations

import logging

from i18n import N_, NW_
from ui import btn, ikb

log = logging.getLogger("receipt_auto")


def _int(v, d=0):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return d


def review_kb(pid):
    return ikb([[btn("✅ واقعی بود", f"padm:rv:{pid}:ok", role="confirm"), btn("🚫 جعلی بود (قطع سرویس)", f"padm:rv:{pid}:no", role="reject")]])


class ReceiptAuto:
    def __init__(self, shop):
        self.shop = shop

    async def eligible(self, p) -> tuple[bool, str]:
        shop, db = self.shop, self.shop.db
        s = await shop.settings()
        if s.get("rc_auto_on") != "1":
            return False, "off"
        if p["method"] != "card" or not p.get("receipt_uid"):
            return False, "no_uid"
        u = await db.get_user(p["user_id"]) or {}
        if u.get("banned"):
            return False, "banned"
        mx = _int(s.get("rc_auto_max"), 0)
        if mx > 0 and p["amount"] > mx:
            return False, "amount"
        if p["order_id"] is None:
            if s.get("rc_auto_wallet") != "1":
                return False, "wallet_off"
        else:
            o = await db.get_order(p["order_id"])
            if not o or o["kind"] != "vpn" or not (o.get("ref") or "").startswith("vpn:new:") or o["status"] != "pending_payment":
                return False, "kind"
        st = await db.user_receipt_stats(p["user_id"])
        if st["rejected"] > 0:
            return False, "rejected_before"
        if st["orders"] < _int(s.get("rc_auto_min_orders"), 0):
            return False, "new_user"
        if await db.rc_open_count(p["user_id"]) >= max(_int(s.get("rc_auto_open_max"), 1), 1):
            return False, "open_limit"
        if await db.rc_uid_used(p["receipt_uid"], p["id"]):
            return False, "dup"
        return True, ""

    async def try_auto(self, p) -> bool:
        """True = رسید خودکار تأیید شد (دیگر نیازی به ارسال عادی برای ادمین نیست)."""
        try:
            ok, why = await self.eligible(p)
        except Exception as e:  # noqa: BLE001  خطا هرگز نباید رسید را گم کند
            log.warning("rc eligible failed: %s", e)
            return False
        if not ok:
            if why == "dup":
                await self.shop.notify_admins(f"♻️ رسید P{p['id']} عکس تکراری است (قبلاً در پرداخت دیگری فرستاده شده)؛ دستی بررسی کنید.", perm="receipts", topic="alerts")
            return False
        await self.shop.db.update_payment(p["id"], rc_state="review")
        if not await self.shop.confirm_payment(p["id"], ref=f"auto:{p['id']}"):
            await self.shop.db.update_payment(p["id"], rc_state=None)
            return False
        await self.shop.db.log("system", "receipt_auto", f"P{p['id']}")
        p2 = await self.shop.db.get_payment(p["id"])
        await self.send_review(None, p2, announce=True)
        return True

    async def caption(self, p) -> str:
        cap = await self.shop.receipt_caption(p)
        return ("⚡ <b>خودکار تأیید شد، منتظر بازبینی شما</b>\n" + cap)[:1020]

    async def send_review(self, chat_id, p, announce=False):
        shop = self.shop
        cap, kb = await self.caption(p), review_kb(p["id"])
        if chat_id:
            try:
                await shop.bot.send_photo(chat_id, p["receipt_file_id"], caption=cap, reply_markup=kb)
            except Exception as e:  # noqa: BLE001
                log.warning("review send failed: %s", e)
            return
        if announce:
            for aid in shop.admins_with("receipts"):
                try:
                    await shop.bot.send_photo(aid, p["receipt_file_id"], caption=cap, reply_markup=kb)
                except Exception as e:  # noqa: BLE001
                    log.warning("review send failed: %s", e)
            if shop.group.allowed("receipts"):
                await shop.group.post_photo("receipts", p["receipt_file_id"], cap, kb)

    async def review(self, pid, verdict, actor="admin") -> str:
        shop, db = self.shop, self.shop.db
        p = await db.get_payment(pid)
        if not p or p.get("rc_state") != "review":
            return NW_("این رسید قبلاً بازبینی شده است.")
        if verdict == "ok":
            if await db.rc_move(pid, "review", "verified"):
                await db.log(actor, "receipt_verified", f"P{pid}")
                await shop.audit(actor, f"✅ رسید خودکار <b>P{pid}</b> را <b>واقعی</b> تأیید کرد")
                return NW_("✅ واقعی ثبت شد.")
            return NW_("این رسید قبلاً بازبینی شده است.")
        if not await db.rc_move(pid, "review", "fake"):
            return NW_("این رسید قبلاً بازبینی شده است.")
        await db.update_payment(pid, status="rejected", reject_reason="fake")
        notes = await self._revoke(p)
        await db.log(actor, "receipt_fake", f"P{pid} {notes}"[:300])
        await shop.audit(actor, f"🚫 رسید خودکار <b>P{pid}</b> را <b>جعلی</b> اعلام کرد\n{notes}")
        await shop.say(p["user_id"], "❌ رسید پرداخت شما (کد P{pid}) پس از بررسی تأیید نشد و خدمات مربوط به آن قطع شد. در صورت اشتباه با پشتیبانی تماس بگیرید.", pid=pid)
        s = await shop.settings()
        if s.get("rc_auto_ban") == "1":
            await db.set_banned(p["user_id"], True, f"رسید جعلی P{pid}")
            notes += " | کاربر مسدود شد"
        return NW_("🚫 جعلی ثبت شد.") + " " + notes

    async def _revoke(self, p) -> str:
        shop, db = self.shop, self.shop.db
        if p["order_id"] is None:
            u = await db.get_user(p["user_id"]) or {}
            take = min(int(u.get("balance") or 0), p["amount"])
            if take > 0 and await db.deduct_balance(p["user_id"], take, f"fake receipt P{p['id']}"):
                short = p["amount"] - take
                return f"کیف پول: {take:,} تومان کسر شد" + (f" (کسری {short:,} تومان؛ کاربر خرج کرده بود)" if short else "")
            return f"کیف پول: چیزی برای کسر نبود (کسری {p['amount']:,} تومان)"
        oid = p["order_id"]
        out = []
        svc = await db.vpn_service_by_order(oid)
        if svc and svc["status"] != "deleted":
            ok, msg = await shop.vpn.admin_delete(svc["id"], refund=False, actor="receipt_fake")
            if ok:
                out.append(f"سرویس #{svc['id']} از پنل و ربات حذف شد")
            else:
                try:
                    await shop.vpn.admin_action(svc["id"], "disable", None, "receipt_fake")
                except Exception as e:  # noqa: BLE001
                    log.warning("disable failed: %s", e)
                await db.update_vpn_service(svc["id"], status="disabled")
                out.append(f"⚠️ حذف از پنل نشد ({msg}); سرویس #{svc['id']} غیرفعال شد، دستی از پنل حذفش کنید")
        elif not svc:
            out.append("سرویسی برای این سفارش ساخته نشده بود")
        await db.transition(oid, ["paid", "delivering", "done", "manual_pending", "failed"], "cancelled", "رسید جعلی")
        return " | ".join(out)
