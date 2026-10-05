#!/usr/bin/env python3
"""Exercise a real Hub 5.5.1 -> 6.0.1 SQLite migration with upstream ORM.

The disposable fixture is created inside the Hub 5 image using JupyterHub's
ORM; it is never copied from a deployment. Docker containers are individually
named/labeled and removed only after ownership is re-verified. No Docker socket,
network, or production configuration is mounted into the containers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

LABEL = "shell.integration.db-test"
OLD_VERSION = "5.5.1"
NEW_VERSION = "6.0.1"
FULL_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_DIGEST = re.compile(r"^[^@]+@sha256:[0-9a-f]{64}$")
_SUITE_DEADLINE: float | None = None

# This worker runs only inside an immutable Hub image and uses that image's ORM.
# Its JSON artifacts are mode 0600 in a mode 0700 temporary directory; token
# hashes and OAuth client secret never go to stdout or published evidence.
WORKER = r"""
import json, os, secrets, sqlite3, sys
from pathlib import Path
from jupyterhub import orm
from jupyterhub.dbutil import _temp_alembic_ini
from alembic.config import Config
from alembic.script import ScriptDirectory

db_url = "sqlite:////fixture/jupyterhub.sqlite"
mode, output = sys.argv[1], Path(sys.argv[2])

def schema_head():
    with _temp_alembic_ini(db_url) as ini:
        heads = ScriptDirectory.from_config(Config(ini)).get_heads()
    con = sqlite3.connect("/fixture/jupyterhub.sqlite")
    try:
        revision = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()
    if len(heads) != 1 or revision != heads[0]:
        raise RuntimeError("schema head mismatch")
    # This additionally checks that the installed ORM accepts this DB revision.
    orm.new_session_factory(db_url)
    return revision

def snapshot():
    schema = schema_head()
    con = sqlite3.connect("/fixture/jupyterhub.sqlite")
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"users", "api_tokens", "spawners", "roles", "user_role_map", "oauth_clients", "alembic_version"}
        if not required <= tables:
            raise RuntimeError("required ORM tables missing")
        def rows(sql):
            return [dict(row) for row in con.execute(sql)]
        data = {
          "schema": schema,
          "users": rows("SELECT name, admin FROM users ORDER BY name"),
          "tokens": rows("SELECT u.name AS user, t.hashed, t.prefix, t.note, t.scopes, t.client_id FROM api_tokens t JOIN users u ON u.id=t.user_id ORDER BY u.name,t.note"),
          "spawners": rows("SELECT u.name AS user,s.name,s.display_name,s.state,s.user_options,s.oauth_client_id FROM spawners s JOIN users u ON u.id=s.user_id ORDER BY u.name,s.name"),
          "roles": rows("SELECT name,scopes FROM roles ORDER BY name"),
          "user_roles": rows("SELECT u.name AS user,r.name AS role FROM user_role_map m JOIN users u ON u.id=m.user_id JOIN roles r ON r.id=m.role_id ORDER BY u.name,r.name"),
          "oauth_clients": rows("SELECT identifier,redirect_uri,allowed_scopes FROM oauth_clients ORDER BY identifier"),
        }
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity failure")
    finally:
        con.close()
    return data

if mode == "version":
    from importlib.metadata import version
    output.write_text(json.dumps({"jupyterhub": version("jupyterhub")}), encoding="utf-8")
    os.chmod(output, 0o600)
elif mode == "seed":
    Session = orm.new_session_factory(db_url)
    db = Session()
    try:
        if db.query(orm.User).count() or db.query(orm.APIToken).count():
            raise RuntimeError("fixture DB is not empty")
        # Hub's global OAuth client and synthetic user client use random
        # private secrets; none are emitted or retained after the temp fixture.
        clients = [
          orm.OAuthClient(identifier="jupyterhub", secret=secrets.token_urlsafe(32), redirect_uri=""),
          orm.OAuthClient(identifier="integration-fixture", secret=secrets.token_urlsafe(32), redirect_uri="http://127.0.0.1/fixture"),
        ]
        db.add_all(clients)
        role = orm.Role(name="integration-user", description="disposable fixture role", scopes=["read:users!user", "access:servers!user"])
        db.add(role)
        db.flush()
        for name in ("integration-user-a", "integration-user-b"):
            user = orm.User(name=name, admin=False, state=None)
            user.roles.append(role)
            db.add(user)
            db.flush()
            spawner = orm.Spawner(name="", display_name="", state={"fixture": "migration-state-v1"}, user_options={"fixture": "preserve"}, oauth_client_id="integration-fixture")
            spawner.user = user
            db.add(spawner)
            token = orm.APIToken(generated=True, note="integration-spa", client_id="jupyterhub", scopes=["access:servers!user=" + name, "read:users!user=" + name])
            token.token = secrets.token_urlsafe(32)
            token.user = user
            db.add(token)
        db.commit()
    finally:
        db.close()
    data = snapshot()
    output.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(output, 0o600)
elif mode == "snapshot":
    data = snapshot()
    output.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(output, 0o600)
else:
    raise RuntimeError("unknown worker mode")
"""


def docker(
    args: list[str], *, timeout: int, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    if _SUITE_DEADLINE is not None:
        remaining = _SUITE_DEADLINE - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("suite deadline exceeded")
        timeout = min(timeout, max(1, int(remaining)))
    return subprocess.run(
        ["docker", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        timeout=timeout,
        check=False,
        text=True,
    )


class OwnedContainers:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.names: list[str] = []
        self.intents: dict[str, dict[str, object]] = {}
        self.containers: list[str] = []
        self.ids_by_name: dict[str, str] = {}
        self.cleanup_failures: list[str] = []
        self.cleanup_verified: bool | None = None

    def run(
        self,
        image_id: str,
        fixture: Path,
        worker: Path,
        mode: str,
        artifact: Path,
        timeout: int,
    ) -> str:
        name = f"jh6db-{self.run_id}-{len(self.containers)}"
        self.names.append(name)
        self.intents[name] = {
            "run_id": self.run_id,
            "image_id": image_id,
            "purpose": mode,
            "fixture": str(fixture.resolve()),
            "worker": str(worker.resolve()),
        }
        command = (
            ["jupyterhub", "upgrade-db", "-f", "/fixture/test_config.py"]
            if mode == "upgrade"
            else ["jupyterhub", "-f", "/fixture/startup_config.py"]
            if mode == "startup-disabled"
            else ["python", "/fixture_worker.py", mode, f"/fixture/{artifact.name}"]
        )
        created = docker(
            [
                "create",
                "--name",
                name,
                "--label",
                f"{LABEL}={self.run_id}",
                "--network",
                "none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--pids-limit=64",
                "--memory=1g",
                "--cpus=1",
                "--log-driver=none",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "-v",
                f"{fixture}:/fixture:rw",
                "-v",
                f"{worker}:/fixture_worker.py:ro",
                image_id,
                *command,
            ],
            timeout=20,
            capture=True,
        )
        cid = created.stdout.strip()
        if created.returncode or not FULL_CONTAINER_ID.fullmatch(cid):
            raise RuntimeError("could not create owned Hub migration test container")
        self.containers.append(cid)
        self.ids_by_name[name] = cid
        expect_failure = mode == "startup-disabled"
        started = docker(
            ["start", "--attach", cid],
            timeout=timeout,
            capture=expect_failure,
        )
        if bool(started.returncode) != expect_failure:
            raise RuntimeError(
                "Hub migration/startup fixture returned an unexpected exit status"
            )
        return (started.stdout or "") + (started.stderr or "")

    def cleanup(self) -> None:
        errors = []
        self.cleanup_failures.clear()
        for name in reversed(self.names):
            found = docker(
                ["ps", "-aq", "--no-trunc", "--filter", f"name=^/{name}$"],
                timeout=15,
                capture=True,
            )
            if found.returncode:
                errors.append("owned test container lookup failed")
                self.cleanup_failures.append(name)
                continue
            cid = found.stdout.strip()
            if not cid:
                continue
            if not FULL_CONTAINER_ID.fullmatch(cid):
                errors.append("owned test container ID verification failed")
                self.cleanup_failures.append(name)
                continue
            self.ids_by_name[name] = cid
            if cid not in self.containers:
                self.containers.append(cid)
            inspected = docker(
                [
                    "inspect",
                    "--format",
                    "{{json .}}",
                    cid,
                ],
                timeout=15,
                capture=True,
            )
            try:
                item = json.loads(inspected.stdout)
                if not isinstance(item, dict):
                    raise TypeError("unexpected inspect object")
                intent = self.intents[name]
                labels = item["Config"]["Labels"]
                mounts = {
                    (mount["Source"], mount["Destination"], bool(mount["RW"]))
                    for mount in item["Mounts"]
                    if mount["Type"] == "bind"
                }
                expected_mounts = {
                    (str(intent["fixture"]), "/fixture", True),
                    (str(intent["worker"]), "/fixture_worker.py", False),
                }
                owned = (
                    FULL_CONTAINER_ID.fullmatch(item.get("Id", "")) is not None
                    and item["Id"] == cid
                    and item["Name"] == f"/{name}"
                    and item["Image"] == intent["image_id"]
                    and labels.get(LABEL) == self.run_id
                    and item["HostConfig"]["NetworkMode"] == "none"
                    and mounts == expected_mounts
                )
            except (IndexError, KeyError, TypeError, ValueError):
                owned = False
            if inspected.returncode or not owned:
                errors.append("owned test container label verification failed")
                self.cleanup_failures.append(name)
                continue
            # Only exact inspected ID after rechecking our per-run ownership label.
            rm = docker(["rm", "--force", cid], timeout=20)
            if rm.returncode:
                errors.append("owned test container cleanup failed")
                self.cleanup_failures.append(name)
                continue
            absent = docker(
                ["ps", "-aq", "--no-trunc", "--filter", f"id={cid}"],
                timeout=15,
                capture=True,
            )
            if absent.returncode or absent.stdout.strip():
                errors.append("owned test container removal was not verified")
                self.cleanup_failures.append(name)
        for name in self.names:
            remaining = docker(
                ["ps", "-aq", "--no-trunc", "--filter", f"name=^/{name}$"],
                timeout=15,
                capture=True,
            )
            if remaining.returncode or remaining.stdout.strip():
                if name not in self.cleanup_failures:
                    self.cleanup_failures.append(name)
                errors.append("owned test container remains after cleanup")
        self.cleanup_verified = not errors
        if errors:
            raise RuntimeError("; ".join(errors))


class FixtureDirectory:
    """Temporary bind-mount tree retained whenever container cleanup is unsure."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="jh6-db-"))
        os.chmod(self.path, 0o700)
        self.preserve = False

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        if exc_type is not None or self.preserve:
            self.preserve = True
            return
        shutil.rmtree(self.path)


def run_image(
    owner: OwnedContainers,
    image_id: str,
    fixture: Path,
    worker: Path,
    mode: str,
    artifact: Path,
    timeout: int,
) -> str:
    return owner.run(image_id, fixture, worker, mode, artifact, timeout)


def assert_seeded_fixture(snapshot: dict) -> None:
    users = snapshot.get("users", [])
    expected_users = {"integration-user-a", "integration-user-b"}
    if {row.get("name") for row in users} != expected_users or any(
        row.get("admin") not in (0, False) for row in users
    ):
        raise RuntimeError("upstream ORM fixture identity/admin mismatch")
    tokens = snapshot.get("tokens", [])
    if len(tokens) != 2 or {token.get("user") for token in tokens} != expected_users:
        raise RuntimeError("upstream ORM fixture token count mismatch")
    for token in tokens:
        scopes = json.loads(token["scopes"])
        expected = [
            f"access:servers!user={token['user']}",
            f"read:users!user={token['user']}",
        ]
        if (
            token.get("user") not in expected_users
            or token.get("note") != "integration-spa"
            or not token.get("hashed")
            or not token.get("prefix")
            or scopes != expected
        ):
            raise RuntimeError("upstream ORM fixture scoped-token mismatch")
    roles = snapshot.get("roles", [])
    if len(roles) != 1 or roles[0].get("name") != "integration-user":
        raise RuntimeError("upstream ORM fixture role mismatch")
    if json.loads(roles[0]["scopes"]) != ["read:users!user", "access:servers!user"]:
        raise RuntimeError("upstream ORM fixture role scopes mismatch")
    if {
        (row.get("user"), row.get("role")) for row in snapshot.get("user_roles", [])
    } != {(name, "integration-user") for name in expected_users}:
        raise RuntimeError("upstream ORM fixture user-role association mismatch")
    spawners = snapshot.get("spawners", [])
    if len(spawners) != 2 or {row.get("user") for row in spawners} != expected_users:
        raise RuntimeError("upstream ORM fixture spawner association mismatch")
    for spawner in spawners:
        if (
            spawner.get("oauth_client_id") != "integration-fixture"
            or json.loads(spawner["state"]) != {"fixture": "migration-state-v1"}
            or json.loads(spawner["user_options"]) != {"fixture": "preserve"}
        ):
            raise RuntimeError("upstream ORM fixture spawner state mismatch")
    clients = {row.get("identifier") for row in snapshot.get("oauth_clients", [])}
    if not {"jupyterhub", "integration-fixture"} <= clients:
        raise RuntimeError("upstream ORM fixture OAuth client association mismatch")


def assert_cold_sqlite(path: Path) -> None:
    sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    if any(sidecar.exists() for sidecar in sidecars):
        raise RuntimeError("refusing cold DB copy while SQLite sidecars remain")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("cold DB integrity check failed")
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--acknowledge-disposable",
        action="store_true",
        help="confirm this uses a disposable Docker daemon, never production/shared",
    )
    parser.add_argument(
        "--old-image", required=True, help="immutable/local Hub 5.5.1 image ref or ID"
    )
    parser.add_argument(
        "--new-image", required=True, help="immutable/local Hub 6.0.1 image ref or ID"
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--suite-timeout", type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.error("upstream migration command timeout must be 1..120 seconds")
    if not 1 <= args.suite_timeout <= 1800:
        parser.error("suite timeout must be 1..1800 seconds")
    if sys.platform != "linux":
        parser.error(
            "Docker bind-mount fixture requires Linux; use an approved disposable Linux runner"
        )
    if not args.acknowledge_disposable:
        parser.error(
            "--acknowledge-disposable is required before creating test containers"
        )
    compile(WORKER, "fixture_worker.py", "exec")
    if docker(["info"], timeout=20).returncode:
        parser.error("Docker daemon is not available")
    containers = docker(["ps", "-aq"], timeout=20, capture=True)
    if containers.returncode:
        parser.error("could not verify Docker container inventory")
    if containers.stdout.strip():
        parser.error("refusing a Docker daemon with pre-existing containers")
    networks = docker(
        ["network", "ls", "-q", "--filter", "type=custom"], timeout=20, capture=True
    )
    if networks.returncode:
        parser.error("could not verify Docker network inventory")
    if networks.stdout.strip():
        parser.error("refusing a Docker daemon with pre-existing custom networks")
    run_id = uuid.uuid4().hex[:12]
    owner = OwnedContainers(run_id)
    temp_directory: FixtureDirectory | None = None
    safe_result: dict[str, object] = {}
    fixture_cleanup = "not-attempted"
    global _SUITE_DEADLINE
    _SUITE_DEADLINE = time.monotonic() + args.suite_timeout
    try:
        temp_directory = FixtureDirectory()
        with temp_directory as directory:
            temp = Path(directory)
            os.chmod(temp, 0o700)
            fixture = temp / "fixture"
            fixture.mkdir(mode=0o700)
            (fixture / "jupyterhub.sqlite").touch(mode=0o600)
            config = fixture / "test_config.py"
            config.write_text(
                'c.JupyterHub.db_url = "sqlite:////fixture/jupyterhub.sqlite"\n'
                'c.JupyterHub.cookie_secret_file = "/fixture/cookie_secret"\n',
                encoding="utf-8",
            )
            os.chmod(config, 0o600)
            worker = temp / "worker.py"
            worker.write_text(WORKER, encoding="utf-8")
            os.chmod(worker, 0o600)
            # Resolve refs and verify actual installed versions inside each image.
            # `docker run` is never used: all commands have recorded IDs and
            # bounded container cleanup in OwnedContainers.
            image_ids = []
            for index, (ref, expected) in enumerate(
                ((args.old_image, OLD_VERSION), (args.new_image, NEW_VERSION))
            ):
                if not (IMAGE_ID.fullmatch(ref) or IMAGE_DIGEST.fullmatch(ref)):
                    raise RuntimeError(
                        "image arguments must be immutable IDs or digest references"
                    )
                inspect = docker(
                    ["image", "inspect", "--format", "{{.Id}}", ref],
                    timeout=20,
                    capture=True,
                )
                image_id = inspect.stdout.strip()
                if inspect.returncode or not image_id.startswith("sha256:"):
                    raise RuntimeError(
                        "requested Hub image unavailable or not immutable"
                    )
                image_ids.append(image_id)
                version_file = fixture / f"version-{index}.json"
                run_image(owner, image_id, fixture, worker, "version", version_file, 30)
                # The image writes the version response to a file rather than stdout.
                version_raw = version_file.read_text(encoding="utf-8")
                if json.loads(version_raw) != {"jupyterhub": expected}:
                    raise RuntimeError(
                        "Hub image installed version does not match expected major baseline"
                    )
            old_id, new_id = image_ids
            if old_id == new_id:
                raise RuntimeError(
                    "old and candidate Hub images resolved to the same image ID"
                )
            # Initialize and seed entirely with the actual old upstream image.
            run_image(
                owner,
                old_id,
                fixture,
                worker,
                "upgrade",
                fixture / "unused.json",
                args.timeout,
            )
            run_image(
                owner,
                old_id,
                fixture,
                worker,
                "seed",
                fixture / "baseline.json",
                args.timeout,
            )
            baseline = json.loads(
                (fixture / "baseline.json").read_text(encoding="utf-8")
            )
            assert_seeded_fixture(baseline)
            if (
                len(baseline["users"]) != 2
                or len(baseline["tokens"]) != 2
                or len(baseline["spawners"]) != 2
            ):
                raise RuntimeError(
                    "old Hub ORM did not create expected two-user fixture"
                )
            assert_cold_sqlite(fixture / "jupyterhub.sqlite")
            shutil.copy2(fixture / "jupyterhub.sqlite", fixture / "cold-backup.sqlite")
            os.chmod(fixture / "cold-backup.sqlite", 0o600)
            # A Hub 6 server startup with upgrade_db=False must reject the cold
            # Hub 5 schema. This is deliberately distinct from `upgrade-db`,
            # whose CLI authorizes and performs the migration.
            startup_fixture = fixture / "startup-disabled"
            startup_fixture.mkdir(mode=0o700)
            shutil.copy2(
                fixture / "cold-backup.sqlite",
                startup_fixture / "jupyterhub.sqlite",
            )
            os.chmod(startup_fixture / "jupyterhub.sqlite", 0o600)
            (startup_fixture / "cookie_secret").write_bytes(os.urandom(32))
            os.chmod(startup_fixture / "cookie_secret", 0o600)
            startup_config = startup_fixture / "startup_config.py"
            startup_config.write_text(
                'c.JupyterHub.db_url = "sqlite:////fixture/jupyterhub.sqlite"\n'
                'c.JupyterHub.cookie_secret_file = "/fixture/cookie_secret"\n'
                "c.JupyterHub.upgrade_db = False\n"
                'c.JupyterHub.bind_url = "http://127.0.0.1:8000"\n'
                'c.JupyterHub.log_level = "DEBUG"\n',
                encoding="utf-8",
            )
            os.chmod(startup_config, 0o600)
            rejection_log = run_image(
                owner,
                new_id,
                startup_fixture,
                worker,
                "startup-disabled",
                startup_fixture / "unused.json",
                args.timeout,
            )
            normalized_log = rejection_log.lower()
            if "schema" not in normalized_log or not any(
                marker in normalized_log for marker in ("upgrade", "migration")
            ):
                raise RuntimeError(
                    "Hub 6 did not report a migration-disabled cold-schema rejection"
                )
            run_image(
                owner,
                old_id,
                startup_fixture,
                worker,
                "snapshot",
                startup_fixture / "after-rejection.json",
                30,
            )
            after_rejection = json.loads(
                (startup_fixture / "after-rejection.json").read_text(encoding="utf-8")
            )
            if after_rejection != baseline:
                raise RuntimeError(
                    "migration-disabled Hub 6 startup changed the cold schema or fixture identities"
                )
            # Candidate upstream Alembic migration; then ORM validates exact
            # installed head and exact identity/token/role/spawner row snapshot.
            run_image(
                owner,
                new_id,
                fixture,
                worker,
                "upgrade",
                fixture / "unused.json",
                args.timeout,
            )
            run_image(
                owner,
                new_id,
                fixture,
                worker,
                "snapshot",
                fixture / "migrated.json",
                30,
            )
            migrated = json.loads(
                (fixture / "migrated.json").read_text(encoding="utf-8")
            )
            if {k: v for k, v in migrated.items() if k != "schema"} != {
                k: v for k, v in baseline.items() if k != "schema"
            }:
                raise RuntimeError(
                    "Hub 6 migration changed seeded identities or associations"
                )
            # This is schema-idempotence, NOT a claim that Hub server startup ran.
            run_image(
                owner,
                new_id,
                fixture,
                worker,
                "upgrade",
                fixture / "unused.json",
                args.timeout,
            )
            run_image(
                owner,
                new_id,
                fixture,
                worker,
                "snapshot",
                fixture / "repeated.json",
                30,
            )
            repeated = json.loads(
                (fixture / "repeated.json").read_text(encoding="utf-8")
            )
            if repeated != migrated:
                raise RuntimeError("repeated Hub 6 schema migration was not idempotent")
            # Cold whole-fixture restore, including original DB; no new sidecars.
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(fixture / "jupyterhub.sqlite") + suffix).unlink(
                    missing_ok=True
                )
            shutil.copy2(fixture / "cold-backup.sqlite", fixture / "jupyterhub.sqlite")
            os.chmod(fixture / "jupyterhub.sqlite", 0o600)
            run_image(
                owner,
                old_id,
                fixture,
                worker,
                "upgrade",
                fixture / "unused.json",
                args.timeout,
            )
            run_image(
                owner,
                old_id,
                fixture,
                worker,
                "snapshot",
                fixture / "restored.json",
                30,
            )
            restored = json.loads(
                (fixture / "restored.json").read_text(encoding="utf-8")
            )
            if restored != baseline:
                raise RuntimeError(
                    "cold pre-migration restore failed old Hub ORM compatibility"
                )
            safe_result = {
                "status": "pass",
                "test": "upstream-db-migration-and-cold-orm-restore",
                "migration_disabled_cold_startup_rejection": "pass",
                "migration_disabled_startup_preserved_schema_and_identities": "pass",
                "positive_server_startup": "not-tested-by-db-fixture",
                "schema_idempotence": "pass",
                "old_orm_cold_restore_compatibility": "pass",
                "owned_container_cleanup": "pending",
                "old_image_id": old_id,
                "new_image_id": new_id,
                "old_version": OLD_VERSION,
                "new_version": NEW_VERSION,
                "schema_before": baseline["schema"],
                "schema_after": migrated["schema"],
            }
            temp_directory.preserve = True
        _SUITE_DEADLINE = time.monotonic() + 120
        owner.cleanup()
        if owner.cleanup_verified is not True:
            raise RuntimeError("owned DB runner container cleanup was unverified")
        safe_result["owned_container_cleanup"] = "verified"
        if temp_directory is not None:
            try:
                shutil.rmtree(temp_directory.path)
            except OSError:
                fixture_cleanup = "incomplete"
                raise
            fixture_cleanup = "verified"
        safe_result["fixture_cleanup"] = fixture_cleanup
        print(json.dumps(safe_result, sort_keys=True))
        return 0
    except (
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        RuntimeError,
        ValueError,
    ):
        _SUITE_DEADLINE = time.monotonic() + 120
        cleanup_exception = False
        try:
            owner.cleanup()
        except Exception:
            cleanup_exception = True
        cleanup_verified = owner.cleanup_verified is True and not cleanup_exception
        if cleanup_verified and temp_directory is not None:
            try:
                shutil.rmtree(temp_directory.path)
                fixture_cleanup = "verified"
            except OSError:
                fixture_cleanup = "incomplete"
                temp_directory.preserve = True
        elif temp_directory is not None:
            temp_directory.preserve = True
            fixture_cleanup = "preserved-owned-containers-unverified"
        print(
            json.dumps(
                {
                    "status": "fail",
                    "reason": "migration-fixture-or-cleanup-error",
                    "owned_container_cleanup": "verified"
                    if cleanup_verified
                    else "incomplete-or-unknown",
                    "fixture_preserved": fixture_cleanup != "verified",
                    "fixture_cleanup": fixture_cleanup,
                    "failed_cleanup_stages": []
                    if cleanup_verified
                    else ["db-owned-container-cleanup"],
                    "remaining_owned_names": owner.cleanup_failures,
                    "remaining_owned_resources": [
                        {
                            "name": name,
                            "id": owner.ids_by_name.get(name, "unknown"),
                        }
                        for name in owner.cleanup_failures
                    ],
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
