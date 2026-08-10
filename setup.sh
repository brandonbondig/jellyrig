#!/usr/bin/env bash
# Jellyrig installer: takes a clean machine to a stack that is ready to
# download. Asks for your accounts, starts the containers, then wires every
# service to every other service through their APIs.
#
#   sudo ./setup.sh
#
# Re-running is safe: saved answers are reused and finished steps are skipped.
set -euo pipefail
cd "$(dirname "$0")"

# ---------------------------------------------------------------- output

if [[ -t 1 ]]; then
  B=$'\033[1m'; DIM=$'\033[2m'; CYAN=$'\033[36m'; YEL=$'\033[33m'
  GRN=$'\033[32m'; RED=$'\033[31m'; R=$'\033[0m'
else
  B=""; DIM=""; CYAN=""; YEL=""; GRN=""; RED=""; R=""
fi

rule()  { printf '%s\n' "${DIM}──────────────────────────────────────────────────────────────${R}"; }
step()  { printf '\n%s\n' "${B}${CYAN}$1${R}"; rule; }
info()  { printf '%s\n' "${DIM}  $1${R}"; }
say()   { printf '\n%s %s\n' "${CYAN}==>${R}" "$1"; }
good()  { printf '%s %s\n' "  ${GRN}✓${R}" "$1"; }
warn()  { printf '%s %s\n' "  ${YEL}!${R}" "$1"; }
die()   { printf '\n%s %s\n\n' "${RED}  ✗${R}" "$1"; exit 1; }

# ---------------------------------------------------------------- checks

command -v docker >/dev/null || die "Docker is not installed. Install it first, then re-run."
docker compose version >/dev/null 2>&1 || die "The Docker compose plugin is missing."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start it, then re-run."
command -v python3 >/dev/null || die "python3 is required — it configures the services for you."

# ---------------------------------------------------------------- prompts

saved() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

# Prefer the controlling terminal (so prompts still work when the script is
# piped), but fall back to stdin when there isn't one - /dev/tty can exist
# and still be unusable, so probe it rather than test for the file.
if exec 3</dev/tty 2>/dev/null; then HAVE_TTY=1; exec 3<&-; else HAVE_TTY=0; fi

read_input() {
  local flag="$1" var="$2"
  if [[ "$HAVE_TTY" == 1 ]]; then
    if [[ "$flag" == "-s" ]]; then read -rs "$var" </dev/tty; else read -r "$var" </dev/tty; fi
  else
    if [[ "$flag" == "-s" ]]; then read -rs "$var"; else read -r "$var"; fi
  fi
}

# ask VAR "Label" "grey hint, or empty" "default" [secret]
ask() {
  local var="$1" label="$2" hint="${3:-}" def="${4:-}" secret="${5:-}" cur val shown
  cur="$(saved "$var")"
  [[ -n "$cur" ]] && def="$cur"

  [[ -n "$hint" ]] && printf '%s\n' "${DIM}  $hint${R}"
  if [[ "$secret" == secret ]]; then
    shown="${def:+ ${DIM}[keeping saved]${R}}"
  else
    shown="${def:+ ${DIM}[$def]${R}}"
  fi

  if [[ -n "${JELLYRIG_NONINTERACTIVE:-}" ]]; then
    val="$def"
  else
    printf '  %s%s: ' "$label" "$shown"
    if [[ "$secret" == secret ]]; then
      read_input -s val
      echo
    else
      read_input "" val
    fi
    [[ -z "$val" ]] && val="$def"
  fi
  printf -v "$var" '%s' "$val"
}

detect_tz() {
  local tz=""
  if command -v timedatectl >/dev/null 2>&1; then
    tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  fi
  if [[ -z "$tz" && -L /etc/localtime ]]; then
    tz="$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
  fi
  [[ -z "$tz" && -f /etc/timezone ]] && tz="$(cat /etc/timezone)"
  echo "${tz:-Etc/UTC}"
}

printf '%s' "${B}${CYAN}"
cat <<'BANNER'

   ╭──────────────────────────────╮
   │   J E L L Y R I G            │
   ╰──────────────────────────────╯
BANNER
printf '%s' "${R}"
cat <<BANNER
  ${B}Your own streaming service${R} — Jellyfin, Seerr, Sonarr, Radarr, Prowlarr,
  SABnzbd, Bazarr, Recyclarr and Jellystat, wired together for you.

  ${DIM}A few questions, then it does the rest. Takes 3-5 minutes.
  Answers are saved to .env so you only ever type them once.${R}
BANNER

step "Step 1 of 4 · Storage"
info "Downloads and your library must sit on ONE filesystem. On one, an"
info "import is an instant rename; split across two, every import turns"
info "into a slow full copy. Pick somewhere with room to grow."
echo
ask DATA_ROOT   "Storage root " "" "/data"
ask CONFIG_ROOT "Config folder" "Every service's settings and database live here — worth backing up." "./config"
ask TZ          "Timezone     " "" "$(detect_tz)"

step "Step 2 of 4 · Usenet provider"
info "Who you download from, over one encrypted connection. You need an"
info "account first — Eweka, Newshosting and Frugal are common choices,"
info "around €3-8/month. Press Enter to skip and add it in SABnzbd later."
echo
ask USENET_HOST "Server host  " "From your provider's welcome email, e.g. news.eweka.nl" ""
ask USENET_PORT "Port         " "563 is the standard SSL port — keep it unless told otherwise." "563"
ask USENET_USER "Username     " "" ""
ask USENET_PASS "Password     " "" "" secret

step "Step 3 of 4 · Indexer"
info "The search engine that tells the automation where things are."
info "NZBgeek is the usual first choice (~\$15/year); its API key is on"
info "your account page. Press Enter to skip and add it in Prowlarr later."
echo
ask INDEXER_NAME    "Indexer name " "Must match Prowlarr's own spelling of it." "NZBgeek"
ask INDEXER_API_KEY "API key      " "" "" secret

step "Step 4 of 4 · Your account"
info "Created in Jellyfin and reused for Seerr, so it is one login for"
info "both. This is what you and your household will sign in with."
echo
ask ADMIN_USER "Username     " "" "admin"
ask ADMIN_PASS "Password     " "Choose a real one — this account can reach your whole library." "" secret

# ---------------------------------------------------------------- accounts

if [[ -z "${PUID:-}" ]]; then
  if id media >/dev/null 2>&1; then
    PUID="$(id -u media)"; PGID="$(id -g media)"
  elif [[ "$(id -u)" -eq 0 ]] && command -v useradd >/dev/null 2>&1 \
       && [[ -z "${JELLYRIG_NONINTERACTIVE:-}" ]]; then
    step "Service account"
    info "Running the containers as a dedicated user keeps file ownership"
    info "tidy and limits what they can reach."
    echo
    printf '  Create a "media" user? %s: ' "${DIM}[Y/n]${R}"
    read_input "" a
    if [[ "${a:-Y}" =~ ^[Yy]?$ ]]; then
      if useradd -r -s /usr/sbin/nologin media 2>/dev/null; then
        PUID="$(id -u media)"; PGID="$(id -g media)"
        good "created user 'media' (uid $PUID)"
      else
        warn "could not create it — using your own account instead"
      fi
    fi
  fi
  owner="${SUDO_USER:-$(id -un)}"
  PUID="${PUID:-$(id -u "$owner")}"
  PGID="${PGID:-$(id -g "$owner")}"
fi

# generated once, preserved across re-runs
JELLYSTAT_DB_USER="$(saved JELLYSTAT_DB_USER)"
JELLYSTAT_DB_USER="${JELLYSTAT_DB_USER:-jellystat}"
JELLYSTAT_DB_PASSWORD="$(saved JELLYSTAT_DB_PASSWORD)"
JELLYSTAT_DB_PASSWORD="${JELLYSTAT_DB_PASSWORD:-$(openssl rand -hex 16)}"
JELLYSTAT_JWT_SECRET="$(saved JELLYSTAT_JWT_SECRET)"
JELLYSTAT_JWT_SECRET="${JELLYSTAT_JWT_SECRET:-$(openssl rand -hex 32)}"

# ---------------------------------------------------------------- GPU

COMPOSE_FILES=(docker-compose.yml)
GPU_NOTE="none — software transcoding (fine for 1-2 streams)"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  if docker info 2>/dev/null | grep -qi nvidia || command -v nvidia-ctk >/dev/null 2>&1; then
    COMPOSE_FILES+=(docker-compose.gpu.yml)
    GPU_NOTE="NVIDIA — hardware transcoding will be enabled"
  else
    GPU_NOTE="${YEL}NVIDIA driver found, but nvidia-container-toolkit is missing (README §8)${R}"
  fi
elif command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qi nvidia; then
  GPU_NOTE="${YEL}NVIDIA card present but the driver isn't working — check nvidia-smi (README §8)${R}"
fi

# ---------------------------------------------------------------- confirm

step "Ready to install"
printf '  %-11s %s\n' "storage"  "$DATA_ROOT ${DIM}(downloads + library)${R}"
printf '  %-11s %s\n' "config"   "$CONFIG_ROOT"
printf '  %-11s %s\n' "timezone" "$TZ"
if [[ -n "$USENET_HOST" ]]; then
  printf '  %-11s %s\n' "provider" "$USENET_HOST:$USENET_PORT ${DIM}as ${USENET_USER:-?}${R}"
else
  printf '  %-11s %s\n' "provider" "${YEL}not set${R} ${DIM}— add it in SABnzbd later${R}"
fi
if [[ -n "$INDEXER_API_KEY" ]]; then
  printf '  %-11s %s\n' "indexer" "$INDEXER_NAME"
else
  printf '  %-11s %s\n' "indexer" "${YEL}not set${R} ${DIM}— add it in Prowlarr later${R}"
fi
printf '  %-11s %s\n' "login"   "$ADMIN_USER"
printf '  %-11s %s\n' "runs as" "uid $PUID / gid $PGID"
printf '  %-11s %s\n' "gpu"     "$GPU_NOTE"
echo
if [[ -z "${JELLYRIG_NONINTERACTIVE:-}" ]]; then
  printf '  Continue? %s: ' "${DIM}[Y/n]${R}"
  read_input "" go
  [[ "${go:-Y}" =~ ^[Yy]?$ ]] || { say "Nothing was changed."; exit 0; }
fi

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

# ---------------------------------------------------------------- install

step "Installing"

mkdir -p \
  "$DATA_ROOT"/usenet/incomplete "$DATA_ROOT"/usenet/complete \
  "$DATA_ROOT"/media/movies "$DATA_ROOT"/media/tv \
  "$CONFIG_ROOT"/{sabnzbd,prowlarr,sonarr,radarr,bazarr,jellyfin,jellyfin-cache,seerr,jellystat-db,recyclarr} \
  "$CONFIG_ROOT"/jellystat/backup-data
if [[ "$(id -u)" -eq 0 ]]; then
  chown -R "$PUID:$PGID" "$DATA_ROOT" "$CONFIG_ROOT"
  chmod -R u=rwX,g=rwX,o=rX "$DATA_ROOT"
fi
good "folders ready under $DATA_ROOT"

busy=""
for port in 8080 9696 8989 7878 6767 8096 5055 3000; do
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN && busy+=" $port"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && busy+=" $port"
  fi
done
if [[ -n "$busy" ]]; then
  echo
  warn "these ports are already in use:$busy"
  info "Something else on this machine owns them. Stop it, or change the"
  info "left-hand number in 'ports:' in docker-compose.yml, then re-run."
  exit 1
fi
good "ports are free"

COMPOSE_FILE="$(IFS=:; echo "${COMPOSE_FILES[*]}")"
export COMPOSE_FILE
docker compose up -d --quiet-pull >/dev/null 2>&1
good "containers started"

say "Connecting the services to each other — a minute or two."
rc=0
python3 setup/wire.py || rc=$?

host_addr="$(hostname -I 2>/dev/null | awk '{print $1}')"
host_addr="${host_addr:-localhost}"
step "Done"
if [[ $rc -eq 0 ]]; then
  printf '  %s\n\n' "Your stack is ready to download."
else
  printf '  %s\n\n' "${YEL}Some steps need attention — see above, fix them, and re-run.${R}"
fi
printf '  %-20s %s\n' "Request things here" "${B}http://$host_addr:5055${R} ${DIM}Seerr${R}"
printf '  %-20s %s\n' "Watch them here"     "${B}http://$host_addr:8096${R} ${DIM}Jellyfin${R}"
printf '  %-20s %s\n' "Sign in as"          "$ADMIN_USER"
echo
info "Try it now: request a popular film in Seerr and watch it arrive."
echo
