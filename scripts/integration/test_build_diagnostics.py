"""Daemon-free build capture, privacy, cancellation and ownership contracts."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts.integration import build_diagnostics as diagnostics
from scripts.integration import run_suite


class BuildDiagnosticsTests(unittest.TestCase):
    def test_parser_fragmentation_and_frozen_unnamed_stage(self):
        drain = diagnostics.Drain(1024)
        prefix = bytearray()
        drain.consume(b"#12 [stage-", prefix)
        drain.consume(b"1 3/7] RUN secret=never-public\n", prefix)
        self.assertEqual((drain.vertex, drain.step, drain.stage), (12, 3, "stage-1"))
        drain.consume(b"#13 [secret-stage 1/1] text\n", prefix)
        self.assertEqual(drain.vertex, 12)
        drain.consume(b"x" * 8192, prefix)
        self.assertLessEqual(len(prefix), diagnostics.PREFIX_CAP)

    def test_log_write_failure_still_drains(self):
        class FailedLog:
            def write(self, _data):
                raise OSError("secret-message")

            def close(self):
                pass

        drain = diagnostics.Drain(1024)
        drain.run(io.BytesIO(b"x" * 65536), FailedLog())
        self.assertTrue(drain.failed)
        self.assertEqual(drain.read, 65536)
        self.assertEqual(drain.stored, 0)

    def child(self, root: Path, source: str, *, timeout: float = 5, cap: int = 1024):
        return diagnostics.run_build(
            [sys.executable, "-u", "-c", source],
            cwd=root,
            log_path=root / "build.private.log",
            timeout=timeout,
            cap=cap,
        )

    def test_secret_output_only_private_and_schema_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.child(root, "print('#7 [jupyter 2/4] RUN secret-token=xyz')")
            self.assertEqual(result.diagnostics["classification"], "success")
            self.assertEqual(result.diagnostics["vertex"], 7)
            self.assertEqual(result.diagnostics["stage"], "jupyter")
            self.assertNotIn("secret", json.dumps(result.diagnostics))
            self.assertIn(b"secret", (root / "build.private.log").read_bytes())
            with self.assertRaises(ValueError):
                diagnostics.validate({**result.diagnostics, "output": "secret"})

    def test_large_no_newline_output_drained_after_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.child(root, "import os; os.write(1,b'x'*(3*1024*1024))")
            self.assertEqual(result.diagnostics["classification"], "success")
            self.assertEqual(result.diagnostics["bytes_read"], 3 * 1024 * 1024)
            self.assertEqual((root / "build.private.log").stat().st_size, 1024)
            self.assertIsNone(result.diagnostics["vertex"])

    def test_nonzero_no_text_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.child(
                Path(directory), "print('network OOM secret'); raise SystemExit(7)"
            )
            self.assertEqual(result.diagnostics["classification"], "nonzero")
            self.assertEqual(result.diagnostics["returncode"], 7)

    def test_timeout_preserves_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.child(
                root,
                "import time; print('#2 [final 1/2] secret'); time.sleep(60)",
                timeout=0.7,
            )
            self.assertEqual(result.diagnostics["classification"], "timeout")
            self.assertTrue(result.diagnostics["client_reaped"])
            self.assertTrue(result.diagnostics["reader_complete"])
            self.assertEqual(result.diagnostics["vertex"], 2)
            self.assertLess(result.diagnostics["elapsed_ms"], 6000)

    def test_launch_failure_and_exclusive_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = diagnostics.run_build(
                [str(root / "absent")], cwd=root, log_path=root / "log", timeout=1
            )
            self.assertFalse(result.launched)
            self.assertEqual(result.diagnostics["classification"], "launch-failure")
            result = diagnostics.run_build(
                [sys.executable], cwd=root, log_path=root / "log", timeout=1
            )
            self.assertFalse(result.launched)
            self.assertEqual(result.diagnostics["classification"], "log-write-failure")

    @unittest.skipUnless(
        sys.platform == "linux", "Linux group and permissions contract"
    )
    def test_term_resistant_group_and_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pidfile = root / "grandchild.pid"
            grand = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
            source = (
                "import signal,subprocess,sys,time; "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                f"p=subprocess.Popen([sys.executable,'-c',{grand!r}]); "
                f"open({str(pidfile)!r},'w').write(str(p.pid)); "
                "print('#3 [builder 1/1] private'); time.sleep(60)"
            )
            try:
                result = self.child(root, source, timeout=1)
                self.assertTrue(result.diagnostics["reader_complete"])
                self.assertEqual(result.diagnostics["classification"], "timeout")
                self.assertEqual(
                    (root / "build.private.log").stat().st_mode & 0o777, 0o600
                )
                pid = int(pidfile.read_text())
                status = Path(f"/proc/{pid}/stat")
                self.assertTrue(
                    not status.exists() or status.read_text().split()[2] == "Z"
                )
            finally:
                self.kill_recorded(pidfile)

    @staticmethod
    def kill_recorded(pidfile: Path):
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    @unittest.skipUnless(
        sys.platform == "linux", "Linux escaped inherited pipe contract"
    )
    def test_escaped_pipe_holder_bounded_return_and_frozen_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pidfile = root / "escaped.pid"
            source = (
                "import subprocess,sys; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                f"open({str(pidfile)!r},'w').write(str(p.pid)); print('#4 [final 1/1] secret')"
            )
            try:
                started = time.monotonic()
                result = self.child(root, source)
                snapshot = json.dumps(result.diagnostics, sort_keys=True)
                self.assertEqual(
                    result.diagnostics["classification"], "reader-incomplete"
                )
                self.assertFalse(result.diagnostics["reader_complete"])
                self.assertLess(time.monotonic() - started, 6)
                self.kill_recorded(pidfile)
                self.assertEqual(
                    json.dumps(result.diagnostics, sort_keys=True), snapshot
                )
            finally:
                self.kill_recorded(pidfile)


class BuildOwnershipTests(unittest.TestCase):
    @staticmethod
    def outcome(classification="success"):
        value = dict.fromkeys(diagnostics.KEYS)
        value.update(
            classification=classification,
            elapsed_ms=1,
            bytes_read=0,
            bytes_stored=0,
            cap_bytes=diagnostics.LOG_CAP,
            reader_complete=True,
            client_reaped=True,
            returncode=0,
        )
        return diagnostics.BuildResult(diagnostics.validate(value), True)

    def test_build_success_uses_progress_and_durable_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, tag = self.fixture(Path(directory))
            suite.manifest["build_intents"] = []
            identity = "sha256:" + "a" * 64
            help_result = subprocess.CompletedProcess([], 0, b"--progress")

            def build(argv, **kwargs):
                self.assertIn("--progress=plain", argv)
                self.assertNotIn("--quiet", argv)
                self.assertEqual(kwargs["timeout"], 1200)
                intent = json.loads(suite.marker.read_text())["build_intents"][0]
                self.assertFalse(intent["resolved"])
                self.assertTrue(intent["absent_before_launch"])
                return self.outcome()

            with (
                patch.object(run_suite, "safe_call", return_value=help_result),
                patch.object(suite, "exact_build_tag", return_value=None),
                patch.object(run_suite, "run_build", side_effect=build),
                patch.object(suite, "operation_image_id", return_value=identity),
            ):
                self.assertEqual(
                    suite.build(
                        Path(directory), "Dockerfile", tag, role="baseline-lab-base"
                    ),
                    identity,
                )
            self.assertTrue(suite.manifest["build_intents"][0]["resolved"])
            self.assertEqual(suite.owned_image_refs[tag], identity)

    def test_build_timeout_does_not_register_success_or_resolve_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, tag = self.fixture(Path(directory))
            suite.manifest["build_intents"] = []
            suite.build_progress_supported = True
            with (
                patch.object(suite, "exact_build_tag", return_value=None),
                patch.object(
                    run_suite, "run_build", return_value=self.outcome("timeout")
                ),
                patch.object(suite, "operation_image_id") as inspect,
            ):
                with self.assertRaises(run_suite.HarnessFailure):
                    suite.build(
                        Path(directory), "Dockerfile", tag, role="baseline-lab-base"
                    )
                inspect.assert_not_called()
            self.assertEqual(suite.failure_context["classification"], "timeout")
            self.assertFalse(suite.manifest["build_intents"][0]["resolved"])
            self.assertEqual(suite.owned_image_refs, {})

    def test_help_unsupported_never_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, tag = self.fixture(Path(directory))
            with (
                patch.object(
                    run_suite,
                    "safe_call",
                    return_value=subprocess.CompletedProcess([], 0, b""),
                ),
                patch.object(run_suite, "run_build") as launch,
            ):
                with self.assertRaisesRegex(run_suite.HarnessFailure, "unsupported"):
                    suite.build(
                        Path(directory), "Dockerfile", tag, role="baseline-lab-base"
                    )
                launch.assert_not_called()

    def fixture(self, root: Path):
        suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
        suite.run_root = root
        suite.created_run_root = True
        suite.marker = root / "manifest.json"
        tag = f"local/jh6/test:{suite.run_id}"
        suite.manifest["build_intents"] = [
            {
                "ref": tag,
                "role": "baseline-lab-base",
                "absent_before_launch": True,
                "id": None,
                "resolved": False,
            }
        ]
        suite.save_manifest()
        return suite, tag

    def test_inventory_error_is_not_absence(self):
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        with patch.object(
            run_suite, "safe_call", return_value=subprocess.CompletedProcess([], 1, b"")
        ):
            with self.assertRaises(run_suite.HarnessFailure):
                suite.exact_build_tag("local/jh6/test:unique")

    def test_late_exact_tag_recorded_but_absence_does_not_resolve_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, tag = self.fixture(Path(directory))
            identity = "sha256:" + "a" * 64
            with patch.object(suite, "exact_build_tag", return_value=identity):
                suite.reconcile_build_intents()
            self.assertEqual(suite.owned_image_refs[tag], identity)
            with patch.object(suite, "exact_build_tag", return_value=None):
                suite.reconcile_build_intents()
            self.assertEqual(
                suite.results["build_cancellation"]["unresolved_intents"], 1
            )
            with self.assertRaisesRegex(run_suite.HarnessFailure, "unresolved daemon"):
                suite.cleanup_run_root()

    def test_identity_mismatch_never_deletes_or_adopts(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, _tag = self.fixture(Path(directory))
            suite.manifest["build_intents"][0]["id"] = "sha256:" + "a" * 64
            with (
                patch.object(
                    suite, "exact_build_tag", return_value="sha256:" + "b" * 64
                ),
                patch.object(run_suite, "safe_call") as docker,
            ):
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure, "identity mismatch"
                ):
                    suite.reconcile_build_intents()
                docker.assert_not_called()
            self.assertEqual(suite.owned_image_refs, {})

    def test_unresolved_reader_preserves_root(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, _tag = self.fixture(Path(directory))
            suite.manifest["build_intents"][0]["resolved"] = True
            suite.results["build_diagnostics"] = {"reader_complete": False}
            with self.assertRaisesRegex(
                run_suite.HarnessFailure, "private build reader"
            ):
                suite.cleanup_run_root()
            self.assertTrue(suite.marker.exists())

    def test_failed_build_still_cleans_independent_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, _tag = self.fixture(Path(directory))
            methods = (
                "cleanup_compose",
                "cleanup_dynamic_labs",
                "cleanup_owned_tools",
                "cleanup_registry",
                "cleanup_network",
                "cleanup_pool",
                "remove_run_images",
            )
            with ExitStack() as stack:
                mocks = [
                    stack.enter_context(patch.object(suite, name)) for name in methods
                ]
                stack.enter_context(
                    patch.object(suite, "exact_build_tag", return_value=None)
                )
                failures = suite.cleanup()
            self.assertEqual(failures, ["run-root-preserved"])
            for mock in mocks:
                mock.assert_called_once()
            self.assertTrue(suite.marker.exists())
            self.assertFalse(
                suite.results["build_cancellation"]["daemon_completion_proven"]
            )

    def test_foreign_intent_never_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            suite, _tag = self.fixture(Path(directory))
            suite.manifest["build_intents"][0]["absent_before_launch"] = False
            with patch.object(suite, "exact_build_tag") as inventory:
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure, "ownership mismatch"
                ):
                    suite.reconcile_build_intents()
                inventory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
