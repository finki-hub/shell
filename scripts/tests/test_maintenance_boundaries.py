from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts import jupyterhub_maintenance as maintenance


class MaintenanceBoundaryTests(unittest.TestCase):
    def make_controller(
        self,
        root: Path,
        *,
        container: dict[str, Any] | None = None,
        http_get: Any = None,
        wrapper_rw: bool = False,
    ) -> maintenance.Controller:
        project = root / "project"
        project.mkdir()
        env_file = root / "environment.env"
        env_file.write_text("TOKEN=fixture\n", encoding="utf-8")
        compose_file = root / "compose.yaml"
        compose_file.write_text("services: {}\n", encoding="utf-8")
        hub = root / "hub-data"
        pool = root / "pool"
        hub.mkdir()
        pool.mkdir()
        backup = root / "backup"
        backup.mkdir()
        wrapper = backup / "configuration" / "jupyterhub_maintenance_config.py"
        wrapper.parent.mkdir()
        wrapper.write_text("# fixture\n", encoding="utf-8")
        services = {
            "hub": {
                "volumes": [
                    {"source": str(hub), "target": "/srv/hub"},
                    {"source": str(pool), "target": "/srv/pool"},
                    {
                        "source": "/var/run/docker.sock",
                        "target": "/var/run/docker.sock",
                    },
                ]
            }
        }
        inspected = container or {
            "Id": "hub-container",
            "Image": "sha256:hub",
            "Name": "/test-project-hub-1",
            "Config": {
                "Cmd": [
                    "jupyterhub",
                    "-f",
                    "/srv/maintenance/jupyterhub-maintenance-config.py",
                ],
                "Labels": {
                    "com.docker.compose.project": "test-project",
                    "com.docker.compose.service": "hub",
                },
            },
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "HostConfig": {
                "NetworkMode": "host",
                "Privileged": True,
                "IpcMode": "private",
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(hub),
                    "Destination": "/srv/hub",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": str(pool),
                    "Destination": "/srv/pool",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": "/var/run/docker.sock",
                    "Destination": "/var/run/docker.sock",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": str(wrapper),
                    "Destination": "/srv/maintenance/jupyterhub-maintenance-config.py",
                    "RW": wrapper_rw,
                },
            ],
        }
        events: list[tuple[str, ...]] = []

        def command(args: Any, timeout: float, check: bool = True) -> str:
            events.append(tuple(args))
            if args[:3] == ["docker", "compose", "--project-directory"]:
                if args[-3:] == ["ps", "-aq", "hub"]:
                    return "hub-container"
                if args[-3:] == ["-d", "--no-deps", "hub"]:
                    return ""
            if args[:2] == ["docker", "inspect"]:
                return json.dumps([inspected])
            if args[:3] == ["docker", "image", "inspect"]:
                return json.dumps(
                    [{"Id": "sha256:hub", "Config": {"Entrypoint": None, "Cmd": []}}]
                )
            raise AssertionError(f"unexpected bounded command: {args!r}")

        controller = maintenance.Controller(
            project_directory=project,
            project_name="test-project",
            env_file=env_file,
            compose_files=[compose_file],
            backup_dir=backup,
            command=command,
            http_get=http_get,
        )
        controller.config = {"services": services}
        controller.host_paths = {
            "/srv/hub": hub,
            "/srv/pool": pool,
            "/var/run/docker.sock": Path("/var/run/docker.sock").resolve(),
        }
        controller.boundary_events = events  # type: ignore[attr-defined]
        return controller

    def test_private_hub_start_uses_real_stage_aware_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = self.make_controller(Path(directory))
            override = controller.backup / "private.json"
            override.write_text("{}", encoding="utf-8")
            controller._start_service("hub", override=override)
            self.assertIn(
                "up",
                [word for call in controller.boundary_events for word in call],  # type: ignore[attr-defined]
            )

    def test_private_mount_validation_error_survives_real_start_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self.make_controller(root, wrapper_rw=True)
            with self.assertRaisesRegex(maintenance.MaintenanceError, "read-only file"):
                controller._start_service(
                    "hub", override=controller.backup / "private.json"
                )

    def test_readiness_parses_handler_json_and_rejects_invalid_responses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requests: list[tuple[str, float]] = []

            def get_readiness(url: str, timeout: float) -> tuple[int, bytes]:
                requests.append((url, timeout))
                return 200, b'{"ready": true}'

            controller = self.make_controller(Path(directory), http_get=get_readiness)
            controller._wait_private_ready(timeout=2)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0][0], "http://127.0.0.1:8000/hub/lab/ready")
            self.assertGreater(requests[0][1], 0)
            self.assertLessEqual(requests[0][1], 2)
            for status, body in (
                (200, b"not-json"),
                (200, b'{"ready": false}'),
                (200, b'{"ready": true, "extra": 1}'),
                (302, b'{"ready": true}'),
            ):
                with self.subTest(status=status, body=body):
                    rejected = self.make_controller(
                        Path(tempfile.mkdtemp()),
                        http_get=lambda _url, _timeout, result=(status, body): result,
                    )
                    with self.assertRaisesRegex(
                        maintenance.MaintenanceError, "readiness probe failed"
                    ):
                        rejected._wait_private_ready(timeout=0.01)

    def test_metadata_probe_timeout_cleans_up_owned_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            env = root / "env"
            env.write_text("TOKEN=fixture\n", encoding="utf-8")
            compose = root / "compose.yaml"
            compose.write_text("services: {}\n", encoding="utf-8")
            hub = root / "hub"
            pool = root / "pool"
            hub.mkdir()
            pool.mkdir()
            name = ""
            label_key = ""
            label_value = ""
            probe_full_id = "a" * 64
            events: list[tuple[str, ...]] = []

            def command(args: Any, _timeout: float, _check: bool = True) -> str:
                nonlocal name, label_key, label_value
                call = tuple(args)
                events.append(call)
                if call[:2] == ("docker", "create"):
                    name = call[call.index("--name") + 1]
                    label_key, label_value = call[call.index("--label") + 1].split(
                        "=", 1
                    )
                    raise maintenance.MaintenanceError(
                        "bounded external command timed out"
                    )
                if call[:3] == ("docker", "ps", "-aq"):
                    return probe_full_id if "--no-trunc" in call else probe_full_id[:12]
                if call[:3] == ("docker", "inspect", probe_full_id):
                    return json.dumps(
                        [
                            {
                                "Id": probe_full_id,
                                "Name": f"/{name}",
                                "State": {"Running": False},
                                "Config": {"Labels": {label_key: label_value}},
                                "HostConfig": {"NetworkMode": "none"},
                                "Mounts": [{"Type": "tmpfs", "Destination": "/tmp"}],  # ruff: ignore[S108] - inspected probe fixture
                            }
                        ]
                    )
                if call[:2] == ("docker", "rm"):
                    return ""
                raise AssertionError(f"unexpected command: {call!r}")

            controller = maintenance.Controller(
                project_directory=project,
                project_name="test-project",
                env_file=env,
                compose_files=[compose],
                backup_dir=root / "backup",
                command=command,
            )
            with self.assertRaisesRegex(maintenance.MaintenanceError, "timed out"):
                controller._probe_lab_image("sha256:lab")
            self.assertEqual(
                [call[1] for call in events], ["create", "ps", "inspect", "rm"]
            )
            self.assertIn("--no-trunc", events[1])
            self.assertIn(probe_full_id, events[-1])

    def test_overlapping_hub_and_pool_paths_fail_before_daemon_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            env = root / "environment.env"
            env.write_text("TOKEN=fixture\n", encoding="utf-8")
            compose = root / "compose.yaml"
            compose.write_text("services: {}\n", encoding="utf-8")
            data = root / "data"
            pool = data / "pool"
            pool.mkdir(parents=True)
            config = {
                "name": "test-project",
                "networks": {
                    "users": {"external": True, "name": "finki-hub-shell-users"}
                },
                "services": {
                    "web": {"network_mode": "host"},
                    "proxy": {
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
                        "network_mode": "host",
                        "privileged": True,
                        "volumes": [
                            {"type": "bind", "source": str(data), "target": "/srv/hub"},
                            {
                                "type": "bind",
                                "source": str(pool),
                                "target": "/srv/pool",
                            },
                            {
                                "type": "bind",
                                "source": "/var/run/docker.sock",
                                "target": "/var/run/docker.sock",
                            },
                        ],
                    },
                    "lab": {},
                },
            }
            events: list[tuple[str, ...]] = []

            def command(args: Any, _timeout: float, _check: bool = True) -> str:
                events.append(tuple(args))
                if "config" in args:
                    return json.dumps(config)
                raise AssertionError(
                    "overlap must be rejected before Docker inspection"
                )

            controller = maintenance.Controller(
                project_directory=project,
                project_name="test-project",
                env_file=env,
                compose_files=[compose],
                backup_dir=root / "backup",
                command=command,
            )
            with self.assertRaisesRegex(
                maintenance.MaintenanceError, "must not overlap"
            ):
                controller._load_config()
            self.assertEqual(len(events), 1)

    def test_hub_data_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            target = data / "target"
            target.write_text("fixture", encoding="utf-8")
            try:
                (data / "alias").symlink_to(target)
            except OSError as exc:
                self.skipTest(f"filesystem does not permit symlink fixtures: {exc}")
            with self.assertRaisesRegex(maintenance.MaintenanceError, "symlinks"):
                maintenance._assert_no_symlinks(data)

    def test_preflight_never_sweeps_another_invocations_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            data = root / "hub-data"
            data.mkdir()
            pool = root / "pool"
            pool.mkdir()
            backup_parent = root / "backups"
            backup_parent.mkdir()
            env_file = root / "environment.env"
            env_file.write_text("FIXTURE=only\n", encoding="utf-8")
            compose_file = root / "compose.yaml"
            compose_file.write_text("services: {}\n", encoding="utf-8")
            config = {
                "name": "test-project",
                "networks": {
                    "users": {"external": True, "name": "finki-hub-shell-users"}
                },
                "services": {
                    "web": {"network_mode": "host"},
                    "proxy": {
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
                        "network_mode": "host",
                        "privileged": True,
                        "volumes": [
                            {"type": "bind", "source": str(data), "target": "/srv/hub"},
                            {
                                "type": "bind",
                                "source": str(pool),
                                "target": "/srv/pool",
                            },
                            {
                                "type": "bind",
                                "source": "/var/run/docker.sock",
                                "target": "/var/run/docker.sock",
                            },
                        ],
                    },
                    "lab": {},
                },
            }
            foreign: dict[str, Any] = {
                "Id": "foreign-probe",
                "Name": "/finki-hub-version-probe-0123456789abcdef0123456789abcdef",
                "Image": "sha256:foreign-probe",
                "State": {"Running": True, "Status": "running"},
                "Config": {
                    "Labels": {
                        "org.finki-hub.maintenance-probe": "0123456789abcdef0123456789abcdef"
                    }
                },
                "HostConfig": {"NetworkMode": "none"},
                "Mounts": [{"Type": "tmpfs", "Destination": "/tmp"}],  # ruff: ignore[S108] - owned probe fixture
            }
            events: list[tuple[str, ...]] = []

            def command(args: Any, _timeout: float, _check: bool = True) -> str:
                call = tuple(args)
                events.append(call)
                if call[:3] == ("docker", "compose", "--project-directory"):
                    if call[-3:] == ("config", "--format", "json"):
                        return json.dumps(config)
                    if call[-3:-1] == ("ps", "-aq"):
                        return ""
                if call[:3] == ("docker", "info", "--format"):
                    return "fixture-daemon"
                if call[:3] == ("docker", "ps", "-aq"):
                    return "foreign-probe"
                if call[:2] == ("docker", "inspect"):
                    return json.dumps([foreign])
                if call[:2] == ("docker", "update"):
                    return ""
                if call[:2] == ("docker", "kill"):
                    foreign["State"]["Running"] = False
                    return ""
                if call[:2] == ("docker", "rm"):
                    foreign["removed"] = True
                    return ""
                raise AssertionError(f"unexpected command: {call!r}")

            controller = maintenance.Controller(
                project_directory=project,
                project_name="test-project",
                env_file=env_file,
                compose_files=[compose_file],
                backup_dir=backup_parent / "upgrade",
                command=command,
            )
            with self.assertRaisesRegex(
                maintenance.MaintenanceError, "Compose web container"
            ):
                controller.preflight()
            self.assertTrue(foreign["State"]["Running"])
            self.assertNotIn("removed", foreign)
            self.assertFalse(
                any(
                    call[:2]
                    in {("docker", "update"), ("docker", "kill"), ("docker", "rm")}
                    for call in events
                )
            )
            self.assertFalse(
                any(
                    call[:3] == ("docker", "ps", "-aq") and "--filter" in call
                    for call in events
                )
            )


if __name__ == "__main__":
    unittest.main()
