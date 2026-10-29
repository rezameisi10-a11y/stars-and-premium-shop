#!/usr/bin/env bash
# =====================================================================
#  StarHub - multi-tenant manager for the StarShop bot
#  Create one bot per customer on a single server, each in its own folder
#  (own token, domain, admin IDs, database, port and systemd service),
#  with per-customer backup / restore / delete.
#
#  Run as root on the server, from the folder that holds the bot files:
#      bash starhub.sh            (first run installs the "starhub" command)
#  Afterwards just type:  starhub
# =====================================================================
HUB_VERSION="1.0.0"

HUB="${STARHUB_BASE:-/opt/starhub}"
CMD_PATH="${STARHUB_CMD:-/usr/local/bin/starhub}"
REPO="${STARHUB_REPO:-rezameisi10-a11y/stars-and-premium-shop}"
BRANCH="${STARHUB_BRANCH:-main}"
SRC="$HUB/source"
VENV="$HUB/venv"
TENANTS="$HUB/tenants"
BACKUPS="$HUB/backups"
WEBDIR="$HUB/web"
WEB_UNIT="starhub-web"
WEB_PORT="${STARHUB_WEB_PORT:-8090}"
SVC_USER="starhub"
UNIT="starhub@"
CADDY_DIR="/etc/caddy/conf.d"
BASE_PORT="${STARHUB_BASE_PORT:-8101}"
KEEP="${STARHUB_KEEP:-7}"
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "$0")"
SELF_DIR="$(dirname "$SELF")"

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
ask() {   # ask VAR "prompt" [default]; skipped when VAR is already set
  local __v="$1" __p="$2" __d="${3-}" __in=""
  [ -n "${!__v:-}" ] && return 0
  if [ "${NONINTERACTIVE:-0}" = 1 ]; then printf -v "$__v" '%s' "$__d"; return 0; fi
  if [ -n "$__d" ]; then read -r -p "$__p [$__d]: " __in <"$TTY"; else read -r -p "$__p: " __in <"$TTY"; fi
  printf -v "$__v" '%s' "${__in:-$__d}"
}
confirm() {
  [ "${YES:-0}" = 1 ] && return 0
  local d="${2:-n}" a="" hint="y/N"
  [ "$d" = y ] && hint="Y/n"
  read -r -p "$1 [$hint]: " a <"$TTY"
  a="${a:-$d}"; [[ "$a" =~ ^[Yy] ]]
}
pause() { [ "${NONINTERACTIVE:-0}" = 1 ] && return 0; read -r -p "Press Enter to continue..." _ <"$TTY"; }
rand_hex()  { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
rand_pass() { head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16; }
need_root() { [ "$(id -u)" -eq 0 ] || die "Run as root (sudo -i)."; }
safe_val() { case "$1" in *\'*|*$'\n'*|*$'\r'*) return 1 ;; esac; return 0; }
now() { date +%Y%m%d-%H%M%S; }

have_systemd() { [ "${STARHUB_NO_SYSTEMD:-0}" != 1 ] && command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; }
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
public_ip() { local i; i=$(curl -s -m 8 https://api.ipify.org 2>/dev/null || curl -s -m 8 https://ifconfig.me 2>/dev/null); [ -z "$i" ] && i=$(hostname -I 2>/dev/null | awk '{print $1}'); printf '%s' "${i:-127.0.0.1}"; }
dns_ip()    { getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1{print $1}'; }
port_busy() {
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
fw_open() {
  command -v ufw >/dev/null 2>&1 || return 0
  ufw status 2>/dev/null | grep -q "Status: active" || return 0
  local p; for p in "$@"; do ufw allow "$p/tcp" >/dev/null 2>&1; done
}

# ---------------------------------------------------------------- tenant paths and .env
valid_name() { [[ "$1" =~ ^[a-z][a-z0-9_-]{1,30}$ ]]; }
tdir()   { printf '%s/%s' "$TENANTS" "$1"; }
tenv()   { printf '%s/%s/.env' "$TENANTS" "$1"; }
exists() { [ -f "$(tenv "$1")" ]; }
tenant_names() { [ -d "$TENANTS" ] && find "$TENANTS" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort; }
env_get() {   # env_get NAME KEY
  local f; f="$(tenv "$1")"; [ -f "$f" ] || return 0
  local l; l=$(grep -E "^$2=" "$f" | tail -1) || true
  l="${l#*=}"; l="${l%\'}"; l="${l#\'}"; printf '%s' "$l"
}
env_set() {   # env_set NAME KEY VALUE
  local f tmp; f="$(tenv "$1")"
  safe_val "$3" || die "The value of $2 must not contain ' or a newline."
  tmp=$(mktemp); touch "$f"
  grep -vE "^$2=" "$f" > "$tmp" || true
  printf "%s='%s'\n" "$2" "$3" >> "$tmp"
  cat "$tmp" > "$f"; rm -f "$tmp"; chmod 600 "$f"
  fix_owner "$1"
}
fix_owner() { id "$SVC_USER" >/dev/null 2>&1 && chown -R "$SVC_USER:$SVC_USER" "$(tdir "$1")" 2>/dev/null; return 0; }

svc_name()   { printf '%s%s' "$UNIT" "$1"; }
svc()        { if have_systemd; then systemctl "$1" "$(svc_name "$2")" >/dev/null 2>&1; else warn "systemd unavailable ($1 $2)"; return 0; fi; }
svc_state()  { if have_systemd; then systemctl is-active "$(svc_name "$1")" 2>/dev/null || true; else echo "no-systemd"; fi; }

check_token() {
  local t="$1" r
  [[ "$t" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]] || { err "Invalid token format (example: 123456789:AA...)"; return 1; }
  BOT_USERNAME=""
  [ "${SKIP_TOKEN_CHECK:-0}" = 1 ] && return 0
  r=$(curl -s -m 15 "https://api.telegram.org/bot$t/getMe")
  if [ -z "$r" ]; then
    warn "Could not reach api.telegram.org (blocked server?)."
    confirm "Continue without verifying the token?" n || return 1
    return 0
  fi
  [ "$(printf '%s' "$r" | json_get ok)" = "True" ] || { err "Telegram rejected the token."; return 1; }
  BOT_USERNAME=$(printf '%s' "$r" | json_get result.username)
  ok "Token is valid: @$BOT_USERNAME"
}
check_admins() { [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]] || { err "Admin IDs must be numeric, comma-separated (e.g. 123456,789012)."; return 1; }; }
token_in_use() {   # prints the tenant name using the token ($2 = tenant to ignore)
  local n; for n in $(tenant_names); do
    [ "$n" = "${2:-}" ] && continue
    [ "$(env_get "$n" BOT_TOKEN)" = "$1" ] && { printf '%s' "$n"; return 0; }
  done; return 1
}
domain_in_use() {
  local n; for n in $(tenant_names); do
    [ "$n" = "${2:-}" ] && continue
    [ -n "$1" ] && [ "$(env_get "$n" HUB_DOMAIN)" = "$1" ] && { printf '%s' "$n"; return 0; }
  done; return 1
}
alloc_port() {
  local p="$BASE_PORT" used n
  used=" $(for n in $(tenant_names); do env_get "$n" WEB_PORT; printf ' '; done) "
  while [[ "$used" == *" $p "* ]] || port_busy "$p"; do p=$((p+1)); done
  printf '%s' "$p"
}

# ---------------------------------------------------------------- Caddy (automatic SSL)
caddy_installed() { command -v caddy >/dev/null 2>&1; }
install_caddy() {
  caddy_installed && return 0
  [ "${STARHUB_NO_CADDY:-0}" = 1 ] && return 1
  step "Installing Caddy (automatic SSL)"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y debian-keyring debian-archive-keyring apt-transport-https gpg curl >/dev/null 2>&1
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null
    apt-get update -y >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y caddy >/dev/null 2>&1
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y 'dnf-command(copr)' >/dev/null 2>&1; dnf copr enable -y @caddy/caddy >/dev/null 2>&1; dnf install -y caddy >/dev/null 2>&1
  fi
  caddy_installed && ok "Caddy installed" || { err "Caddy installation failed."; return 1; }
}
caddy_reload() {
  [ -f /etc/caddy/Caddyfile ] || echo "# managed" > /etc/caddy/Caddyfile
  grep -q 'conf.d/\*.caddy' /etc/caddy/Caddyfile || printf '\nimport /etc/caddy/conf.d/*.caddy\n' >> /etc/caddy/Caddyfile
  if caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
    have_systemd && { systemctl enable caddy >/dev/null 2>&1; systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1; }
    return 0
  fi
  err "Invalid Caddy configuration (check $CADDY_DIR)."; return 1
}
caddy_write() {   # name domain port
  mkdir -p "$CADDY_DIR"
  cat > "$CADDY_DIR/starhub-$1.caddy" <<EOF
$2 {
	encode gzip
	reverse_proxy 127.0.0.1:$3
}
EOF
  caddy_reload
}
caddy_remove() { [ -f "$CADDY_DIR/starhub-$1.caddy" ] || return 0; rm -f "$CADDY_DIR/starhub-$1.caddy"; caddy_installed && caddy_reload; return 0; }

# ---------------------------------------------------------------- base setup (once)
find_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY="$c"; return 0; fi
  done; return 1
}
pkg_install() {
  [ "${STARHUB_NO_PKG:-0}" = 1 ] && return 0
  if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" >/dev/null
  elif command -v dnf >/dev/null 2>&1; then dnf install -y "$@" >/dev/null
  elif command -v yum >/dev/null 2>&1; then yum install -y "$@" >/dev/null
  else die "No supported package manager (apt/dnf/yum)."; fi
}
copy_code() {   # copy_code FROM TO  (only runtime files)
  local f
  mkdir -p "$2"
  for f in "$1"/*.py "$1"/miniapp.html "$1"/button_catalog.json "$1"/VERSION "$1"/requirements.txt; do
    [ "$(basename "$f")" = starhub_web.py ] && continue   # dashboard of the manager itself, not part of the bot
    [ -f "$f" ] && cp -f "$f" "$2/"
  done
  rm -rf "$2/locales"; [ -d "$1/locales" ] && cp -r "$1/locales" "$2/locales"
  return 0
}
fill_source() {   # populate $SRC from the folder next to this script, or from git
  if [ -f "$SELF_DIR/main.py" ] && [ -f "$SELF_DIR/requirements.txt" ]; then
    copy_code "$SELF_DIR" "$SRC"; ok "Bot source copied from $SELF_DIR (version $(cat "$SRC/VERSION" 2>/dev/null))"
  else
    command -v git >/dev/null 2>&1 || pkg_install git
    local tmp; tmp=$(mktemp -d)
    git clone -q --depth 1 -b "$BRANCH" "${STARHUB_GIT_URL:-https://github.com/$REPO.git}" "$tmp/r" || { rm -rf "$tmp"; die "Could not download the bot from GitHub (is the repo public?)."; }
    copy_code "$tmp/r" "$SRC"; web_sync "$tmp/r"
    [ -f "$tmp/r/starhub.sh" ] && cp -f "$tmp/r/starhub.sh" "$HUB/starhub.sh"
    rm -rf "$tmp"; ok "Bot source downloaded (version $(cat "$SRC/VERSION" 2>/dev/null))"
  fi
  [ -f "$SRC/main.py" ] || die "main.py is missing from the source."
}
write_unit() {
  have_systemd || return 0
  cat > "/etc/systemd/system/${UNIT}.service" <<EOF
[Unit]
Description=StarShop bot (%i)
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC_USER
WorkingDirectory=$TENANTS/%i/app
EnvironmentFile=$TENANTS/%i/.env
ExecStart=$VENV/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
}
is_setup() { [ -x "$VENV/bin/python" ] && [ -f "$SRC/main.py" ]; }
do_setup() {
  need_root
  step "Installing prerequisites"
  if command -v apt-get >/dev/null 2>&1; then pkg_install python3 python3-venv python3-pip git curl ca-certificates sqlite3 openssl iproute2 tar
  else pkg_install python3 python3-pip git curl ca-certificates sqlite openssl iproute tar; fi
  find_python || die "Python 3.10+ is required."
  id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$HUB" --shell /usr/sbin/nologin "$SVC_USER" 2>/dev/null || useradd -r -d "$HUB" -s /sbin/nologin "$SVC_USER"
  mkdir -p "$TENANTS" "$BACKUPS"
  fill_source
  web_sync
  if [ ! -x "$VENV/bin/python" ]; then
    step "Creating the shared Python environment"
    "$PY" -m venv "$VENV" || die "Could not create the virtual environment (python3-venv installed?)."
  fi
  if [ "${STARHUB_NO_PIP:-0}" != 1 ]; then
    "$VENV/bin/pip" install -q --upgrade pip >/dev/null 2>&1
    "$VENV/bin/pip" install -q -r "$SRC/requirements.txt" || die "Installing the requirements failed."
  fi
  write_unit
  web_restart_if_active
  if [ "$SELF" != "$CMD_PATH" ]; then
    # when started as  bash <(curl ...)  the script is a pipe and cannot be copied; use the copy from the download
    if [ -f "$SELF" ] && head -c 2 "$SELF" 2>/dev/null | grep -q '#!'; then cp -f "$SELF" "$CMD_PATH"
    elif [ -f "$HUB/starhub.sh" ]; then cp -f "$HUB/starhub.sh" "$CMD_PATH"
    else err "Could not install the starhub command (starhub.sh not found). Download the files and run: bash starhub.sh"; fi
    [ -f "$CMD_PATH" ] && chmod +x "$CMD_PATH" && ok "Command installed: type  starhub"
  fi
  chown "$SVC_USER:$SVC_USER" "$HUB" "$TENANTS" 2>/dev/null; chmod 755 "$HUB"; chmod 750 "$TENANTS"
  chmod 700 "$BACKUPS"
  ok "Base setup finished."
}
need_setup() { is_setup || { say "First-time setup is required..."; do_setup || return 1; }; }

# ---------------------------------------------------------------- tenant: create
wait_up() {   # port
  local i; for i in $(seq 1 30); do curl -s -m 2 -o /dev/null "http://127.0.0.1:$1/" && return 0; sleep 1; done; return 1
}
do_new() {   # args: [name] [token] [domain] [admins]
  need_root; need_setup || return 1
  local name="${1:-}" token="${2:-}" domain="${3:-}" admins="${4:-}"
  ask name "Customer name (folder name: a-z 0-9 _ -)"
  valid_name "$name" || { err "Invalid name. Use 2-31 chars: lowercase letters, digits, _ or -, starting with a letter."; return 1; }
  exists "$name" && { err "A customer named '$name' already exists."; return 1; }
  [ -e "$(tdir "$name")" ] && { err "The folder $(tdir "$name") already exists."; return 1; }
  ask token "Bot token (from @BotFather)"
  check_token "$token" || return 1
  local o; o=$(token_in_use "$token") && { err "This token is already used by customer '$o' (two bots cannot share a token)."; return 1; }
  ask admins "Admin Telegram numeric ID(s), comma-separated"
  check_admins "$admins" || return 1
  ask domain "Domain for the web panel (empty = no domain, uses IP:port)" ""
  domain="${domain#http://}"; domain="${domain#https://}"; domain="${domain%%/*}"
  if [ -n "$domain" ]; then
    [[ "$domain" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || { err "Invalid domain."; return 1; }
    o=$(domain_in_use "$domain") && { err "Domain already used by customer '$o'."; return 1; }
    local ip dip; ip=$(public_ip); dip=$(dns_ip "$domain")
    if [ -z "$dip" ]; then warn "$domain does not resolve yet; point its A record to ${ip:-this server} or SSL will fail."
    elif [ -n "$ip" ] && [ "$dip" != "$ip" ]; then warn "$domain resolves to $dip but this server is $ip."; fi
  fi
  local port pass; port=$(alloc_port); pass=$(rand_pass)
  local d; d="$(tdir "$name")"
  step "Creating customer '$name'"
  mkdir -p "$d/data" "$d/app" || return 1
  copy_code "$SRC" "$d/app"
  {
    printf "BOT_TOKEN='%s'\n" "$token"
    printf "ADMIN_IDS='%s'\n" "$admins"
    if [ -n "$domain" ]; then printf "PUBLIC_URL='https://%s'\nWEB_HOST='127.0.0.1'\nTRUST_PROXY='1'\n" "$domain"
    else printf "PUBLIC_URL='http://%s:%s'\nWEB_HOST='0.0.0.0'\n" "$(public_ip)" "$port"; fi
    printf "WEB_PORT='%s'\nWEB_USER='admin'\nWEB_PASSWORD='%s'\nWEB_SECRET='%s'\n" "$port" "$pass" "$(rand_hex)"
    printf "DB_PATH='%s/data/shop.db'\n" "$d"
    printf "HUB_DOMAIN='%s'\nHUB_BOT_USERNAME='%s'\nHUB_CREATED='%s'\n" "$domain" "${BOT_USERNAME:-}" "$(date +%F)"
  } > "$d/.env"
  chmod 600 "$d/.env"; fix_owner "$name"; chmod 750 "$d"
  [ -z "$domain" ] && warn "No domain: the panel login will travel over plain HTTP. Use a domain (HTTPS) for real use."
  if [ -n "$domain" ]; then
    if install_caddy && caddy_write "$name" "$domain" "$port"; then fw_open 80 443; else warn "Caddy not configured; the panel is on 127.0.0.1:$port only."; fi
  else fw_open "$port"; fi
  if have_systemd; then systemctl enable "$(svc_name "$name")" >/dev/null 2>&1; svc start "$name"; fi
  if have_systemd; then
    wait_up "$port" && ok "Bot is running" || warn "The bot did not answer yet; check: starhub logs $name"
  fi
  say ""
  say "${C_W}Customer created${C_0}"
  say "  Name:      $name"
  say "  Folder:    $d"
  say "  Bot:       ${BOT_USERNAME:+@$BOT_USERNAME}"
  say "  Panel:     $(env_get "$name" PUBLIC_URL)   (user: admin  password: $pass)"
  say "  Port:      $port"
}

# ---------------------------------------------------------------- tenant: list / info
pick() {   # pick [name]  -> sets T
  T="${1:-}"
  if [ -z "$T" ]; then
    local arr=() n i=1; for n in $(tenant_names); do arr+=("$n"); done
    [ ${#arr[@]} -eq 0 ] && { err "There are no customers yet."; return 1; }
    for n in "${arr[@]}"; do printf '  %s) %s\n' "$i" "$n"; i=$((i+1)); done
    local c=""; read -r -p "Choose a customer (number or name): " c <"$TTY"
    if [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -le ${#arr[@]} ]; then T="${arr[$((c-1))]}"; else T="$c"; fi
  fi
  exists "$T" || { err "Customer '$T' not found."; return 1; }
}
do_list() {
  local n any=0
  printf '%s%-18s %-10s %-6s %-28s %-8s %s%s\n' "$C_W" NAME STATE PORT DOMAIN VERSION BOT "$C_0"
  for n in $(tenant_names); do
    exists "$n" || continue; any=1
    printf '%-18s %-10s %-6s %-28s %-8s %s\n' "$n" "$(svc_state "$n")" "$(env_get "$n" WEB_PORT)" "$(env_get "$n" HUB_DOMAIN)" "$(cat "$(tdir "$n")/app/VERSION" 2>/dev/null)" "$(env_get "$n" HUB_BOT_USERNAME)"
  done
  [ $any = 0 ] && say "(no customers yet)"
  return 0
}
do_info() {
  pick "$1" || return 1
  local n="$T" d; d="$(tdir "$T")"
  say "${C_W}$n${C_0}"
  say "  Folder:   $d"
  say "  State:    $(svc_state "$n")"
  say "  Bot:      @$(env_get "$n" HUB_BOT_USERNAME)"
  say "  Admins:   $(env_get "$n" ADMIN_IDS)"
  say "  Panel:    $(env_get "$n" PUBLIC_URL)   (user: $(env_get "$n" WEB_USER))"
  say "  Port:     $(env_get "$n" WEB_PORT)"
  say "  Version:  $(cat "$d/app/VERSION" 2>/dev/null)"
  say "  DB size:  $(du -h "$d/data/shop.db" 2>/dev/null | cut -f1)"
  say "  Backups:  $(find "$BACKUPS/$n" -name '*.tar.gz' 2>/dev/null | wc -l) (latest: $(ls -1t "$BACKUPS/$n"/*.tar.gz 2>/dev/null | head -1 | xargs -r basename))"
}

# ---------------------------------------------------------------- tenant: control
do_ctl() {   # action [name]
  need_root
  local a="$1"; pick "${2:-}" || return 1
  case "$a" in
    start|stop|restart) svc "$a" "$T"; ok "$a: $T ($(svc_state "$T"))" ;;
    enable) have_systemd && systemctl enable "$(svc_name "$T")" >/dev/null 2>&1 ;;
  esac
}
do_logs() { pick "${1:-}" || return 1; have_systemd && journalctl -u "$(svc_name "$T")" -n "${2:-80}" --no-pager || warn "systemd/journalctl unavailable"; }

do_edit() {   # edit what name value
  need_root
  local what="${1:-}" name="${2:-}" val="${3:-}"
  pick "$name" || return 1; name="$T"
  if [ -z "$what" ]; then
    say "  1) Change bot token"; say "  2) Change domain"; say "  3) Change admin IDs"; say "  4) Reset panel password"
    local c=""; read -r -p "Choose: " c <"$TTY"
    case "$c" in 1) what=token;; 2) what=domain;; 3) what=admins;; 4) what=password;; *) return 0;; esac
  fi
  case "$what" in
    token)
      ask val "New bot token"; check_token "$val" || return 1
      local o; o=$(token_in_use "$val" "$name") && { err "Token is used by '$o'."; return 1; }
      env_set "$name" BOT_TOKEN "$val"; env_set "$name" HUB_BOT_USERNAME "${BOT_USERNAME:-}" ;;
    admins) ask val "Admin IDs (comma-separated)"; check_admins "$val" || return 1; env_set "$name" ADMIN_IDS "$val" ;;
    password) val=$(rand_pass); env_set "$name" WEB_PASSWORD "$val"; say "New panel password: $val" ;;
    domain)
      ask val "New domain (empty = none)" ""
      val="${val#http://}"; val="${val#https://}"; val="${val%%/*}"
      local port; port=$(env_get "$name" WEB_PORT)
      if [ -n "$val" ]; then
        local o; o=$(domain_in_use "$val" "$name") && { err "Domain used by '$o'."; return 1; }
        env_set "$name" PUBLIC_URL "https://$val"; env_set "$name" WEB_HOST "127.0.0.1"; env_set "$name" TRUST_PROXY 1
        install_caddy && caddy_write "$name" "$val" "$port" && fw_open 80 443
      else
        caddy_remove "$name"
        env_set "$name" PUBLIC_URL "http://$(public_ip):$port"; env_set "$name" WEB_HOST "0.0.0.0"; env_set "$name" TRUST_PROXY 0; fw_open "$port"
      fi
      env_set "$name" HUB_DOMAIN "$val" ;;
    *) err "Unknown setting: $what"; return 1 ;;
  esac
  svc restart "$name"; ok "Updated '$name' and restarted it."
}

# ---------------------------------------------------------------- backup / restore / delete
db_snapshot() {   # src dst  - consistent copy even while the bot is running
  python3 - "$1" "$2" <<'PY'
import sqlite3, sys
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2])
s.backup(d); d.close(); s.close()
PY
}
backup_make() {   # name [outdir]  -> BACKUP_FILE
  local n="$1" out="${2:-$BACKUPS/$1}" d tmp f
  d="$(tdir "$n")"; tmp=$(mktemp -d); mkdir -p "$out"; chmod 700 "$BACKUPS" 2>/dev/null
  mkdir -p "$tmp/data"
  cp -f "$d/.env" "$tmp/.env"
  [ -d "$d/data" ] && cp -a "$d/data/." "$tmp/data/" 2>/dev/null
  rm -f "$tmp/data"/shop.db "$tmp/data"/shop.db-wal "$tmp/data"/shop.db-shm
  if [ -f "$d/data/shop.db" ]; then db_snapshot "$d/data/shop.db" "$tmp/data/shop.db" || { rm -rf "$tmp"; err "Database snapshot failed."; return 1; }; fi
  printf 'name=%s\nversion=%s\ncreated=%s\nhub=%s\n' "$n" "$(cat "$d/app/VERSION" 2>/dev/null)" "$(date -Is)" "$HUB_VERSION" > "$tmp/INFO"
  f="$out/$n-$(now).tar.gz"
  tar -czf "$f" -C "$tmp" . || { rm -rf "$tmp"; return 1; }
  chmod 600 "$f"; rm -rf "$tmp"; BACKUP_FILE="$f"
}
backup_rotate() { local n="$1" k="${2:-$KEEP}"; ls -1t "$BACKUPS/$n"/*.tar.gz 2>/dev/null | tail -n +$((k+1)) | xargs -r rm -f; }
do_backup() {   # [name|all]
  need_root
  local a="${1:-}"
  if [ "$a" = all ]; then
    local n c=0; for n in $(tenant_names); do exists "$n" || continue; backup_make "$n" && { ok "$n -> $BACKUP_FILE"; backup_rotate "$n"; c=$((c+1)); }; done
    say "Backed up $c customer(s)."; return 0
  fi
  pick "$a" || return 1
  backup_make "$T" || return 1; backup_rotate "$T"
  ok "Backup saved: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
}
do_backups() {   # list
  local n; for n in $(ls -1 "$BACKUPS" 2>/dev/null); do
    [ "$n" = _deleted ] && continue
    ls -1t "$BACKUPS/$n"/*.tar.gz 2>/dev/null | while read -r f; do printf '%-16s %s  %s\n' "$n" "$(du -h "$f" | cut -f1)" "$f"; done
  done
  ls -1t "$BACKUPS/_deleted"/*.tar.gz 2>/dev/null | while read -r f; do printf '%-16s %s  %s\n' "(deleted)" "$(du -h "$f" | cut -f1)" "$f"; done
  return 0
}
do_restore() {   # [file] [target-name]
  need_root; need_setup || return 1
  local f="${1:-}" target="${2:-}"
  if [ -z "$f" ]; then
    local arr=() i=1 x
    while read -r x; do [ -n "$x" ] && arr+=("$x"); done < <(ls -1t "$BACKUPS"/*/*.tar.gz "$BACKUPS"/_deleted/*.tar.gz 2>/dev/null | awk '!s[$0]++')
    [ ${#arr[@]} -eq 0 ] && { err "No backups found in $BACKUPS. You can also pass a file path."; return 1; }
    for x in "${arr[@]}"; do printf '  %s) %s (%s)\n' "$i" "$(basename "$x")" "$(du -h "$x" | cut -f1)"; i=$((i+1)); done
    local c=""; read -r -p "Choose a backup (number or file path): " c <"$TTY"
    if [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -le ${#arr[@]} ]; then f="${arr[$((c-1))]}"; else f="$c"; fi
  fi
  [ -f "$f" ] || { err "File not found: $f"; return 1; }
  local tmp; tmp=$(mktemp -d)
  if tar -tzvf "$f" 2>/dev/null | awk '{print substr($1,1,1)}' | grep -qE '^[lh]' || tar -tzf "$f" 2>/dev/null | grep -qE '(^/|(^|/)\.\.(/|$))'; then
    rm -rf "$tmp"; err "Unsafe archive (links or paths outside the folder) - refusing to restore."; return 1
  fi
  tar -xzf "$f" -C "$tmp" --no-same-owner 2>/dev/null || { rm -rf "$tmp"; err "Not a valid backup archive."; return 1; }
  [ -f "$tmp/.env" ] || { rm -rf "$tmp"; err "The backup has no .env (not a StarHub backup)."; return 1; }
  if [ -f "$tmp/data/shop.db" ]; then
    python3 - "$tmp/data/shop.db" <<'PY' || { rm -rf "$tmp"; err "The database in the backup is corrupted."; return 1; }
import sqlite3, sys
r = sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()
sys.exit(0 if r and r[0] == "ok" else 1)
PY
  fi
  local orig; orig=$(grep -E '^name=' "$tmp/INFO" 2>/dev/null | cut -d= -f2)
  ask target "Restore into customer name (existing = overwrite its data, new = create)" "${orig:-}"
  valid_name "$target" || { rm -rf "$tmp"; err "Invalid name."; return 1; }
  local d; d="$(tdir "$target")"
  if exists "$target"; then
    warn "Customer '$target' exists: its database and data files will be replaced (a safety backup is taken first)."
    confirm "Continue?" n || { rm -rf "$tmp"; return 1; }
    backup_make "$target" "$BACKUPS/$target" && ok "Safety backup: $BACKUP_FILE"
    svc stop "$target"
    local keep_env=1
    case "${RESTORE_ENV:-}" in
      replace) keep_env=0 ;;
      keep) ;;
      *) confirm "Also replace token/domain/admins with the ones stored in the backup?" n && keep_env=0 ;;
    esac
    rm -rf "$d/data"; mkdir -p "$d/data"; cp -a "$tmp/data/." "$d/data/"
    if [ $keep_env = 0 ]; then
      local port db; port=$(env_get "$target" WEB_PORT)
      cp -f "$tmp/.env" "$d/.env"; env_set "$target" WEB_PORT "$port"; env_set "$target" DB_PATH "$d/data/shop.db"
    fi
    copy_code "$SRC" "$d/app"
    fix_owner "$target"; svc start "$target"
    ok "Restored into '$target'."
  else
    local tok o; tok=$(grep -E '^BOT_TOKEN=' "$tmp/.env" | head -1 | cut -d= -f2- | sed "s/^'//;s/'$//")
    mkdir -p "$d/data" "$d/app"; cp -a "$tmp/data/." "$d/data/"; cp -f "$tmp/.env" "$d/.env"
    local port; port=$(alloc_port)
    env_set "$target" WEB_PORT "$port"; env_set "$target" DB_PATH "$d/data/shop.db"
    copy_code "$SRC" "$d/app"; fix_owner "$target"
    local dom; dom=$(env_get "$target" HUB_DOMAIN)
    o=$(token_in_use "$tok" "$target") && warn "The restored token is also used by '$o'. Change the token (starhub edit token $target) before starting, or stop '$o' first."
    if [ -n "$dom" ] && o=$(domain_in_use "$dom" "$target"); then
      warn "Domain $dom is used by '$o'; clearing it for '$target' (set a new one with: starhub edit domain $target)."
      env_set "$target" HUB_DOMAIN ""; env_set "$target" PUBLIC_URL "http://$(public_ip):$port"; env_set "$target" WEB_HOST 0.0.0.0; dom=""
    fi
    if [ -n "$dom" ]; then install_caddy && caddy_write "$target" "$dom" "$port" && fw_open 80 443; else fw_open "$port"; fi
    if [ -z "${o:-}" ]; then have_systemd && systemctl enable "$(svc_name "$target")" >/dev/null 2>&1; svc start "$target"; ok "Created and started '$target' from the backup."
    else ok "Created '$target' but did NOT start it (token conflict)."; fi
  fi
  rm -rf "$tmp"
}
do_delete() {   # [name]
  need_root; pick "${1:-}" || return 1
  local n="$T" d; d="$(tdir "$T")"
  warn "This deletes customer '$n': the bot, its folder, database and domain config."
  say  "A final backup is saved to $BACKUPS/_deleted/ first."
  if [ "${YES:-0}" != 1 ]; then
    local c=""; read -r -p "Type the customer name ($n) to confirm: " c <"$TTY"
    [ "$c" = "$n" ] || { say "Cancelled."; return 1; }
  fi
  backup_make "$n" "$BACKUPS/_deleted" || { err "Final backup failed; nothing was deleted."; return 1; }
  ok "Final backup: $BACKUP_FILE"
  svc stop "$n"; have_systemd && systemctl disable "$(svc_name "$n")" >/dev/null 2>&1
  caddy_remove "$n"
  rm -rf "$d"
  ok "Customer '$n' deleted. Its old backups remain in $BACKUPS/$n/."
}

# ---------------------------------------------------------------- update / auto-backup / doctor
do_update() {   # update [name|all]  - refreshes code from $SRC (data and .env untouched)
  need_root; is_setup || { err "Run setup first."; return 1; }
  local a="${1:-all}" n list
  if [ "$a" = pull ]; then fill_source; a="${2:-all}"; fi
  [ "$a" = all ] && list="$(tenant_names)" || { exists "$a" || { err "Customer not found."; return 1; }; list="$a"; }
  [ -f "$SRC/requirements.txt" ] && [ "${STARHUB_NO_PIP:-0}" != 1 ] && "$VENV/bin/pip" install -q -r "$SRC/requirements.txt"
  web_sync
  for n in $list; do
    backup_make "$n" >/dev/null && backup_rotate "$n"
    copy_code "$SRC" "$(tdir "$n")/app"; fix_owner "$n"; svc restart "$n"
    ok "$n updated to $(cat "$SRC/VERSION" 2>/dev/null)"
  done
  web_restart_if_active
}
do_cron() {   # on|off
  need_root
  case "${1:-}" in
    on) printf '30 3 * * * root %s autobackup >/dev/null 2>&1\n' "$CMD_PATH" > /etc/cron.d/starhub; chmod 644 /etc/cron.d/starhub; ok "Daily backups at 03:30 (last $KEEP kept per customer)." ;;
    off) rm -f /etc/cron.d/starhub; ok "Automatic backups disabled." ;;
    *) [ -f /etc/cron.d/starhub ] && say "Automatic backups: ON" || say "Automatic backups: OFF"; say "Use: starhub cron on|off" ;;
  esac
}
do_doctor() {
  local n; [ -x "$VENV/bin/python" ] && ok "Shared Python environment OK" || err "Missing: $VENV"
  [ -f "$SRC/main.py" ] && ok "Bot source: version $(cat "$SRC/VERSION" 2>/dev/null)" || err "Missing: $SRC"
  caddy_installed && ok "Caddy installed" || warn "Caddy is not installed (needed for domains)"
  for n in $(tenant_names); do
    exists "$n" || continue
    printf '  %-16s service=%-10s http=' "$n" "$(svc_state "$n")"
    curl -s -m 3 -o /dev/null "http://127.0.0.1:$(env_get "$n" WEB_PORT)/" && echo up || echo down
  done
}

# ---------------------------------------------------------------- web dashboard (optional)
web_sync() {   # web_sync [dir...]: copy starhub_web.py from the first dir that has it (or from next to this script)
  local d f=""
  for d in "$@" "$SELF_DIR" "$HUB"; do [ -f "$d/starhub_web.py" ] && { f="$d/starhub_web.py"; break; }; done
  [ -n "$f" ] || return 0
  mkdir -p "$WEBDIR"; chmod 700 "$WEBDIR"
  cmp -s "$f" "$WEBDIR/starhub_web.py" || { cp -f "$f" "$WEBDIR/starhub_web.py"; WEB_CHANGED=1; }
  chmod 644 "$WEBDIR/starhub_web.py"
}
web_active() { have_systemd && systemctl is-active --quiet "$WEB_UNIT"; }
web_restart_if_active() {   # when called by the dashboard itself, restart only if its code changed and do not block (it would kill this process)
  web_active || return 0
  if [ "${STARHUB_FROM_WEB:-0}" = 1 ]; then
    [ "${WEB_CHANGED:-0}" = 1 ] && systemctl restart --no-block "$WEB_UNIT" >/dev/null 2>&1
    return 0
  fi
  systemctl restart "$WEB_UNIT" && ok "Web dashboard restarted"; return 0
}
web_python() {   # a python that has aiohttp
  local p; for p in "$VENV/bin/python" python3; do command -v "$p" >/dev/null 2>&1 && "$p" -c 'import aiohttp' 2>/dev/null && { printf '%s' "$p"; return 0; }; done
  return 1
}
web_envget() { [ -f "$WEBDIR/web.env" ] && grep -E "^$1=" "$WEBDIR/web.env" | tail -1 | cut -d= -f2-; return 0; }
web_set_pass() {   # web_set_pass PASSWORD
  local py; py=$(web_python) || { err "aiohttp is not available (run: starhub setup)."; return 1; }
  mkdir -p "$WEBDIR"; chmod 700 "$WEBDIR"
  STARHUB_BASE="$HUB" STARHUB_WEB_PASSWORD="$1" "$py" "$WEBDIR/starhub_web.py" --set-password >/dev/null || { err "Could not save the password (min 8 characters)."; return 1; }
}
do_web() {   # web on [domain] | off | status | passwd [password]
  need_root
  local a="${1:-status}"
  case "$a" in
    on)
      need_setup || return 1
      web_sync; [ -f "$WEBDIR/starhub_web.py" ] || die "starhub_web.py not found: put it next to starhub.sh and run: bash starhub.sh setup"
      local domain="${2:-${STARHUB_WEB_DOMAIN:-}}"
      ask domain "Domain for the dashboard (empty = local only, reach it through an SSH tunnel)" ""
      domain="${domain#http://}"; domain="${domain#https://}"; domain="${domain%%/*}"
      if [ -n "$domain" ] && ! [[ "$domain" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then err "Invalid domain."; return 1; fi
      local newpass=""
      if [ ! -f "$WEBDIR/auth.json" ]; then newpass=$(rand_pass); web_set_pass "$newpass" || return 1; fi
      web_python >/dev/null || { err "aiohttp is missing in the shared environment (run: starhub setup)."; return 1; }
      cat > "$WEBDIR/web.env" <<EOF
STARHUB_BASE=$HUB
STARHUB_CMD=$CMD_PATH
STARHUB_CLI=$CMD_PATH
STARHUB_WEB_TRUST_PROXY=$([ -n "$domain" ] && echo 1 || echo 0)
STARHUB_WEB_HOST=127.0.0.1
STARHUB_WEB_PORT=$WEB_PORT
STARHUB_WEB_SECURE=$([ -n "$domain" ] && echo 1 || echo 0)
STARHUB_WEB_DOMAIN=$domain
EOF
      chmod 600 "$WEBDIR/web.env"
      if [ -n "$domain" ]; then
        if install_caddy; then
          mkdir -p "$CADDY_DIR"
          printf '%s {\n\tencode gzip\n\treverse_proxy 127.0.0.1:%s\n}\n' "$domain" "$WEB_PORT" > "$CADDY_DIR/hub-dashboard.caddy"
          caddy_reload && fw_open 80 443
        else warn "Caddy is not available; the dashboard is only on 127.0.0.1:$WEB_PORT."; fi
      else rm -f "$CADDY_DIR/hub-dashboard.caddy" 2>/dev/null; fi
      if have_systemd; then
        cat > "/etc/systemd/system/${WEB_UNIT}.service" <<EOF
[Unit]
Description=StarHub web dashboard
After=network-online.target

[Service]
User=root
EnvironmentFile=$WEBDIR/web.env
ExecStart=$(web_python) $WEBDIR/starhub_web.py serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload; systemctl enable "$WEB_UNIT" >/dev/null 2>&1; systemctl restart "$WEB_UNIT"
        sleep 1; web_active && ok "Web dashboard is running" || err "The dashboard did not start: journalctl -u $WEB_UNIT -n 30"
      else
        warn "systemd is not available. Run it manually:  set -a; . $WEBDIR/web.env; set +a; $(web_python) $WEBDIR/starhub_web.py serve"
      fi
      say ""
      if [ -n "$domain" ]; then say "  Address:  https://$domain"
      else say "  Local only (127.0.0.1:$WEB_PORT). From your computer run:"; say "    ssh -L $WEB_PORT:127.0.0.1:$WEB_PORT root@$(public_ip)"; say "  then open  http://127.0.0.1:$WEB_PORT"; fi
      if [ -n "$newpass" ]; then say "  Password: $newpass   (change it: starhub web passwd)"; fi
      ;;
    off)
      have_systemd && { systemctl disable --now "$WEB_UNIT" >/dev/null 2>&1; rm -f "/etc/systemd/system/${WEB_UNIT}.service"; systemctl daemon-reload; }
      if [ -f "$CADDY_DIR/hub-dashboard.caddy" ]; then rm -f "$CADDY_DIR/hub-dashboard.caddy"; caddy_installed && caddy_reload; fi
      ok "Web dashboard turned off (the password is kept)." ;;
    passwd|password)
      local p="${2:-${STARHUB_WEB_PASS:-}}" shown=0
      [ -z "$p" ] && { p=$(rand_pass); shown=1; }
      web_set_pass "$p" || return 1
      ok "Dashboard password changed."; if [ $shown = 1 ]; then say "  New password: $p"; fi ;;
    status)
      if web_active; then ok "Dashboard: running"; else say "Dashboard: not running ($([ -f "$WEBDIR/web.env" ] && echo configured || echo 'not configured; run: starhub web on'))"; fi
      local dm; dm=$(web_envget STARHUB_WEB_DOMAIN)
      if [ -f "$WEBDIR/web.env" ]; then say "  Address: $([ -n "$dm" ] && echo "https://$dm" || echo "http://127.0.0.1:$(web_envget STARHUB_WEB_PORT) (SSH tunnel)")"; fi ;;
    *) err "Usage: starhub web on [domain] | off | status | password [password]"; return 1 ;;
  esac
}

# ---------------------------------------------------------------- menu
menu() {
  while true; do
    clear 2>/dev/null
    printf '%sStarHub%s v%s   %s\n\n' "$C_W" "$C_0" "$HUB_VERSION" "$HUB"
    do_list; echo
    cat <<'EOF'
  1) New customer           7) Backup a customer
  2) Customer info          8) Backup all customers
  3) Start / stop / restart 9) Restore a backup
  4) Logs                  10) Delete a customer
  5) Change token/domain/  11) Update bot code
     admins/password       12) Automatic daily backups
  6) List backups          13) Doctor
                           14) Web dashboard (on/off)
  0) Exit
EOF
    local c=""; read -r -p "> " c <"$TTY"
    case "$c" in
      1) do_new ;;
      2) do_info "" ;;
      3) pick "" && { local a=""; read -r -p "start / stop / restart: " a <"$TTY"; do_ctl "$a" "$T"; } ;;
      4) do_logs "" ;;
      5) do_edit "" "" ;;
      6) do_backups ;;
      7) do_backup "" ;;
      8) do_backup all ;;
      9) do_restore ;;
      10) do_delete "" ;;
      11) confirm "Re-download the code from GitHub first (n = use the local copy)?" n && do_update pull all || do_update all ;;
      12) do_cron; if [ -f /etc/cron.d/starhub ]; then confirm "Turn OFF?" n && do_cron off; else confirm "Turn ON?" y && do_cron on; fi ;;
      13) do_doctor ;;
      14) local w=""; read -r -p "on / off / status / password: " w <"$TTY"; do_web "$w" ;;
      0|q) exit 0 ;;
    esac
    echo; pause
  done
}

usage() {
  cat <<EOF
StarHub $HUB_VERSION - one server, many bots

  starhub                         interactive menu
  starhub setup                   install prerequisites and the shared environment
  starhub new [name token domain admins]
  starhub list | info [name]
  starhub start|stop|restart [name]
  starhub logs [name] [lines]
  starhub edit token|domain|admins|password [name] [value]
  starhub backup [name|all]       starhub backups
  starhub restore [file] [name]
  starhub delete [name]           (a final backup is kept in backups/_deleted)
  starhub update [pull] [name|all]
  starhub autobackup              backup everything and rotate (used by cron)
  starhub cron on|off
  starhub doctor
  starhub web on [domain] | off | status | password [pw]   browser dashboard
EOF
}

case "${1:-}" in
  "")        need_root; is_setup || do_setup; menu ;;
  setup)     do_setup ;;
  new)       shift; do_new "$@" ;;
  list|ls)   do_list ;;
  info)      do_info "${2:-}" ;;
  start|stop|restart) do_ctl "$1" "${2:-}" ;;
  logs)      do_logs "${2:-}" "${3:-80}" ;;
  edit)      do_edit "${2:-}" "${3:-}" "${4:-}" ;;
  backup)    do_backup "${2:-}" ;;
  backups)   do_backups ;;
  restore)   do_restore "${2:-}" "${3:-}" ;;
  delete|rm) do_delete "${2:-}" ;;
  update)    shift; do_update "$@" ;;
  autobackup) need_root; do_backup all ;;
  cron)      do_cron "${2:-}" ;;
  doctor)    do_doctor ;;
  web)       do_web "${2:-status}" "${3:-}" ;;
  -h|--help|help) usage ;;
  *)         usage; exit 1 ;;
esac
