"""بکاپ کامل (bundle) و وارد کردن بکاپ (restore) با اعتبارسنجی، بکاپ ایمنی قبل از جایگزینی و تعویض داغ دیتابیس."""
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime

MAX_DB_BYTES = 400 * 1024 * 1024
REQUIRED_TABLES = {"settings", "users", "orders", "products"}
SQLITE_MAGIC = b"SQLite format 3\x00"


def _app_version() -> str:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "?"


def env_path() -> str:
    return os.path.join(os.getcwd(), ".env")


def inspect_db_file(path: str) -> dict:
    """اعتبارسنجی فایل SQLite و گرفتن آمار؛ در صورت مشکل ValueError با پیام."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise ValueError(f"not a SQLite file: {e}")
    try:
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as e:
            raise ValueError(f"database is corrupted: {e}")
        if ok != "ok":
            raise ValueError(f"integrity_check failed: {ok}")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError("this is not a bot database (missing tables: " + ", ".join(sorted(missing)) + ")")
        cnt = lambda t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if t in tables else 0  # noqa: E731
        shop = ""
        try:
            r = con.execute("SELECT value FROM settings WHERE key='shop_name'").fetchone()
            shop = r[0] if r else ""
        except sqlite3.Error:
            pass
        return {"users": cnt("users"), "orders": cnt("orders"), "products": cnt("products"), "payments": cnt("payments"),
                "discounts": cnt("discounts"), "shop_name": shop, "size": os.path.getsize(path)}
    finally:
        con.close()


async def make_bundle(shop, include_env=False) -> bytes:
    """بسته‌ی بکاپ .zip شامل shop.db + meta.json (+ env اختیاری، حاوی توکن؛ نگهداری امن)."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "shop.db")
    try:
        await shop.db.backup_to(path)
        info = inspect_db_file(path)
        meta = {"app": "starshop", "version": _app_version(), "created": datetime.now().isoformat(timespec="seconds"),
                "has_env": bool(include_env), **{k: info[k] for k in ("users", "orders", "products", "shop_name")}}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(path, "shop.db")
            z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
            if include_env and os.path.exists(env_path()):
                with open(env_path(), "rb") as f:
                    z.writestr("env", f.read())
        return buf.getvalue()
    finally:
        try:
            os.remove(path)
            os.rmdir(d)
        except OSError:
            pass


def _pick(names, wanted):
    for n in names:
        if os.path.basename(n) in wanted:
            return n
    return None


def extract_upload(data: bytes):
    """محتوای فایل بارگذاری‌شده را (SQLite خام، .zip یا .tar.gz) تحلیل می‌کند.
    خروجی: (db_bytes, env_text|None, meta|None). هیچ مسیری از داخل آرشیو روی دیسک نوشته نمی‌شود."""
    if data[:16] == SQLITE_MAGIC:
        return data, None, None
    db = env = meta = None
    if data[:4] == b"PK\x03\x04":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("the zip file is damaged")
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        dbn = _pick(names, {"shop.db"}) or next((n for n in names if n.endswith(".db")), None)
        if not dbn:
            raise ValueError("shop.db is not inside the zip")
        if z.getinfo(dbn).file_size > MAX_DB_BYTES:
            raise ValueError("database is too large")
        with z.open(dbn) as zf:           # خواندن محدود: سربرگ zip می‌تواند دروغ بگوید (zip bomb)
            db = zf.read(MAX_DB_BYTES + 1)
        en = _pick(names, {"env", ".env"})
        if en and z.getinfo(en).file_size < 100_000:
            with z.open(en) as zf:
                env = zf.read(100_001).decode("utf-8", "replace")[:100_000]
        mn = _pick(names, {"meta.json"})
        if mn and z.getinfo(mn).file_size < 100_000:
            try:
                with z.open(mn) as zf:
                    meta = json.loads(zf.read(100_001))
            except ValueError:
                meta = None
    elif data[:2] == b"\x1f\x8b":
        try:
            t = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        except tarfile.TarError:
            raise ValueError("the archive is damaged")
        members = [m for m in t.getmembers() if m.isfile()]
        dbm = next((m for m in members if os.path.basename(m.name) == "shop.db"), None) or \
            next((m for m in members if m.name.endswith(".db")), None)
        if not dbm:
            raise ValueError("shop.db is not inside the archive")
        if dbm.size > MAX_DB_BYTES:
            raise ValueError("database is too large")
        db = t.extractfile(dbm).read()
        em = next((m for m in members if os.path.basename(m.name) in ("env", ".env") and m.size < 100_000), None)
        if em:
            env = t.extractfile(em).read().decode("utf-8", "replace")
    else:
        raise ValueError("unsupported file: upload a .db, a .zip bundle or a .tar.gz backup")
    if len(db) > MAX_DB_BYTES:
        raise ValueError("database is too large")
    return db, env, meta


def stage_db(db_bytes: bytes) -> tuple[str, dict]:
    """نوشتن موقت و اعتبارسنجی؛ خروجی (مسیر فایل موقت, آمار)."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="restore-")
    with os.fdopen(fd, "wb") as f:
        f.write(db_bytes)
    try:
        return path, inspect_db_file(path)
    except ValueError:
        os.remove(path)
        raise


async def apply_restore(shop, staged_path: str, env_text: str | None = None) -> dict:
    """جایگزینی دیتابیس فعلی با فایل staged. قبلش از وضعیت فعلی یک بکاپ ایمنی کنار دیتابیس گرفته می‌شود."""
    info = inspect_db_file(staged_path)
    bdir = os.path.join(os.path.dirname(os.path.abspath(shop.db.path)), "backups")
    os.makedirs(bdir, exist_ok=True)
    safety = os.path.join(bdir, f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid() % 1000:03d}{int(time.time() * 1000) % 1000:03d}.db")
    await shop.db.backup_to(safety)
    await shop.db.replace_file(staged_path)
    await shop.reload_after_restore()
    restart = False
    if env_text:
        ep = env_path()
        if os.path.exists(ep):
            try:
                os.replace(ep, ep + f".bak-{int(time.time())}")
            except OSError:
                pass
        # مسیر دیتابیس همین سرور حفظ شود
        lines = [ln for ln in env_text.splitlines() if not ln.startswith("DB_PATH=")]
        lines.append(f"DB_PATH={shop.db.path}")
        with open(ep, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        try:
            os.chmod(ep, 0o600)
        except OSError:
            pass
        restart = True
    try:
        os.remove(staged_path)
    except OSError:
        pass
    return {"info": info, "safety": safety, "restart": restart}
