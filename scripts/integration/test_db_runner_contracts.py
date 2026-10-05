"""Pure contract checks for DB fixture construction; no Docker is used."""

from __future__ import annotations

import inspect
import json
import subprocess
import unittest
from unittest.mock import patch

from scripts.integration.test_db_migration import (
    IMAGE_DIGEST,
    IMAGE_ID,
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

        with patch(
            "scripts.integration.test_db_migration.docker", side_effect=fake_docker
        ):
            with self.assertRaises(RuntimeError):
                owner.cleanup()
        self.assertIs(owner.cleanup_verified, False)
        self.assertEqual(owner.cleanup_failures, [name])


if __name__ == "__main__":
    unittest.main()
