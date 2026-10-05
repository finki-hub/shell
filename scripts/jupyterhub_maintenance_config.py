"""Fixed maintenance wrapper, bind-mounted read-only by the controller."""

import os
from pathlib import Path

_upgrade = os.environ.get("JUPYTERHUB_MAINTENANCE_UPGRADE_DB")
_suppress_cullers = os.environ.get("JUPYTERHUB_MAINTENANCE_SUPPRESS_CULLERS")
if _upgrade not in {"true", "false"} or _suppress_cullers not in {"true", "false"}:
    raise RuntimeError("maintenance wrapper requires an explicit upgrade-db mode")

_config_globals = globals().copy()
with Path("/app/jupyterhub_config.py").open(encoding="utf-8") as _config_file:
    exec(  # ruff: ignore[S102] - execute only the fixed, image-owned JupyterHub config
        compile(_config_file.read(), "/app/jupyterhub_config.py", "exec"),
        _config_globals,
    )

_config = _config_globals["c"]
_config.JupyterHub.upgrade_db = _upgrade == "true"
if _suppress_cullers == "true":
    _culler_names = {"idle-culler-servers", "idle-culler-users"}
    _config.JupyterHub.services = [
        service
        for service in _config.JupyterHub.services
        if service.get("name") not in _culler_names
    ]
    _config.JupyterHub.load_roles = [
        role
        for role in _config.JupyterHub.load_roles
        if role.get("name") not in _culler_names
    ]
