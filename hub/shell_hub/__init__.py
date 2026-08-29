"""Shell's Hub package: identity, admission and quotas for JupyterHub.

The package is loaded from ``jupyterhub_config.py`` inside the ``shell-hub``
image. It contributes an authenticator with no login page, three ``/hub/lab/*``
endpoints for the SPA, and a pre-spawn admission hook that provisions home
directories and their XFS project quotas.
"""

from __future__ import annotations

from typing import Final

__version__: Final[str] = "0.1.0"

__all__ = ["__version__"]
