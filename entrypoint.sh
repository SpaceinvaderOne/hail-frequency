#!/bin/bash
set -e

# Data dir must match HF_DATA_DIR (Unraid template sets /data; compose uses /app/config)
DATA_DIR="${HF_DATA_DIR:-/app/config}"
PUID="${PUID:-99}"
PGID="${PGID:-100}"

# Remap the runtime user to the requested UID/GID (Unraid: 99/100 = nobody/users)
groupmod -o -g "$PGID" hailfrequency
usermod -o -u "$PUID" hailfrequency

# Ensure the data dir exists, is owned by the runtime user, and is private
# (the SQLite DB inside holds the media server API key)
mkdir -p "$DATA_DIR"
chown -R hailfrequency:hailfrequency "$DATA_DIR"
chmod 700 "$DATA_DIR"

# Drop to non-root user and exec the CMD
exec gosu hailfrequency "$@"
