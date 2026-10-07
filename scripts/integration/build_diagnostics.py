"""Private, bounded build capture; never expose commands or child output."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, NamedTuple

LOG_CAP = 2 * 1024 * 1024
PREFIX_CAP = 192
CHUNK = 8192
GRACE = 1.0
JOIN = 1.0
STAGES = frozenset({"internal", "builder", "final", "jupyter", "stage-0", "stage-1"})
CLASSES = frozenset(
    "success launch-failure nonzero timeout log-write-failure reader-incomplete".split()
)
KEYS = frozenset(
    (
        "classification elapsed_ms last_progress_age_ms vertex step stage bytes_read "
        "bytes_stored cap_bytes returncode reader_complete client_reaped "
        "free_disk_bytes memory_available_bytes"
    ).split()
)
PROGRESS = re.compile(rb"^#([0-9]{1,6}) \[([a-z0-9-]+)(?: ([0-9]{1,6})/[0-9]{1,6})?\]")
REQUIRED = frozenset("elapsed_ms bytes_read bytes_stored cap_bytes".split())


def validate(value: dict[str, Any]) -> dict[str, Any]:
    """Reject unknown fields and unsafe values; return an independent snapshot."""
    if (
        set(value) != KEYS
        or not isinstance(value["classification"], str)
        or value["classification"] not in CLASSES
    ):
        raise ValueError("invalid-build-diagnostics")
    if value["stage"] is not None and (
        not isinstance(value["stage"], str) or value["stage"] not in STAGES
    ):
        raise ValueError("invalid-build-diagnostics")
    for key in KEYS - {"classification", "stage", "reader_complete", "client_reaped"}:
        item = value[key]
        if item is None and key in REQUIRED:
            raise ValueError("invalid-build-diagnostics")
        if item is not None and (
            type(item) is not int or (key != "returncode" and item < 0)
        ):
            raise ValueError("invalid-build-diagnostics")
    if any(type(value[k]) is not bool for k in ("reader_complete", "client_reaped")):
        raise ValueError("invalid-build-diagnostics")
    if (
        not 1 <= value["cap_bytes"] <= LOG_CAP
        or value["bytes_stored"] > value["cap_bytes"]
    ):
        raise ValueError("invalid-build-diagnostics")
    return value.copy()


class BuildResult(NamedTuple):
    diagnostics: dict[str, Any]
    launched: bool


class Drain:
    """One owner for pipe/log handles; fixed-size reads and bounded line prefix."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.lock = threading.Lock()
        self.read = 0
        self.stored = 0
        self.vertex: int | None = None
        self.step: int | None = None
        self.stage: str | None = None
        self.progress: float | None = None
        self.failed = False

    def consume(self, data: bytes, prefix: bytearray) -> None:
        for part in data.splitlines(keepends=True):
            prefix.extend(part[: max(0, PREFIX_CAP - len(prefix))])
            if part.endswith(b"\n"):
                match = PROGRESS.match(prefix)
                if match and match[2].decode("ascii") in STAGES:
                    with self.lock:
                        self.vertex = int(match[1])
                        self.stage = match[2].decode("ascii")
                        self.step = int(match[3]) if match[3] else None
                        self.progress = time.monotonic()
                prefix.clear()

    def run(self, pipe: Any, log: Any) -> None:
        prefix = bytearray()
        try:
            while data := pipe.read(CHUNK):
                with self.lock:
                    self.read += len(data)
                    remaining = self.cap - self.stored
                if remaining and not self.failed:
                    try:
                        written = log.write(data[:remaining])
                        with self.lock:
                            self.stored += written
                    except Exception:
                        self.failed = True
                self.consume(data, prefix)
        except Exception:
            self.failed = True
        finally:
            # Never close a still-reading pipe from the controller thread.
            for stream in (log, pipe):
                try:
                    stream.close()
                except Exception:
                    self.failed = True


def resources(root: Path) -> tuple[int | None, int | None]:
    try:
        disk = shutil.disk_usage(root).free
    except OSError:
        disk = None
    memory = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if re.fullmatch(r"MemAvailable:\s+[0-9]+ kB", line):
                memory = int(line.split()[1]) * 1024
    except OSError:
        pass
    return disk, memory


def terminate(child: subprocess.Popen[bytes]) -> bool:
    """Signal only this launched group, then bounded reap; not daemon cancellation."""
    signals = (
        (signal.SIGTERM, signal.SIGKILL) if os.name == "posix" else (signal.SIGTERM,)
    )
    for sig in signals:
        try:
            if os.name == "posix":
                os.killpg(child.pid, sig)
            elif child.poll() is None:
                child.kill()
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=GRACE)
        except subprocess.TimeoutExpired:
            continue
        # A reaped leader does not imply its descendants exited.
        if os.name == "posix" and sig == signal.SIGTERM:
            time.sleep(GRACE)
            continue
        return True
    return child.poll() is not None


def run_build(
    argv: list[str], *, cwd: Path, log_path: Path, timeout: float, cap: int = LOG_CAP
) -> BuildResult:
    if type(cap) is not int or not 1 <= cap <= LOG_CAP or timeout <= 0:
        raise ValueError("invalid-build-limits")
    started = time.monotonic()
    drain = Drain(cap)
    child: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    classification = "launch-failure"
    reaped = True
    fd: int | None = None
    try:
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        log = os.fdopen(fd, "wb", buffering=0)
        fd = None
    except OSError:
        classification = "log-write-failure"
    else:
        env = os.environ.copy()
        env.pop("CONFIGPROXY_AUTH_TOKEN", None)
        try:
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                start_new_session=os.name == "posix",
            )
        except OSError:
            log.close()
        else:
            reader = threading.Thread(
                target=drain.run, args=(child.stdout, log), daemon=True
            )
            try:
                reader.start()
                child.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
                classification = "success" if child.returncode == 0 else "nonzero"
            except subprocess.TimeoutExpired:
                classification = "timeout"
                reaped = terminate(child)
            except Exception:
                classification = "launch-failure"
                reaped = terminate(child)
                if reader.ident is None:
                    log.close()
                    if child.stdout is not None:
                        child.stdout.close()
            except BaseException:
                terminate(child)
                raise
            finally:
                if reader.ident is not None:
                    reader.join(JOIN)
            if reader.is_alive():
                # Also terminate inherited same-group pipe holders after leader exit.
                reaped = terminate(child)
                reader.join(JOIN)
                classification = (
                    "reader-incomplete" if reader.is_alive() else classification
                )
            if drain.failed:
                classification = "log-write-failure"
    finally:
        if fd is not None:
            os.close(fd)
    now = time.monotonic()
    disk, memory = resources(cwd)
    with drain.lock:
        age = None if drain.progress is None else int((now - drain.progress) * 1000)
        snapshot = {
            "classification": classification,
            "elapsed_ms": int((now - started) * 1000),
            "last_progress_age_ms": age,
            "vertex": drain.vertex,
            "step": drain.step,
            "stage": drain.stage,
            "bytes_read": drain.read,
            "bytes_stored": drain.stored,
            "cap_bytes": cap,
            "returncode": child.poll() if child else None,
            "reader_complete": reader is None or not reader.is_alive(),
            "client_reaped": reaped,
            "free_disk_bytes": disk,
            "memory_available_bytes": memory,
        }
    return BuildResult(validate(snapshot), child is not None)
