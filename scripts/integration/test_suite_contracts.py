"""Static/nonprivileged safeguards for the full runtime orchestrator."""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import jupyterhub_maintenance as maintenance
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


def configured_suite(root: Path) -> run_suite.Suite:
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
    suite.image_ids.update(
        {
            "web": "sha256:" + "a" * 64,
            "proxy": "sha256:" + "b" * 64,
            "old_hub": "sha256:" + "c" * 64,
            "candidate_hub": "sha256:" + "d" * 64,
        }
    )
    return suite


def fixture_lab_cleanup_record(suite: run_suite.Suite, container_id: str, home: Path):
    record = {
        "id": container_id,
        "name": "lab-user-a",
        "kind": "fixture-lab",
        "label": suite.run_id,
        "user": "user-a",
        "image_id": "sha256:" + "a" * 64,
        "home_source": str(home.resolve()),
        "auto_remove": True,
        "removed": False,
    }
    suite.manifest["resources"]["containers"].append(record)
    return record


def fixture_lab_cleanup_inspect(
    suite: run_suite.Suite,
    container_id: str,
    home: Path,
    *,
    running: bool = True,
):
    return {
        "Id": container_id,
        "Image": "sha256:" + "a" * 64,
        "Name": "/lab-user-a",
        "State": {"Running": running},
        "HostConfig": {"AutoRemove": True},
        "Config": {
            "Labels": {
                run_suite.RUN_LABEL: suite.run_id,
                "finki.role": "lab",
                "finki.user": "user-a",
            }
        },
        "Mounts": [{"Destination": "/home/ubuntu", "Source": str(home.resolve())}],
    }


def owned_network_cleanup_record(suite: run_suite.Suite, network_id: str):
    record = {
        "id": network_id,
        "name": run_suite.NETWORK_NAME,
        "run_label": suite.run_id,
        "purpose": "isolated-users-network",
        "removed": False,
        "intent": False,
    }
    suite.network_id = network_id
    suite.manifest["resources"]["networks"].append(record)
    return record


def owned_network_cleanup_inspect(
    suite: run_suite.Suite, network_id: str, *, endpoints=None
):
    return {
        "Id": network_id,
        "Name": run_suite.NETWORK_NAME,
        "Driver": "bridge",
        "Internal": True,
        "Labels": {run_suite.RUN_LABEL: suite.run_id},
        "IPAM": {"Config": [{"Subnet": "172.30.0.0/23", "Gateway": "172.30.0.1"}]},
        "Options": {
            "com.docker.network.bridge.enable_icc": "false",
            "com.docker.network.bridge.name": run_suite.BRIDGE_NAME,
        },
        "Containers": {} if endpoints is None else endpoints,
    }


def run_xfs_setup_case(root: Path, safe_call, *, fail_save_at: int | None = None):
    suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
    root.mkdir(parents=True, exist_ok=True)
    run_root = root / "run"
    pool = run_root / "pool"
    run_root.mkdir()
    pool.mkdir()
    suite.run_root = run_root
    suite.pool = pool
    suite.marker = run_root / "owner.json"
    suite.created_run_root = True
    suite.preflight = lambda: None
    suite.create_run_root = lambda: None
    suite.cleanup = lambda: []

    def fake_require_call(argv, **_kwargs):
        if argv[0] == "fallocate":
            Path(argv[-1]).write_bytes(b"synthetic-xfs-image")
        return b""

    original_save = suite.save_manifest
    save_calls = 0

    def save_manifest():
        nonlocal save_calls
        save_calls += 1
        if fail_save_at == save_calls:
            raise OSError(errno.ENOSPC, "synthetic-secret-save-failure", "hidden-path")
        original_save()

    suite.save_manifest = save_manifest
    stdout = io.StringIO()
    with (
        patch.object(run_suite, "require_call", side_effect=fake_require_call),
        patch.object(run_suite, "safe_call", side_effect=safe_call),
        patch("sys.stdout", stdout),
    ):
        exit_code = suite.run()
    return exit_code, json.loads(stdout.getvalue()), suite


def quota_test_suite(root: Path) -> tuple[run_suite.Suite, Path]:
    root.mkdir(parents=True, exist_ok=True)
    pool = root / "pool"
    pool.mkdir()
    projects = pool / ".projects"
    projects.write_text("1000:/pool/users\n", encoding="utf-8")
    suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
    suite.pool = pool
    return suite, projects


def quota_report(*, inode: bool, block_limit: int = 16_384) -> str:
    hard = 1000 if inode else block_limit
    used = 1 if inode else 2048
    return (
        f"Project ID Used Soft Hard Warn/Grace\n#9999 {used} 0 {hard} 00 [--------]\n"
    )


def quota_guard_patches(suite: run_suite.Suite):
    pool = suite.pool
    assert pool is not None
    scratch = pool / ".integration-quota-probe"

    def attrs(path: Path):
        return (
            (
                run_suite.QUOTA_PROJECT_ID,
                run_suite.FS_XFLAG_PROJINHERIT,
            )
            if path == scratch
            else (0, 0)
        )

    return (
        patch.object(suite, "inode_xfs_attributes", side_effect=attrs),
        patch.object(
            suite,
            "inode_xfs_attributes_fd",
            return_value=(run_suite.QUOTA_PROJECT_ID, 0),
        ),
        patch.object(
            suite,
            "filesystem_capacity",
            return_value=(1024 * 1024 * 1024, 10_000),
        ),
    )


@contextlib.contextmanager
def patch_quota_guards(suite: run_suite.Suite):
    with contextlib.ExitStack() as stack:
        for patcher in quota_guard_patches(suite):
            stack.enter_context(patcher)
        yield


def successful_quota_commands(*, block_used: int = 2048, inode_used: int = 1):
    block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB

    def command(command: str, operation: str, **_kwargs):
        nonlocal block_limit
        if operation == "quota-limit":
            if "bhard=32m" in command:
                block_limit = run_suite.QUOTA_RELIEF_BLOCK_LIMIT_KIB
            elif "bhard=16m" in command:
                block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB
        if operation == "quota-report":
            hard = 1000 if "-i" in command else block_limit
            used = inode_used if "-i" in command else block_used
            return (
                "Project ID Used Soft Hard Warn/Grace\n"
                f"#9999 {used} 0 {hard} 00 [--------]\n"
            )
        if operation in {"quota-state", "quota-post-state"}:
            return XFS_PROJECT_QUOTA_ON
        return ""

    return command


XFS_PROJECT_QUOTA_ON = """User quota state on fixture (/dev/loop0)
\tAccounting: ON
\tEnforcement: ON
Project quota state on fixture (/dev/loop0)
\tAccounting: ON
\tEnforcement:\tON
\tInode: #45 (2 blocks, 2 extents)
"""


def isolated_os_proxy(**overrides):
    proxy = SimpleNamespace(**vars(os))
    for name, value in overrides.items():
        setattr(proxy, name, value)
    return proxy


@contextlib.contextmanager
def patch_quota_probe_io(
    write_probe: Callable[[int, bytes], int],
    fsync_probe: Callable[[int], None],
) -> Iterator[None]:
    original_open = run_suite.os.open
    original_write = run_suite.os.write
    original_fsync = run_suite.os.fsync
    original_close = run_suite.os.close
    probe_fds: set[int] = set()

    def tracked_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        candidate = Path(os.fspath(path))
        if (
            candidate.name == "probe.bin"
            and candidate.parent.name == ".integration-quota-probe"
        ):
            probe_fds.add(fd)
        return fd

    def scoped_write(fd: int, payload: bytes) -> int:
        if fd in probe_fds:
            return write_probe(fd, payload)
        return original_write(fd, payload)

    def scoped_fsync(fd: int) -> None:
        if fd in probe_fds:
            fsync_probe(fd)
            return
        original_fsync(fd)

    def scoped_close(fd: int) -> None:
        try:
            original_close(fd)
        finally:
            probe_fds.discard(fd)

    os_proxy = isolated_os_proxy(
        open=tracked_open,
        write=scoped_write,
        fsync=scoped_fsync,
        close=scoped_close,
    )
    with patch.object(run_suite, "os", os_proxy):
        yield


class SuiteContractTests(unittest.TestCase):
    def test_quota_io_mocks_forward_unrelated_descriptors_to_the_os(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ordinary-file"

            def unexpected_probe_write(_fd, _payload):
                self.fail("ordinary file descriptor was treated as the quota probe")

            def unexpected_probe_fsync(_fd):
                self.fail("ordinary file descriptor was treated as the quota probe")

            with patch_quota_probe_io(unexpected_probe_write, unexpected_probe_fsync):
                fd = run_suite.os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                try:
                    run_suite.os.write(fd, b"forwarded-to-operating-system")
                    run_suite.os.fsync(fd)
                finally:
                    run_suite.os.close(fd)

            self.assertEqual(path.read_bytes(), b"forwarded-to-operating-system")

    def test_workflow_gates_full_runtime_on_sanitized_xfs_preflight(self) -> None:
        workflow = Path(".github/workflows/jupyterhub-migration.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("  xfs-preflight:\n    needs: checks", workflow)
        self.assertIn("timeout-minutes: 15", workflow)
        self.assertIn("--xfs-only", workflow)
        self.assertIn("needs: [checks, xfs-preflight]", workflow)
        self.assertIn("needs.xfs-preflight.result == 'success'", workflow)
        self.assertIn(
            "uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            workflow,
        )
        self.assertIn("jupyterhub-xfs-preflight.json", workflow)
        self.assertEqual(
            workflow.count(
                "contains(github.event.pull_request.labels.*.name, 'jupyterhub-migration-runtime')"
            ),
            2,
        )

    def test_project_quota_state_parser_uses_project_section_and_tolerates_spacing(
        self,
    ) -> None:
        self.assertIsNone(
            run_suite.Suite.project_quota_state_classification(XFS_PROJECT_QUOTA_ON)
        )
        user_only = "User quota state on fixture\n Accounting: ON\n Enforcement: ON\n"
        self.assertEqual(
            run_suite.Suite.project_quota_state_classification(user_only),
            "project-state-missing",
        )
        accounting_off = XFS_PROJECT_QUOTA_ON.replace(
            "Project quota state on fixture (/dev/loop0)\n\tAccounting: ON",
            "Project quota state on fixture (/dev/loop0)\n\tAccounting: OFF",
        )
        self.assertEqual(
            run_suite.Suite.project_quota_state_classification(accounting_off),
            "project-accounting-disabled",
        )
        enforcement_off = XFS_PROJECT_QUOTA_ON.replace(
            "\tEnforcement:\tON", "\tEnforcement:\tOFF"
        )
        self.assertEqual(
            run_suite.Suite.project_quota_state_classification(enforcement_off),
            "project-enforcement-disabled",
        )

    def test_quota_report_parser_requires_exact_numeric_row_and_known_columns(
        self,
    ) -> None:
        fixture = (
            "Project ID Used Soft Hard Warn/Grace\n"
            "#999 12 0 4096 00 [--------]\n"
            "#9999 2048 0 16384 00 [--------]\n"
        )
        self.assertEqual(
            run_suite.Suite.parse_quota_report(fixture, 9999), (2048, 16384)
        )
        invalid_reports = (
            "Project ID Used Soft Hard Warn/Grace\n#9999xyz 2 0 16384 00 [--------]\n",
            fixture + "9999 1 0 16384 00 [--------]\n",
            "Project ID Used Soft Hard Warn/Grace\n#9999 2 broken 16384\n",
            "#9999 2 0 16384\n",
            "Project ID Used Soft Hard Warn/Grace\n#999 2 0 16384 00 [--------]\n",
        )
        for report in invalid_reports:
            with self.subTest(report=report):
                with self.assertRaises(run_suite.HarnessFailure):
                    run_suite.Suite.parse_quota_report(report, 9999)

    def test_post_denial_block_usage_may_equal_hard_limit_only(self) -> None:
        scenarios = (
            ("post-equal", (2048, 16_384, 16_384), True, None),
            ("post-over", (2048, 16_385), False, "quota-usage-unsafe"),
            ("initial-equal", (16_384,), False, "quota-usage-unsafe"),
            ("initial-over", (16_385,), False, "quota-usage-unsafe"),
        )
        for name, block_usages, should_pass, expected_failure in scenarios:
            with (
                self.subTest(case=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                suite, projects = quota_test_suite(Path(directory))
                original = projects.read_text(encoding="utf-8")
                block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB
                report_index = 0
                successful_bytes = 0
                denial_sent = False

                def quota(command, operation, block_usages=block_usages, **_kwargs):
                    nonlocal block_limit, report_index
                    if operation == "quota-limit":
                        if "bhard=32m" in command:
                            block_limit = run_suite.QUOTA_RELIEF_BLOCK_LIMIT_KIB
                        elif "bhard=16m" in command:
                            block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB
                    if operation == "quota-report":
                        if "-i" in command:
                            return (
                                "Project ID Used Soft Hard Warn/Grace\n"
                                "#9999 1 0 1000 00 [--------]\n"
                            )
                        usage = block_usages[min(report_index, len(block_usages) - 1)]
                        report_index += 1
                        return (
                            "Project ID Used Soft Hard Warn/Grace\n"
                            f"#9999 {usage} 0 {block_limit} 00 [--------]\n"
                        )
                    if operation in {"quota-state", "quota-post-state"}:
                        return XFS_PROJECT_QUOTA_ON
                    return ""

                def write(_fd, payload):
                    nonlocal successful_bytes, denial_sent
                    if not denial_sent and successful_bytes >= 16 * 1024 * 1024:
                        denial_sent = True
                        raise OSError(errno.ENOSPC, "synthetic project quota denial")
                    successful_bytes += len(payload)
                    return len(payload)

                with (
                    patch.object(suite, "quota_command", side_effect=quota),
                    patch_quota_guards(suite),
                    patch_quota_probe_io(write, lambda _fd: None),
                ):
                    if should_pass:
                        suite.verify_quota_enforcement()
                    else:
                        with self.assertRaises(run_suite.HarnessFailure):
                            suite.verify_quota_enforcement()

                if should_pass:
                    self.assertIsNone(suite.failure_context)
                    self.assertEqual(
                        suite.quota_evidence["block_used_kib_before_relief"], 16_384
                    )
                    self.assertEqual(
                        suite.quota_evidence["relief_block_hard_kib"], 32_768
                    )
                else:
                    self.assertEqual(
                        suite.failure_context["classification"], expected_failure
                    )
                self.assertEqual(projects.read_text(encoding="utf-8"), original)
                self.assertFalse((suite.pool / ".integration-quota-probe").exists())

    def test_quota_probe_rejects_root_project_and_low_filesystem_capacity(self) -> None:
        cases = (
            (
                "root-project",
                (9999, 0),
                (1024**3, 10_000),
                "project-assignment-mismatch",
            ),
            (
                "root-inheritance",
                (0, run_suite.FS_XFLAG_PROJINHERIT),
                (1024**3, 10_000),
                "project-assignment-mismatch",
            ),
            (
                "low-bytes",
                (0, 0),
                (64 * 1024 * 1024, 10_000),
                "filesystem-capacity-low",
            ),
            ("low-inodes", (0, 0), (1024**3, 10), "filesystem-capacity-low"),
        )
        for name, root_attrs, capacity, classification in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                suite, projects = quota_test_suite(Path(directory))
                original = projects.read_text(encoding="utf-8")
                with (
                    patch.object(
                        suite, "quota_command", return_value=XFS_PROJECT_QUOTA_ON
                    ),
                    patch.object(
                        suite, "inode_xfs_attributes", return_value=root_attrs
                    ),
                    patch.object(suite, "filesystem_capacity", return_value=capacity),
                    self.assertRaises(run_suite.HarnessFailure),
                ):
                    suite.verify_quota_enforcement()
                self.assertEqual(
                    suite.failure_context["classification"], classification
                )
                self.assertEqual(projects.read_text(encoding="utf-8"), original)
                self.assertFalse((suite.pool / ".integration-quota-probe").exists())

    def test_quota_probe_rejects_wrong_scratch_project_or_missing_inheritance(
        self,
    ) -> None:
        for attributes, expected in (
            ((10001, run_suite.FS_XFLAG_PROJINHERIT), "project-assignment-mismatch"),
            ((9999, 0), "project-inheritance-missing"),
        ):
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                suite, projects = quota_test_suite(Path(directory))
                original = projects.read_text(encoding="utf-8")
                scratch = suite.pool / ".integration-quota-probe"

                def xattrs(path, attributes=attributes, scratch=scratch):
                    return attributes if path == scratch else (0, 0)

                with (
                    patch.object(
                        suite, "quota_command", side_effect=successful_quota_commands()
                    ),
                    patch.object(suite, "inode_xfs_attributes", side_effect=xattrs),
                    patch.object(
                        suite, "filesystem_capacity", return_value=(1024**3, 10_000)
                    ),
                    self.assertRaises(run_suite.HarnessFailure),
                ):
                    suite.verify_quota_enforcement()
                self.assertEqual(suite.failure_context["classification"], expected)
                self.assertEqual(projects.read_text(encoding="utf-8"), original)
                self.assertFalse(scratch.exists())

    def test_quota_probe_rejects_wrong_file_project_and_unsafe_usage(self) -> None:
        cases = (
            ("wrong-file-project", 1, 1, "project-assignment-mismatch"),
            ("unexpected-inode-usage", 2048, 3, "quota-usage-unsafe"),
            ("inode-hard-limit-hit", 2048, 1000, "inode-quota-exhausted"),
            ("block-hard-limit-hit", 16384, 1, "quota-usage-unsafe"),
        )
        for name, block_used, inode_used, expected in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                suite, _projects = quota_test_suite(Path(directory))
                with contextlib.ExitStack() as stack:
                    stack.enter_context(
                        patch.object(
                            suite,
                            "quota_command",
                            side_effect=successful_quota_commands(
                                block_used=block_used, inode_used=inode_used
                            ),
                        )
                    )
                    stack.enter_context(patch_quota_guards(suite))
                    if name == "wrong-file-project":
                        stack.enter_context(
                            patch.object(
                                suite,
                                "inode_xfs_attributes_fd",
                                return_value=(10001, 0),
                            )
                        )
                    with self.assertRaises(run_suite.HarnessFailure):
                        suite.verify_quota_enforcement()
                self.assertEqual(suite.failure_context["classification"], expected)

    def test_quota_probe_rejects_relief_write_failure_and_preserves_cleanup(
        self,
    ) -> None:
        for relief_failure in ("write", "fsync"):
            with (
                self.subTest(relief_failure=relief_failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                suite, projects = quota_test_suite(Path(directory))
                original = projects.read_text(encoding="utf-8")
                denial_sent = False

                def write(_fd, payload, relief_failure=relief_failure):
                    nonlocal denial_sent
                    if not denial_sent:
                        denial_sent = True
                        raise OSError(errno.ENOSPC, "synthetic guarded quota denial")
                    if relief_failure == "write":
                        raise OSError(errno.ENOSPC, "synthetic relief failure")
                    return len(payload)

                def fsync(_fd, relief_failure=relief_failure):
                    if relief_failure == "fsync":
                        raise OSError(errno.ENOSPC, "synthetic relief failure")

                with (
                    patch.object(
                        suite, "quota_command", side_effect=successful_quota_commands()
                    ),
                    patch_quota_guards(suite),
                    patch_quota_probe_io(write, fsync),
                    self.assertRaises(run_suite.HarnessFailure),
                ):
                    suite.verify_quota_enforcement()

                self.assertEqual(
                    suite.failure_context["classification"], "quota-relief-failed"
                )
                self.assertEqual(projects.read_text(encoding="utf-8"), original)
                self.assertFalse((suite.pool / ".integration-quota-probe").exists())

    def test_quota_probe_counts_partial_writes_within_aggregate_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, _projects = quota_test_suite(Path(directory))
            successful = 0
            denial_sent = False

            def write(_fd, payload):
                nonlocal denial_sent, successful
                if not denial_sent and successful >= 16 * 1024 * 1024:
                    denial_sent = True
                    raise OSError(errno.EDQUOT, "synthetic quota denial")
                count = min(len(payload), 4096)
                successful += count
                return count

            with (
                patch.object(
                    suite, "quota_command", side_effect=successful_quota_commands()
                ),
                patch_quota_guards(suite),
                patch_quota_probe_io(write, lambda _fd: None),
            ):
                suite.verify_quota_enforcement()

            self.assertEqual(successful, 17 * 1024 * 1024)
            self.assertLessEqual(
                suite.quota_evidence["successful_bytes_before_denial"]
                + suite.quota_evidence["relief_bytes"],
                run_suite.QUOTA_WRITE_BUDGET,
            )

    def test_quota_probe_rejects_zero_progress_and_wrong_reported_limit(self) -> None:
        for case in ("zero-progress", "wrong-limit"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                suite, projects = quota_test_suite(Path(directory))
                original = projects.read_text(encoding="utf-8")
                commands = successful_quota_commands()

                def quota(command, operation, case=case, commands=commands, **kwargs):
                    if case == "wrong-limit" and operation == "quota-report":
                        hard = 999 if "-i" in command else 16_383
                        used = 1
                        return (
                            "Project ID Used Soft Hard Warn/Grace\n"
                            f"#9999 {used} 0 {hard} 00 [--------]\n"
                        )
                    return commands(command, operation, **kwargs)

                with (
                    patch.object(suite, "quota_command", side_effect=quota),
                    patch_quota_guards(suite),
                    patch_quota_probe_io(
                        (lambda _fd, _payload: 0)
                        if case == "zero-progress"
                        else (lambda _fd, payload: len(payload)),
                        lambda _fd: None,
                    ),
                    self.assertRaises(run_suite.HarnessFailure),
                ):
                    suite.verify_quota_enforcement()

                expected = (
                    "no-write-progress"
                    if case == "zero-progress"
                    else "quota-limit-mismatch"
                )
                self.assertEqual(suite.failure_context["classification"], expected)
                self.assertEqual(projects.read_text(encoding="utf-8"), original)
                self.assertFalse((suite.pool / ".integration-quota-probe").exists())

    def test_quota_command_detects_diagnostic_even_with_zero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, _projects = quota_test_suite(Path(directory))
            secret = b"Error: project token-secret was not set"
            argv = ["xfs_quota", "-x", "-c", "project -s", str(suite.pool)]
            with patch.object(
                run_suite,
                "safe_call",
                return_value=subprocess.CompletedProcess(argv, 0, b"", secret),
            ):
                with self.assertRaises(run_suite.HarnessFailure):
                    suite.quota_command("project -s", "quota-project")

        self.assertEqual(
            suite.failure_context,
            {
                "operation": "quota-project",
                "classification": "command-reported-error",
                "returncode": 0,
            },
        )
        self.assertNotIn("token-secret", json.dumps(suite.failure_context))

    def test_quota_state_rejects_disabled_project_accounting_and_enforcement(
        self,
    ) -> None:
        states = (
            (
                XFS_PROJECT_QUOTA_ON.replace(
                    "Project quota state on fixture (/dev/loop0)\n\tAccounting: ON",
                    "Project quota state on fixture (/dev/loop0)\n\tAccounting: OFF",
                ),
                "project-accounting-disabled",
            ),
            (
                XFS_PROJECT_QUOTA_ON.replace(
                    "\tEnforcement:\tON", "\tEnforcement:\tOFF"
                ),
                "project-enforcement-disabled",
            ),
        )
        for state, expected in states:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                suite, _projects = quota_test_suite(Path(directory))
                with patch.object(suite, "quota_state", return_value=state):
                    with self.assertRaises(run_suite.HarnessFailure):
                        suite.verify_quota_enforcement()
                self.assertEqual(
                    suite.failure_context,
                    {"operation": "quota-state", "classification": expected},
                )
                self.assertFalse((suite.pool / ".integration-quota-probe").exists())

    def test_quota_probe_accepts_edquot_from_write_or_fsync_and_restores_fixture(
        self,
    ) -> None:
        for edquot_location, denial_error in (
            ("write", errno.EDQUOT),
            ("fsync", errno.EDQUOT),
            ("write", errno.ENOSPC),
            ("fsync", errno.ENOSPC),
        ):
            with (
                self.subTest(edquot_location=edquot_location, errno=denial_error),
                tempfile.TemporaryDirectory() as directory,
            ):
                suite, projects = quota_test_suite(Path(directory))
                original = projects.read_text(encoding="utf-8")
                calls: list[tuple[str, str]] = []
                block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB

                def quota(command, operation, calls=calls, **_kwargs):
                    nonlocal block_limit
                    calls.append((command, operation))
                    if operation == "quota-limit":
                        if "bhard=32m" in command:
                            block_limit = run_suite.QUOTA_RELIEF_BLOCK_LIMIT_KIB
                        elif "bhard=16m" in command:
                            block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB
                    if operation == "quota-report":
                        return quota_report(
                            inode="-i" in command, block_limit=block_limit
                        )
                    return (
                        XFS_PROJECT_QUOTA_ON
                        if operation
                        in {
                            "quota-state",
                            "quota-post-state",
                        }
                        else ""
                    )

                written_total = 0
                denial_sent = False

                def write(
                    _fd,
                    payload,
                    edquot_location=edquot_location,
                    denial_error=denial_error,
                ):
                    nonlocal denial_sent, written_total
                    if (
                        edquot_location == "write"
                        and not denial_sent
                        and written_total >= 16 * 1024 * 1024
                    ):
                        denial_sent = True
                        raise OSError(denial_error, "synthetic quota denial")
                    written_total += len(payload)
                    return len(payload)

                def fsync(
                    _fd,
                    edquot_location=edquot_location,
                    denial_error=denial_error,
                ):
                    nonlocal denial_sent
                    if edquot_location == "fsync" and not denial_sent:
                        denial_sent = True
                        raise OSError(denial_error, "synthetic quota denial")

                with (
                    patch.object(suite, "quota_command", side_effect=quota),
                    patch_quota_guards(suite),
                    patch_quota_probe_io(write, fsync),
                ):
                    suite.verify_quota_enforcement()

                self.assertEqual(projects.read_text(encoding="utf-8"), original)
                self.assertFalse((suite.pool / ".integration-quota-probe").exists())
                self.assertEqual(suite.failure_context, None)
                self.assertEqual(suite.cleanup_failure_context, None)
                self.assertEqual(
                    suite.quota_evidence["classification"],
                    "project-quota-enforced",
                )
                self.assertEqual(suite.quota_evidence["errno"], denial_error)
                self.assertGreaterEqual(
                    suite.quota_evidence["relief_bytes"], 1024 * 1024
                )
                self.assertEqual(
                    suite.quota_evidence["relief_block_hard_kib"], 32 * 1024
                )
                self.assertIn(
                    ("limit -p bhard=16m ihard=1000 9999", "quota-limit"), calls
                )
                self.assertIn(
                    ("limit -p bhard=0 ihard=0 9999", "quota-reset-limit"), calls
                )

    def test_quota_probe_fails_if_write_and_fsync_never_report_edquot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, projects = quota_test_suite(Path(directory))
            original = projects.read_text(encoding="utf-8")

            def quota(_command, operation, **_kwargs):
                if operation == "quota-report":
                    return quota_report(inode="-i" in _command)
                return (
                    XFS_PROJECT_QUOTA_ON
                    if operation
                    in {
                        "quota-state",
                        "quota-post-state",
                    }
                    else ""
                )

            def write(_fd, data):
                return len(data)

            def fsync(_fd):
                return None

            with (
                patch.object(suite, "quota_command", side_effect=quota),
                patch_quota_guards(suite),
                patch_quota_probe_io(write, fsync),
                self.assertRaises(run_suite.HarnessFailure),
            ):
                suite.verify_quota_enforcement()

            self.assertEqual(
                suite.failure_context,
                {"operation": "quota-write", "classification": "limit-not-enforced"},
            )
            self.assertEqual(projects.read_text(encoding="utf-8"), original)
            self.assertFalse((suite.pool / ".integration-quota-probe").exists())

    def test_post_probe_state_is_checked_once_and_must_remain_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, _projects = quota_test_suite(Path(directory))
            state_calls: list[str] = []
            block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB

            def quota(_command, operation, **_kwargs):
                nonlocal block_limit
                if operation == "quota-limit":
                    if "32m" in _command:
                        block_limit = run_suite.QUOTA_RELIEF_BLOCK_LIMIT_KIB
                    elif "16m" in _command:
                        block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB
                if operation == "quota-report":
                    return quota_report(inode="-i" in _command, block_limit=block_limit)
                if operation in {"quota-state", "quota-post-state"}:
                    state_calls.append(operation)
                    if operation == "quota-post-state":
                        return XFS_PROJECT_QUOTA_ON.replace(
                            "\tEnforcement:\tON", "\tEnforcement:\tOFF"
                        )
                    return XFS_PROJECT_QUOTA_ON
                return ""

            denial_sent = False

            def write(_fd, data):
                nonlocal denial_sent
                if not denial_sent:
                    denial_sent = True
                    raise OSError(errno.EDQUOT, "synthetic quota denial")
                return len(data)

            def fsync(_fd):
                return None

            with (
                patch.object(suite, "quota_command", side_effect=quota),
                patch_quota_guards(suite),
                patch_quota_probe_io(write, fsync),
                self.assertRaises(run_suite.HarnessFailure),
            ):
                suite.verify_quota_enforcement()

        self.assertEqual(state_calls, ["quota-state", "quota-post-state"])
        self.assertEqual(
            suite.failure_context,
            {
                "operation": "quota-post-state",
                "classification": "project-enforcement-disabled",
            },
        )

    def test_reset_only_failure_is_reported_as_the_primary_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, _projects = quota_test_suite(Path(directory))
            block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB

            def quota(_command, operation, **_kwargs):
                nonlocal block_limit
                if operation == "quota-limit":
                    if "32m" in _command:
                        block_limit = run_suite.QUOTA_RELIEF_BLOCK_LIMIT_KIB
                    elif "16m" in _command:
                        block_limit = run_suite.QUOTA_BLOCK_LIMIT_KIB
                if operation == "quota-report":
                    return quota_report(inode="-i" in _command, block_limit=block_limit)
                if operation in {"quota-state", "quota-post-state"}:
                    return XFS_PROJECT_QUOTA_ON
                if operation == "quota-reset-limit":
                    suite.record_quota_failure(
                        "quota-reset-limit",
                        "command-reported-error",
                        returncode=1,
                        cleanup_phase="limit-reset",
                    )
                    raise run_suite.HarnessFailure("unreported mock detail")
                return ""

            denial_sent = False

            def write(_fd, data):
                nonlocal denial_sent
                if not denial_sent:
                    denial_sent = True
                    raise OSError(errno.EDQUOT, "synthetic quota denial")
                return len(data)

            def fsync(_fd):
                return None

            with (
                patch.object(suite, "quota_command", side_effect=quota),
                patch_quota_guards(suite),
                patch_quota_probe_io(write, fsync),
                self.assertRaises(run_suite.HarnessFailure),
            ):
                suite.verify_quota_enforcement()

        self.assertEqual(
            suite.failure_context,
            {
                "operation": "quota-reset-limit",
                "classification": "command-reported-error",
                "returncode": 1,
                "phase": "limit-reset",
            },
        )
        self.assertEqual(suite.cleanup_failure_context, suite.failure_context)
        self.assertEqual(suite.active_stage, "quota-reset-limit")

    def test_unexpected_quota_write_and_fsync_errors_are_classified(self) -> None:
        for operation in ("quota-write", "quota-fsync"):
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory() as directory,
            ):
                suite, _projects = quota_test_suite(Path(directory))

                def quota(_command, quota_operation, **_kwargs):
                    if quota_operation == "quota-report":
                        return quota_report(inode="-i" in _command)
                    return (
                        XFS_PROJECT_QUOTA_ON
                        if quota_operation
                        in {
                            "quota-state",
                            "quota-post-state",
                        }
                        else ""
                    )

                def write(_fd, data, operation=operation):
                    if operation == "quota-write":
                        raise OSError(errno.EIO, "synthetic secret I/O message")
                    return len(data)

                def fsync(_fd, operation=operation):
                    if operation == "quota-fsync":
                        raise OSError(errno.EIO, "synthetic secret fsync message")

                with (
                    patch.object(suite, "quota_command", side_effect=quota),
                    patch_quota_guards(suite),
                    patch_quota_probe_io(write, fsync),
                    self.assertRaises(run_suite.HarnessFailure),
                ):
                    suite.verify_quota_enforcement()

                self.assertEqual(
                    suite.failure_context,
                    {
                        "operation": operation,
                        "classification": "io-error",
                        "errno": errno.EIO,
                    },
                )

    def test_quota_reset_failure_does_not_replace_primary_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite, _projects = quota_test_suite(Path(directory))
            call_number = 0
            commands = [
                subprocess.CompletedProcess([], 0, XFS_PROJECT_QUOTA_ON.encode(), b""),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess([], 1, b"", b"Error: primary-secret"),
                subprocess.CompletedProcess([], 1, b"", b"Error: reset-secret"),
            ]

            def safe_call(argv, **_kwargs):
                nonlocal call_number
                result = commands[call_number]
                call_number += 1
                return subprocess.CompletedProcess(
                    argv, result.returncode, result.stdout, result.stderr
                )

            def write(_fd, _data):
                return 1024

            def fsync(_fd):
                return None

            with (
                patch.object(run_suite, "safe_call", side_effect=safe_call),
                patch_quota_guards(suite),
                patch_quota_probe_io(write, fsync),
                self.assertRaises(run_suite.HarnessFailure),
            ):
                suite.verify_quota_enforcement()

        self.assertEqual(
            suite.failure_context,
            {
                "operation": "quota-limit",
                "classification": "command-reported-error",
                "returncode": 1,
            },
        )
        self.assertEqual(
            suite.cleanup_failure_context,
            {
                "operation": "quota-reset-limit",
                "classification": "command-reported-error",
                "returncode": 1,
                "phase": "limit-reset",
            },
        )
        self.assertEqual(suite.active_stage, "quota-limit")
        self.assertNotIn("primary-secret", json.dumps(suite.failure_context))
        self.assertNotIn("reset-secret", json.dumps(suite.cleanup_failure_context))

    def test_xfs_only_mode_does_not_build_images_or_claim_runtime_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = run_suite.Suite(Path(directory), run_suite.BASELINE_SHA)
            suite.preflight = lambda: None
            suite.create_run_root = lambda: setattr(suite, "created_run_root", True)
            suite.setup_xfs_pool = lambda: None
            suite.cleanup = lambda: []
            suite.build_images = lambda: self.fail("xfs-only must not build images")
            suite.scenario = lambda: self.fail("xfs-only must not start acceptance")
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = suite.run_xfs_only()

        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["xfs_probe"], "pass")
        self.assertEqual(result["runtime"], "not-run")
        self.assertEqual(result["phase"], "xfs-only")
        self.assertEqual(result["cleanup"], "verified")
        self.assertEqual(result["cases"], {})
        self.assertNotIn("images", result)

    def test_xfs_only_failure_emits_only_sanitized_quota_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = run_suite.Suite(Path(directory), run_suite.BASELINE_SHA)
            suite.preflight = lambda: None
            suite.create_run_root = lambda: setattr(suite, "created_run_root", True)

            def failed_setup():
                suite.record_quota_failure(
                    "quota-state", "project-enforcement-disabled"
                )
                raise run_suite.HarnessFailure("synthetic raw path and token-secret")

            suite.setup_xfs_pool = failed_setup
            suite.cleanup = lambda: []
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = suite.run_xfs_only()

        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["phase"], "xfs-only")
        self.assertEqual(result["runtime"], "not-run")
        self.assertEqual(result["xfs_probe"], "fail")
        self.assertEqual(result["failed_stage"], "quota-state")
        self.assertEqual(
            result["failure_context"],
            {
                "operation": "quota-state",
                "classification": "project-enforcement-disabled",
            },
        )
        self.assertNotIn("raw path", json.dumps(result))
        self.assertNotIn("token-secret", json.dumps(result))

    def test_xfs_only_cleanup_summary_does_not_emit_resource_identifiers_or_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = run_suite.Suite(Path(directory), run_suite.BASELINE_SHA)
            suite.preflight = lambda: None
            suite.create_run_root = lambda: setattr(suite, "created_run_root", True)
            suite.setup_xfs_pool = lambda: None

            def failed_cleanup():
                suite.results["preserved_run_root"] = "secret-path"
                suite.results["cleanup_incomplete"] = ["xfs-pool"]
                return ["xfs-pool"]

            suite.cleanup = failed_cleanup
            suite.cleanup_resource_summary = lambda: [
                {"id": "secret-device-id", "name": "secret-path", "kind": "loop-mount"}
            ]
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = suite.run_xfs_only()

        result = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["runtime"], "not-run")
        self.assertEqual(result["cleanup"], "incomplete")
        self.assertEqual(result["remaining_owned_resource_count"], 1)
        self.assertEqual(result["remaining_owned_resource_kinds"], ["loop-mount"])
        self.assertNotIn("secret-device-id", json.dumps(result))
        self.assertNotIn("secret-path", json.dumps(result))

    def test_loop_attach_failure_emits_only_safe_static_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = b"CONFIGPROXY_AUTH_TOKEN=fixture-secret no free loop device"

            def fake_safe_call(argv, **_kwargs):
                self.assertEqual(argv[0], "losetup")
                return subprocess.CompletedProcess(argv, 1, b"", secret)

            code, result, _suite = run_xfs_setup_case(Path(directory), fake_safe_call)

        self.assertEqual(code, 1)
        self.assertEqual(result["failed_stage"], "loop-attach")
        self.assertEqual(
            result["failure_context"],
            {
                "operation": "loop-attach",
                "classification": "no-free-loop-device",
                "returncode": 1,
            },
        )
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertNotIn("CONFIGPROXY_AUTH_TOKEN", json.dumps(result))

    def test_attachment_manifest_write_failure_is_distinct_and_scrubbed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[str] = []

            def fake_safe_call(argv, **_kwargs):
                calls.append(argv[0])
                return subprocess.CompletedProcess(argv, 0, b"/dev/loop42\n", b"")

            code, result, suite = run_xfs_setup_case(
                Path(directory), fake_safe_call, fail_save_at=2
            )

        self.assertEqual(code, 1)
        self.assertEqual(calls, ["losetup"])
        self.assertEqual(result["failed_stage"], "loop-attachment-record")
        self.assertEqual(
            result["failure_context"],
            {
                "operation": "manifest-write",
                "phase": "loop-attachment-record",
                "classification": "write-failed",
            },
        )
        self.assertEqual(suite.loop_device, "/dev/loop42")
        self.assertNotIn("hidden-path", json.dumps(result))
        self.assertNotIn("synthetic-secret-save-failure", json.dumps(result))

    def test_mount_failure_is_distinguished_from_attachment_and_scrubbed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[str] = []

            def fake_safe_call(argv, **_kwargs):
                calls.append(argv[0])
                if argv[0] == "losetup":
                    return subprocess.CompletedProcess(argv, 0, b"/dev/loop8\n", b"")
                return subprocess.CompletedProcess(
                    argv,
                    32,
                    b"",
                    b"permission denied CONFIGPROXY_AUTH_TOKEN=mount-secret",
                )

            code, result, _suite = run_xfs_setup_case(Path(directory), fake_safe_call)

        self.assertEqual(code, 1)
        self.assertEqual(calls, ["losetup", "mount"])
        self.assertEqual(result["failed_stage"], "loop-mount")
        self.assertEqual(
            result["failure_context"],
            {
                "operation": "loop-mount",
                "classification": "permission-denied",
                "returncode": 32,
            },
        )
        self.assertNotIn("mount-secret", json.dumps(result))

    def test_loop_setup_timeout_and_malformed_device_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:

            def timeout_safe_call(argv, **_kwargs):
                raise run_suite.HarnessFailure("opaque child output") from (
                    subprocess.TimeoutExpired(["secret-argv"], 20)
                )

            code, timeout_result, _suite = run_xfs_setup_case(
                Path(directory) / "timeout", timeout_safe_call
            )
            self.assertEqual(code, 1)
            self.assertEqual(timeout_result["failed_stage"], "loop-attach")
            self.assertEqual(
                timeout_result["failure_context"],
                {"operation": "loop-attach", "classification": "timeout"},
            )
            self.assertNotIn("secret-argv", json.dumps(timeout_result))

            def malformed_safe_call(argv, **_kwargs):
                return subprocess.CompletedProcess(
                    argv, 0, b"/dev/very-secret-token", b""
                )

            code, malformed_result, _suite = run_xfs_setup_case(
                Path(directory) / "malformed", malformed_safe_call
            )

        self.assertEqual(code, 1)
        self.assertEqual(malformed_result["failed_stage"], "loop-device-validation")
        self.assertEqual(
            malformed_result["failure_context"],
            {
                "operation": "loop-device-validation",
                "classification": "unexpected-device-identifier",
            },
        )
        self.assertNotIn("very-secret-token", json.dumps(malformed_result))

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

    def test_db_runner_pass_parser_requires_verified_inner_cleanup(self) -> None:
        evidence = {
            "status": "pass",
            "test": "upstream-db-migration-and-cold-orm-restore",
            "migration_disabled_cold_startup_rejection": "pass",
            "migration_disabled_startup_preserved_schema_and_identities": "pass",
            "positive_server_startup": "not-tested-by-db-fixture",
            "schema_idempotence": "pass",
            "old_orm_cold_restore_compatibility": "pass",
            "owned_container_cleanup": "verified",
            "fixture_cleanup": "verified",
            "owned_container_count": 9,
            "remaining_owned_container_count": 0,
        }
        parsed = run_suite.parse_db_runner_evidence(
            json.dumps(evidence).encode(), returncode=0
        )
        self.assertEqual(parsed["owned_container_cleanup"], "verified")
        evidence["owned_container_cleanup"] = "incomplete-or-unknown"
        with self.assertRaises(run_suite.HarnessFailure):
            run_suite.parse_db_runner_evidence(json.dumps(evidence).encode(), 0)

    def test_db_runner_failure_parser_keeps_child_cleanup_separate_and_safe(
        self,
    ) -> None:
        evidence = {
            "status": "fail",
            "failure_context": {
                "operation": "seed-old",
                "image_role": "baseline-hub",
                "classification": "worker-failed",
                "returncode": 1,
            },
            "cleanup_failure_context": None,
            "owned_container_cleanup": "verified",
            "fixture_cleanup": "verified",
            "owned_container_count": 4,
            "remaining_owned_container_count": 0,
            "failed_cleanup_stages": [],
        }
        parsed = run_suite.parse_db_runner_evidence(
            json.dumps(evidence).encode(), returncode=1
        )
        self.assertEqual(parsed["failure_context"]["operation"], "seed-old")
        self.assertEqual(parsed["owned_container_cleanup"], "verified")
        self.assertEqual(parsed["fixture_cleanup"], "verified")
        self.assertEqual(parsed["remaining_owned_container_count"], 0)

    def test_db_runner_failure_forwards_only_bounded_context_and_cleanup(self) -> None:
        evidence = {
            "status": "fail",
            "failure_context": {
                "operation": "seed-old",
                "image_role": "baseline-hub",
                "classification": "worker-failed",
                "returncode": 1,
            },
            "cleanup_failure_context": {
                "operation": "db-owned-cleanup",
                "image_role": "shared",
                "classification": "owned-container-cleanup-incomplete",
            },
            "owned_container_cleanup": "incomplete-or-unknown",
            "fixture_cleanup": "preserved-owned-containers-unverified",
            "owned_container_count": 2,
            "remaining_owned_container_count": 1,
            "failed_cleanup_stages": ["db-owned-container-cleanup"],
        }
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        suite.run_root = Path.cwd()
        suite.image_ids.update(
            {"old_hub": "sha256:" + "a" * 64, "candidate_hub": "sha256:" + "b" * 64}
        )
        completed = subprocess.CompletedProcess(
            [], 1, json.dumps(evidence).encode(), b"private stderr token-secret"
        )
        with (
            patch.object(run_suite, "safe_call", return_value=completed),
            self.assertRaises(run_suite.HarnessFailure),
        ):
            suite.test_db_runner()
        self.assertEqual(
            suite.failure_context,
            {
                "operation": "seed-old",
                "image_role": "baseline-hub",
                "classification": "worker-failed",
                "returncode": 1,
            },
        )
        self.assertEqual(
            suite.cleanup_failure_context,
            {
                "operation": "db-owned-cleanup",
                "image_role": "shared",
                "classification": "owned-container-cleanup-incomplete",
            },
        )
        self.assertEqual(
            suite.results["db_runner_cleanup"]["remaining_owned_container_count"], 1
        )
        self.assertNotIn("token-secret", json.dumps(suite.results))

    def test_db_runner_parser_rejects_unknown_or_oversized_output_safely(self) -> None:
        evidence = {
            "status": "fail",
            "failure_context": {
                "operation": "seed-old",
                "image_role": "baseline-hub",
                "classification": "worker-failed",
            },
            "cleanup_failure_context": None,
            "owned_container_cleanup": "verified",
            "fixture_cleanup": "verified",
            "owned_container_count": 1,
            "remaining_owned_container_count": 0,
            "failed_cleanup_stages": [],
            "exception": "token-secret-must-not-be-forwarded",
        }
        with self.assertRaisesRegex(
            run_suite.HarnessFailure, "invalid sanitized evidence"
        ) as raised:
            run_suite.parse_db_runner_evidence(
                json.dumps(evidence).encode(), returncode=1
            )
        self.assertNotIn("token-secret", str(raised.exception))
        with self.assertRaisesRegex(
            run_suite.HarnessFailure, "invalid sanitized evidence"
        ):
            run_suite.parse_db_runner_evidence(
                b" " * (run_suite.DB_RESULT_MAX_BYTES + 1), returncode=1
            )

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

    def test_wrapper_build_passes_owned_tag_and_verifies_base_before_and_after(
        self,
    ) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        base_id = "sha256:" + "a" * 64
        wrapper_id = "sha256:" + "b" * 64
        base_ref = f"local/jh6/base-hub6:{suite.run_id}"
        suite.owned_image_refs[base_ref] = base_id
        inspect = patch.object(
            suite, "operation_image_id", side_effect=[base_id, base_id]
        )

        def build(context, dockerfile, tag, *, role, args, pull):
            self.assertEqual(dockerfile, "Dockerfile")
            self.assertEqual(role, "candidate-wrapper")
            self.assertEqual(args, {"HUB_BASE": base_ref})
            self.assertFalse(pull)
            suite.owned_image_refs[tag] = wrapper_id
            return wrapper_id

        with (
            inspect as inspect_mock,
            patch.object(suite, "build", side_effect=build) as build_mock,
        ):
            self.assertEqual(
                suite.build_wrapper_image(
                    Path("wrapper-context"),
                    f"local/jh6/hub6:{suite.run_id}",
                    base_ref,
                    base_id,
                    role="candidate-wrapper",
                ),
                wrapper_id,
            )
        self.assertEqual(inspect_mock.call_count, 2)
        self.assertEqual(build_mock.call_count, 1)

    def test_baseline_and_candidate_hub_wrapper_contexts_preserve_versions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            run_root.mkdir()
            wrapper_source = root / "integration_jupyterhub_config.py"
            wrapper_source.write_text("# fixture wrapper\n", encoding="utf-8")
            suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
            suite.run_root = run_root
            run_tag = suite.run_id
            baseline_ref = f"local/jh6/base-hub5:{run_tag}"
            candidate_ref = f"local/jh6/base-hub6:{run_tag}"
            baseline_id = "sha256:" + "a" * 64
            candidate_id = "sha256:" + "b" * 64
            baseline_wrapper_id = "sha256:" + "c" * 64
            candidate_wrapper_id = "sha256:" + "d" * 64
            suite.owned_image_refs.update(
                {baseline_ref: baseline_id, candidate_ref: candidate_id}
            )
            base_ids = {baseline_ref: baseline_id, candidate_ref: candidate_id}
            wrapper_ids = {
                f"local/jh6/hub5:{run_tag}": baseline_wrapper_id,
                f"local/jh6/hub6:{run_tag}": candidate_wrapper_id,
            }
            image_labels = {
                baseline_wrapper_id: "5.5.1",
                candidate_wrapper_id: "6.0.1",
            }
            inspect_calls: list[str] = []
            build_calls: list[tuple[Path, str, str, dict[str, str], bool]] = []

            def inspect(reference: str, **_kwargs: object) -> str:
                inspect_calls.append(reference)
                return base_ids[reference]

            def build(
                context: Path,
                dockerfile: str,
                tag: str,
                *,
                role: str,
                args: dict[str, str],
                pull: bool,
            ) -> str:
                self.assertEqual(dockerfile, "Dockerfile")
                self.assertTrue((context / dockerfile).is_file())
                self.assertEqual(
                    (context / "integration_jupyterhub_config.py").read_text(
                        encoding="utf-8"
                    ),
                    "# fixture wrapper\n",
                )
                build_calls.append((context, role, tag, args, pull))
                suite.owned_image_refs[tag] = wrapper_ids[tag]
                return wrapper_ids[tag]

            def image_label(image: str, label: str) -> str:
                self.assertEqual(label, run_suite.VERSION_LABEL)
                return image_labels[image]

            with (
                patch.object(suite, "operation_image_id", side_effect=inspect),
                patch.object(suite, "build", side_effect=build),
                patch.object(suite, "image_label", side_effect=image_label),
            ):
                self.assertEqual(
                    suite.build_hub_wrapper_images(
                        wrapper_source,
                        run_tag=run_tag,
                        baseline_base_reference=baseline_ref,
                        baseline_base_id=baseline_id,
                        candidate_base_reference=candidate_ref,
                        candidate_base_id=candidate_id,
                    ),
                    (baseline_wrapper_id, candidate_wrapper_id),
                )

            self.assertEqual(
                inspect_calls,
                [baseline_ref, baseline_ref, candidate_ref, candidate_ref],
            )
            self.assertEqual(
                [entry[1:] for entry in build_calls],
                [
                    (
                        "baseline-wrapper",
                        f"local/jh6/hub5:{run_tag}",
                        {"HUB_BASE": baseline_ref},
                        False,
                    ),
                    (
                        "candidate-wrapper",
                        f"local/jh6/hub6:{run_tag}",
                        {"HUB_BASE": candidate_ref},
                        False,
                    ),
                ],
            )
            baseline_dockerfile = (build_calls[0][0] / "Dockerfile").read_text(
                encoding="utf-8"
            )
            candidate_dockerfile = (build_calls[1][0] / "Dockerfile").read_text(
                encoding="utf-8"
            )
            self.assertIn('m.version("jupyterhub") == "5.5.1"', baseline_dockerfile)
            self.assertIn(
                f'LABEL {run_suite.VERSION_LABEL}="5.5.1"', baseline_dockerfile
            )
            self.assertNotIn("5.5.1", candidate_dockerfile)
            self.assertNotIn("LABEL", candidate_dockerfile)
            self.assertIn("FROM ${HUB_BASE}", candidate_dockerfile)

    def test_wrapper_build_stops_before_build_if_base_tag_identity_changed(
        self,
    ) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        expected_id = "sha256:" + "a" * 64
        changed_id = "sha256:" + "b" * 64
        base_ref = f"local/jh6/base-hub5:{suite.run_id}"
        suite.owned_image_refs[base_ref] = expected_id
        with (
            patch.object(suite, "operation_image_id", return_value=changed_id),
            patch.object(suite, "build") as build_mock,
            self.assertRaises(run_suite.HarnessFailure),
        ):
            suite.build_wrapper_image(
                Path("wrapper-context"),
                f"local/jh6/hub5:{suite.run_id}",
                base_ref,
                expected_id,
                role="baseline-wrapper",
            )
        build_mock.assert_not_called()
        self.assertEqual(
            suite.failure_context,
            {
                "operation": "wrapper-base-verify",
                "role": "baseline-wrapper",
                "classification": "tag-id-mismatch",
            },
        )

    def test_wrapper_build_rejects_postbuild_retag_but_keeps_owned_id(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        base_id = "sha256:" + "a" * 64
        wrapper_id = "sha256:" + "b" * 64
        changed_id = "sha256:" + "c" * 64
        base_ref = f"local/jh6/base-hub6:{suite.run_id}"
        wrapper_ref = f"local/jh6/hub6:{suite.run_id}"
        suite.owned_image_refs[base_ref] = base_id

        def build(_context, _dockerfile, tag, **_kwargs):
            suite.owned_image_refs[tag] = wrapper_id
            suite.manifest["resources"]["images"].append(
                {"ref": tag, "id": wrapper_id, "kind": "built", "removed": False}
            )
            return wrapper_id

        with (
            patch.object(
                suite,
                "operation_image_id",
                side_effect=[base_id, changed_id],
            ),
            patch.object(suite, "build", side_effect=build),
            self.assertRaises(run_suite.HarnessFailure),
        ):
            suite.build_wrapper_image(
                Path("wrapper-context"),
                wrapper_ref,
                base_ref,
                base_id,
                role="candidate-wrapper",
            )

        self.assertEqual(suite.owned_image_refs[wrapper_ref], wrapper_id)
        self.assertNotIn("candidate_hub", suite.image_ids)
        self.assertEqual(suite.failure_context["classification"], "tag-id-mismatch")

    def test_image_version_probe_failure_has_safe_candidate_lab_stage(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        secret = b"private container output"
        with (
            patch.object(suite, "run_owned_tool", return_value=(23, secret)),
            self.assertRaises(run_suite.HarnessFailure),
        ):
            suite.inspect_version(
                "sha256:" + "a" * 64,
                "/opt/jupyter/bin/python",
                role="candidate-lab",
            )
        self.assertEqual(suite.active_stage, "image-version-probe-candidate-lab")
        self.assertEqual(
            suite.failure_context,
            {
                "operation": "image-version-probe",
                "role": "candidate-lab",
                "classification": "probe-failed",
                "returncode": 23,
            },
        )
        self.assertNotIn(secret.decode(), json.dumps(suite.failure_context))

    def test_probe_failure_parser_forwards_only_allowlisted_context(self) -> None:
        output = json.dumps(
            {
                "stage": "baseline",
                "status": "failed",
                "completed_cases": ["readiness", "login-a"],
                "failure_context": {
                    "checkpoint": "terminals",
                    "classification": "http-status",
                    "http_status": 200,
                    "assertion": "terminal-cleanup",
                },
            }
        ).encode()
        context, completed, count, _ = run_suite.parse_probe_result(
            output, expected_stage="baseline", returncode=7
        )
        self.assertEqual(
            context,
            {
                "operation": "api-probe",
                "checkpoint": "terminals",
                "classification": "http-status",
                "http_status": 200,
                "assertion": "terminal-cleanup",
            },
        )
        self.assertEqual(completed, ["readiness", "login-a"])
        self.assertEqual(count, 0)
        self.assertNotIn("cases", context)

    def test_probe_failure_parser_rejects_unknown_fields_and_duplicate_keys(
        self,
    ) -> None:
        valid = {
            "stage": "baseline",
            "status": "failed",
            "failure_context": {
                "checkpoint": "login-a",
                "classification": "request-transport",
            },
            "completed_cases": ["readiness"],
        }
        unknown = {**valid, "response_body": "sensitive-body"}
        duplicate = (
            b'{"stage":"baseline","stage":"baseline","status":"failed",'
            b'"failure_context":{"checkpoint":"login-a",'
            b'"classification":"request-transport"}}'
        )
        for output in (json.dumps(unknown).encode(), duplicate):
            with self.subTest(output_length=len(output)):
                with self.assertRaises(run_suite.HarnessFailure) as raised:
                    run_suite.parse_probe_result(
                        output, expected_stage="baseline", returncode=7
                    )
                self.assertNotIn("sensitive-body", str(raised.exception))

    def test_probe_failure_parser_rejects_unknown_completed_case(self) -> None:
        output = json.dumps(
            {
                "stage": "baseline",
                "status": "failed",
                "completed_cases": ["private-response-body"],
                "failure_context": {
                    "checkpoint": "login-a",
                    "classification": "request-transport",
                },
            }
        ).encode()
        with self.assertRaises(run_suite.HarnessFailure) as raised:
            run_suite.parse_probe_result(
                output, expected_stage="baseline", returncode=7
            )
        self.assertNotIn("private-response-body", str(raised.exception))

    def test_probe_failure_parser_rejects_unknown_assertion_label(self) -> None:
        for assertion in ("private-path-token-secret", "terminal-delete"):
            output = json.dumps(
                {
                    "stage": "baseline",
                    "status": "failed",
                    "completed_cases": ["readiness"],
                    "failure_context": {
                        "checkpoint": "terminals",
                        "classification": "http-status",
                        "http_status": 200,
                        "assertion": assertion,
                    },
                }
            ).encode()
            with self.subTest(assertion=assertion):
                with self.assertRaises(run_suite.HarnessFailure) as raised:
                    run_suite.parse_probe_result(
                        output, expected_stage="baseline", returncode=7
                    )
                self.assertNotIn(assertion, str(raised.exception))

    def test_run_probe_preserves_failure_checkpoint_and_partial_case_names_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            probe = run_root / "probe"
            probe.mkdir(parents=True)
            suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
            suite.run_root = run_root
            suite.probe_dir = probe
            suite.image_ids["candidate_hub"] = "sha256:" + "a" * 64
            child_output = json.dumps(
                {
                    "stage": "baseline",
                    "status": "failed",
                    "completed_cases": ["readiness"],
                    "failure_context": {
                        "checkpoint": "login-a",
                        "classification": "http-status",
                        "http_status": 403,
                    },
                }
            ).encode()
            with (
                patch.object(suite, "run_owned_tool", return_value=(7, child_output)),
                self.assertRaisesRegex(
                    run_suite.HarnessFailure, "acceptance stage failed"
                ),
            ):
                suite.run_probe("baseline")
            self.assertEqual(
                suite.failure_context,
                {
                    "operation": "api-probe",
                    "checkpoint": "login-a",
                    "classification": "http-status",
                    "http_status": 403,
                },
            )
            self.assertEqual(suite.results["completed_probe_cases"], ["readiness"])
            self.assertEqual(suite.results["cases"], {})

    def test_proxy_image_reference_is_registered_with_its_content_id(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        content_id = "sha256:" + "a" * 64
        suite.register_image_reference("proxy", content_id, "local/proxy:run")
        self.assertEqual(suite.image_ids["proxy"], content_id)
        self.assertEqual(suite.image_refs["proxy"], "local/proxy:run")

    def test_scenario_creates_project_before_asserting_and_starting_old_stack(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = configured_suite(Path(directory))
            self.assertIsNone(suite.project_dir)
            calls: list[str] = []

            def setup_network() -> None:
                self.assertIsNone(suite.project_dir)
                calls.append("network")

            def start_old_stack() -> None:
                self.assertIsNotNone(suite.project_dir)
                calls.append("old-stack")
                raise RuntimeError("stop after startup-order assertion")

            write_fixture = suite.write_compose_fixture

            def write_fixture_and_record_order() -> None:
                write_fixture()
                self.assertIsNotNone(suite.project_dir)
                calls.append("fixture")

            with (
                patch.object(suite, "setup_network", side_effect=setup_network),
                patch.object(
                    suite,
                    "write_compose_fixture",
                    side_effect=write_fixture_and_record_order,
                ),
                patch.object(suite, "start_old_stack", side_effect=start_old_stack),
                self.assertRaisesRegex(
                    RuntimeError, "stop after startup-order assertion"
                ),
            ):
                suite.scenario()

            self.assertIsNotNone(suite.project_dir)
            self.assertEqual(calls, ["network", "fixture", "old-stack"])
            self.assertEqual(suite.active_stage, "explicit-fixture-configuration")
            self.assertEqual(suite.active_case, "scenario-setup")

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
            suite = configured_suite(root)
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="test-generated-proxy-auth-token",
            ):
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

    def test_proxy_token_is_runtime_only_and_excluded_from_backup_configuration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = configured_suite(root)
            token = "test-generated-proxy-auth-token"
            process_environment = os.environ.copy()
            with (
                patch.dict(
                    os.environ,
                    {
                        "CONFIGPROXY_AUTH_TOKEN": "inherited-parent-token",
                        "LOG_LEVEL": "inherited-log-level",
                        "FIXTURE_PARENT_MARKER": "preserved-parent-setting",
                    },
                ),
                patch.object(
                    run_suite.secrets, "token_urlsafe", return_value=token
                ) as generate,
            ):
                suite.write_compose_fixture()
                generate.assert_called_once_with(48)
                self.assertEqual(
                    os.environ["CONFIGPROXY_AUTH_TOKEN"], "inherited-parent-token"
                )
                self.assertEqual(os.environ["LOG_LEVEL"], "inherited-log-level")
                self.assertEqual(suite.update_env["CONFIGPROXY_AUTH_TOKEN"], token)
                self.assertEqual(suite.update_env["LOG_LEVEL"], "INFO")
                self.assertEqual(
                    suite.update_env["FIXTURE_PARENT_MARKER"],
                    "preserved-parent-setting",
                )
                self.assertEqual(
                    suite.update_env["COMPOSE_PROJECT_NAME"], suite.project_name
                )
                self.assertEqual(
                    suite.update_env["UPDATE_LOCK_FILE"],
                    str(suite.run_root / "update.lock"),
                )
            self.assertEqual(os.environ, process_environment)

            assert suite.env_file and suite.compose
            generated_files = [
                suite.env_file,
                suite.compose,
                suite.candidate_override,
                suite.initial_override,
            ]
            for path in generated_files:
                if path is not None and token.encode() in path.read_bytes():
                    self.fail("generated fixture file contains runtime proxy token")
            if token.encode() in json.dumps(suite.manifest).encode():
                self.fail("fixture manifest contains runtime proxy token")

            backup_parent = root / "backups"
            backup_parent.mkdir()
            backup = backup_parent / "snapshot"
            backup.mkdir(mode=0o700)
            controller = maintenance.Controller(
                project_directory=suite.project_dir,
                project_name=suite.project_name,
                env_file=suite.env_file,
                compose_files=[
                    suite.compose,
                    suite.initial_override,
                    suite.candidate_override,
                ],
                backup_dir=backup_parent / "controller-backup",
                command=lambda *_args: "",
            )
            controller._copy_operator_configuration(backup)
            for path in (backup / "configuration").iterdir():
                if token.encode() in path.read_bytes():
                    self.fail("copied backup configuration contains runtime token")

    def test_proxy_token_is_passed_only_to_configuration_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = configured_suite(Path(directory))
            token = "test-generated-proxy-auth-token"
            with patch.object(run_suite.secrets, "token_urlsafe", return_value=token):
                suite.write_compose_fixture()
            assert suite.run_root

            def verify_runtime_env(env: dict[str, str]) -> None:
                self.assertEqual(env["CONFIGPROXY_AUTH_TOKEN"], token)
                self.assertEqual(env["COMPOSE_PROJECT_NAME"], suite.project_name)
                self.assertEqual(
                    env["UPDATE_LOCK_FILE"], str(suite.run_root / "update.lock")
                )
                self.assertIsNot(env, suite.update_env)

            compose_calls: list[dict[str, str]] = []

            def capture_compose(argv, **kwargs):
                if token in " ".join(argv):
                    self.fail("Compose command arguments contain runtime proxy token")
                compose_calls.append(kwargs["env"])
                return b""

            with patch.object(
                run_suite,
                "require_call",
                side_effect=capture_compose,
            ):
                suite.compose_command("config", capture=True)
                suite.compose_command("ps", "-aq", "--no-trunc", "hub")
                suite.compose_command("up", "-d", "web")
                suite.compose_command(
                    "up",
                    "-d",
                    "--force-recreate",
                    "hub",
                    override=suite.initial_override,
                )
            self.assertEqual(len(compose_calls), 4)
            for env in compose_calls:
                verify_runtime_env(env)

            helper_calls: list[dict[str, str]] = []
            failed_child_output = subprocess.CompletedProcess(
                [], 1, token.encode(), token.encode()
            )

            def failed_helper(argv, **kwargs):
                if token in " ".join(argv):
                    self.fail("helper command arguments contain runtime proxy token")
                helper_calls.append(kwargs["env"])
                return failed_child_output

            with patch.object(
                run_suite,
                "safe_call",
                side_effect=failed_helper,
            ):
                for verb in (
                    "preflight",
                    "migrate",
                    "restore",
                    "accept",
                    "migrate",
                    "accept",
                ):
                    with self.assertRaisesRegex(
                        run_suite.HarnessFailure,
                        f"shipping maintenance helper {verb} failed",
                    ) as failure:
                        suite.run_helper(
                            verb,
                            suite.run_root / f"{verb}-backup",
                            preflight=verb == "preflight",
                            restore=verb == "restore",
                            acceptance=verb == "accept",
                        )
                    if token in str(failure.exception):
                        self.fail("helper failure disclosed runtime proxy token")
            self.assertEqual(len(helper_calls), 6)
            for env in helper_calls:
                verify_runtime_env(env)

            reject_calls: list[dict[str, str]] = []
            reject_result = subprocess.CompletedProcess(
                [],
                1,
                token.encode(),
                (
                    b'{"status":"failed","operation":"accept",'
                    b'"phase":"dispatch","classification":"acceptance-not-acknowledged",'
                    b'"error":"accept requires the explicit --acceptance-passed acknowledgment"}'
                ),
            )

            def failed_unasserted_accept(argv, **kwargs):
                if token in " ".join(argv):
                    self.fail("accept command arguments contain runtime proxy token")
                reject_calls.append(kwargs["env"])
                return reject_result

            with (
                patch.object(
                    run_suite,
                    "safe_call",
                    side_effect=failed_unasserted_accept,
                ),
                patch.object(
                    suite,
                    "compose_ids",
                    side_effect=lambda service: [f"{service}-id"],
                ),
                patch.object(
                    suite,
                    "inspect_container",
                    side_effect=lambda cid: {"State": {"Running": cid != "web-id"}},
                ),
            ):
                suite.reject_unasserted_accept(suite.run_root / "accept-backup")
            verify_runtime_env(reject_calls[0])

            updater_calls: list[dict[str, str]] = []
            updater_result = subprocess.CompletedProcess(
                [],
                1,
                run_suite.EXPECTED_UPDATER_MAJOR_REFUSAL.encode()
                + b"\n"
                + token.encode(),
                b"",
            )

            def refused_updater(argv, **kwargs):
                if token in " ".join(argv):
                    self.fail("updater command arguments contain runtime proxy token")
                updater_calls.append(kwargs["env"])
                return updater_result

            with (
                patch.object(
                    run_suite,
                    "safe_call",
                    side_effect=refused_updater,
                ),
                patch.object(
                    suite,
                    "snapshot_service_ids",
                    return_value={"web": "w", "proxy": "p", "hub": "h"},
                ),
                patch.object(suite, "wait_ready"),
                patch.object(suite, "run_probe"),
            ):
                suite.run_updater_refusal()
            verify_runtime_env(updater_calls[0])
            if token in json.dumps(suite.results):
                self.fail("suite result disclosed runtime proxy token")

    def test_maintenance_helper_failure_context_is_strict_and_bounded(self) -> None:
        valid = {
            "status": "failed",
            "operation": "preflight",
            "phase": "configuration",
            "classification": "required-service-missing",
            "error": "Compose configuration is missing a required service",
        }
        malformed = [
            (
                b'{"status":"failed","status":"failed",'
                b'"operation":"preflight","phase":"configuration",'
                b'"classification":"required-service-missing",'
                b'"error":"Compose configuration is missing a required service"}'
            ),
            json.dumps({**valid, "secret": "do-not-leak"}).encode(),
            json.dumps({**valid, "operation": "migrate"}).encode(),
            json.dumps({**valid, "phase": "secret-path"}).encode(),
            json.dumps({**valid, "classification": "unknown-error"}).encode(),
            json.dumps({**valid, "error": "do-not-leak-token"}).encode(),
            b"{" + (b" " * run_suite.MAINTENANCE_HELPER_MAX_STDERR),
            b'{"status":true,"operation":"preflight","phase":"configuration",'
            b'"classification":"required-service-missing","error":"secret"}',
        ]
        with tempfile.TemporaryDirectory() as directory:
            suite = configured_suite(Path(directory))
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="fixture-proxy-token",
            ):
                suite.write_compose_fixture()
            assert suite.run_root
            backup = suite.run_root / "backup"
            for stderr in malformed:
                child = subprocess.CompletedProcess(
                    ["fixture-helper"], 2, b"secret-stdout", stderr
                )
                with patch.object(run_suite, "safe_call", return_value=child):
                    with self.assertRaisesRegex(
                        run_suite.HarnessFailure,
                        "shipping maintenance helper preflight failed",
                    ) as raised:
                        suite.run_helper("preflight", backup, preflight=True)
                self.assertEqual(
                    suite.failure_context,
                    {
                        "operation": "maintenance-preflight",
                        "classification": "helper-result-invalid",
                        "returncode": 2,
                    },
                )
                self.assertNotIn("secret", str(raised.exception))
                self.assertNotIn("secret", json.dumps(suite.failure_context))

            child = subprocess.CompletedProcess(
                ["fixture-helper"], 2, b"secret-stdout", json.dumps(valid).encode()
            )
            with patch.object(run_suite, "safe_call", return_value=child):
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure,
                    "shipping maintenance helper preflight failed",
                ):
                    suite.run_helper("preflight", backup, preflight=True)
            self.assertEqual(
                suite.failure_context,
                {
                    "operation": "maintenance-preflight",
                    "helper_operation": "preflight",
                    "phase": "configuration",
                    "classification": "required-service-missing",
                    "returncode": 2,
                },
            )

    def test_typed_external_helper_failures_are_validated_without_raw_details(
        self,
    ) -> None:
        base = {
            "status": "failed",
            "operation": "preflight",
            "phase": "migration-readiness",
            "classification": "external-command-failed",
            "error": "bounded external command failed",
            "command_operation": "compose-up-hub",
        }
        valid_payloads = (
            {**base, "command_status": "nonzero", "external_returncode": 17},
            {**base, "command_status": "timeout", "external_returncode": None},
            {**base, "command_status": "exec-failed", "external_returncode": None},
        )
        malformed_payloads = (
            {**base, "command_status": "nonzero"},
            {**base, "command_status": [], "external_returncode": None},
            {**base, "command_status": "nonzero", "external_returncode": 0},
            {**base, "command_status": "nonzero", "external_returncode": True},
            {**base, "command_status": "nonzero", "external_returncode": 256},
            {**base, "command_status": "timeout", "external_returncode": 9},
            {**base, "command_status": "other", "external_returncode": None},
            {
                **base,
                "command_status": "nonzero",
                "external_returncode": 17,
                "secret": "not-output",
            },
            {
                **base,
                "command_operation": "container-inspect-secret",
                "command_status": "nonzero",
                "external_returncode": 17,
            },
            {
                **base,
                "command_status": "nonzero",
                "external_returncode": 17,
                "error": "private command detail",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            suite = configured_suite(Path(directory))
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="fixture-proxy-token",
            ):
                suite.write_compose_fixture()
            assert suite.run_root
            backup = suite.run_root / "backup"

            for payload in valid_payloads:
                child = subprocess.CompletedProcess(
                    ["fixture-helper"],
                    2,
                    b"untrusted stdout",
                    json.dumps(payload).encode(),
                )
                with patch.object(run_suite, "safe_call", return_value=child):
                    with self.assertRaisesRegex(
                        run_suite.HarnessFailure,
                        "shipping maintenance helper preflight failed",
                    ):
                        suite.run_helper("preflight", backup, preflight=True)
                self.assertEqual(
                    suite.failure_context,
                    {
                        "operation": "maintenance-preflight",
                        "helper_operation": "preflight",
                        "phase": "migration-readiness",
                        "classification": "external-command-failed",
                        "command_operation": "compose-up-hub",
                        "command_status": payload["command_status"],
                        "external_returncode": payload["external_returncode"],
                        "returncode": 2,
                    },
                )
                self.assertNotIn("untrusted stdout", json.dumps(suite.failure_context))

            for payload in malformed_payloads:
                raw = json.dumps(payload).encode()
                child = subprocess.CompletedProcess(
                    ["fixture-helper"], 2, b"untrusted stdout", raw
                )
                with patch.object(run_suite, "safe_call", return_value=child):
                    with self.assertRaisesRegex(
                        run_suite.HarnessFailure,
                        "shipping maintenance helper preflight failed",
                    ):
                        suite.run_helper("preflight", backup, preflight=True)
                self.assertEqual(
                    suite.failure_context,
                    {
                        "operation": "maintenance-preflight",
                        "classification": "helper-result-invalid",
                        "returncode": 2,
                    },
                )
                self.assertNotIn(
                    "private command detail", json.dumps(suite.failure_context)
                )
                self.assertNotIn("not-output", json.dumps(suite.failure_context))

            success_payload = {"status": "completed", "fixture": "unchanged"}
            child = subprocess.CompletedProcess(
                ["fixture-helper"], 0, json.dumps(success_payload).encode(), b""
            )
            with patch.object(run_suite, "safe_call", return_value=child):
                self.assertEqual(
                    suite.run_helper("preflight", backup, preflight=True),
                    success_payload,
                )

            wrong_refusal = {
                "status": "failed",
                "operation": "accept",
                "phase": "dispatch",
                "classification": "external-command-failed",
                "error": "bounded external command failed",
                "command_operation": "compose-config",
                "command_status": "nonzero",
                "external_returncode": 17,
            }
            child = subprocess.CompletedProcess(
                ["fixture-helper"], 2, b"", json.dumps(wrong_refusal).encode()
            )
            with patch.object(run_suite, "safe_call", return_value=child):
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure,
                    "maintenance helper accepted without the gate",
                ):
                    suite.reject_unasserted_accept(backup)
            self.assertEqual(
                suite.failure_context,
                {
                    "operation": "maintenance-accept",
                    "helper_operation": "accept",
                    "phase": "dispatch",
                    "classification": "external-command-failed",
                    "command_operation": "compose-config",
                    "command_status": "nonzero",
                    "external_returncode": 17,
                    "returncode": 2,
                },
            )

    def test_controller_emitted_failure_diagnostics_are_parent_validated(self) -> None:
        class FailingController:
            failure: BaseException

            def __init__(self, **_kwargs: object) -> None:
                self.failure_phase = "dispatch"

            def preflight(self) -> None:
                self.failure_phase = "configuration"
                raise self.failure

            def accept(self, _args: object) -> None:
                self.failure_phase = "dispatch"
                raise self.failure

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = configured_suite(root)
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="fixture-proxy-token",
            ):
                suite.write_compose_fixture()
            assert suite.run_root

            cases = (
                (
                    maintenance.MaintenanceError(
                        "Compose configuration is missing a required service"
                    ),
                    "required-service-missing",
                    "Compose configuration is missing a required service",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "unsupported external Lab network configuration"
                    ),
                    "network-topology-mismatch",
                    "unsupported external Lab network configuration",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "Compose Hub binds or networking differ from the protected deployment"
                    ),
                    "hub-shape-mismatch",
                    "Compose Hub binds or networking differ from the protected deployment",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "running Hub and its selected Lab image are not version-matched"
                    ),
                    "running-pair-mismatch",
                    "running Hub and its selected Lab image are not version-matched",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "candidate Hub and Lab image versions do not match"
                    ),
                    "candidate-pair-mismatch",
                    "candidate Hub and Lab image versions do not match",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "possible Lab has an unexpected or ambiguous home bind"
                    ),
                    "lab-home-mismatch",
                    "possible Lab has an unexpected or ambiguous home bind",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "backup parent directory must already exist"
                    ),
                    "backup-parent-missing",
                    "backup parent directory must already exist",
                    None,
                ),
                (
                    maintenance.MaintenanceError("bounded external command failed"),
                    "external-command-failed",
                    "bounded external command failed",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "accept requires the explicit --acceptance-passed acknowledgment"
                    ),
                    "acceptance-not-acknowledged",
                    "accept requires the explicit --acceptance-passed acknowledgment",
                    None,
                ),
                (
                    maintenance.MaintenanceError(
                        "private secret token must never appear in helper output"
                    ),
                    "maintenance-error",
                    "maintenance operation failed",
                    "private secret token",
                ),
                (
                    RuntimeError("opaque unexpected credential must never appear"),
                    "unexpected-error",
                    "unexpected maintenance failure",
                    "opaque unexpected credential",
                ),
            )
            for failure, classification, canonical_error, secret in cases:
                FailingController.failure = failure
                suite.failure_context = None
                operation = (
                    "accept"
                    if classification == "acceptance-not-acknowledged"
                    else "preflight"
                )
                phase = "dispatch" if operation == "accept" else "configuration"
                stderr = io.StringIO()
                argv = [
                    operation,
                    "--project-directory",
                    str(root / "project"),
                    "--project-name",
                    "fixture-project",
                    "--env-file",
                    str(root / "environment"),
                    "--compose-file",
                    str(root / "compose.yaml"),
                    "--backup-dir",
                    str(root / "backup"),
                ]
                if operation == "accept":
                    argv.extend(
                        [
                            "--acknowledge-interruption",
                            "--acknowledge-ingress-fenced",
                            "--acknowledge-updater-paused",
                        ]
                    )
                with (
                    patch.object(maintenance, "Controller", FailingController),
                    patch.object(maintenance, "DeploymentLock"),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(maintenance.main(argv), 2)
                emitted = stderr.getvalue().encode()
                envelope = json.loads(emitted)
                self.assertEqual(envelope["status"], "failed")
                self.assertEqual(envelope["operation"], operation)
                self.assertEqual(envelope["phase"], phase)
                self.assertEqual(envelope["classification"], classification)
                self.assertEqual(envelope["error"], canonical_error)
                if secret:
                    self.assertNotIn(secret.encode(), emitted)
                validated = run_suite.parse_maintenance_helper_failure(
                    emitted, expected_operation=operation
                )
                self.assertEqual(
                    validated,
                    {
                        "operation": operation,
                        "phase": phase,
                        "classification": classification,
                    },
                )

                child = subprocess.CompletedProcess(
                    ["actual-controller"], 2, b"", emitted
                )
                if operation == "accept":
                    with (
                        patch.object(run_suite, "safe_call", return_value=child),
                        patch.object(
                            suite,
                            "compose_ids",
                            side_effect=lambda service: [f"{service}-id"],
                        ),
                        patch.object(
                            suite,
                            "inspect_container",
                            side_effect=lambda cid: {
                                "State": {"Running": cid != "web-id"}
                            },
                        ),
                    ):
                        suite.reject_unasserted_accept(suite.run_root / "backup")
                    self.assertIsNone(suite.failure_context)
                else:
                    with patch.object(run_suite, "safe_call", return_value=child):
                        with self.assertRaisesRegex(
                            run_suite.HarnessFailure,
                            "shipping maintenance helper preflight failed",
                        ):
                            suite.run_helper(
                                "preflight",
                                suite.run_root / "backup",
                                preflight=True,
                            )
                    self.assertEqual(
                        suite.failure_context,
                        {
                            "operation": "maintenance-preflight",
                            "helper_operation": "preflight",
                            "phase": phase,
                            "classification": classification,
                            "returncode": 2,
                        },
                    )
                    self.assertNotIn("error", suite.failure_context)
                if secret:
                    self.assertNotIn(secret, emitted.decode())
                    self.assertNotIn(secret, json.dumps(suite.failure_context))

            typed_operations = tuple(maintenance.COMMAND_OPERATIONS)
            for index, command_operation in enumerate(typed_operations):
                command_status = ("nonzero", "timeout", "exec-failed")[index % 3]
                external_returncode = 17 if command_status == "nonzero" else None
                failure = maintenance.ExternalCommandFailure(
                    command_operation,
                    command_status,
                    external_returncode,
                )
                failure.failure_phase = "migration-readiness"
                FailingController.failure = failure
                stderr = io.StringIO()
                argv = [
                    "preflight",
                    "--project-directory",
                    str(root / "project"),
                    "--project-name",
                    "fixture-project",
                    "--env-file",
                    str(root / "environment"),
                    "--compose-file",
                    str(root / "compose.yaml"),
                    "--backup-dir",
                    str(root / "backup"),
                ]
                with (
                    patch.object(maintenance, "Controller", FailingController),
                    patch.object(maintenance, "DeploymentLock"),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(maintenance.main(argv), 2)
                emitted = stderr.getvalue().encode()
                envelope = json.loads(emitted)
                self.assertEqual(
                    set(envelope),
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
                self.assertEqual(envelope["phase"], "migration-readiness")
                self.assertEqual(envelope["command_operation"], command_operation)
                self.assertEqual(envelope["command_status"], command_status)
                self.assertEqual(envelope["external_returncode"], external_returncode)
                validated = run_suite.parse_maintenance_helper_failure(
                    emitted, expected_operation="preflight"
                )
                self.assertEqual(
                    validated,
                    {
                        "operation": "preflight",
                        "phase": "migration-readiness",
                        "classification": "external-command-failed",
                        "command_operation": command_operation,
                        "command_status": command_status,
                        "external_returncode": external_returncode,
                    },
                )
                suite.failure_context = None
                child = subprocess.CompletedProcess(
                    ["actual-controller"], 2, b"PRIVATE-STDOUT", emitted
                )
                with patch.object(run_suite, "safe_call", return_value=child):
                    with self.assertRaisesRegex(
                        run_suite.HarnessFailure,
                        "shipping maintenance helper preflight failed",
                    ):
                        suite.run_helper(
                            "preflight",
                            suite.run_root / "backup",
                            preflight=True,
                        )
                self.assertEqual(
                    suite.failure_context,
                    {
                        "operation": "maintenance-preflight",
                        "helper_operation": "preflight",
                        "phase": "migration-readiness",
                        "classification": "external-command-failed",
                        "command_operation": command_operation,
                        "command_status": command_status,
                        "external_returncode": external_returncode,
                        "returncode": 2,
                    },
                )
                self.assertNotIn("PRIVATE-STDOUT", json.dumps(suite.failure_context))

    def test_uninitialized_proxy_token_fails_before_configuration_children(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = configured_suite(Path(directory))
            suite.write_compose_fixture()
            suite.update_env.pop("CONFIGPROXY_AUTH_TOKEN")
            assert suite.run_root
            with (
                patch.dict(
                    os.environ,
                    {"CONFIGPROXY_AUTH_TOKEN": "inherited-parent-token"},
                ),
                patch.object(run_suite, "require_call") as compose_child,
                patch.object(run_suite, "safe_call") as helper_child,
                patch.object(run_suite.secrets, "token_urlsafe") as regenerate,
            ):
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure, "runtime environment is unavailable"
                ):
                    suite.compose_command("config")
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure, "runtime environment is unavailable"
                ):
                    suite.run_helper("preflight", suite.run_root / "backup")
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure, "runtime environment is unavailable"
                ):
                    suite.reject_unasserted_accept(suite.run_root / "backup")
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure, "runtime environment is unavailable"
                ):
                    suite.run_updater_refusal()
            compose_child.assert_not_called()
            helper_child.assert_not_called()
            regenerate.assert_not_called()

    def test_default_subprocess_environment_does_not_forward_parent_proxy_token(
        self,
    ) -> None:
        inherited_token = "inherited-parent-token"
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with (
            patch.dict(os.environ, {"CONFIGPROXY_AUTH_TOKEN": inherited_token}),
            patch.object(run_suite.subprocess, "run", return_value=completed) as child,
        ):
            run_suite.safe_call(["docker", "inspect", "container-id"], timeout=5)
        child_environment = child.call_args.kwargs["env"]
        if "CONFIGPROXY_AUTH_TOKEN" in child_environment:
            self.fail("ordinary Docker child inherited the parent proxy token")

    def test_updater_receives_fixture_project_and_refusal_preserves_all_old_ids(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = configured_suite(root)
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="test-generated-proxy-auth-token",
            ):
                suite.write_compose_fixture()
            assert suite.run_root
            old_ids = {
                "web": "web-full-id",
                "proxy": "proxy-full-id",
                "hub": "hub-full-id",
            }
            captured: dict = {}

            def updater_call(_argv, **kwargs):
                captured.update(kwargs)
                return subprocess.CompletedProcess(
                    [],
                    1,
                    (run_suite.EXPECTED_UPDATER_MAJOR_REFUSAL.encode()),
                    b"",
                )

            with (
                patch.object(suite, "snapshot_service_ids", return_value=old_ids),
                patch.object(suite, "wait_ready") as wait_ready,
                patch.object(suite, "run_probe") as updater_smoke,
                patch.object(run_suite, "safe_call", side_effect=updater_call),
            ):
                suite.run_updater_refusal()
            self.assertEqual(
                captured["env"]["COMPOSE_PROJECT_NAME"], suite.project_name
            )
            self.assertEqual(
                captured["env"]["UPDATE_LOCK_FILE"],
                str(suite.run_root / "update.lock"),
            )
            wait_ready.assert_called_once_with()
            updater_smoke.assert_called_once_with("updater-smoke")
            self.assertEqual(
                suite.results["cases"]["routine-updater-refusal-old-stack-usable"][
                    "status"
                ],
                "pass",
            )
            self.assertEqual(
                suite.results["cases"]["routine-updater-refusal-old-stack-usable"][
                    "refusal"
                ],
                "version-change",
            )

    def test_unknown_running_label_does_not_pass_major_version_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = configured_suite(Path(directory))
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="test-generated-proxy-auth-token",
            ):
                suite.write_compose_fixture()
            old_ids = {
                "web": "web-full-id",
                "proxy": "proxy-full-id",
                "hub": "hub-full-id",
            }
            child = subprocess.CompletedProcess(
                [], 1, b"", b"running Hub version is unknown; refusing update"
            )
            with (
                patch.object(suite, "snapshot_service_ids", return_value=old_ids),
                patch.object(suite, "wait_ready") as wait_ready,
                patch.object(suite, "run_probe") as updater_smoke,
                patch.object(run_suite, "safe_call", return_value=child),
            ):
                with self.assertRaisesRegex(
                    run_suite.HarnessFailure,
                    "did not confirm the expected major-version refusal",
                ):
                    suite.run_updater_refusal()
            wait_ready.assert_not_called()
            updater_smoke.assert_not_called()
            self.assertNotIn(
                "routine-updater-refusal-old-stack-usable",
                suite.results["cases"],
            )
            self.assertEqual(
                suite.failure_context,
                {
                    "operation": "routine-updater-refusal",
                    "classification": "major-version-refusal-not-observed",
                    "refusal": "unknown-running-image-label",
                    "returncode": 1,
                },
            )

    def test_updater_refusal_rejects_missing_or_replaced_old_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = configured_suite(root)
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="test-generated-proxy-auth-token",
            ):
                suite.write_compose_fixture()
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
                        [],
                        1,
                        run_suite.EXPECTED_UPDATER_MAJOR_REFUSAL.encode(),
                        b"",
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
            fake_fd = 123
            target = Path(directory).resolve()
            original_open = run_suite.os.open
            original_close = run_suite.os.close

            def scoped_open(path, *args, **kwargs):
                if Path(path).resolve() == target:
                    return fake_fd
                return original_open(path, *args, **kwargs)

            def scoped_close(fd):
                if fd == fake_fd:
                    return None
                return original_close(fd)

            os_proxy = isolated_os_proxy(open=scoped_open, close=scoped_close)

            def ioctl(_fd, _request, attributes, _mutate):
                run_suite.struct.pack_into("=I", attributes, 12, encoded_id)

            with (
                patch.dict("sys.modules", {"fcntl": SimpleNamespace(ioctl=ioctl)}),
                patch.object(run_suite, "os", os_proxy),
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
            [], 1, b"[]\n", f"Error: No such object: {resource['id']}".encode()
        )
        with patch.object(run_suite, "safe_call", return_value=failure):
            suite.reconcile_helper_lab_presence(resource)
        self.assertTrue(resource["removed"])

    def test_lab_inspect_empty_stdout_not_found_remains_supported(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        resource = {"id": "a" * 64, "kind": "fixture-lab", "removed": False}
        failure = subprocess.CompletedProcess(
            [], 1, b"", f"Error: No such object: {resource['id']}".encode()
        )
        with patch.object(run_suite, "safe_call", return_value=failure):
            suite.reconcile_helper_lab_presence(resource)
        self.assertTrue(resource["removed"])

    def test_lab_inspect_nonempty_or_invalid_json_is_not_absence(self) -> None:
        container_id = "a" * 64
        outputs = (
            b'[{"Id":"' + container_id.encode() + b'"}]\n',
            b"{malformed}\n",
            b"{}\n",
            b"null\n",
            b"false\n",
        )
        for stdout in outputs:
            with self.subTest(stdout=stdout):
                suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
                resource = {
                    "id": container_id,
                    "kind": "fixture-lab",
                    "removed": False,
                }
                failure = subprocess.CompletedProcess(
                    [], 1, stdout, f"Error: No such object: {container_id}".encode()
                )
                with (
                    patch.object(run_suite, "safe_call", return_value=failure),
                    self.assertRaises(run_suite.HarnessFailure),
                ):
                    suite.reconcile_helper_lab_presence(resource)
                self.assertFalse(resource["removed"])

    def test_lab_inspect_not_found_diagnostic_must_bind_exact_id(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        container_id = "a" * 64
        resource = {
            "id": container_id,
            "kind": "fixture-lab",
            "removed": False,
        }
        failure = subprocess.CompletedProcess(
            [],
            1,
            b"[]\n",
            f"Error: No such object: {'b' * 64}".encode(),
        )
        with (
            patch.object(run_suite, "safe_call", return_value=failure),
            self.assertRaises(run_suite.HarnessFailure),
        ):
            suite.reconcile_helper_lab_presence(resource)
        self.assertFalse(resource["removed"])

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
                suite,
                "docker_result",
                return_value=subprocess.CompletedProcess(
                    [],
                    1,
                    b"[]\n",
                    f"Error: No such container: {container_id}".encode(),
                ),
            ):
                suite.cleanup_compose()
            self.assertEqual(intent["id"], container_id)
            self.assertTrue(intent["removed"])

    def test_helper_replacement_intents_are_saved_before_cli_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup"
            config = backup / "configuration" / "jupyterhub_maintenance_config.py"
            config.parent.mkdir(parents=True)
            config.write_text("# fixture\n", encoding="utf-8")
            suite = configured_suite(root)
            with patch.object(
                run_suite.secrets,
                "token_urlsafe",
                return_value="test-generated-proxy-auth-token",
            ):
                suite.write_compose_fixture()
            assert suite.project_dir and suite.pool
            project = suite.project_dir
            pool = suite.pool

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
                suite,
                "docker_result",
                return_value=subprocess.CompletedProcess(
                    [],
                    1,
                    b"[]\n",
                    f"Error: No such container: {container_id}".encode(),
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

    def test_network_cleanup_accepts_exact_moby_absence_after_removal(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        network_id = "c" * 64
        record = owned_network_cleanup_record(suite, network_id)
        item = owned_network_cleanup_inspect(suite, network_id)
        outputs = [
            subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b""),
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess(
                [],
                1,
                b"[]\n",
                f"Error response from daemon: network {network_id} not found".encode(),
            ),
        ]
        calls: list[list[str]] = []

        def fake_result(*args, **_kwargs):
            calls.append(list(args))
            return outputs.pop(0)

        suite.docker_result = fake_result
        suite.save_manifest = lambda: None

        suite.cleanup_network()

        self.assertEqual(
            calls,
            [
                ["network", "inspect", network_id],
                ["network", "rm", network_id],
                ["network", "inspect", network_id],
            ],
        )
        self.assertTrue(record["removed"])
        self.assertFalse(record["intent"])
        self.assertIsNone(suite.network_id)
        self.assertIsNone(suite.manifest["network_id"])

    def test_network_cleanup_reconciles_only_exact_id_not_found_before_remove(
        self,
    ) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        network_id = "d" * 64
        record = owned_network_cleanup_record(suite, network_id)
        suite.save_manifest = lambda: None
        calls: list[list[str]] = []
        suite.docker_result = lambda *args, **_kwargs: (
            calls.append(list(args))
            or subprocess.CompletedProcess(
                [],
                1,
                b"[]\n",
                f"Error response from daemon: network {network_id} not found".encode(),
            )
        )

        suite.cleanup_network()

        self.assertEqual(calls, [["network", "inspect", network_id]])
        self.assertTrue(record["removed"])
        self.assertIsNone(suite.network_id)

    def test_network_not_found_classifier_rejects_untrusted_diagnostics(self) -> None:
        network_id = "e" * 64
        cases = (
            subprocess.CompletedProcess(
                [],
                1,
                b"[]\n",
                f"Error response from daemon: network {'f' * 64} not found".encode(),
            ),
            subprocess.CompletedProcess(
                [],
                1,
                b"[]\n",
                f"permission denied inspecting network {network_id}".encode(),
            ),
            subprocess.CompletedProcess(
                [],
                1,
                b'[{"Id":"other"}]',
                f"Error response from daemon: network {network_id} not found".encode(),
            ),
            subprocess.CompletedProcess(
                [],
                0,
                b"[]\n",
                f"Error response from daemon: network {network_id} not found".encode(),
            ),
        )
        for result in cases:
            with self.subTest(returncode=result.returncode, stdout=result.stdout):
                self.assertFalse(
                    run_suite.Suite.docker_network_not_found(result, network_id)
                )

    def test_network_cleanup_unknown_inspection_preserves_owned_record(self) -> None:
        network_id = "a" * 64
        cases = (
            subprocess.CompletedProcess(
                [],
                1,
                b"[]\n",
                f"Error response from daemon: network {'b' * 64} not found".encode(),
            ),
            subprocess.CompletedProcess([], 0, b"not json", b""),
            subprocess.CompletedProcess([], 0, b'[{"Id":"other"}]', b""),
            subprocess.CompletedProcess(
                [],
                1,
                b"[{}]\n",
                f"Error response from daemon: network {network_id} not found".encode(),
            ),
        )
        for result in cases:
            with self.subTest(stdout=result.stdout, returncode=result.returncode):
                suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
                record = owned_network_cleanup_record(suite, network_id)
                calls: list[list[str]] = []

                def fake_result(*args, _result=result, _calls=calls, **_kwargs):
                    _calls.append(list(args))
                    return _result

                suite.docker_result = fake_result
                suite.save_manifest = lambda: None
                with self.assertRaises(run_suite.HarnessFailure):
                    suite.cleanup_network()
                self.assertFalse(record["removed"])
                self.assertEqual(suite.network_id, network_id)
                self.assertEqual(calls, [["network", "inspect", network_id]])
                self.assertEqual(suite.cleanup_failure_context["phase"], "ownership")

    def test_live_network_endpoint_refuses_network_removal(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        network_id = "b" * 64
        record = owned_network_cleanup_record(suite, network_id)
        item = owned_network_cleanup_inspect(
            suite, network_id, endpoints={"live-endpoint": {"Name": "fixture"}}
        )
        calls: list[list[str]] = []
        suite.docker_result = lambda *args, **_kwargs: (
            calls.append(list(args))
            or subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b"")
        )
        suite.save_manifest = lambda: None
        with self.assertRaises(run_suite.HarnessFailure):
            suite.cleanup_network()
        self.assertEqual(calls, [["network", "inspect", network_id]])
        self.assertFalse(record["removed"])
        self.assertEqual(suite.cleanup_failure_context["phase"], "endpoints")
        self.assertEqual(
            suite.cleanup_failure_context["classification"], "endpoints-present"
        )

    def test_network_cleanup_refuses_inspected_id_mismatch(self) -> None:
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        network_id = "9" * 64
        record = owned_network_cleanup_record(suite, network_id)
        item = owned_network_cleanup_inspect(suite, "8" * 64)
        calls: list[list[str]] = []
        suite.docker_result = lambda *args, **_kwargs: (
            calls.append(list(args))
            or subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b"")
        )
        suite.save_manifest = lambda: None
        with self.assertRaises(run_suite.HarnessFailure):
            suite.cleanup_network()
        self.assertEqual(calls, [["network", "inspect", network_id]])
        self.assertFalse(record["removed"])
        self.assertEqual(suite.cleanup_failure_context["phase"], "ownership")
        self.assertEqual(
            suite.cleanup_failure_context["classification"], "inspection-invalid"
        )

    def test_network_remove_failure_preserves_record_and_primary_api_context(self):
        suite = run_suite.Suite(Path.cwd(), run_suite.BASELINE_SHA)
        network_id = "7" * 64
        record = owned_network_cleanup_record(suite, network_id)
        item = owned_network_cleanup_inspect(suite, network_id)
        primary = {
            "operation": "api-probe",
            "checkpoint": "terminals",
            "classification": "http-status",
            "http_status": 200,
        }
        suite.failure_context = primary.copy()
        outputs = [
            subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b""),
            subprocess.CompletedProcess([], 1, b"", b"network is busy"),
            subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b""),
        ]
        calls: list[list[str]] = []

        def fake_result(*args, **_kwargs):
            calls.append(list(args))
            return outputs.pop(0)

        suite.docker_result = fake_result
        suite.save_manifest = lambda: None
        with self.assertRaises(run_suite.HarnessFailure):
            suite.cleanup_network()

        self.assertFalse(record["removed"])
        self.assertEqual(suite.network_id, network_id)
        self.assertEqual(suite.failure_context, primary)
        self.assertEqual(
            suite.cleanup_failure_context,
            {
                "operation": "cleanup-network",
                "phase": "removal",
                "classification": "remove-failed",
                "returncode": 1,
            },
        )
        self.assertEqual(calls[1], ["network", "rm", network_id])

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

    def test_lab_auto_remove_after_term_is_verified_without_rm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "pool" / "users" / "user-a"
            home.mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            container_id = "e" * 64
            record = fixture_lab_cleanup_record(suite, container_id, home)
            item = fixture_lab_cleanup_inspect(suite, container_id, home)
            outputs = [
                subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b""),
                subprocess.CompletedProcess(
                    [],
                    1,
                    b"[]\n",
                    f"Error: No such object: {container_id}".encode(),
                ),
            ]
            calls: list[list[str]] = []
            suite.docker = docker_call_logger(calls)
            suite.docker_result = lambda *_args, **_kwargs: outputs.pop(0)

            suite.remove_owned_container(container_id)

            self.assertTrue(record["removed"])
            self.assertIn(["kill", "--signal=TERM", container_id], calls)
            self.assertFalse(any(call[0] == "rm" for call in calls))

    def test_dynamic_lab_cleanup_records_identity_and_accepts_verified_autoremove(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = root / "pool"
            home = pool / "users" / "user-a"
            home.mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.pool = pool
            suite.image_ids.update(
                {
                    "old_lab": "sha256:" + "a" * 64,
                    "candidate_lab": "sha256:" + "b" * 64,
                }
            )
            container_id = "9" * 64
            item = fixture_lab_cleanup_inspect(suite, container_id, home)
            calls: list[list[str]] = []
            suite.docker = lambda *args, **_kwargs: (
                (container_id + "\n").encode()
                if args[:3] == ("ps", "-aq", "--no-trunc")
                else docker_call_logger(calls)(*args)
            )
            suite.inspect_container = lambda _cid: item
            outputs = [
                subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b""),
                subprocess.CompletedProcess(
                    [],
                    1,
                    b"[]\n",
                    f"Error: No such object: {container_id}".encode(),
                ),
            ]
            suite.docker_result = lambda *_args, **_kwargs: outputs.pop(0)

            suite.cleanup_dynamic_labs({"user-a"})

            resource = suite.manifest["resources"]["containers"][0]
            self.assertEqual(resource["user"], "user-a")
            self.assertEqual(resource["image_id"], item["Image"])
            self.assertEqual(resource["home_source"], str(home.resolve()))
            self.assertTrue(resource["auto_remove"])
            self.assertTrue(resource["removed"])
            self.assertIn(["kill", "--signal=TERM", container_id], calls)
            self.assertFalse(any(call[0] == "rm" for call in calls))

    def test_lab_rm_race_is_removed_only_after_exact_absence_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "pool" / "users" / "user-a"
            home.mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            container_id = "f" * 64
            record = fixture_lab_cleanup_record(suite, container_id, home)
            item = fixture_lab_cleanup_inspect(suite, container_id, home, running=False)
            outputs = [
                subprocess.CompletedProcess([], 0, json.dumps([item]).encode(), b""),
                subprocess.CompletedProcess(
                    [],
                    1,
                    b"[]\n",
                    f"Error: No such container: {container_id}".encode(),
                ),
            ]
            calls: list[list[str]] = []

            def docker(*args, **_kwargs):
                calls.append(list(args))
                if args[0] == "rm":
                    raise run_suite.CommandFailure(1)
                return b""

            suite.docker = docker
            suite.docker_result = lambda *_args, **_kwargs: outputs.pop(0)

            suite.remove_owned_container(container_id)

            self.assertTrue(record["removed"])
            self.assertIn(["rm", container_id], calls)

    def test_lab_term_timeout_does_not_force_remove(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "pool" / "users" / "user-a"
            home.mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            container_id = "d" * 64
            record = fixture_lab_cleanup_record(suite, container_id, home)
            item = fixture_lab_cleanup_inspect(suite, container_id, home)
            calls: list[list[str]] = []
            suite.inspect_owned_container_for_removal = lambda _cid: item
            suite.docker = docker_call_logger(calls)
            with (
                patch.object(run_suite.time, "monotonic", side_effect=[0.0, 61.0]),
                patch.object(run_suite.time, "sleep"),
                self.assertRaisesRegex(run_suite.HarnessFailure, "bounded TERM"),
            ):
                suite.remove_owned_container(container_id)
            self.assertIn(["kill", "--signal=TERM", container_id], calls)
            self.assertFalse(any(call[0] == "rm" for call in calls))
            self.assertFalse(record["removed"])

    def test_lab_cleanup_rejects_changed_owned_facts_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "pool" / "users" / "user-a"
            home.mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            container_id = "c" * 64
            record = fixture_lab_cleanup_record(suite, container_id, home)
            item = fixture_lab_cleanup_inspect(suite, container_id, home)
            item["Name"] = "/lab-other-user"
            suite.inspect_owned_container_for_removal = lambda _cid: item
            calls: list[list[str]] = []
            suite.docker = docker_call_logger(calls)
            with self.assertRaisesRegex(run_suite.HarnessFailure, "identity"):
                suite.remove_owned_container(container_id)
            self.assertEqual(calls, [])
            self.assertFalse(record["removed"])

    def test_lab_cleanup_unknown_inspect_or_wrong_id_is_not_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "pool" / "users" / "user-a"
            home.mkdir(parents=True)
            for result in (
                subprocess.CompletedProcess(
                    [], 1, b"", b"Cannot connect to Docker daemon"
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        [{"Id": "b" * 64, "State": {"Running": False}}]
                    ).encode(),
                    b"",
                ),
            ):
                with self.subTest(returncode=result.returncode):
                    suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
                    container_id = "a" * 64
                    record = fixture_lab_cleanup_record(suite, container_id, home)
                    calls: list[list[str]] = []
                    suite.docker = docker_call_logger(calls)
                    suite.docker_result = lambda *_args, _result=result, **_kwargs: (
                        _result
                    )
                    with self.assertRaises(run_suite.HarnessFailure):
                        suite.remove_owned_container(container_id)
                    self.assertFalse(record["removed"])
                    self.assertEqual(calls, [])

    def test_lab_exactly_absent_id_is_marked_removed_without_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "pool" / "users" / "user-a"
            home.mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            container_id = "b" * 64
            record = fixture_lab_cleanup_record(suite, container_id, home)
            suite.docker = lambda *_args, **_kwargs: self.fail(
                "confirmed-absent container must not be mutated"
            )
            suite.docker_result = lambda *_args, **_kwargs: subprocess.CompletedProcess(
                [],
                1,
                b"[]\n",
                f"Error: No such object: {container_id}".encode(),
            )
            suite.remove_owned_container(container_id)
            self.assertTrue(record["removed"])

    def test_tracked_lab_absent_from_inventory_is_reconciled_by_exact_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pool = root / "pool"
            home = pool / "users" / "user-a"
            home.mkdir(parents=True)
            suite = run_suite.Suite(root, run_suite.BASELINE_SHA)
            suite.pool = pool
            container_id = "8" * 64
            record = fixture_lab_cleanup_record(suite, container_id, home)
            suite.docker = lambda *_args, **_kwargs: b""
            suite.docker_result = lambda *_args, **_kwargs: subprocess.CompletedProcess(
                [],
                1,
                b"[]\n",
                f"Error: No such object: {container_id}".encode(),
            )
            suite.cleanup_dynamic_labs({"user-a"})
            self.assertTrue(record["removed"])

    def test_uncertain_lab_inspection_preserves_network_pool_and_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run-root"
            root.mkdir()
            pool = root / "pool"
            pool.mkdir()
            probe = root / "probe"
            probe.mkdir()
            (probe / "probe-state.json").touch()
            suite = run_suite.Suite(Path(directory), run_suite.BASELINE_SHA)
            suite.run_root = root
            suite.pool = pool
            suite.probe_dir = probe
            suite.created_run_root = True
            primary = {
                "operation": "api-probe",
                "checkpoint": "login-a",
                "classification": "http-status",
                "http_status": 403,
            }
            suite.failure_context = primary.copy()
            suite.docker = lambda *args, **_kwargs: (
                ("a" * 64 + "\n").encode()
                if args[:3] == ("ps", "-aq", "--no-trunc")
                else b""
            )
            suite.lab_names = lambda: {"user-a"}
            suite.inspect_container = lambda _cid: (_ for _ in ()).throw(
                run_suite.HarnessFailure("bounded inspect failed")
            )
            with (
                patch.object(suite, "cleanup_compose"),
                patch.object(suite, "cleanup_owned_tools"),
                patch.object(suite, "cleanup_registry"),
                patch.object(suite, "cleanup_network") as network,
                patch.object(suite, "cleanup_pool") as cleanup_pool,
                patch.object(suite, "remove_run_images"),
                patch.object(suite, "cleanup_run_root") as cleanup_root,
            ):
                failures = suite.cleanup()
            self.assertIn("dynamic-labs", failures)
            self.assertIn("users-network-blocked-by-live-container", failures)
            self.assertIn("xfs-pool-preserved-after-upstream-cleanup-failure", failures)
            self.assertEqual(
                suite.results["cleanup_failure_context"],
                {
                    "operation": "cleanup-dynamic-labs",
                    "classification": "cleanup-incomplete",
                },
            )
            self.assertEqual(suite.failure_context, primary)
            network.assert_not_called()
            cleanup_pool.assert_not_called()
            cleanup_root.assert_not_called()
            self.assertTrue(root.is_dir())


if __name__ == "__main__":
    unittest.main()
