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
from scripts.integration import run_suite


class MaintenanceComposeConfigTests(unittest.TestCase):
    def assert_fixture_credential_key_absent(self, environment_file: str) -> None:
        self.assertFalse(
            "CONFIGPROXY_AUTH_TOKEN=" in environment_file,
            "fixture credential key was persisted",
        )

    def assert_unprofiled_lab_absent(self, services: dict[str, Any]) -> None:
        self.assertFalse("lab" in services, "unprofiled Lab was included")

    def make_suite_fixture(self, root: Path, repository: Path) -> run_suite.Suite:
        run_root = root / "run"
        run_root.mkdir()
        pool = run_root / "pool"
        pool.mkdir()
        candidate_source = run_root / "source" / "candidate"
        candidate_source.mkdir(parents=True)
        shutil.copy2(repository / "compose.yaml", candidate_source / "compose.yaml")

        suite = run_suite.Suite(repository, run_suite.BASELINE_SHA)
        suite.run_root = run_root
        suite.pool = pool
        suite.image_refs.update(
            {
                "old_hub": "ghcr.io/finki-hub/fixture-hub@sha256:" + "1" * 64,
                "old_lab": "ghcr.io/finki-hub/fixture-lab@sha256:" + "2" * 64,
                "candidate_hub": "ghcr.io/finki-hub/fixture-hub@sha256:" + "3" * 64,
                "candidate_lab": "ghcr.io/finki-hub/fixture-lab@sha256:" + "4" * 64,
                "web": "ghcr.io/finki-hub/fixture-web@sha256:" + "5" * 64,
                "proxy": "ghcr.io/finki-hub/fixture-proxy@sha256:" + "6" * 64,
            }
        )
        self.assertIsNone(suite.project_dir)
        suite.write_compose_fixture()
        return suite

    def test_actual_suite_fixture_files_keep_proxy_secret_in_memory(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            suite = self.make_suite_fixture(Path(directory), repository)
            assert suite.project_dir and suite.env_file and suite.compose
            assert suite.initial_override and suite.candidate_override

            self.assertTrue(suite.compose.is_file())
            self.assertTrue(
                suite.compose.read_bytes()
                == (repository / "compose.yaml").read_bytes(),
                "shipping Compose must remain unchanged by fixture adaptation",
            )
            self.assertTrue(suite.initial_override.is_file())
            self.assertTrue(suite.candidate_override.is_file())
            environment_file = suite.env_file.read_text(encoding="utf-8")
            self.assert_fixture_credential_key_absent(environment_file)
            credential = suite.fixture_runtime_env()["CONFIGPROXY_AUTH_TOKEN"]
            for generated in (
                suite.env_file,
                suite.compose,
                suite.initial_override,
                suite.candidate_override,
            ):
                self.assertFalse(
                    credential in generated.read_text(encoding="utf-8"),
                    "fixture credential was persisted",
                )

            old_override = json.loads(
                suite.initial_override.read_text(encoding="utf-8")
            )
            candidate_override = json.loads(
                suite.candidate_override.read_text(encoding="utf-8")
            )
            for override in (old_override, candidate_override):
                self.assertEqual(override["services"]["lab"]["networks"], ["users"])
                self.assertNotIn("networks", override["services"]["hub"])
                self.assertNotIn("networks", override["services"]["web"])
                self.assertNotIn("networks", override["services"]["proxy"])
            self.assertTrue(
                old_override["services"]["hub"]["image"] == suite.image_refs["old_hub"],
                "old Hub override differs",
            )
            self.assertTrue(
                old_override["services"]["lab"]["image"] == suite.image_refs["old_lab"],
                "old Lab override differs",
            )
            self.assertTrue(
                candidate_override["services"]["hub"]["image"]
                == suite.image_refs["candidate_hub"],
                "candidate Hub override differs",
            )
            self.assertTrue(
                candidate_override["services"]["lab"]["image"]
                == suite.image_refs["candidate_lab"],
                "candidate Lab override differs",
            )

    def test_sensitive_assertion_failures_do_not_render_operands(self) -> None:
        credential_sentinel = "SYNTHETIC_CREDENTIAL_SENTINEL"
        environment_payload = (
            "CONFIGPROXY_AUTH_TOKEN=" + credential_sentinel + "\nOTHER=value\n"
        )
        with self.assertRaises(AssertionError) as environment_failure:
            self.assert_fixture_credential_key_absent(environment_payload)
        environment_message = str(environment_failure.exception)
        self.assertFalse(
            credential_sentinel in environment_message,
            "credential sentinel appeared in assertion failure",
        )
        self.assertFalse(
            environment_payload in environment_message,
            "environment content appeared in assertion failure",
        )

        rendered_sentinel = "SYNTHETIC_RENDERED_MODEL_SENTINEL"
        rendered_services = {
            "web": {"image": "fixture-web"},
            "lab": {"environment": {"TOKEN": rendered_sentinel}},
        }
        rendered_content = json.dumps(rendered_services, sort_keys=True)
        with self.assertRaises(AssertionError) as services_failure:
            self.assert_unprofiled_lab_absent(rendered_services)
        services_message = str(services_failure.exception)
        self.assertFalse(
            rendered_sentinel in services_message,
            "rendered-model sentinel appeared in assertion failure",
        )
        self.assertFalse(
            rendered_content in services_message,
            "rendered services appeared in assertion failure",
        )

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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = self.make_suite_fixture(root, repository)
            assert suite.project_dir and suite.pool and suite.env_file and suite.compose
            assert suite.initial_override and suite.candidate_override
            fixture_env = suite.fixture_runtime_env()
            fixture_env.pop("COMPOSE_PROFILES", None)
            base_args = [
                docker,
                "compose",
                "--project-directory",
                str(suite.project_dir),
                "--project-name",
                suite.project_name,
                "--env-file",
                str(suite.env_file),
                "-f",
                str(suite.compose),
            ]

            def invoke_config(args: Sequence[str]) -> dict[str, Any]:
                actual_executable = shutil.which(args[0])
                if actual_executable is None:
                    raise AssertionError("Compose executable is unavailable")
                self.assertTrue(
                    Path(actual_executable).resolve() == Path(docker).resolve(),
                    "unexpected config executable",
                )
                self.assertTrue(args[1] == "compose", "expected Compose CLI")
                self.assertTrue("config" in args, "only config commands are allowed")
                self.assertTrue(
                    "--format" in args and "json" in args, "JSON config required"
                )
                result = subprocess.run(
                    list(args),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    stdin=subprocess.DEVNULL,
                    env=fixture_env,
                )
                self.assertEqual(
                    result.returncode, 0, "Compose config rendering failed"
                )
                rendered = json.loads(result.stdout)
                self.assertTrue(
                    isinstance(rendered, dict), "Compose config was not an object"
                )
                return rendered

            ordinary = invoke_config([*base_args, "config", "--format", "json"])
            self.assert_unprofiled_lab_absent(ordinary["services"])

            expected_hub_mounts = {
                (
                    str((suite.project_dir / "data" / "hub").resolve()),
                    "/srv/hub",
                    False,
                ),
                (str(suite.pool.resolve()), "/srv/pool", False),
                (
                    str(Path("/var/run/docker.sock").resolve()),
                    "/var/run/docker.sock",
                    False,
                ),
            }

            def assert_protected_model(
                model: dict[str, Any], *, hub_image: str, lab_image: str
            ) -> None:
                services = model.get("services") or {}
                hub = services.get("hub") or {}
                lab = services.get("lab") or {}
                self.assertTrue(
                    set(services) == {"web", "proxy", "hub", "lab"},
                    "profiled service set differs",
                )
                self.assertTrue(
                    model.get("name") == suite.project_name, "project name differs"
                )
                self.assertTrue(
                    hub.get("network_mode") == "host", "Hub network mode differs"
                )
                self.assertTrue(
                    hub.get("privileged") is True, "Hub privilege setting differs"
                )
                self.assertTrue(
                    hub.get("security_opt") in (None, []),
                    "unexpected Hub security options",
                )
                volumes = hub.get("volumes") or []
                actual_hub_mounts = {
                    (
                        str(Path(volume["source"]).resolve()),
                        volume.get("target"),
                        bool(volume.get("read_only", False)),
                    )
                    for volume in volumes
                }
                self.assertTrue(
                    actual_hub_mounts == expected_hub_mounts and len(volumes) == 3,
                    "protected Hub bind sources, targets, or modes differ",
                )
                self.assertTrue(hub.get("image") == hub_image, "Hub image differs")
                self.assertTrue(lab.get("image") == lab_image, "Lab image differs")
                self.assertTrue(
                    (hub.get("environment") or {}).get("LAB_IMAGE") == lab_image,
                    "Hub-selected Lab image differs",
                )
                networks = model.get("networks") or {}
                users_network = networks.get("users") or {}
                lab_networks = lab.get("networks") or {}
                self.assertTrue(
                    set(networks) == {"users"}
                    and users_network.get("external") is True
                    and users_network.get("name") == "finki-hub-shell-users"
                    and set(lab_networks) == {"users"},
                    "profiled Lab network topology differs",
                )
                self.assertTrue(
                    services["web"].get("image") == suite.image_refs["web"]
                    and services["proxy"].get("image") == suite.image_refs["proxy"],
                    "web or proxy image override differs",
                )

            def controller_for(
                override: Path,
                mutate: Callable[[dict[str, Any]], None] | None = None,
            ) -> tuple[maintenance.Controller, list[tuple[str, ...]]]:
                calls: list[tuple[str, ...]] = []

                def command(
                    args: Sequence[str], timeout: float, check: bool = True
                ) -> str:
                    call = tuple(args)
                    calls.append(call)
                    profile_index = call.index("--profile")
                    self.assertEqual(
                        call[profile_index : profile_index + 2], ("--profile", "images")
                    )
                    self.assertEqual(
                        call[profile_index + 2 : profile_index + 5],
                        ("config", "--format", "json"),
                    )
                    self.assertIn(
                        str(override), call, "generated image override missing"
                    )
                    configuration = invoke_config(args)
                    if mutate is not None:
                        mutate(configuration)
                    return json.dumps(configuration)

                return (
                    maintenance.Controller(
                        project_directory=suite.project_dir,
                        project_name=suite.project_name,
                        env_file=suite.env_file,
                        compose_files=[suite.compose, override],
                        backup_dir=root / "backup",
                        command=command,
                    ),
                    calls,
                )

            for override, hub_image, lab_image in (
                (
                    suite.initial_override,
                    suite.image_refs["old_hub"],
                    suite.image_refs["old_lab"],
                ),
                (
                    suite.candidate_override,
                    suite.image_refs["candidate_hub"],
                    suite.image_refs["candidate_lab"],
                ),
            ):
                controller, calls = controller_for(override)
                controller._load_config()
                self.assertEqual(len(calls), 1)
                assert controller.config
                assert_protected_model(
                    controller.config,
                    hub_image=hub_image,
                    lab_image=lab_image,
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
                    invalid, _ = controller_for(suite.candidate_override, mutate)
                    with self.assertRaises(maintenance.MaintenanceError):
                        invalid._load_config()


if __name__ == "__main__":
    unittest.main()
