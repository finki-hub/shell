# Python Sonar review

Review of the supplied 40-issue snapshot, primarily covering `hub/` Python files
and `lab/extension/`, with related infrastructure dispositions noted below.
With owner authorization, 11 architecture security
findings were marked `ACCEPTED` and the pathlib S930 finding `FALSE_POSITIVE`, with detailed
Sonar comments. These dispositions are not code fixes or claims that the
accepted risks were eliminated. No inline analyzer suppressions were added.

## Code fixes

- S1192: share `HUB_INTERNAL_URL` between the Hub bind/connect settings and spawner.
- S3626: remove the redundant return from the intentionally empty XSRF override.
- S7503: make environment creation synchronous in the backend protocol,
  implementation, caller, and test double. This operation already performs only
  synchronous Hub user creation; it must remain on the event-loop thread rather
  than moving Hub/database state into a worker thread.
- S5863: save the first username derivation before checking repeatability.
- S5778: construct settings/backends before exception assertions, leaving only
  the operation under test inside each context.

## Owner-authorized security dispositions

### S1313 / S5332: internal Hub address and HTTP

Snapshot keys ending `bZB` through `bZG` refer to the same endpoint. The address
`172.30.0.1:8081` is the users bridge's host gateway, shared with Compose's proxy
error target and health check. The Hub uses host networking while spawned labs
use `finki-hub-shell-users`; replacing the address with loopback would break
lab-to-Hub communication. The literal is now centralized, not hidden.

The HTTP findings are **not automatically false positives**: API tokens can
traverse this bridge without transport encryption. Retaining HTTP preserves the
existing deployment. The owner-authorized `ACCEPTED` disposition relies on a
trusted single-host
network boundary, appropriate host firewall rules, and isolation from unrelated
workloads. External TLS alone does not encrypt this hop. If those assumptions
do not hold, deploy internal TLS and update Hub, spawner, proxy, and health-check
configuration together. Acceptance is recorded with explanatory Sonar comments;
the underlying cleartext transport risk remains.

### S5443: container temporary filesystems

Snapshot keys ending `bZH`, `bZI`, and `bZJ` concern Docker tmpfs mount targets,
not host-side creation of predictable files in shared temporary directories.
Each lab receives separate, size-limited tmpfs mounts for `/tmp`, `/var/tmp`,
and `/dev/shm`, with sticky-bit mode 1777 and `nosuid,nodev`. Labs run with
configured non-root UID/GID, all capabilities dropped, no-new-privileges, and
a read-only root filesystem. No temporary filesystem is shared across lab
containers by this configuration. Preserve these standard paths for application
compatibility; these findings are `ACCEPTED`, conditional on maintaining
the container isolation boundary. This is not a claim that arbitrary application
temporary-file handling is safe.

### Related infrastructure: root execution and HTTP health check

Related architecture review also covers root execution and internal HTTP health
checks. The detailed infrastructure dispositions and
rationale are recorded in Sonar. Root execution remains an accepted privileged
deployment boundary, not a removed risk; the health-check HTTP disposition uses
the same single-host network assumptions described above. Dockerfile changes
also reduce dependency-installation risks through wheel-only installation.

### S930: modern pathlib argument (false positive)

Key `AaEyULsuC6DD4rHk_bZQ` points to `Path.is_file(follow_symlinks=False)` in
`lab/extension/shell_lab_extension/__init__.py`, not a chmod call in the current
source. Python added this keyword in 3.13, and both packages require 3.15.
Keep the argument: it prevents upload cleanup from treating a symlink target as
a regular leftover file. `Path.chmod(follow_symlinks=False)` is likewise valid
since Python 3.10 and must not be weakened if separately reported.
This finding is `FALSE_POSITIVE` with the version-specific API
rationale, rather than changing safe code or marking it fixed.

Primary API reference:
https://docs.python.org/3.15/library/pathlib.html#pathlib.Path.is_file

## Verification evidence

Both projects were validated under WSL Ubuntu on CPython 3.15.2 using isolated
Linux virtual environments outside the repository. Hub: 129 tests passed. Lab:
42 tests passed, including all bounded HTTP integration fixtures with pytest
teardown; seven upstream warnings concern synchronous ContentsManager deprecation
in future Jupyter Server 3.0. Both projects passed Ruff lint/format, native Linux
mypy, and wheel/sdist builds. Both lockfiles were fully refreshed for the Python
3.15 contract. The readiness path variable was renamed to avoid shadowing the
new Python 3.15 `sentinel` builtin.

Windows still lacks `fcntl` and `os.statvfs`, so native Windows execution is not
the runtime evidence for these Linux deployment components. No checks were
suppressed and no persistent standalone services were started.
