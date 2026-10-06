"""Fail-closed checks for the Hub/Lab runtime pair before Hub opens its DB."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from docker.client import DockerClient
from docker.models.containers import Container


def _lab_home(container: Container, destination: str) -> Path | None:
    for mount in container.attrs.get("Mounts", []):
        if mount.get("Destination") == destination and mount.get("Type") == "bind":
            source = mount.get("Source")
            if source:
                return Path(source).resolve()
    return None


def verify_rollout(
    *,
    lab_image: str,
    pool_dir: Path,
    lab_user: str,
    docker_client: DockerClient,
    hub_version: str | None = None,
) -> str:
    """Return the immutable Lab image ID after validating the built version pair.

    Docker errors and incomplete/unknown metadata intentionally propagate as
    startup failures. Container ownership requires role, user, canonical name,
    and the exact expected home bind; a role label alone is never adopted.
    """
    hub_version = hub_version or version("jupyterhub")
    image = docker_client.images.get(lab_image)
    attrs = image.attrs
    labels = attrs.get("Config", {}).get("Labels") or {}
    lab_version = labels.get("org.finki-hub.jupyterhub-version")
    if not lab_version:
        raise RuntimeError(
            f"Lab image {lab_image!r} has no verified JupyterHub version label"
        )
    if lab_version != hub_version:
        raise RuntimeError(
            f"Hub JupyterHub {hub_version} does not match Lab JupyterHub {lab_version}; "
            "use a matched Hub/Lab image pair or run the approved maintenance migration"
        )

    users_root = (pool_dir / "users").resolve()
    for container in docker_client.containers.list(all=True):
        details = container.attrs
        config = details.get("Config") or {}
        container_labels = config.get("Labels") or {}
        username = container_labels.get("finki.user")
        name = str(details.get("Name", "")).removeprefix("/")
        # Ignore unrelated containers, including role-labelled decoys without
        # a canonical name or user marker. Verify possible Labs exhaustively.
        if not username and not name.startswith("lab-"):
            continue
        role = container_labels.get("finki.role")
        if not username or name != f"lab-{username}" or role != "lab":
            raise RuntimeError(
                f"Cannot establish ownership of Lab container {name or '<unnamed>'}"
            )
        expected_home = (users_root / username).resolve()
        if (
            expected_home.parent != users_root
            or _lab_home(container, f"/home/{lab_user}") != expected_home
        ):
            raise RuntimeError(
                f"Lab container {name} has an unexpected home bind; refusing startup"
            )
        container_image = docker_client.images.get(details["Image"])
        old_version = (container_image.attrs.get("Config", {}).get("Labels") or {}).get(
            "org.finki-hub.jupyterhub-version"
        )
        if old_version != hub_version:
            state = (details.get("State") or {}).get("Status", "unknown")
            raise RuntimeError(
                f"Retained {state} Lab {name} uses JupyterHub {old_version or 'unknown'}, "
                f"not {hub_version}; run the approved maintenance migration before startup"
            )
    return str(image.id)
