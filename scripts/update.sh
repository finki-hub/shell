#!/bin/sh
# Conservative routine updater. Schema/version changes require the explicit
# maintenance procedure; this command never drains Labs or deletes images.
set -eu

DIR=${1:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
DIR=$(CDPATH= cd -- "$DIR" && pwd)
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required for fail-closed image inspection" >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { echo "flock is required to serialize updates" >&2; exit 1; }
command -v timeout >/dev/null 2>&1 || { echo "timeout is required for bounded Docker operations" >&2; exit 1; }

LOCK=${UPDATE_LOCK_FILE:-/run/lock/finki-hub-shell-update.lock}
exec 9>"$LOCK"
flock -n 9 || { echo "another deployment update or maintenance operation holds $LOCK" >&2; exit 1; }

# Maintenance spans invocations; its marker must be checked before any pull.
if [ -e "$DIR/.jupyterhub-maintenance.json" ]; then
  echo "maintenance interlock is present; routine updates are disabled" >&2
  exit 1
fi

cd "$DIR"
compose() { timeout 30s docker compose --project-directory "$DIR" "$@"; }
compose_pull() { timeout 300s docker compose --project-directory "$DIR" "$@"; }
docker_bounded() { timeout 30s docker "$@"; }
python_bounded() { timeout 30s python3 "$@"; }
compose_pull --profile images pull

# Inspect candidate image JupyterHub version labels.
config_values=$(compose --profile images config --format json | python_bounded -c 'import json,sys; s=json.load(sys.stdin)["services"]; h=s["hub"]; print("\t".join((s["web"]["image"],s["proxy"]["image"],h["image"],s["lab"]["image"],next(v["source"] for v in h["volumes"] if v.get("target")=="/srv/pool"),str(h["environment"]["LAB_USER"]))))')
IFS="$(printf '\t')" read -r candidate_web candidate_proxy candidate_hub candidate_lab expected_pool expected_lab_user <<EOF
$config_values
EOF
image_info() { docker_bounded image inspect --format '{{.Id}}|{{ index .Config.Labels "org.finki-hub.jupyterhub-version" }}' "$1"; }
IFS='|' read -r candidate_web_id _ <<EOF
$(docker_bounded image inspect --format '{{.Id}}' "$candidate_web")
EOF
IFS='|' read -r candidate_proxy_id _ <<EOF
$(docker_bounded image inspect --format '{{.Id}}' "$candidate_proxy")
EOF
IFS='|' read -r candidate_hub_id hub_version <<EOF
$(image_info "$candidate_hub")
EOF
IFS='|' read -r candidate_lab_id lab_version <<EOF
$(image_info "$candidate_lab")
EOF
for candidate_id in "$candidate_web_id" "$candidate_proxy_id" "$candidate_hub_id" "$candidate_lab_id"; do
  case "$candidate_id" in sha256:*) ;; *) echo "could not resolve every candidate service to an immutable image ID" >&2; exit 1;; esac
done
case "$hub_version:$lab_version" in *'<no value>'*|:*|*:) echo "candidate Hub/Lab version metadata is missing; refusing update" >&2; exit 1;; esac
[ "$hub_version" = "$lab_version" ] || { echo "candidate Hub $hub_version and Lab $lab_version do not match" >&2; exit 1; }

# Retain the exact inspected IDs through activation, even if a mutable tag moves.
export WEB_IMAGE="$candidate_web_id"
export PROXY_IMAGE="$candidate_proxy_id"
export HUB_IMAGE="$candidate_hub_id"
export LAB_IMAGE="$candidate_lab_id"
compose --profile images config --format json | python_bounded -c '
import json,sys
s=json.load(sys.stdin)["services"]
expected={"web": __import__("os").environ["WEB_IMAGE"], "proxy": __import__("os").environ["PROXY_IMAGE"], "hub": __import__("os").environ["HUB_IMAGE"], "lab": __import__("os").environ["LAB_IMAGE"]}
if any(s[name].get("image") != image for name,image in expected.items()):
    raise SystemExit("effective Compose layers do not accept immutable activation image pins")
if s["hub"].get("environment", {}).get("LAB_IMAGE") != expected["lab"]:
    raise SystemExit("Hub LAB_IMAGE does not resolve to the inspected immutable Lab image")
'

current_id=$(compose ps -q hub)
[ -n "$current_id" ] || { echo "cannot identify the running Hub; use explicit maintenance" >&2; exit 1; }
current_version=$(docker_bounded inspect --format '{{ index .Config.Labels "org.finki-hub.jupyterhub-version" }}' "$current_id")
[ -n "$current_version" ] && [ "$current_version" != '<no value>' ] || { echo "running Hub version is unknown; refusing update" >&2; exit 1; }
[ "$current_version" = "$hub_version" ] || {
  echo "Hub version change $current_version -> $hub_version may require a schema migration; use only an independently approved and validated maintenance procedure" >&2
  exit 1
}

# Inspect all containers so a canonical Lab whose ownership labels were lost
# cannot evade verification. Unrelated containers are not adopted.
# The inline inspector treats malformed canonical Labs and Docker inspection
# errors as blockers, and ignores unrelated role-labelled decoys.
container_ids=$(docker_bounded ps -aq)
if [ -n "$container_ids" ]; then
  if ! container_inspections=$(
    printf '%s\n' "$container_ids" | timeout 30s xargs docker inspect 2>/dev/null
  ); then
    echo "failed to inspect retained Labs; refusing update" >&2
    exit 1
  fi
  printf '%s\n' "$container_inspections" | CANDIDATE_VERSION="$hub_version" EXPECTED_POOL="$expected_pool" EXPECTED_LAB_USER="$expected_lab_user" python_bounded -c '
import json,sys
import os
import pathlib
items=json.load(sys.stdin)
pool=pathlib.Path(os.environ["EXPECTED_POOL"]).resolve()
for c in items:
    cfg=c.get("Config") or {}; labels=cfg.get("Labels") or {}
    name=(c.get("Name") or "").lstrip("/"); user=labels.get("finki.user")
    if not user and not name.startswith("lab-"): continue
    if labels.get("finki.role") != "lab" or not user or name != "lab-"+user:
        raise SystemExit("cannot establish ownership of retained Lab " + (name or "<unnamed>"))
    mounts=[m for m in c.get("Mounts",[]) if m.get("Destination")=="/home/"+os.environ["EXPECTED_LAB_USER"] and m.get("Type")=="bind"]
    expected=(pool/"users"/user).resolve()
    if expected.parent != (pool/"users").resolve() or len(mounts)!=1 or not mounts[0].get("Source") or pathlib.Path(mounts[0]["Source"]).resolve()!=expected:
        raise SystemExit("canonical Lab has an unverifiable home bind: "+name)
    image=c.get("Image")
    import subprocess
    labels=json.loads(subprocess.run(["timeout","30s","docker","image","inspect",image,"--format","{{json .Config.Labels}}"],check=True,capture_output=True,text=True,timeout=30).stdout)
    v=(labels or {}).get("org.finki-hub.jupyterhub-version")
    if v != os.environ["CANDIDATE_VERSION"]:
        raise SystemExit("retained Lab "+name+" has incompatible/unknown JupyterHub version; use maintenance")
' || exit 1
fi

timeout 180s docker compose --project-directory "$DIR" up -d --wait --wait-timeout 160
