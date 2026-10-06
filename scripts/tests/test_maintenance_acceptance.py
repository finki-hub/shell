from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts import jupyterhub_maintenance as maintenance


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.web_start_fault = False
        self.web_start_calls = 0

    def arm_web_start_fault(self) -> None:
        self.web_start_fault = True
        self.web_start_calls = 0

    def __call__(self) -> float:
        self.now += 0.1
        if self.web_start_fault:
            self.web_start_calls += 1
            if self.web_start_calls >= 3:
                self.now += 200
        return self.now


class AcceptanceDocker:
    """Command-runner-only Docker fixture backed by realistic inspect JSON."""

    def __init__(self, project: Path, config: dict[str, Any], backup: Path) -> None:
        self.project = project
        self.config = config
        self.backup = backup
        self.events: list[tuple[str, ...]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.service_ids: dict[str, str] = {}
        self.image_ids = {
            "sha256:web",
            "sha256:proxy",
            "sha256:old-hub",
            "sha256:old-lab",
            "sha256:candidate-hub",
            "sha256:candidate-lab",
        }
        self.counter = 0
        self.web_health = "healthy"
        self.term_stuck_service: str | None = None
        self.fail_state_write_on_web_activation = False
        self.clock: FakeClock | None = None

    def add_service(
        self,
        service: str,
        image: str,
        *,
        running: bool,
        private: bool = False,
        env: dict[str, str] | None = None,
        health: str = "healthy",
    ) -> dict[str, Any]:
        self.counter += 1
        identifier = f"{service}-{self.counter}"
        labels = {
            "com.docker.compose.project": "acceptance-project",
            "com.docker.compose.service": service,
        }
        mounts: list[dict[str, Any]] = []
        if service == "hub":
            mounts.extend(
                {
                    "Type": "bind",
                    "Source": volume["source"],
                    "Destination": volume["target"],
                    "RW": not volume.get("read_only", False),
                }
                for volume in self.config["services"]["hub"]["volumes"]
            )
            if private:
                override = self._override_wrapper()
                mounts.append(
                    {
                        "Type": "bind",
                        "Source": str(override),
                        "Destination": "/srv/maintenance/jupyterhub-maintenance-config.py",
                        "RW": False,
                    }
                )
        command = self.config["services"][service].get("command") or []
        config: dict[str, Any] = {
            "Cmd": [
                "jupyterhub",
                "-f",
                "/srv/maintenance/jupyterhub-maintenance-config.py",
            ]
            if private
            else command,
            "Labels": labels,
            "Env": [f"{key}={value}" for key, value in (env or {}).items()],
        }
        return {
            "Id": identifier,
            "Image": image,
            "Name": f"/{self.project.name}-{service}-1",
            "Config": config,
            "State": {
                "Running": running,
                "Status": "running" if running else "exited",
                "Health": {"Status": health},
            },
            "HostConfig": {
                "NetworkMode": "host",
                "Privileged": service == "hub",
                "IpcMode": "private" if service == "hub" else "",
                "RestartPolicy": {"Name": "no"},
            },
            "Mounts": mounts,
        }

    def _override_wrapper(self) -> Path:
        return self.backup / "configuration" / "jupyterhub_maintenance_config.py"

    def register(self, service: str, item: dict[str, Any]) -> None:
        prior = self.service_ids.get(service)
        if prior is not None and prior != item["Id"]:
            self.containers.pop(prior, None)
        self.containers[item["Id"]] = item
        self.service_ids[service] = item["Id"]

    def command(self, argv: Any, _timeout: float, _check: bool = True) -> str:
        args = tuple(argv)
        self.events.append(args)
        if args[:3] == ("docker", "compose", "--project-directory"):
            if args[-3:-1] == ("ps", "-aq"):
                return self.service_ids.get(args[-1], "")
            if args[-3:] == ("-d", "--no-deps", args[-1]):
                service = args[-1]
                override_files = [
                    Path(args[index + 1])
                    for index, value in enumerate(args[:-1])
                    if value == "-f"
                ]
                override = (
                    json.loads(override_files[-1].read_text(encoding="utf-8"))
                    if len(override_files) > 1
                    else {"services": {}}
                )
                service_override = override.get("services", {}).get(service, {})
                image = service_override.get(
                    "image", self.config["services"][service]["image"]
                )
                env = dict(self.config["services"][service].get("environment", {}))
                env.update(service_override.get("environment", {}))
                private = "command" in service_override
                health = self.web_health if service == "web" else "healthy"
                item = self.add_service(
                    service,
                    image,
                    running=True,
                    private=private,
                    env=env,
                    health=health,
                )
                if service == "web" and self.clock is not None:
                    if self.web_health != "healthy":
                        self.clock.arm_web_start_fault()
                    if self.fail_state_write_on_web_activation:
                        state_path = self.backup / "state.json"
                        state_path.unlink()
                        state_path.mkdir()
                self.register(service, item)
                return ""
            if args[-3:] == ("config", "--format", "json"):
                return json.dumps(self.config)
        if args[:2] == ("docker", "info"):
            return "fixture-daemon-id"
        if args[:2] == ("docker", "image") and args[2:3] == ("inspect",):
            image_id = args[-1]
            return json.dumps(
                [
                    {
                        "Id": image_id,
                        "Config": {"Entrypoint": None, "Cmd": [], "Labels": {}},
                    }
                ]
            )
        if args[:2] == ("docker", "inspect"):
            inspected = [self.containers[item] for item in args[2:]]
            return json.dumps(inspected)
        if args[:3] == ("docker", "ps", "-aq"):
            return "\n".join(self.containers)
        if args[:2] == ("docker", "update"):
            return ""
        if args[:2] == ("docker", "kill"):
            identifier = args[-1]
            item = self.containers[identifier]
            service = (
                (item.get("Config") or {})
                .get("Labels", {})
                .get("com.docker.compose.service")
            )
            if service != self.term_stuck_service:
                item["State"]["Running"] = False
                item["State"]["Status"] = "exited"
                if service == "web" and self.clock is not None:
                    self.clock.web_start_fault = False
            return ""
        if args[:2] == ("docker", "rm"):
            identifier = args[-1]
            item = self.containers.pop(identifier)
            service = item["Config"]["Labels"]["com.docker.compose.service"]
            if self.service_ids.get(service) == identifier:
                self.service_ids.pop(service)
            return ""
        raise AssertionError(f"unexpected Docker command: {args!r}")


class AcceptanceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.data = self.root / "hub-data"
        self.data.mkdir()
        self.pool = self.root / "pool"
        self.pool.mkdir()
        self.home = self.pool / "users" / "alice"
        self.home.mkdir(parents=True)
        (self.home / "keep.txt").write_text("user home", encoding="utf-8")
        (self.pool / ".pool-id").write_text("fixture-pool", encoding="utf-8")
        (self.pool / ".projects").write_text("alice:1001\n", encoding="utf-8")
        (self.pool / ".projid-counter").write_text("1002\n", encoding="utf-8")
        self.pool_before = self._pool_inventory()
        self.backup_parent = self.root / "backups"
        self.backup_parent.mkdir()
        self.backup = self.backup_parent / "upgrade"
        self.backup.mkdir(mode=0o700)
        configuration = self.backup / "configuration"
        configuration.mkdir(mode=0o700)
        self.wrapper = configuration / "jupyterhub_maintenance_config.py"
        source_wrapper = Path(maintenance.__file__).with_name(
            "jupyterhub_maintenance_config.py"
        )
        shutil.copy2(source_wrapper, self.wrapper)
        self.wrapper.chmod(0o600)
        self.env_file = self.root / "environment.env"
        self.env_file.write_text("FIXTURE=only\n", encoding="utf-8")
        self.compose_file = self.root / "compose.yaml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")
        self.config = self._compose_config()
        self.clock = FakeClock()
        self.docker = AcceptanceDocker(self.project, self.config, self.backup)
        self.docker.clock = self.clock
        self._install_private_candidate()
        self.controller = maintenance.Controller(
            project_directory=self.project,
            project_name="acceptance-project",
            env_file=self.env_file,
            compose_files=[self.compose_file],
            backup_dir=self.backup,
            command=self.docker.command,
            clock=self.clock,
            http_get=lambda _url, _timeout: (200, b'{"ready": true}'),
        )
        self._write_valid_protected_backup()

    def _compose_config(self) -> dict[str, Any]:
        return {
            "name": "acceptance-project",
            "networks": {"users": {"external": True, "name": "finki-hub-shell-users"}},
            "services": {
                "web": {
                    "image": "sha256:web",
                    "network_mode": "host",
                    "command": [],
                },
                "proxy": {
                    "image": "sha256:proxy",
                    "network_mode": "host",
                    "command": [
                        "--ip",
                        "127.0.0.1",
                        "--port",
                        "8000",
                        "--api-ip",
                        "127.0.0.1",
                        "--api-port",
                        "8001",
                    ],
                },
                "hub": {
                    "image": "sha256:old-hub",
                    "network_mode": "host",
                    "privileged": True,
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(self.data),
                            "target": "/srv/hub",
                        },
                        {
                            "type": "bind",
                            "source": str(self.pool),
                            "target": "/srv/pool",
                        },
                        {
                            "type": "bind",
                            "source": "/var/run/docker.sock",
                            "target": "/var/run/docker.sock",
                        },
                    ],
                    "environment": {"LAB_IMAGE": "sha256:old-lab"},
                },
                "lab": {
                    "image": "sha256:old-lab",
                    "networks": {"users": {}},
                },
            },
        }

    def _install_private_candidate(self) -> None:
        self.docker.register(
            "web",
            self.docker.add_service("web", "sha256:web", running=False),
        )
        self.docker.register(
            "proxy",
            self.docker.add_service("proxy", "sha256:proxy", running=True),
        )
        self.docker.register(
            "hub",
            self.docker.add_service(
                "hub",
                "sha256:candidate-hub",
                running=True,
                private=True,
                env={
                    "JUPYTERHUB_ALLOW_DB_UPGRADE": "false",
                    "JUPYTERHUB_MAINTENANCE_UPGRADE_DB": "false",
                    "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS": "true",
                    "LAB_IMAGE": "sha256:candidate-lab",
                },
            ),
        )

    def _write_valid_protected_backup(self) -> None:
        source = self.root / "initial-state"
        source.mkdir()
        connection = sqlite3.connect(source / "jupyterhub.sqlite")
        try:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES ('before-upgrade')")
            connection.commit()
        finally:
            connection.close()
        (source / "jupyterhub_cookie_secret").write_text(
            "fixture-secret", encoding="utf-8"
        )
        entries = maintenance._copy_tree_preserving(source, self.backup / "snapshot")
        maintenance._atomic_json(
            self.backup / "snapshot-inventory.json", {"entries": entries}
        )
        self.controller._load_config()
        self.controller._daemon()
        manifest = {
            "daemon_id": self.controller.daemon_id,
            "project_name": self.controller.project_name,
            "project_directory": str(self.project.resolve()),
            "compose_paths": [
                str(self.compose_file.resolve()),
                str(self.env_file.resolve()),
            ],
            "config_hashes": self.controller._config_hashes(),
            "effective_config_sha256": maintenance._sha256_json(self.controller.config),
            "hub_data": str(self.data.resolve()),
            "pool": str(self.pool.resolve()),
            "services": {
                "web": {
                    "container_id": self.docker.service_ids["web"],
                    "image_id": "sha256:web",
                    "restart_policy": "no",
                },
                "proxy": {
                    "container_id": self.docker.service_ids["proxy"],
                    "image_id": "sha256:proxy",
                    "restart_policy": "no",
                },
                "hub": {
                    "container_id": "hub-before-upgrade",
                    "image_id": "sha256:old-hub",
                    "restart_policy": "no",
                },
            },
            "old_hub_version": "5.5.1",
            "old_hub_image_id": "sha256:old-hub",
            "old_lab_image_id": "sha256:old-lab",
            "candidate_hub_image_id": "sha256:candidate-hub",
            "candidate_lab_image_id": "sha256:candidate-lab",
            "old_lab_user": "ubuntu",
        }
        manifest["config_wrapper_sha256"] = maintenance._sha256_file(self.wrapper)
        maintenance._atomic_json(self.backup / "manifest.json", manifest)
        self.backup.joinpath("manifest.json").chmod(0o600)
        maintenance._atomic_json(
            self.backup / "state.json",
            {"stages": [{"stage": "backup-complete"}, {"stage": "candidate-private"}]},
        )
        self.controller.manifest = manifest
        self.controller._write_marker(manifest, self.backup)

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

    def snapshot_digest(self) -> tuple[str, list[dict[str, Any]]]:
        snapshot = self.backup / "snapshot"
        return (
            maintenance._sha256_file(snapshot / "jupyterhub.sqlite"),
            maintenance._inventory(snapshot),
        )

    def test_failed_candidate_accept_restore_then_old_pair_accept(self) -> None:
        cold_before = self.snapshot_digest()
        self.docker.web_health = "unhealthy"
        with self.assertRaisesRegex(
            maintenance.MaintenanceError,
            "public web was re-fenced.*retryable private Hub state",
        ):
            self.controller.accept(self.acknowledgments())
        self.assertFalse(
            self.docker.containers[self.docker.service_ids["web"]]["State"]["Running"]
        )
        fallback = self.docker.containers[self.docker.service_ids["hub"]]
        self.assertEqual(fallback["Image"], "sha256:candidate-hub")
        self.assertIn(
            "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS=true",
            fallback["Config"]["Env"],
        )
        candidate_override = self.backup / "activation-candidate-v1.override.json"
        candidate_bytes = candidate_override.read_bytes()
        self.assertTrue(self.controller.marker.exists())
        self.assert_web_term_fenced()

        self.docker.web_health = "healthy"
        retry = self.controller.accept(self.acknowledgments())
        self.assertEqual(Path(retry["runtime_override"]).name, candidate_override.name)
        self.assertEqual(candidate_override.read_bytes(), candidate_bytes)
        self.assertTrue(self.controller.marker.exists())

        restored = self.controller.restore(self.acknowledgments())
        self.assertEqual(restored["stage"], "restored-private")
        self.assertTrue(self.controller.marker.exists())
        restored_private = self.docker.containers[self.docker.service_ids["hub"]]
        self.assertEqual(restored_private["Image"], "sha256:old-hub")
        self.assertIn(
            "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS=true",
            restored_private["Config"]["Env"],
        )
        self.assertFalse(
            self.docker.containers[self.docker.service_ids["web"]]["State"]["Running"]
        )
        self.docker.web_health = "healthy"
        result = self.controller.accept(self.acknowledgments())
        self.assertEqual(result["stage"], "accepted")
        self.assertEqual(
            Path(result["runtime_override"]).name,
            "activation-restored-v1.override.json",
        )
        self.assertEqual(candidate_override.read_bytes(), candidate_bytes)
        accepted_hub = self.docker.containers[self.docker.service_ids["hub"]]
        self.assertEqual(accepted_hub["Image"], "sha256:old-hub")
        self.assertIn(
            "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS=false",
            accepted_hub["Config"]["Env"],
        )
        self.assertTrue(
            self.docker.containers[self.docker.service_ids["web"]]["State"]["Running"]
        )
        self.assertTrue(self.controller.marker.exists())
        self.assertEqual(self.snapshot_digest(), cold_before)
        self.assertTrue(
            any(
                args[:3] == ("docker", "kill", "--signal=TERM")
                for args in self.docker.events
            )
        )
        self.assert_no_forced_or_volume_deletion()

    def test_missing_acceptance_acknowledgment_does_not_activate_services(self) -> None:
        with self.assertRaisesRegex(maintenance.MaintenanceError, "acceptance-passed"):
            self.controller.accept(self.acknowledgments(acceptance_passed=False))
        self.assertEqual(self.docker.service_ids["web"], "web-1")
        self.assertFalse(self.docker.containers["web-1"]["State"]["Running"])
        self.assertFalse(any("up" in args for args in self.docker.events))

    def test_state_write_failure_after_web_start_refences_and_keeps_marker(
        self,
    ) -> None:
        cold_before = self.snapshot_digest()
        self.docker.fail_state_write_on_web_activation = True
        with self.assertRaisesRegex(
            maintenance.MaintenanceError,
            "public web is confirmed re-fenced.*private Hub recovery could not be confirmed",
        ):
            self.controller.accept(self.acknowledgments())
        web = self.docker.containers[self.docker.service_ids["web"]]
        self.assertFalse(web["State"]["Running"])
        hub = self.docker.containers[self.docker.service_ids["hub"]]
        self.assertIn(
            "JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS=true", hub["Config"]["Env"]
        )
        self.assertTrue(self.controller.marker.exists())
        self.assertEqual(self.snapshot_digest(), cold_before)
        self.assert_web_term_fenced()
        self.assert_no_forced_or_volume_deletion()

    def test_refence_timeout_reports_ingress_closure_unconfirmed(self) -> None:
        cold_before = self.snapshot_digest()
        self.docker.web_health = "unhealthy"
        self.docker.term_stuck_service = "web"
        with self.assertRaisesRegex(
            maintenance.MaintenanceError,
            "public ingress closure could not be confirmed.*operator intervention",
        ):
            self.controller.accept(self.acknowledgments())
        web = self.docker.containers[self.docker.service_ids["web"]]
        self.assertTrue(web["State"]["Running"])
        self.assertTrue(self.controller.marker.exists())
        self.assertEqual(self.snapshot_digest(), cold_before)
        self.assertTrue(
            any(
                args[:3] == ("docker", "kill", "--signal=TERM")
                for args in self.docker.events
            )
        )
        self.assert_no_forced_or_volume_deletion()
        self.assertFalse(
            any(
                "--signal=KILL" in args or "--force" in args
                for args in self.docker.events
            )
        )

    def assert_no_forced_or_volume_deletion(self) -> None:
        self.assertFalse(
            any(
                "--signal=KILL" in args or "--force" in args or "-v" in args
                for args in self.docker.events
            )
        )
        self.assertEqual(self._pool_inventory(), self.pool_before)

    def assert_web_term_fenced(self) -> None:
        self.assertTrue(
            any(
                args[:3] == ("docker", "kill", "--signal=TERM")
                for args in self.docker.events
            )
        )
        web = self.docker.containers[self.docker.service_ids["web"]]
        self.assertFalse(web["State"]["Running"])

    def _pool_inventory(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.pool)): path.read_bytes()
            for path in self.pool.rglob("*")
            if path.is_file()
        }


if __name__ == "__main__":
    unittest.main()
