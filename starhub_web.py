#!/usr/bin/env python3
"""StarHub web dashboard: a small, strict aiohttp front end for the `starhub` CLI.

It never re-implements manager logic: every action shells out to the CLI with an
argument list (no shell).  Binds to 127.0.0.1 by default; put Caddy/HTTPS or an
SSH tunnel in front of it.

  python starhub_web.py                    run the server (same as `serve`)
  python starhub_web.py --set-password     store the admin password (hashed)
Env: STARHUB_BASE, STARHUB_CLI (or STARHUB_CMD), STARHUB_WEB_HOST, STARHUB_WEB_PORT,
     STARHUB_WEB_SECURE=1 (https), STARHUB_WEB_TRUST_PROXY=1, STARHUB_WEB_PASSWORD.
"""
import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import signal
import sys
import time
from dataclasses import dataclass, field

from aiohttp import web

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
TOKEN_RE = re.compile(r"^[0-9]{6,}:[A-Za-z0-9_-]{30,}$")
ADMINS_RE = re.compile(r"^[0-9]+(,[0-9]+)*$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
BACKUP_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.tar\.gz$")
TOKEN_ANY_RE = re.compile(r"\b(\d{6,}):[A-Za-z0-9_-]{30,}")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

PBKDF2_ITER = 300_000
SESSION_TTL = 12 * 3600
MAX_UPLOAD = 500 * 1024 * 1024
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 600
esc = html.escape


# ----------------------------------------------------------------- config
@dataclass
class Config:
    base: str = field(default_factory=lambda: os.environ.get("STARHUB_BASE", "/opt/starhub"))
    cli: str = field(default_factory=lambda: os.environ.get("STARHUB_CLI") or os.environ.get("STARHUB_CMD") or "/usr/local/bin/starhub")
    secure: bool = field(default_factory=lambda: os.environ.get("STARHUB_WEB_SECURE") == "1")
    trust_proxy: bool = field(default_factory=lambda: os.environ.get("STARHUB_WEB_TRUST_PROXY") == "1")
    fail_delay: float = 0.4
    max_upload: int = MAX_UPLOAD

    @property
    def webdir(self): return os.path.join(self.base, "web")
    @property
    def auth_file(self): return os.path.join(self.webdir, "auth.json")
    @property
    def secret_file(self): return os.path.join(self.webdir, "secret")
    @property
    def tmpdir(self): return os.path.join(self.webdir, "tmp")
    @property
    def backups(self): return os.path.join(self.base, "backups")


def ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def write_private(path, data: bytes):
    tmp = f"{path}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


# ----------------------------------------------------------------- password / secret
def hash_password(password: str, salt: bytes | None = None, iters: int = PBKDF2_ITER) -> dict:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    return {"algo": "pbkdf2_sha256", "iter": iters, "salt": salt.hex(), "hash": dk.hex()}


def verify_password(password: str, rec: dict) -> bool:
    try:
        iters = int(rec["iter"])
        if iters < 100_000:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(rec["salt"]), iters)
        return hmac.compare_digest(dk.hex(), rec["hash"])
    except (KeyError, ValueError, TypeError):
        return False


def load_auth(cfg: Config):
    try:
        with open(cfg.auth_file, encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) and "hash" in rec else None
    except (OSError, ValueError):
        return None


def set_password(cfg: Config, password: str):
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    ensure_dir(cfg.webdir)
    write_private(cfg.auth_file, json.dumps(hash_password(password)).encode())


def load_secret(cfg: Config) -> bytes:
    ensure_dir(cfg.webdir)
    try:
        with open(cfg.secret_file, "rb") as f:
            s = f.read().strip()
        if len(s) >= 32:
            return s
    except OSError:
        pass
    s = secrets.token_hex(32).encode()
    write_private(cfg.secret_file, s)
    return s


# ----------------------------------------------------------------- sessions / csrf
def _sign(secret: bytes, msg: str) -> str:
    return hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()


def make_session(secret: bytes, auth_rec: dict) -> tuple[str, str]:
    sid = secrets.token_hex(12)
    exp = int(time.time()) + SESSION_TTL
    av = auth_rec["hash"][:12]          # a password change invalidates old sessions
    payload = f"{sid}.{exp}.{av}"
    return sid, f"{payload}.{_sign(secret, payload)}"


def parse_session(secret: bytes, auth_rec: dict | None, cookie: str | None) -> str | None:
    if not cookie or not auth_rec:
        return None
    try:
        payload, sig = cookie.rsplit(".", 1)
        sid, exp, av = payload.split(".")
        if not hmac.compare_digest(sig, _sign(secret, payload)):
            return None
        if int(exp) < time.time() or not hmac.compare_digest(av, auth_rec["hash"][:12]):
            return None
        return sid
    except (ValueError, KeyError):
        return None


def csrf_for(secret: bytes, sid: str) -> str:
    return _sign(secret, "csrf:" + sid)


def login_token(secret: bytes) -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_sign(secret, 'login:' + ts)}"


def login_token_ok(secret: bytes, tok: str) -> bool:
    try:
        ts, sig = tok.split(".", 1)
        return hmac.compare_digest(sig, _sign(secret, "login:" + ts)) and 0 <= time.time() - int(ts) < 3600
    except ValueError:
        return False


# ----------------------------------------------------------------- CLI runner
def clean_output(text: str, secrets_: tuple = ()) -> str:
    text = ANSI_RE.sub("", text)
    for s in secrets_:
        if s:
            text = text.replace(s, "<token hidden>")
    return TOKEN_ANY_RE.sub(lambda m: m.group(1) + ":<hidden>", text)


def cli_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if (k.startswith("STARHUB_") and not k.startswith("STARHUB_WEB_")) or k in ("PATH", "HOME", "LANG", "LC_ALL", "SKIP_TOKEN_CHECK", "TMPDIR")}
    env.update(NONINTERACTIVE="1", YES="1", STARHUB_FROM_WEB="1", TERM="dumb")
    env.update(extra or {})
    return env


async def run_cli(app, args: list[str], *, mutating=False, timeout=30, extra_env=None, hide=()) -> tuple[int, str]:
    cfg: Config = app["cfg"]

    async def _go():
        try:
            p = await asyncio.create_subprocess_exec(
                cfg.cli, *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env=cli_env(extra_env), start_new_session=True)
        except OSError as ex:
            return 127, f"Cannot run the starhub CLI ({cfg.cli}): {ex}"
        try:
            out, _ = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            await p.wait()
            return 124, f"Timed out after {timeout}s."
        return p.returncode, out.decode("utf-8", "replace")

    if mutating:
        async with app["lock"]:
            code, out = await _go()
    else:
        code, out = await _go()
    out = clean_output(out, hide)
    if len(out) > 20000:
        out = "...(truncated)\n" + out[-20000:]
    return code, out.strip()


# ----------------------------------------------------------------- parsing CLI output
def parse_list(out: str) -> list[dict]:
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] == "NAME" or not NAME_RE.match(parts[0]):
            continue
        r = {"name": parts[0], "state": parts[1], "port": "", "domain": "", "version": "", "bot": ""}
        rest = parts[2:]
        if rest and rest[0].isdigit():
            r["port"] = rest.pop(0)
        if rest and DOMAIN_RE.match(rest[0]):
            r["domain"] = rest.pop(0)
        if rest and re.match(r"^\d[0-9A-Za-z._+-]*$", rest[0]):
            r["version"] = rest.pop(0)
        if rest:
            r["bot"] = " ".join(rest)
        rows.append(r)
    return rows


def list_backups(cfg: Config) -> list[dict]:
    root = os.path.realpath(cfg.backups)
    res = []
    try:
        groups = sorted(os.listdir(root))
    except OSError:
        return res
    for g in groups:
        if not (NAME_RE.match(g) or g == "_deleted"):
            continue
        try:
            files = os.listdir(os.path.join(root, g))
        except OSError:
            continue
        for fn in files:
            p = os.path.join(root, g, fn)
            if BACKUP_FILE_RE.match(fn) and os.path.isfile(p) and not os.path.islink(p):
                st = os.stat(p)
                res.append({"id": f"{g}/{fn}", "group": g, "file": fn, "size": st.st_size, "mtime": st.st_mtime})
    res.sort(key=lambda r: r["mtime"], reverse=True)
    return res


def resolve_backup(cfg: Config, bid: str) -> str | None:
    """'group/file.tar.gz' -> real path inside the backups dir, or None."""
    if not isinstance(bid, str) or bid.count("/") != 1:
        return None
    g, fn = bid.split("/")
    if not (NAME_RE.match(g) or g == "_deleted") or not BACKUP_FILE_RE.match(fn) or fn.startswith("."):
        return None
    root = os.path.realpath(cfg.backups)
    p = os.path.realpath(os.path.join(root, g, fn))
    if os.path.dirname(os.path.dirname(p)) != root or not os.path.isfile(p):
        return None
    return p


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


# ----------------------------------------------------------------- HTML
CSS = """
:root{--bg:#f5f6f8;--card:#fff;--tx:#1d232c;--mut:#667085;--bd:#dfe3ea;--pri:#2563eb;--prit:#fff;--ok:#15803d;--bad:#b91c1c;--warn:#b45309;--code:#eef1f6}
@media (prefers-color-scheme:dark){:root{--bg:#0e1218;--card:#171d26;--tx:#e6eaf0;--mut:#93a0b4;--bd:#2a3342;--pri:#3b82f6;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--code:#0b0f15}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.w{max-width:1080px;margin:0 auto;padding:16px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
h1{font-size:20px;margin:0}h1 small{display:block;font-size:12px;color:var(--mut);font-weight:400}h2{font-size:16px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px;margin-bottom:14px}
table{width:100%;border-collapse:collapse}th,td{padding:7px 8px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:middle}
th{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}.tw{overflow-x:auto}
.btn{display:inline-block;font:inherit;font-size:13px;padding:5px 11px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--tx);cursor:pointer;text-decoration:none}
.btn:hover{border-color:var(--pri)}.btn.p{background:var(--pri);border-color:var(--pri);color:var(--prit)}.btn.d{color:var(--bad);border-color:var(--bad)}
input,select{font:inherit;padding:7px 9px;border:1px solid var(--bd);border-radius:8px;background:var(--bg);color:var(--tx);max-width:100%}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:10px}.grid label{display:block;font-size:12px;color:var(--mut)}.grid input,.grid select{width:100%}
form.i{display:inline}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.st{font-weight:600}.st.active{color:var(--ok)}.st.failed,.st.inactive{color:var(--bad)}.st.other{color:var(--warn)}
pre{background:var(--code);border:1px solid var(--bd);border-radius:8px;padding:10px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:13px;max-height:60vh}
.mut{color:var(--mut);font-size:13px}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}code{background:var(--code);padding:1px 5px;border-radius:5px}
.login{max-width:360px;margin:12vh auto}.login input{width:100%;margin-bottom:10px}
"""

HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def layout(title: str, body: str, csrf: str | None = None, status=200) -> web.Response:
    nav = ""
    if csrf:
        nav = (f"<form class='i' method='post' action='/logout'><input type='hidden' name='csrf' value='{esc(csrf)}'>"
               "<button class='btn'>Log out</button></form>")
    doc = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<meta name='color-scheme' content='light dark'><title>{esc(title)} - StarHub</title><style>{CSS}</style></head><body><div class='w'>"
           f"<header><h1><a href='/' style='color:inherit;text-decoration:none'>StarHub</a><small>Customer dashboard &middot; پنل مدیریت مشتری‌ها</small></h1>{nav}</header>"
           f"{body}</div></body></html>")
    return web.Response(text=doc, content_type="text/html", status=status)


def hidden(csrf: str, **kw) -> str:
    return f"<input type='hidden' name='csrf' value='{esc(csrf)}'>" + "".join(
        f"<input type='hidden' name='{esc(k)}' value='{esc(str(v))}'>" for k, v in kw.items())


def result_page(request, title: str, code: int, out: str, status=None) -> web.Response:
    ok = code == 0
    cls, label = ("ok", "Done") if ok else ("bad", f"Failed (exit {code})")
    body = (f"<div class='card'><h2>{esc(title)} <span class='{cls}'>- {label}</span></h2><pre>{esc(out or '(no output)')}</pre>"
            f"<a class='btn p' href='/'>Back to dashboard</a></div>")
    return layout(title, body, request["csrf"], status or (200 if ok else 500))


def error_page(request, msg: str, status=400) -> web.Response:
    return layout("Error", f"<div class='card'><h2 class='bad'>Rejected</h2><p>{esc(msg)}</p><a class='btn' href='/'>Back</a></div>",
                  request.get("csrf"), status)


# ----------------------------------------------------------------- middleware
def client_ip(request) -> str:
    if request.app["cfg"].trust_proxy:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[-1].strip()[:64] or "?"
    return request.remote or "?"


@web.middleware
async def auth_mw(request: web.Request, handler):
    app = request.app
    if request.path == "/login":
        return await handler(request)
    auth = load_auth(app["cfg"])
    sid = parse_session(app["secret"], auth, request.cookies.get("sh_session"))
    if not sid:
        raise web.HTTPSeeOther("/login")
    request["sid"] = sid
    request["csrf"] = csrf_for(app["secret"], sid)
    return await handler(request)


async def add_headers(request, response):
    for k, v in HEADERS.items():
        response.headers[k] = v


async def read_form(request) -> dict:
    """Urlencoded POST body with CSRF check."""
    if (request.content_length or 0) > 65536:
        raise web.HTTPRequestEntityTooLarge(max_size=65536, actual_size=request.content_length or 0)
    data = await request.post()
    form = {k: v for k, v in data.items() if isinstance(v, str)}
    if not hmac.compare_digest(form.get("csrf", ""), request["csrf"]):
        raise web.HTTPForbidden(text="Bad or missing CSRF token")
    return form


# ----------------------------------------------------------------- handlers
async def login_page(request):
    app = request.app
    secret = app["secret"]
    if request.method == "GET":
        return layout_login("")
    ip = client_ip(request)
    limiter = app["fails"]
    now = time.time()
    recent = [t for t in limiter.get(ip, []) if now - t < LOGIN_WINDOW]
    limiter[ip] = recent
    if len(limiter) > 5000:
        for k in [k for k, v in limiter.items() if not v or now - v[-1] > LOGIN_WINDOW]:
            limiter.pop(k, None)
    if len(recent) >= LOGIN_MAX_FAILS:
        resp = layout_login("Too many failed attempts. Try again later.", status=429)
        resp.headers["Retry-After"] = str(LOGIN_WINDOW)
        return resp
    if (request.content_length or 0) > 4096:
        raise web.HTTPRequestEntityTooLarge(max_size=4096, actual_size=request.content_length or 0)
    form = await request.post()
    pw = form.get("password", "")
    tok = form.get("token", "")
    auth = load_auth(app["cfg"])
    if auth is None:
        return layout_login("No admin password is set. Run: starhub web password", status=503)
    good = isinstance(pw, str) and verify_password(pw, auth)
    if not (isinstance(tok, str) and login_token_ok(secret, tok)):
        good = False
    if not good:
        limiter.setdefault(ip, []).append(time.time())
        await asyncio.sleep(app["cfg"].fail_delay)       # small global delay on failures
        return layout_login("Wrong password.", status=401)
    sid, cookie = make_session(secret, auth)
    resp = web.HTTPSeeOther("/")
    resp.set_cookie("sh_session", cookie, max_age=SESSION_TTL, httponly=True, samesite="Strict",
                    secure=app["cfg"].secure, path="/")
    raise resp


def layout_login(msg: str, status=200):
    # token is bound to the secret; filled in by the caller via middleware-less access
    return _LOGIN_STUB(msg, status)


_LOGIN_STUB = None  # set in create_app (needs the secret)


async def logout(request):
    await read_form(request)
    resp = web.HTTPSeeOther("/login")
    resp.del_cookie("sh_session", path="/")
    raise resp


def state_class(s: str) -> str:
    return s if s in ("active", "failed", "inactive") else "other"


async def dashboard(request):
    app = request.app
    csrf = request["csrf"]
    cfg = app["cfg"]
    (c1, out_list), (c2, out_cron) = await asyncio.gather(
        run_cli(app, ["list"]), run_cli(app, ["cron"]))
    rows = parse_list(out_list)
    cron_on = "Automatic backups: ON" in out_cron
    trs = ""
    for r in rows:
        n = r["name"]
        btn = lambda act, label, cls="": (  # noqa: E731
            f"<form class='i' method='post' action='/action'>{hidden(csrf, name=n, action=act)}<button class='btn {cls}'>{label}</button></form>")
        trs += (f"<tr><td><b>{esc(n)}</b></td><td class='st {state_class(r['state'])}'>{esc(r['state'])}</td><td>{esc(r['port'])}</td>"
                f"<td>{esc(r['domain']) or '<span class=mut>-</span>'}</td><td>{esc(r['version'])}</td><td>{esc(r['bot'])}</td>"
                f"<td class='row'>{btn('start', 'Start')}{btn('stop', 'Stop')}{btn('restart', 'Restart')}{btn('backup', 'Backup')}{btn('update', 'Update')}"
                f"<a class='btn' href='/logs?name={esc(n)}&amp;n=100'>Logs</a><a class='btn d' href='/delete?name={esc(n)}'>Delete</a></td></tr>")
    if not rows:
        trs = "<tr><td colspan='7' class='mut'>No customers yet.</td></tr>"
    cust = (f"<div class='card'><h2>Customers</h2><div class='tw'><table><thead><tr><th>Name</th><th>State</th><th>Port</th><th>Domain</th>"
            f"<th>Version</th><th>Bot</th><th>Actions</th></tr></thead><tbody>{trs}</tbody></table></div>"
            f"<div class='row' style='margin-top:10px'>"
            f"<form class='i' method='post' action='/action'>{hidden(csrf, name='all', action='backup')}<button class='btn'>Backup all</button></form>"
            f"<form class='i' method='post' action='/action'>{hidden(csrf, name='all', action='update')}<button class='btn'>Update all</button></form>"
            f"<a class='btn' href='/doctor'>Doctor</a></div></div>")
    create = (f"<div class='card'><h2>New customer</h2><form method='post' action='/create'>{hidden(csrf)}<div class='grid'>"
              "<div><label>Name (a-z, 0-9, _ -)</label><input name='name' required maxlength='31' pattern='[a-z][a-z0-9_\\-]{1,30}'></div>"
              "<div><label>Bot token</label><input name='token' required autocomplete='off' spellcheck='false'></div>"
              "<div><label>Admin Telegram IDs (comma-separated)</label><input name='admins' required pattern='[0-9]+(,[0-9]+)*'></div>"
              "<div><label>Domain (optional)</label><input name='domain' placeholder='shop.example.com'></div></div>"
              "<button class='btn p'>Create customer</button></form></div>")
    bks = list_backups(cfg)
    names = [r["name"] for r in rows]
    brows = ""
    for b in bks[:200]:
        brows += (f"<tr><td>{esc(b['group'])}</td><td><code>{esc(b['file'])}</code></td><td>{human(b['size'])}</td>"
                  f"<td>{time.strftime('%Y-%m-%d %H:%M', time.localtime(b['mtime']))}</td>"
                  f"<td><a class='btn' href='/download?f={esc(b['id'])}'>Download</a></td></tr>")
    if not brows:
        brows = "<tr><td colspan='5' class='mut'>No backups yet.</td></tr>"
    opts = "".join(f"<option value='{esc(b['id'])}'>{esc(b['id'])} ({human(b['size'])})</option>" for b in bks[:200])
    datalist = "".join(f"<option value='{esc(n)}'>" for n in names)
    restore = (f"<div class='card'><h2>Restore</h2><p class='mut'>Existing customer = its data is replaced (a safety backup is taken first; "
               f"token/domain/admins are kept). New name = a new customer is created from the backup.</p>"
               f"<datalist id='cn'>{datalist}</datalist>"
               f"<form method='post' action='/restore' enctype='multipart/form-data'>{hidden(csrf)}<div class='grid'>"
               f"<div><label>Target customer name</label><input name='target' list='cn' required maxlength='31'></div>"
               f"<div><label>Existing backup</label><select name='backup'><option value=''>- none (use upload) -</option>{opts}</select></div>"
               f"<div><label>Or upload .tar.gz (max {MAX_UPLOAD // 1024 // 1024} MB)</label><input type='file' name='file' accept='.gz,.tgz,application/gzip'></div>"
               f"</div><button class='btn p'>Restore</button></form></div>")
    backups = (f"<div class='card'><h2>Backups</h2><div class='tw'><table><thead><tr><th>Customer</th><th>File</th><th>Size</th><th>Created</th><th></th></tr></thead>"
               f"<tbody>{brows}</tbody></table></div></div>")
    cron = (f"<div class='card'><h2>Automatic daily backup</h2><p>Status: <b class='{'ok' if cron_on else 'warn'}'>{'ON' if cron_on else 'OFF'}</b></p>"
            f"<form class='i' method='post' action='/cron'>{hidden(csrf, state='off' if cron_on else 'on')}"
            f"<button class='btn'>{'Turn off' if cron_on else 'Turn on'}</button></form></div>")
    warn = "" if c1 == 0 else f"<div class='card'><b class='bad'>Could not list customers:</b><pre>{esc(out_list)}</pre></div>"
    return layout("Dashboard", warn + cust + create + backups + restore + cron, csrf)


def valid_name_or_all(v: str, allow_all: bool) -> bool:
    return bool(NAME_RE.match(v)) or (allow_all and v == "all")


async def create(request):
    f = await read_form(request)
    name, token, admins = f.get("name", "").strip(), f.get("token", "").strip(), f.get("admins", "").strip()
    domain = f.get("domain", "").strip().lower()
    if not NAME_RE.match(name):
        return error_page(request, "Invalid name: 2-31 chars, a-z 0-9 _ -, must start with a letter.")
    if not TOKEN_RE.match(token):
        return error_page(request, "Invalid bot token format.")
    if not ADMINS_RE.match(admins):
        return error_page(request, "Admin IDs must be numbers separated by commas.")
    if domain and not DOMAIN_RE.match(domain):
        return error_page(request, "Invalid domain name.")
    code, out = await run_cli(request.app, ["new", name, token, domain, admins], mutating=True, timeout=300, hide=(token,))
    return result_page(request, f"Create '{name}'", code, out)


ACTIONS = {"start": 30, "stop": 30, "restart": 30, "backup": 300, "update": 300}


async def action(request):
    f = await read_form(request)
    act, name = f.get("action", ""), f.get("name", "")
    if act not in ACTIONS:
        return error_page(request, "Unknown action.")
    if not valid_name_or_all(name, act in ("backup", "update")):
        return error_page(request, "Invalid customer name.")
    code, out = await run_cli(request.app, [act, name], mutating=True, timeout=ACTIONS[act])
    return result_page(request, f"{act} {name}", code, out)


async def logs(request):
    name = request.query.get("name", "")
    n = request.query.get("n", "100")
    if not NAME_RE.match(name):
        return error_page(request, "Invalid customer name.")
    if not n.isdigit() or not 1 <= int(n) <= 2000:
        return error_page(request, "Line count must be 1-2000.")
    code, out = await run_cli(request.app, ["logs", name, n], timeout=30)
    body = (f"<div class='card'><h2>Logs: {esc(name)} (last {esc(n)})</h2>"
            f"<form class='row' method='get' action='/logs'><input type='hidden' name='name' value='{esc(name)}'>"
            f"<input name='n' value='{esc(n)}' size='5'><button class='btn'>Show</button><a class='btn' href='/'>Back</a></form>"
            f"<pre>{esc(out or '(empty)')}</pre></div>")
    return layout("Logs", body, request["csrf"])


async def doctor(request):
    code, out = await run_cli(request.app, ["doctor"], timeout=30)
    return layout("Doctor", f"<div class='card'><h2>Doctor</h2><pre>{esc(out)}</pre><a class='btn' href='/'>Back</a></div>", request["csrf"])


async def cron(request):
    f = await read_form(request)
    if f.get("state") not in ("on", "off"):
        return error_page(request, "Invalid state.")
    code, out = await run_cli(request.app, ["cron", f["state"]], mutating=True)
    return result_page(request, f"Auto-backup {f['state']}", code, out)


async def delete_page(request):
    name = request.query.get("name", "")
    if not NAME_RE.match(name):
        return error_page(request, "Invalid customer name.")
    body = (f"<div class='card'><h2 class='bad'>Delete '{esc(name)}'</h2><p>This removes the bot, its data and domain config. "
            f"A final backup is kept in <code>backups/_deleted</code>. Type the customer name to confirm.</p>"
            f"<form method='post' action='/delete'>{hidden(request['csrf'], name=name)}"
            f"<p><input name='confirm' autocomplete='off' placeholder='{esc(name)}' required></p>"
            f"<button class='btn d'>Delete permanently</button> <a class='btn' href='/'>Cancel</a></form></div>")
    return layout("Delete", body, request["csrf"])


async def delete(request):
    f = await read_form(request)
    name = f.get("name", "")
    if not NAME_RE.match(name):
        return error_page(request, "Invalid customer name.")
    if f.get("confirm", "") != name:
        return error_page(request, "The typed name does not match; nothing was deleted.")
    code, out = await run_cli(request.app, ["delete", name], mutating=True, timeout=300)
    return result_page(request, f"Delete '{name}'", code, out)


async def download(request):
    p = resolve_backup(request.app["cfg"], request.query.get("f", ""))
    if not p:
        return error_page(request, "Backup not found.", 404)
    return web.FileResponse(p, headers={"Content-Disposition": f'attachment; filename="{os.path.basename(p)}"',
                                        "Content-Type": "application/gzip"})


async def restore(request):
    app = request.app
    cfg: Config = app["cfg"]
    if not request.content_type.startswith("multipart/"):
        return error_page(request, "Expected a multipart form.")
    reader = await request.multipart()
    csrf_ok = False
    fields = {}
    tmp_path = None
    size = 0
    try:
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "csrf":
                csrf_ok = hmac.compare_digest((await part.text())[:200], request["csrf"])
                continue
            if not csrf_ok:
                return error_page(request, "Bad or missing CSRF token", 403)
            if part.name == "file" and part.filename:
                ensure_dir(cfg.tmpdir)
                tmp_path = os.path.join(cfg.tmpdir, f"up-{secrets.token_hex(12)}.tar.gz")
                fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    while True:
                        chunk = await part.read_chunk(1 << 20)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > cfg.max_upload:
                            return error_page(request, f"Upload too large (limit {cfg.max_upload // 1024 // 1024} MB).", 413)
                        out.write(chunk)
            elif part.name in ("target", "backup"):
                fields[part.name] = (await part.text())[:300].strip()
            else:
                await part.release()
        if not csrf_ok:
            return error_page(request, "Bad or missing CSRF token", 403)
        target = fields.get("target", "")
        if not NAME_RE.match(target):
            return error_page(request, "Invalid target customer name.")
        if tmp_path and size > 0:
            src = tmp_path
        elif fields.get("backup"):
            src = resolve_backup(cfg, fields["backup"])
            if not src:
                return error_page(request, "Backup not found.", 404)
        else:
            return error_page(request, "Choose an existing backup or upload a .tar.gz file.")
        code, out = await run_cli(app, ["restore", src, target], mutating=True, timeout=300, extra_env={"RESTORE_ENV": "keep"})
        if tmp_path:
            out = out.replace(tmp_path, "<uploaded file>")
        return result_page(request, f"Restore into '{target}'", code, out)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ----------------------------------------------------------------- app
def create_app(cfg: Config | None = None) -> web.Application:
    cfg = cfg or Config()
    app = web.Application(middlewares=[auth_mw], client_max_size=cfg.max_upload + 2 * 1024 * 1024)
    app["cfg"] = cfg
    app["secret"] = load_secret(cfg)
    app["lock"] = asyncio.Lock()
    app["fails"] = {}
    ensure_dir(cfg.tmpdir)
    for f in os.listdir(cfg.tmpdir):                      # leftovers from a crash
        if f.startswith("up-"):
            try:
                os.unlink(os.path.join(cfg.tmpdir, f))
            except OSError:
                pass

    global _LOGIN_STUB

    def stub(msg, status=200):
        m = f"<p class='bad'>{esc(msg)}</p>" if msg else ""
        body = (f"<div class='card login'><h2>Sign in</h2>{m}<form method='post' action='/login'>"
                f"<input type='hidden' name='token' value='{esc(login_token(app['secret']))}'>"
                "<input type='password' name='password' placeholder='Admin password' autocomplete='current-password' autofocus required>"
                "<button class='btn p'>Sign in</button></form></div>")
        return layout("Sign in", body, None, status)
    _LOGIN_STUB = stub

    app.router.add_route("*", "/login", login_page)
    app.router.add_post("/logout", logout)
    app.router.add_get("/", dashboard)
    app.router.add_post("/create", create)
    app.router.add_post("/action", action)
    app.router.add_get("/logs", logs)
    app.router.add_get("/doctor", doctor)
    app.router.add_post("/cron", cron)
    app.router.add_get("/delete", delete_page)
    app.router.add_post("/delete", delete)
    app.router.add_get("/download", download)
    app.router.add_post("/restore", restore)
    app.on_response_prepare.append(add_headers)
    return app


def read_new_password() -> str:
    pw = os.environ.get("STARHUB_WEB_PASSWORD") or os.environ.get("STARHUB_WEB_PASS")
    if pw:
        return pw
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    a, b = getpass.getpass("New admin password: "), getpass.getpass("Repeat: ")
    if a != b:
        raise ValueError("passwords do not match")
    return a


def main(argv=None):
    ap = argparse.ArgumentParser(description="StarHub web dashboard")
    ap.add_argument("cmd", nargs="?", default="serve", choices=["serve", "passwd"])
    ap.add_argument("--set-password", action="store_true", help="store the admin password (hashed) and exit")
    ap.add_argument("--host", default=os.environ.get("STARHUB_WEB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("STARHUB_WEB_PORT", "8090")))
    a = ap.parse_args(argv)
    cfg = Config()
    if a.set_password or a.cmd == "passwd":
        try:
            set_password(cfg, read_new_password())
        except ValueError as ex:
            print(f"error: {ex}", file=sys.stderr)
            return 1
        load_secret(cfg)
        print(f"Password saved to {cfg.auth_file}")
        return 0
    if load_auth(cfg) is None:
        print("warning: no admin password set; run with --set-password", file=sys.stderr)
    web.run_app(create_app(cfg), host=a.host, port=a.port, print=lambda *_: None, access_log=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
