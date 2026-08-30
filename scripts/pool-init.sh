#!/usr/bin/env bash
#
# Initializes the XFS pool backing environment home directories. Run once as
# root before the first `docker compose up`.
#
#   sudo scripts/pool-init.sh <pool-dir> <size>
#
# Example:
#   sudo scripts/pool-init.sh /var/lib/finki-hub-shell/pool 150G
#
# Creates and mounts <pool-dir>/pool.img as XFS with prjquota,nosuid,nodev,
# and noatime, writes the startup .pool-id sentinel, creates users/, and creates
# the external `finki-hub-shell-users` network. Refuses non-empty, configured,
# or already-mounted pool directories.
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <pool-dir> <size>" >&2
    echo "  <size> is anything mkfs/fallocate accepts, e.g. 150G" >&2
    exit 1
fi

POOL_DIR=$1
SIZE=$2

if [ "$(id -u)" -ne 0 ]; then
    echo "pool-init.sh must run as root (it writes /etc/fstab and mounts a filesystem)." >&2
    exit 1
fi

case "$POOL_DIR" in
    /*) ;;
    *)
        echo "pool-init.sh: <pool-dir> must be an absolute path" >&2
        exit 1
        ;;
esac

if ! command -v mkfs.xfs > /dev/null 2>&1; then
    echo "pool-init.sh: mkfs.xfs not found; install xfsprogs on the host first." >&2
    exit 1
fi

IMAGE_FILE="$POOL_DIR/pool.img"

if mountpoint -q "$POOL_DIR" 2> /dev/null; then
    echo "pool-init.sh: $POOL_DIR is already a mountpoint; refusing to reinitialize it." >&2
    echo "Run scripts/pool-status.sh to inspect the existing pool." >&2
    exit 1
fi

if grep -qsE "^[^#[:space:]]+[[:space:]]+${POOL_DIR}[[:space:]]" /etc/fstab; then
    echo "pool-init.sh: /etc/fstab already has an entry for $POOL_DIR; refusing to add another." >&2
    exit 1
fi

if [ -e "$POOL_DIR" ] && [ -n "$(ls -A "$POOL_DIR" 2> /dev/null)" ]; then
    echo "pool-init.sh: $POOL_DIR already exists and is not empty; refusing to reinitialize it." >&2
    exit 1
fi

mkdir -p "$POOL_DIR"

echo "Allocating $SIZE at $IMAGE_FILE ..."
fallocate -l "$SIZE" "$IMAGE_FILE"

echo "Formatting $IMAGE_FILE as XFS ..."
mkfs.xfs -q "$IMAGE_FILE"

LOOP_DEV=$(losetup --show -f "$IMAGE_FILE")
echo "Attached $IMAGE_FILE at $LOOP_DEV"

FSTAB_ENTRY="$IMAGE_FILE $POOL_DIR xfs loop,prjquota,nosuid,nodev,noatime 0 0"
echo "Adding fstab entry:"
echo "  $FSTAB_ENTRY"
printf '%s\n' "$FSTAB_ENTRY" >> /etc/fstab

losetup -d "$LOOP_DEV"

echo "Mounting $POOL_DIR ..."
mount "$POOL_DIR"

if ! grep -q " $POOL_DIR .*prjquota" /proc/self/mountinfo; then
    echo "pool-init.sh: $POOL_DIR did not mount with prjquota active; check dmesg and /etc/fstab." >&2
    exit 1
fi

POOL_ID=$(cat /proc/sys/kernel/random/uuid)
printf '%s' "$POOL_ID" > "$POOL_DIR/.pool-id"
chmod 0400 "$POOL_DIR/.pool-id"
chown root:root "$POOL_DIR/.pool-id"

: > "$POOL_DIR/.projects"
echo 1000 > "$POOL_DIR/.projid-counter"
: > "$POOL_DIR/.lock"
chown root:root "$POOL_DIR/.projects" "$POOL_DIR/.projid-counter" "$POOL_DIR/.lock"
chmod 0600 "$POOL_DIR/.projects" "$POOL_DIR/.projid-counter" "$POOL_DIR/.lock"

mkdir -p "$POOL_DIR/users"
chown root:root "$POOL_DIR/users"
chmod 0755 "$POOL_DIR/users"

# The external network must outlive `docker compose down` because the hub binds
# its gateway (172.30.0.1). `internal: true` and disabled ICC isolate user
# containers from each other, the internet, and host addresses except the gateway.
NETWORK=finki-hub-shell-users

if docker network inspect "$NETWORK" > /dev/null 2>&1; then
    echo "Docker network $NETWORK already exists; leaving it alone."
else
    echo "Creating Docker network $NETWORK ..."
    docker network create \
        --driver bridge \
        --internal \
        --subnet 172.30.0.0/23 \
        --gateway 172.30.0.1 \
        -o com.docker.network.bridge.enable_icc=false \
        -o com.docker.network.bridge.name=br-finki-users \
        "$NETWORK"
fi

echo
echo "Pool ready at $POOL_DIR (pool id $POOL_ID)."
echo "Set LAB_POOL_DIR=$POOL_DIR in .env, then bring the stack up."
