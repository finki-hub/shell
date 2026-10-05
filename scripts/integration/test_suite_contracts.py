"""Static/nonprivileged safeguards for the full runtime orchestrator."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.integration import run_suite


def running_inspector(run_id: str):
    return lambda container_id: {
        "Id": container_id,
        "State": {"Running": True},
        "Config": {"Labels": {run_suite.RUN_LABEL: run_id}},
    }


def docker_call_logger(calls: list[list[str]]):
    def docker(*args, **_kwargs):
        calls.append(list(args))
        return b""

    return docker


class SuiteContractTests(unittest.TestCase):
    def test_full_suite_uses_pinned_baseline_and_bounded_xfs_fixture(self) -> None:
        self.assertEqual(
            run_suite.BASELINE_SHA,
            "6f682ee17c8affa988deffa44c56f2e39e28e462",
        )
        self.assertEqual(run_suite.POOL_SIZE, 1024**3)
        self.assertGreaterEqual(run_suite.MIN_FREE, 25 * 1024**3)
        self.assertLessEqual(run_suite.OVERALL_TEST_TIMEOUT, 5400)

    def test_suite_is_explicitly_acknowledged_and_contains_required_transactions(
        self,
    ) -> None:
        import inspect

        source = inspect.getsource(run_suite)
        for flag in (
            "--acknowledge-disposable",
            "--acknowledge-interruption",
            "--acknowledge-ingress-fenced",
            "--acknowledge-updater-paused",
        ):
            self.assertIn(flag, source)
        for operation in (
            'self.run_helper("preflight"',
            'self.run_helper("migrate"',
            'self.run_helper("restore"',
            'self.run_helper("accept"',
            'self.run_probe("baseline")',
            'self.run_probe("candidate")',
            'self.run_probe("restore")',
        ):
            self.assertIn(operation, source)

    def test_cleanup_has_no_global_prune_and_xfs_proves_edquot(self) -> None:
        import inspect

        source = inspect.getsource(run_suite)
        self.assertNotIn("system prune", source)
        self.assertNotIn('"volume", "prune"', source)
        self.assertIn("errno.EDQUOT", source)
        self.assertIn("losetup", source)
        self.assertIn("run_label", source)
        self.assertIn("hub6-normal-start-after-explicit-upgrade-db", source)
        self.assertIn("migration_disabled=True", source)
        self.assertIn("cullers_suppressed=True", source)

    def test_accept_uses_helper_returned_versioned_runtime_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            backup = root / "backup"
            project.mkdir()
            backup.mkdir()
            runtime = backup / "activation-restored-v7.override.json"
            runtime.write_text('{"services":{}}\n', encoding="utf-8")
            runtime.chmod(0o600)
            marker = project / ".jupyterhub-maintenance.json"
            marker.write_text(
                json.dumps({"backup_directory": str(backup)}), encoding="utf-8"
            )
            marker.chmod(0o600)
            (backup / "state.json").write_text(
                json.dumps(
                    {
                        "stages": [
                            {
                                "stage": "accepted",
                                "runtime_override": str(runtime),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.project_dir = project
            with patch.object(run_suite.stat, "S_IMODE", return_value=0o600):
                self.assertEqual(
                    suite.verify_acceptance_paths(
                        backup,
                        {"runtime_override": str(runtime), "marker": str(marker)},
                    ),
                    (runtime.resolve(), marker.resolve()),
                )

    def test_readiness_parses_bounded_semantic_json(self) -> None:
        self.assertTrue(run_suite.readiness_is_ready(200, b'{"ready": true}'))
        self.assertFalse(run_suite.readiness_is_ready(503, b'{"ready": true}'))
        self.assertFalse(run_suite.readiness_is_ready(200, b'{"ready": false}'))
        self.assertFalse(run_suite.readiness_is_ready(200, b"not-json"))
        self.assertFalse(run_suite.readiness_is_ready(200, b" " * 1025))

    def test_db_result_contract_is_scoped_and_rejects_missing_evidence(self) -> None:
        facts = {
            "status": "pass",
            "migration_disabled_cold_startup_rejection": "pass",
            "migration_disabled_startup_preserved_schema_and_identities": "pass",
            "schema_idempotence": "pass",
            "old_orm_cold_restore_compatibility": "pass",
            "positive_server_startup": "not-tested-by-db-fixture",
        }
        run_suite.validate_db_evidence(facts)
        del facts["migration_disabled_startup_preserved_schema_and_identities"]
        with self.assertRaises(run_suite.HarnessFailure):
            run_suite.validate_db_evidence(facts)

    def test_source_archive_creates_owned_source_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            run_root = root / "run"
            run_root.mkdir()
            suite = run_suite.Suite(workspace, run_suite.BASELINE_SHA)
            suite.run_root = run_root
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                content = b"fixture-source"
                entry = tarfile.TarInfo("marker.txt")
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
            payload = buffer.getvalue()

            def fake_git_archive(_argv, *, stdout, **_kwargs):
                stdout.write(payload)
                return subprocess.CompletedProcess([], 0)

            destination = run_root / "source" / "baseline"
            with patch.object(
                run_suite.subprocess, "run", side_effect=fake_git_archive
            ):
                extracted = suite.archive_source("a" * 40, destination)
            self.assertEqual((extracted / "marker.txt").read_bytes(), b"fixture-source")

    def test_proxy_image_reference_is_registered_with_its_content_id(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        content_id = "sha256:" + "a" * 64
        suite.register_image_reference("proxy", content_id, "local/proxy:run")
        self.assertEqual(suite.image_ids["proxy"], content_id)
        self.assertEqual(suite.image_refs["proxy"], "local/proxy:run")

    def test_second_scenario_normalizes_old_hub_before_preflight(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        suite.initial_override = Path("old.override.json")
        suite.image_ids["old_hub"] = "sha256:" + "a" * 64
        calls: list[str] = []
        with (
            patch.object(
                suite,
                "compose_command",
                side_effect=lambda *a, **k: calls.append("compose"),
            ),
            patch.object(suite, "compose_ids", return_value=["owned-hub"]),
            patch.object(
                suite, "wait_ready", side_effect=lambda: calls.append("ready")
            ),
            patch.object(
                suite, "verify_hub_mode", side_effect=lambda **_k: calls.append("mode")
            ),
            patch.object(
                suite, "run_probe", side_effect=lambda _stage: calls.append("probe")
            ),
            patch.object(
                suite, "record_stage", side_effect=lambda _name: calls.append("record")
            ),
        ):
            suite.normalize_old_baseline()
        self.assertEqual(calls, ["compose", "ready", "mode", "probe", "record"])

    def test_old_and_candidate_compose_overrides_label_web_and_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            pool = run_root / "pool"
            source = run_root / "source" / "candidate"
            pool.mkdir(parents=True)
            source.mkdir(parents=True)
            (source / "compose.yaml").write_text("name: fixture\n", encoding="utf-8")
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.run_root = run_root
            suite.pool = pool
            suite.image_refs.update(
                {
                    "old_hub": "registry/old-hub@sha256:" + "a" * 64,
                    "old_lab": "registry/old-lab@sha256:" + "b" * 64,
                    "candidate_hub": "registry/new-hub@sha256:" + "c" * 64,
                    "candidate_lab": "registry/new-lab@sha256:" + "d" * 64,
                    "web": "registry/web@sha256:" + "e" * 64,
                    "proxy": "registry/proxy@sha256:" + "f" * 64,
                }
            )
            suite.write_compose_fixture()
            self.assertIsNotNone(suite.initial_override)
            self.assertIsNotNone(suite.candidate_override)
            old = json.loads(suite.initial_override.read_text(encoding="utf-8"))
            candidate = json.loads(suite.candidate_override.read_text(encoding="utf-8"))
            for config in (old, candidate):
                for service in ("web", "proxy"):
                    self.assertEqual(
                        config["services"][service]["labels"][run_suite.RUN_LABEL],
                        suite.run_id,
                    )

    def test_updater_receives_fixture_project_and_refusal_preserves_all_old_ids(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            project = root / "project"
            run_root.mkdir()
            project.mkdir()
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.run_root = run_root
            suite.project_dir = project
            suite.project_name = "fixture-" + suite.run_id
            old_ids = {
                "web": "web-full-id",
                "proxy": "proxy-full-id",
                "hub": "hub-full-id",
            }
            captured: dict = {}

            def updater_call(_argv, **kwargs):
                captured.update(kwargs)
                return subprocess.CompletedProcess(
                    [], 1, b"Hub version change 5.5.1 -> 6.0.1", b""
                )

            with (
                patch.object(suite, "snapshot_service_ids", return_value=old_ids),
                patch.object(suite, "wait_ready") as wait_ready,
                patch.object(run_suite, "safe_call", side_effect=updater_call),
            ):
                suite.run_updater_refusal()
            self.assertEqual(
                captured["env"]["COMPOSE_PROJECT_NAME"], suite.project_name
            )
            self.assertEqual(
                captured["env"]["UPDATE_LOCK_FILE"], str(run_root / "update.lock")
            )
            wait_ready.assert_called_once_with()
            self.assertEqual(
                suite.results["cases"]["routine-updater-refusal-old-stack-usable"][
                    "status"
                ],
                "pass",
            )

    def test_updater_refusal_rejects_missing_or_replaced_old_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            run_root.mkdir()
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.run_root = run_root
            suite.project_dir = root / "project"
            old_ids = {
                "web": "web-full-id",
                "proxy": "proxy-full-id",
                "hub": "hub-full-id",
            }
            changed_ids = {
                "web": "web-full-id",
                "proxy": "missing",
                "hub": "hub-full-id",
            }
            with (
                patch.object(
                    suite,
                    "snapshot_service_ids",
                    side_effect=[old_ids, changed_ids],
                ),
                patch.object(
                    run_suite,
                    "safe_call",
                    return_value=subprocess.CompletedProcess(
                        [], 1, b"Hub version change 5.5.1 -> 6.0.1", b""
                    ),
                ),
                self.assertRaisesRegex(
                    run_suite.HarnessFailure, "changed old service identities"
                ),
            ):
                suite.run_updater_refusal()

    def test_compose_cleanup_failure_preserves_labs_pool_and_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-root"
            root.mkdir()
            manifest = root / "owner.json"
            manifest.write_text("owned fixture manifest", encoding="utf-8")
            suite = run_suite.Suite(Path(directory), run_suite.BASELINE_SHA)
            suite.run_root = root
            suite.marker = manifest
            suite.created_run_root = True
            with (
                patch.object(
                    suite,
                    "cleanup_compose",
                    side_effect=run_suite.HarnessFailure("sanitized"),
                ),
                patch.object(suite, "cleanup_dynamic_labs") as labs,
                patch.object(suite, "cleanup_owned_tools"),
                patch.object(suite, "cleanup_registry"),
                patch.object(suite, "cleanup_network") as network,
                patch.object(suite, "cleanup_pool") as pool,
                patch.object(suite, "remove_run_images"),
                patch.object(suite, "cleanup_run_root") as root_cleanup,
            ):
                failures = suite.cleanup()
            self.assertIn("compose-control-plane", failures)
            labs.assert_not_called()
            network.assert_not_called()
            pool.assert_not_called()
            root_cleanup.assert_not_called()
            self.assertTrue(manifest.is_file())
            self.assertEqual(
                manifest.read_text(encoding="utf-8"), "owned fixture manifest"
            )
            self.assertEqual(suite.results["preserved_run_root"], str(root))

    def test_lab_image_must_match_stage_content_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = root / "pool"
            users = pool / "users"
            (users / "user-a").mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.pool = pool
            suite.run_id = "a" * 12
            container_id = "a" * 64
            suite.docker = lambda *_args, **_kwargs: (container_id + "\n").encode()
            suite.inspect_container = lambda _cid: {
                "Id": container_id,
                "Image": "sha256:" + "b" * 64,
                "Name": "/lab-user-a",
                "Config": {
                    "Labels": {
                        run_suite.RUN_LABEL: suite.run_id,
                        "finki.role": "lab",
                        "finki.user": "user-a",
                    }
                },
                "Mounts": [
                    {"Destination": "/home/ubuntu", "Source": str(users / "user-a")}
                ],
            }
            with self.assertRaisesRegex(run_suite.HarnessFailure, "ownership"):
                suite.capture_lab_ids({"user-a"}, "sha256:" + "a" * 64)

    def test_inode_project_id_reads_inode_xfs_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            encoded_id = 12345

            def ioctl(_fd, _request, attributes, _mutate):
                run_suite.struct.pack_into("=I", attributes, 12, encoded_id)

            with (
                patch.dict("sys.modules", {"fcntl": SimpleNamespace(ioctl=ioctl)}),
                patch.object(run_suite.os, "open", return_value=123),
                patch.object(run_suite.os, "close"),
            ):
                self.assertEqual(
                    run_suite.Suite.inode_project_id(Path(directory)), encoded_id
                )

    def test_wrong_inode_project_id_fails_even_when_mapping_has_an_id(self) -> None:
        with (
            patch.object(run_suite.Suite, "inode_project_id", return_value=1002),
            self.assertRaisesRegex(run_suite.HarnessFailure, "inode XFS project ID"),
        ):
            run_suite.Suite.verify_inode_project_id(Path("/fixture/home"), 1001)

    def test_partial_container_create_timeout_reconciles_exact_owned_name(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        container_id = "a" * 64
        content_id = "sha256:" + "b" * 64
        suite.image_id = lambda _reference: content_id

        def fake_docker(*args, **_kwargs):
            if args[0] == "create":
                raise run_suite.HarnessFailure("bounded Docker operation timed out")
            if args[0] == "ps":
                return (container_id + "\n").encode()
            raise AssertionError("unexpected Docker call")

        suite.docker = fake_docker
        suite.docker_result = lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, (container_id + "\n").encode(), b""
        )
        suite.inspect_container = lambda _cid: {
            "Id": container_id,
            "Image": content_id,
            "Name": "/probe-owned-name",
            "HostConfig": {"NetworkMode": "none"},
            "Config": {
                "Labels": {
                    run_suite.RUN_LABEL: suite.run_id,
                    "shell.integration.purpose": "probe-tool",
                }
            },
            "Mounts": [],
        }
        with self.assertRaisesRegex(run_suite.HarnessFailure, "timed out"):
            suite.own_container(
                "probe-owned-name",
                "immutable-probe-image",
                ["python", "probe.py"],
                kind="probe-tool",
            )
        intent = suite.manifest["resources"]["containers"][0]
        self.assertEqual(intent["id"], container_id)
        self.assertFalse(intent["intent"])

    def test_create_timeout_and_failed_empty_inventory_keeps_intent_owned(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        suite.image_id = lambda _reference: "sha256:" + "b" * 64
        suite.docker = lambda *args, **_kwargs: (_ for _ in ()).throw(
            run_suite.HarnessFailure("create timed out")
        )
        suite.docker_result = lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, b"", b"Cannot connect to the Docker daemon"
        )
        with self.assertRaisesRegex(run_suite.HarnessFailure, "inventory"):
            suite.own_container(
                "probe-after-timeout",
                "sha256:" + "b" * 64,
                ["python", "probe.py"],
                kind="probe-tool",
            )
        intent = suite.manifest["resources"]["containers"][0]
        self.assertTrue(intent["intent"])
        self.assertFalse(intent["removed"])

    def test_create_timeout_and_successful_empty_inventory_confirms_absence(
        self,
    ) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        suite.image_id = lambda _reference: "sha256:" + "b" * 64
        suite.docker = lambda *args, **_kwargs: (_ for _ in ()).throw(
            run_suite.HarnessFailure("create timed out")
        )
        suite.docker_result = lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, b"", b""
        )
        with self.assertRaisesRegex(run_suite.HarnessFailure, "create timed out"):
            suite.own_container(
                "probe-absent-after-timeout",
                "sha256:" + "b" * 64,
                ["python", "probe.py"],
                kind="probe-tool",
            )
        intent = suite.manifest["resources"]["containers"][0]
        self.assertFalse(intent["intent"])
        self.assertTrue(intent["removed"])

    def test_lab_inspect_transport_failure_preserves_resource_and_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = root / "pool"
            pool.mkdir()
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.pool = pool
            resource = {
                "id": "a" * 64,
                "kind": "fixture-lab",
                "removed": False,
            }
            failure = subprocess.CompletedProcess(
                [], 1, b"", b"Cannot connect to the Docker daemon"
            )
            with (
                patch.object(run_suite, "safe_call", return_value=failure),
                self.assertRaisesRegex(run_suite.HarnessFailure, "verify fixture Lab"),
            ):
                suite.reconcile_helper_lab_presence(resource)
            self.assertFalse(resource["removed"])
            self.assertTrue(pool.is_dir())

    def test_lab_inspect_exact_not_found_confirms_removal(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        resource = {"id": "a" * 64, "kind": "fixture-lab", "removed": False}
        failure = subprocess.CompletedProcess(
            [], 1, b"", f"Error: No such object: {resource['id']}".encode()
        )
        with patch.object(run_suite, "safe_call", return_value=failure):
            suite.reconcile_helper_lab_presence(resource)
        self.assertTrue(resource["removed"])

    def test_lab_inspect_unrelated_error_is_not_absence(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        resource = {"id": "a" * 64, "kind": "fixture-lab", "removed": False}
        failure = subprocess.CompletedProcess(
            [], 1, b"", b"Error: Docker daemon is unavailable"
        )
        with (
            patch.object(run_suite, "safe_call", return_value=failure),
            self.assertRaises(run_suite.HarnessFailure),
        ):
            suite.reconcile_helper_lab_presence(resource)
        self.assertFalse(resource["removed"])

    def test_partial_compose_startup_is_reconciled_from_full_fixture_intent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.project_dir = project
            suite.project_name = "fixture-project"
            container_id = "f" * 64
            image_id = "sha256:" + "a" * 64
            intent = {
                "id": None,
                "name": "fixture-project-web-1",
                "kind": "compose-intent",
                "project": "fixture-project",
                "service": "web",
                "project_directory": str(project),
                "expected_images": [image_id],
                "allowed_mount_sets": [[]],
                "removed": False,
                "intent": True,
            }
            suite.manifest["resources"]["containers"].append(intent)
            suite.docker = lambda *args, **_kwargs: (
                f"{container_id}\n".encode()
                if args == ("ps", "-aq", "--no-trunc")
                else b""
            )
            suite.inspect_container = lambda _cid: {
                "Id": container_id,
                "Image": image_id,
                "Name": "/fixture-project-web-1",
                "State": {"Running": False},
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "fixture-project",
                        "com.docker.compose.service": "web",
                        "com.docker.compose.project.working_dir": str(project),
                        run_suite.RUN_LABEL: suite.run_id,
                    }
                },
                "Mounts": [],
            }
            with patch.object(
                run_suite,
                "safe_call",
                return_value=subprocess.CompletedProcess(
                    [], 1, b"", b"Error: No such container"
                ),
            ):
                suite.cleanup_compose()
            self.assertEqual(intent["id"], container_id)
            self.assertTrue(intent["removed"])

    def test_helper_replacement_intents_are_saved_before_cli_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            pool = root / "pool"
            backup = root / "backup"
            config = backup / "configuration" / "jupyterhub_maintenance_config.py"
            project.mkdir()
            pool.mkdir()
            config.parent.mkdir(parents=True)
            config.write_text("# fixture\n", encoding="utf-8")
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.run_root = root
            suite.project_dir = project
            suite.pool = pool
            suite.env_file = project / ".env"
            suite.compose = project / "compose.yaml"
            suite.candidate_override = project / "candidate.json"
            suite.image_ids.update(
                {
                    "web": "sha256:" + "a" * 64,
                    "proxy": "sha256:" + "b" * 64,
                    "old_hub": "sha256:" + "c" * 64,
                    "candidate_hub": "sha256:" + "d" * 64,
                }
            )

            container_id = "9" * 64
            actual: dict = {}

            def failed_helper(_argv, **_kwargs):
                intents = suite.manifest["resources"]["containers"]
                self.assertEqual(
                    {item["service"] for item in intents}, {"hub", "proxy", "web"}
                )
                self.assertTrue(all(item["intent"] for item in intents))
                hub = next(item for item in intents if item["service"] == "hub")
                hub["id"] = container_id
                hub["intent"] = False
                mounts = [
                    {
                        "Type": "bind",
                        "Source": str(Path("/var/run/docker.sock").resolve()),
                        "Destination": "/var/run/docker.sock",
                        "RW": True,
                    },
                    {
                        "Type": "bind",
                        "Source": str((project / "data" / "hub").resolve()),
                        "Destination": "/srv/hub",
                        "RW": True,
                    },
                    {
                        "Type": "bind",
                        "Source": str(pool.resolve()),
                        "Destination": "/srv/pool",
                        "RW": True,
                    },
                    {
                        "Type": "bind",
                        "Source": str(config.resolve()),
                        "Destination": "/srv/maintenance/jupyterhub-maintenance-config.py",
                        "RW": False,
                    },
                ]
                actual.update(
                    {
                        "Image": suite.image_ids["candidate_hub"],
                        "Name": f"/{suite.project_name}-hub-1",
                        "State": {"Running": False},
                        "Mounts": mounts,
                        "Id": container_id,
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": suite.project_name,
                                "com.docker.compose.service": "hub",
                                "com.docker.compose.project.working_dir": str(project),
                                run_suite.RUN_LABEL: suite.run_id,
                            }
                        },
                    }
                )
                suite.save_manifest = lambda: None
                raise run_suite.HarnessFailure(
                    "bounded helper failure after Hub replacement"
                )

            with (
                patch.object(run_suite, "safe_call", side_effect=failed_helper),
                self.assertRaisesRegex(run_suite.HarnessFailure, "helper failure"),
            ):
                suite.run_helper("migrate", backup)
            suite.docker = lambda *args, **_kwargs: (
                f"{container_id}\n".encode()
                if args == ("ps", "-aq", "--no-trunc")
                else b""
            )
            suite.inspect_container = lambda _cid: actual
            with patch.object(
                run_suite,
                "safe_call",
                return_value=subprocess.CompletedProcess(
                    [], 1, b"", b"Error: No such container"
                ),
            ):
                suite.cleanup_compose()
            self.assertTrue(
                all(
                    item.get("project_directory", str(project)) == str(project)
                    for item in suite.manifest["resources"]["containers"]
                )
            )
            self.assertTrue(
                next(
                    item
                    for item in suite.manifest["resources"]["containers"]
                    if item["service"] == "hub"
                )["removed"]
            )

    def test_live_network_endpoint_refuses_network_removal(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        network_id = "c" * 64
        suite.network_id = network_id
        suite.manifest["resources"]["networks"].append(
            {"id": network_id, "name": run_suite.NETWORK_NAME, "removed": False}
        )
        suite.docker_json = lambda *_args, **_kwargs: [
            {
                "Name": run_suite.NETWORK_NAME,
                "Driver": "bridge",
                "Internal": True,
                "Labels": {run_suite.RUN_LABEL: suite.run_id},
                "IPAM": {
                    "Config": [{"Subnet": "172.30.0.0/23", "Gateway": "172.30.0.1"}]
                },
                "Options": {
                    "com.docker.network.bridge.enable_icc": "false",
                    "com.docker.network.bridge.name": run_suite.BRIDGE_NAME,
                },
                "Containers": {"live-endpoint": {"Name": "fixture"}},
            }
        ]
        calls: list[str] = []
        suite.docker = lambda *args, **_kwargs: calls.append(args[0]) or b""
        with self.assertRaisesRegex(run_suite.HarnessFailure, "endpoints"):
            suite.cleanup_network()
        self.assertNotIn("network", calls)

    def test_wrong_loop_backing_is_rejected_before_unmount_or_detach(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = root / "pool"
            pool.mkdir()
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.run_root = root
            suite.pool = pool
            suite.loop_device = "/dev/loop17"
            calls: list[list[str]] = []

            def fake_safe_call(argv, **_kwargs):
                calls.append(argv)
                if argv[0] == "findmnt" and "-S" in argv:
                    return subprocess.CompletedProcess(
                        argv, 0, f"{pool}\n".encode(), b""
                    )
                if argv[0] == "findmnt":
                    return subprocess.CompletedProcess(
                        argv, 0, f"/dev/loop17 {pool}\n".encode(), b""
                    )
                raise AssertionError("unexpected process call")

            def fake_require(argv, **_kwargs):
                calls.append(argv)
                if "BACK-FILE" in argv:
                    return b"/different/pool.img\n"
                raise AssertionError("detach must not be reached")

            with (
                patch.object(run_suite, "safe_call", side_effect=fake_safe_call),
                patch.object(run_suite, "require_call", side_effect=fake_require),
                self.assertRaisesRegex(run_suite.HarnessFailure, "different backing"),
            ):
                suite.cleanup_pool()
            self.assertFalse(any(argv[0] == "umount" for argv in calls))
            self.assertFalse(any("-d" in argv for argv in calls))

    def test_owned_tool_removal_failure_is_not_swallowed(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        container_id = "d" * 64
        with (
            patch.object(suite, "own_container", return_value=container_id),
            patch.object(
                run_suite,
                "safe_call",
                return_value=subprocess.CompletedProcess([], 0, b"ok", b""),
            ),
            patch.object(
                suite,
                "remove_owned_container",
                side_effect=run_suite.HarnessFailure("cleanup failure"),
            ),
            self.assertRaisesRegex(run_suite.HarnessFailure, "cleanup failure"),
        ):
            suite.run_owned_tool("sha256:image", ["true"], name_prefix="test-tool")

    def test_hub_and_lab_term_timeout_prevents_removal(self) -> None:
        container_id = "e" * 64
        for service in ("hub", "lab"):
            with self.subTest(service=service):
                suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
                suite.inspect_container = running_inspector(suite.run_id)
                calls: list[list[str]] = []
                suite.docker = docker_call_logger(calls)
                with (
                    patch.object(run_suite.time, "monotonic", side_effect=[0.0, 61.0]),
                    patch.object(run_suite.time, "sleep"),
                    self.assertRaisesRegex(run_suite.HarnessFailure, "bounded TERM"),
                ):
                    suite.remove_owned_container(container_id)
                self.assertTrue(
                    any(call[:2] == ["kill", "--signal=TERM"] for call in calls)
                )
                self.assertFalse(any(call[0] == "rm" for call in calls))


if __name__ == "__main__":
    unittest.main()
