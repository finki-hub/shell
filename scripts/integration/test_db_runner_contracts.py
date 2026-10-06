"""Pure contract checks for DB fixture construction; no Docker is used."""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.integration.test_db_migration import (
    DISPLAY_NAME_COUNT_QUERY,
    IMAGE_DIGEST,
    IMAGE_ID,
    SPAWNER_SHARED_SELECT,
    WORKER,
    OwnedContainers,
    assert_seeded_fixture,
    main,
)


def fixture() -> dict:
    users = [
        {"name": name, "admin": 0}
        for name in ("integration-user-a", "integration-user-b")
    ]
    return {
        "schema": "expected-head",
        "users": users,
        "tokens": [
            {
                "user": name,
                "hashed": "stored-hash-redacted",
                "prefix": "fixture-prefix",
                "note": "integration-spa",
                "scopes": json.dumps(
                    [f"access:servers!user={name}", f"read:users!user={name}"]
                ),
            }
            for name in ("integration-user-a", "integration-user-b")
        ],
        "roles": [
            {
                "name": "integration-user",
                "scopes": json.dumps(["read:users!user", "access:servers!user"]),
            }
        ],
        "user_roles": [
            {"user": name, "role": "integration-user"}
            for name in ("integration-user-a", "integration-user-b")
        ],
        "spawners": [
            {
                "user": name,
                "oauth_client_id": "integration-fixture",
                "state": json.dumps({"fixture": "migration-state-v1"}),
                "user_options": json.dumps({"fixture": "preserve"}),
            }
            for name in ("integration-user-a", "integration-user-b")
        ],
        "oauth_clients": [
            {"identifier": "jupyterhub"},
            {"identifier": "integration-fixture"},
        ],
    }


class DatabaseRunnerContracts(unittest.TestCase):
    def test_worker_source_is_syntactically_valid(self) -> None:
        compile(WORKER, "fixture_worker.py", "exec")

    def test_old_orm_seed_uses_only_old_spawner_constructor_fields(self) -> None:
        seed_line = next(
            line.strip()
            for line in WORKER.splitlines()
            if "spawner = orm.Spawner(" in line
        )
        expression = ast.parse(seed_line).body[0].value
        self.assertEqual(
            {keyword.arg for keyword in expression.keywords},
            {"name", "state", "user_options", "oauth_client_id"},
        )

        class StrictOldSpawner:
            def __init__(self, name, state, user_options, oauth_client_id):
                self.values = (name, state, user_options, oauth_client_id)

        namespace = {"orm": SimpleNamespace(Spawner=StrictOldSpawner)}
        exec(seed_line, namespace)
        self.assertIsInstance(namespace["spawner"], StrictOldSpawner)

    def test_shared_spawner_snapshot_query_runs_on_actual_old_sqlite_shape(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
            "CREATE TABLE spawners ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, "
            "state TEXT, user_options TEXT, oauth_client_id TEXT);"
            "INSERT INTO users VALUES (1, 'fixture-user');"
            "INSERT INTO spawners VALUES (1, 1, '', '{}', '{}', 'fixture-client');"
        )
        try:
            rows = [dict(row) for row in connection.execute(SPAWNER_SHARED_SELECT)]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["user"], "fixture-user")
            self.assertEqual(rows[0]["oauth_client_id"], "fixture-client")
            self.assertNotIn("display_name", rows[0])
        finally:
            connection.close()

    def test_candidate_display_name_extension_requires_nullable_migrated_rows(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE TABLE spawners (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(DISPLAY_NAME_COUNT_QUERY)
            connection.execute("ALTER TABLE spawners ADD COLUMN display_name TEXT")
            connection.execute(
                "INSERT INTO spawners (id, name) VALUES (1, ''), (2, 'named')"
            )
            display_column = next(
                row
                for row in connection.execute("PRAGMA table_info(spawners)")
                if row[1] == "display_name"
            )
            self.assertEqual(display_column[3], 0)
            self.assertEqual(
                connection.execute(DISPLAY_NAME_COUNT_QUERY).fetchone(), (2, 0)
            )
            connection.execute("UPDATE spawners SET display_name='changed' WHERE id=1")
            self.assertEqual(
                connection.execute(DISPLAY_NAME_COUNT_QUERY).fetchone(), (2, 1)
            )
        finally:
            connection.close()

    def test_fixture_requires_both_user_scoped_token_and_spawner_associations(
        self,
    ) -> None:
        assert_seeded_fixture(fixture())
        bad = fixture()
        bad["tokens"][0]["scopes"] = json.dumps(["read:users"])
        with self.assertRaises(RuntimeError):
            assert_seeded_fixture(bad)
        missing_spawner = fixture()
        missing_spawner["spawners"].pop()
        with self.assertRaises(RuntimeError):
            assert_seeded_fixture(missing_spawner)

    def test_container_image_references_are_immutable_only(self) -> None:
        self.assertIsNotNone(IMAGE_ID.fullmatch("sha256:" + "a" * 64))
        self.assertIsNotNone(
            IMAGE_DIGEST.fullmatch("registry.example/hub@sha256:" + "b" * 64)
        )
        for ref in ("hub:latest", "hub:test", "registry.example/hub"):
            self.assertIsNone(IMAGE_ID.fullmatch(ref))
            self.assertIsNone(IMAGE_DIGEST.fullmatch(ref))

    def test_cold_schema_startup_rejection_is_separate_from_upgrade_cli(self) -> None:
        source = inspect.getsource(OwnedContainers.run)
        self.assertIn('"startup-disabled"', source)
        self.assertIn('"jupyterhub", "-f", "/fixture/startup_config.py"', source)
        self.assertIn('"jupyterhub", "upgrade-db"', source)
        migration = inspect.getsource(main)
        self.assertIn("c.JupyterHub.upgrade_db = False", migration)
        self.assertIn("after_rejection != baseline", migration)

    def test_failed_owned_container_removal_is_reported_unverified(self) -> None:
        owner = OwnedContainers("a" * 12)
        name = f"jh6db-{owner.run_id}-0"
        container_id = "b" * 64
        fixture_path = "/tmp/fixture"
        worker_path = "/tmp/worker.py"
        owner.names.append(name)
        owner.intents[name] = {
            "run_id": owner.run_id,
            "image_id": "sha256:" + "c" * 64,
            "purpose": "snapshot",
            "fixture": fixture_path,
            "worker": worker_path,
        }
        owner.ids_by_name[name] = container_id
        owner.containers.append(container_id)
        inspect = {
            "Id": container_id,
            "Name": "/" + name,
            "Image": owner.intents[name]["image_id"],
            "Config": {"Labels": {"shell.integration.db-test": owner.run_id}},
            "HostConfig": {"NetworkMode": "none"},
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": fixture_path,
                    "Destination": "/fixture",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": worker_path,
                    "Destination": "/fixture_worker.py",
                    "RW": False,
                },
            ],
        }

        def fake_docker(args, *, timeout, capture=False):
            if args[:2] == ["ps", "-aq"]:
                return subprocess.CompletedProcess(args, 0, container_id, "")
            if args[0] == "inspect":
                return subprocess.CompletedProcess(args, 0, json.dumps(inspect), "")
            if args[:2] == ["rm", "--force"]:
                return subprocess.CompletedProcess(args, 1, "", "rm failed")
            raise AssertionError("unexpected Docker boundary call")

        with (
            patch(
                "scripts.integration.test_db_migration.docker", side_effect=fake_docker
            ),
            self.assertRaises(RuntimeError),
        ):
            owner.cleanup()
        self.assertIs(owner.cleanup_verified, False)
        self.assertEqual(owner.cleanup_failures, [name])

    @staticmethod
    def _owner_with_container(container_id: str) -> tuple[OwnedContainers, str, dict]:
        owner = OwnedContainers("a" * 12)
        name = f"jh6db-{owner.run_id}-0"
        fixture_path = "/tmp/fixture"
        worker_path = "/tmp/worker.py"
        intent = {
            "run_id": owner.run_id,
            "image_id": "sha256:" + "c" * 64,
            "purpose": "snapshot",
            "fixture": fixture_path,
            "worker": worker_path,
        }
        owner.names.append(name)
        owner.intents[name] = intent
        owner.ids_by_name[name] = container_id
        owner.containers.append(container_id)
        inspect = {
            "Id": container_id,
            "Name": "/" + name,
            "Image": intent["image_id"],
            "Config": {"Labels": {"shell.integration.db-test": owner.run_id}},
            "HostConfig": {"NetworkMode": "none"},
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": fixture_path,
                    "Destination": "/fixture",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": worker_path,
                    "Destination": "/fixture_worker.py",
                    "RW": False,
                },
            ],
        }
        return owner, name, inspect

    def test_db_cleanup_uses_full_ps_ids_and_removes_exact_inspected_id(self) -> None:
        full_id = "d" * 64
        owner, name, inspected_item = self._owner_with_container(full_id)
        calls: list[list[str]] = []
        name_lookups = 0

        def fake_ps_id(args: list[str]) -> str:
            # Docker's default list output is abbreviated; --no-trunc exposes
            # the same container's immutable ID for comparison with inspect.
            return full_id if "--no-trunc" in args else full_id[:12]

        self.assertEqual(fake_ps_id(["ps", "-aq"]), full_id[:12])
        self.assertEqual(fake_ps_id(["ps", "-aq", "--no-trunc"]), full_id)

        def fake_docker(args, *, timeout, capture=False):
            nonlocal name_lookups
            calls.append(args)
            if args[:2] == ["ps", "-aq"]:
                self.assertIn("--no-trunc", args)
                filter_value = args[args.index("--filter") + 1]
                if filter_value.startswith("name="):
                    name_lookups += 1
                    return subprocess.CompletedProcess(
                        args, 0, fake_ps_id(args) if name_lookups == 1 else "", ""
                    )
                if filter_value == f"id={full_id}":
                    return subprocess.CompletedProcess(args, 0, "", "")
            if args[0] == "inspect":
                return subprocess.CompletedProcess(
                    args, 0, json.dumps(inspected_item), ""
                )
            if args[:2] == ["rm", "--force"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            raise AssertionError(f"unexpected Docker call: {args[0]}")

        with patch(
            "scripts.integration.test_db_migration.docker", side_effect=fake_docker
        ):
            owner.cleanup()

        self.assertIs(owner.cleanup_verified, True)
        self.assertEqual(
            next(call[-1] for call in calls if call[:2] == ["rm", "--force"]),
            full_id,
        )
        self.assertTrue(any(call[:3] == ["ps", "-aq", "--no-trunc"] for call in calls))
        self.assertEqual(owner.cleanup_failures, [])
        self.assertEqual(owner.ids_by_name[name], full_id)

    def test_db_cleanup_refuses_different_full_inspect_id_without_removal(self) -> None:
        listed_id = "e" * 64
        different_id = "f" * 64
        owner, name, inspected_item = self._owner_with_container(different_id)
        rm_calls: list[list[str]] = []
        lookup_count = 0

        def fake_docker(args, *, timeout, capture=False):
            nonlocal lookup_count
            if args[:2] == ["ps", "-aq"]:
                self.assertIn("--no-trunc", args)
                filter_value = args[args.index("--filter") + 1]
                if filter_value.startswith("name="):
                    lookup_count += 1
                    return subprocess.CompletedProcess(args, 0, listed_id, "")
            if args[0] == "inspect":
                return subprocess.CompletedProcess(
                    args, 0, json.dumps(inspected_item), ""
                )
            if args[:2] == ["rm", "--force"]:
                rm_calls.append(args)
                return subprocess.CompletedProcess(args, 0, "", "")
            raise AssertionError(f"unexpected Docker call: {args[0]}")

        with (
            patch(
                "scripts.integration.test_db_migration.docker", side_effect=fake_docker
            ),
            self.assertRaises(RuntimeError),
        ):
            owner.cleanup()

        self.assertIs(owner.cleanup_verified, False)
        self.assertEqual(owner.cleanup_failures, [name])
        self.assertEqual(rm_calls, [])


if __name__ == "__main__":
    unittest.main()
