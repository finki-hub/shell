from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from scripts import jupyterhub_maintenance as maintenance


class MaintenanceComposeConfigTests(unittest.TestCase):
    @unittest.skipIf(
        os.name == "nt",
        "the actual Compose normalization test is required on native Linux CI",
    )
    def test_images_profile_loads_actual_compose_topology_and_rejects_drift(
        self,
    ) -> None:
        docker = shutil.which("docker")
        if docker is None:
            self.fail(
                "Docker Compose CLI is required for native Linux config validation"
            )

        repository = Path(__file__).resolve().parents[2]
        compose_file = repository / "compose.yaml"
        self.assertTrue(compose_file.is_file())
        child_env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "TMPDIR", "LANG")
            if key in os.environ
        }
        child_env.pop("COMPOSE_PROFILES", None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            (project / "data" / "hub").mkdir(parents=True)
            pool = root / "pool"
            pool.mkdir()
            env_file = root / "compose.env"
            env_file.write_text(
                "CONFIGPROXY_AUTH_TOKEN=nonsecret-compose-test-placeholder\n"
                f"LAB_POOL_DIR={pool}\n",
                encoding="utf-8",
            )
            base_args = [
                docker,
                "compose",
                "--project-directory",
                str(project),
                "--project-name",
                "finki-hub-shell",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
            ]

            def render(args: Sequence[str]) -> dict[str, Any]:
                result = subprocess.run(
                    list(args),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    stdin=subprocess.DEVNULL,
                    env=child_env,
                )
                self.assertEqual(
                    result.returncode, 0, "Compose config rendering failed"
                )
                rendered = json.loads(result.stdout)
                self.assertIsInstance(rendered, dict)
                return rendered

            ordinary = render([*base_args, "config", "--format", "json"])
            self.assertNotIn("lab", ordinary["services"])

            def controller_for(
                mutate: Callable[[dict[str, Any]], None] | None = None,
            ) -> tuple[maintenance.Controller, list[tuple[str, ...]]]:
                calls: list[tuple[str, ...]] = []

                def command(
                    args: Sequence[str], timeout: float, check: bool = True
                ) -> str:
                    call = tuple(args)
                    calls.append(call)
                    actual_executable = shutil.which(call[0])
                    if actual_executable is None:
                        raise AssertionError(
                            "Compose command executable is unavailable"
                        )
                    self.assertEqual(
                        Path(actual_executable).resolve(), Path(docker).resolve()
                    )
                    self.assertEqual(call[1], "compose")
                    profile_index = call.index("--profile")
                    self.assertEqual(
                        call[profile_index : profile_index + 2], ("--profile", "images")
                    )
                    self.assertEqual(
                        call[profile_index + 2 : profile_index + 5],
                        ("config", "--format", "json"),
                    )
                    result = subprocess.run(
                        list(args),
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=min(timeout, 30),
                        stdin=subprocess.DEVNULL,
                        env=child_env,
                    )
                    if result.returncode:
                        raise AssertionError("Compose config rendering failed")
                    configuration = json.loads(result.stdout)
                    if mutate is not None:
                        mutate(configuration)
                    return json.dumps(configuration)

                return (
                    maintenance.Controller(
                        project_directory=project,
                        project_name="finki-hub-shell",
                        env_file=env_file,
                        compose_files=[compose_file],
                        backup_dir=root / "backup",
                        command=command,
                    ),
                    calls,
                )

            controller, calls = controller_for()
            controller._load_config()
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                set(controller.config["services"]), {"web", "proxy", "hub", "lab"}
            )
            self.assertEqual(set(controller.config["networks"]), {"users"})
            self.assertIs(controller.config["networks"]["users"]["external"], True)
            self.assertEqual(
                controller.config["networks"]["users"]["name"],
                "finki-hub-shell-users",
            )
            self.assertEqual(
                set(controller.config["services"]["lab"]["networks"]), {"users"}
            )
            self.assertNotIn("--profile", controller._compose_args())

            invalid_models: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
                (
                    "missing-profile-service",
                    lambda model: model["services"].pop("lab"),
                ),
                (
                    "missing-lab-network",
                    lambda model: model["services"]["lab"].pop("networks", None),
                ),
                (
                    "wrong-lab-network",
                    lambda model: model["services"]["lab"].update(
                        {"networks": {"other": {}}}
                    ),
                ),
                (
                    "extra-lab-network",
                    lambda model: model["services"]["lab"].update(
                        {"networks": {"users": {}, "other": {}}}
                    ),
                ),
                (
                    "wrong-network-name",
                    lambda model: model["networks"]["users"].update(
                        {"name": "other-users"}
                    ),
                ),
                (
                    "internal-users-network",
                    lambda model: model["networks"]["users"].update(
                        {"external": False}
                    ),
                ),
                (
                    "extra-top-level-network",
                    lambda model: model["networks"].update(
                        {"other": {"name": "other-users", "external": True}}
                    ),
                ),
            )
            for name, mutate in invalid_models:
                with self.subTest(case=name):
                    invalid, _ = controller_for(mutate)
                    with self.assertRaises(maintenance.MaintenanceError):
                        invalid._load_config()


if __name__ == "__main__":
    unittest.main()
