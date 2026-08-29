#!/usr/bin/env bash
#
# Read-only inspection of the storage pool and the environments living on it.
# Never mounts, formats, or mutates anything.
#
#   sudo scripts/pool-status.sh [pool-dir]
#
# <pool-dir> defaults to $LAB_POOL_DIR, falling back to the same default as
# .env.example. The per-project quota report needs root and xfsprogs on the host.
set -euo pipefail

POOL_DIR="${1:-${LAB_POOL_DIR:-/var/lib/finki-hub-shell/pool}}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

bold "== Mount =="
if mountpoint -q "$POOL_DIR" 2> /dev/null; then
    findmnt --output TARGET,SOURCE,FSTYPE,OPTIONS "$POOL_DIR"
else
    echo "$POOL_DIR is not mounted."
fi
echo

bold "== Space =="
df -h "$POOL_DIR" 2> /dev/null || echo "df: $POOL_DIR not available"
echo

bold "== Inodes =="
df -i "$POOL_DIR" 2> /dev/null || echo "df -i: $POOL_DIR not available"
echo

bold "== Per-project quota usage =="
if [ ! -f "$POOL_DIR/.pool-id" ]; then
    echo "$POOL_DIR/.pool-id not found; pool is not initialized (see scripts/pool-init.sh)."
elif [ "$(id -u)" -ne 0 ]; then
    echo "xfs_quota needs root; re-run with sudo for the per-project report."
elif ! command -v xfs_quota > /dev/null 2>&1; then
    echo "xfs_quota not found; install xfsprogs on the host."
else
    xfs_quota -x -c "report -p -b -i" "$POOL_DIR"
fi
echo

bold "== Environments on disk =="
if [ -d "$POOL_DIR/users" ]; then
    COUNT=$(find "$POOL_DIR/users" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
    echo "$COUNT environment(s) under $POOL_DIR/users"
    find "$POOL_DIR/users" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
else
    echo "$POOL_DIR/users not found."
fi
echo

bold "== Running lab containers =="
docker ps --filter "label=finki.role=lab" \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Label "finki.user"}}'
