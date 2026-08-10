#!/usr/bin/env bash
# Creates the /data tree and config directories with the right ownership.
# Run once before first `docker compose up`:   sudo ./setup.sh
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "No .env found - run:  cp .env.example .env  and edit it first." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

DATA_ROOT="${DATA_ROOT:-/data}"
CONFIG_ROOT="${CONFIG_ROOT:-./config}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

echo "data:   $DATA_ROOT (owner ${PUID}:${PGID})"
echo "config: $CONFIG_ROOT"

# TRaSH-style layout: downloads and library on ONE filesystem so imports are
# instant renames/hardlinks, never copies.
mkdir -p \
  "$DATA_ROOT"/usenet/incomplete \
  "$DATA_ROOT"/usenet/complete \
  "$DATA_ROOT"/media/movies \
  "$DATA_ROOT"/media/tv \
  "$DATA_ROOT"/audiobooks \
  "$DATA_ROOT"/books

mkdir -p \
  "$CONFIG_ROOT"/sabnzbd \
  "$CONFIG_ROOT"/prowlarr \
  "$CONFIG_ROOT"/sonarr \
  "$CONFIG_ROOT"/radarr \
  "$CONFIG_ROOT"/bazarr \
  "$CONFIG_ROOT"/jellyfin \
  "$CONFIG_ROOT"/jellyfin-cache \
  "$CONFIG_ROOT"/seerr \
  "$CONFIG_ROOT"/jellystat-db \
  "$CONFIG_ROOT"/jellystat/backup-data \
  "$CONFIG_ROOT"/recyclarr \
  "$CONFIG_ROOT"/audiobookshelf/config \
  "$CONFIG_ROOT"/audiobookshelf/metadata \
  "$CONFIG_ROOT"/shelfarr

if [[ "$(id -u)" -eq 0 ]]; then
  chown -R "$PUID:$PGID" "$DATA_ROOT" "$CONFIG_ROOT"
  chmod -R u=rwX,g=rwX,o=rX "$DATA_ROOT"
else
  echo "note: not running as root - skipped chown. If PUID/PGID isn't your"
  echo "      own user, re-run with sudo so the containers can write."
fi

echo "done. Next:  docker compose up -d   (see README for first-run wiring)"
