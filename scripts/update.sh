#!/bin/sh
#
# Pull the current images and recreate whatever moved. Safe to run on a timer:
# `docker compose up -d` recreates only the services whose image changed, and a
# hub restart never stops a running user container (cleanup_servers = False).
#
#   scripts/update.sh [compose-dir]
#
# <compose-dir> defaults to the directory holding this script's parent, i.e. the
# repository checkout that contains compose.yaml and .env.
set -eu

DIR=${1:-$(dirname "$(dirname "$0")")}
cd "$DIR"

# --profile images includes the never-started `lab` service, so the user
# container image is pulled here rather than by the hub at spawn time.
docker compose --profile images pull
docker compose up -d
docker image prune -f
