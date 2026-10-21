#!/usr/bin/env bash
# =====================================================================
#  StarShop Manager - install and manage the Stars / Premium / Gift shop bot
#  Run on the server as root:
#     bash <(curl -Ls https://raw.githubusercontent.com/rezameisi10-a11y/stars-and-premium-shop/main/starshop.sh)
#  After installing, just type:  starshop
# =====================================================================
SCRIPT_VERSION="1.2.0"

REPO="${STARSHOP_REPO:-rezameisi10-a11y/stars-and-premium-shop}"
BRANCH="${STARSHOP_BRANCH:-main}"
# A version tag can be given as an argument: "... v1.1.0" or "starshop install v1.1.0" / "starshop update v1.1.0"
VERSION_ARG=""
for __a in "$@"; do
  if [[ "$__a" =~ ^v?[0-9]+(\.[0-9]+)+$ ]]; then VERSION_ARG="v${__a#v}"; fi
done
[ -n "$VERSION_ARG" ] && BRANCH="$VERSION_ARG"
BASE="${STARSHOP_BASE:-/opt/starshop}"
CMD_PATH="${STARSHOP_CMD:-/usr/local/bin/starshop}"
APP="$BASE/app"
DATA="$BASE/data"
BACKUPS="$BASE/backups"
ENVF="$BASE/.env"
VENV="$BASE/venv"
SERVICE="starshop"
SVC_USER="starshop"
CADDY_CONF="/etc/caddy/conf.d/starshop.caddy"

# ---------------------------------------------------------------- look & feel
if [ -t 1 ]; then
  C_R=$'\e[31m'; C_G=$'\e[32m'; C_Y=$'\e[33m'; C_B=$'\e[36m'; C_D=$'\e[2m'; C_W=$'\e[1m'; C_0=$'\e[0m'
else
  C_R=""; C_G=""; C_Y=""; C_B=""; C_D=""; C_W=""; C_0=""
fi
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✔%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '%s⚠%s %s\n' "$C_Y" "$C_0" "$*"; }
err()  { printf '%s✘%s %s\n' "$C_R" "$C_0" "$*" >&2; }
die()  { err "$*"; exit 1; }
step() { printf '\n%s▶ %s%s\n' "$C_B" "$*" "$C_0"; }

TTY=/dev/tty
[ -r /dev/tty ] || TTY=/dev/stdin

# ask VAR "prompt" [default] - does not prompt if VAR already has a value (env)
ask() {
  local __v="$1" __p="$2" __d="${3-}" __in=""
  [ -n "${!__v:-}" ] && return 0
  if [ "${NONINTERACTIVE:-0}" = 1 ]; then printf -v "$__v" '%s' "$__d"; return 0; fi
  if [ -n "$__d" ]; then read -r -p "$__p [$__d]: " __in <"$TTY"; else read -r -p "$__p: " __in <"$TTY"; fi
  printf -v "$__v" '%s' "${__in:-$__d}"
}
ask_secret() {
  local __v="$1" __p="$2" __in=""
  [ -n "${!__v:-}" ] && return 0
  [ "${NONINTERACTIVE:-0}" = 1 ] && return 0
  read -r -s -p "$__p: " __in <"$TTY"; echo
  printf -v "$__v" '%s' "$__in"
}
confirm() {   # confirm "question" [y|n]
  [ "${YES:-0}" = 1 ] && return 0
  local d="${2:-n}" a="" hint="y/N"
  [ "$d" = y ] && hint="Y/n"
  read -r -p "$1 [$hint]: " a <"$TTY"
  a="${a:-$d}"
  [[ "$a" =~ ^[Yy] ]]
}
pause() { [ "${NONINTERACTIVE:-0}" = 1 ] && return 0; read -r -p "Press Enter to continue..." _ <"$TTY"; }

rand_hex()  { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
rand_pass() { head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16; }

need_root() { [ "$(id -u)" -eq 0 ] || die "This script must be run as root (run: sudo -i)"; }

safe_val() {  # values stored in .env must not contain ' or newlines
  case "$1" in *\'*|*$'\n'*|*$'\r'*) return 1 ;; esac
  return 0
}

# ---------------------------------------------------------------- .env
env_get() {
  [ -f "$ENVF" ] || return 0
  local l
  l=$(grep -E "^$1=" "$ENVF" | tail -1) || true
  l="${l#*=}"; l="${l%\'}"; l="${l#\'}"
  printf '%s' "$l"
}
env_set() {
  local k="$1" v="$2" tmp
  safe_val "$v" || die "The value of $k must not contain ' or a newline."
  mkdir -p "$(dirname "$ENVF")"; touch "$ENVF"
  tmp=$(mktemp)
  grep -vE "^$k=" "$ENVF" > "$tmp" || true
  printf "%s='%s'\n" "$k" "$v" >> "$tmp"
  cat "$tmp" > "$ENVF"; rm -f "$tmp"
  chmod 600 "$ENVF"
  id "$SVC_USER" >/dev/null 2>&1 && chown "$SVC_USER:$SVC_USER" "$ENVF" 2>/dev/null
  return 0
}

gitapp() { git -c safe.directory="$APP" -C "$APP" "$@"; }
# Revision for a branch or a release tag (tags win when one exists with that name)
git_ref() { if gitapp rev-parse -q --verify "refs/tags/$1^{commit}" >/dev/null 2>&1; then printf 'refs/tags/%s^{commit}' "$1"; else printf 'origin/%s' "$1"; fi; }
git_fetch_ref() { gitapp fetch -q --tags origin "+refs/heads/$1:refs/remotes/origin/$1" 2>/dev/null || gitapp fetch -q --tags origin "$1" 2>/dev/null || gitapp fetch -q --tags origin; }
is_installed() { [ -f "$ENVF" ] && [ -d "$APP/.git" ]; }
need_installed() { is_installed || { err "The bot is not installed yet. Choose option 1 (Install) first."; return 1; }; }

# ---------------------------------------------------------------- systemd
have_systemd() { [ "${STARSHOP_NO_SYSTEMD:-0}" != 1 ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; }
svc()        { if have_systemd; then systemctl "$@" "$SERVICE"; else warn "systemd is not available (svc $*)"; fi; }
svc_active() { have_systemd && systemctl is-active --quiet "$SERVICE"; }
svc_state()  { if have_systemd; then systemctl is-active "$SERVICE" 2>/dev/null || true; else echo "no-systemd"; fi; }

# ---------------------------------------------------------------- network helpers
public_ip() { curl -s -m 8 https://api.ipify.org 2>/dev/null || curl -s -m 8 https://ifconfig.me 2>/dev/null || true; }
dns_ip()    { getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1{print $1}'; }
port_busy() {   # returns 0 if the port is in use
  python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)
sys.exit(1)
PY
}
json_get() {
  python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    for k in sys.argv[1].split("."):
        d = d[k]
    print(d)
except Exception:
    pass' "$1"
}
tg_getme() { curl -s -m 15 "https://api.telegram.org/bot$1/getMe"; }

check_token() {
  local t="$1" r
  [[ "$t" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]] || { err "Invalid token format (example: 123456789:AA...)"; return 1; }
  [ "${SKIP_TOKEN_CHECK:-0}" = 1 ] && return 0
  r=$(tg_getme "$t")
  if [ -z "$r" ]; then
    warn "Could not reach api.telegram.org. If this server is in a country where Telegram is blocked, the bot will not work (use a server elsewhere)."
    confirm "Continue with this token without verifying it?" n || return 1
    return 0
  fi
  [ "$(printf '%s' "$r" | json_get ok)" = "True" ] || { err "Telegram rejected the token (invalid or revoked)."; return 1; }
  BOT_USERNAME=$(printf '%s' "$r" | json_get result.username)
  ok "Token is valid: @$BOT_USERNAME"
}

check_admins() { [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]] || { err "Admin IDs must be numeric and comma-separated (example: 123456,789012). Get your ID from @userinfobot."; return 1; }; }

check_domain_dns() {   # $1 = domain
  local d="$1" mine theirs
  mine=$(public_ip); theirs=$(dns_ip "$d")
  if [ -z "$theirs" ]; then warn "Domain $d does not point to any IP yet (create an A record)."; return 1; fi
  if [ -n "$mine" ] && [ "$mine" != "$theirs" ]; then
    warn "The domain points to $theirs but this server's IP is $mine."
    warn "If you use Cloudflare, temporarily turn the orange cloud off (DNS only) so the SSL certificate can be issued."
    return 1
  fi
  ok "The domain correctly points to this server ($mine)."
}

# ---------------------------------------------------------------- packages
pkg_install() {
  [ "${STARSHOP_NO_PKG:-0}" = 1 ] && return 0
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" >/dev/null
  elif command -v dnf >/dev/null 2>&1; then dnf install -y "$@" >/dev/null
  elif command -v yum >/dev/null 2>&1; then yum install -y "$@" >/dev/null
  else die "No supported package manager (apt/dnf/yum) found. Ubuntu 22.04+ or Debian 12+ is recommended."
  fi
}

PY=""
find_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY="$c"; return 0; fi
  done
  return 1
}

# Python used for helper tasks (sqlite checks, zip extraction): the venv if present, else system python3
helper_python() {
  if [ -x "$VENV/bin/python" ]; then printf '%s' "$VENV/bin/python"; else printf '%s' "python3"; fi
}

install_base_packages() {
  step "Installing prerequisites (python, git, curl, sqlite, openssl)"
  if command -v apt-get >/dev/null 2>&1; then
    pkg_install python3 python3-venv python3-pip git curl ca-certificates sqlite3 openssl iproute2
  else
    pkg_install python3 python3-pip git curl ca-certificates sqlite openssl iproute
  fi
  if ! find_python; then
    command -v apt-get >/dev/null 2>&1 && pkg_install python3.11 python3.11-venv 2>/dev/null
    find_python || die "Python 3.10 or newer is required (Ubuntu 22.04+ / Debian 12+ recommended)."
  fi
  ok "Python: $($PY --version 2>&1)"
}

# ---------------------------------------------------------------- Caddy (automatic SSL)
caddy_installed() { command -v caddy >/dev/null 2>&1; }
install_caddy() {
  caddy_installed && return 0
  [ "${STARSHOP_NO_CADDY:-0}" = 1 ] && return 1
  step "Installing Caddy (web server with automatic SSL)"
  if command -v apt-get >/dev/null 2>&1; then
    pkg_install debian-keyring debian-archive-keyring apt-transport-https curl gnupg 2>/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null
    pkg_install caddy
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y 'dnf-command(copr)' >/dev/null 2>&1; dnf copr enable -y @caddy/caddy >/dev/null 2>&1; dnf install -y caddy >/dev/null 2>&1
  fi
  caddy_installed && ok "Caddy installed" || { err "Caddy installation failed."; return 1; }
}

caddy_write() {   # $1=domain $2=port
  mkdir -p /etc/caddy/conf.d
  cat > "$CADDY_CONF" <<EOF
$1 {
    encode gzip
    reverse_proxy 127.0.0.1:$2
}
EOF
  [ -f /etc/caddy/Caddyfile ] || echo "# managed" > /etc/caddy/Caddyfile
  grep -q 'conf.d/\*.caddy' /etc/caddy/Caddyfile || printf '\nimport /etc/caddy/conf.d/*.caddy\n' >> /etc/caddy/Caddyfile
  if caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
    have_systemd && { systemctl enable caddy >/dev/null 2>&1; systemctl restart caddy; }
    ok "Caddy configured for $1 (SSL certificate is issued and renewed automatically)"
  else
    err "Invalid Caddy configuration; check the file $CADDY_CONF."; return 1
  fi
}
caddy_remove() {
  [ -f "$CADDY_CONF" ] || return 0
  rm -f "$CADDY_CONF"
  have_systemd && systemctl reload caddy 2>/dev/null
  return 0
}

fw_open() {   # opens ports only if ufw is active
  command -v ufw >/dev/null 2>&1 || return 0
  ufw status 2>/dev/null | grep -q "Status: active" || return 0
  local p; for p in "$@"; do ufw allow "$p/tcp" >/dev/null 2>&1; done
  ok "Opened port(s) $* in the firewall (ufw)"
}

wait_health() {   # $1 = port
  local i
  for i in $(seq 1 30); do
    curl -fs -m 2 "http://127.0.0.1:$1/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# ---------------------------------------------------------------- install
write_service_files() {
  have_systemd || return 0
  cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=StarShop Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$APP
EnvironmentFile=$ENVF
ExecStart=$VENV/bin/python main.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}

pip_install_reqs() {
  "$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt" || return 1
}

selftest_import() {
  ( cd "$APP" && "$VENV/bin/python" -c "import main" ) 2>&1
}

repo_url() {
  case "$REPO" in
    *://*|/*|git@*) printf '%s' "$REPO" ;;
    *) printf 'https://github.com/%s.git' "$REPO" ;;
  esac
}

do_install() {
  need_root
  step "Installing the Stars shop bot"
  if is_installed; then
    warn "The bot is already installed."
    confirm "Reinstall / repair? (database and settings are kept)" n || return 0
  fi

  if [[ "$REPO" == YOUR_GITHUB_USER/* ]]; then
    REPO=""; ask REPO "GitHub repository of the project (as user/repo)"
    [ -n "$REPO" ] || die "A repository address is required."
  fi

  # ---- collect information
  say ""
  say "${C_W}Information needed for installation:${C_0}"
  while true; do
    ask BOT_TOKEN "Bot token (from @BotFather)"
    check_token "${BOT_TOKEN:-}" && break
    BOT_TOKEN=""; [ "${NONINTERACTIVE:-0}" = 1 ] && die "Invalid token"
  done
  while true; do
    ask ADMIN_IDS "Numeric ID(s) of the admin(s), comma-separated"
    ADMIN_IDS="${ADMIN_IDS// /}"
    check_admins "${ADMIN_IDS:-}" && break
    ADMIN_IDS=""; [ "${NONINTERACTIVE:-0}" = 1 ] && die "Invalid admin ID"
  done
  say "${C_D}A domain is optional: with a domain the web panel runs over HTTPS with automatic SSL. Without one, the panel is served on http://IP:port.${C_0}"
  ask DOMAIN "Panel domain (e.g. panel.example.com) - press Enter to skip"
  DOMAIN="${DOMAIN,,}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%/}"
  if [ -n "$DOMAIN" ]; then
    [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]] || die "Invalid domain format."
    check_domain_dns "$DOMAIN" || { confirm "Continue anyway? (without correct DNS, SSL cannot be issued)" n || die "Fix the domain's DNS first."; }
  fi
  ask WEB_PORT "Internal web panel port" "8080"
  [[ "$WEB_PORT" =~ ^[0-9]+$ ]] && [ "$WEB_PORT" -ge 1024 ] && [ "$WEB_PORT" -le 65535 ] || die "Invalid port."
  ask WEB_USER "Web panel username" "admin"
  say "${C_D}Note: the database is SQLite and has no separate password; the password below is for logging in to the web panel. Press Enter for a random one.${C_0}"
  ask_secret PANEL_PASSWORD "Web panel password (Enter = random and secure)"
  if [ -z "${PANEL_PASSWORD:-}" ]; then PANEL_PASSWORD=$(rand_pass); GENERATED_PW=1; fi
  safe_val "$PANEL_PASSWORD" || die "The password must not contain '."
  [ "${#PANEL_PASSWORD}" -ge 8 ] || die "The panel password must be at least 8 characters."
  ask BACKUP_PASS "Backup encryption password (optional; Enter = no encryption)"
  if [ -n "${BACKUP_PASS:-}" ] && ! safe_val "$BACKUP_PASS"; then die "The backup password must not contain '."; fi

  # ---- install
  install_base_packages
  step "Preparing user and directories"
  id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home "$BASE" --shell /usr/sbin/nologin "$SVC_USER"
  mkdir -p "$BASE" "$DATA" "$BACKUPS"

  step "Fetching the bot source from GitHub"
  if [ -d "$APP/.git" ]; then
    git_fetch_ref "$BRANCH"; gitapp reset -q --hard "$(git_ref "$BRANCH")" || die "Updating the source failed (is \"$BRANCH\" a valid branch or version?)."
  else
    rm -rf "$APP"
    git clone -q -b "$BRANCH" "$(repo_url)" "$APP" || die "Cloning the repository failed. Check the repository, the branch/version \"$BRANCH\" and your internet access (GitHub)."
  fi
  [ -f "$APP/main.py" ] || die "main.py was not found in the repository; this is not the right repository."
  ok "Source version: $(src_version)"

  step "Creating the Python environment and installing dependencies"
  [ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV" || die "Creating the venv failed."
  pip_install_reqs || die "Installing dependencies failed."
  ok "Dependencies installed"

  step "Writing settings"
  local pub host
  if [ -n "$DOMAIN" ]; then pub="https://$DOMAIN"; host="127.0.0.1"
  else
    local ip; ip=$(public_ip); [ -n "$ip" ] || ip="SERVER_IP"
    pub="http://$ip:$WEB_PORT"; host="0.0.0.0"
  fi
  [ -f "$ENVF" ] && WEB_SECRET_KEEP=$(env_get WEB_SECRET)
  env_set BOT_TOKEN "$BOT_TOKEN"
  env_set BOT_USERNAME "${BOT_USERNAME:-}"
  env_set ADMIN_IDS "$ADMIN_IDS"
  env_set PUBLIC_URL "$pub"
  env_set DOMAIN "$DOMAIN"
  env_set WEB_HOST "$host"
  env_set WEB_PORT "$WEB_PORT"
  env_set WEB_USER "$WEB_USER"
  env_set WEB_PASSWORD "$PANEL_PASSWORD"
  env_set WEB_SECRET "${WEB_SECRET_KEEP:-$(rand_hex)}"
  env_set DB_PATH "$DATA/shop.db"
  env_set BACKUP_PASSWORD "${BACKUP_PASS:-}"
  env_set BACKUP_TG "$(env_get BACKUP_TG)"
  env_set KEEP_BACKUPS "$(env_get KEEP_BACKUPS)"
  env_set STARSHOP_REPO "$REPO"
  env_set STARSHOP_BRANCH "$BRANCH"
  [ -n "$(env_get BACKUP_TG)" ] || env_set BACKUP_TG "0"
  [ -n "$(env_get KEEP_BACKUPS)" ] || env_set KEEP_BACKUPS "14"
  chown -R "$SVC_USER:$SVC_USER" "$BASE"
  chmod 700 "$BACKUPS"; chmod 600 "$ENVF"
  ok "Settings file: $ENVF"

  step "Checking code health"
  local out; out=$(selftest_import) || { err "Importing the application failed:"; echo "$out"; die "Installation stopped."; }
  ok "Code loaded without errors"

  step "Installing the service and starting it"
  write_service_files
  install -m 755 "$APP/starshop.sh" "$CMD_PATH" 2>/dev/null && ok "Management command installed: ${C_W}starshop${C_0}"
  if have_systemd; then
    systemctl enable "$SERVICE" >/dev/null 2>&1
    systemctl restart "$SERVICE"
    sleep 3
    if svc_active; then ok "The bot service is running"; else err "The service did not start; log:"; journalctl -u "$SERVICE" -n 25 --no-pager; fi
  else
    warn "systemd is not available; to run manually: cd $APP && $VENV/bin/python main.py"
  fi

  if [ -n "$DOMAIN" ]; then
    if install_caddy; then
      caddy_write "$DOMAIN" "$WEB_PORT" && fw_open 80 443
    else
      warn "Could not install Caddy; the panel is available on 127.0.0.1:$WEB_PORT and you must set up an HTTPS proxy yourself."
    fi
  else
    fw_open "$WEB_PORT"
  fi

  if have_systemd && wait_health "$WEB_PORT"; then ok "The web panel is up"; fi
  print_summary "${GENERATED_PW:-0}"
}

print_summary() {
  local gen="${1:-0}" bu
  bu=$(env_get BOT_USERNAME)
  say ""
  say "${C_G}══════════════ Installation complete ══════════════${C_0}"
  [ -n "$bu" ] && say "Bot:         https://t.me/$bu"
  say "Web panel:   $(env_get PUBLIC_URL)"
  say "Username:    $(env_get WEB_USER)"
  if [ "$gen" = 1 ]; then say "Password:    ${C_W}$(env_get WEB_PASSWORD)${C_0}  ${C_Y}(save it now)${C_0}"; else say "Password:    (the one you entered)"; fi
  say "Management:  run the command ${C_W}starshop${C_0}"
  say ""
  say "${C_W}Next steps:${C_0}"
  say " 1) Start the bot in Telegram and send /admin (with an admin account)"
  say " 2) Under \"Settings > Pricing\" set the daily Star rate and your profit margin"
  say " 3) For automatic Premium/Gift delivery, top up the bot's Star balance"
  say " 4) Open \"Products > Gifts > Sync\""
  say " 5) Enable your card number and payment methods in the settings"
  say ""
  say "${C_D}Payment gateways (Zarinpal / TON / USDT), prices, languages and admins are configured from the bot's /admin or the web panel.${C_0}"
}

# ---------------------------------------------------------------- version
src_version() {
  local v c
  v=$(cat "$APP/VERSION" 2>/dev/null || echo "?")
  c=$(gitapp rev-parse --short HEAD 2>/dev/null || echo "?")
  printf '%s (%s)' "$v" "$c"
}

# ---------------------------------------------------------------- backup
backup_make() {   # output: file path in BACKUP_FILE
  local ts tmp out bp
  ts=$(date +%Y%m%d-%H%M%S); tmp=$(mktemp -d); mkdir -p "$BACKUPS"
  [ -f "$DATA/shop.db" ] || { err "Database not found ($DATA/shop.db)"; rm -rf "$tmp"; return 1; }
  "$(helper_python)" - "$DATA/shop.db" "$tmp/shop.db" <<'PY' || { err "Safe database copy failed"; rm -rf "$tmp"; return 1; }
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
  cp "$ENVF" "$tmp/env"
  src_version > "$tmp/version"
  out="$BACKUPS/starshop-$ts.tar.gz"
  tar -czf "$out" -C "$tmp" . || { rm -rf "$tmp"; return 1; }
  bp=$(env_get BACKUP_PASSWORD)
  if [ -n "$bp" ]; then
    BP="$bp" openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BP -in "$out" -out "$out.enc" && rm -f "$out" && out="$out.enc"
  fi
  chmod 600 "$out"
  # database-only file to send to Telegram (if not encrypted, .env is not included)
  TG_FILE="$out"
  if [ -z "$bp" ]; then
    TG_FILE="$tmp/starshop-db-$ts.tar.gz"
    tar -czf "$TG_FILE" -C "$tmp" shop.db
  fi
  BACKUP_FILE="$out"
  BACKUP_TMP="$tmp"
  return 0
}

backup_rotate() {
  local keep; keep=$(env_get KEEP_BACKUPS); keep="${keep:-14}"
  [[ "$keep" =~ ^[0-9]+$ ]] || keep=14
  ls -1t "$BACKUPS"/starshop-*.tar.gz* 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
}

tg_send_file() {
  local f="$1" token ids id
  token=$(env_get BOT_TOKEN); ids=$(env_get ADMIN_IDS)
  [ -n "$token" ] || return 1
  if [ "$(stat -c %s "$f")" -gt 49000000 ]; then warn "The file is larger than 50MB and will not be sent to Telegram."; return 1; fi
  for id in ${ids//,/ }; do
    if curl -s -m 180 -F "chat_id=$id" -F "document=@$f" -F "caption=💾 StarShop backup $(date '+%F %H:%M')" \
         "https://api.telegram.org/bot$token/sendDocument" | grep -q '"ok":true'; then ok "Backup sent to admin $id on Telegram"; else warn "Sending to $id failed"; fi
  done
}

do_backup() {
  need_root; need_installed || return 1
  local auto="${1:-}"
  [ "$auto" = "--auto" ] || step "Creating a backup"
  backup_make || return 1
  backup_rotate
  [ "$auto" = "--auto" ] || { ok "Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"; [ -n "$(env_get BACKUP_PASSWORD)" ] && ok "The file is encrypted (do not lose the password!)"; }
  if [ "$(env_get BACKUP_TG)" = "1" ]; then tg_send_file "$TG_FILE"; fi
  rm -rf "$BACKUP_TMP"
}

# Extract shop.db / env / meta.json from a backup bundle .zip into $2 (nothing else is extracted)
zip_extract_bundle() {   # $1 = zip file, $2 = destination dir
  "$(helper_python)" -I - "$1" "$2" <<'PY'
import sys, zipfile, os, shutil
zf, dest = sys.argv[1], sys.argv[2]
try:
    z = zipfile.ZipFile(zf)
except Exception as e:
    print("not a valid zip file: %s" % e); sys.exit(1)
names = {os.path.basename(n): n for n in z.namelist() if not n.endswith("/")}
if "shop.db" not in names:
    print("shop.db is missing from the zip"); sys.exit(2)
for want in ("shop.db", "meta.json", "env"):
    if want in names:
        with z.open(names[want]) as src, open(os.path.join(dest, want), "wb") as dst:
            shutil.copyfileobj(src, dst)
PY
}

# Validate a SQLite file: integrity_check == ok, and (optionally) a table named settings exists
db_validate() {   # $1 = db file, $2 = "need-settings" to require the settings table
  "$(helper_python)" -I - "$1" "${2:-}" <<'PY'
import sqlite3, sys
path, need = sys.argv[1], sys.argv[2]
try:
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    r = c.execute("PRAGMA integrity_check").fetchone()[0]
    if r != "ok":
        print("integrity check failed: %s" % r); sys.exit(1)
    if need == "need-settings":
        row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        if not row:
            print("table 'settings' not found"); sys.exit(1)
    print("ok")
except Exception as e:
    print("not a valid SQLite database: %s" % e); sys.exit(1)
PY
}

do_restore() {
  need_root; need_installed || return 1
  step "Restoring a backup"
  local files f n sel tmp pass
  mapfile -t files < <(ls -1t "$BACKUPS"/starshop-*.tar.gz* 2>/dev/null)
  if [ "${#files[@]}" -gt 0 ]; then
    say "Backups available on this server:"
    n=1; for f in "${files[@]}"; do say "  $n) $(basename "$f")  ($(du -h "$f" | cut -f1))"; n=$((n + 1)); done
  fi
  say "  Or enter the full path of a backup file: a raw .db file, a backup bundle .zip (shop.db [+ meta.json + env]), or a .tar.gz / .tar.gz.enc made by this script."
  ask RESTORE_SEL "Backup number or file path"
  sel="$RESTORE_SEL"
  if [[ "$sel" =~ ^[0-9]+$ ]] && [ "$sel" -ge 1 ] && [ "$sel" -le "${#files[@]}" ]; then f="${files[$((sel - 1))]}"; else f="$sel"; fi
  [ -f "$f" ] || { err "File not found: $f"; return 1; }
  tmp=$(mktemp -d)
  mkdir -p "$tmp/x"
  local chk need=""
  case "$f" in
    *.zip)
      chk=$(zip_extract_bundle "$f" "$tmp/x") || { err "Invalid backup bundle: $chk"; rm -rf "$tmp"; return 1; }
      need="need-settings"
      ;;
    *.db|*.sqlite|*.sqlite3)
      cp "$f" "$tmp/x/shop.db"
      need="need-settings"
      ;;
    *)
      local work="$tmp/in"
      cp "$f" "$work"
      if [[ "$f" == *.enc ]]; then
        pass="${RESTORE_PASS:-}"; ask_secret pass "Backup encryption password"
        BP="$pass" openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BP -in "$work" -out "$tmp/dec.tgz" 2>/dev/null || { err "Wrong password or the file is corrupted."; rm -rf "$tmp"; return 1; }
        work="$tmp/dec.tgz"
      fi
      tar -xzf "$work" -C "$tmp/x" 2>/dev/null || { err "The backup file is not valid."; rm -rf "$tmp"; return 1; }
      ;;
  esac
  [ -f "$tmp/x/shop.db" ] || { err "shop.db is not inside the backup."; rm -rf "$tmp"; return 1; }
  chk=$(db_validate "$tmp/x/shop.db" "$need") || { err "The database in the backup is not valid: $chk"; rm -rf "$tmp"; return 1; }
  if [ -f "$tmp/x/version" ]; then ok "Backup is valid (version: $(cat "$tmp/x/version" 2>/dev/null))"
  elif [ -f "$tmp/x/meta.json" ]; then ok "Backup is valid (meta: $(head -c 200 "$tmp/x/meta.json" | tr -d '\n'))"
  else ok "Backup is valid"; fi
  warn "The current database will be replaced with the backup's database (the current state is backed up first)."
  confirm "Continue?" n || { rm -rf "$tmp"; return 0; }
  do_backup --auto
  svc stop
  rm -f "$DATA/shop.db" "$DATA/shop.db-wal" "$DATA/shop.db-shm"
  cp "$tmp/x/shop.db" "$DATA/shop.db"
  chown "$SVC_USER:$SVC_USER" "$DATA/shop.db" 2>/dev/null
  if [ -f "$tmp/x/env" ] && confirm "Also restore the settings (.env with token/admins/domain) from the backup? (choose No for a new server)" n; then
    cp "$tmp/x/env" "$ENVF"; env_set DB_PATH "$DATA/shop.db"; chmod 600 "$ENVF"; chown "$SVC_USER:$SVC_USER" "$ENVF" 2>/dev/null
    ok ".env restored"
  fi
  rm -rf "$tmp"
  svc start; sleep 2
  if have_systemd; then
    svc_active && ok "Restore complete and the service is running" || warn "The service is not running; check the log (option 11)."
  else
    ok "Restore complete"
  fi
}

# ---------------------------------------------------------------- automatic backup
do_auto_backup() {
  need_root; need_installed || return 1
  step "Daily automatic backup"
  local cur_tg cur_en hour
  cur_en="disabled"; [ -f /etc/systemd/system/starshop-backup.timer ] && cur_en="enabled"
  say "Current status: $cur_en | Send to Telegram: $([ "$(env_get BACKUP_TG)" = 1 ] && echo on || echo off) | Keep: $(env_get KEEP_BACKUPS) backups | Encryption: $([ -n "$(env_get BACKUP_PASSWORD)" ] && echo yes || echo no)"
  say "  1) Enable / update automatic backup"
  say "  2) Disable it"
  say "  3) Turn sending backups to the admins on Telegram on/off"
  say "  4) Set / change the backup encryption password"
  say "  5) Number of backups to keep"
  ask AB_SEL "Choice" "1"
  case "$AB_SEL" in
    1) have_systemd || { err "systemd is required."; return 1; }
       ask AB_HOUR "Hour to run (0-23)" "4"
       [[ "$AB_HOUR" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || { err "Invalid hour"; return 1; }
       cat > /etc/systemd/system/starshop-backup.service <<EOF
[Unit]
Description=StarShop daily backup
[Service]
Type=oneshot
ExecStart=$CMD_PATH backup --auto
EOF
       cat > /etc/systemd/system/starshop-backup.timer <<EOF
[Unit]
Description=StarShop daily backup timer
[Timer]
OnCalendar=*-*-* $(printf '%02d' "$AB_HOUR"):00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
       systemctl daemon-reload; systemctl enable --now starshop-backup.timer >/dev/null 2>&1
       ok "Automatic backup enabled every day at $AB_HOUR:00" ;;
    2) have_systemd && { systemctl disable --now starshop-backup.timer >/dev/null 2>&1; rm -f /etc/systemd/system/starshop-backup.{timer,service}; systemctl daemon-reload; }
       ok "Automatic backup disabled" ;;
    3) cur_tg=$(env_get BACKUP_TG)
       if [ "$cur_tg" = 1 ]; then env_set BACKUP_TG 0; ok "Sending to Telegram turned off"; else env_set BACKUP_TG 1; ok "Sending to Telegram turned on"; [ -z "$(env_get BACKUP_PASSWORD)" ] && warn "No encryption set: only the database (without the token) is sent to Telegram."; fi ;;
    4) ask_secret AB_PASS "New backup encryption password (empty = remove password)"
       safe_val "$AB_PASS" || { err "The password must not contain '"; return 1; }
       env_set BACKUP_PASSWORD "$AB_PASS"; ok "Saved (old backups still open with their old password)" ;;
    5) ask AB_KEEP "Number of backups to keep" "14"
       [[ "$AB_KEEP" =~ ^[0-9]+$ ]] && [ "$AB_KEEP" -ge 1 ] || { err "Invalid number"; return 1; }
       env_set KEEP_BACKUPS "$AB_KEEP"; ok "Saved" ;;
    *) err "Invalid option" ;;
  esac
}

# ---------------------------------------------------------------- update
do_update() {
  local target="${1:-$VERSION_ARG}"
  need_root; need_installed || return 1
  step "Updating the bot"
  local branch local_h remote_h
  branch=$(env_get STARSHOP_BRANCH); branch="${branch:-main}"
  [ -n "$target" ] && { branch="$target"; say "Target version: ${C_W}$branch${C_0}"; }
  git_fetch_ref "$branch" || { err "Connecting to GitHub failed."; return 1; }
  local_h=$(gitapp rev-parse HEAD); remote_h=$(gitapp rev-parse "$(git_ref "$branch")" 2>/dev/null) || { err "Version/branch \"$branch\" was not found in the repository."; return 1; }
  if [ "$local_h" = "$remote_h" ]; then ok "The bot is up to date: $(src_version)"; return 0; fi
  say "New changes:"; gitapp log --oneline "$local_h..$remote_h" 2>/dev/null | head -15 | sed 's/^/   • /'
  confirm "Run the update? (a backup is made first)" y || return 0
  do_backup --auto
  if [ -n "$(gitapp status --porcelain 2>/dev/null)" ]; then
    warn "Source files on the server were modified by hand (e.g. deliver_stars)."
    confirm "Stash the manual changes? (otherwise the update is cancelled)" n || return 0
    gitapp stash push -u -q -m "starshop-pre-update-$(date +%s)"
  fi
  gitapp reset -q --hard "$(git_ref "$branch")" || { err "Updating the source failed."; return 1; }
  if ! pip_install_reqs || ! selftest_import >/dev/null; then
    err "The new version does not run on this server; rolling back to the previous version..."
    gitapp reset -q --hard "$local_h"; pip_install_reqs
    return 1
  fi
  chown -R "$SVC_USER:$SVC_USER" "$APP"
  install -m 755 "$APP/starshop.sh" "$CMD_PATH" 2>/dev/null
  write_service_files
  svc restart; sleep 4
  if have_systemd && ! svc_active; then
    err "The service did not start after the update; rolling back to the previous version..."
    gitapp reset -q --hard "$local_h"; pip_install_reqs; chown -R "$SVC_USER:$SVC_USER" "$APP"; svc restart
    return 1
  fi
  [ -n "$target" ] && env_set STARSHOP_BRANCH "$branch"
  ok "Update complete: $(src_version)"
}

# ---------------------------------------------------------------- switch version
do_switch_version() {
  need_root; need_installed || return 1
  step "Switch version (upgrade or roll back)"
  gitapp fetch -q --tags origin 2>/dev/null || { err "Connecting to GitHub failed."; return 1; }
  local cur tags=() t i=1 pick=""
  cur=$(env_get STARSHOP_BRANCH); cur="${cur:-main}"
  say "Current: ${C_W}$(src_version)${C_0}  (following: $cur)"
  say ""
  say "  ${C_W}0)${C_0} main  (always the latest version)"
  while IFS= read -r t; do [ -n "$t" ] && { tags+=("$t"); say "  ${C_W}$i)${C_0} $t"; i=$((i + 1)); }; done < <(gitapp tag --sort=-v:refname 2>/dev/null | head -20)
  [ "${#tags[@]}" -gt 0 ] || warn "No release tags were found in the repository (create a Release on GitHub)."
  say ""
  read -r -p "Choose a version number (Enter = cancel): " pick <"$TTY" || return 0
  [ -n "$pick" ] || return 0
  if [ "$pick" = "0" ]; then t="main"
  elif [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#tags[@]}" ]; then t="${tags[$((pick - 1))]}"
  else err "Invalid choice"; return 1; fi
  do_update "$t"
}

# ---------------------------------------------------------------- settings
do_change_token() {
  need_root; need_installed || return 1
  step "Change the bot token"
  BOT_USERNAME=""
  ask BOT_TOKEN "New bot token"
  check_token "$BOT_TOKEN" || return 1
  env_set BOT_TOKEN "$BOT_TOKEN"; env_set BOT_USERNAME "${BOT_USERNAME:-}"
  # the panel session key is independent of the token and stays the same
  svc restart; ok "Token changed and the bot was restarted"
  warn "If this is a new bot: existing users must start it again, and the File IDs of earlier receipts will not work."
}

do_change_admins() {
  need_root; need_installed || return 1
  step "Change admins"
  say "Current admins (.env): $(env_get ADMIN_IDS)"
  say "${C_D}Admins you added from inside the bot are stored separately and are not changed here.${C_0}"
  ask ADMIN_IDS "New list of numeric IDs (comma-separated)"
  ADMIN_IDS="${ADMIN_IDS// /}"
  check_admins "$ADMIN_IDS" || return 1
  env_set ADMIN_IDS "$ADMIN_IDS"; svc restart; ok "Saved and the bot was restarted"
}

do_panel_login() {
  need_root; need_installed || return 1
  step "Web panel login details and password"
  say "Panel URL: $(env_get PUBLIC_URL)"
  say "Username:  $(env_get WEB_USER)"
  say "${C_D}Alternative login: in the bot (with an admin account) send /panel to get a one-time link.${C_0}"
  say "  1) Show the current password"
  say "  2) Change the password (or generate a random one)"
  say "  3) Change the username"
  ask PL_SEL "Choice" "1"
  case "$PL_SEL" in
    1) say "Password: ${C_W}$(env_get WEB_PASSWORD)${C_0}" ;;
    2) ask_secret NEWPW "New password (Enter = random)"
       local gen=0; [ -n "${NEWPW:-}" ] || { NEWPW=$(rand_pass); gen=1; }
       safe_val "$NEWPW" || { err "The password must not contain '"; return 1; }
       [ "${#NEWPW}" -ge 8 ] || { err "At least 8 characters"; return 1; }
       env_set WEB_PASSWORD "$NEWPW"; svc restart
       ok "Password changed"; [ "$gen" = 1 ] && say "New password: ${C_W}$NEWPW${C_0}" ;;
    3) ask NEWUSER "New username"
       [[ "$NEWUSER" =~ ^[A-Za-z0-9_.-]{3,32}$ ]] || { err "Invalid username"; return 1; }
       env_set WEB_USER "$NEWUSER"; svc restart; ok "Changed" ;;
    *) err "Invalid option" ;;
  esac
}

apply_domain() {   # $1=domain (empty = no domain)
  local d="$1" port pub host ip
  port=$(env_get WEB_PORT)
  if [ -n "$d" ]; then
    pub="https://$d"; host="127.0.0.1"
  else
    ip=$(public_ip); [ -n "$ip" ] || ip="SERVER_IP"
    pub="http://$ip:$port"; host="0.0.0.0"
  fi
  env_set DOMAIN "$d"; env_set PUBLIC_URL "$pub"; env_set WEB_HOST "$host"
  if [ -n "$d" ]; then
    install_caddy && caddy_write "$d" "$port" && fw_open 80 443 || warn "Caddy was not configured; set up HTTPS manually."
  else
    caddy_remove; fw_open "$port"
  fi
  svc restart
}

do_change_domain() {
  need_root; need_installed || return 1
  step "Change domain / SSL"
  say "Current domain: $(env_get DOMAIN)  | Panel URL: $(env_get PUBLIC_URL)"
  say "${C_D}The new domain must already point to this server's IP ($(public_ip)) with an A record. Press Enter to remove the domain.${C_0}"
  ask DOMAIN "New domain (empty = no domain)"
  DOMAIN="${DOMAIN,,}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%/}"
  if [ -n "$DOMAIN" ]; then
    [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$ ]] || { err "Invalid domain format."; return 1; }
    check_domain_dns "$DOMAIN" || confirm "Continue anyway?" n || return 0
  fi
  apply_domain "$DOMAIN"
  ok "Done. Panel URL: $(env_get PUBLIC_URL)"
  [ -n "$DOMAIN" ] && say "${C_D}Issuing SSL may take up to 1 minute. If it does not open: option 15 (Doctor).${C_0}"
}

do_change_port() {
  need_root; need_installed || return 1
  step "Change the web panel port"
  ask NEWPORT "New port (1024-65535)" "$(env_get WEB_PORT)"
  [[ "$NEWPORT" =~ ^[0-9]+$ ]] && [ "$NEWPORT" -ge 1024 ] && [ "$NEWPORT" -le 65535 ] || { err "Invalid port"; return 1; }
  if [ "$NEWPORT" != "$(env_get WEB_PORT)" ] && port_busy "$NEWPORT"; then err "Port $NEWPORT is used by another program."; return 1; fi
  env_set WEB_PORT "$NEWPORT"
  apply_domain "$(env_get DOMAIN)"
  ok "Port changed: $NEWPORT"
}

do_edit_env() {
  need_root; need_installed || return 1
  local ed="${EDITOR:-nano}"; command -v "$ed" >/dev/null 2>&1 || ed=vi
  "$ed" "$ENVF" </dev/tty >/dev/tty 2>&1
  chmod 600 "$ENVF"; chown "$SVC_USER:$SVC_USER" "$ENVF"
  confirm "Restart the bot to apply the new settings?" y && { svc restart; ok "Restarted"; }
}

# ---------------------------------------------------------------- info, service, logs, doctor
db_scalar() {
  [ -f "$DATA/shop.db" ] || { echo "-"; return; }
  "$(helper_python)" - "$DATA/shop.db" "$1" <<'PY' 2>/dev/null || echo "-"
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print(c.execute(sys.argv[2]).fetchone()[0])
PY
}

do_info() {
  need_installed || return 1
  step "Details and version"
  local bu up
  say "Bot version:         $(src_version)"
  say "Script version:      $SCRIPT_VERSION"
  bu=$(env_get BOT_USERNAME); say "Telegram bot:        ${bu:+@$bu  (https://t.me/$bu)}"
  say "Service status:      $(svc_state)"
  if have_systemd; then up=$(systemctl show "$SERVICE" -p ActiveEnterTimestamp --value 2>/dev/null); say "Running since:       ${up:--}"; fi
  say "Web panel URL:       $(env_get PUBLIC_URL)"
  say "Domain:              ${C_W}$(env_get DOMAIN)${C_0}$([ -z "$(env_get DOMAIN)" ] && echo "(none)")"
  say "Internal panel port: $(env_get WEB_PORT)"
  say "Admins (.env):       $(env_get ADMIN_IDS)"
  say "Install path:        $BASE"
  say "Database:            $DATA/shop.db ($(du -h "$DATA/shop.db" 2>/dev/null | cut -f1))"
  say "Users:               $(db_scalar 'select count(*) from users')"
  say "Delivered orders:    $(db_scalar "select count(*) from orders where status='done'")"
  say "Total sales:         $(db_scalar "select coalesce(sum(amount),0) from orders where status='done'")"
  say "Backups:             $(ls -1 "$BACKUPS" 2>/dev/null | wc -l) file(s) | latest: $(ls -1t "$BACKUPS" 2>/dev/null | head -1)"
  say "Automatic backup:    $([ -f /etc/systemd/system/starshop-backup.timer ] && echo enabled || echo disabled)"
  say "Python:              $("$(helper_python)" --version 2>&1)"
  say "aiogram:             $("$VENV/bin/pip" show aiogram 2>/dev/null | awk '/^Version/{print $2}')"
  say "Operating system:    $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
  say "Server IP:           $(public_ip)"
  say "Free disk space:     $(df -h "$BASE" | awk 'NR==2{print $4}')"
  say "Service memory:      $(systemctl show "$SERVICE" -p MemoryCurrent --value 2>/dev/null | awk '{ if ($1+0>0) printf "%.0f MB", $1/1048576; else print "-" }')"
}

do_service() {
  need_root; need_installed || return 1
  step "Service control (status: $(svc_state))"
  say "  1) Start   2) Stop   3) Restart   4) Enable start at boot   5) Disable start at boot"
  ask SV_SEL "Choice" "3"
  case "$SV_SEL" in
    1) svc start ;; 2) svc stop ;; 3) svc restart ;;
    4) svc enable ;; 5) svc disable ;;
    *) err "Invalid option"; return 1 ;;
  esac
  sleep 2; ok "Current status: $(svc_state)"
}

do_logs() {
  need_installed || return 1
  have_systemd || { err "systemd is required."; return 1; }
  say "${C_D}Exit the log with Ctrl+C${C_0}"
  journalctl -u "$SERVICE" -n 80 -f --no-pager
}

do_doctor() {
  need_installed || return 1
  step "Diagnostics (Doctor)"
  local port d ip t r
  port=$(env_get WEB_PORT); d=$(env_get DOMAIN); t=$(env_get BOT_TOKEN)
  svc_active && ok "The bot service is active" || err "The bot service is not active (option 11 shows the log)"
  selftest_import >/dev/null && ok "The code imports without errors" || err "Error importing the code (broken dependencies? try option 2, Update)"
  r=$(tg_getme "$t")
  if [ -z "$r" ]; then err "Cannot reach api.telegram.org (server in a blocked region? firewall?)"
  elif [ "$(printf '%s' "$r" | json_get ok)" = "True" ]; then ok "Telegram connection and token are fine (@$(printf '%s' "$r" | json_get result.username))"
  else err "Telegram rejected the token"; fi
  port_busy "$port" && ok "The web panel is listening on port $port" || err "No program is listening on port $port"
  curl -fs -m 4 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && ok "The web panel responds" || err "The web panel does not respond"
  if [ -n "$d" ]; then
    ip=$(public_ip)
    [ "$(dns_ip "$d")" = "$ip" ] && ok "Domain DNS is correct ($ip)" || warn "Domain DNS does not match the server IP ($ip) (currently: $(dns_ip "$d"))"
    if curl -fsI -m 10 "https://$d/health" >/dev/null 2>&1; then ok "HTTPS and the SSL certificate are working"; else err "https://$d does not open (SSL not issued? ports 80/443 closed? Cloudflare proxy?)"; fi
    caddy_installed && { have_systemd && systemctl is-active --quiet caddy && ok "Caddy is active" || err "Caddy is not active"; }
    port_busy 80 && ! pgrep -x caddy >/dev/null 2>&1 && warn "Port 80 is used by another program (nginx/apache?) which blocks Caddy"
  fi
  local free; free=$(df -Pm "$BASE" | awk 'NR==2{print $4}')
  [ "${free:-0}" -gt 500 ] && ok "Enough disk space (${free}MB)" || warn "Low disk space (${free}MB)"
  [ -n "$(ls -1 "$BACKUPS" 2>/dev/null)" ] && ok "You have at least one backup" || warn "No backups yet (option 7)"
  return 0
}

# ---------------------------------------------------------------- uninstall
do_uninstall() {
  need_root; need_installed || return 1
  step "Completely remove the bot"
  warn "The service, source, database and settings will be deleted."
  if [ "${FORCE:-0}" != 1 ]; then
    local word=""; read -r -p "Type DELETE to confirm: " word <"$TTY"
    [ "$word" = "DELETE" ] || { say "Cancelled."; return 0; }
  fi
  local keep=""
  if [ "${FORCE:-0}" != 1 ] && confirm "Create a final backup and keep it in /root before removing?" y; then
    backup_make && { keep="/root/$(basename "$BACKUP_FILE")"; cp "$BACKUP_FILE" "$keep"; rm -rf "$BACKUP_TMP"; ok "Final backup: $keep"; }
  fi
  if have_systemd; then
    systemctl disable --now "$SERVICE" >/dev/null 2>&1
    systemctl disable --now starshop-backup.timer >/dev/null 2>&1
    rm -f "/etc/systemd/system/$SERVICE.service" /etc/systemd/system/starshop-backup.{service,timer}
    systemctl daemon-reload
  fi
  caddy_remove
  rm -rf "$BASE"
  rm -f "$CMD_PATH"
  id "$SVC_USER" >/dev/null 2>&1 && userdel "$SVC_USER" 2>/dev/null
  ok "The bot was removed."
  [ -n "$keep" ] && say "Your backup: $keep"
  say "${C_D}Caddy and the prerequisites (python/git) were left in place on purpose.${C_0}"
}

# ---------------------------------------------------------------- menu
header() {
  clear 2>/dev/null
  say "${C_B}╔════════════════════════════════════════════════╗${C_0}"
  say "${C_B}║${C_0}   ${C_W}⭐ StarShop Manager${C_0}  v$SCRIPT_VERSION                       ${C_B}║${C_0}"
  say "${C_B}╚════════════════════════════════════════════════╝${C_0}"
  if is_installed; then
    local st; st=$(svc_state)
    say " Status: $([ "$st" = active ] && echo "${C_G}● running${C_0}" || echo "${C_R}● $st${C_0}")   Version: $(src_version)   Bot: @$(env_get BOT_USERNAME)"
    say " Panel: $(env_get PUBLIC_URL)"
  else
    say " ${C_Y}The bot is not installed yet - choose option 1.${C_0}"
  fi
  say ""
}

menu() {
  while true; do
    header
    say "  ${C_W}1)${C_0}  Install the bot (automatic: SSL, service, web panel)"
    say "  ${C_W}2)${C_0}  Update the bot (with backup and automatic rollback on failure)"
    say "  ${C_W}3)${C_0}  Change the bot token"
    say "  ${C_W}4)${C_0}  Change domain / reissue SSL"
    say "  ${C_W}5)${C_0}  Change admins (admin IDs)"
    say "  ${C_W}6)${C_0}  Web panel login details and password"
    say "  ${C_W}7)${C_0}  Create a backup"
    say "  ${C_W}8)${C_0}  Restore a backup (.db, .zip bundle or .tar.gz)"
    say "  ${C_W}9)${C_0}  Details and bot version"
    say "  ${C_W}10)${C_0} Service control (start / stop / restart)"
    say "  ${C_W}11)${C_0} View live log"
    say "  ${C_W}12)${C_0} Daily automatic backup (+ Telegram delivery, encryption)"
    say "  ${C_W}13)${C_0} Change the web panel port"
    say "  ${C_W}14)${C_0} Edit the settings file (.env) by hand"
    say "  ${C_W}15)${C_0} Diagnostics (Doctor)"
    say "  ${C_W}16)${C_0} ${C_R}Completely remove the bot${C_0}"
    say "  ${C_W}17)${C_0} Switch version (choose a release: upgrade or roll back)"
    say "  ${C_W}0)${C_0}  Exit"
    say ""
    local c=""; read -r -p "Choose an option: " c <"$TTY" || exit 0
    # options do not run in a subshell so variables persist; clear earlier inputs:
    unset BOT_TOKEN ADMIN_IDS DOMAIN RESTORE_SEL AB_SEL PL_SEL SV_SEL NEWPORT NEWPW NEWUSER
    case "$c" in
      1) do_install ;; 2) do_update ;; 3) do_change_token ;; 4) do_change_domain ;; 5) do_change_admins ;;
      6) do_panel_login ;; 7) do_backup ;; 8) do_restore ;; 9) do_info ;; 10) do_service ;;
      11) do_logs ;; 12) do_auto_backup ;; 13) do_change_port ;; 14) do_edit_env ;;
      15) do_doctor ;; 16) do_uninstall ;; 17) do_switch_version ;;
      0|q|exit) exit 0 ;;
      *) err "Invalid option" ;;
    esac
    pause
  done
}

usage() {
  cat <<EOF
StarShop Manager $SCRIPT_VERSION
  starshop                interactive menu
  starshop install        install       starshop update      update
  starshop backup         backup        starshop restore     restore (.db / .zip / .tar.gz)
  starshop info           details       starshop doctor      diagnostics
  starshop start|stop|restart|status    service control
  starshop logs           live log      starshop uninstall   remove
  starshop version        choose a release to switch to (upgrade / roll back)
  Specific version:  starshop install v1.1.0   /   starshop update v1.2.0   (a release tag; "update main" returns to the latest)
Non-interactive install: BOT_TOKEN=... ADMIN_IDS=... DOMAIN=... PANEL_PASSWORD=... NONINTERACTIVE=1 starshop install
EOF
}

trap 'echo; exit 130' INT

__cmd="${1:-menu}"
[ -n "$VERSION_ARG" ] && [ "$__cmd" = "$1" ] && [[ "$__cmd" =~ ^v?[0-9]+(\.[0-9]+)+$ ]] && __cmd=install
case "$__cmd" in
  menu)      need_root; menu ;;
  install)   do_install ;;
  update)    do_update ;;
  backup)    do_backup "${2:-}" ;;
  restore)   do_restore ;;
  version|switch) do_switch_version ;;
  info)      do_info ;;
  doctor)    do_doctor ;;
  start|stop|restart) need_root; need_installed && svc "$1" ;;
  status)    need_installed && { svc_state; } ;;
  logs)      do_logs ;;
  uninstall) do_uninstall ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
