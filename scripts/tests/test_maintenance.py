from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from scripts import jupyterhub_maintenance as maintenance

OWNED_LAB_ID = "a" * 64


def make_database(directory: Path, value: str = "seed") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / "jupyterhub.sqlite")
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT NOT NULL)")
        connection.execute("DELETE FROM marker")
        connection.execute("INSERT INTO marker VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def service_container(
    service: str, image: str, *, running: bool = True
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "Labels": {
            "com.docker.compose.project": "test-project",
            "com.docker.compose.service": service,
        }
    }
    if service == "hub":
        config["Env"] = [
            "JUPYTERHUB_ALLOW_DB_UPGRADE=false",
            "JUPYTERHUB_MAINTENANCE_UPGRADE_DB=false",
            "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS=true",
        ]
        config["Cmd"] = [
            "jupyterhub",
            "-f",
            "/srv/maintenance/jupyterhub-maintenance-config.py",
        ]
    return {
        "Id": f"{service}-container",
        "Name": f"/{service}-project-1",
        "Image": image,
        "State": {"Running": running, "Status": "running" if running else "exited"},
        "Config": config,
        "HostConfig": {
            "RestartPolicy": {"Name": "unless-stopped"},
            "NetworkMode": "host",
        },
        "Mounts": [],
    }


class MigrationHarness(maintenance.Controller):
    """Controller subclass: filesystem logic is real; every Docker action is fake."""

    def __init__(self, root: Path, *, bad_database: bool = False) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.bad_database = bad_database
        self.clock_value = 0.0
        self.probe_count = 0
        self.all_container_scans = 0
        self.inspect_fault: str | None = None
        self.auto_remove_delay_inspects = 0
        self.pending_auto_remove: dict[str, int] = {}
        self.keep_auto_remove_container = False
        self.remove_non_auto_on_term = False
        self.inventory_mutation: str | None = None
        self.lab_inspect_count = 0
        self.inspect_fault_at = 1
        self.fail_command_phase: str | None = None
        self.fail_command_operation: str | None = None
        self.fail_command_occurrence = 1
        self.command_operation_counts: dict[str, int] = {}
        self.phase_events: list[tuple[Any, ...]] = []
        self.lab = {
            "id": OWNED_LAB_ID,
            "name": "lab-alice",
            "username": "alice",
            "image_id": "sha256:old-lab",
            "version": "5.5.1",
            "home": str(root / "pool" / "users" / "alice"),
            "running": True,
            "restart_policy": "unless-stopped",
            "auto_remove": False,
        }
        pool = root / "pool"
        self.containers = {
            "web-container": service_container("web", "sha256:web"),
            "proxy-container": service_container("proxy", "sha256:proxy"),
            "hub-container": service_container("hub", "sha256:old-hub"),
            OWNED_LAB_ID: {
                "Id": OWNED_LAB_ID,
                "Name": "/lab-alice",
                "Image": "sha256:old-lab",
                "State": {"Running": True},
                "Config": {"Labels": {"finki.role": "lab", "finki.user": "alice"}},
                "HostConfig": {
                    "RestartPolicy": {"Name": "unless-stopped"},
                    "AutoRemove": False,
                },
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(pool / "users" / "alice"),
                        "Destination": "/home/ubuntu",
                        "RW": True,
                    }
                ],
            },
        }
        self.new_hub: dict[str, Any] | None = None
        project = root / "project"
        project.mkdir(parents=True)
        self.data = root / "data" / "hub"
        self.data.mkdir(parents=True)
        if bad_database:
            (self.data / "jupyterhub.sqlite").write_text("not sqlite", encoding="utf-8")
        else:
            make_database(self.data)
        (self.data / "jupyterhub_cookie_secret").write_text("private", encoding="utf-8")
        (pool / "users" / "alice").mkdir(parents=True)
        (pool / ".pool-id").write_text("pool-fixture", encoding="utf-8")
        config_file = root / "compose.yaml"
        config_file.write_text("services: {}\n", encoding="utf-8")
        env_file = root / "fixture.env"
        env_file.write_text(
            "CONFIGPROXY_AUTH_TOKEN=do-not-disclose\n", encoding="utf-8"
        )
        backup_parent = root / "protected-backups"
        backup_parent.mkdir()
        super().__init__(
            project_directory=project,
            project_name="test-project",
            env_file=env_file,
            compose_files=[config_file],
            backup_dir=backup_parent / "upgrade-1",
            command=self.fake_command,
            clock=self.clock,
        )
        self.sleeper = self._advance_time
        self.host_paths = {"/srv/hub": self.data, "/srv/pool": pool}
        self.lab["shape_sha256"] = self._lab_shape_sha256(
            self.containers[OWNED_LAB_ID], "alice"
        )
        self.manifest = {
            "daemon_id": "fixture-daemon-id",
            "project_name": "test-project",
            "project_directory": str(project.resolve()),
            "compose_paths": [str(config_file), str(env_file)],
            "config_hashes": {},
            "effective_config_sha256": "fixture-config",
            "hub_data": str(self.data),
            "pool": str(pool),
            "services": {
                "web": {
                    "container_id": "web-container",
                    "image_id": "sha256:web",
                    "restart_policy": "unless-stopped",
                },
                "proxy": {
                    "container_id": "proxy-container",
                    "image_id": "sha256:proxy",
                    "restart_policy": "unless-stopped",
                },
                "hub": {
                    "container_id": "hub-container",
                    "image_id": "sha256:old-hub",
                    "restart_policy": "unless-stopped",
                },
            },
            "old_hub_version": "5.5.1",
            "old_hub_image_id": "sha256:old-hub",
            "old_lab_image_id": "sha256:old-lab",
            "old_lab_version": "5.5.1",
            "old_lab_user": "ubuntu",
            "candidate_hub_ref": "candidate-hub",
            "candidate_hub_image_id": "sha256:candidate-hub",
            "candidate_hub_version": "6.0.1",
            "candidate_lab_ref": "candidate-lab",
            "candidate_lab_image_id": "sha256:candidate-lab",
            "candidate_lab_version": "6.0.1",
            "labs": [self.lab],
        }
        self.readiness_calls = 0
        self.fail_readiness_at: int | None = None

    def fake_command(self, args: Any, timeout: float, check: bool = True) -> Any:
        self.events.append(("command", tuple(args), timeout))
        operation = self._command_operation(args)
        self.phase_events.append(("command", self.failure_phase, operation))
        count_operation = operation is not None and not (
            operation == "container-list" and "--filter" in args
        )
        if operation is not None and count_operation:
            self.command_operation_counts[operation] = (
                self.command_operation_counts.get(operation, 0) + 1
            )
            if (
                operation == self.fail_command_operation
                and self.failure_phase == self.fail_command_phase
                and count_operation
                and self.command_operation_counts[operation]
                == self.fail_command_occurrence
            ):
                failure = maintenance.ExternalCommandFailure(operation, "nonzero", 27)
                failure.failure_phase = self.failure_phase
                raise failure
        if args[:2] == ["docker", "inspect"] and len(args) == 3:
            item = self._fake_container(args[2])
            if item is None:
                return maintenance._CONTAINER_ABSENT
            if args[2] == OWNED_LAB_ID:
                self.lab_inspect_count += 1
                if (
                    self.inspect_fault
                    and self.lab_inspect_count >= self.inspect_fault_at
                    and self.inspect_fault == "transport"
                ):
                    raise maintenance.ExternalCommandFailure(
                        "container-inspect", "nonzero", 23
                    )
                if (
                    self.lab_inspect_count >= self.inspect_fault_at
                    and self.inspect_fault == "malformed"
                ):
                    return "not-json"
                if (
                    self.lab_inspect_count >= self.inspect_fault_at
                    and self.inspect_fault == "wrong-id"
                ):
                    item = json.loads(json.dumps(item))
                    item["Id"] = "different-full-id"
            if args[2] == OWNED_LAB_ID and args[2] in self.pending_auto_remove:
                remaining = self.pending_auto_remove[args[2]]
                if remaining <= 0:
                    self.pending_auto_remove.pop(args[2], None)
                    self.containers.pop(args[2], None)
                    return maintenance._CONTAINER_ABSENT
                self.pending_auto_remove[args[2]] = remaining - 1
            return json.dumps([item])
        if args[:2] == ["docker", "create"]:
            self.probe_count += 1
            return f"probe-container-{self.probe_count}"
        if args[:3] == ["docker", "start", "-a"]:
            return "5.5.1"
        if args[:2] == ["docker", "ps"]:
            if "--filter" in args:
                return ""
            self.all_container_scans += 1
            if self.all_container_scans == 3 and self.inventory_mutation:
                self._mutate_inventory(self.inventory_mutation)
            identifiers = list(self.containers)
            if self.new_hub is not None:
                identifiers.append(self.new_hub["Id"])
            return "\n".join(identifiers)
        if args[:2] == ["docker", "update"]:
            item = self._fake_container(args[-1])
            if item is not None:
                item.setdefault("HostConfig", {}).setdefault("RestartPolicy", {})[
                    "Name"
                ] = "no"
            return ""
        if args[:2] == ["docker", "kill"]:
            identifier = args[-1]
            item = self._fake_container(identifier)
            if item is not None:
                item.setdefault("State", {})["Running"] = False
                labels = maintenance.Controller._labels(item)
                name = labels.get("com.docker.compose.service") or item.get(
                    "Name", "lab"
                )
                self.events.append(("graceful-exit", name))
                if item.get("HostConfig", {}).get("AutoRemove") is True:
                    if not self.keep_auto_remove_container:
                        if self.auto_remove_delay_inspects:
                            self.pending_auto_remove[item["Id"]] = (
                                self.auto_remove_delay_inspects
                            )
                        else:
                            self.containers.pop(item["Id"], None)
                elif self.remove_non_auto_on_term:
                    self.containers.pop(item["Id"], None)
            return ""
        if args[:2] == ["docker", "rm"]:
            item = self._fake_container(args[-1])
            if item is not None:
                if maintenance.Controller._labels(item).get("finki.role") == "lab":
                    self.events.append(("remove-labs", (item["Id"],)))
                self.containers.pop(item["Id"], None)
        return ""

    @staticmethod
    def _command_operation(args: Any) -> str | None:
        if args[:2] != ["docker", "compose"]:
            if args[:2] == ["docker", "inspect"]:
                return "container-inspect"
            if args[:2] == ["docker", "image"]:
                return "image-inspect"
            if args[:2] == ["docker", "info"]:
                return "daemon-info"
            if args[:2] == ["docker", "ps"]:
                return "container-list"
            if args[:2] == ["docker", "update"]:
                return "restart-disable"
            if args[:2] == ["docker", "kill"]:
                return "send-term"
            if args[:2] == ["docker", "rm"]:
                return "container-remove"
            if args[:2] == ["docker", "create"]:
                return "lab-version-create"
            if args[:2] == ["docker", "start"]:
                return "lab-version-start"
            if args[:2] == ["docker", "exec"]:
                return "hub-version-exec"
            return None
        if "config" in args:
            return "compose-config"
        if "ps" in args:
            return "compose-ps"
        if "up" in args and args[-1:] == ["proxy"]:
            return "compose-up-proxy"
        if "up" in args and args[-1:] == ["hub"]:
            return "compose-up-hub"
        if "up" in args and args[-1:] == ["web"]:
            return "compose-up-web"
        return None

    def _mutate_inventory(self, mutation: str) -> None:
        if mutation == "new-lab":
            home = self.host_paths["/srv/pool"] / "users" / "bob"
            home.mkdir(parents=True, exist_ok=True)
            self.containers["unrelated-lab-id"] = {
                "Id": "unrelated-lab-id",
                "Name": "/lab-bob",
                "Image": "sha256:old-lab",
                "State": {"Running": False},
                "Config": {"Labels": {"finki.role": "lab", "finki.user": "bob"}},
                "HostConfig": {
                    "RestartPolicy": {"Name": "no"},
                    "AutoRemove": False,
                },
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(home),
                        "Destination": "/home/ubuntu",
                        "RW": True,
                    }
                ],
            }
        elif mutation == "mount":
            self.containers[OWNED_LAB_ID]["Mounts"][0]["Source"] = str(
                self.host_paths["/srv/pool"] / "users" / "changed"
            )
        elif mutation == "home":
            changed_home = self.host_paths["/srv/pool"] / "users" / "changed"
            changed_home.mkdir(parents=True, exist_ok=True)
            self.containers[OWNED_LAB_ID]["Mounts"][0]["Source"] = str(changed_home)
        elif mutation == "image":
            self.containers[OWNED_LAB_ID]["Image"] = "sha256:changed-image"

    def _fake_container(self, identifier: str) -> dict[str, Any] | None:
        if identifier in self.containers:
            return self.containers[identifier]
        if self.new_hub and identifier == self.new_hub.get("Id"):
            return self.new_hub
        return next(
            (
                item
                for item in self.containers.values()
                if str(item.get("Id", "")).startswith(identifier)
            ),
            None,
        )

    def clock(self) -> float:
        return self.clock_value

    def _advance_time(self, seconds: float) -> None:
        self.clock_value += seconds

    def _preflight(self, *, new_backup: bool) -> dict[str, Any]:
        self.events.append(("preflight", new_backup))
        return {"plan": "sanitized"}

    def _write_stage(self, backup: Path, stage: str, facts: Any = None) -> None:
        super()._write_stage(backup, stage, facts)
        self.events.append((stage,))
        self.phase_events.append(("stage", stage, self.failure_phase))

    def _start_service(
        self, service: str, *, override: Path, files: Any = None
    ) -> None:
        operation = {
            "proxy": "compose-up-proxy",
            "hub": "compose-up-hub",
            "web": "compose-up-web",
        }[service]
        self.phase_events.append(("start", service, self.failure_phase))
        self.command_operation_counts[operation] = (
            self.command_operation_counts.get(operation, 0) + 1
        )
        if (
            self.fail_command_operation == operation
            and self.failure_phase == self.fail_command_phase
            and self.command_operation_counts[operation] == self.fail_command_occurrence
        ):
            failure = maintenance.ExternalCommandFailure(operation, "nonzero", 27)
            failure.failure_phase = self.failure_phase
            raise failure
        override_content = json.loads(override.read_text(encoding="utf-8"))
        self.events.append(("start", service, override_content["services"]))
        if service == "hub":
            image = override_content["services"]["hub"]["image"]
            self.new_hub = service_container("hub", image)
            hub_override = override_content["services"]["hub"]
            environment = hub_override.get("environment") or {}
            if environment:
                self.new_hub["Config"]["Env"] = [
                    f"{key}={value}" for key, value in environment.items()
                ]
            if "command" in hub_override:
                self.new_hub["Config"]["Cmd"] = hub_override["command"]
            self.new_hub["Id"] = (
                f"candidate-hub-{len([e for e in self.events if e[0] == 'start' and e[1] == 'hub'])}"
            )

    def _service_container(
        self, service: str, *, required_running: bool = True
    ) -> dict[str, Any]:
        if service == "hub" and self.new_hub:
            return self.new_hub
        return self.containers[f"{service}-container"]

    def _wait_private_ready(self, timeout: float = 180) -> None:
        self.readiness_calls += 1
        self.events.append(("private-ready", self.readiness_calls))
        self.phase_events.append(("ready", self.readiness_calls, self.failure_phase))
        if self.fail_readiness_at == self.readiness_calls:
            raise maintenance.MaintenanceError(
                "private Hub readiness probe failed before deadline"
            )


class RestoreHarness(MigrationHarness):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.backup.mkdir(mode=0o700)
        self.backup.chmod(0o700)
        (self.backup / "configuration").mkdir(mode=0o700)
        wrapper = Path(maintenance.__file__).with_name(
            "jupyterhub_maintenance_config.py"
        )
        wrapper_copy = self.backup / "configuration" / wrapper.name
        wrapper_copy.write_bytes(wrapper.read_bytes())
        wrapper_copy.chmod(0o600)
        cold_source = root / "pre-upgrade-state"
        cold_source.mkdir()
        connection = sqlite3.connect(cold_source / "jupyterhub.sqlite")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE identity (value TEXT)")
        connection.execute("INSERT INTO identity VALUES ('old-state')")
        connection.commit()
        (cold_source / "jupyterhub_cookie_secret").write_text(
            "old-cookie", encoding="utf-8"
        )
        self.backup_snapshot = self.backup / "snapshot"
        entries = maintenance._copy_tree_preserving(cold_source, self.backup_snapshot)
        self.assert_has_wal = (self.backup_snapshot / "jupyterhub.sqlite-wal").exists()
        maintenance._sqlite_integrity(self.backup_snapshot)
        connection.close()
        maintenance._atomic_json(
            self.backup / "snapshot-inventory.json", {"entries": entries}
        )
        self.manifest["config_wrapper_sha256"] = maintenance._sha256_file(wrapper_copy)
        maintenance._atomic_json(self.backup / "manifest.json", self.manifest)
        maintenance._atomic_json(
            self.backup / "state.json", {"stages": [{"stage": "backup-complete"}]}
        )
        self.backup.joinpath("manifest.json").chmod(0o600)
        self.current_services = {
            "web": service_container("web", "sha256:web", running=False),
            "proxy": service_container("proxy", "sha256:proxy", running=True),
            "hub": service_container("hub", "sha256:old-hub", running=True),
        }
        self._write_marker(self.manifest, self.backup)

    def _load_active(self) -> tuple[dict[str, Any], Path, dict[str, Any]]:
        self.manifest = maintenance._read_json(self.backup / "manifest.json")
        return maintenance._read_json(self.marker), self.backup, self.manifest

    def _verify_recorded_images(self, manifest: dict[str, Any]) -> None:
        self.events.append(("verify-images",))

    def _verify_current_service_images(
        self, manifest: dict[str, Any], *, allow_missing: bool = False
    ) -> dict[str, Any]:
        self.events.append(("verify-current-images", allow_missing))
        return self.current_services

    def _check_foreign_hub_mounts(self, containers: Any, hub_ids: Any) -> None:
        self.events.append(("check-foreign-writers", tuple(hub_ids)))

    def _current_owned_labs(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def _start_service(
        self, service: str, *, override: Path, files: Any = None
    ) -> None:
        super()._start_service(service, override=override, files=files)
        if service in self.current_services:
            if service == "hub" and self.new_hub:
                self.current_services[service] = self.new_hub
            else:
                self.current_services[service] = service_container(
                    service, self.current_services[service]["Image"], running=True
                )


class MaintenanceControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    @staticmethod
    def acknowledgments(**overrides: Any) -> argparse.Namespace:
        values = {
            "acknowledge_interruption": True,
            "acknowledge_ingress_fenced": True,
            "acknowledge_updater_paused": True,
            "acceptance_passed": True,
            "acknowledge_restore": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def _main_arguments(self, operation: str, root: Path) -> list[str]:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
        env_file = root / "environment.env"
        env_file.write_text("FIXTURE=nonsecret\n", encoding="utf-8")
        compose_file = root / "compose.yaml"
        compose_file.write_text("services: {}\n", encoding="utf-8")
        return [
            operation,
            "--project-directory",
            str(project),
            "--project-name",
            "test-project",
            "--env-file",
            str(env_file),
            "--compose-file",
            str(compose_file),
            "--backup-dir",
            str(root / "backup"),
        ]

    def _invoke_main(
        self, operation: str, *, extra: tuple[str, ...] = ()
    ) -> tuple[int, str, str]:
        root = self.root / operation
        root.mkdir(exist_ok=True)
        args = self._main_arguments(operation, root)
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"UPDATE_LOCK_FILE": str(root / "lock")}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = maintenance.main([*args, *extra])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_main_preflight_dispatches_bound_method_without_namespace(self) -> None:
        with patch.object(
            maintenance.Controller,
            "_load_config",
            side_effect=maintenance.MaintenanceError(
                "Compose configuration is missing a required service"
            ),
        ):
            status, stdout, stderr = self._invoke_main("preflight")

        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            json.loads(stderr),
            {
                "status": "failed",
                "operation": "preflight",
                "phase": "configuration",
                "classification": "required-service-missing",
                "error": "Compose configuration is missing a required service",
            },
        )

    def test_main_passes_arguments_to_guarded_operations(self) -> None:
        for operation in ("migrate", "accept", "restore"):
            with self.subTest(operation=operation):
                status, stdout, stderr = self._invoke_main(operation)
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                diagnostic = json.loads(stderr)
                self.assertEqual(
                    set(diagnostic),
                    {"status", "operation", "phase", "classification", "error"},
                )
                self.assertEqual(diagnostic["status"], "failed")
                self.assertEqual(diagnostic["operation"], operation)
                self.assertEqual(diagnostic["phase"], "dispatch")
                self.assertEqual(diagnostic["classification"], "maintenance-error")
                self.assertEqual(diagnostic["error"], "maintenance operation failed")

    def test_acceptance_refusal_preserves_canonical_error(self) -> None:
        status, stdout, stderr = self._invoke_main(
            "accept",
            extra=(
                "--acknowledge-interruption",
                "--acknowledge-ingress-fenced",
                "--acknowledge-updater-paused",
            ),
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            json.loads(stderr),
            {
                "status": "failed",
                "operation": "accept",
                "phase": "dispatch",
                "classification": "acceptance-not-acknowledged",
                "error": "accept requires the explicit --acceptance-passed acknowledgment",
            },
        )

    def test_successful_preflight_output_remains_unchanged(self) -> None:
        with patch.object(
            maintenance.Controller, "_preflight", return_value={"ok": True}
        ):
            status, stdout, stderr = self._invoke_main("preflight")
        self.assertEqual(status, 0)
        self.assertEqual(stdout, '{"ok": true}\n')
        self.assertEqual(stderr, "")

    def test_closed_diagnostic_classifies_only_exact_known_errors(self) -> None:
        expected = {
            "Compose configuration is missing a required service": "required-service-missing",
            "unsupported external Lab network configuration": "network-topology-mismatch",
            "Compose Hub binds or networking differ from the protected deployment": "hub-shape-mismatch",
            "running Hub and its selected Lab image are not version-matched": "running-pair-mismatch",
            "candidate Hub and Lab image versions do not match": "candidate-pair-mismatch",
            "possible Lab has an unexpected or ambiguous home bind": "lab-home-mismatch",
            "backup parent directory must already exist": "backup-parent-missing",
            "bounded external command failed": "external-command-failed",
            "accept requires the explicit --acceptance-passed acknowledgment": "acceptance-not-acknowledged",
        }
        for error, classification in expected.items():
            with self.subTest(classification=classification):
                diagnostic = maintenance._failure_diagnostic(
                    maintenance.MaintenanceError(error),
                    operation="preflight",
                    phase="configuration",
                )
                self.assertEqual(diagnostic["classification"], classification)
                self.assertEqual(diagnostic["error"], error)
        near_match = maintenance._failure_diagnostic(
            maintenance.MaintenanceError(
                "prefix: Compose configuration is missing a required service"
            ),
            operation="preflight",
            phase="configuration",
        )
        self.assertEqual(near_match["classification"], "maintenance-error")
        self.assertEqual(near_match["error"], "maintenance operation failed")
        invalid_context = maintenance._failure_diagnostic(
            maintenance.MaintenanceError("synthetic private failure"),
            operation="not-a-parser-operation",
            phase="C:\\private\\synthetic-path",
        )
        self.assertEqual(invalid_context["operation"], "preflight")
        self.assertEqual(invalid_context["phase"], "dispatch")
        self.assertLessEqual(
            len(json.dumps(invalid_context).encode("utf-8")), 64 * 1024
        )

    def _system_command_controller(self) -> maintenance.Controller:
        root = self.root / "system-command"
        args = self._main_arguments("preflight", root)
        return maintenance.Controller(
            project_directory=Path(args[args.index("--project-directory") + 1]),
            project_name="test-project",
            env_file=Path(args[args.index("--env-file") + 1]),
            compose_files=[Path(args[args.index("--compose-file") + 1])],
            backup_dir=Path(args[args.index("--backup-dir") + 1]),
        )

    def test_external_command_failures_keep_only_closed_fields_and_primary_phase(
        self,
    ) -> None:
        controller = self._system_command_controller()
        controller.failure_phase = "reconcile-labs"
        secret = "SYNTHETIC_COMMAND_OUTPUT_SECRET"
        scenarios = (
            (
                "nonzero",
                patch(
                    "scripts.jupyterhub_maintenance.subprocess.run",
                    return_value=maintenance.subprocess.CompletedProcess(
                        ["private-argv"], 23, f"{secret}-stdout", f"{secret}-stderr"
                    ),
                ),
                23,
            ),
            (
                "timeout",
                patch(
                    "scripts.jupyterhub_maintenance.subprocess.run",
                    side_effect=maintenance.subprocess.TimeoutExpired(
                        ["private-argv"], 30, output=secret, stderr=secret
                    ),
                ),
                None,
            ),
            (
                "exec-failed",
                patch(
                    "scripts.jupyterhub_maintenance.subprocess.run",
                    side_effect=OSError(secret),
                ),
                None,
            ),
        )
        for status, subprocess_mock, returncode in scenarios:
            with self.subTest(command_status=status), subprocess_mock:
                with self.assertRaises(maintenance.ExternalCommandFailure) as raised:
                    controller._docker("ps", "-aq", operation="container-list")
                failure = raised.exception
                self.assertEqual(
                    vars(failure),
                    {
                        "command_operation": "container-list",
                        "command_status": status,
                        "external_returncode": returncode,
                        "failure_phase": "reconcile-labs",
                    },
                )
                diagnostic = maintenance._failure_diagnostic(
                    failure, operation="migrate", phase="dispatch"
                )
                self.assertEqual(
                    set(diagnostic),
                    {
                        "status",
                        "operation",
                        "phase",
                        "classification",
                        "error",
                        "command_operation",
                        "command_status",
                        "external_returncode",
                    },
                )
                self.assertEqual(diagnostic["phase"], "reconcile-labs")
                self.assertEqual(
                    diagnostic["classification"], "external-command-failed"
                )
                self.assertEqual(diagnostic["error"], "bounded external command failed")
                self.assertEqual(diagnostic["command_operation"], "container-list")
                self.assertEqual(diagnostic["command_status"], status)
                self.assertEqual(diagnostic["external_returncode"], returncode)
                self.assertNotIn(secret, json.dumps(diagnostic))
                self.assertEqual(str(failure), "bounded external command failed")

    def test_cli_emits_typed_command_envelope_without_private_operands(self) -> None:
        secret = "SYNTHETIC_COMMAND_FAILURE_SECRET"
        with patch.object(
            maintenance.subprocess,
            "run",
            return_value=maintenance.subprocess.CompletedProcess(
                ["private-command", secret], 19, secret, secret
            ),
        ):
            status, stdout, stderr = self._invoke_main("preflight")
        diagnostic = json.loads(stderr)
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            diagnostic,
            {
                "status": "failed",
                "operation": "preflight",
                "phase": "configuration",
                "classification": "external-command-failed",
                "error": "bounded external command failed",
                "command_operation": "compose-config",
                "command_status": "nonzero",
                "external_returncode": 19,
            },
        )
        self.assertNotIn(secret, stderr)
        self.assertNotIn(str(self.root / "preflight"), stderr)

    def test_exact_container_absence_requires_bound_cli_not_found_evidence(
        self,
    ) -> None:
        controller = self._system_command_controller()
        identifier = "f" * 64
        allowed = (
            ("", f"Error: No such object: {identifier}"),
            ("[]\n", f"Error: No such container: {identifier}"),
            (
                "[]",
                f"Error response from daemon: No such container: {identifier}",
            ),
        )
        for stdout, stderr in allowed:
            with (
                self.subTest(allowed_stdout=bool(stdout)),
                patch(
                    "scripts.jupyterhub_maintenance.subprocess.run",
                    return_value=maintenance.subprocess.CompletedProcess(
                        ["docker", "inspect", identifier], 1, stdout, stderr
                    ),
                ),
            ):
                self.assertIsNone(controller._inspect_exact_or_missing(identifier))

        rejected = (
            ("[]", f"Error: No such object: {'e' * 64}"),
            ('[{"Id":"' + identifier + '"}]', f"Error: No such object: {identifier}"),
            ("not-json", f"Error: No such object: {identifier}"),
        )
        for stdout, stderr in rejected:
            with (
                self.subTest(rejected_stdout=stdout[:2]),
                patch(
                    "scripts.jupyterhub_maintenance.subprocess.run",
                    return_value=maintenance.subprocess.CompletedProcess(
                        ["docker", "inspect", identifier], 1, stdout, stderr
                    ),
                ),
            ):
                with self.assertRaises(maintenance.ExternalCommandFailure):
                    controller._inspect_exact_or_missing(identifier)

    def test_unknown_failure_text_is_never_emitted(self) -> None:
        secret = "CONFIGPROXY_AUTH_TOKEN=synthetic-diagnostic-test-secret"

        class OpaqueError(Exception):
            def __str__(self) -> str:
                raise AssertionError("unexpected exception text must not be read")

        for error, classification, safe_text in (
            (
                maintenance.MaintenanceError(secret),
                "maintenance-error",
                "maintenance operation failed",
            ),
            (OSError(secret), "unexpected-error", "unexpected maintenance failure"),
            (OpaqueError(secret), "unexpected-error", "unexpected maintenance failure"),
        ):
            with self.subTest(classification=classification, kind=type(error).__name__):
                with patch.object(
                    maintenance.Controller, "__init__", side_effect=error
                ):
                    status, stdout, stderr = self._invoke_main("preflight")
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn(secret, stderr)
                diagnostic = json.loads(stderr)
                self.assertEqual(
                    set(diagnostic),
                    {"status", "operation", "phase", "classification", "error"},
                )
                self.assertEqual(diagnostic["phase"], "initialize")
                self.assertEqual(diagnostic["classification"], classification)
                self.assertEqual(diagnostic["error"], safe_text)

    def test_missing_acknowledgments_cause_no_controller_activity(self) -> None:
        controller = MigrationHarness(self.root)
        with self.assertRaisesRegex(maintenance.MaintenanceError, "acknowledgments"):
            controller.migrate(self.acknowledgments(acknowledge_interruption=False))
        self.assertEqual(controller.events, [])
        self.assertFalse(controller.marker.exists())

    def test_acknowledgments_cannot_be_supplied_through_environment(self) -> None:
        env = {
            "JUPYTERHUB_ACKNOWLEDGE_INTERRUPTION": "true",
            "JUPYTERHUB_ACKNOWLEDGE_INGRESS_FENCED": "true",
            "JUPYTERHUB_ACKNOWLEDGE_UPDATER_PAUSED": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            args = maintenance._parser().parse_args(
                [
                    "migrate",
                    "--project-directory",
                    str(self.root.resolve()),
                    "--project-name",
                    "test-project",
                    "--env-file",
                    str(self.root / "env"),
                    "--compose-file",
                    str(self.root / "compose.yaml"),
                    "--backup-dir",
                    str(self.root / "backup"),
                ]
            )
        self.assertFalse(args.acknowledge_interruption)
        self.assertFalse(args.acknowledge_ingress_fenced)
        self.assertFalse(args.acknowledge_updater_paused)

    def test_shared_lock_rejects_a_second_writer(self) -> None:
        path = self.root / "lock" / "deployment.lock"
        with maintenance.DeploymentLock(path):
            with self.assertRaisesRegex(maintenance.MaintenanceError, "lock is held"):
                with maintenance.DeploymentLock(path):
                    self.fail("second writer acquired the deployment lock")

    def test_config_drift_refuses_active_marker(self) -> None:
        controller = MigrationHarness(self.root)
        setattr(  # ruff: ignore[B010] - intentional test double for config drift
            controller,
            "_load_config",
            lambda: setattr(controller, "config", {"changed": True}),
        )
        setattr(  # ruff: ignore[B010] - intentional test double for daemon lookup
            controller,
            "_daemon",
            lambda: setattr(controller, "daemon_id", "fixture-daemon-id"),
        )
        setattr(  # ruff: ignore[B010] - intentional config-hash test double
            controller, "_config_hashes", lambda: {"config": "changed"}
        )
        marker = {
            "manifest": {
                "daemon_id": "fixture-daemon-id",
                "project_name": controller.project_name,
                "project_directory": str(controller.project),
                "config_hashes": {"config": "original"},
                "effective_config_sha256": "original-effective-config",
            }
        }
        with self.assertRaisesRegex(maintenance.MaintenanceError, "drifted"):
            controller._validate_input_binding(marker)
        self.assertFalse(controller.events)

    def test_graceful_timeout_never_force_kills_or_removes(self) -> None:
        calls: list[tuple[str, ...]] = []
        alive = service_container("lab", "sha256:lab")

        def command(args: Any, timeout: float, check: bool = True) -> str:
            calls.append(tuple(args))
            if args[1:3] == ["inspect", "container-id"]:
                return json.dumps([{**alive, "Id": "container-id"}])
            return ""

        (self.root / "env").write_text("x=1", encoding="utf-8")
        (self.root / "compose.yaml").write_text("services: {}", encoding="utf-8")
        times = [0.0]

        def advancing_clock() -> float:
            times[0] += 1
            return times[0]

        (self.root / "env").write_text("x=1", encoding="utf-8")
        (self.root / "compose.yaml").write_text("services: {}", encoding="utf-8")
        controller = maintenance.Controller(
            project_directory=self.root,
            project_name="test-project",
            env_file=self.root / "env",
            compose_files=[self.root / "compose.yaml"],
            backup_dir=self.root / "backup",
            command=command,
            clock=advancing_clock,
        )
        with self.assertRaisesRegex(maintenance.MaintenanceError, "timed out"):
            controller._graceful_exit({**alive, "Id": "container-id"}, timeout=2)
        flat = [token for call in calls for token in call]
        self.assertIn("--restart=no", flat)
        self.assertIn("--signal=TERM", flat)
        self.assertNotIn("stop", flat)
        self.assertNotIn("--signal=KILL", flat)
        self.assertNotIn("rm", flat)

    def test_old_lab_without_label_is_probed_in_finite_isolated_container(self) -> None:
        calls: list[tuple[str, ...]] = []

        def command(args: Any, timeout: float, check: bool = True) -> str:
            calls.append(tuple(args))
            if args[:2] == ["docker", "create"]:
                return "probe-id"
            if args[:3] == ["docker", "start", "-a"]:
                return "not-a-version"
            if args[:3] == ["docker", "ps", "-aq"]:
                return "probe-id"
            if args[:3] == ["docker", "inspect", "probe-id"]:
                create_args = calls[0]
                name = create_args[create_args.index("--name") + 1]
                label = create_args[create_args.index("--label") + 1]
                key, value = label.split("=", 1)
                return json.dumps(
                    [
                        {
                            "Id": "probe-id",
                            "Name": f"/{name}",
                            "State": {"Running": False},
                            "Config": {"Labels": {key: value}},
                            "HostConfig": {"NetworkMode": "none"},
                            "Mounts": [{"Type": "tmpfs", "Destination": "/tmp"}],  # ruff: ignore[S108] - inspected probe fixture
                        }
                    ]
                )
            if args[:2] == ["docker", "rm"]:
                return ""
            return "not-a-version"

        (self.root / "env").write_text("x=1", encoding="utf-8")
        (self.root / "compose.yaml").write_text("services: {}", encoding="utf-8")
        controller = maintenance.Controller(
            project_directory=self.root,
            project_name="test-project",
            env_file=self.root / "env",
            compose_files=[self.root / "compose.yaml"],
            backup_dir=self.root / "backup",
            command=command,
        )
        with self.assertRaisesRegex(
            maintenance.MaintenanceError, "unknown or malformed"
        ):
            controller._probe_lab_image("sha256:unlabeled-old-lab")
        call = calls[0]
        self.assertIn("create", call)
        self.assertIn("--network", call)
        self.assertIn("none", call)
        self.assertIn("--read-only", call)
        self.assertNotIn("/var/run/docker.sock", call)
        self.assertNotIn(str(self.root / "data" / "hub"), call)
        self.assertTrue(any("rm" in call for call in calls))
        self.assertEqual(len(calls), 5)

    def test_duplicate_backup_destination_is_never_overwritten(self) -> None:
        controller = MigrationHarness(self.root)
        controller.backup.mkdir()
        original = controller.backup / "sentinel"
        original.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(maintenance.MaintenanceError, "already exists"):
            controller._validate_backup_location(must_be_new=True)
        self.assertEqual(original.read_text(encoding="utf-8"), "keep")

    def test_cold_copy_preserves_sqlite_sidecars_metadata_and_integrity(self) -> None:
        source = self.root / "state"
        source.mkdir()
        connection = sqlite3.connect(source / "jupyterhub.sqlite")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE identity (name TEXT)")
        connection.execute("INSERT INTO identity VALUES ('fixture-user')")
        connection.commit()
        wal = source / "jupyterhub.sqlite-wal"
        self.assertTrue(wal.exists())
        (source / "jupyterhub_cookie_secret").write_text("secret", encoding="utf-8")
        (source / "jupyterhub_cookie_secret").chmod(0o600)
        target = self.root / "cold-copy"
        records = maintenance._copy_tree_preserving(source, target)
        self.assertEqual(maintenance._inventory(source), records)
        self.assertTrue((target / "jupyterhub.sqlite-wal").is_file())
        self.assertEqual(
            stat.S_IMODE((source / "jupyterhub_cookie_secret").stat().st_mode),
            stat.S_IMODE((target / "jupyterhub_cookie_secret").stat().st_mode),
        )
        if os.name != "nt":
            self.assertEqual(
                (source / "jupyterhub_cookie_secret").stat().st_uid,
                (target / "jupyterhub_cookie_secret").stat().st_uid,
            )
        maintenance._sqlite_integrity(target)
        connection.close()

    def test_migration_order_and_dual_private_wrapper_modes(self) -> None:
        controller = MigrationHarness(self.root)
        pool = controller.host_paths["/srv/pool"]
        pool_files = {
            ".pool-id": "pool-fixture",
            ".projects": "alice:1001\n",
            ".projid-counter": "1002\n",
        }
        for name, contents in pool_files.items():
            (pool / name).write_text(contents, encoding="utf-8")
        home_marker = Path(str(controller.lab["home"])) / "keep.txt"
        home_marker.write_text("persistent home", encoding="utf-8")
        result = controller.migrate(self.acknowledgments())
        self.assertEqual(result["stage"], "candidate-private")
        stops = [event[1] for event in controller.events if event[0] == "graceful-exit"]
        self.assertEqual(stops[:3], ["web", "proxy", "hub"])
        self.assertLess(
            next(
                i
                for i, event in enumerate(controller.events)
                if event[0] == "backup-complete"
            ),
            next(
                i
                for i, event in enumerate(controller.events)
                if event[0] == "remove-labs"
            ),
        )
        starts = [event for event in controller.events if event[0] == "start"]
        self.assertEqual([(event[1]) for event in starts], ["proxy", "hub", "hub"])
        migration_env = starts[1][2]["hub"]["environment"]
        normal_env = starts[2][2]["hub"]["environment"]
        self.assertEqual(migration_env["JUPYTERHUB_MAINTENANCE_UPGRADE_DB"], "true")
        self.assertEqual(normal_env["JUPYTERHUB_MAINTENANCE_UPGRADE_DB"], "false")
        self.assertEqual(
            migration_env["JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS"], "true"
        )
        self.assertEqual(normal_env["JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS"], "true")
        self.assertFalse(
            any(
                event[0] == "start" and event[1] == "web" for event in controller.events
            )
        )
        self.assertTrue(controller.marker.exists())

        serialized = " ".join(
            controller.marker.read_text(encoding="utf-8") for _ in range(1)
        )
        self.assertNotIn("do-not-disclose", serialized)
        self.assertEqual(
            {name: (pool / name).read_text(encoding="utf-8") for name in pool_files},
            pool_files,
        )
        self.assertEqual(home_marker.read_text(encoding="utf-8"), "persistent home")
        docker_calls = [event for event in controller.events if event[0] == "docker"]
        for call in docker_calls:
            self.assertNotIn("-v", call)
            self.assertNotIn("--force", call)
            self.assertNotIn("--signal=KILL", call)

    def test_autoremove_lab_shutdown_reconciles_after_cold_backup(self) -> None:
        controller = MigrationHarness(self.root)
        lab = controller.containers[OWNED_LAB_ID]
        lab["HostConfig"]["AutoRemove"] = True
        controller.lab["auto_remove"] = True
        controller.lab["shape_sha256"] = controller._lab_shape_sha256(lab, "alice")
        home_marker = Path(str(controller.lab["home"])) / "keep.txt"
        home_marker.write_text("persistent home", encoding="utf-8")

        result = controller.migrate(self.acknowledgments())

        self.assertEqual(result["stage"], "candidate-private")
        self.assertNotIn(OWNED_LAB_ID, controller.containers)
        self.assertTrue(home_marker.is_file())
        self.assertTrue(
            (controller.backup / "snapshot" / "jupyterhub.sqlite").is_file()
        )
        stages = maintenance._read_json(controller.backup / "state.json")["stages"]
        stopped = next(
            item for item in stages if item["stage"] == "labs-gracefully-stopped"
        )
        self.assertEqual(stopped["verified_auto_removed"], 1)
        self.assertTrue(
            next(
                i
                for i, event in enumerate(controller.events)
                if event[0] == "graceful-exit" and event[1] == "/lab-alice"
            )
            < next(
                i
                for i, event in enumerate(controller.events)
                if event[0] == "backup-complete"
            )
        )
        self.assertFalse(any(event[0] == "remove-labs" for event in controller.events))

    def test_autoremove_stopped_then_removed_race_is_verified_by_full_id(self) -> None:
        controller = MigrationHarness(self.root)
        lab = controller.containers[OWNED_LAB_ID]
        lab["HostConfig"]["AutoRemove"] = True
        controller.lab["auto_remove"] = True
        controller.lab["shape_sha256"] = controller._lab_shape_sha256(lab, "alice")
        controller.auto_remove_delay_inspects = 1

        controller.migrate(self.acknowledgments())

        self.assertNotIn(OWNED_LAB_ID, controller.containers)
        self.assertTrue(
            any(
                event[0] == "command"
                and event[1][:2] == ("docker", "inspect")
                and event[1][-1] == OWNED_LAB_ID
                for event in controller.events
            )
        )
        self.assertFalse(any(event[0] == "remove-labs" for event in controller.events))

    def test_persistent_autoremove_container_times_out_with_interlock_retained(
        self,
    ) -> None:
        controller = MigrationHarness(self.root)
        lab = controller.containers[OWNED_LAB_ID]
        lab["HostConfig"]["AutoRemove"] = True
        controller.lab["auto_remove"] = True
        controller.lab["shape_sha256"] = controller._lab_shape_sha256(lab, "alice")
        controller.keep_auto_remove_container = True

        with self.assertRaisesRegex(
            maintenance.MaintenanceError, "did not disappear after TERM"
        ):
            controller.migrate(self.acknowledgments())

        self.assertTrue(controller.marker.is_file())
        self.assertIn(OWNED_LAB_ID, controller.containers)
        stages = maintenance._read_json(controller.backup / "state.json")["stages"]
        self.assertFalse(any(item["stage"] == "backup-complete" for item in stages))
        self.assertFalse(any(event[0] == "start" for event in controller.events))

    def test_lab_identity_inspection_failures_retain_interlock_before_term(
        self,
    ) -> None:
        for fault, expected in (
            ("wrong-id", "different container identity"),
            ("malformed", "invalid container inspection"),
            ("transport", "bounded external command failed"),
        ):
            with self.subTest(fault=fault):
                root = self.root / fault
                controller = MigrationHarness(root)
                controller.inspect_fault = fault
                controller.inspect_fault_at = 3
                with self.assertRaisesRegex(maintenance.MaintenanceError, expected):
                    controller.migrate(self.acknowledgments())
                self.assertTrue(controller.marker.is_file())
                self.assertTrue(controller.containers[OWNED_LAB_ID]["State"]["Running"])
                self.assertFalse(
                    any(
                        event[0] == "command"
                        and event[1][:2] == ("docker", "kill")
                        and event[1][-1] == OWNED_LAB_ID
                        for event in controller.events
                    )
                )
                stages = maintenance._read_json(controller.backup / "state.json")[
                    "stages"
                ]
                self.assertFalse(
                    any(item["stage"] == "backup-complete" for item in stages)
                )

    def test_unexpected_non_autoremove_disappearance_is_not_accepted(self) -> None:
        controller = MigrationHarness(self.root)
        controller.remove_non_auto_on_term = True

        with self.assertRaises(maintenance.MaintenanceError):
            controller.migrate(self.acknowledgments())

        self.assertTrue(controller.marker.is_file())
        stages = maintenance._read_json(controller.backup / "state.json")["stages"]
        self.assertFalse(any(item["stage"] == "backup-complete" for item in stages))
        self.assertFalse(any(event[0] == "start" for event in controller.events))

    def test_unrelated_or_changed_lab_inventory_fails_post_backup_reconciliation(
        self,
    ) -> None:
        for mutation in ("new-lab", "mount", "home", "image"):
            with self.subTest(mutation=mutation):
                controller = MigrationHarness(self.root / mutation)
                lab = controller.containers[OWNED_LAB_ID]
                lab["HostConfig"]["AutoRemove"] = True
                controller.lab["auto_remove"] = True
                controller.lab["shape_sha256"] = controller._lab_shape_sha256(
                    lab, "alice"
                )
                controller.inventory_mutation = mutation
                with self.assertRaises(maintenance.MaintenanceError):
                    controller.migrate(self.acknowledgments())
                self.assertTrue(controller.marker.is_file())
                stages = maintenance._read_json(controller.backup / "state.json")[
                    "stages"
                ]
                self.assertTrue(
                    any(item["stage"] == "backup-complete" for item in stages)
                )
                self.assertFalse(
                    any(event[0] == "start" for event in controller.events)
                )

    def test_migration_failure_phases_are_set_at_each_boundary(self) -> None:
        controller = MigrationHarness(self.root)
        controller.migrate(self.acknowledgments())
        stages = {
            stage: phase
            for kind, stage, phase in controller.phase_events
            if kind == "stage"
        }
        self.assertEqual(stages["interlock-written"], "interlock")
        self.assertEqual(stages["web-stopped"], "quiesce-services")
        self.assertEqual(stages["labs-gracefully-stopped"], "drain-labs")
        self.assertEqual(stages["backup-complete"], "cold-backup")
        self.assertEqual(stages["owned-labs-removed"], "reconcile-labs")
        self.assertEqual(stages["proxy-private"], "start-private-proxy")
        self.assertEqual(stages["migration-start-ready"], "migration-readiness")
        self.assertEqual(stages["candidate-private"], "candidate-readiness")
        starts = [event for event in controller.phase_events if event[0] == "start"]
        self.assertEqual(
            [(event[1], event[2]) for event in starts],
            [
                ("proxy", "start-private-proxy"),
                ("hub", "start-migration-hub"),
                ("hub", "start-candidate-hub"),
            ],
        )
        self.assertTrue(
            any(
                kind == "command"
                and phase == "stop-migration-hub"
                and operation == "restart-disable"
                for kind, phase, operation in controller.phase_events
            )
        )
        self.assertTrue(
            any(
                kind == "command"
                and phase == "reconcile-labs"
                and operation == "container-list"
                for kind, phase, operation in controller.phase_events
            )
        )

    def test_typed_external_failures_capture_the_active_migration_phase(self) -> None:
        cases = (
            ("quiesce-services", "container-inspect", 1),
            ("drain-labs", "container-list", 1),
            ("reconcile-labs", "container-list", 3),
            ("start-private-proxy", "compose-up-proxy", 1),
            ("start-migration-hub", "compose-up-hub", 1),
            ("stop-migration-hub", "restart-disable", 5),
            ("start-candidate-hub", "compose-up-hub", 2),
        )
        for phase, operation, occurrence in cases:
            with self.subTest(phase=phase):
                controller = MigrationHarness(self.root / phase)
                controller.fail_command_phase = phase
                controller.fail_command_operation = operation
                controller.fail_command_occurrence = occurrence
                with self.assertRaises(maintenance.ExternalCommandFailure) as raised:
                    controller.migrate(self.acknowledgments())
                self.assertEqual(raised.exception.failure_phase, phase)
                self.assertTrue(controller.marker.is_file())
                self.assertEqual(controller.failure_phase, phase)

    def test_interlock_backup_and_readiness_failures_keep_primary_phase(self) -> None:
        interlock = MigrationHarness(self.root / "interlock")
        write_stage = interlock._write_stage

        def fail_after_interlock(
            backup_path: Path, stage: str, facts: Any = None
        ) -> None:
            write_stage(backup_path, stage, facts)
            if stage == "interlock-written":
                raise maintenance.MaintenanceError("fixed fixture failure")

        with patch.object(interlock, "_write_stage", side_effect=fail_after_interlock):
            with self.assertRaises(maintenance.MaintenanceError):
                interlock.migrate(self.acknowledgments())
        self.assertEqual(interlock.failure_phase, "interlock")
        self.assertTrue(interlock.marker.is_file())

        backup = MigrationHarness(self.root / "cold-backup", bad_database=True)
        with self.assertRaisesRegex(maintenance.MaintenanceError, "integrity"):
            backup.migrate(self.acknowledgments())
        self.assertEqual(backup.failure_phase, "cold-backup")
        self.assertTrue(backup.marker.is_file())

        for readiness_call, phase in (
            (1, "migration-readiness"),
            (2, "candidate-readiness"),
        ):
            with self.subTest(readiness_phase=phase):
                controller = MigrationHarness(self.root / phase)
                controller.fail_readiness_at = readiness_call
                with self.assertRaisesRegex(
                    maintenance.MaintenanceError, "private Hub readiness"
                ):
                    controller.migrate(self.acknowledgments())
                self.assertEqual(controller.failure_phase, phase)
                self.assertTrue(controller.marker.is_file())

    def test_backup_integrity_failure_prevents_lab_removal_and_candidate_start(
        self,
    ) -> None:
        controller = MigrationHarness(self.root, bad_database=True)
        with self.assertRaisesRegex(maintenance.MaintenanceError, "integrity"):
            controller.migrate(self.acknowledgments())
        self.assertTrue(controller.marker.exists())
        self.assertFalse(any(event[0] == "remove-labs" for event in controller.events))
        self.assertFalse(any(event[0] == "start" for event in controller.events))
        state = maintenance._read_json(controller.backup / "state.json")
        self.assertFalse(
            any(item["stage"] == "backup-complete" for item in state["stages"])
        )

    def test_late_fault_keeps_web_stopped_and_marker_present(self) -> None:
        controller = MigrationHarness(self.root)
        controller.fail_readiness_at = 2
        with self.assertRaisesRegex(
            maintenance.MaintenanceError, "private Hub readiness"
        ):
            controller.migrate(self.acknowledgments())
        self.assertFalse(controller.containers["web-container"]["State"]["Running"])
        self.assertTrue(controller.marker.exists())
        self.assertFalse(
            any(
                event[0] == "start" and event[1] == "web" for event in controller.events
            )
        )

    def test_missing_acceptance_ack_cannot_restart_public_services(self) -> None:
        controller = MigrationHarness(self.root)
        with self.assertRaisesRegex(maintenance.MaintenanceError, "acceptance-passed"):
            controller.accept(self.acknowledgments(acceptance_passed=False))
        self.assertEqual(controller.events, [])

    def test_incomplete_backup_refuses_restore_before_any_service_action(self) -> None:
        controller = RestoreHarness(self.root)
        maintenance._atomic_json(controller.backup / "state.json", {"stages": []})
        with self.assertRaisesRegex(
            maintenance.MaintenanceError, "incomplete cold backup"
        ):
            controller.restore(self.acknowledgments())
        self.assertEqual(controller.events, [])
        self.assertTrue(controller.data.is_dir())

    def test_restore_repeats_from_immutable_cold_snapshot_and_keeps_failed_states(
        self,
    ) -> None:
        controller = RestoreHarness(self.root)
        self.assertTrue(controller.assert_has_wal)
        original_snapshot_db = maintenance._sha256_file(
            controller.backup_snapshot / "jupyterhub.sqlite"
        )
        original_snapshot_inventory = maintenance._inventory(controller.backup_snapshot)
        (controller.data / "candidate-only-state").write_text(
            "failed", encoding="utf-8"
        )
        result1 = controller.restore(self.acknowledgments())
        self.assertEqual(result1["stage"], "restored-private")
        self.assertTrue(controller.marker.exists())
        self.assertFalse((controller.data / "candidate-only-state").exists())
        self.assertEqual(
            (controller.data / "jupyterhub_cookie_secret").read_text(encoding="utf-8"),
            "old-cookie",
        )
        connection = sqlite3.connect(
            (controller.data / "jupyterhub.sqlite").resolve().as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            self.assertEqual(
                connection.execute("SELECT value FROM identity").fetchone(),
                ("old-state",),
            )
        finally:
            connection.close()
        failed1 = Path(result1["failed_state"])
        self.assertTrue((failed1 / "candidate-only-state").is_file())

        result2 = controller.restore(self.acknowledgments())
        self.assertEqual(result2["stage"], "restored-private")
        self.assertNotEqual(result1["failed_state"], result2["failed_state"])
        self.assertEqual(
            maintenance._sha256_file(controller.backup_snapshot / "jupyterhub.sqlite"),
            original_snapshot_db,
        )
        self.assertEqual(
            maintenance._inventory(controller.backup_snapshot),
            original_snapshot_inventory,
        )
        self.assertTrue((controller.data / "jupyterhub.sqlite-wal").exists())
        state = maintenance._read_json(controller.backup / "state.json")
        self.assertEqual(
            len(
                [
                    item
                    for item in state["stages"]
                    if item["stage"] == "failed-state-preserved"
                ]
            ),
            2,
        )

    def test_accept_resumes_only_after_flag_and_keeps_old_override_migration_off(
        self,
    ) -> None:
        controller = RestoreHarness(self.root)
        controller._write_stage(
            controller.backup, "restored-private", {"cullers_suppressed": True}
        )
        result = controller.accept(self.acknowledgments(acceptance_passed=True))
        self.assertEqual(result["stage"], "accepted")
        self.assertEqual(
            Path(result["runtime_override"]).name,
            "activation-restored-v1.override.json",
        )
        self.assertFalse(result["timer_resumed"])
        starts = [event for event in controller.events if event[0] == "start"]
        self.assertEqual([event[1] for event in starts[-3:]], ["proxy", "hub", "web"])
        runtime = json.loads(
            Path(result["runtime_override"]).read_text(encoding="utf-8")
        )
        hub = runtime["services"]["hub"]
        self.assertEqual(
            hub["environment"]["JUPYTERHUB_MAINTENANCE_UPGRADE_DB"], "false"
        )
        self.assertEqual(
            hub["environment"]["JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS"], "false"
        )
        self.assertIn("command", hub)
        self.assertTrue(controller.marker.exists())

    def test_candidate_and_restore_activation_overrides_are_separately_reusable(
        self,
    ) -> None:
        controller = RestoreHarness(self.root)
        candidate = controller._write_runtime_override(
            controller.backup, restored=False
        )
        self.assertEqual(
            candidate,
            controller._write_runtime_override(controller.backup, restored=False),
        )
        restored = controller._write_runtime_override(controller.backup, restored=True)
        self.assertNotEqual(candidate, restored)
        self.assertEqual(candidate.name, "activation-candidate-v1.override.json")
        self.assertEqual(restored.name, "activation-restored-v1.override.json")
        self.assertTrue((controller.backup / "snapshot").is_dir())

    def test_accept_rejects_private_hub_with_database_upgrade_enabled(self) -> None:
        controller = RestoreHarness(self.root)
        controller._write_stage(
            controller.backup, "restored-private", {"cullers_suppressed": True}
        )
        controller.current_services["hub"]["Config"]["Env"] = [
            "JUPYTERHUB_ALLOW_DB_UPGRADE=true",
            "JUPYTERHUB_MAINTENANCE_UPGRADE_DB=true",
            "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS=true",
        ]
        with self.assertRaisesRegex(
            maintenance.MaintenanceError, "explicitly disable database upgrades"
        ):
            controller.accept(self.acknowledgments())
        self.assertFalse(any(event[0] == "start" for event in controller.events))

    def test_wrapper_overrides_old_and_candidate_configuration_shapes(self) -> None:
        wrapper = Path(maintenance.__file__).with_name(
            "jupyterhub_maintenance_config.py"
        )
        source = wrapper.read_text(encoding="utf-8")
        base_config = """
c = get_config()
c.JupyterHub.upgrade_db = True
c.JupyterHub.services = [
    {"name": "idle-culler-servers"}, {"name": "idle-culler-users"},
    {"name": "other-service"},
]
c.JupyterHub.load_roles = [
    {"name": "idle-culler-servers"}, {"name": "idle-culler-users"},
    {"name": "other-service"},
]
"""
        for upgrade, suppress, expected_upgrade, expected_services in (
            ("true", "true", True, ["other-service"]),
            ("false", "true", False, ["other-service"]),
            (
                "false",
                "false",
                False,
                ["idle-culler-servers", "idle-culler-users", "other-service"],
            ),
        ):
            config = SimpleNamespace(JupyterHub=SimpleNamespace())
            namespace = {"get_config": lambda: config}
            real_open = Path.open

            def controlled_open(path: Path, *args: Any, **kwargs: Any) -> Any:
                if str(path).replace("\\", "/") == "/app/jupyterhub_config.py":
                    from io import StringIO

                    return StringIO(base_config)
                return real_open(path, *args, **kwargs)

            with (
                patch.dict(
                    os.environ,
                    {
                        "JUPYTERHUB_MAINTENANCE_UPGRADE_DB": upgrade,
                        "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS": suppress,
                    },
                    clear=False,
                ),
                patch.object(Path, "open", controlled_open),
            ):
                exec(compile(source, str(wrapper), "exec"), namespace)  # noqa: S102
            self.assertIs(config.JupyterHub.upgrade_db, expected_upgrade)
            names = [item["name"] for item in config.JupyterHub.services]
            self.assertEqual(names, expected_services)
            role_names = [item["name"] for item in config.JupyterHub.load_roles]
            if suppress == "true":
                self.assertEqual(role_names, ["other-service"])
            else:
                self.assertEqual(
                    role_names,
                    ["idle-culler-servers", "idle-culler-users", "other-service"],
                )

    def test_updater_interlock_check_is_before_any_pull(self) -> None:
        script = (Path(__file__).parents[1] / "update.sh").read_text(encoding="utf-8")
        marker_check = script.index('if [ -e "$DIR/.jupyterhub-maintenance.json" ]')
        first_pull = script.index("compose_pull --profile images pull")
        self.assertLess(marker_check, first_pull)


if __name__ == "__main__":
    unittest.main()
