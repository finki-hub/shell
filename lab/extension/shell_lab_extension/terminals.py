"""Terminal manager with a per-container cap."""

from __future__ import annotations

import os
from typing import Any, Final

from jupyter_server_terminals.terminalmanager import TerminalManager
from tornado import web

MAX_TERMINALS_ENV: Final = "LAB_MAX_TERMINALS"
DEFAULT_MAX_TERMINALS: Final = 4


def max_terminals() -> int:
    """The cap from `LAB_MAX_TERMINALS`, falling back to the contract default."""
    try:
        value = int(os.environ.get(MAX_TERMINALS_ENV, ""))
    except ValueError:
        return DEFAULT_MAX_TERMINALS
    return value if value > 0 else DEFAULT_MAX_TERMINALS


class CappedTerminalManager(TerminalManager):
    """Refuses to open more than `LAB_MAX_TERMINALS` ptys.

    `create` is the only HTTP-facing hook: `TerminalRootHandler.post` calls it,
    while terminado's `new_terminal` is also reached from `get_terminal` on a
    websocket reconnect and would refuse a reconnect to a live terminal.
    """

    def create(self, **kwargs: Any) -> dict[str, Any]:  # ruff: ignore[ANN401]
        if len(self.terminals) >= max_terminals():
            raise web.HTTPError(429, "terminal-limit")
        return super().create(**kwargs)
