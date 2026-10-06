"""Portable, daemon-free contracts for the test-only validation binding."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.integration import run_suite, source_binding

ROOT = Path(__file__).resolve().parents[2]
VALIDATION_SHA = "a" * 40


class SourceBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lean = {
            name: "100644 blob " + "1" * 40
            for name in source_binding.APPLICATION_INPUTS
        }
        self.validation = self.lean.copy()
        self.baseline = {"hub": "baseline-hub-tree"}
        self.dirty = b""
        self.untracked = b""

    def git(self, workspace: Path, *args: str) -> bytes:
        if args == ("rev-parse", "HEAD"):
            return VALIDATION_SHA.encode()
        if args[:2] == ("rev-parse", "--verify"):
            return args[2].split("^")[0].encode()
        if args[0] == "diff":
            return self.dirty
        if args[0] == "ls-files":
            return self.untracked
        raise AssertionError("unexpected Git operation")

    def objects(self, workspace: Path, reference: str) -> dict[str, str]:
        return {
            VALIDATION_SHA: self.validation,
            source_binding.LEAN_SOURCE_REF: self.lean,
            source_binding.BASELINE_SOURCE_REF: self.baseline,
        }[reference]

    def bind(self, reference: str | None = source_binding.LEAN_SOURCE_REF) -> dict:
        with (
            patch.object(source_binding, "git", self.git),
            patch.object(source_binding, "input_objects", self.objects),
        ):
            return source_binding.bind_source(ROOT, reference)

    def test_exact_required_ref_and_separate_source_identities(self) -> None:
        for reference in (None, "HEAD", "b" * 40, source_binding.LEAN_SOURCE_REF[:7]):
            with (
                self.subTest(reference=reference),
                self.assertRaises(source_binding.SourceBindingFailure),
            ):
                self.bind(reference)
        evidence = self.bind()
        self.assertEqual(evidence["lean"], source_binding.LEAN_SOURCE_REF)
        self.assertEqual(evidence["baseline"], source_binding.BASELINE_SOURCE_REF)
        self.assertEqual(evidence["validation"], VALIDATION_SHA)
        self.assertEqual(evidence["manual_readme_procedure"], "not-rehearsed")
        self.assertEqual(evidence["baseline_root_dockerignore"], "identity-absence")

    def test_each_complete_tree_or_root_blob_mismatch_fails(self) -> None:
        for name in source_binding.APPLICATION_INPUTS:
            with self.subTest(path=name):
                self.validation[name] = "different-Git-object"
                with self.assertRaisesRegex(
                    source_binding.SourceBindingFailure, "binding-mismatch"
                ):
                    self.bind()
                self.validation[name] = self.lean[name]

    def test_missing_input_dirty_input_and_untracked_input_fail(self) -> None:
        self.lean.pop(".dockerignore")
        with self.assertRaisesRegex(
            source_binding.SourceBindingFailure, "input-missing"
        ):
            self.bind()
        self.lean[".dockerignore"] = self.validation[".dockerignore"]
        self.dirty = b"hub/jupyterhub_config.py\n"
        with self.assertRaisesRegex(source_binding.SourceBindingFailure, "not-frozen"):
            self.bind()
        self.dirty = b""
        self.untracked = b"web/extra-build-input\n"
        with self.assertRaisesRegex(source_binding.SourceBindingFailure, "untracked"):
            self.bind()

    def test_existing_baseline_ignore_not_silently_skipped(self) -> None:
        self.baseline[".dockerignore"] = "baseline-ignore-blob"
        self.assertEqual(
            self.bind()["baseline_root_dockerignore"], "baseline-ignore-blob"
        )

    def test_preflight_binding_failure_precedes_all_docker_calls(self) -> None:
        suite = run_suite.Suite(ROOT, run_suite.BASELINE_SHA)
        with (
            patch.object(
                run_suite,
                "bind_source",
                side_effect=source_binding.SourceBindingFailure(
                    "application-source-binding-mismatch"
                ),
            ),
            patch.object(suite, "docker") as docker,
        ):
            with self.assertRaises(source_binding.SourceBindingFailure):
                suite.preflight()
            docker.assert_not_called()

    def test_build_rechecks_before_source_archive_or_docker(self) -> None:
        suite = run_suite.Suite(ROOT, run_suite.BASELINE_SHA)
        with (
            patch.object(
                run_suite, "bind_source", return_value={"validation": "changed"}
            ),
            patch.object(suite, "archive_source") as archive,
            patch.object(suite, "docker") as docker,
        ):
            with self.assertRaisesRegex(
                run_suite.HarnessFailure, "changed before image build"
            ):
                suite.build_images()
            archive.assert_not_called()
            docker.assert_not_called()

    def test_real_git_objects_cover_full_application_trees(self) -> None:
        frozen = source_binding.input_objects(ROOT, source_binding.LEAN_SOURCE_REF)
        self.assertEqual(set(frozen), set(source_binding.APPLICATION_INPUTS))
        for tree in ("hub", "lab", "web"):
            self.assertTrue(frozen[tree].startswith("040000 tree "))
        validation = source_binding.git(ROOT, "rev-parse", "HEAD").decode().strip()
        actual = source_binding.input_objects(ROOT, validation)
        if actual != frozen:
            # Parent's unstaged copies intentionally cannot satisfy HEAD binding.
            with self.assertRaisesRegex(
                source_binding.SourceBindingFailure, "binding-mismatch"
            ):
                source_binding.bind_source(ROOT, source_binding.LEAN_SOURCE_REF)
        else:
            self.assertEqual(
                source_binding.bind_source(ROOT, source_binding.LEAN_SOURCE_REF)[
                    "validation"
                ],
                validation,
            )

    def test_fixture_only_lab_profile_network_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source/candidate"
            source.mkdir(parents=True)
            original = (ROOT / "compose.yaml").read_bytes()
            (source / "compose.yaml").write_bytes(original)
            pool = root / "pool"
            pool.mkdir()
            suite = run_suite.Suite(ROOT, run_suite.BASELINE_SHA)
            suite.run_root, suite.pool = root, pool
            suite.image_refs = {
                name: "fixture-" + name
                for name in (
                    "web",
                    "proxy",
                    "old_hub",
                    "old_lab",
                    "candidate_hub",
                    "candidate_lab",
                )
            }
            suite.write_compose_fixture()
            assert suite.compose and suite.candidate_override and suite.initial_override
            self.assertTrue(suite.compose.read_bytes() == original)
            for path in (suite.initial_override, suite.candidate_override):
                services = json.loads(path.read_text())["services"]
                self.assertEqual(services["lab"]["networks"], ["users"])
                for name in ("hub", "web", "proxy"):
                    self.assertNotIn("networks", services[name])
            command_source = (ROOT / "scripts/integration/run_suite.py").read_text()
            self.assertNotIn('"up", "lab"', command_source)
            self.assertNotIn('"up", "--profile", "images"', command_source)

    def test_validation_workflow_scoped_and_source_bound(self) -> None:
        workflow = (ROOT / ".github/workflows/jupyterhub-migration.yaml").read_text()
        self.assertIn("jupyterhub-6-validation-runtime-approved", workflow)
        self.assertNotIn("'jupyterhub-migration-runtime'", workflow)
        self.assertIn("github.head_ref == 'jupyterhub-6-validation'", workflow)
        self.assertIn("inputs.disposable_runner_approved == 'APPROVED'", workflow)
        self.assertEqual(
            workflow.count("python3 -m scripts.integration.source_binding"), 3
        )
        self.assertEqual(workflow.count("persist-credentials: false"), 3)
        self.assertIn("NOT manual README rehearsal", workflow)


if __name__ == "__main__":
    unittest.main()
