#!/usr/bin/env python3
"""End-to-end PR17 acceptance on one empty ephemeral rootful Linux Docker VM.

All Docker/filesystem work is scoped to a private run root with an ownership
manifest. This program intentionally refuses shared/nonempty Docker daemons.
It does not access SSH, production, credentials, or GitHub APIs.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Literal

BASELINE_SHA = "6f682ee17c8affa988deffa44c56f2e39e28e462"
VERSION_LABEL = "org.finki-hub.jupyterhub-version"
RUN_LABEL = "shell.integration.run"
PROJECT = "finki-hub-shell"
NETWORK_NAME = "finki-hub-shell-users"
BRIDGE_NAME = "br-finki-users"
POOL_SIZE = 1024**3
MIN_FREE = 25 * 1024**3
BUILD_TIMEOUT = 1200
UPDATE_TIMEOUT = 600
DB_TIMEOUT = 1800
PROBE_TIMEOUT = 1230
STAGE_TIMEOUT = 1800
OVERALL_TEST_TIMEOUT = 4800
IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[a-f0-9]{12}$")
FULL_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
FS_IOC_FSGETXATTR = 0x801C581F


class HarnessFailure(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def readiness_is_ready(status: int, payload: bytes) -> bool:
    if status != 200 or len(payload) > 1024:
        return False
    try:
        body = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(body, dict) and body.get("ready") is True


def validate_db_evidence(facts: dict[str, Any]) -> None:
    expected_passes = (
        "migration_disabled_cold_startup_rejection",
        "migration_disabled_startup_preserved_schema_and_identities",
        "schema_idempotence",
        "old_orm_cold_restore_compatibility",
    )
    if (
        facts.get("status") != "pass"
        or any(facts.get(key) != "pass" for key in expected_passes)
        or facts.get("positive_server_startup") != "not-tested-by-db-fixture"
    ):
        raise HarnessFailure("DB runner did not prove its declared migration contract")


def atomic_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        path.chmod(mode)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def safe_call(
    argv: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    child_env = env
    if child_env is None:
        child_env = os.environ.copy()
        child_env.pop("CONFIGPROXY_AUTH_TOKEN", None)
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=child_env,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            input=input_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessFailure("bounded command timed out") from exc
    except OSError as exc:
        raise HarnessFailure("required command could not be executed") from exc
    return result


def require_call(
    argv: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> bytes:
    result = safe_call(argv, timeout=timeout, cwd=cwd, env=env, capture=capture)
    if result.returncode:
        raise HarnessFailure("bounded command failed: " + Path(argv[0]).name)
    return result.stdout or b""


class Suite:
    def __init__(self, workspace: Path, baseline: str) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.baseline_sha = baseline
        self.candidate_sha = ""
        self.run_id = uuid.uuid4().hex[:12]
        self.run_root: Path | None = None
        self.marker: Path | None = None
        self.manifest: dict[str, Any] = {
            "kind": "jupyterhub-migration-integration",
            "run_id": self.run_id,
            "resources": {"containers": [], "images": [], "networks": []},
            "stages": [],
        }
        self.results: dict[str, Any] = {"cases": {}, "runtime": "not-run"}
        self.active_stage = "initialization"
        self.active_case = ""
        self.failure_context: dict[str, str | int] | None = None
        self.started = time.monotonic()
        self.stage_deadline = self.started + OVERALL_TEST_TIMEOUT
        self.project_dir: Path | None = None
        self.backups: dict[str, Path] = {}
        self.image_ids: dict[str, str] = {}
        self.image_refs: dict[str, str] = {}
        self.owned_image_refs: dict[str, str] = {}
        self.service_ids: set[str] = set()
        self.retained_lab_ids: set[str] = set()
        self.expected_live_lab_image: str | None = None
        self.network_id: str | None = None
        self.loop_device: str | None = None
        self.registry_id: str | None = None
        self.pool: Path | None = None
        self.probe_dir: Path | None = None
        self.compose: Path | None = None
        self.candidate_override: Path | None = None
        self.env_file: Path | None = None
        self.project_name = f"{PROJECT}-{self.run_id}"
        self.update_env: dict[str, str] = {}
        self.created_run_root = False

    def record_stage(self, name: str, **details: Any) -> None:
        self.active_stage = name
        self.manifest["stages"].append(
            {
                "name": name,
                "at_monotonic": round(time.monotonic() - self.started, 3),
                **details,
            }
        )
        self.save_manifest()

    def save_manifest(self) -> None:
        if self.marker is not None:
            atomic_json(self.marker, self.manifest)

    def add_case(self, name: str, status: str = "pass", **facts: Any) -> None:
        self.active_case = name
        self.results["cases"][name] = {"status": status, **facts}

    def require_time(self, seconds: int = 1) -> None:
        if time.monotonic() + seconds > self.stage_deadline:
            raise HarnessFailure("bounded integration-suite deadline exceeded")

    @staticmethod
    def setup_stderr_classification(stderr: bytes) -> str:
        message = stderr.lower()
        if any(
            phrase in message
            for phrase in (
                b"no free loop device",
                b"cannot find an unused loop device",
                b"no loop device available",
            )
        ):
            return "no-free-loop-device"
        if b"operation not permitted" in message or b"permission denied" in message:
            return "permission-denied"
        if b"device or resource busy" in message or b"already mounted" in message:
            return "device-busy"
        if b"unknown filesystem type" in message or b"bad fs type" in message:
            return "unsupported-filesystem"
        if b"no such file or directory" in message:
            return "missing-path-or-device"
        if b"invalid argument" in message:
            return "invalid-argument"
        return "unclassified-command-failure"

    def setup_command(
        self,
        operation: Literal["loop-attach", "loop-mount", "loop-mount-validation"],
        argv: list[str],
        *,
        timeout: int,
    ) -> bytes:
        self.active_stage = operation
        self.failure_context = None
        try:
            result = safe_call(argv, timeout=timeout, capture=True)
        except HarnessFailure as exc:
            cause = exc.__cause__
            context: dict[str, str | int] = {"operation": operation}
            if isinstance(cause, subprocess.TimeoutExpired):
                context["classification"] = "timeout"
            elif isinstance(cause, OSError):
                if cause.errno in (errno.EACCES, errno.EPERM):
                    classification = "permission-denied"
                elif cause.errno == errno.ENOENT:
                    classification = "missing-command-or-path"
                else:
                    classification = "operating-system-error"
                context["classification"] = classification
                if isinstance(cause.errno, int):
                    context["errno"] = cause.errno
            else:
                context["classification"] = "command-launch-failure"
            self.failure_context = context
            raise HarnessFailure("bounded filesystem setup command failed") from None
        if result.returncode:
            self.failure_context = {
                "operation": operation,
                "classification": self.setup_stderr_classification(
                    result.stderr or b""
                ),
                "returncode": result.returncode,
            }
            raise HarnessFailure("bounded filesystem setup command failed")
        self.failure_context = None
        return result.stdout or b""

    def record_xfs_setup_stage(
        self,
        name: str,
        phase: Literal[
            "loop-intent-record", "loop-attachment-record", "loop-mount-record"
        ],
        **details: Any,
    ) -> None:
        self.manifest["stages"].append(
            {
                "name": name,
                "at_monotonic": round(time.monotonic() - self.started, 3),
                **details,
            }
        )
        self.active_stage = phase
        self.failure_context = {
            "operation": "manifest-write",
            "phase": phase,
            "classification": "write-failed",
        }
        try:
            self.save_manifest()
        except Exception:
            raise HarnessFailure(
                "could not persist XFS setup ownership state"
            ) from None
        self.failure_context = None
        self.active_stage = name

    def docker(self, *args: str, timeout: int = 30, capture: bool = False) -> bytes:
        result = self.docker_result(*args, timeout=timeout, capture=capture)
        if result.returncode:
            raise HarnessFailure("bounded Docker operation failed")
        return result.stdout or b""

    def fixture_runtime_env(self) -> dict[str, str]:
        token = self.update_env.get("CONFIGPROXY_AUTH_TOKEN")
        if (
            not token
            or not self.run_root
            or self.update_env.get("COMPOSE_PROJECT_NAME") != self.project_name
            or self.update_env.get("UPDATE_LOCK_FILE")
            != str(self.run_root / "update.lock")
        ):
            raise HarnessFailure("fixture runtime environment is unavailable")
        return self.update_env.copy()

    def docker_result(
        self, *args: str, timeout: int = 30, capture: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        self.require_time(min(timeout, 120))
        return safe_call(["docker", *args], timeout=timeout, capture=capture)

    def docker_json(self, *args: str, timeout: int = 30) -> Any:
        try:
            return json.loads(self.docker(*args, timeout=timeout, capture=True))
        except (json.JSONDecodeError, TypeError) as exc:
            raise HarnessFailure("Docker returned invalid structured output") from exc

    def image_id(self, reference: str) -> str:
        value = (
            self.docker(
                "image", "inspect", "--format", "{{.Id}}", reference, capture=True
            )
            .decode()
            .strip()
        )
        if not IMAGE_RE.fullmatch(value):
            raise HarnessFailure("image did not resolve to an immutable content ID")
        return value

    def register_image_reference(self, key: str, image_id: str, reference: str) -> None:
        self.image_ids[key] = image_id
        self.image_refs[key] = reference

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        values = self.docker_json("inspect", container_id)
        if (
            not isinstance(values, list)
            or len(values) != 1
            or not isinstance(values[0], dict)
        ):
            raise HarnessFailure("container inspection shape was invalid")
        return values[0]

    def own_container(
        self,
        name: str,
        image: str,
        command: list[str],
        *,
        kind: str,
        labels: dict[str, str] | None = None,
        mounts: list[str] | None = None,
        network: str = "none",
        ports: list[str] | None = None,
        read_only: bool = True,
        timeout: int = 120,
    ) -> str:
        image_content_id = self.image_id(image)
        resource: dict[str, Any] = {
            "id": None,
            "name": name,
            "kind": kind,
            "label": self.run_id,
            "purpose": kind,
            "image_id": image_content_id,
            "project_directory": str(self.project_dir) if self.project_dir else "",
            "network": network,
            "expected_mounts": self.mount_signature(mounts or []),
            "removed": False,
            "intent": True,
        }
        self.manifest["resources"]["containers"].append(resource)
        self.save_manifest()
        argv = [
            "create",
            "--name",
            name,
            "--label",
            f"{RUN_LABEL}={self.run_id}",
            "--network",
            network,
            "--read-only" if read_only else "--tmpfs",
        ]
        if not read_only:
            argv = [
                "create",
                "--name",
                name,
                "--label",
                f"{RUN_LABEL}={self.run_id}",
                "--network",
                network,
            ]
        if read_only:
            argv.extend(
                [
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges:true",
                    "--pids-limit=128",
                    "--log-driver=none",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,noexec,size=64m",
                ]
            )
        if labels:
            for key, value in labels.items():
                argv.extend(["--label", f"{key}={value}"])
        for mount in mounts or []:
            argv.extend(["--mount", mount])
        for port in ports or []:
            argv.extend(["--publish", port])
        argv.extend(
            [
                "--label",
                f"shell.integration.purpose={kind}",
            ]
        )
        argv.extend([image, *command])
        try:
            output = self.docker(*argv, timeout=30, capture=True).decode().strip()
        except Exception:
            self.reconcile_container_intent(resource)
            raise
        if not FULL_CONTAINER_ID_RE.fullmatch(output):
            self.reconcile_container_intent(resource)
            raise HarnessFailure("Docker did not return a container ID")
        resource["id"] = output
        resource["intent"] = False
        self.save_manifest()
        self.verify_container_record(resource)
        return output

    @staticmethod
    def mount_signature(mounts: list[str]) -> list[dict[str, Any]]:
        signature: list[dict[str, Any]] = []
        for value in mounts:
            fields = dict(
                part.split("=", 1) for part in value.split(",") if "=" in part
            )
            if (
                fields.get("type") != "bind"
                or not fields.get("source")
                or not fields.get("target")
            ):
                raise HarnessFailure("owned container mount declaration was invalid")
            signature.append(
                {
                    "source": str(Path(fields["source"]).resolve()),
                    "target": fields["target"],
                    "read_only": "readonly" in value.split(",")
                    or fields.get("readonly") == "true",
                }
            )
        return signature

    def reconcile_container_intent(self, resource: dict[str, Any]) -> None:
        inventory = self.docker_result(
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            f"name=^/{resource['name']}$",
            capture=True,
        )
        if inventory.returncode:
            raise HarnessFailure("could not reconcile container creation inventory")
        found = (inventory.stdout or b"").decode().splitlines()
        ids = [item.strip() for item in found if item.strip()]
        if not ids:
            resource["intent"] = False
            resource["removed"] = True
            self.save_manifest()
            return
        if len(ids) != 1 or not FULL_CONTAINER_ID_RE.fullmatch(ids[0]):
            raise HarnessFailure("creation intent matched ambiguous Docker resources")
        resource["id"] = ids[0]
        resource["intent"] = False
        self.save_manifest()
        self.verify_container_record(resource)

    def verify_container_record(self, resource: dict[str, Any]) -> dict[str, Any]:
        container_id = resource.get("id")
        if not isinstance(container_id, str) or not FULL_CONTAINER_ID_RE.fullmatch(
            container_id
        ):
            raise HarnessFailure("owned container record has no verified ID")
        item = self.inspect_container(container_id)
        labels = (item.get("Config") or {}).get("Labels") or {}
        if (
            item.get("Id") != container_id
            or labels.get(RUN_LABEL) != self.run_id
            or labels.get("shell.integration.purpose") != resource.get("purpose")
            or (item.get("Name") or "").lstrip("/") != resource.get("name")
            or item.get("Image") != resource.get("image_id")
            or (item.get("HostConfig") or {}).get("NetworkMode")
            != resource.get("network")
        ):
            raise HarnessFailure(
                "owned container identity did not match its creation intent"
            )
        expected_mounts = resource.get("expected_mounts", [])
        actual_mounts = [
            {
                "source": str(Path(str(mount.get("Source", ""))).resolve()),
                "target": str(mount.get("Destination", "")),
                "read_only": not bool(mount.get("RW")),
            }
            for mount in item.get("Mounts", [])
            if mount.get("Type") == "bind"
        ]
        if sorted(actual_mounts, key=lambda entry: str(entry["target"])) != sorted(
            expected_mounts, key=lambda entry: str(entry["target"])
        ):
            raise HarnessFailure("owned container bind mounts differed from intent")
        return item

    def verify_container_absent(self, container_id: str) -> None:
        result = safe_call(
            ["docker", "inspect", container_id], timeout=15, capture=True
        )
        message = (result.stderr or b"").decode("utf-8", errors="replace").lower()
        if result.returncode == 0 or not any(
            phrase in message for phrase in ("no such object", "no such container")
        ):
            raise HarnessFailure("Docker removal could not be verified")

    def remove_owned_container(self, container_id: str, *, force: bool = False) -> None:
        if not FULL_CONTAINER_ID_RE.fullmatch(container_id):
            raise HarnessFailure("container cleanup requires a full immutable ID")
        resource = next(
            (
                entry
                for entry in self.manifest["resources"]["containers"]
                if entry.get("id") == container_id and not entry.get("removed")
            ),
            None,
        )
        if force:
            if resource is None:
                raise HarnessFailure("forced cleanup requires a recorded owned tool")
            item = self.verify_container_record(resource)
        else:
            item = self.inspect_container(container_id)
        if item.get("Id") != container_id:
            raise HarnessFailure("inspected container ID differed from cleanup target")
        labels = (item.get("Config") or {}).get("Labels") or {}
        if labels.get(RUN_LABEL) != self.run_id:
            raise HarnessFailure(
                "refusing to adopt/remove a container without this run label"
            )
        if bool((item.get("State") or {}).get("Running")):
            if not force:
                self.docker("update", "--restart=no", container_id, timeout=20)
                self.docker("kill", "--signal=TERM", container_id, timeout=20)
                until = time.monotonic() + 60
                while time.monotonic() < until:
                    item = self.inspect_container(container_id)
                    if not bool((item.get("State") or {}).get("Running")):
                        break
                    time.sleep(1)
                if bool((item.get("State") or {}).get("Running")):
                    raise HarnessFailure(
                        "owned container did not exit after bounded TERM"
                    )
            else:
                self.docker("kill", "--signal=TERM", container_id, timeout=15)
                self.docker("rm", "--force", container_id, timeout=20)
                self.verify_container_absent(container_id)
                for resource in self.manifest["resources"]["containers"]:
                    if resource["id"] == container_id:
                        resource["removed"] = True
                self.save_manifest()
                return
        self.docker("rm", container_id, timeout=30)
        self.verify_container_absent(container_id)
        for resource in self.manifest["resources"]["containers"]:
            if resource["id"] == container_id:
                resource["removed"] = True
        self.save_manifest()

    def run_owned_tool(
        self,
        image: str,
        command: list[str],
        *,
        name_prefix: str,
        mounts: list[str] | None = None,
        network: str = "none",
        ports: list[str] | None = None,
        timeout: int = 60,
    ) -> tuple[int, bytes]:
        name = f"{name_prefix}-{self.run_id}-{len(self.manifest['resources']['containers'])}"
        cid = self.own_container(
            name,
            image,
            command,
            kind=name_prefix,
            mounts=mounts,
            network=network,
            ports=ports,
            timeout=timeout,
        )
        try:
            output = safe_call(
                ["docker", "start", "--attach", cid], timeout=timeout, capture=True
            )
            return output.returncode, output.stdout or b""
        finally:
            # Exact disposable tool IDs are ownership-verified before bounded
            # cleanup; cleanup failures must escape and preserve fixture data.
            self.remove_owned_container(cid, force=True)

    def inspect_version(self, image: str, executable: str) -> str:
        code = "import importlib.metadata as m; print(m.version('jupyterhub'))"
        status, output = self.run_owned_tool(
            image, [executable, "-c", code], name_prefix="version-probe", timeout=30
        )
        if status:
            raise HarnessFailure("isolated installed-version probe failed")
        version = output.decode("ascii", errors="strict").strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise HarnessFailure("installed JupyterHub version was malformed")
        return version

    def preflight(self) -> None:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise HarnessFailure("full acceptance requires root on Linux")
        for tool in (
            "docker",
            "timeout",
            "mkfs.xfs",
            "xfs_quota",
            "losetup",
            "findmnt",
            "fallocate",
            "mount",
            "umount",
            "modprobe",
            "git",
            "tar",
            "ip",
        ):
            if shutil.which(tool) is None:
                raise HarnessFailure("required Linux runner tool is unavailable")
        info = (
            self.docker("info", "--format", "{{json .SecurityOptions}}", capture=True)
            .decode()
            .lower()
        )
        if "rootless" in info:
            raise HarnessFailure("rootless Docker daemon is forbidden")
        containers = self.docker("ps", "-aq", capture=True).decode().strip()
        networks = (
            self.docker("network", "ls", "-q", "--filter", "type=custom", capture=True)
            .decode()
            .strip()
        )
        volumes = self.docker("volume", "ls", "-q", capture=True).decode().strip()
        if containers or networks or volumes:
            raise HarnessFailure(
                "refusing a Docker daemon with pre-existing containers, networks, or volumes"
            )
        for port in (5000, 8000, 8001, 8080, 8081):
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise HarnessFailure(
                        "a reserved integration loopback port is already in use"
                    )
        try:
            self.docker("network", "inspect", NETWORK_NAME)
        except HarnessFailure:
            pass
        else:
            raise HarnessFailure(
                "reserved production-shape network name already exists"
            )
        try:
            self.docker("image", "inspect", "ghcr.io/finki-hub/shell-hub:latest")
        except HarnessFailure:
            pass
        else:
            raise HarnessFailure(
                "refusing an ambiguous pre-existing production-name Hub image"
            )
        root_dir = Path(
            self.docker("info", "--format", "{{.DockerRootDir}}", capture=True)
            .decode()
            .strip()
        )
        if shutil.disk_usage(root_dir).free < MIN_FREE:
            raise HarnessFailure(
                "Docker filesystem has less than the 25 GiB build/test budget"
            )
        if shutil.disk_usage(self.workspace).free < MIN_FREE:
            raise HarnessFailure(
                "workspace filesystem has less than the 25 GiB build/test budget"
            )
        for port in (8000, 8001, 8080, 8081, 5000):
            if shutil.which("lsof"):
                result = safe_call(
                    ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                    timeout=10,
                    capture=True,
                )
                if result.returncode == 0 and (result.stdout or b"").strip():
                    raise HarnessFailure("a reserved integration port has a listener")
        if (
            not Path("/proc/filesystems").read_text().splitlines()
            or not Path("/dev/loop-control").exists()
        ):
            raise HarnessFailure("XFS or loop-device support is unavailable")
        if not any(
            "xfs" in line.split()
            for line in Path("/proc/filesystems").read_text().splitlines()
        ):
            require_call(["timeout", "20s", "modprobe", "xfs"], timeout=25)
        if not any(
            "xfs" in line.split()
            for line in Path("/proc/filesystems").read_text().splitlines()
        ):
            raise HarnessFailure("kernel XFS support did not become available")
        free_loop = (
            require_call(["losetup", "-f"], timeout=10, capture=True).decode().strip()
        )
        if not free_loop.startswith("/dev/loop"):
            raise HarnessFailure("no free loop device")
        baseline = safe_call(
            [
                "git",
                "-C",
                str(self.workspace),
                "cat-file",
                "-e",
                f"{self.baseline_sha}^{{commit}}",
            ],
            timeout=20,
        )
        if baseline.returncode:
            raise HarnessFailure("pinned pre-PR baseline commit is unavailable")
        self.candidate_sha = (
            require_call(
                ["git", "-C", str(self.workspace), "rev-parse", "HEAD"],
                timeout=20,
                capture=True,
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[0-9a-f]{40}", self.candidate_sha):
            raise HarnessFailure("candidate source identity unavailable")
        if self.candidate_sha == self.baseline_sha:
            raise HarnessFailure("candidate revision equals the pinned old baseline")
        self.record_stage(
            "exclusive-runner-preflight",
            daemon_id=self.docker("info", "--format", "{{.ID}}", capture=True)
            .decode()
            .strip(),
            free_disk_bytes=shutil.disk_usage(root_dir).free,
        )

    def create_run_root(self) -> None:
        temp_root = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve(
            strict=True
        )
        if not temp_root.is_dir():
            raise HarnessFailure("runner temporary directory is unavailable")
        self.run_root = temp_root / f"jh6-integr-{self.run_id}"
        if self.run_root.exists() or self.run_root.is_symlink():
            raise HarnessFailure("run root already exists; will not adopt it")
        self.run_root.mkdir(mode=0o700)
        self.run_root.chmod(0o700)
        self.created_run_root = True
        self.marker = self.run_root / "owner.json"
        self.pool = self.run_root / "pool"
        self.pool.mkdir(mode=0o700)
        (self.run_root / "backups").mkdir(mode=0o700)
        self.probe_dir = self.run_root / "probe"
        self.probe_dir.mkdir(mode=0o700)
        self.manifest.update(
            {
                "run_root": str(self.run_root.resolve()),
                "workspace": str(self.workspace),
                "baseline_source_sha": self.baseline_sha,
                "candidate_source_sha": self.candidate_sha,
                "daemon_id": self.docker("info", "--format", "{{.ID}}", capture=True)
                .decode()
                .strip(),
                "loop_device": None,
                "pool_mount": str(self.pool),
                "network_id": None,
            }
        )
        self.save_manifest()
        self.record_stage("run-root-owned")

    def setup_xfs_pool(self) -> None:
        assert self.run_root is not None and self.pool is not None
        image = self.run_root / "pool.img"
        if image.exists() or image.is_symlink():
            raise HarnessFailure("XFS backing image already exists")
        require_call(["fallocate", "-l", str(POOL_SIZE), str(image)], timeout=30)
        require_call(["mkfs.xfs", "-f", "-q", str(image)], timeout=90)
        self.manifest["loop_backing_intent"] = str(image.resolve())
        self.record_xfs_setup_stage(
            "loop-attach-intent",
            "loop-intent-record",
            backing=str(image.resolve()),
        )
        loop_output = self.setup_command(
            "loop-attach",
            ["losetup", "--find", "--show", str(image)],
            timeout=20,
        )
        try:
            loop = loop_output.decode("ascii").strip()
        except UnicodeDecodeError:
            loop = ""
        if not re.fullmatch(r"/dev/loop\d+", loop):
            self.active_stage = "loop-device-validation"
            self.failure_context = {
                "operation": "loop-device-validation",
                "classification": "unexpected-device-identifier",
            }
            raise HarnessFailure("loop setup returned an unexpected device")
        self.loop_device = loop
        self.manifest["loop_device"] = loop
        self.record_xfs_setup_stage("loop-device-attached", "loop-attachment-record")
        self.setup_command(
            "loop-mount",
            [
                "mount",
                "-t",
                "xfs",
                "-o",
                "prjquota,nosuid,nodev,noatime",
                loop,
                str(self.pool),
            ],
            timeout=30,
        )
        self.active_stage = "xfs-pool-initialize"
        pool_id = str(uuid.uuid4())
        (self.pool / ".pool-id").write_text(pool_id, encoding="ascii")
        (self.pool / ".pool-id").chmod(0o400)
        (self.pool / ".projects").touch(mode=0o600)
        (self.pool / ".projid-counter").write_text("1000\n", encoding="ascii")
        (self.pool / ".lock").touch(mode=0o600)
        for name in (".projects", ".projid-counter", ".lock"):
            (self.pool / name).chmod(0o600)
        (self.pool / "users").mkdir(mode=0o755)
        findmnt_output = self.setup_command(
            "loop-mount-validation",
            [
                "findmnt",
                "-n",
                "-o",
                "FSTYPE,OPTIONS,SOURCE,TARGET",
                "--target",
                str(self.pool),
            ],
            timeout=15,
        )
        try:
            findmnt = findmnt_output.decode("utf-8").strip().split()
        except UnicodeDecodeError:
            findmnt = []
        if (
            len(findmnt) < 4
            or findmnt[0] != "xfs"
            or "prjquota" not in findmnt[1].split(",")
            or findmnt[2] != loop
            or Path(findmnt[3]).resolve() != self.pool.resolve()
        ):
            self.active_stage = "loop-mount-validation"
            self.failure_context = {
                "operation": "loop-mount-validation",
                "classification": "mount-state-mismatch",
            }
            raise HarnessFailure(
                "mounted fixture is not the expected XFS project-quota filesystem"
            )
        self.record_xfs_setup_stage(
            "loop-mount-verified",
            "loop-mount-record",
            filesystem="xfs",
            project_quota="prjquota",
        )
        self.active_stage = "xfs-quota-verification"
        self.verify_quota_enforcement()
        self.record_stage(
            "xfs-project-quota-verified",
            pool_id_sha256=hashlib.sha256(pool_id.encode()).hexdigest(),
            size_bytes=POOL_SIZE,
        )

    def quota_command(self, command: str) -> str:
        assert self.pool is not None
        return require_call(
            ["xfs_quota", "-x", "-c", command, str(self.pool)], timeout=20, capture=True
        ).decode(errors="replace")

    def quota_state(self) -> str:
        return self.quota_command("state -p")

    def verify_quota_enforcement(self) -> None:
        assert self.pool is not None
        state = self.quota_state().lower()
        if (
            "project" not in state
            or "accounting: on" not in state
            or "enforcement: on" not in state
        ):
            raise HarnessFailure("XFS project quota accounting/enforcement is not ON")
        scratch_id = 9999
        scratch = self.pool / ".integration-quota-probe"
        scratch.mkdir(mode=0o700)
        projects = self.pool / ".projects"
        original = projects.read_text(encoding="utf-8")
        try:
            projects.write_text(
                original + f"{scratch_id}:{scratch}\n", encoding="utf-8"
            )
            self.quota_command(f"project -s -p {scratch} {scratch_id}")
            self.quota_command(f"limit -p bhard=16m ihard=1000 {scratch_id}")
            blob = scratch / "probe.bin"
            fd = os.open(blob, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            saw_edquot = False
            try:
                block = b"\0" * (1024 * 1024)
                remaining = 64 * 1024 * 1024
                while remaining:
                    try:
                        written = os.write(fd, block[: min(len(block), remaining)])
                        if written <= 0:
                            raise HarnessFailure(
                                "bounded XFS quota probe made no write progress"
                            )
                        remaining -= written
                    except OSError as exc:
                        if exc.errno == errno.EDQUOT:
                            saw_edquot = True
                            break
                        raise
                if not saw_edquot:
                    try:
                        os.fsync(fd)
                    except OSError as exc:
                        if exc.errno == errno.EDQUOT:
                            saw_edquot = True
                        else:
                            raise
            finally:
                os.close(fd)
            if not saw_edquot:
                raise HarnessFailure(
                    "XFS project quota scratch write did not return EDQUOT"
                )
        finally:
            try:
                self.quota_command(f"limit -p bhard=0 ihard=0 {scratch_id}")
            finally:
                if scratch.exists():
                    shutil.rmtree(scratch)
                projects.write_text(original, encoding="utf-8")
                projects.chmod(0o600)
        if (
            self.quota_state().lower().count("accounting: on") < 1
            or self.quota_state().lower().count("enforcement: on") < 1
        ):
            raise HarnessFailure("XFS quota state changed after enforcement probe")

    def archive_source(self, revision: str, destination: Path) -> Path:
        assert self.run_root is not None
        archive = self.run_root / f"source-{revision[:12]}.tar"
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        archive_env = os.environ.copy()
        archive_env.pop("CONFIGPROXY_AUTH_TOKEN", None)
        with archive.open("wb") as stream:
            result = subprocess.run(
                ["git", "-C", str(self.workspace), "archive", "--format=tar", revision],
                stdout=stream,
                stderr=subprocess.DEVNULL,
                timeout=BUILD_TIMEOUT,
                check=False,
                env=archive_env,
            )
        if result.returncode:
            raise HarnessFailure("could not create exact source archive")
        destination.mkdir(mode=0o700)
        with tarfile.open(archive, "r:") as bundle:
            for member in bundle.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise HarnessFailure("source archive path escaped the run root")
            bundle.extractall(destination, filter="data")
        return destination

    def build(
        self,
        context: Path,
        dockerfile: str,
        tag: str,
        *,
        args: dict[str, str] | None = None,
        pull: bool = True,
    ) -> str:
        argv = ["docker", "build"]
        if pull:
            argv.append("--pull")
        argv.extend(["--quiet", "--file", dockerfile, "--tag", tag])
        for key, value in (args or {}).items():
            argv.extend(["--build-arg", f"{key}={value}"])
        argv.append(".")
        result = safe_call(argv, timeout=BUILD_TIMEOUT, cwd=context)
        if result.returncode:
            raise HarnessFailure("bounded locked-image build failed")
        image_id = self.image_id(tag)
        self.manifest["resources"]["images"].append(
            {
                "ref": tag,
                "id": image_id,
                "kind": "built",
                "source_context": str(context),
                "removed": False,
            }
        )
        self.owned_image_refs[tag] = image_id
        self.save_manifest()
        return image_id

    def pull_image(self, reference: str, timeout: int = 300) -> str:
        require_call(["docker", "pull", reference], timeout=timeout)
        image_id = self.image_id(reference)
        self.owned_image_refs[reference] = image_id
        self.manifest["resources"]["images"].append(
            {"ref": reference, "id": image_id, "kind": "pulled", "removed": False}
        )
        self.save_manifest()
        return image_id

    def tag_image(self, image_id: str, reference: str) -> None:
        self.docker("image", "tag", image_id, reference)
        self.owned_image_refs[reference] = image_id
        self.manifest["resources"]["images"].append(
            {"ref": reference, "id": image_id, "kind": "tag", "removed": False}
        )
        self.save_manifest()

    def build_images(self) -> None:
        assert self.run_root is not None
        source_root = self.run_root / "source"
        old_source = self.archive_source(self.baseline_sha, source_root / "baseline")
        candidate_source = self.archive_source(
            self.candidate_sha, source_root / "candidate"
        )
        run_tag = self.run_id
        old_hub_base = self.build(
            old_source / "hub", "Dockerfile", f"local/jh6/base-hub5:{run_tag}"
        )
        old_lab = self.build(
            old_source / "lab", "Dockerfile", f"local/jh6/lab5:{run_tag}"
        )
        candidate_hub_base = self.build(
            candidate_source / "hub", "Dockerfile", f"local/jh6/base-hub6:{run_tag}"
        )
        candidate_lab = self.build(
            candidate_source / "lab", "Dockerfile", f"local/jh6/lab6:{run_tag}"
        )
        web = self.build(candidate_source, "web/Dockerfile", f"local/jh6/web:{run_tag}")
        old_hub_version = self.inspect_version(old_hub_base, "/app/.venv/bin/python")
        old_lab_version = self.inspect_version(old_lab, "/opt/jupyter/bin/python")
        new_hub_version = self.inspect_version(
            candidate_hub_base, "/app/.venv/bin/python"
        )
        new_lab_version = self.inspect_version(candidate_lab, "/opt/jupyter/bin/python")
        if (old_hub_version, old_lab_version, new_hub_version, new_lab_version) != (
            "5.5.1",
            "5.5.1",
            "6.0.1",
            "6.0.1",
        ):
            raise HarnessFailure(
                "actual installed Hub/Lab versions differ from pinned baseline/candidate"
            )
        if (
            self.image_label(candidate_hub_base, VERSION_LABEL) != "6.0.1"
            or self.image_label(candidate_lab, VERSION_LABEL) != "6.0.1"
        ):
            raise HarnessFailure("candidate build-verified version labels mismatch")
        # A test-only Hub image wrapper adds only a run label to DockerSpawner.
        wrapper = self.run_root / "integration_jupyterhub_config.py"
        wrapper.write_text(
            "import os\nfrom pathlib import Path\n"
            "_base = Path('/app/jupyterhub_config.base.py')\n"
            "exec(compile(_base.read_text(encoding='utf-8'), str(_base), 'exec'), globals())\n"
            "_labels = c.DockerSpawner.extra_create_kwargs['labels']\n"
            "_labels['shell.integration.run'] = os.environ['SHELL_INTEGRATION_RUN_ID']\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o600)
        dockerfile = self.run_root / "Integration.Dockerfile"
        dockerfile.write_text(
            "ARG HUB_BASE\nFROM ${HUB_BASE}\nUSER root\n"
            "RUN cp /app/jupyterhub_config.py /app/jupyterhub_config.base.py\n"
            "COPY integration_jupyterhub_config.py /app/jupyterhub_config.py\n",
            encoding="utf-8",
        )
        old_context = self.run_root / "hub-wrapper-old"
        new_context = self.run_root / "hub-wrapper-new"
        for context in (old_context, new_context):
            context.mkdir(mode=0o700)
            shutil.copy2(wrapper, context / "integration_jupyterhub_config.py")
            shutil.copy2(dockerfile, context / "Dockerfile")
        old_hub = self.build(
            old_context,
            "Dockerfile",
            f"local/jh6/hub5:{run_tag}",
            args={"HUB_BASE": old_hub_base},
            pull=False,
        )
        new_hub = self.build(
            new_context,
            "Dockerfile",
            f"local/jh6/hub6:{run_tag}",
            args={"HUB_BASE": candidate_hub_base},
            pull=False,
        )
        if self.image_label(new_hub, VERSION_LABEL) != "6.0.1":
            raise HarnessFailure(
                "candidate wrapper lost its build-verified version label"
            )
        if self.image_label(old_hub, VERSION_LABEL) not in {"", "<no value>"}:
            raise HarnessFailure("baseline Hub acquired a synthetic version label")
        self.image_ids.update(
            {
                "old_hub": old_hub,
                "old_lab": old_lab,
                "candidate_hub": new_hub,
                "candidate_lab": candidate_lab,
                "web": web,
            }
        )
        self.image_refs.update(
            {
                "old_hub": f"local/jh6/hub5:{run_tag}",
                "old_lab": f"local/jh6/lab5:{run_tag}",
                "candidate_hub": f"local/jh6/hub6:{run_tag}",
                "candidate_lab": f"local/jh6/lab6:{run_tag}",
                "web": f"local/jh6/web:{run_tag}",
            }
        )
        proxy = self.pull_image("quay.io/jupyterhub/configurable-http-proxy:5.3.0")
        proxy_local = f"local/jh6/proxy:{run_tag}"
        self.tag_image(proxy, proxy_local)
        self.register_image_reference("proxy", proxy, proxy_local)
        registry = self.pull_image("registry:2.8.3")
        self.image_ids["registry"] = registry
        self.manifest["images"] = {
            key: {"id": value, "ref": self.image_refs.get(key)}
            for key, value in self.image_ids.items()
        }
        self.manifest["installed_versions"] = {
            "old_hub": old_hub_version,
            "old_lab": old_lab_version,
            "candidate_hub": new_hub_version,
            "candidate_lab": new_lab_version,
        }
        self.save_manifest()
        self.test_db_runner()
        self.record_stage(
            "db-runner-complete", result="pass", db_runner="actual-images-upstream-orm"
        )
        if (
            self.docker("ps", "-aq", capture=True).decode().strip()
            or self.docker(
                "network", "ls", "-q", "--filter", "type=custom", capture=True
            )
            .decode()
            .strip()
        ):
            raise HarnessFailure(
                "DB runner did not return daemon to its empty pre-stack state"
            )
        self.start_local_registry()
        for key in (
            "old_hub",
            "old_lab",
            "candidate_hub",
            "candidate_lab",
            "web",
            "proxy",
        ):
            local_ref = self.image_refs[key]
            self.tag_image(self.image_ids[key], local_ref)
            ref = f"127.0.0.1:5000/{local_ref}"
            self.image_refs[key] = ref
            self.tag_image(self.image_ids[key], ref)
            require_call(["docker", "push", ref], timeout=300)
            if self.image_id(ref) != self.image_ids[key]:
                raise HarnessFailure(
                    "local integration registry changed immutable image identity"
                )
        self.record_stage("immutable-fixture-images-ready", image_count=6)

    def image_label(self, image: str, label: str) -> str:
        return (
            self.docker(
                "image",
                "inspect",
                "--format",
                f'{{{{ index .Config.Labels "{label}" }}}}',
                image,
                capture=True,
            )
            .decode()
            .strip()
        )

    def test_db_runner(self) -> None:
        self.active_stage = "upstream-db-contract"
        self.active_case = "actual-upstream-db-migration"
        assert self.run_root is not None
        script = self.workspace / "scripts" / "integration" / "test_db_migration.py"
        result = safe_call(
            [
                sys.executable,
                str(script),
                "--acknowledge-disposable",
                "--old-image",
                self.image_ids["old_hub"],
                "--new-image",
                self.image_ids["candidate_hub"],
                "--timeout",
                "120",
                "--suite-timeout",
                str(DB_TIMEOUT),
            ],
            # The DB runner may need its bounded 120s ownership cleanup after
            # its acceptance deadline; leave that cleanup alive in this parent.
            timeout=DB_TIMEOUT + 180,
            cwd=self.workspace,
            capture=True,
        )
        if result.returncode:
            raise HarnessFailure("actual Hub 5-to-6 database migration runner failed")
        try:
            facts = json.loads((result.stdout or b"").decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise HarnessFailure(
                "DB runner returned invalid sanitized evidence"
            ) from exc
        validate_db_evidence(facts)
        self.add_case(
            "actual-upstream-db-migration",
            schema_before=facts.get("schema_before"),
            schema_after=facts.get("schema_after"),
            schema_idempotence=facts["schema_idempotence"],
            old_orm_cold_restore=facts["old_orm_cold_restore_compatibility"],
            migration_disabled_startup=facts[
                "migration_disabled_cold_startup_rejection"
            ],
        )

    def start_local_registry(self) -> None:
        assert self.run_root is not None
        data = self.run_root / "registry-data"
        data.mkdir(mode=0o700)
        cid = self.own_container(
            f"jh6-registry-{self.run_id}",
            self.image_ids["registry"],
            ["/etc/docker/registry/config.yml"],
            kind="local-image-registry",
            network="bridge",
            ports=["127.0.0.1:5000:5000"],
            read_only=False,
            mounts=[f"type=bind,source={data},target=/var/lib/registry"],
            timeout=30,
        )
        self.registry_id = cid
        self.docker("start", cid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:5000/v2/", timeout=2
                ) as response:
                    if response.status in (200, 401):
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.5)
        else:
            raise HarnessFailure("private local image registry did not become ready")
        self.add_case("local-image-registry", status="pass")

    def setup_network(self) -> None:
        assert self.run_root is not None
        resource: dict[str, Any] = {
            "id": None,
            "name": NETWORK_NAME,
            "run_label": self.run_id,
            "purpose": "isolated-users-network",
            "removed": False,
            "intent": True,
        }
        self.manifest["resources"]["networks"].append(resource)
        self.save_manifest()
        create_argv = [
            "docker",
            "network",
            "create",
            "--driver",
            "bridge",
            "--internal",
            "--subnet",
            "172.30.0.0/23",
            "--gateway",
            "172.30.0.1",
            "--label",
            f"{RUN_LABEL}={self.run_id}",
            "-o",
            "com.docker.network.bridge.enable_icc=false",
            "-o",
            f"com.docker.network.bridge.name={BRIDGE_NAME}",
            NETWORK_NAME,
        ]
        try:
            created = safe_call(create_argv, timeout=30, capture=True)
            cid = (created.stdout or b"").decode().strip()
            if created.returncode:
                raise HarnessFailure("bounded Docker network creation failed")
        except Exception:
            self.reconcile_network_intent(resource)
            raise
        if not re.fullmatch(r"[0-9a-f]{64}", cid):
            self.reconcile_network_intent(resource)
            raise HarnessFailure("fixture network ID was malformed")
        self.network_id = cid
        self.manifest["network_id"] = cid
        resource["id"] = cid
        resource["intent"] = False
        self.save_manifest()
        item = self.docker_json("network", "inspect", cid)[0]
        self.verify_network_resource(item)
        self.record_stage("private-users-network-ready", network_id=cid)

    def reconcile_network_intent(self, resource: dict[str, Any]) -> None:
        inventory = self.docker_result(
            "network",
            "ls",
            "-q",
            "--filter",
            f"name=^{NETWORK_NAME}$",
            capture=True,
        )
        if inventory.returncode:
            raise HarnessFailure("could not reconcile network creation inventory")
        candidates = (inventory.stdout or b"").decode().splitlines()
        ids = [candidate.strip() for candidate in candidates if candidate.strip()]
        if not ids:
            resource["removed"] = True
            self.save_manifest()
            return
        if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{64}", ids[0]):
            raise HarnessFailure("network intent matched ambiguous daemon resources")
        resource["id"] = ids[0]
        self.network_id = ids[0]
        self.manifest["network_id"] = ids[0]
        self.save_manifest()
        self.verify_network_resource(self.docker_json("network", "inspect", ids[0])[0])

    def verify_network_resource(self, item: dict[str, Any]) -> None:
        labels = item.get("Labels") or {}
        ipam = (item.get("IPAM") or {}).get("Config") or []
        if (
            item.get("Name") != NETWORK_NAME
            or item.get("Driver") != "bridge"
            or item.get("Internal") is not True
            or labels.get(RUN_LABEL) != self.run_id
            or len(ipam) != 1
            or ipam[0].get("Subnet") != "172.30.0.0/23"
            or ipam[0].get("Gateway") != "172.30.0.1"
            or item.get("Options", {}).get("com.docker.network.bridge.enable_icc")
            != "false"
            or item.get("Options", {}).get("com.docker.network.bridge.name")
            != BRIDGE_NAME
        ):
            raise HarnessFailure(
                "fixture users network does not match isolated production shape"
            )

    def write_compose_fixture(self) -> None:
        assert self.run_root is not None and self.pool is not None
        project = self.run_root / "project"
        project.mkdir(mode=0o700)
        self.project_dir = project
        self.project_name = f"{PROJECT}-{self.run_id}"
        candidate_source = self.run_root / "source" / "candidate"
        self.compose = project / "compose.yaml"
        shutil.copy2(candidate_source / "compose.yaml", self.compose)
        self.compose.chmod(0o600)
        self.env_file = project / ".env"
        proxy_secret = secrets.token_urlsafe(48)
        if (
            not isinstance(proxy_secret, str)
            or not proxy_secret
            or any(char.isspace() for char in proxy_secret)
        ):
            raise HarnessFailure("fixture proxy credential generation failed")
        values = {
            "LOG_LEVEL": "INFO",
            "TZ": "UTC",
            "LAB_POOL_DIR": str(self.pool),
            "LAB_POOL_RESERVE_PCT": "2",
            "LAB_ENV_QUOTA_MB": "100",
            "LAB_ENV_MAX_INODES": "20000",
            "HUB_IMAGE": self.image_refs["candidate_hub"],
            "LAB_IMAGE": self.image_refs["candidate_lab"],
            "WEB_IMAGE": self.image_refs["web"],
            "PROXY_IMAGE": self.image_refs["proxy"],
            "HOST_PORT": "8080",
            "TURNSTILE_SITEKEY": "",
            "TURNSTILE_SECRET": "",
            "LAB_USER": "ubuntu",
            "LAB_UID": "1000",
            "LAB_GID": "1000",
            "LAB_MEMORY_MB": "384",
            "LAB_CPUS": "0.5",
            "LAB_PIDS": "256",
            "LAB_TMP_MB": "32",
            "LAB_VARTMP_MB": "16",
            "LAB_RUN_MB": "8",
            "LAB_SHM_MB": "16",
            "LAB_MAX_SESSIONS": "20",
            "LAB_MAX_TERMINALS": "4",
            "LAB_IDLE_MIN": "1440",
            "LAB_CONTAINER_MAX_AGE_H": "8760",
            "LAB_RETENTION_H": "8760",
            "LAB_MAX_AGE_H": "8760",
            "LAB_MAX_CREATES_PER_MIN": "60",
            "LAB_LOG_MAX_SIZE": "512k",
            "LAB_LOG_MAX_FILES": "2",
            "SHELL_INTEGRATION_RUN_ID": self.run_id,
        }
        if any(
            "\n" in key or "\n" in value or " " in value
            for key, value in values.items()
        ):
            raise HarnessFailure("fixture environment contains unsupported whitespace")
        self.update_env = os.environ.copy()
        self.update_env.update(values)
        self.update_env["CONFIGPROXY_AUTH_TOKEN"] = proxy_secret
        self.update_env["COMPOSE_PROJECT_NAME"] = self.project_name
        self.update_env["UPDATE_LOCK_FILE"] = str(self.run_root / "update.lock")
        self.env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()),
            encoding="utf-8",
        )
        self.env_file.chmod(0o600)
        self.candidate_override = project / "candidate.override.json"
        override = {
            "services": {
                "web": {
                    "image": self.image_refs["web"],
                    "restart": "no",
                    "labels": {RUN_LABEL: self.run_id},
                },
                "proxy": {
                    "image": self.image_refs["proxy"],
                    "restart": "no",
                    "labels": {RUN_LABEL: self.run_id},
                },
                "hub": {
                    "image": self.image_refs["candidate_hub"],
                    "restart": "no",
                    "labels": {RUN_LABEL: self.run_id},
                    "environment": {"SHELL_INTEGRATION_RUN_ID": self.run_id},
                },
                "lab": {"image": self.image_refs["candidate_lab"]},
            }
        }
        atomic_json(self.candidate_override, override)
        old_override = project / "old.override.json"
        atomic_json(
            old_override,
            {
                "services": {
                    "hub": {
                        "image": self.image_refs["old_hub"],
                        "restart": "no",
                        "labels": {RUN_LABEL: self.run_id},
                        "environment": {
                            "LAB_IMAGE": self.image_refs["old_lab"],
                            "SHELL_INTEGRATION_RUN_ID": self.run_id,
                        },
                    },
                    "web": {
                        "image": self.image_refs["web"],
                        "restart": "no",
                        "labels": {RUN_LABEL: self.run_id},
                    },
                    "proxy": {
                        "image": self.image_refs["proxy"],
                        "restart": "no",
                        "labels": {RUN_LABEL: self.run_id},
                    },
                    "lab": {"image": self.image_refs["old_lab"]},
                }
            },
        )
        self.initial_override = old_override
        data_dir = project / "data" / "hub"
        data_dir.mkdir(parents=True, mode=0o700)
        self.record_stage(
            "explicit-fixture-configuration",
            env_sha256=digest(self.env_file),
            compose_sha256=digest(self.compose),
            compose_project=self.project_name,
        )

    def compose_command(
        self,
        *args: str,
        override: Path | None = None,
        timeout: int = 60,
        capture: bool = False,
    ) -> bytes:
        assert (
            self.project_dir is not None
            and self.env_file is not None
            and self.compose is not None
        )
        argv = [
            "docker",
            "compose",
            "--project-directory",
            str(self.project_dir),
            "--project-name",
            self.project_name,
            "--env-file",
            str(self.env_file),
            "-f",
            str(self.compose),
        ]
        if override:
            argv.extend(["-f", str(override)])
        if args and args[0] == "up":
            requested = [value for value in args if value in {"web", "proxy", "hub"}]
            self.register_compose_intents(requested)
        return require_call(
            [*argv, *args],
            timeout=timeout,
            env=self.fixture_runtime_env(),
            capture=capture,
        )

    def register_compose_intents(
        self, services: list[str], wrapper_source: Path | None = None
    ) -> None:
        assert self.project_dir is not None and self.pool is not None
        expected_images = {
            "web": [self.image_ids["web"]],
            "proxy": [self.image_ids["proxy"]],
            "hub": [self.image_ids["old_hub"], self.image_ids["candidate_hub"]],
        }
        hub_mounts = [
            {
                "source": str(Path("/var/run/docker.sock").resolve()),
                "target": "/var/run/docker.sock",
                "read_only": False,
            },
            {
                "source": str((self.project_dir / "data" / "hub").resolve()),
                "target": "/srv/hub",
                "read_only": False,
            },
            {
                "source": str(self.pool.resolve()),
                "target": "/srv/pool",
                "read_only": False,
            },
        ]
        hub_mount_options = [hub_mounts]
        if wrapper_source is not None:
            hub_mount_options.append(
                [
                    *hub_mounts,
                    {
                        "source": str(wrapper_source.resolve()),
                        "target": "/srv/maintenance/jupyterhub-maintenance-config.py",
                        "read_only": True,
                    },
                ]
            )
        for service in services:
            if service not in expected_images:
                raise HarnessFailure("compose intent named an unexpected service")
            name = f"{self.project_name}-{service}-1"
            for previous in self.manifest["resources"]["containers"]:
                if (
                    previous.get("kind") == "compose-intent"
                    and previous.get("name") == name
                    and not previous.get("removed")
                ):
                    previous["superseded"] = True
            self.manifest["resources"]["containers"].append(
                {
                    "id": None,
                    "name": name,
                    "kind": "compose-intent",
                    "project": self.project_name,
                    "service": service,
                    "project_directory": str(self.project_dir),
                    "expected_images": expected_images[service],
                    "allowed_mount_sets": hub_mount_options
                    if service == "hub"
                    else [[]],
                    "removed": False,
                    "intent": True,
                }
            )
        self.save_manifest()

    def compose_ids(self, service: str, override: Path | None = None) -> list[str]:
        output = (
            self.compose_command(
                "ps",
                "-aq",
                "--no-trunc",
                service,
                override=override,
                timeout=30,
                capture=True,
            )
            .decode()
            .splitlines()
        )
        ids = [item.strip() for item in output if item.strip()]
        if len(ids) > 1:
            raise HarnessFailure("fixture Compose service has ambiguous containers")
        for cid in ids:
            if not FULL_CONTAINER_ID_RE.fullmatch(cid):
                raise HarnessFailure("Compose returned a truncated container ID")
            item = self.inspect_container(cid)
            labels = (item.get("Config") or {}).get("Labels") or {}
            if (
                item.get("Id") != cid
                or labels.get("com.docker.compose.project") != self.project_name
                or labels.get("com.docker.compose.service") != service
                or labels.get("com.docker.compose.project.working_dir")
                != str(self.project_dir)
            ):
                raise HarnessFailure("Compose service ownership label mismatch")
            expected_images = {
                "web": {self.image_ids["web"]},
                "proxy": {self.image_ids["proxy"]},
                "hub": {self.image_ids["old_hub"], self.image_ids["candidate_hub"]},
            }
            expected_name = f"{self.project_name}-{service}-1"
            if (
                item.get("Image") not in expected_images[service]
                or (item.get("Name") or "").lstrip("/") != expected_name
            ):
                raise HarnessFailure(
                    "Compose service image/name does not belong to this fixture"
                )
            self.service_ids.add(cid)
            name = (item.get("Name") or "").lstrip("/")
            intent = next(
                (
                    resource
                    for resource in reversed(self.manifest["resources"]["containers"])
                    if resource.get("name") == name
                    and resource.get("kind") == "compose-intent"
                    and resource.get("project") == self.project_name
                    and not resource.get("removed")
                ),
                None,
            )
            if intent is None:
                raise HarnessFailure(
                    "Compose resource has no persisted creation intent"
                )
            self.verify_compose_resource(item, intent)
            intent["id"] = cid
            intent["intent"] = False
        self.save_manifest()
        return ids

    def verify_compose_resource(
        self, item: dict[str, Any], intent: dict[str, Any]
    ) -> None:
        labels = (item.get("Config") or {}).get("Labels") or {}
        service = intent.get("service")
        name = str(intent.get("name", ""))
        actual_mounts = [
            {
                "source": str(Path(str(mount.get("Source", ""))).resolve()),
                "target": str(mount.get("Destination", "")),
                "read_only": not bool(mount.get("RW")),
            }
            for mount in item.get("Mounts", [])
            if mount.get("Type") == "bind"
        ]
        allowed_mount_sets = [
            sorted(candidate, key=lambda entry: str(entry["target"]))
            for candidate in intent.get("allowed_mount_sets", [[]])
        ]
        actual_mount_set = sorted(actual_mounts, key=lambda entry: str(entry["target"]))
        mismatches = []
        if labels.get("com.docker.compose.project") != self.project_name:
            mismatches.append("project")
        if labels.get("com.docker.compose.service") != service:
            mismatches.append("service")
        if labels.get(RUN_LABEL) != self.run_id:
            mismatches.append("run-label")
        if labels.get("com.docker.compose.project.working_dir") != intent.get(
            "project_directory"
        ):
            mismatches.append("working-directory")
        if (item.get("Name") or "").lstrip("/") != name:
            mismatches.append("canonical-name")
        if item.get("Image") not in intent.get("expected_images", []):
            mismatches.append("immutable-image")
        if actual_mount_set not in allowed_mount_sets:
            mismatches.append("bind-mounts")
        if mismatches:
            raise HarnessFailure(
                "Compose resource differs from fixture intent: " + ",".join(mismatches)
            )

    def start_old_stack(self) -> None:
        self.compose_command(
            "up",
            "-d",
            "--pull",
            "never",
            "web",
            "proxy",
            override=self.candidate_override,
            timeout=180,
        )
        self.compose_command(
            "up",
            "-d",
            "--pull",
            "never",
            "hub",
            override=self.initial_override,
            timeout=180,
        )
        for service in ("web", "proxy", "hub"):
            ids = self.compose_ids(
                service,
                self.initial_override if service == "hub" else self.candidate_override,
            )
            if len(ids) != 1:
                raise HarnessFailure(
                    "initial Hub5 stack service did not start exactly once"
                )
        self.wait_ready()
        self.record_stage(
            "old-hub-stack-ready",
            hub_image_id=self.image_ids["old_hub"],
            lab_image_id=self.image_ids["old_lab"],
        )

    def wait_ready(self, timeout: int = 180) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8000/hub/lab/ready", timeout=5
                ) as response:
                    body = response.read(1025)
                    if readiness_is_ready(response.status, body):
                        return
            except (OSError, urllib.error.URLError, ValueError):
                time.sleep(1)
        raise HarnessFailure("Hub/proxy readiness did not pass before deadline")

    def capture_lab_ids(self, usernames: set[str], expected_image: str) -> None:
        assert self.pool is not None
        ids = self.docker("ps", "-aq", "--no-trunc", capture=True).decode().splitlines()
        for cid in filter(None, (item.strip() for item in ids)):
            if not FULL_CONTAINER_ID_RE.fullmatch(cid):
                raise HarnessFailure("Lab inventory returned a truncated container ID")
            item = self.inspect_container(cid)
            labels = (item.get("Config") or {}).get("Labels") or {}
            if labels.get(RUN_LABEL) != self.run_id:
                continue
            if labels.get("finki.role") == "lab":
                name = (item.get("Name") or "").lstrip("/")
                user = labels.get("finki.user")
                mounts = item.get("Mounts") or []
                home = next(
                    (m for m in mounts if m.get("Destination") == "/home/ubuntu"), None
                )
                if (
                    user not in usernames
                    or name != f"lab-{user}"
                    or item.get("Image") != expected_image
                    or not home
                    or Path(home.get("Source", "")).resolve()
                    != (self.pool / "users" / str(user)).resolve()
                ):
                    raise HarnessFailure(
                        "dynamic Lab ownership or home bind did not match this fixture"
                    )
                self.retained_lab_ids.add(cid)
                if not any(
                    resource.get("id") == cid
                    for resource in self.manifest["resources"]["containers"]
                ):
                    self.manifest["resources"]["containers"].append(
                        {
                            "id": cid,
                            "name": name,
                            "kind": "fixture-lab",
                            "label": self.run_id,
                            "removed": False,
                        }
                    )
        self.manifest["fixture_lab_ids"] = sorted(self.retained_lab_ids)
        self.save_manifest()

    def run_probe(self, stage: str) -> dict[str, Any]:
        self.active_stage = f"api-probe-{stage}"
        self.active_case = f"api-{stage}"
        assert self.run_root is not None and self.probe_dir is not None
        script = self.workspace / "scripts" / "integration" / "probe.py"
        probe_marker = self.probe_dir / "owner.json"
        if not probe_marker.exists():
            atomic_json(
                probe_marker,
                {"kind": "jupyterhub-migration-integration", "run_id": self.run_id},
            )
        mounts = [
            f"type=bind,source={script},target=/probe.py,readonly",
            f"type=bind,source={self.probe_dir},target=/runstate",
        ]
        command = [
            "/app/.venv/bin/python",
            "/probe.py",
            "--base",
            "http://127.0.0.1:8000",
            "--stage",
            stage,
            "--run-id",
            self.run_id,
            "--marker-file",
            "/runstate/owner.json",
            "--state-file",
            "/runstate/probe-state.json",
            "--acknowledge-disposable",
            "--stage-deadline",
            str(STAGE_TIMEOUT),
        ]
        code, output = self.run_owned_tool(
            self.image_ids["candidate_hub"],
            command,
            name_prefix="api-probe",
            mounts=mounts,
            network="host",
            timeout=PROBE_TIMEOUT,
        )
        if code:
            raise HarnessFailure(f"real API acceptance stage failed: {stage}")
        try:
            result = json.loads(output.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HarnessFailure("API probe returned invalid sanitized JSON") from exc
        cases = result.get("cases") if isinstance(result, dict) else None
        if (
            result.get("stage") != stage
            or not isinstance(cases, dict)
            or not cases
            or any(value != "pass" for value in cases.values())
        ):
            raise HarnessFailure(f"API acceptance stage had nonpassing cases: {stage}")
        self.add_case("api-" + stage, count=len(cases))
        self.record_stage("api-probe-" + stage, case_count=len(cases))
        if stage == "baseline":
            state = json.loads(
                (self.probe_dir / "probe-state.json").read_text(encoding="utf-8")
            )
            if (
                stat.S_IMODE((self.probe_dir / "probe-state.json").stat().st_mode)
                != 0o600
            ):
                raise HarnessFailure("probe credentials are not mode 0600")
            self.manifest["fixture_usernames_sha256"] = sorted(
                hashlib.sha256(item["username"].encode()).hexdigest()
                for item in state["identities"]
            )
            self.save_manifest()
            self.capture_lab_ids(
                {item["username"] for item in state["identities"]},
                self.image_ids["old_lab"],
            )
            self.expected_live_lab_image = self.image_ids["old_lab"]
        elif stage in {"candidate", "restore", "accepted-smoke"}:
            expected_lab = (
                self.image_ids["candidate_lab"]
                if stage in {"candidate", "accepted-smoke"}
                else self.image_ids["old_lab"]
            )
            self.capture_lab_ids(self.lab_names(), expected_lab)
            self.expected_live_lab_image = expected_lab
        return result

    def lab_names(self) -> set[str]:
        assert self.probe_dir is not None
        state = json.loads(
            (self.probe_dir / "probe-state.json").read_text(encoding="utf-8")
        )
        return {item["username"] for item in state["identities"]}

    def project_mapping(self, usernames: set[str]) -> dict[str, int]:
        assert self.pool is not None
        mapping: dict[str, int] = {}
        for line in (self.pool / ".projects").read_text(encoding="utf-8").splitlines():
            projid, sep, username = line.partition(":")
            if sep and username in usernames and projid.isdigit():
                mapping[username] = int(projid)
        if set(mapping) != usernames:
            raise HarnessFailure("XFS project ID map is missing a fixture user")
        return mapping

    def quota_hard_limit(self, projid: int, *, inode: bool) -> tuple[int, int]:
        report = self.quota_command("report -p -i -n" if inode else "report -p -b -n")
        for line in report.splitlines():
            fields = line.split()
            if not fields:
                continue
            match = re.match(r"#?(\d+)", fields[0])
            if not match or int(match.group(1)) != projid:
                continue
            numeric = [int(value) for value in fields[1:] if value.isdigit()]
            if len(numeric) < 3:
                continue
            # report columns are Used, Soft, Hard; output is private and never logged.
            return numeric[0], numeric[2]
        raise HarnessFailure("xfs_quota did not report a fixture project")

    @staticmethod
    def inode_project_id(path: Path) -> int:
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - full runtime is Linux-only
            raise HarnessFailure(
                "XFS inode project ID requires Linux ioctl support"
            ) from exc
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            attributes = bytearray(28)
            fcntl.ioctl(descriptor, FS_IOC_FSGETXATTR, attributes, True)
            return struct.unpack_from("=I", attributes, 12)[0]
        finally:
            os.close(descriptor)

    @classmethod
    def verify_inode_project_id(cls, path: Path, expected: int) -> int:
        actual = cls.inode_project_id(path)
        if actual != expected:
            raise HarnessFailure(
                "home inode XFS project ID differs from the project mapping"
            )
        return actual

    def pool_snapshot(self) -> dict[str, Any]:
        assert self.pool is not None and self.probe_dir is not None
        usernames = self.lab_names()
        mapping = self.project_mapping(usernames)
        identity_files = {}
        for filename in (".pool-id", ".projects", ".projid-counter", ".lock"):
            path = self.pool / filename
            info = path.stat()
            identity_files[filename] = {
                "sha256": digest(path),
                "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid,
                "gid": info.st_gid,
            }
        homes = {}
        for username, projid in mapping.items():
            home = self.pool / "users" / username
            if (
                home.is_symlink()
                or not home.is_dir()
                or home.parent.resolve() != (self.pool / "users").resolve()
            ):
                raise HarnessFailure("fixture home path escaped its marked pool")
            info = home.stat()
            if (info.st_uid, info.st_gid) != (1000, 1000):
                raise HarnessFailure(
                    "fixture home UID/GID differs from configured Lab identity"
                )
            inode_project_id = self.verify_inode_project_id(home, projid)
            marker = next(
                item["marker"]
                for item in json.loads(
                    (self.probe_dir / "probe-state.json").read_text(encoding="utf-8")
                )["identities"]
                if item["username"] == username
            )
            marker_path = home / f"migration-{marker[:16]}.txt"
            if (
                not marker_path.is_file()
                or digest(marker_path) != hashlib.sha256(marker.encode()).hexdigest()
            ):
                raise HarnessFailure("home persistence marker checksum mismatch")
            block_used, block_hard = self.quota_hard_limit(projid, inode=False)
            inode_used, inode_hard = self.quota_hard_limit(projid, inode=True)
            if (
                block_hard not in {100 * 1024, 200 * 1024, 100 * 1024 * 1024 // 512}
                or inode_hard != 20_000
            ):
                raise HarnessFailure(
                    "actual XFS hard limits differ from fixture quota configuration"
                )
            homes[hashlib.sha256(username.encode()).hexdigest()] = {
                "uid": info.st_uid,
                "gid": info.st_gid,
                "projid": projid,
                "inode_project_id": inode_project_id,
                "marker_sha256": digest(marker_path),
                "block_hard": block_hard,
                "inode_hard": inode_hard,
                "block_used": block_used,
                "inode_used": inode_used,
            }
        return {
            "pool_id_sha256": digest(self.pool / ".pool-id"),
            "identity_files": identity_files,
            "homes": homes,
        }

    def assert_pool_preserved(
        self, before: dict[str, Any], stage: str
    ) -> dict[str, Any]:
        after = self.pool_snapshot()
        if (
            before["pool_id_sha256"] != after["pool_id_sha256"]
            or before["identity_files"] != after["identity_files"]
        ):
            raise HarnessFailure(
                f"pool identity/project metadata changed during {stage}"
            )
        for username_hash, old in before["homes"].items():
            new = after["homes"].get(username_hash)
            keys = ("uid", "gid", "projid", "marker_sha256", "block_hard", "inode_hard")
            if new is None or any(new[key] != old[key] for key in keys):
                raise HarnessFailure(
                    f"home ownership/project/quota marker changed during {stage}"
                )
        return after

    def run_helper(
        self,
        verb: str,
        backup: Path,
        *,
        acceptance: bool = False,
        restore: bool = False,
        preflight: bool = False,
    ) -> dict[str, Any]:
        self.active_stage = f"maintenance-{verb}"
        self.active_case = f"maintenance-{verb}"
        assert (
            self.project_dir is not None
            and self.env_file is not None
            and self.compose is not None
            and self.candidate_override is not None
            and self.run_root is not None
        )
        helper = self.workspace / "scripts" / "jupyterhub-maintenance.sh"
        argv = [
            "/bin/sh",
            str(helper),
            verb,
            "--project-directory",
            str(self.project_dir),
            "--project-name",
            self.project_name,
            "--env-file",
            str(self.env_file),
            "--compose-file",
            str(self.compose),
            "--compose-file",
            str(self.candidate_override),
            "--backup-dir",
            str(backup),
        ]
        if not preflight:
            argv.extend(
                [
                    "--acknowledge-interruption",
                    "--acknowledge-ingress-fenced",
                    "--acknowledge-updater-paused",
                ]
            )
        if acceptance:
            argv.append("--acceptance-passed")
        if restore:
            argv.append("--acknowledge-restore")
        if verb in {"migrate", "restore", "accept"}:
            self.register_compose_intents(
                ["hub", "proxy", "web"],
                backup / "configuration" / "jupyterhub_maintenance_config.py",
            )
        env = self.fixture_runtime_env()
        result = safe_call(
            argv,
            timeout=900 if verb == "migrate" else 600,
            cwd=self.workspace,
            env=env,
            capture=True,
        )
        if result.returncode:
            # helper emits sanitized JSON errors. Do not copy command output to CI logs.
            raise HarnessFailure(f"shipping maintenance helper {verb} failed")
        try:
            output = json.loads((result.stdout or b"").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HarnessFailure(
                "shipping maintenance helper returned invalid JSON"
            ) from exc
        if verb == "accept":
            self.verify_acceptance_paths(backup, output)
        if verb != "preflight":
            for service in ("web", "proxy", "hub"):
                self.compose_ids(service)
            for resource in self.manifest["resources"]["containers"]:
                if resource.get("kind") != "fixture-lab" or resource.get("removed"):
                    continue
                self.reconcile_helper_lab_presence(resource)
            self.save_manifest()
        return output

    @staticmethod
    def docker_inspect_not_found(
        result: subprocess.CompletedProcess[bytes], container_id: str
    ) -> bool:
        if result.returncode == 0:
            return False
        stdout = (result.stdout or b"").strip()
        if stdout:
            try:
                inspected = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return False
            if not isinstance(inspected, list) or inspected:
                return False
        message = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        return message in {
            f"Error: No such object: {container_id}",
            f"Error: No such container: {container_id}",
            f"Error response from daemon: No such container: {container_id}",
        }

    def reconcile_helper_lab_presence(self, resource: dict[str, Any]) -> None:
        container_id = resource.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise HarnessFailure("fixture Lab record has no container ID")
        inspected = safe_call(
            ["docker", "inspect", container_id], timeout=20, capture=True
        )
        if inspected.returncode == 0:
            return
        if self.docker_inspect_not_found(inspected, container_id):
            resource["removed"] = True
            self.save_manifest()
            return
        raise HarnessFailure(
            "could not verify fixture Lab presence after helper execution"
        )

    def verify_acceptance_paths(
        self, backup: Path, output: dict[str, Any]
    ) -> tuple[Path, Path]:
        if self.project_dir is None:
            raise HarnessFailure("fixture project is unavailable during acceptance")
        runtime_value = output.get("runtime_override")
        marker_value = output.get("marker")
        if not isinstance(runtime_value, str) or not isinstance(marker_value, str):
            raise HarnessFailure("maintenance accept omitted protected path evidence")
        backup_root = backup.resolve(strict=True)
        runtime_input = Path(runtime_value)
        marker_input = Path(marker_value)
        if runtime_input.is_symlink() or marker_input.is_symlink():
            raise HarnessFailure("maintenance accept returned a symlinked path")
        runtime_path = runtime_input.resolve(strict=True)
        marker_path = marker_input.resolve(strict=True)
        if (
            backup_root not in runtime_path.parents
            or not runtime_path.is_file()
            or stat.S_IMODE(runtime_path.stat().st_mode) != 0o600
            or marker_path
            != (self.project_dir / ".jupyterhub-maintenance.json").resolve()
        ):
            raise HarnessFailure(
                "maintenance accept paths are outside the owned fixture"
            )
        state = json.loads((backup_root / "state.json").read_text(encoding="utf-8"))
        stages = state.get("stages", [])
        if (
            not stages
            or stages[-1].get("stage") != "accepted"
            or Path(stages[-1].get("runtime_override", "")).resolve() != runtime_path
        ):
            raise HarnessFailure(
                "maintenance accept path differs from its persisted stage"
            )
        return runtime_path, marker_path

    def reject_unasserted_accept(self, backup: Path) -> None:
        assert (
            self.project_dir is not None
            and self.env_file is not None
            and self.compose is not None
            and self.candidate_override is not None
            and self.run_root is not None
        )
        helper = self.workspace / "scripts" / "jupyterhub-maintenance.sh"
        env = self.fixture_runtime_env()
        result = safe_call(
            [
                "/bin/sh",
                str(helper),
                "accept",
                "--project-directory",
                str(self.project_dir),
                "--project-name",
                self.project_name,
                "--env-file",
                str(self.env_file),
                "--compose-file",
                str(self.compose),
                "--compose-file",
                str(self.candidate_override),
                "--backup-dir",
                str(backup),
                "--acknowledge-interruption",
                "--acknowledge-ingress-fenced",
                "--acknowledge-updater-paused",
            ],
            timeout=120,
            cwd=self.workspace,
            env=env,
            capture=True,
        )
        try:
            payload = json.loads((result.stderr or b"").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HarnessFailure(
                "unasserted accept returned no sanitized refusal"
            ) from exc
        if result.returncode == 0 or "--acceptance-passed" not in payload.get(
            "error", ""
        ):
            raise HarnessFailure("maintenance helper accepted without the gate")
        web_ids = self.compose_ids("web")
        proxy_ids = self.compose_ids("proxy")
        hub_ids = self.compose_ids("hub")
        if len(web_ids) != 1 or len(proxy_ids) != 1 or len(hub_ids) != 1:
            raise HarnessFailure("unasserted accept changed private service inventory")
        states = {
            service: bool(
                (self.inspect_container(cid).get("State") or {}).get("Running")
            )
            for service, cid in (
                ("web", web_ids[0]),
                ("proxy", proxy_ids[0]),
                ("hub", hub_ids[0]),
            )
        }
        if states != {"web": False, "proxy": True, "hub": True}:
            raise HarnessFailure("unasserted accept changed the private ingress fence")
        self.add_case("maintenance-accept-requires-explicit-success", status="pass")
        self.record_stage("unasserted-accept-refused-web-still-fenced")

    def run_updater_refusal(self) -> None:
        self.active_stage = "routine-updater-refusal"
        self.active_case = "routine-updater-refusal-old-stack-usable"
        assert self.project_dir is not None and self.run_root is not None
        updater = self.workspace / "scripts" / "update.sh"
        env = self.fixture_runtime_env()
        old_service_ids = self.snapshot_service_ids()
        result = safe_call(
            ["/bin/sh", str(updater), str(self.project_dir)],
            timeout=UPDATE_TIMEOUT,
            cwd=self.workspace,
            env=env,
            capture=True,
        )
        combined = ((result.stdout or b"") + (result.stderr or b"")).decode(
            "utf-8", errors="replace"
        )
        expected_change = "Hub version change 5.5.1 -> 6.0.1"
        expected_unknown = "running Hub version is unknown; refusing update"
        if result.returncode == 0 or not (
            expected_change in combined or expected_unknown in combined
        ):
            raise HarnessFailure(
                "routine updater did not refuse before replacing the old stack"
            )
        if self.snapshot_service_ids() != old_service_ids:
            raise HarnessFailure(
                "routine updater refusal changed old service identities"
            )
        self.wait_ready()
        self.add_case(
            "routine-updater-refusal-old-stack-usable",
            status="pass",
            old_hub_version="5.5.1",
            candidate_hub_version="6.0.1",
            refusal="unknown-running-image-label"
            if expected_unknown in combined
            else "version-change",
        )
        self.record_stage("routine-updater-refused-before-replacement")

    def service_id(self, service: str) -> str:
        ids = self.compose_ids(service)
        if len(ids) != 1:
            raise HarnessFailure("expected exactly one live Compose service")
        return ids[0]

    def web_probe(self) -> None:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:8080/config.json", timeout=5
            ) as response:
                body = json.loads(response.read(1024))
                if response.status != 200 or body != {"sitekey": ""}:
                    raise HarnessFailure(
                        "loopback web ingress did not return fixture config"
                    )
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise HarnessFailure("loopback web ingress probe failed") from exc
        self.add_case("web-loopback-acceptance", status="pass")

    def normalize_old_baseline(self) -> None:
        self.compose_command(
            "up",
            "-d",
            "--force-recreate",
            "--pull",
            "never",
            "hub",
            override=self.initial_override,
            timeout=180,
        )
        if len(self.compose_ids("hub", self.initial_override)) != 1:
            raise HarnessFailure("normalized Hub 5 service did not start exactly once")
        self.wait_ready()
        self.verify_hub_mode(
            expected_image=self.image_ids["old_hub"],
            upgrade_db=False,
            suppress_cullers=False,
        )
        self.run_probe("restore")
        self.record_stage("second-transaction-baseline-normalized")

    def clear_disposable_interlock(
        self, backup: Path, accepted: dict[str, Any]
    ) -> None:
        assert self.project_dir is not None
        marker = Path(accepted["marker"])
        runtime = Path(accepted["runtime_override"])
        value = json.loads(marker.read_text(encoding="utf-8"))
        if Path(value.get("backup_directory", "")).resolve() != backup.resolve():
            raise HarnessFailure(
                "refusing to clear an interlock not owned by this test backup"
            )
        status = json.loads((backup / "state.json").read_text(encoding="utf-8"))
        if not status.get("stages") or status["stages"][-1].get("stage") != "accepted":
            raise HarnessFailure(
                "disposable interlock clear requires successful helper acceptance"
            )
        if (
            Path(status["stages"][-1]["runtime_override"]).resolve()
            != runtime.resolve()
            or not runtime.is_file()
            or stat.S_IMODE(runtime.stat().st_mode) != 0o600
        ):
            raise HarnessFailure("accepted runtime override is not protected")
        if marker.is_symlink() or stat.S_IMODE(marker.stat().st_mode) != 0o600:
            raise HarnessFailure("maintenance marker ownership/permissions mismatch")
        marker.unlink()
        self.record_stage(
            "disposable-test-interlock-cleared-after-verified-accept",
            backup_sha256=digest(backup / "manifest.json"),
        )

    def snapshot_service_ids(self) -> dict[str, str]:
        return {
            service: self.service_id(service) for service in ("web", "proxy", "hub")
        }

    def scenario(self) -> None:
        assert (
            self.run_root is not None
            and self.project_dir is not None
            and self.pool is not None
        )
        self.setup_network()
        self.write_compose_fixture()
        self.start_old_stack()
        self.run_probe("baseline")
        users = self.lab_names()
        before = self.pool_snapshot()
        old_ids = self.snapshot_service_ids()
        # This runs the shipping updater with candidates already available from
        # the private local registry; its actual major-version guard must fire.
        self.run_updater_refusal()
        if self.snapshot_service_ids() != old_ids:
            raise HarnessFailure("routine update changed old Hub/web/proxy IDs")
        self.run_probe("updater-smoke")
        self.add_case("old-stack-usable-after-updater-refusal", status="pass")
        backup_root = self.run_root / "backups"
        backup1 = backup_root / "rollback-cycle"
        self.backups["rollback"] = backup1
        self.run_helper("preflight", backup1, preflight=True)
        migrated = self.run_helper("migrate", backup1)
        if migrated.get("stage") != "candidate-private":
            raise HarnessFailure(
                "maintenance migrate did not leave the candidate private"
            )
        self.verify_hub_mode(
            expected_image=self.image_ids["candidate_hub"],
            upgrade_db=False,
            suppress_cullers=True,
        )
        self.run_probe("candidate")
        self.add_case(
            "hub6-normal-start-after-explicit-upgrade-db",
            status="pass",
            allow_db_upgrade=False,
            migration_disabled=True,
            cullers_suppressed=True,
            readiness_probe="candidate",
        )
        self.restart_private_candidate()
        candidate_facts = self.assert_pool_preserved(
            before, "first candidate migration"
        )
        self.add_case(
            "candidate-home-project-quota-preserved",
            status="pass",
            users=len(candidate_facts["homes"]),
        )
        self.reject_unasserted_accept(backup1)
        restored = self.run_helper("restore", backup1, restore=True)
        if restored.get("stage") != "restored-private":
            raise HarnessFailure(
                "maintenance restore did not return old pair privately"
            )
        self.verify_hub_mode(
            expected_image=self.image_ids["old_hub"],
            upgrade_db=False,
            suppress_cullers=True,
        )
        self.run_probe("restore")
        restored_facts = self.assert_pool_preserved(before, "cold rollback restore")
        self.add_case(
            "rollback-old-pair-identity-files-quotas-ws",
            status="pass",
            users=len(restored_facts["homes"]),
        )
        accepted_old = self.run_helper("accept", backup1, acceptance=True)
        if (
            accepted_old.get("stage") != "accepted"
            or accepted_old.get("web") != "started"
        ):
            raise HarnessFailure(
                "restore acceptance did not activate the verified old pair"
            )
        self.web_probe()
        self.verify_hub_mode(
            expected_image=self.image_ids["old_hub"],
            upgrade_db=False,
            suppress_cullers=False,
        )
        self.clear_disposable_interlock(backup1, accepted_old)

        # Normalize only disposable Hub 5; backup one remains immutable.
        self.normalize_old_baseline()

        # A second, isolated transaction proves successful candidate acceptance
        # after the rollback scenario without weakening the failure path.
        backup2 = backup_root / "candidate-accept-cycle"
        self.backups["candidate"] = backup2
        self.run_helper("preflight", backup2, preflight=True)
        migrated = self.run_helper("migrate", backup2)
        if migrated.get("stage") != "candidate-private":
            raise HarnessFailure(
                "second maintenance migration did not produce private candidate"
            )
        self.verify_hub_mode(
            expected_image=self.image_ids["candidate_hub"],
            upgrade_db=False,
            suppress_cullers=True,
        )
        self.run_probe("candidate")
        candidate_facts = self.assert_pool_preserved(
            before, "candidate acceptance cycle"
        )
        self.add_case(
            "second-candidate-home-project-quota-preserved",
            status="pass",
            users=len(candidate_facts["homes"]),
        )
        accepted = self.run_helper("accept", backup2, acceptance=True)
        if accepted.get("stage") != "accepted" or accepted.get("web") != "started":
            raise HarnessFailure("candidate maintenance acceptance did not complete")
        self.verify_hub_mode(
            expected_image=self.image_ids["candidate_hub"],
            upgrade_db=False,
            suppress_cullers=False,
        )
        self.web_probe()
        self.run_probe("accepted-smoke")
        final_facts = self.assert_pool_preserved(
            before, "normal accepted candidate runtime"
        )
        self.add_case(
            "accepted-candidate-normal-runtime",
            status="pass",
            users=len(final_facts["homes"]),
        )
        self.record_stage(
            "full-migration-restore-acceptance-complete",
            old_image=self.image_ids["old_hub"],
            candidate_image=self.image_ids["candidate_hub"],
        )
        # Preserve enough in-run facts for targeted cleanup; no raw identity escapes.
        (self.run_root / "fixture-users.sha256").write_text(
            "\n".join(self.manifest["fixture_usernames_sha256"]) + "\n",
            encoding="ascii",
        )
        (self.run_root / "fixture-users.sha256").chmod(0o600)
        self.run_probe("cleanup")
        self.cleanup_dynamic_labs(users)

    def verify_hub_mode(
        self, *, expected_image: str, upgrade_db: bool, suppress_cullers: bool
    ) -> None:
        ids = self.compose_ids("hub")
        if len(ids) != 1:
            raise HarnessFailure("private Hub container is missing")
        item = self.inspect_container(ids[0])
        if item.get("Image") != expected_image:
            raise HarnessFailure("maintenance stage selected the wrong Hub image ID")
        env = {}
        for value in (item.get("Config") or {}).get("Env") or []:
            key, sep, val = value.partition("=")
            if sep:
                env[key] = val
        if env.get("JUPYTERHUB_ALLOW_DB_UPGRADE") != "false":
            raise HarnessFailure("restored old Hub is not migration-disabled")
        private_mode = (
            "JUPYTERHUB_MAINTENANCE_UPGRADE_DB" in env
            or "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS" in env
        )
        if private_mode:
            if env.get("JUPYTERHUB_MAINTENANCE_UPGRADE_DB") != (
                "true" if upgrade_db else "false"
            ):
                raise HarnessFailure("maintenance Hub upgrade mode differs from stage")
            if env.get("JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS") != (
                "true" if suppress_cullers else "false"
            ):
                raise HarnessFailure(
                    "culler suppression is not independent/explicit for stage"
                )
        elif suppress_cullers or upgrade_db:
            raise HarnessFailure(
                "private Hub is missing fixed maintenance wrapper mode"
            )
        elif expected_image == self.image_ids["old_hub"] and not suppress_cullers:
            raise HarnessFailure(
                "accepted old Hub is missing its migration-disabled wrapper"
            )

    def restart_private_candidate(self) -> None:
        self.verify_hub_mode(
            expected_image=self.image_ids["candidate_hub"],
            upgrade_db=False,
            suppress_cullers=True,
        )
        hub_id = self.service_id("hub")
        item = self.inspect_container(hub_id)
        labels = (item.get("Config") or {}).get("Labels") or {}
        if (
            labels.get("com.docker.compose.project") != self.project_name
            or labels.get("com.docker.compose.service") != "hub"
            or labels.get("com.docker.compose.project.working_dir")
            != str(self.project_dir)
        ):
            raise HarnessFailure(
                "candidate restart target is not the owned Hub service"
            )
        self.docker("restart", "--time", "30", hub_id, timeout=45)
        self.wait_ready()
        self.verify_hub_mode(
            expected_image=self.image_ids["candidate_hub"],
            upgrade_db=False,
            suppress_cullers=True,
        )
        self.run_probe("candidate")
        self.add_case(
            "hub6-second-server-boot-after-upgrade-db",
            status="pass",
            allow_db_upgrade=False,
            cullers_suppressed=True,
            readiness_probe="candidate",
        )
        self.record_stage("candidate-second-normal-server-boot-verified")

    def cleanup_dynamic_labs(self, usernames: set[str]) -> None:
        assert self.pool is not None
        ids = self.docker("ps", "-aq", "--no-trunc", capture=True).decode().splitlines()
        for cid in filter(None, (x.strip() for x in ids)):
            if not FULL_CONTAINER_ID_RE.fullmatch(cid):
                raise HarnessFailure(
                    "cleanup inventory returned a truncated container ID"
                )
            item = self.inspect_container(cid)
            labels = (item.get("Config") or {}).get("Labels") or {}
            if (
                labels.get(RUN_LABEL) != self.run_id
                or labels.get("finki.role") != "lab"
            ):
                continue
            name = (item.get("Name") or "").lstrip("/")
            user = labels.get("finki.user")
            mounts = item.get("Mounts") or []
            home = next(
                (
                    mount
                    for mount in mounts
                    if mount.get("Destination") == "/home/ubuntu"
                ),
                None,
            )
            if (
                user not in usernames
                or name != f"lab-{user}"
                or item.get("Image")
                not in (
                    {self.expected_live_lab_image}
                    if self.expected_live_lab_image
                    else {self.image_ids["old_lab"], self.image_ids["candidate_lab"]}
                )
                or not home
                or Path(home.get("Source", "")).resolve()
                != (self.pool / "users" / user).resolve()
            ):
                raise HarnessFailure(
                    "cleanup refused a Lab with mismatched run/user/name/home ownership"
                )
            if not any(
                resource.get("id") == cid
                for resource in self.manifest["resources"]["containers"]
            ):
                self.manifest["resources"]["containers"].append(
                    {
                        "id": cid,
                        "name": name,
                        "kind": "fixture-lab",
                        "label": self.run_id,
                        "removed": False,
                    }
                )
                self.save_manifest()
            self.remove_owned_container(cid)
            self.retained_lab_ids.discard(cid)

    def cleanup_compose(self) -> None:
        # Operate only on recorded project containers after rechecking project
        # and service labels. TERM and exact-ID rm avoid Compose down/global cleanup.
        ids = self.docker("ps", "-aq", "--no-trunc", capture=True).decode().splitlines()
        inspected: list[tuple[int, str, dict[str, Any]]] = []
        order = {"web": 0, "proxy": 1, "hub": 2}
        for cid in filter(None, (x.strip() for x in ids)):
            if not FULL_CONTAINER_ID_RE.fullmatch(cid):
                raise HarnessFailure(
                    "cleanup inventory returned a truncated container ID"
                )
            item = self.inspect_container(cid)
            labels = (item.get("Config") or {}).get("Labels") or {}
            if item.get("Id") != cid:
                raise HarnessFailure("inspected Compose ID differed from inventory ID")
            if labels.get("com.docker.compose.project") != self.project_name:
                continue
            service = labels.get("com.docker.compose.service")
            inspected.append((order.get(str(service), 3), cid, item))
        for _rank, cid, item in sorted(inspected, key=lambda entry: entry[0]):
            labels = (item.get("Config") or {}).get("Labels") or {}
            if labels.get("com.docker.compose.service") not in {"web", "proxy", "hub"}:
                raise HarnessFailure(
                    "unexpected project container is not eligible for teardown"
                )
            service = labels["com.docker.compose.service"]
            name = f"{self.project_name}-{service}-1"
            intent = next(
                (
                    resource
                    for resource in reversed(self.manifest["resources"]["containers"])
                    if resource.get("kind") == "compose-intent"
                    and resource.get("name") == name
                    and resource.get("project") == self.project_name
                ),
                None,
            )
            if intent is None:
                raise HarnessFailure("Compose container has no durable creation intent")
            self.verify_compose_resource(item, intent)
            intent["id"] = cid
            intent["intent"] = False
            self.service_ids.add(cid)
            if bool((item.get("State") or {}).get("Running")):
                self.docker("update", "--restart=no", cid, timeout=20)
                self.docker("kill", "--signal=TERM", cid, timeout=20)
                until = time.monotonic() + 60
                while time.monotonic() < until:
                    item = self.inspect_container(cid)
                    if not bool((item.get("State") or {}).get("Running")):
                        break
                    time.sleep(1)
                if bool((item.get("State") or {}).get("Running")):
                    raise HarnessFailure(
                        "Compose service did not exit after bounded TERM"
                    )
            self.docker("rm", cid, timeout=30)
            self.verify_container_absent(cid)
            intent["removed"] = True
            self.save_manifest()
        for resource in self.manifest["resources"]["containers"]:
            if resource.get("kind") != "compose-intent" or resource.get("removed"):
                continue
            resource_id = resource.get("id")
            if isinstance(resource_id, str):
                self.verify_container_absent(resource_id)
            else:
                remaining = (
                    self.docker(
                        "ps",
                        "-aq",
                        "--filter",
                        f"name=^/{resource.get('name')}$",
                        capture=True,
                    )
                    .decode()
                    .strip()
                )
                if remaining:
                    raise HarnessFailure("unreconciled Compose creation intent remains")
            resource["removed"] = True
            self.save_manifest()

    def cleanup_network(self) -> None:
        if not self.network_id:
            if any(
                not resource.get("removed")
                for resource in self.manifest["resources"]["networks"]
            ):
                raise HarnessFailure("network creation intent remains unresolved")
            return
        item = self.docker_json("network", "inspect", self.network_id)[0]
        resource = next(
            (
                network
                for network in self.manifest["resources"]["networks"]
                if network.get("id") == self.network_id and not network.get("removed")
            ),
            None,
        )
        if resource is None:
            raise HarnessFailure("refusing to remove a network without creation intent")
        self.verify_network_resource(item)
        if item.get("Containers") or {}:
            raise HarnessFailure("run-owned users network still has endpoints")
        self.docker("network", "rm", self.network_id)
        result = safe_call(
            ["docker", "network", "inspect", self.network_id],
            timeout=15,
            capture=True,
        )
        message = (result.stderr or b"").decode("utf-8", errors="replace").lower()
        if result.returncode == 0 or "no such network" not in message:
            raise HarnessFailure("users network removal could not be verified")
        self.manifest["resources"]["networks"] = [
            {**network, "removed": True, "intent": False}
            if network.get("id") == self.network_id
            else network
            for network in self.manifest["resources"]["networks"]
        ]
        self.network_id = None
        self.save_manifest()

    def cleanup_registry(self) -> None:
        if self.registry_id is None:
            registry_resource = next(
                (
                    resource
                    for resource in self.manifest["resources"]["containers"]
                    if resource.get("kind") == "local-image-registry"
                    and not resource.get("removed")
                ),
                None,
            )
            if registry_resource is not None:
                if registry_resource.get("id") is None:
                    self.reconcile_container_intent(registry_resource)
                if not registry_resource.get("removed"):
                    registry_id = registry_resource.get("id")
                    if not isinstance(registry_id, str):
                        raise HarnessFailure(
                            "registry creation intent lacks an owned ID"
                        )
                    self.registry_id = registry_id
        if self.registry_id:
            item = self.inspect_container(self.registry_id)
            labels = (item.get("Config") or {}).get("Labels") or {}
            if (
                (item.get("Name") or "").lstrip("/") != f"jh6-registry-{self.run_id}"
                or labels.get(RUN_LABEL) != self.run_id
                or item.get("Image") != self.image_ids.get("registry")
            ):
                raise HarnessFailure(
                    "refusing to remove a registry container with mismatched ownership"
                )
            self.remove_owned_container(self.registry_id, force=True)
            self.registry_id = None

    def cleanup_owned_tools(self) -> None:
        tool_kinds = {"version-probe", "api-probe"}
        for resource in self.manifest["resources"]["containers"]:
            if resource.get("kind") not in tool_kinds or resource.get("removed"):
                continue
            if resource.get("id") is None:
                self.reconcile_container_intent(resource)
            if resource.get("removed"):
                continue
            container_id = resource.get("id")
            if not isinstance(container_id, str):
                raise HarnessFailure("owned tool cleanup lacks an inspected ID")
            self.remove_owned_container(container_id, force=True)

    def cleanup_pool(self) -> None:
        if self.pool is None:
            if self.loop_device or self.manifest.get("loop_backing_intent"):
                raise HarnessFailure("loop backing intent has no owned pool path")
            return
        if any(
            not resource.get("removed")
            for resource in self.manifest["resources"]["containers"]
        ):
            raise HarnessFailure("cannot remove XFS pool while owned containers remain")
        if self.loop_device is None and self.manifest.get("loop_backing_intent"):
            if self.run_root is None:
                raise HarnessFailure("loop backing intent lacks an owned run root")
            listed = safe_call(
                ["losetup", "--json", "--list", "--output", "NAME,BACK-FILE"],
                timeout=15,
                capture=True,
            )
            if listed.returncode:
                raise HarnessFailure("could not reconcile loop attachment intent")
            try:
                entries = json.loads(listed.stdout or b"").get("loopdevices", [])
            except (json.JSONDecodeError, AttributeError) as exc:
                raise HarnessFailure("loop attachment inventory was malformed") from exc
            owned = [
                entry
                for entry in entries
                if Path(str(entry.get("back-file", ""))).resolve()
                == Path(str(self.manifest["loop_backing_intent"])).resolve()
            ]
            if len(owned) > 1:
                raise HarnessFailure("multiple loops claim the fixture backing image")
            if owned:
                name = owned[0].get("name")
                if not isinstance(name, str) or not name.startswith("/dev/loop"):
                    raise HarnessFailure("loop intent resolved to an invalid device")
                self.loop_device = name
                self.manifest["loop_device"] = name
                self.save_manifest()
            else:
                self.manifest["pool_unmounted"] = True
                self.save_manifest()
        if not self.pool.exists():
            if self.loop_device:
                raise HarnessFailure("owned loop is attached but pool path is missing")
            return
        if (
            self.pool.is_symlink()
            or self.run_root is None
            or self.pool.parent.resolve() != self.run_root.resolve()
        ):
            raise HarnessFailure("pool mount path escaped the marked run root")
        if self.loop_device:
            mounted = safe_call(
                ["findmnt", "-rn", "-S", self.loop_device, "-o", "TARGET"],
                timeout=15,
                capture=True,
            )
            target = (mounted.stdout or b"").decode().strip()
            if mounted.returncode not in {0, 1}:
                raise HarnessFailure("could not inspect the recorded loop mount")
            source_check = safe_call(
                [
                    "findmnt",
                    "-rn",
                    "-T",
                    str(self.pool),
                    "-o",
                    "SOURCE,TARGET",
                ],
                timeout=15,
                capture=True,
            )
            fields = (source_check.stdout or b"").decode().strip().split()
            if source_check.returncode or len(fields) != 2:
                raise HarnessFailure("could not verify fixture mount source/target")
            pool_is_mount = Path(fields[1]).resolve() == self.pool.resolve()
            if pool_is_mount and (
                fields[0] != self.loop_device
                or not target
                or Path(target).resolve() != self.pool.resolve()
            ):
                raise HarnessFailure(
                    "recorded loop source/target differs from the fixture mount"
                )
            if target and (
                not pool_is_mount or Path(target).resolve() != self.pool.resolve()
            ):
                raise HarnessFailure("recorded loop is mounted at an unexpected target")
            backing = (
                require_call(
                    [
                        "losetup",
                        "--noheadings",
                        "--output",
                        "BACK-FILE",
                        self.loop_device,
                    ],
                    timeout=15,
                    capture=True,
                )
                .decode()
                .strip()
            )
            if Path(backing).resolve() != (self.run_root / "pool.img").resolve():
                raise HarnessFailure(
                    "refusing to detach a loop device with a different backing image"
                )
            if pool_is_mount:
                require_call(["umount", str(self.pool)], timeout=30)
            require_call(["losetup", "-d", self.loop_device], timeout=20)
            self.manifest["loop_device"] = None
            self.manifest["pool_unmounted"] = True
            self.loop_device = None
            self.save_manifest()

    def remove_run_images(self) -> None:
        # Remove only the exact local tags created for this run; never prune.
        for ref, expected in self.owned_image_refs.items():
            try:
                actual = self.image_id(ref)
            except HarnessFailure:
                continue
            if actual != expected:
                raise HarnessFailure(
                    "run image tag points to a different immutable ID; refusing cleanup"
                )
            result = safe_call(["docker", "image", "rm", ref], timeout=30)
            if result.returncode:
                raise HarnessFailure("could not remove an exact run-owned image tag")
            for resource in self.manifest["resources"]["images"]:
                if resource.get("ref") == ref:
                    resource["removed"] = True
            self.save_manifest()

    def cleanup_run_root(self) -> None:
        if not self.created_run_root or self.run_root is None or self.marker is None:
            return
        if self.loop_device or (self.pool and os.path.ismount(self.pool)):
            raise HarnessFailure("run root still contains an attached or mounted pool")
        if self.manifest.get("loop_device") or (
            self.manifest.get("loop_backing_intent")
            and not self.manifest.get("pool_unmounted")
        ):
            raise HarnessFailure(
                "run root still has an unresolved loop attachment intent"
            )
        if any(
            not resource.get("removed")
            for resource in self.manifest["resources"]["containers"]
        ) or any(
            not resource.get("removed")
            for resource in self.manifest["resources"]["networks"]
        ):
            raise HarnessFailure("run root still owns live container/network resources")
        if (
            self.run_root.is_symlink()
            or self.run_root.resolve().parent
            != Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve()
        ):
            raise HarnessFailure(
                "refusing recursive cleanup outside the exact runner temp root"
            )
        value = json.loads(self.marker.read_text(encoding="utf-8"))
        if value.get("run_id") != self.run_id or value.get("run_root") != str(
            self.run_root.resolve()
        ):
            raise HarnessFailure("run-root ownership marker did not match")
        self.results["ownership_manifest_sha256"] = digest(self.marker)
        self.results["owned_resources"] = {
            "containers": len(self.manifest["resources"]["containers"]),
            "removed_containers": sum(
                bool(item.get("removed"))
                for item in self.manifest["resources"]["containers"]
            ),
            "networks": len(self.manifest["resources"]["networks"]),
            "images": len(self.manifest["resources"]["images"]),
            "removed_images": sum(
                bool(item.get("removed"))
                for item in self.manifest["resources"]["images"]
            ),
        }
        shutil.rmtree(self.run_root)
        self.created_run_root = False

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        try:
            # Public ingress and Hub control-plane are fenced before dynamic Labs.
            self.cleanup_compose()
        except Exception:
            failures.append("compose-control-plane")
        if not failures:
            try:
                # Re-enumerate Labs only after the control plane is stopped.
                state_path = (
                    self.probe_dir / "probe-state.json" if self.probe_dir else None
                )
                usernames = (
                    self.lab_names() if state_path and state_path.is_file() else set()
                )
                self.cleanup_dynamic_labs(usernames)
            except Exception:
                failures.append("dynamic-labs")
        for stage, action in (
            ("owned-tools", self.cleanup_owned_tools),
            ("registry", self.cleanup_registry),
        ):
            try:
                action()
            except Exception:
                failures.append(stage)
        container_failures = bool(failures)
        if not container_failures:
            try:
                self.cleanup_network()
            except Exception:
                failures.append("users-network")
        else:
            failures.append("users-network-blocked-by-live-container")
        if not failures:
            try:
                self.cleanup_pool()
            except Exception:
                failures.append("xfs-pool")
        else:
            failures.append("xfs-pool-preserved-after-upstream-cleanup-failure")
        if not failures:
            try:
                self.remove_run_images()
            except Exception:
                failures.append("run-images")
        if not failures:
            try:
                self.cleanup_run_root()
            except Exception:
                failures.append("run-root-preserved")
        self.results["cleanup_stages_failed"] = failures
        if failures:
            self.results["cleanup_incomplete"] = failures
            if self.created_run_root and self.run_root is not None:
                self.results["preserved_run_root"] = str(self.run_root)
        return failures

    def run(self) -> int:
        self.preflight()
        self.create_run_root()
        self.stage_deadline = time.monotonic() + OVERALL_TEST_TIMEOUT
        try:
            self.setup_xfs_pool()
            self.build_images()
            self.scenario()
            self.results["runtime"] = "pass"
            self.results["source"] = {
                "baseline": self.baseline_sha,
                "candidate": self.candidate_sha,
            }
            self.results["images"] = {
                key: value for key, value in self.image_ids.items() if key != "registry"
            }
            self.results["stages"] = len(self.manifest["stages"])
            self.record_stage("integration-run-passed")
            self.stage_deadline = time.monotonic() + 300
            failures = self.cleanup()
            if failures:
                self.results["runtime"] = "fail"
                self.results["cleanup"] = "incomplete"
                self.results["remaining_owned_resources"] = (
                    self.cleanup_resource_summary()
                )
                print(json.dumps(self.results, sort_keys=True))
                return 1
            self.results["cleanup"] = "verified"
            print(json.dumps(self.results, sort_keys=True))
            return 0
        except Exception:
            self.results["runtime"] = "fail"
            self.results["failure"] = "bounded-integration-stage-failed"
            self.results["failed_stage"] = self.active_stage
            if self.failure_context is not None:
                self.results["failure_context"] = self.failure_context.copy()
            if self.active_case:
                self.results["failed_case"] = self.active_case
            self.stage_deadline = time.monotonic() + 300
            failures = self.cleanup()
            if failures:
                self.results["cleanup"] = "incomplete"
                self.results["remaining_owned_resources"] = (
                    self.cleanup_resource_summary()
                )
            else:
                self.results["cleanup"] = "verified"
            print(json.dumps(self.results, sort_keys=True))
            return 1

    def cleanup_resource_summary(self) -> list[dict[str, str]]:
        remaining = []
        for resource in self.manifest["resources"]["containers"]:
            if resource.get("removed"):
                continue
            remaining.append(
                {
                    "id": str(resource.get("id") or "unknown"),
                    "name": str(resource.get("name") or "unknown"),
                    "kind": str(resource.get("kind") or "unknown"),
                }
            )
        if self.network_id:
            remaining.append(
                {"id": self.network_id, "name": NETWORK_NAME, "kind": "network"}
            )
        for network in self.manifest["resources"]["networks"]:
            if not network.get("removed") and network.get("id") != self.network_id:
                remaining.append(
                    {
                        "id": str(network.get("id") or "unknown"),
                        "name": str(network.get("name") or "unknown"),
                        "kind": "network-intent",
                    }
                )
        if self.loop_device:
            remaining.append(
                {"id": self.loop_device, "name": str(self.pool), "kind": "loop-mount"}
            )
        elif self.manifest.get("loop_backing_intent") and not self.manifest.get(
            "pool_unmounted"
        ):
            remaining.append(
                {
                    "id": "unresolved",
                    "name": str(self.manifest["loop_backing_intent"]),
                    "kind": "loop-attachment-intent",
                }
            )
        for image in self.manifest["resources"]["images"]:
            if not image.get("removed"):
                remaining.append(
                    {
                        "id": image.get("id", ""),
                        "name": image.get("ref", ""),
                        "kind": "image-tag",
                    }
                )
        return remaining


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--baseline-revision", default=BASELINE_SHA)
    parser.add_argument("--acknowledge-disposable", action="store_true")
    parser.add_argument("--acknowledge-interruption", action="store_true")
    parser.add_argument("--acknowledge-ingress-fenced", action="store_true")
    parser.add_argument("--acknowledge-updater-paused", action="store_true")
    args = parser.parse_args()
    if not all(
        (
            args.acknowledge_disposable,
            args.acknowledge_interruption,
            args.acknowledge_ingress_fenced,
            args.acknowledge_updater_paused,
        )
    ):
        parser.error(
            "all transient disposable-runner and maintenance acknowledgments are required"
        )
    if args.baseline_revision != BASELINE_SHA:
        parser.error("only the accepted exact pre-PR baseline is supported")
    try:
        return Suite(args.workspace, args.baseline_revision).run()
    except Exception:
        print(
            json.dumps(
                {"runtime": "fail", "failure": "runner-preflight-or-setup-failed"},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
