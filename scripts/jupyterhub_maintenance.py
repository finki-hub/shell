"""Bounded, fail-closed single-host JupyterHub maintenance controller.

This module intentionally uses only the Python standard library and Docker CLI.
It has no user-supplied command hooks. Tests inject a finite command runner and
exercise file/database handling without contacting a daemon.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, BinaryIO

LOCK_DEFAULT = "/run/lock/finki-hub-shell-update.lock"
MARKER_NAME = ".jupyterhub-maintenance.json"
VERSION_LABEL = "org.finki-hub.jupyterhub-version"
MAX_READINESS_BYTES = 4096
COMMAND_TIMEOUT = 30
START_TIMEOUT = 180
STOP_TIMEOUT = 60
SERVICES = ("web", "proxy", "hub")


class MaintenanceError(RuntimeError):
    """Sanitized operational failure; never carries command output or secrets."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        _req: urllib.request.Request,
        _fp: object,
        _code: int,
        _msg: str,
        _headers: object,
        _newurl: str,
    ) -> None:
        return None


def _http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET")  # ruff: ignore[S310] - internal readiness endpoint only
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    with response:
        body = response.read(MAX_READINESS_BYTES + 1)
        if len(body) > MAX_READINESS_BYTES:
            raise MaintenanceError("readiness response exceeds its size limit")
        return response.status, body


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        path.chmod(0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaintenanceError("protected state file is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise MaintenanceError("protected state file has an invalid shape")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


def _reject_symlink_components(path: Path, *, include_leaf: bool = True) -> None:
    absolute = path.absolute()
    components = [absolute.anchor, *absolute.parts[1:]]
    current = Path(absolute.anchor)
    for index, component in enumerate(components[1:], start=1):
        current = current / component
        if not include_leaf and index == len(components) - 1:
            break
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise MaintenanceError(
                    "symlink paths are unsupported for protected rollout state"
                )
        except FileNotFoundError:
            break


def _assert_no_symlinks(root: Path) -> None:
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in [*dirs, *files]:
            if stat.S_ISLNK((base / name).lstat().st_mode):
                raise MaintenanceError("symlinks are unsupported in Hub data snapshots")


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _inventory(root: Path) -> list[dict[str, Any]]:
    """Record copy inventory without printing it (it contains secret hashes)."""
    records: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        base = Path(current)
        for name in sorted([*dirs, *files]):
            path = base / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            record: dict[str, Any] = {
                "path": relative,
                "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid,
                "gid": info.st_gid,
            }
            if stat.S_ISLNK(info.st_mode):
                raise MaintenanceError(
                    "symlinks are unsupported in protected snapshots"
                )
            if stat.S_ISDIR(info.st_mode):
                record["type"] = "directory"
            elif stat.S_ISREG(info.st_mode):
                record.update(type="file", size=info.st_size, sha256=_sha256_file(path))
            else:
                raise MaintenanceError(
                    "Hub data contains an unsupported filesystem object"
                )
            records.append(record)
    return records


def _copy_tree_preserving(source: Path, destination: Path) -> list[dict[str, Any]]:
    if destination.exists():
        raise MaintenanceError("backup or restore destination already exists")
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    for current, dirs, files in os.walk(source, followlinks=False):
        source_dir = Path(current)
        target_dir = destination / source_dir.relative_to(source)
        _copy_owner(source_dir, target_dir)
        for name in [*dirs, *files]:
            src, dst = source_dir / name, target_dir / name
            if not src.is_symlink():
                _copy_owner(src, dst)
    original, copied = _inventory(source), _inventory(destination)
    if original != copied:
        raise MaintenanceError("cold-copy inventory verification failed")
    return copied


def _copy_owner(source: Path, destination: Path) -> None:
    source_info = source.lstat()
    if source.is_symlink():
        return
    if os.name != "nt" and hasattr(os, "chown"):
        try:
            os.chown(destination, source_info.st_uid, source_info.st_gid)
        except OSError as exc:
            raise MaintenanceError(
                "could not preserve Hub data ownership in protected copy"
            ) from exc
    destination.chmod(stat.S_IMODE(source_info.st_mode))


def _sqlite_integrity(directory: Path) -> None:
    db = directory / "jupyterhub.sqlite"
    if not db.is_file():
        raise MaintenanceError("Hub snapshot has no JupyterHub SQLite database")
    verify = directory.parent / f".{directory.name}.verify-{uuid.uuid4().hex}"
    try:
        _copy_tree_preserving(directory, verify)
        uri = verify.joinpath(db.name).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            deadline = time.monotonic() + 120
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() >= deadline else 0, 1_000
            )
            result = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            raise MaintenanceError(
                "SQLite integrity check failed on disposable verification copy"
            ) from exc
        finally:
            connection.close()
        if result != ("ok",):
            raise MaintenanceError(
                "SQLite integrity check failed on disposable verification copy"
            )
    finally:
        if verify.exists():
            shutil.rmtree(verify)


def _version(value: str, *, label: str = "image") -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[.+-][A-Za-z0-9.+-]+)?", value):
        raise MaintenanceError(f"{label} JupyterHub version is unknown or malformed")
    return value


class DeploymentLock:
    """Same advisory lock used by update.sh; held for the entire invocation."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: BinaryIO | None = None

    def __enter__(self) -> DeploymentLock:  # ruff: ignore[PYI034] - preserve Python 3.10 stdlib support
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if sys.platform == "win32":
            import msvcrt  # ruff: ignore[PLC0415] - platform-specific lock backend

            try:
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                self.stream.close()
                raise MaintenanceError("deployment update lock is held") from exc
        else:
            import fcntl  # ruff: ignore[PLC0415] - Linux-only lock backend

            try:
                fcntl.flock(
                    self.stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except OSError as exc:
                self.stream.close()
                raise MaintenanceError("deployment update lock is held") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.stream is None:
            return
        if sys.platform == "win32":
            import msvcrt  # ruff: ignore[PLC0415] - platform-specific lock backend

            self.stream.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # ruff: ignore[PLC0415] - Linux-only lock backend

            fcntl.flock(
                self.stream.fileno(),
                fcntl.LOCK_UN,
            )
        self.stream.close()


class Controller:
    def __init__(
        self,
        *,
        project_directory: Path,
        project_name: str,
        env_file: Path,
        compose_files: Sequence[Path],
        backup_dir: Path,
        command: Callable[[Sequence[str], float, bool], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        http_get: Callable[[str, float], tuple[int, bytes]] | None = None,
    ) -> None:
        if (
            not project_directory.is_absolute()
            or not env_file.is_absolute()
            or not backup_dir.is_absolute()
        ):
            raise MaintenanceError(
                "project, environment, and backup paths must be absolute"
            )
        if any(not path.is_absolute() for path in compose_files):
            raise MaintenanceError("all Compose file paths must be absolute")
        _reject_symlink_components(project_directory)
        _reject_symlink_components(env_file)
        for path in compose_files:
            _reject_symlink_components(path)
        _reject_symlink_components(backup_dir, include_leaf=backup_dir.exists())
        self.project = project_directory.resolve(strict=True)
        if not self.project.is_dir() or not self.project.is_absolute():
            raise MaintenanceError(
                "project directory must be an existing absolute directory"
            )
        self.project_name = project_name
        self.env_file = env_file.resolve(strict=True)
        self.compose_files = tuple(path.resolve(strict=True) for path in compose_files)
        self.backup = (
            backup_dir.resolve(strict=True)
            if backup_dir.exists()
            else backup_dir.parent.resolve(strict=True) / backup_dir.name
        )
        if not self.env_file.is_file() or not self.compose_files:
            raise MaintenanceError(
                "explicit environment and Compose files are required"
            )
        if any(not path.is_file() for path in self.compose_files):
            raise MaintenanceError("all explicit Compose files must be regular files")
        self.command = command or self._system_command
        self.clock = clock
        self.sleeper = time.sleep
        self.http_get = http_get or _http_get
        self.marker = self.project / MARKER_NAME
        self.manifest: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.host_paths: dict[str, Path] = {}
        self.service_containers: dict[str, dict[str, Any]] = {}
        self.owned_labs: list[dict[str, Any]] = []
        self._image_config_cache: dict[str, dict[str, Any]] = {}
        self.daemon_id = ""

    @staticmethod
    def _system_command(args: Sequence[str], timeout: float, check: bool = True) -> str:
        try:
            result = subprocess.run(  # ruff: ignore[S603] - fixed argv, no shell, finite timeout
                list(args),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise MaintenanceError("bounded external command timed out") from exc
        except OSError as exc:
            raise MaintenanceError(
                "required external command could not be executed"
            ) from exc
        if check and result.returncode:
            raise MaintenanceError("bounded external command failed")
        return result.stdout.strip()

    def _docker(self, *args: str, timeout: float = COMMAND_TIMEOUT) -> str:
        return self.command(["docker", *args], timeout, True)

    def _compose_args(self, files: Sequence[Path] | None = None) -> list[str]:
        args = [
            "docker",
            "compose",
            "--project-directory",
            str(self.project),
            "--project-name",
            self.project_name,
            "--env-file",
            str(self.env_file),
        ]
        for path in self.compose_files:
            args.extend(["-f", str(path)])
        for path in files or ():
            args.extend(["-f", str(path)])
        return args

    def _compose(
        self,
        *args: str,
        files: Sequence[Path] | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> str:
        return self.command([*self._compose_args(files), *args], timeout, True)

    def _inspect(self, container_id: str) -> dict[str, Any]:
        try:
            value = json.loads(self._docker("inspect", container_id))
            item = value[0]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise MaintenanceError(
                "Docker returned an invalid container inspection"
            ) from exc
        if not isinstance(item, dict):
            raise MaintenanceError("Docker returned an invalid container inspection")
        return item

    def _image(self, reference: str) -> dict[str, Any]:
        try:
            value = json.loads(self._docker("image", "inspect", reference))
            item = value[0]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise MaintenanceError(
                "required image is not locally available or inspectable"
            ) from exc
        if not isinstance(item, dict):
            raise MaintenanceError("Docker returned an invalid image inspection")
        return item

    @staticmethod
    def _labels(container: dict[str, Any]) -> dict[str, str]:
        return (container.get("Config") or {}).get("Labels") or {}

    @staticmethod
    def _mounts(container: dict[str, Any]) -> list[dict[str, Any]]:
        return container.get("Mounts") or []

    def _config_hashes(self) -> dict[str, str]:
        return {
            str(path): _sha256_file(path)
            for path in (*self.compose_files, self.env_file)
        }

    def _load_config(self) -> None:
        try:
            self.config = json.loads(self._compose("config", "--format", "json"))
        except json.JSONDecodeError as exc:
            raise MaintenanceError(
                "Compose returned invalid effective configuration"
            ) from exc
        if self.config.get("name") != self.project_name:
            raise MaintenanceError(
                "Compose project name differs from the required project name"
            )
        services = self.config.get("services") or {}
        if not all(name in services for name in (*SERVICES, "lab", "hub")):
            raise MaintenanceError(
                "Compose configuration is missing a required service"
            )
        networks = self.config.get("networks") or {}
        users_network = networks.get("users") or {}
        if (
            set(networks) != {"users"}
            or users_network.get("external") is not True
            or users_network.get("name") != "finki-hub-shell-users"
        ):
            raise MaintenanceError("unsupported external Lab network configuration")
        if (
            services["web"].get("network_mode") != "host"
            or services["proxy"].get("network_mode") != "host"
            or services["hub"].get("network_mode") != "host"
            or services["hub"].get("privileged") is not True
        ):
            raise MaintenanceError(
                "unsupported web/proxy/Hub network or privilege topology"
            )
        proxy_command = services["proxy"].get("command") or []

        def binds_loopback(flag: str, port_flag: str, port: str) -> bool:
            try:
                index = proxy_command.index(flag)
                port_index = proxy_command.index(port_flag)
                return (
                    proxy_command[index + 1] == "127.0.0.1"
                    and proxy_command[port_index + 1] == port
                )
            except (ValueError, IndexError):  # fmt: skip
                return False

        if not binds_loopback("--ip", "--port", "8000") or not binds_loopback(
            "--api-ip", "--api-port", "8001"
        ):
            raise MaintenanceError(
                "proxy must bind its public and API listeners to loopback"
            )
        hub_volumes = services["hub"].get("volumes") or []
        targets: set[str] = set()
        for destination in ("/srv/hub", "/srv/pool", "/var/run/docker.sock"):
            matches = [
                volume
                for volume in hub_volumes
                if volume.get("type") == "bind" and volume.get("target") == destination
            ]
            if len(matches) != 1:
                raise MaintenanceError(
                    "unsupported Hub data, pool, or Docker-socket mounts"
                )
            source = Path(matches[0]["source"]).resolve()
            self.host_paths[destination] = source
            targets.add(destination)
        if len(hub_volumes) != 3 or targets != {
            "/srv/hub",
            "/srv/pool",
            "/var/run/docker.sock",
        }:
            raise MaintenanceError(
                "unsupported additional Hub volume or persistence mount"
            )
        if not self.host_paths["/srv/hub"].is_dir():
            raise MaintenanceError("configured Hub data directory is unavailable")
        if not self.host_paths["/srv/pool"].is_dir():
            raise MaintenanceError("configured pool bind source is unavailable")
        if _paths_overlap(self.host_paths["/srv/hub"], self.host_paths["/srv/pool"]):
            raise MaintenanceError("Hub-data and pool bind paths must not overlap")
        _assert_no_symlinks(self.host_paths["/srv/hub"])
        for path in (*self.compose_files, self.env_file):
            _assert_no_symlinks(path)
        _reject_symlink_components(self.backup, include_leaf=self.backup.exists())

    def _required_acknowledgments(self, args: argparse.Namespace) -> None:
        missing = [
            flag
            for attr, flag in (
                ("acknowledge_interruption", "--acknowledge-interruption"),
                ("acknowledge_ingress_fenced", "--acknowledge-ingress-fenced"),
                ("acknowledge_updater_paused", "--acknowledge-updater-paused"),
            )
            if not getattr(args, attr, False)
        ]
        if missing:
            raise MaintenanceError(
                "required transient acknowledgments are missing: " + ", ".join(missing)
            )

    def _daemon(self) -> None:
        self.daemon_id = self._docker("info", "--format", "{{.ID}}")
        if not self.daemon_id or "\n" in self.daemon_id:
            raise MaintenanceError("Docker daemon identity is unavailable")

    def _service_container(
        self, service: str, *, required_running: bool = True
    ) -> dict[str, Any]:
        item = self._find_service_container(service)
        if item is None:
            raise MaintenanceError(f"expected exactly one Compose {service} container")
        running = bool((item.get("State") or {}).get("Running"))
        if required_running and not running:
            raise MaintenanceError(f"Compose {service} is not running")
        return item

    def _find_service_container(self, service: str) -> dict[str, Any] | None:
        output = self._compose("ps", "-aq", service)
        ids = [line.strip() for line in output.splitlines() if line.strip()]
        if not ids:
            return None
        if len(ids) != 1:
            raise MaintenanceError(f"expected exactly one Compose {service} container")
        item = self._inspect(ids[0])
        labels = self._labels(item)
        if (
            labels.get("com.docker.compose.project") != self.project_name
            or labels.get("com.docker.compose.service") != service
        ):
            raise MaintenanceError(
                f"Compose {service} container ownership is ambiguous"
            )
        self._verify_service_shape(item, service)
        return item

    def _verify_service_shape(self, item: dict[str, Any], service: str) -> None:
        image_id = item.get("Image")
        if not image_id:
            raise MaintenanceError(f"Compose {service} image identity is unavailable")
        image_config = self._image_config_cache.get(image_id)
        if image_config is None:
            image = self._image(image_id)
            image_config = image.get("Config") or {}
            self._image_config_cache[image_id] = image_config
        container_config = item.get("Config") or {}
        compose_service = (self.config.get("services") or {}).get(service) or {}
        expected_entrypoint = compose_service.get("entrypoint")
        if expected_entrypoint is None:
            expected_entrypoint = image_config.get("Entrypoint")
        expected_command = compose_service.get("command")
        if expected_command is None:
            expected_command = image_config.get("Cmd")
        if (
            expected_entrypoint is not None
            and container_config.get("Entrypoint") != expected_entrypoint
        ):
            raise MaintenanceError(
                f"Compose {service} entrypoint differs from protected configuration"
            )
        actual_command = container_config.get("Cmd")
        helper_command = [
            "jupyterhub",
            "-f",
            "/srv/maintenance/jupyterhub-maintenance-config.py",
        ]
        if service == "hub" and actual_command == helper_command:
            pass
        elif expected_command is not None and actual_command != expected_command:
            raise MaintenanceError(
                f"Compose {service} command differs from protected configuration"
            )
        if service in {"web", "proxy"}:
            if (item.get("HostConfig") or {}).get("NetworkMode") != "host":
                raise MaintenanceError(
                    f"Compose {service} network differs from the supported topology"
                )
            return
        expected = {
            (str(self.host_paths.get(target, Path("/missing"))), target)
            for target in ("/srv/hub", "/srv/pool", "/var/run/docker.sock")
        }
        expected_modes = {
            volume.get("target"): not volume.get("read_only", False)
            for volume in compose_service.get("volumes", [])
        }
        helper_mount_target = "/srv/maintenance/jupyterhub-maintenance-config.py"
        helper_mounts = [
            mount
            for mount in self._mounts(item)
            if mount.get("Destination") == helper_mount_target
        ]
        helper_command = [
            "jupyterhub",
            "-f",
            helper_mount_target,
        ]
        uses_helper = service == "hub" and container_config.get("Cmd") == helper_command
        if uses_helper:
            wrapper = self.backup / "configuration" / "jupyterhub_maintenance_config.py"
            if (
                len(helper_mounts) != 1
                or helper_mounts[0].get("Type") != "bind"
                or Path(helper_mounts[0].get("Source", "")).resolve()
                != wrapper.resolve()
                or helper_mounts[0].get("RW") is not False
            ):
                raise MaintenanceError(
                    "private Hub wrapper bind must match the protected read-only file"
                )
            expected_modes[helper_mount_target] = False
            expected.add((str(wrapper.resolve()), helper_mount_target))
        elif helper_mounts:
            raise MaintenanceError(
                "unexpected maintenance-wrapper bind on a normal Hub"
            )
        actual_mounts = {
            mount.get("Destination"): (
                str(Path(mount.get("Source", "")).resolve()),
                mount.get("RW") is True,
            )
            for mount in self._mounts(item)
            if mount.get("Type") == "bind"
        }
        actual = {
            (source, target) for target, (source, _read_write) in actual_mounts.items()
        }
        host_config = item.get("HostConfig") or {}
        if (
            actual != expected
            or {
                target: read_write
                for target, (_source, read_write) in actual_mounts.items()
            }
            != expected_modes
            or host_config.get("NetworkMode") != "host"
            or host_config.get("Privileged") is not True
            or host_config.get("CapAdd")
            or host_config.get("CapDrop")
            or host_config.get("SecurityOpt")
            or host_config.get("Devices")
            or host_config.get("PidMode")
            or host_config.get("IpcMode") not in (None, "", "private")
        ):
            raise MaintenanceError(
                "Compose Hub binds or networking differ from the protected deployment"
            )

    def _all_containers(self) -> list[dict[str, Any]]:
        output = self._docker("ps", "-aq")
        ids = [line.strip() for line in output.splitlines() if line.strip()]
        return [self._inspect(identifier) for identifier in ids]

    def _hub_environment(self, hub: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in (hub.get("Config") or {}).get("Env") or []:
            if "=" in item:
                key, value = item.split("=", 1)
                result[key] = value
        return result

    def _verify_runtime_environment(
        self, service: str, container: dict[str, Any], *, hub_version: str
    ) -> None:
        expected = self.config["services"][service].get("environment") or {}
        actual = self._hub_environment(container)
        for key, value in expected.items():
            if value is None or key == "LAB_IMAGE":
                continue
            if (
                service == "hub"
                and key == "JUPYTERHUB_ALLOW_DB_UPGRADE"
                and key not in actual
                and hub_version != "6.0.1"
            ):
                continue
            if actual.get(key) != str(value):
                raise MaintenanceError(
                    f"running {service} environment differs from protected Compose input"
                )
        desired_restart = self.config["services"][service].get("restart", "no")
        running_restart = (
            (container.get("HostConfig") or {}).get("RestartPolicy") or {}
        ).get("Name", "no")
        if desired_restart != running_restart:
            raise MaintenanceError(
                f"running {service} restart policy differs from Compose input"
            )

    def _metadata_from_running_hub(self, hub: dict[str, Any]) -> str:
        output = self._docker(
            "exec",
            hub["Id"],
            "python",
            "-c",
            "import importlib.metadata as m; print(m.version('jupyterhub'))",
            timeout=15,
        )
        return _version(output, label="running Hub")

    def _probe_lab_image(self, image_id: str) -> str:
        probe_id = uuid.uuid4().hex
        name = f"finki-hub-version-probe-{probe_id}"
        label_key = "org.finki-hub.maintenance-probe"
        label_value = probe_id
        container_id: str | None = None
        failure: Exception | None = None
        try:
            container_id = self._docker(
                "create",
                "--name",
                name,
                "--label",
                f"{label_key}={label_value}",
                "--network",
                "none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--memory=64m",
                "--cpus=0.25",
                "--pids-limit=16",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=16m",  # ruff: ignore[S108] - private probe tmpfs only
                "--entrypoint",
                "/opt/jupyter/bin/python",
                image_id,
                "-c",
                "import importlib.metadata as m; print(m.version('jupyterhub'))",
                timeout=COMMAND_TIMEOUT,
            )
            output = (
                self._docker("start", "-a", container_id, timeout=COMMAND_TIMEOUT)
                if container_id
                else ""
            )
        except Exception as exc:
            failure = exc
            raise
        finally:
            try:
                self._cleanup_probe_container(
                    name, label_key, label_value, container_id
                )
            except MaintenanceError as cleanup_error:
                if failure is not None:
                    raise MaintenanceError(
                        "Lab metadata probe failed and owned probe cleanup could not be confirmed"
                    ) from cleanup_error
                raise
        if not container_id:
            raise MaintenanceError(
                "isolated Lab metadata probe returned no container ID"
            )
        return _version(output, label="selected Lab image")

    def _cleanup_probe_container(
        self,
        name: str,
        label_key: str,
        label_value: str,
        expected_id: str | None,
    ) -> None:
        output = self._docker(
            "ps", "-aq", "--filter", f"label={label_key}={label_value}"
        )
        ids = [line.strip() for line in output.splitlines() if line.strip()]
        if not ids:
            return
        if len(ids) != 1 or (expected_id is not None and ids[0] != expected_id):
            raise MaintenanceError("owned Lab probe container identity is ambiguous")
        container = self._inspect(ids[0])
        labels = self._labels(container)
        if (
            labels.get(label_key) != label_value
            or str(container.get("Name", "")).removeprefix("/") != name
            or (container.get("HostConfig") or {}).get("NetworkMode") != "none"
            or any(
                mount.get("Type") != "tmpfs" or mount.get("Destination") != "/tmp"  # ruff: ignore[S108] - fixed private probe tmpfs
                for mount in self._mounts(container)
            )
        ):
            raise MaintenanceError("refusing to clean an unowned Lab probe container")
        if bool((container.get("State") or {}).get("Running")):
            self._docker("update", "--restart=no", ids[0], timeout=COMMAND_TIMEOUT)
            self._docker("kill", "--signal=TERM", ids[0], timeout=COMMAND_TIMEOUT)
            deadline = self.clock() + STOP_TIMEOUT
            while self.clock() < deadline:
                container = self._inspect(ids[0])
                if not bool((container.get("State") or {}).get("Running")):
                    break
                self.sleeper(1)
            else:
                raise MaintenanceError(
                    "owned Lab probe container did not stop after TERM"
                )
        self._docker("rm", ids[0], timeout=COMMAND_TIMEOUT)

    def _candidate_image(self, service: str) -> tuple[str, str, str]:
        image_ref = (self.config["services"][service] or {}).get("image")
        if not image_ref:
            raise MaintenanceError(f"candidate {service} image is not configured")
        image = self._image(image_ref)
        labels = (image.get("Config") or {}).get("Labels") or {}
        version = labels.get(VERSION_LABEL)
        if not version:
            raise MaintenanceError(
                f"candidate {service} image has no build-verified version metadata"
            )
        return (
            image_ref,
            image.get("Id", ""),
            _version(version, label=f"candidate {service}"),
        )

    def _validate_labs(
        self,
        containers: Sequence[dict[str, Any]],
        *,
        pool: Path,
        lab_user: str,
        lab_home_target: str | None = None,
        expected_version: str | None = None,
    ) -> list[dict[str, Any]]:
        labs: list[dict[str, Any]] = []
        users_root = (pool / "users").resolve()
        for item in containers:
            name = str(item.get("Name", "")).removeprefix("/")
            labels = self._labels(item)
            role, username = labels.get("finki.role"), labels.get("finki.user")
            if not name.startswith("lab-") and username is None:
                continue
            if role != "lab" or not username or name != f"lab-{username}":
                raise MaintenanceError(
                    "ambiguous possible Lab ownership; no container was adopted"
                )
            expected_home = (users_root / username).resolve()
            if expected_home.parent != users_root:
                raise MaintenanceError(
                    "Lab username escapes the configured pool users directory"
                )
            mounts = [
                mount
                for mount in self._mounts(item)
                if mount.get("Type") == "bind"
                and mount.get("Destination") == (lab_home_target or f"/home/{lab_user}")
            ]
            if (
                len(mounts) != 1
                or Path(mounts[0].get("Source", "")).resolve() != expected_home
                or mounts[0].get("RW") is not True
            ):
                raise MaintenanceError(
                    "possible Lab has an unexpected or ambiguous home bind"
                )
            image_id = item.get("Image")
            if not image_id:
                raise MaintenanceError("owned Lab image identity is unavailable")
            lab_version = self._probe_lab_image(image_id)
            if expected_version is not None and lab_version != expected_version:
                raise MaintenanceError(
                    "owned Lab is not compatible with its current Hub"
                )
            labs.append(
                {
                    "id": item.get("Id"),
                    "name": name,
                    "username": username,
                    "image_id": image_id,
                    "version": lab_version,
                    "home": str(expected_home),
                    "running": bool((item.get("State") or {}).get("Running")),
                    "restart_policy": (
                        (item.get("HostConfig") or {}).get("RestartPolicy") or {}
                    ).get("Name", "no"),
                }
            )
        return labs

    def _check_foreign_hub_mounts(
        self, containers: Sequence[dict[str, Any]], hub_ids: set[str]
    ) -> None:
        hub_data = self.host_paths["/srv/hub"]
        for item in containers:
            if item.get("Id") in hub_ids:
                continue
            for mount in self._mounts(item):
                source = mount.get("Source")
                source_path = Path(source).resolve() if source else None
                if source_path and (
                    source_path == hub_data
                    or source_path in hub_data.parents
                    or hub_data in source_path.parents
                ):
                    raise MaintenanceError(
                        "another container mounts Hub data; external writers are not safe"
                    )

    def _record_configuration(self) -> dict[str, Any]:
        self._load_config()
        self._daemon()
        containers = self._all_containers()
        services = {name: self._service_container(name) for name in SERVICES}
        self.service_containers = services
        hub = services["hub"]
        self._check_foreign_hub_mounts(containers, {hub.get("Id", "")})
        for service in SERVICES:
            actual_image_id = services[service].get("Image")
            if (
                not actual_image_id
                or self._image(actual_image_id).get("Id") != actual_image_id
            ):
                raise MaintenanceError(
                    f"running {service} image cannot be recorded immutably"
                )
        hub_version = self._metadata_from_running_hub(hub)
        for service, item in services.items():
            self._verify_runtime_environment(service, item, hub_version=hub_version)
        environment = self._hub_environment(hub)
        old_lab_ref = environment.get("LAB_IMAGE")
        lab_user = environment.get("LAB_USER", "ubuntu")
        if not old_lab_ref:
            raise MaintenanceError(
                "running Hub does not expose its selected Lab reference"
            )
        old_lab = self._image(old_lab_ref)
        old_lab_id = old_lab.get("Id", "")
        old_lab_version = self._probe_lab_image(old_lab_id)
        if old_lab_version != hub_version:
            raise MaintenanceError(
                "running Hub and its selected Lab image are not version-matched"
            )
        candidate_hub_ref, candidate_hub_id, candidate_hub_version = (
            self._candidate_image("hub")
        )
        candidate_lab_ref, candidate_lab_id, candidate_lab_version = (
            self._candidate_image("lab")
        )
        if candidate_hub_version != candidate_lab_version:
            raise MaintenanceError("candidate Hub and Lab image versions do not match")
        # The configured Hub pool mount, not the candidate .env value, is authoritative.
        configured_pool = self.host_paths["/srv/pool"]
        configured_pool_value = environment.get("LAB_POOL_DIR")
        if (
            not configured_pool_value
            or Path(configured_pool_value).resolve() != configured_pool
        ):
            raise MaintenanceError(
                "Hub LAB_POOL_DIR does not match the configured pool bind"
            )
        self.owned_labs = self._validate_labs(
            containers,
            pool=configured_pool,
            lab_user=lab_user,
            expected_version=hub_version,
        )
        compose_paths = [str(path) for path in (*self.compose_files, self.env_file)]
        return {
            "daemon_id": self.daemon_id,
            "project_name": self.project_name,
            "project_directory": str(self.project),
            "compose_paths": compose_paths,
            "config_hashes": self._config_hashes(),
            "effective_config_sha256": _sha256_json(self.config),
            "hub_data": str(self.host_paths["/srv/hub"]),
            "pool": str(configured_pool),
            "services": {
                name: {
                    "container_id": item["Id"],
                    "image_id": item["Image"],
                    "restart_policy": (
                        (item.get("HostConfig") or {}).get("RestartPolicy") or {}
                    ).get("Name", "no"),
                }
                for name, item in services.items()
            },
            "old_hub_version": hub_version,
            "old_hub_image_id": services["hub"]["Image"],
            "old_lab_image_id": old_lab_id,
            "old_lab_version": old_lab_version,
            "old_lab_user": lab_user,
            "candidate_hub_ref": candidate_hub_ref,
            "candidate_hub_image_id": candidate_hub_id,
            "candidate_hub_version": candidate_hub_version,
            "candidate_lab_ref": candidate_lab_ref,
            "candidate_lab_image_id": candidate_lab_id,
            "candidate_lab_version": candidate_lab_version,
            "labs": self.owned_labs,
        }

    def _validate_backup_location(self, *, must_be_new: bool) -> None:
        if not self.backup.is_absolute():
            raise MaintenanceError("backup directory must be an absolute path")
        data, pool = self.host_paths["/srv/hub"], self.host_paths["/srv/pool"]
        for protected in (data, pool):
            if (
                self.backup == protected
                or protected in self.backup.parents
                or self.backup in protected.parents
            ):
                raise MaintenanceError(
                    "backup directory must be separate from Hub data and pool"
                )
        if (
            self.backup == self.project
            or self.project in self.backup.parents
            or self.backup in self.project.parents
        ):
            raise MaintenanceError(
                "backup directory must be separate from the deployment checkout"
            )
        if must_be_new and self.backup.exists():
            raise MaintenanceError(
                "backup destination already exists; retries never overwrite it"
            )
        parent = self.backup.parent
        if not parent.is_dir():
            raise MaintenanceError("backup parent directory must already exist")
        if not os.access(parent, os.W_OK | os.X_OK):
            raise MaintenanceError("backup parent is not writable")
        usage = shutil.disk_usage(parent)
        required = _tree_size(data) * 2 + 16 * 1024 * 1024
        if usage.free < required:
            raise MaintenanceError(
                "insufficient free space for cold backup and verification copy"
            )

    def _validate_input_binding(self, marker: dict[str, Any]) -> None:
        self._load_config()
        self._daemon()
        expected = marker.get("manifest", {})
        requested_paths = [str(path) for path in (*self.compose_files, self.env_file)]
        if (
            expected.get("daemon_id") != self.daemon_id
            or expected.get("project_name") != self.project_name
            or expected.get("project_directory") != str(self.project)
            or expected.get("compose_paths") != requested_paths
            or expected.get("config_hashes") != self._config_hashes()
            or expected.get("effective_config_sha256") != _sha256_json(self.config)
        ):
            raise MaintenanceError(
                "deployment daemon, project, or protected config has drifted"
            )
        if str(self.host_paths["/srv/hub"].resolve()) != str(
            Path(expected["hub_data"]).resolve()
        ) or str(self.host_paths["/srv/pool"].resolve()) != str(
            Path(expected["pool"]).resolve()
        ):
            raise MaintenanceError(
                "Hub data or pool bind path differs from the protected interlock"
            )

    def _marker_state(self) -> dict[str, Any] | None:
        if not self.marker.exists():
            return None
        marker = _read_json(self.marker)
        self._validate_input_binding(marker)
        return marker

    def _write_stage(
        self, backup: Path, stage: str, facts: Mapping[str, object] | None = None
    ) -> None:
        status_path = backup / "state.json"
        state = _read_json(status_path) if status_path.exists() else {"stages": []}
        state["stages"].append(
            {"stage": stage, "at": int(time.time()), **(facts or {})}
        )
        _atomic_json(status_path, state)

    def _copy_operator_configuration(self, backup: Path) -> None:
        config_dir = backup / "configuration"
        config_dir.mkdir(mode=0o700)
        records = []
        for index, source in enumerate(self.compose_files):
            target = config_dir / f"compose-{index:02d}{source.suffix or '.yaml'}"
            shutil.copy2(source, target)
            target.chmod(0o600)
            records.append(
                {
                    "source": str(source),
                    "backup": str(target),
                    "sha256": _sha256_file(source),
                }
            )
        env_target = config_dir / "environment.env"
        shutil.copy2(self.env_file, env_target)
        env_target.chmod(0o600)
        records.append(
            {
                "source": str(self.env_file),
                "backup": str(env_target),
                "sha256": _sha256_file(self.env_file),
            }
        )
        self.manifest["configuration_snapshots"] = records

    def _preflight(self, *, new_backup: bool) -> dict[str, Any]:
        self._load_config()
        manifest = self._record_configuration()
        self.manifest = manifest
        self._validate_backup_location(must_be_new=new_backup)
        # Reject unexplained bind/network/service settings instead of guessing.
        services = self.config["services"]
        if set(services) != {"web", "proxy", "hub", "lab"}:
            raise MaintenanceError(
                "unsupported Compose service set; maintenance requires the known topology"
            )
        hub_data_mount = [
            item
            for item in services["hub"].get("volumes", [])
            if item.get("target") == "/srv/hub"
        ]
        pool_mount = [
            item
            for item in services["hub"].get("volumes", [])
            if item.get("target") == "/srv/pool"
        ]
        if len(hub_data_mount) != 1 or len(pool_mount) != 1:
            raise MaintenanceError("unsupported Hub persistence configuration")
        if services["hub"].get("network_mode") != "host":
            raise MaintenanceError("unsupported Hub networking topology")
        web_id = manifest["services"]["web"]["container_id"]
        proxy_id = manifest["services"]["proxy"]["container_id"]
        if not web_id or not proxy_id:
            raise MaintenanceError("cannot establish public ingress service ownership")
        return {
            "project": self.project_name,
            "daemon": self.daemon_id[:12],
            "old_hub_version": manifest["old_hub_version"],
            "candidate_hub_version": manifest["candidate_hub_version"],
            "owned_lab_count": len(self.owned_labs),
            "backup_directory": str(self.backup),
            "ingress_acknowledgment_required": True,
        }

    def preflight(self) -> dict[str, Any]:
        marker = self._marker_state()
        if marker:
            raise MaintenanceError(
                "maintenance interlock already exists; use accept or restore"
            )
        return self._preflight(new_backup=False)

    def _ensure_no_marker(self) -> None:
        if self.marker.exists():
            raise MaintenanceError(
                "maintenance interlock already exists; use accept or restore"
            )

    def _write_private_override(
        self,
        backup: Path,
        *,
        hub_image: str,
        lab_image: str,
        upgrade_db: bool,
        suppress_cullers: bool,
        wrapper: Path,
        proxy_image: str,
        web_image: str,
    ) -> Path:
        override = backup / f"private-{uuid.uuid4().hex}.json"
        services: dict[str, Any] = {
            "web": {"image": web_image, "restart": "no"},
            "proxy": {"image": proxy_image, "restart": "no"},
            "hub": {
                "image": hub_image,
                "restart": "no",
                "command": [
                    "jupyterhub",
                    "-f",
                    "/srv/maintenance/jupyterhub-maintenance-config.py",
                ],
                "environment": {
                    "JUPYTERHUB_ALLOW_DB_UPGRADE": "false",
                    "JUPYTERHUB_MAINTENANCE_UPGRADE_DB": "true"
                    if upgrade_db
                    else "false",
                    "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS": "true"
                    if suppress_cullers
                    else "false",
                    "LAB_IMAGE": lab_image,
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(wrapper.resolve()),
                        "target": "/srv/maintenance/jupyterhub-maintenance-config.py",
                        "read_only": True,
                    }
                ],
            },
        }
        _atomic_json(override, {"services": services})
        return override

    def _write_runtime_override(self, backup: Path, *, restored: bool) -> Path:
        manifest = _read_json(backup / "manifest.json")
        services: dict[str, Any] = {
            "web": {
                "image": manifest["services"]["web"]["image_id"],
                "restart": manifest["services"]["web"]["restart_policy"],
            },
            "proxy": {
                "image": manifest["services"]["proxy"]["image_id"],
                "restart": manifest["services"]["proxy"]["restart_policy"],
            },
            "lab": {
                "image": manifest["candidate_lab_image_id"]
                if not restored
                else manifest["old_lab_image_id"]
            },
            "hub": {
                "image": manifest["candidate_hub_image_id"]
                if not restored
                else manifest["old_hub_image_id"],
                "restart": manifest["services"]["hub"]["restart_policy"],
                "environment": {
                    "JUPYTERHUB_ALLOW_DB_UPGRADE": "false",
                    "LAB_IMAGE": manifest["candidate_lab_image_id"]
                    if not restored
                    else manifest["old_lab_image_id"],
                },
            },
        }
        if restored:
            wrapper = backup / "configuration" / "jupyterhub_maintenance_config.py"
            services["hub"].update(
                command=[
                    "jupyterhub",
                    "-f",
                    "/srv/maintenance/jupyterhub-maintenance-config.py",
                ],
                environment={
                    "JUPYTERHUB_ALLOW_DB_UPGRADE": "false",
                    "JUPYTERHUB_MAINTENANCE_UPGRADE_DB": "false",
                    "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS": "false",
                    "LAB_IMAGE": manifest["old_lab_image_id"],
                },
                volumes=[
                    {
                        "type": "bind",
                        "source": str(wrapper),
                        "target": "/srv/maintenance/jupyterhub-maintenance-config.py",
                        "read_only": True,
                    }
                ],
            )
        variant = "restored" if restored else "candidate"
        path = backup / f"activation-{variant}-v1.override.json"
        document = {"services": services}
        if path.exists():
            if _read_json(path) != document:
                raise MaintenanceError(
                    "versioned activation override exists with different protected content"
                )
            return path
        _atomic_json(path, document)
        return path

    def _graceful_exit(
        self, container: dict[str, Any], timeout: float = STOP_TIMEOUT
    ) -> None:
        identifier = container["Id"]
        self._docker("update", "--restart=no", identifier, timeout=COMMAND_TIMEOUT)
        if not bool((container.get("State") or {}).get("Running")):
            return
        self._docker("kill", "--signal=TERM", identifier, timeout=COMMAND_TIMEOUT)
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            inspected = self._inspect(identifier)
            if not bool((inspected.get("State") or {}).get("Running")):
                return
            self.sleeper(1)
        raise MaintenanceError(
            "graceful container shutdown timed out; no forced kill was attempted"
        )

    def _start_service(
        self, service: str, *, override: Path, files: Sequence[Path] | None = None
    ) -> None:
        self._compose(
            "up",
            "-d",
            "--no-deps",
            service,
            files=(*((override,) if files is None else files),),
            timeout=START_TIMEOUT,
        )
        deadline = self.clock() + START_TIMEOUT
        while self.clock() < deadline:
            item = self._find_service_container(service)
            if item is not None:
                state = item.get("State") or {}
                if state.get("Running"):
                    health = (state.get("Health") or {}).get("Status")
                    if health in (None, "healthy"):
                        return
            self.sleeper(1)
        raise MaintenanceError(f"bounded {service} startup/readiness failed")

    def _refence_web(self) -> bool:
        web = self._find_service_container("web")
        if web is None:
            return False
        if bool((web.get("State") or {}).get("Running")):
            self._graceful_exit(web)
        final = self._inspect(web["Id"])
        return not bool((final.get("State") or {}).get("Running"))

    def _return_to_private_acceptance_state(
        self,
        backup: Path,
        manifest: dict[str, Any],
        *,
        restored: bool,
        failure: Exception,
    ) -> None:
        hub = self._service_container("hub")
        proxy = self._service_container("proxy")
        self._docker("update", "--restart=no", proxy["Id"], timeout=COMMAND_TIMEOUT)
        self._graceful_exit(hub)
        self._docker("rm", hub["Id"])
        wrapper = backup / "configuration" / "jupyterhub_maintenance_config.py"
        private = self._write_private_override(
            backup,
            hub_image=manifest["old_hub_image_id"]
            if restored
            else manifest["candidate_hub_image_id"],
            lab_image=manifest["old_lab_image_id"]
            if restored
            else manifest["candidate_lab_image_id"],
            upgrade_db=False,
            suppress_cullers=True,
            wrapper=wrapper,
            proxy_image=manifest["services"]["proxy"]["image_id"],
            web_image=manifest["services"]["web"]["image_id"],
        )
        self._start_service("hub", override=private)
        private_hub = self._service_container("hub")
        self._verify_hub_wrapper_mode(
            private_hub, upgrade_db=False, suppress_cullers=True
        )
        self._wait_private_ready()
        self._write_stage(
            backup,
            "activation-failed-private",
            {"cullers_suppressed": True, "failure": _safe_failure(failure)},
        )

    def _wait_private_ready(self, timeout: float = START_TIMEOUT) -> None:
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            try:
                status, body = self.http_get(
                    "http://127.0.0.1:8000/hub/lab/ready",
                    min(5, max(0.1, deadline - self.clock())),
                )
                if status == 200:
                    report = json.loads(body)
                    if report == {"ready": True}:
                        return
            except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError):  # fmt: skip
                pass
            self.sleeper(1)
        raise MaintenanceError("private Hub readiness probe failed before deadline")

    def _stop_owned_labs(self, labs: Sequence[dict[str, Any]]) -> None:
        for lab in labs:
            item = self._inspect(lab["id"])
            labels = self._labels(item)
            if (
                labels.get("finki.role") != "lab"
                or labels.get("finki.user") != lab["username"]
            ):
                raise MaintenanceError("Lab ownership changed before graceful shutdown")
            checked = self._validate_labs(
                [item],
                pool=Path(self.manifest["pool"]),
                lab_user=self.manifest["old_lab_user"],
            )
            if len(checked) != 1 or checked[0]["home"] != lab["home"]:
                raise MaintenanceError(
                    "Lab home ownership changed before graceful shutdown"
                )
            self._graceful_exit(item)

    def _remove_owned_labs(self, labs: Sequence[dict[str, Any]]) -> None:
        for lab in labs:
            item = self._inspect(lab["id"])
            if bool((item.get("State") or {}).get("Running")):
                raise MaintenanceError("refusing to remove a running Lab")
            if (
                self._labels(item).get("finki.role") != "lab"
                or self._labels(item).get("finki.user") != lab["username"]
                or str(item.get("Name", "")).removeprefix("/") != lab["name"]
            ):
                raise MaintenanceError("Lab ownership changed before removal")
            checked = self._validate_labs(
                [item],
                pool=Path(self.manifest["pool"]),
                lab_user=self.manifest["old_lab_user"],
            )
            if len(checked) != 1 or checked[0]["home"] != lab["home"]:
                raise MaintenanceError("Lab home ownership changed before removal")
            # Docker rm without --force or -v: homes and volumes are not removed.
            self._docker("rm", lab["id"])

    def _write_marker(self, manifest: dict[str, Any], backup: Path) -> dict[str, Any]:
        marker = {
            "version": 1,
            "backup_directory": str(backup),
            "manifest_sha256": _sha256_file(backup / "manifest.json"),
            "manifest": {
                key: manifest[key]
                for key in (
                    "daemon_id",
                    "project_name",
                    "project_directory",
                    "compose_paths",
                    "config_hashes",
                    "effective_config_sha256",
                    "hub_data",
                    "pool",
                )
            },
        }
        _atomic_json(self.marker, marker)
        return marker

    def _copy_and_verify_cold_backup(self, backup: Path) -> None:
        data = self.host_paths["/srv/hub"]
        snapshot = backup / "snapshot"
        inventory = _copy_tree_preserving(data, snapshot)
        _sqlite_integrity(snapshot)
        _atomic_json(backup / "snapshot-inventory.json", {"entries": inventory})
        self._write_stage(backup, "backup-complete", {"entry_count": len(inventory)})

    def migrate(self, args: argparse.Namespace) -> dict[str, Any]:
        self._required_acknowledgments(args)
        self._ensure_no_marker()
        self._preflight(new_backup=True)
        self.backup.mkdir(mode=0o700)
        self.backup.chmod(0o700)
        try:
            self._copy_operator_configuration(self.backup)
            source_wrapper = Path(__file__).with_name(
                "jupyterhub_maintenance_config.py"
            )
            wrapper_copy = self.backup / "configuration" / source_wrapper.name
            shutil.copy2(source_wrapper, wrapper_copy)
            wrapper_copy.chmod(0o600)
            self.manifest["config_wrapper_sha256"] = _sha256_file(wrapper_copy)
            _atomic_json(self.backup / "manifest.json", self.manifest)
            self._write_marker(self.manifest, self.backup)
            self._write_stage(self.backup, "interlock-written")
            # External routing and every updater are fenced by explicit operator acknowledgments.
            for service in ("web", "proxy", "hub"):
                live = self._inspect(self.manifest["services"][service]["container_id"])
                self._graceful_exit(live)
                self._write_stage(self.backup, f"{service}-stopped")
            # No Hub remains to create Labs; enumerate and verify a second time before stopping.
            labs = self._validate_labs(
                self._all_containers(),
                pool=Path(self.manifest["pool"]),
                lab_user=self.manifest["old_lab_user"],
                expected_version=self.manifest["old_hub_version"],
            )
            self._check_foreign_hub_mounts(
                self._all_containers(),
                {self.manifest["services"]["hub"]["container_id"]},
            )
            self._stop_owned_labs(labs)
            self._write_stage(
                self.backup, "labs-gracefully-stopped", {"count": len(labs)}
            )
            self._copy_and_verify_cold_backup(self.backup)
            # Revalidate before removal; the verified backup is already complete.
            latest = self._validate_labs(
                self._all_containers(),
                pool=Path(self.manifest["pool"]),
                lab_user=self.manifest["old_lab_user"],
                expected_version=self.manifest["old_hub_version"],
            )
            if {lab["id"] for lab in latest} != {lab["id"] for lab in labs}:
                raise MaintenanceError("owned Lab inventory changed after cold backup")  # ruff: ignore[TRY301]
            self._remove_owned_labs(latest)
            self._write_stage(self.backup, "owned-labs-removed", {"count": len(latest)})
            wrapper = self.backup / "configuration" / source_wrapper.name
            private = self._write_private_override(
                self.backup,
                hub_image=self.manifest["candidate_hub_image_id"],
                lab_image=self.manifest["candidate_lab_image_id"],
                upgrade_db=True,
                suppress_cullers=True,
                wrapper=wrapper,
                proxy_image=self.manifest["services"]["proxy"]["image_id"],
                web_image=self.manifest["services"]["web"]["image_id"],
            )
            self._start_service("proxy", override=private)
            self._write_stage(self.backup, "proxy-private")
            self._start_service("hub", override=private)
            migration_hub = self._service_container("hub")
            self._verify_hub_wrapper_mode(
                migration_hub, upgrade_db=True, suppress_cullers=True
            )
            self._wait_private_ready()
            self._write_stage(self.backup, "migration-start-ready")
            # Never let Compose implicitly stop/escalate the migration process.
            self._graceful_exit(migration_hub)
            self._docker("rm", migration_hub["Id"])
            private_normal = self._write_private_override(
                self.backup,
                hub_image=self.manifest["candidate_hub_image_id"],
                lab_image=self.manifest["candidate_lab_image_id"],
                upgrade_db=False,
                suppress_cullers=True,
                wrapper=wrapper,
                proxy_image=self.manifest["services"]["proxy"]["image_id"],
                web_image=self.manifest["services"]["web"]["image_id"],
            )
            self._start_service("hub", override=private_normal)
            candidate_hub = self._service_container("hub")
            self._verify_hub_wrapper_mode(
                candidate_hub, upgrade_db=False, suppress_cullers=True
            )
            self._wait_private_ready()
            self._write_stage(
                self.backup, "candidate-private", {"cullers_suppressed": True}
            )
            self._write_marker(self.manifest, self.backup)
            return {
                "stage": "candidate-private",
                "web": "stopped",
                "backup": str(self.backup),
            }
        except Exception as exc:
            if self.marker.exists():
                with contextlib.suppress(Exception):
                    self._write_stage(
                        self.backup, "failed", {"failure": _safe_failure(exc)}
                    )
            if isinstance(exc, MaintenanceError):
                raise
            raise MaintenanceError(
                "maintenance failed after interlock; ingress remains fenced"
            ) from exc

    def _load_active(self) -> tuple[dict[str, Any], Path, dict[str, Any]]:
        marker = self._marker_state()
        if marker is None:
            raise MaintenanceError("maintenance interlock is absent")
        backup = Path(marker.get("backup_directory", ""))
        if not backup.is_absolute() or not backup.is_dir():
            raise MaintenanceError("interlocked backup directory is unavailable")
        if backup.resolve(strict=True) != self.backup:
            raise MaintenanceError(
                "--backup-dir differs from the protected maintenance interlock"
            )
        if _sha256_file(backup / "manifest.json") != marker.get("manifest_sha256"):
            raise MaintenanceError(
                "protected deployment manifest does not match its interlock"
            )
        manifest = _read_json(backup / "manifest.json")
        if os.name != "nt" and (
            stat.S_IMODE(backup.stat().st_mode) != 0o700
            or stat.S_IMODE((backup / "manifest.json").stat().st_mode) != 0o600
        ):
            raise MaintenanceError("maintenance backup permissions are not protected")
        self.manifest = manifest
        self.backup = backup
        return marker, backup, manifest

    def _verify_cold_backup(self, backup: Path) -> None:
        status = _read_json(backup / "state.json")
        if not any(
            item.get("stage") == "backup-complete" for item in status.get("stages", [])
        ):
            raise MaintenanceError("incomplete cold backup cannot be used")
        expected = _read_json(backup / "snapshot-inventory.json").get("entries")
        if (
            not isinstance(expected, list)
            or _inventory(backup / "snapshot") != expected
        ):
            raise MaintenanceError("immutable cold backup inventory has changed")
        _sqlite_integrity(backup / "snapshot")

    def _verify_recorded_images(self, manifest: dict[str, Any]) -> None:
        ids = {
            manifest["old_hub_image_id"],
            manifest["old_lab_image_id"],
            manifest["candidate_hub_image_id"],
            manifest["candidate_lab_image_id"],
            manifest["services"]["web"]["image_id"],
            manifest["services"]["proxy"]["image_id"],
        }
        for image_id in ids:
            if not image_id or self._image(image_id).get("Id") != image_id:
                raise MaintenanceError(
                    "recorded rollback or candidate image identity is unavailable"
                )

    def _verify_current_service_images(
        self, manifest: dict[str, Any], *, allow_missing: bool = False
    ) -> dict[str, dict[str, Any] | None]:
        current = {
            service: self._find_service_container(service) for service in SERVICES
        }
        if not allow_missing and any(item is None for item in current.values()):
            raise MaintenanceError("a required Compose service container is missing")
        for service in ("web", "proxy"):
            item = current[service]
            if (
                item is not None
                and item.get("Image") != manifest["services"][service]["image_id"]
            ):
                raise MaintenanceError(
                    f"current {service} image differs from recorded deployment"
                )
        allowed_hub = {manifest["candidate_hub_image_id"], manifest["old_hub_image_id"]}
        hub = current["hub"]
        if hub is not None and hub.get("Image") not in allowed_hub:
            raise MaintenanceError(
                "current Hub image is not a recorded candidate or rollback image"
            )
        return current

    def _verify_hub_wrapper_mode(
        self,
        hub: dict[str, Any],
        *,
        upgrade_db: bool,
        suppress_cullers: bool,
    ) -> None:
        environment = self._hub_environment(hub)
        if environment.get("JUPYTERHUB_ALLOW_DB_UPGRADE") != "false":
            raise MaintenanceError(
                "private Hub does not explicitly disable database upgrades"
            )
        expected_upgrade = "true" if upgrade_db else "false"
        if environment.get("JUPYTERHUB_MAINTENANCE_UPGRADE_DB") != expected_upgrade:
            raise MaintenanceError(
                "private Hub migration mode differs from the expected stage"
            )
        expected_suppression = "true" if suppress_cullers else "false"
        if (
            environment.get("JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS")
            != expected_suppression
        ):
            raise MaintenanceError(
                "private Hub culler mode differs from the expected stage"
            )
        config = hub.get("Config") or {}
        command = [*(config.get("Entrypoint") or []), *(config.get("Cmd") or [])]
        if "jupyterhub-maintenance-config.py" not in " ".join(command):
            raise MaintenanceError(
                "private Hub is not using the fixed maintenance config wrapper"
            )

    def _verify_private_hub_mode(self, hub: dict[str, Any]) -> None:
        self._verify_hub_wrapper_mode(hub, upgrade_db=False, suppress_cullers=True)

    def _current_owned_labs(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        return self._validate_labs(
            self._all_containers(),
            pool=Path(manifest["pool"]),
            lab_user=manifest["old_lab_user"],
        )

    def accept(self, args: argparse.Namespace) -> dict[str, Any]:
        self._required_acknowledgments(args)
        if not args.acceptance_passed:
            raise MaintenanceError(
                "accept requires the explicit --acceptance-passed acknowledgment"
            )
        _, backup, manifest = self._load_active()
        self._verify_cold_backup(backup)
        self._verify_recorded_images(manifest)
        current = self._verify_current_service_images(manifest)
        state = _read_json(backup / "state.json")
        stages = [item["stage"] for item in state.get("stages", [])]
        if not any(
            stage in stages
            for stage in (
                "candidate-private",
                "restored-private",
                "activation-failed-private",
            )
        ):
            raise MaintenanceError(
                "acceptance is allowed only from a ready private candidate/restore"
            )
        if not stages or stages[-1] not in {
            "candidate-private",
            "restored-private",
            "activation-failed-private",
        }:
            raise MaintenanceError(
                "the latest maintenance stage is not a ready private state"
            )
        # The caller asserts external HTTP/file/WS/quota acceptance; this helper does not claim it ran.
        running_hub = current["hub"]
        if running_hub is None:
            raise MaintenanceError("private Hub container is missing")
        if not bool((running_hub.get("State") or {}).get("Running")):
            raise MaintenanceError("private Hub is not running")
        if running_hub["Image"] not in {
            manifest["candidate_hub_image_id"],
            manifest["old_hub_image_id"],
        }:
            raise MaintenanceError(
                "private Hub image differs from the protected manifest"
            )
        self._verify_private_hub_mode(running_hub)
        web = current["web"]
        if web is None:
            raise MaintenanceError("web service container is missing")
        if bool((web.get("State") or {}).get("Running")):
            self._graceful_exit(web)
        proxy = current["proxy"]
        if proxy is None:
            raise MaintenanceError("proxy service container is missing")
        if not bool((proxy.get("State") or {}).get("Running")):
            raise MaintenanceError("proxy is not privately available for acceptance")
        self._check_foreign_hub_mounts(
            self._all_containers(), {str(running_hub.get("Id", ""))}
        )
        self._wait_private_ready()
        restored = running_hub["Image"] == manifest["old_hub_image_id"]
        runtime = self._write_runtime_override(backup, restored=restored)
        self._graceful_exit(running_hub)
        self._docker("rm", running_hub["Id"])
        self._graceful_exit(proxy)
        self._docker("rm", proxy["Id"])
        self._start_service("proxy", override=runtime)
        self._start_service("hub", override=runtime)
        accepted_hub = self._service_container("hub")
        if restored:
            self._verify_hub_wrapper_mode(
                accepted_hub, upgrade_db=False, suppress_cullers=False
            )
        else:
            environment = self._hub_environment(accepted_hub)
            if environment.get("JUPYTERHUB_ALLOW_DB_UPGRADE") != "false":
                raise MaintenanceError(
                    "accepted candidate Hub does not disable database upgrades"
                )
        self._wait_private_ready()
        # Public route is re-enabled only after explicit acknowledgment + normal Hub readiness.
        try:
            self._start_service("web", override=runtime)
            self._write_stage(
                backup,
                "accepted",
                {"acceptance_asserted": True, "runtime_override": str(runtime)},
            )
        except Exception as activation_error:
            try:
                fenced = self._refence_web()
            except Exception as fence_error:
                raise MaintenanceError(
                    "accept failed; public ingress closure could not be confirmed; "
                    "maintenance interlock remains present and operator intervention is required"
                ) from fence_error
            if not fenced:
                raise MaintenanceError(
                    "accept failed; public ingress closure could not be confirmed; "
                    "maintenance interlock remains present and operator intervention is required"
                ) from activation_error
            try:
                self._return_to_private_acceptance_state(
                    backup,
                    manifest,
                    restored=restored,
                    failure=activation_error,
                )
            except Exception as recovery_error:
                raise MaintenanceError(
                    "accept failed; public web is confirmed re-fenced, but private Hub recovery "
                    "could not be confirmed; maintenance interlock remains present"
                ) from recovery_error
            raise MaintenanceError(
                "accept failed; public web was re-fenced and a retryable private Hub state was restored"
            ) from activation_error
        return {
            "stage": "accepted",
            "acceptance_was_operator_asserted": True,
            "runtime_override": str(runtime),
            "marker": str(self.marker),
            "web": "started",
            "timer_resumed": False,
        }

    def restore(self, args: argparse.Namespace) -> dict[str, Any]:
        self._required_acknowledgments(args)
        if not args.acknowledge_restore:
            raise MaintenanceError("restore requires --acknowledge-restore")
        _, backup, manifest = self._load_active()
        self._verify_cold_backup(backup)
        self._verify_recorded_images(manifest)
        current = self._verify_current_service_images(manifest, allow_missing=True)
        allowed_hub_ids = {
            str(current["hub"]["Id"]) if current["hub"] is not None else ""
        }
        self._check_foreign_hub_mounts(self._all_containers(), allowed_hub_ids)
        # Web must already be stopped; if it has restarted externally, fence it first.
        for service in ("web", "proxy", "hub"):
            item = current[service]
            if item is not None and bool((item.get("State") or {}).get("Running")):
                self._graceful_exit(item)
                self._write_stage(backup, f"restore-stopped-{service}")
        labs = self._current_owned_labs(manifest)
        self._stop_owned_labs(labs)
        self._check_foreign_hub_mounts(self._all_containers(), allowed_hub_ids)
        failed = backup / f"failed-state-{uuid.uuid4().hex}"
        self._copy_and_verify_cold_backup_to(self.host_paths["/srv/hub"], failed)
        self._write_stage(backup, "failed-state-preserved", {"directory": str(failed)})
        self._remove_owned_labs(labs)
        data = self.host_paths["/srv/hub"]
        if not data.is_dir():
            raise MaintenanceError("configured Hub data destination is unavailable")
        displaced = data.with_name(f"{data.name}.failed-{uuid.uuid4().hex}")
        staging = data.with_name(f"{data.name}.restore-{uuid.uuid4().hex}")
        _copy_tree_preserving(backup / "snapshot", staging)
        _sqlite_integrity(staging)
        data.replace(displaced)
        try:
            staging.replace(data)
        except Exception:
            displaced.replace(data)
            raise
        self._write_stage(backup, "database-restored", {"displaced": str(displaced)})
        wrapper_source = backup / "configuration" / "jupyterhub_maintenance_config.py"
        private = self._write_private_override(
            backup,
            hub_image=manifest["old_hub_image_id"],
            lab_image=manifest["old_lab_image_id"],
            upgrade_db=False,
            suppress_cullers=True,
            wrapper=wrapper_source,
            proxy_image=manifest["services"]["proxy"]["image_id"],
            web_image=manifest["services"]["web"]["image_id"],
        )
        self._start_service("proxy", override=private)
        self._start_service("hub", override=private)
        restored_hub = self._service_container("hub")
        self._verify_hub_wrapper_mode(
            restored_hub, upgrade_db=False, suppress_cullers=True
        )
        self._wait_private_ready()
        self._write_stage(
            backup,
            "restored-private",
            {"cullers_suppressed": True, "failed_state": str(failed)},
        )
        self._write_marker(manifest, backup)
        return {
            "stage": "restored-private",
            "web": "stopped",
            "backup": str(backup),
            "private_override": str(private),
            "failed_state": str(failed),
        }

    def _copy_and_verify_cold_backup_to(self, source: Path, destination: Path) -> None:
        inventory = _copy_tree_preserving(source, destination)
        _atomic_json(
            destination.parent / f"{destination.name}.inventory.json",
            {"entries": inventory},
        )


def _tree_size(root: Path) -> int:
    total = 0
    for current, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(current) / name
            if not path.is_symlink():
                total += path.stat().st_size
    return total


def _safe_failure(exc: Exception) -> str:
    if isinstance(exc, MaintenanceError):
        return str(exc)
    return "unexpected bounded maintenance failure"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed JupyterHub maintenance")
    parser.add_argument(
        "command", choices=("preflight", "migrate", "accept", "restore")
    )
    parser.add_argument("--project-directory", required=True, type=Path)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--compose-file", required=True, action="append", type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--acknowledge-interruption", action="store_true")
    parser.add_argument("--acknowledge-ingress-fenced", action="store_true")
    parser.add_argument("--acknowledge-updater-paused", action="store_true")
    parser.add_argument("--acceptance-passed", action="store_true")
    parser.add_argument("--acknowledge-restore", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        controller = Controller(
            project_directory=args.project_directory,
            project_name=args.project_name,
            env_file=args.env_file,
            compose_files=args.compose_file,
            backup_dir=args.backup_dir,
        )
        lock = Path(os.environ.get("UPDATE_LOCK_FILE", LOCK_DEFAULT))
        with DeploymentLock(lock):
            result = getattr(controller, args.command)(args)
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    except MaintenanceError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}, sort_keys=True) + "\n")
        return 2
    except (OSError, sqlite3.Error, subprocess.SubprocessError):  # fmt: skip
        sys.stderr.write(
            json.dumps({"error": "bounded maintenance operation failed"}) + "\n"
        )
        return 2
    except Exception:  # ruff: ignore[BLE001] - redact unexpected operational failures
        sys.stderr.write(
            json.dumps({"error": "bounded maintenance operation failed"}) + "\n"
        )
        return 2
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
