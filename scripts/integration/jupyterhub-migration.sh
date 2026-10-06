#!/usr/bin/env bash
# Full acceptance runs only inside a fresh, ephemeral GitHub-hosted Linux VM.
set -Eeuo pipefail
umask 077
if [[ ${1:-} != run ]]; then
  echo 'Usage: jupyterhub-migration.sh run --acknowledge-disposable --acknowledge-interruption --acknowledge-ingress-fenced --acknowledge-updater-paused --workspace ABSOLUTE_PATH' >&2
  exit 2
fi
shift
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/run_suite.py" "$@"
