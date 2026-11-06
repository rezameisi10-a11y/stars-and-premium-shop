"""عملیات پیشرفته‌ی سرویس‌های VPN: انتقال بین پنل‌ها (تغییر لوکیشن) و عملیات گروهی.
جدا از vpn_service.py نگه داشته شده تا آن فایل بزرگ‌تر نشود؛ از طریق shop.vpn.ops در دسترس است."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

from i18n import N_
from ui import esc
from vpn_panels import PanelError

log = logging.getLogger("vpn_ops")
GB = 1024 ** 3


class MoveError(Exception):
    """خطای قابل‌نمایش (پیام فارسی که به کاربر/ادمین گفته می‌شود)."""


# فیلترهای عملیات گروهی (کلید: عنوان)
BULK_FILTERS = {
    "active": "✅ فعال‌ها",
    "soon": "⏳ رو به اتمام زمان (≤ N روز)",
    "lowvol": "📉 کم‌حجم (≤ N٪ باقی)",
    "expired": "⌛️ منقضی‌شده‌ها",
    "disabled": "⏸ غیرفعال‌ها",
    "all": "📡 همه‌ی سرویس‌ها",
}
BULK_ACTIONS = {
    "add_days": "➕ روز", "add_gb": "➕ حجم (GB)", "enable": "▶️ فعال‌سازی", "disable": "⏸ غیرفعال‌سازی",
    "move": "📍 انتقال به پنل دیگر", "msg": "✉️ پیام به صاحب‌ها", "delete": "🗑 حذف",
}


class VpnOps:
    def __init__(self, vs):
        self.vs = vs
        self.db = vs.db
        self._locks: dict[int, asyncio.Lock] = {}
        self._dest: set[int] = set()
        self.jobs: dict[int, dict] = {}            # وضعیت آخرین عملیات گروهی هر ادمین

    # ------------------------------------------------------------------ تغییر لوکیشن
    def enabled(self) -> bool:
        return (self.vs.s.get("vpn_allow_move", "0") or "0") == "1"

    async def targets(self, svc, admin=False) -> list[dict]:
        """پنل‌هایی که این سرویس می‌تواند به آن‌ها منتقل شود (کاربر: فقط پنل‌های «مقصد»؛ ادمین: همه‌ی پنل‌های فعال غیر دستی)."""
        from vpn_service import pflag
        out = []
        for p in await self.db.list_vpn_panels():
            if p["id"] == svc["panel_id"] or not p["enabled"] or p["type"] == "manual":
                continue
            if not admin and not pflag(p, "move_to"):
                continue
            if self.vs.is_down(p["id"]):
                continue
            out.append(p)
        return out

    async def refresh_dest(self):
        """فهرست پنل‌های مقصد را برای نمایش همگام دکمه (service_kb sync است) تازه می‌کند."""
        from vpn_service import pflag
        ids = set()
        for p in await self.db.list_vpn_panels():
            if p["enabled"] and p["type"] != "manual" and pflag(p, "move_to") and not self.vs.is_down(p["id"]):
                ids.add(p["id"])
        self._dest = ids

    def show_button(self, svc) -> bool:
        """نمایش دکمه‌ی «تغییر لوکیشن» روی کارت سرویس (بدون دسترسی به دیتابیس؛ بعد از refresh_dest)."""
        from vpn_service import pflag
        if not self.enabled() or svc["is_test"] or svc["status"] in ("deleted", "pending_manual", "expired"):
            return False
        if not pflag(svc, "move") or self.vs.limit_error(svc, "move"):
            return False
        return bool(self._dest - {svc["panel_id"]})

    async def can_user_move(self, svc) -> bool:
        from vpn_service import pflag
        if not self.enabled() or svc["is_test"] or svc["status"] in ("deleted", "pending_manual"):
            return False
        if not pflag(svc, "move") or self.vs.limit_error(svc, "move"):
            return False
        return bool(await self.targets(svc))

    def preview(self, svc) -> tuple[float, int]:
        """(حجم باقی‌مانده GB (۰=نامحدود)، روز باقی‌مانده (۰=بدون انقضا)) — دقیقاً همان چیزی که روی پنل جدید ساخته می‌شود."""
        if svc["total_bytes"]:
            rest = svc["total_bytes"] - svc["used_bytes"]
            if rest <= 0:
                raise MoveError(N_("حجم این سرویس تمام شده است؛ ابتدا حجم اضافه بخرید یا تمدید کنید."))
            gb = math.ceil(rest / GB * 100) / 100
        else:
            gb = 0.0
        if svc["expires_at"]:
            secs = svc["expires_at"] - time.time()
            if secs <= 0:
                raise MoveError(N_("زمان این سرویس تمام شده است؛ ابتدا تمدید کنید."))
            days = max(1, math.ceil(secs / 86400))
        else:
            days = 0
        return gb, days

    async def move(self, sid: int, target_pid: int, actor: str = "admin", user_id: int | None = None) -> dict:
        """سرویس را با حجم و زمانِ باقی‌مانده به پنل دیگر می‌برد (نه حجم/زمان تازه). خروجی: ردیف جدید سرویس. خطا: MoveError."""
        vs = self.vs
        lock = self._locks.setdefault(sid, asyncio.Lock())
        if lock.locked():
            raise MoveError(N_("انتقال همین سرویس در حال انجام است."))
        async with lock:
            svc = await self.db.get_vpn_service(sid)
            if not svc or svc["status"] in ("deleted", "pending_manual") or (user_id is not None and svc["user_id"] != user_id):
                raise MoveError(N_("سرویس پیدا نشد."))
            if user_id is not None and not await self.can_user_move(svc):
                raise MoveError(N_("تغییر لوکیشن برای این سرویس فعال نیست."))
            dst = next((p for p in await self.targets(svc, admin=user_id is None) if p["id"] == int(target_pid)), None)
            if not dst:
                raise MoveError(N_("این لوکیشن در دسترس نیست."))
            src_drv = await vs.driver(svc["panel_id"])
            if getattr(src_drv, "manual", False):
                raise MoveError(N_("این سرویس روی پنل دستی است و قابل انتقال خودکار نیست."))
            try:
                svc = await vs.sync(svc)                      # آخرین مصرف/زمان از پنل مبدأ
            except Exception as e:  # noqa: BLE001
                raise MoveError(N_("ارتباط با پنل فعلی ممکن نشد؛ کمی بعد دوباره تلاش کنید.")) from e
            if svc["status"] not in ("active", "limited", "on_hold", "disabled", "expired"):
                raise MoveError(N_("سرویس پیدا نشد."))
            if user_id is not None and svc["status"] in ("disabled", "expired", "limited"):
                raise MoveError(N_("فقط سرویس فعال قابل انتقال است."))
            gb, days = self.preview(svc)
            dst_drv = await vs.driver(dst["id"])
            old_meta = json.loads(svc["meta"] or "{}")
            plan = await self.db.get_vpn_plan(svc["plan_id"]) if svc.get("plan_id") else None
            try:
                name = await vs.make_name(dst["id"], svc["user_id"], svc["name"])
            except Exception:  # noqa: BLE001
                name = await vs.make_name(dst["id"], svc["user_id"])
            try:
                u, meta = await dst_drv.create(name, days, gb, note=f"tg:{svc['user_id']} moved:#{sid}", tg_id=svc["user_id"],
                                               limit_ip=(plan or {}).get("ip_limit") or 0)
            except PanelError as e:
                raise MoveError(f"{N_('ساخت روی پنل جدید ناموفق بود')}: {str(e)[:160]}") from e
            new_status = u.status
            if svc["status"] == "disabled":                    # سرویس خاموش بعد از انتقال هم خاموش بماند
                try:
                    await dst_drv.set_enabled(name, meta or {}, False)
                    new_status = "disabled"
                except Exception:  # noqa: BLE001
                    log.warning("move: could not keep disabled state on target")
            new_meta = dict(meta or {})
            for k, v in old_meta.items():
                if k.startswith("n_") or k in ("applied", "unote", "transfers", "report_at", "toggle_at", "move_at"):
                    new_meta[k] = v
            new_meta["n_move"] = int(old_meta.get("n_move", 0)) + 1
            new_meta["move_at"] = int(time.time())
            new_meta["moved_from"] = svc["panel_id"]
            old_panel_id, old_name = svc["panel_id"], svc["name"]
            try:
                await self.db.update_vpn_service(sid, panel_id=dst["id"], name=name, status=new_status, expires_at=u.expire,
                                                 total_bytes=u.total or int(gb * GB), used_bytes=u.used, sub_url=u.sub_url,
                                                 links=u.links or [], meta=new_meta, last_sync=int(time.time()), notified="")
            except Exception:
                log.exception("move: db update failed; rolling back new panel user %s", name)
                try:
                    await dst_drv.delete(name, meta or {})
                except Exception:  # noqa: BLE001
                    log.exception("move rollback failed")
                raise MoveError(N_("ثبت انتقال ناموفق بود؛ سرویس قبلی دست‌نخورده ماند.")) from None
            try:                                              # پاک‌کردن سرویس قدیمی از پنل مبدأ
                await src_drv.delete(old_name, old_meta)
            except Exception as e:  # noqa: BLE001
                log.warning("move: old delete failed: %s", e)
                await vs.shop.notify_admins(f"⚠️ انتقال سرویس #{sid} انجام شد ولی حذف نسخه‌ی قدیمی <code>{esc(old_name)}</code> از پنل مبدأ (#{old_panel_id}) ناموفق بود؛ دستی حذفش کنید.\n{esc(str(e)[:150])}",
                                            perm="vpn", topic="alerts")
            await self.db.log(actor, "vpn_move", f"#{sid} {old_panel_id}->{dst['id']}")
            fresh = await self.db.get_vpn_service(sid)
            return fresh

    async def user_move(self, uid: int, sid: int, target_pid: int):
        row = await self.move(sid, target_pid, actor=f"user:{uid}", user_id=uid)
        await self.vs.shop.audit(f"user:{uid}", f"📍 کاربر <code>{uid}</code> سرویس <code>{esc(row['name'])}</code> را به پنل «{esc(row.get('panel_name') or row['panel_id'])}» منتقل کرد")
        return row

    # ------------------------------------------------------------------ عملیات گروهی
    async def select(self, panel_id, flt: str, n: int = 3, limit: int = 5000) -> list[dict]:
        """سرویس‌های زنده (غیرحذف‌شده، غیر دستیِ در انتظار) مطابق فیلتر. panel_id=None → همه‌ی پنل‌ها."""
        now = int(time.time())
        w = ["s.status NOT IN ('deleted','pending_manual')"]
        a: list = []
        if panel_id:
            w.append("s.panel_id=?")
            a.append(int(panel_id))
        if flt == "active":
            w.append("s.status='active'")
        elif flt == "soon":
            w.append("s.status='active' AND s.expires_at>? AND s.expires_at<=?")
            a += [now, now + max(n, 1) * 86400]
        elif flt == "lowvol":
            w.append("s.status='active' AND s.total_bytes>0 AND (s.total_bytes-s.used_bytes)*100 <= ?*s.total_bytes")
            a.append(max(n, 1))
        elif flt == "expired":
            w.append("(s.status='expired' OR (s.expires_at>0 AND s.expires_at<=?))")
            a.append(now)
        elif flt == "disabled":
            w.append("s.status='disabled'")
        elif flt != "all":
            return []
        return await self.db.all("SELECT s.*, p.name AS panel_name, p.type AS panel_type FROM vpn_services s LEFT JOIN vpn_panels p ON p.id=s.panel_id "
                                 f"WHERE {' AND '.join(w)} ORDER BY s.id LIMIT ?", (*a, limit))

    async def bulk_run(self, rows: list[dict], act: str, value=None, target_pid=None, actor="admin", progress=None) -> dict:
        """اجرای یک عملیات روی فهرست سرویس‌ها؛ خطای یک سرویس بقیه را متوقف نمی‌کند. خروجی: {ok, fail, errors:[...]}"""
        vs = self.vs
        res = {"ok": 0, "fail": 0, "errors": []}
        for i, svc in enumerate(rows, 1):
            try:
                if act in ("add_days", "add_gb", "enable", "disable"):
                    ok, msg = await vs.admin_action(svc["id"], act, value, actor=actor)
                elif act == "delete":
                    ok, msg = await vs.admin_delete(svc["id"], actor=actor)
                elif act == "move":
                    await self.move(svc["id"], int(target_pid), actor=actor)
                    ok, msg = True, ""
                    row = await self.db.get_vpn_service(svc["id"])
                    await vs.shop.safe_send(row["user_id"], f"📍 سرویس <code>{esc(row['name'])}</code> به لوکیشن جدید منتقل شد؛ حجم و زمان باقی‌مانده‌ی شما حفظ شده است. لینک اشتراک جدید:")
                    await vs.send_service(row["user_id"], row)
                elif act == "msg":
                    await vs.shop.safe_send(svc["user_id"], str(value))
                    ok, msg = True, ""
                else:
                    ok, msg = False, "عملیات نامعتبر"
            except MoveError as e:
                ok, msg = False, str(e)
            except Exception as e:  # noqa: BLE001
                log.exception("bulk %s on #%s failed", act, svc["id"])
                ok, msg = False, str(e)[:120]
            if ok:
                res["ok"] += 1
            else:
                res["fail"] += 1
                if len(res["errors"]) < 8:
                    res["errors"].append(f"#{svc['id']} {svc['name']}: {msg}")
            if progress and i % 20 == 0:
                await progress(i, len(rows))
            await asyncio.sleep(0.25)                          # فشار آرام به پنل‌ها و محدودیت تلگرام
        return res
