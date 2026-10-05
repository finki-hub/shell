from __future__ import annotations

from typing import Any

import pytest

from shell_hub.rollout import verify_rollout


class FakeImage:
    def __init__(self, image_id: str, version: str | None) -> None:
        self.id = image_id
        self.attrs: dict[str, Any] = {"Config": {"Labels": {}}}
        if version:
            self.attrs["Config"]["Labels"]["org.finki-hub.jupyterhub-version"] = version


class FakeContainer:
    def __init__(
        self,
        name: str,
        username: str,
        home: str,
        image: str,
        status: str = "running",
        *,
        home_target: str = "/home/ubuntu",
    ) -> None:
        self.attrs: dict[str, Any] = {
            "Name": f"/{name}",
            "Image": image,
            "State": {"Status": status},
            "Config": {"Labels": {"finki.role": "lab", "finki.user": username}},
            "Mounts": [{"Type": "bind", "Source": home, "Destination": home_target}],
        }


class FakeDocker:
    def __init__(self, containers: list[FakeContainer] | None = None) -> None:
        self.by_reference = {"candidate": FakeImage("sha256:immutable-lab", "6.0.1")}
        self.containers_to_return = containers or []
        self.images = self
        self.containers = self

    def get(self, reference: str) -> FakeImage:
        return self.by_reference[reference]

    def list(self, **kwargs: Any) -> list[FakeContainer]:
        assert kwargs == {"all": True}
        return self.containers_to_return


def test_returns_immutable_image_after_version_and_owned_lab_checks(
    tmp_path: Any,
) -> None:
    pool = tmp_path / "pool"
    home = pool / "users" / "alice"
    home.mkdir(parents=True)
    lab_image = FakeImage("sha256:oldlab", "6.0.1")
    client = FakeDocker(
        [FakeContainer("lab-alice", "alice", str(home), lab_image.id, "exited")]
    )
    client.by_reference[lab_image.id] = lab_image

    assert (
        verify_rollout(
            lab_image="candidate",
            pool_dir=pool,
            lab_user="ubuntu",
            docker_client=client,
            hub_version="6.0.1",
        )
        == "sha256:immutable-lab"
    )


@pytest.mark.parametrize("label", [None, "5.5.1"])
def test_unverifiable_or_mismatched_candidate_image_fails_closed(
    tmp_path: Any, label: str | None
) -> None:
    client = FakeDocker()
    client.by_reference["candidate"] = FakeImage("sha256:lab", label)
    with pytest.raises(RuntimeError, match=r"JupyterHub|version"):
        verify_rollout(
            lab_image="candidate",
            pool_dir=tmp_path,
            lab_user="ubuntu",
            docker_client=client,
            hub_version="6.0.1",
        )


@pytest.mark.parametrize("status", ["running", "exited"])
def test_incompatible_retained_lab_fails_even_when_stopped(
    tmp_path: Any, status: str
) -> None:
    pool = tmp_path / "pool"
    home = pool / "users" / "alice"
    home.mkdir(parents=True)
    old_image = FakeImage("sha256:oldlab", "5.5.1")
    client = FakeDocker(
        [FakeContainer("lab-alice", "alice", str(home), old_image.id, status)]
    )
    client.by_reference[old_image.id] = old_image
    with pytest.raises(RuntimeError, match="Retained"):
        verify_rollout(
            lab_image="candidate",
            pool_dir=pool,
            lab_user="ubuntu",
            docker_client=client,
            hub_version="6.0.1",
        )


def test_incomplete_canonical_lab_identity_fails_closed(tmp_path: Any) -> None:
    client = FakeDocker(
        [FakeContainer("lab-alice", "alice", "/outside/alice", "sha256:old")]
    )
    with pytest.raises(RuntimeError, match="unexpected home bind"):
        verify_rollout(
            lab_image="candidate",
            pool_dir=tmp_path,
            lab_user="ubuntu",
            docker_client=client,
            hub_version="6.0.1",
        )


def test_unrelated_role_labelled_container_is_not_adopted(tmp_path: Any) -> None:
    container = FakeContainer("unrelated", "", "/outside", "sha256:other")
    container.attrs["Config"]["Labels"] = {"finki.role": "lab"}
    client = FakeDocker([container])
    assert (
        verify_rollout(
            lab_image="candidate",
            pool_dir=tmp_path,
            lab_user="ubuntu",
            docker_client=client,
            hub_version="6.0.1",
        )
        == "sha256:immutable-lab"
    )


def test_owned_lab_home_target_uses_configured_lab_user(tmp_path: Any) -> None:
    pool = tmp_path / "pool"
    home = pool / "users" / "alice"
    home.mkdir(parents=True)
    lab_image = FakeImage("sha256:oldlab", "6.0.1")
    client = FakeDocker(
        [
            FakeContainer(
                "lab-alice",
                "alice",
                str(home),
                lab_image.id,
                home_target="/home/sandbox",
            )
        ]
    )
    client.by_reference[lab_image.id] = lab_image
    assert (
        verify_rollout(
            lab_image="candidate",
            pool_dir=pool,
            lab_user="sandbox",
            docker_client=client,
            hub_version="6.0.1",
        )
        == "sha256:immutable-lab"
    )


def test_renamed_container_with_user_label_is_rejected(tmp_path: Any) -> None:
    pool = tmp_path / "pool"
    home = pool / "users" / "alice"
    home.mkdir(parents=True)
    lab = FakeContainer("renamed", "alice", str(home), "sha256:oldlab")
    client = FakeDocker([lab])
    client.by_reference["sha256:oldlab"] = FakeImage("sha256:oldlab", "6.0.1")
    with pytest.raises(RuntimeError, match="Cannot establish ownership"):
        verify_rollout(
            lab_image="candidate",
            pool_dir=pool,
            lab_user="ubuntu",
            docker_client=client,
            hub_version="6.0.1",
        )
