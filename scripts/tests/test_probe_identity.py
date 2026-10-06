from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts import jupyterhub_maintenance as maintenance


class ProbeIdentityTests(unittest.TestCase):
    def make_controller(self, root: Path, command: Any) -> maintenance.Controller:
        project = root / "project"
        project.mkdir()
        env_file = root / "environment.env"
        env_file.write_text("TOKEN=fixture\n", encoding="utf-8")
        compose_file = root / "compose.yaml"
        compose_file.write_text("services: {}\n", encoding="utf-8")
        backup_parent = root / "backups"
        backup_parent.mkdir()
        return maintenance.Controller(
            project_directory=project,
            project_name="probe-test",
            env_file=env_file,
            compose_files=[compose_file],
            backup_dir=backup_parent / "backup",
            command=command,
        )

    def test_probe_cleanup_uses_full_id_and_removes_its_owned_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            full_id = "a" * 64
            short_id = full_id[:12]
            calls: list[tuple[str, ...]] = []
            name = ""
            label_key = ""
            label_value = ""
            inspected: dict[str, Any] = {}

            def command(args: Any, _timeout: float, _check: bool = True) -> str:
                nonlocal name, label_key, label_value
                call = tuple(args)
                calls.append(call)
                if call[:2] == ("docker", "create"):
                    name = call[call.index("--name") + 1]
                    label_key, label_value = call[call.index("--label") + 1].split(
                        "=", 1
                    )
                    return full_id
                if call[:3] == ("docker", "start", "-a"):
                    self.assertEqual(call[3], full_id)
                    return "6.0.1"
                if call[:3] == ("docker", "ps", "-aq"):
                    # Docker truncates by default; --no-trunc preserves identity.
                    return full_id if "--no-trunc" in call else short_id
                if call[:2] == ("docker", "inspect"):
                    self.assertEqual(call[2], full_id)
                    inspected.update(
                        {
                            "Id": full_id,
                            "Name": f"/{name}",
                            "State": {"Running": False},
                            "Config": {"Labels": {label_key: label_value}},
                            "HostConfig": {"NetworkMode": "none"},
                            "Mounts": [{"Type": "tmpfs", "Destination": "/tmp"}],  # ruff: ignore[S108] - owned probe fixture
                        }
                    )
                    return json.dumps([inspected])
                if call[:2] == ("docker", "rm"):
                    self.assertEqual(call[2], full_id)
                    return ""
                raise AssertionError(f"unexpected command: {call!r}")

            controller = self.make_controller(Path(directory), command)
            self.assertEqual(controller._probe_lab_image("sha256:lab"), "6.0.1")
            listing = next(
                call for call in calls if call[:3] == ("docker", "ps", "-aq")
            )
            self.assertIn("--no-trunc", listing)
            self.assertTrue(any(call[:2] == ("docker", "rm") for call in calls))
            self.assertEqual(inspected["Id"], full_id)

    def test_different_full_id_refuses_cleanup_without_removing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected_id = "a" * 64
            other_id = "b" * 64
            calls: list[tuple[str, ...]] = []
            name = ""
            label_key = ""
            label_value = ""

            def command(args: Any, _timeout: float, _check: bool = True) -> str:
                nonlocal name, label_key, label_value
                call = tuple(args)
                calls.append(call)
                if call[:2] == ("docker", "create"):
                    name = call[call.index("--name") + 1]
                    label_key, label_value = call[call.index("--label") + 1].split(
                        "=", 1
                    )
                    return expected_id
                if call[:3] == ("docker", "start", "-a"):
                    return "6.0.1"
                if call[:3] == ("docker", "ps", "-aq"):
                    self.assertIn("--no-trunc", call)
                    return other_id
                if call[:2] == ("docker", "inspect"):
                    self.fail("mismatched full ID must be refused before inspect")
                if call[:2] == ("docker", "rm"):
                    self.fail("mismatched container must never be removed")
                raise AssertionError(
                    f"unexpected command: {call!r}; probe={name, label_key, label_value}"
                )

            controller = self.make_controller(Path(directory), command)
            with self.assertRaisesRegex(
                maintenance.MaintenanceError, "identity is ambiguous"
            ):
                controller._probe_lab_image("sha256:lab")
            self.assertFalse(any(call[:2] == ("docker", "rm") for call in calls))

    def test_foreign_probe_with_other_label_is_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[tuple[str, ...]] = []
            foreign: dict[str, Any] = {
                "id": "f" * 64,
                "label": "foreign-invocation-token",
                "running": True,
            }

            def command(args: Any, _timeout: float, _check: bool = True) -> str:
                call = tuple(args)
                calls.append(call)
                if call[:3] == ("docker", "ps", "-aq"):
                    # Exact invocation-label filtering must not select this probe.
                    filter_arg = next(arg for arg in call if arg.startswith("label="))
                    if filter_arg == (
                        "label=org.finki-hub.maintenance-probe=" + foreign["label"]
                    ):
                        return str(foreign["id"])
                    self.assertEqual(
                        filter_arg,
                        "label=org.finki-hub.maintenance-probe=our-invocation-token",
                    )
                    return ""
                raise AssertionError(
                    f"foreign probe was unexpectedly accessed: {call!r}"
                )

            controller = self.make_controller(Path(directory), command)
            controller._cleanup_probe_container(
                "finki-hub-version-probe-our-invocation-token",
                "org.finki-hub.maintenance-probe",
                "our-invocation-token",
                "e" * 64,
            )
            self.assertTrue(foreign["running"])
            self.assertFalse(
                any(
                    call[:2] in {("docker", "rm"), ("docker", "kill")} for call in calls
                )
            )


if __name__ == "__main__":
    unittest.main()
