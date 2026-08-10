#!/usr/bin/env bash
# Jellyrig installer: one run takes you from a clean machine to a stack
# that's ready to download. It asks for your accounts, starts the
# containers, then wires every service together through their APIs.
#
#   sudo ./setup.sh
#
# Re-running is safe: existing answers in .env are kept, and the wiring
# step skips anything already configured.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m ! \033[0m %s\n' "$*"; }

command -v docker >/dev/null || { warn "docker is not installed - install Docker first."; exit 1; }
docker compose version >/dev/null 2>&1 || { warn "docker compose plugin missing."; exit 1; }
command -v python3 >/dev/null || { warn "python3 is required."; exit 1; }

# ---------------------------------------------------------------- prompts

# ask VAR "question" "default" [secret]
ask() {
  local var="$1" q="$2" def="${3:-}" secret="${4:-}" cur val
  cur="$(grep -E "^${var}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
  [[ -n "$cur" ]] && def="$cur"
  if [[ -n "${JELLYRIG_NONINTERACTIVE:-}" ]]; then
    val="$def"
  elif [[ "$secret" == secret ]]; then
    read -rs -p "$q${def:+ [saved]}: " val </dev/tty; echo
    [[ -z "$val" ]] && val="$def"
  else
    read -r -p "$q${def:+ [$def]}: " val </dev/tty
    [[ -z "$val" ]] && val="$def"
  fi
  printf -v "$var" '%s' "$val"
}

detect_tz() { timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo Etc/UTC; }

say "Jellyrig setup - answers are saved to .env (never committed)."
echo

ask DATA_ROOT   "Storage root (downloads AND library, ONE filesystem)" "/data"
ask CONFIG_ROOT "Config folder" "./config"
ask TZ          "Timezone" "$(detect_tz)"

echo
say "Usenet provider (e.g. Eweka, Newshosting)"
ask USENET_HOST "  server host (e.g. news.eweka.nl)" ""
ask USENET_PORT "  port" "563"
ask USENET_USER "  username" ""
ask USENET_PASS "  password" "" secret

echo
say "Indexer (the Usenet search engine, e.g. NZBgeek)"
ask INDEXER_NAME    "  indexer name as on prowlarr's list" "NZBgeek"
ask INDEXER_API_KEY "  its API key (empty = skip, add later in Prowlarr)" ""

echo
say "Your admin account (used for Jellyfin and Seerr)"
ask ADMIN_USER "  username" "admin"
ask ADMIN_PASS "  password" "" secret

# service account ------------------------------------------------------
if [[ -z "${PUID:-}" ]]; then
  if id media >/dev/null 2>&1; then
    PUID="$(id -u media)"; PGID="$(id -g media)"
  elif [[ "$(id -u)" -eq 0 ]] && [[ -z "${JELLYRIG_NONINTERACTIVE:-}" ]]; then
    read -r -p "Create dedicated 'media' service user? [Y/n]: " a </dev/tty
    if [[ "${a:-Y}" =~ ^[Yy]?$ ]]; then
      useradd -r -s /usr/sbin/nologin media
      PUID="$(id -u media)"; PGID="$(id -g media)"
    fi
  fi
  PUID="${PUID:-$(id -u "${SUDO_USER:-$(id -un)}")}"
  PGID="${PGID:-$(id -g "${SUDO_USER:-$(id -un)}")}"
fi

# secrets: generate once, keep on re-run
JELLYSTAT_DB_USER="$(grep -E '^JELLYSTAT_DB_USER=' .env 2>/dev/null | cut -d= -f2- || true)"
JELLYSTAT_DB_USER="${JELLYSTAT_DB_USER:-jellystat}"
JELLYSTAT_DB_PASSWORD="$(grep -E '^JELLYSTAT_DB_PASSWORD=' .env 2>/dev/null | cut -d= -f2- || true)"
JELLYSTAT_DB_PASSWORD="${JELLYSTAT_DB_PASSWORD:-$(openssl rand -hex 16)}"
JELLYSTAT_JWT_SECRET="$(grep -E '^JELLYSTAT_JWT_SECRET=' .env 2>/dev/null | cut -d= -f2- || true)"
JELLYSTAT_JWT_SECRET="${JELLYSTAT_JWT_SECRET:-$(openssl rand -hex 32)}"

cat > .env <<EOF
PUID=$PUID
PGID=$PGID
TZ=$TZ
DATA_ROOT=$DATA_ROOT
CONFIG_ROOT=$CONFIG_ROOT
JELLYSTAT_DB_USER=$JELLYSTAT_DB_USER
JELLYSTAT_DB_PASSWORD=$JELLYSTAT_DB_PASSWORD
JELLYSTAT_JWT_SECRET=$JELLYSTAT_JWT_SECRET
USENET_HOST=$USENET_HOST
USENET_PORT=$USENET_PORT
USENET_USER=$USENET_USER
USENET_PASS=$USENET_PASS
INDEXER_NAME=$INDEXER_NAME
INDEXER_API_KEY=$INDEXER_API_KEY
ADMIN_USER=$ADMIN_USER
ADMIN_PASS=$ADMIN_PASS
EOF
chmod 600 .env
say ".env written"

# ---------------------------------------------------------------- dirs

say "Creating directories"
mkdir -p \
  "$DATA_ROOT"/usenet/incomplete "$DATA_ROOT"/usenet/complete \
  "$DATA_ROOT"/media/movies "$DATA_ROOT"/media/tv \
  "$CONFIG_ROOT"/{sabnzbd,prowlarr,sonarr,radarr,bazarr,jellyfin,jellyfin-cache,seerr,jellystat-db,recyclarr} \
  "$CONFIG_ROOT"/jellystat/backup-data

if [[ "$(id -u)" -eq 0 ]]; then
  chown -R "$PUID:$PGID" "$DATA_ROOT" "$CONFIG_ROOT"
  chmod -R u=rwX,g=rwX,o=rX "$DATA_ROOT"
else
  warn "not root - skipped chown; containers must be able to write as $PUID:$PGID"
fi

# ---------------------------------------------------------------- GPU

COMPOSE_FILES=(docker-compose.yml)
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  if docker info 2>/dev/null | grep -qi nvidia || command -v nvidia-ctk >/dev/null 2>&1; then
    say "NVIDIA GPU + container toolkit detected - enabling hardware transcoding"
    COMPOSE_FILES+=(docker-compose.gpu.yml)
  else
    warn "NVIDIA driver found but nvidia-container-toolkit is missing."
    warn "Install it, then re-run this script (README section 8). Continuing CPU-only."
  fi
elif lspci 2>/dev/null | grep -qi 'nvidia'; then
  warn "NVIDIA GPU present but no working driver (nvidia-smi failed)."
  warn "If Secure Boot is on, install the SIGNED modules - README section 8."
  warn "Continuing CPU-only; re-run after installing the driver."
fi

# ---------------------------------------------------------------- up + wire

# a port already in use fails the whole `up` with a cryptic message, so
# name the culprit before we start
busy=""
for port in 8080 9696 8989 7878 6767 8096 5055 3000; do
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN && busy+=" $port"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && busy+=" $port"
  fi
done
if [[ -n "$busy" ]]; then
  warn "these ports are already in use:$busy"
  warn "Stop whatever owns them, or remap the left-hand side of 'ports:' in"
  warn "docker-compose.yml, then re-run. (See README section 7.)"
  exit 1
fi

say "Starting containers"
# COMPOSE_FILE is honoured by every later `docker compose` call, including
# the ones wire.py makes to discover published ports
COMPOSE_FILE="$(IFS=:; echo "${COMPOSE_FILES[*]}")"
export COMPOSE_FILE
docker compose up -d --quiet-pull

say "Wiring services together (this takes a minute or two)"
python3 setup/wire.py

echo
say "Done. Open Seerr and request something:  http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo localhost):5055"
