from __future__ import annotations

import os
from typing import Any, Final

from jupyter_server_terminals.terminalmanager import TerminalManager
from tornado import web

MAX_TERMINALS_ENV: Final = "LAB_MAX_TERMINALS"
DEFAULT_MAX_TERMINALS: Final = 4


def max_terminals() -> int:
    try:
        value = int(os.environ.get(MAX_TERMINALS_ENV, ""))
    except ValueError:
        return DEFAULT_MAX_TERMINALS
    return value if value > 0 else DEFAULT_MAX_TERMINALS


class CappedTerminalManager(TerminalManager):
    """Terminado reaches `new_terminal` from `get_terminal` during reconnect, so
    the cap belongs on the HTTP-facing `create` hook.
    """

    def create(self, **kwargs: Any) -> dict[str, Any]:  # ruff: ignore[ANN401]
        if len(self.terminals) >= max_terminals():
            raise web.HTTPError(429, "terminal-limit")
        return super().create(**kwargs)
