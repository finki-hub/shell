#!/bin/sh
#
# Pull current images and recreate changed services. Safe on a timer: `docker
# compose up -d` leaves unchanged services and running user containers alone
# (`cleanup_servers = False`).
#
#   scripts/update.sh [compose-dir]
#
# <compose-dir> defaults to the directory holding this script's parent, i.e. the
# repository checkout that contains compose.yaml and .env.
set -eu

DIR=${1:-$(dirname "$(dirname "$0")")}
cd "$DIR"

# Pull the never-started `lab` service image before the hub spawns users.
docker compose --profile images pull
docker compose up -d
docker image prune -f
