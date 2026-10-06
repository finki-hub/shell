#!/bin/sh
# Finite, fail-closed maintenance entry point. All state changes are implemented
# and tested in the stdlib-only Python controller.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or newer is required" >&2
  exit 1
fi
exec python3 "$SCRIPT_DIR/jupyterhub_maintenance.py" "$@"
