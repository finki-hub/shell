import pytest
from jupyter_server_terminals.terminalmanager import TerminalManager
from tornado import web

from shell_lab_extension.terminals import (
    DEFAULT_MAX_TERMINALS,
    CappedTerminalManager,
    max_terminals,
)


@pytest.fixture
def manager():
    return CappedTerminalManager(shell_command=["/bin/bash"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_MAX_TERMINALS),
        ("2", 2),
        ("0", DEFAULT_MAX_TERMINALS),
        ("-1", DEFAULT_MAX_TERMINALS),
        ("many", DEFAULT_MAX_TERMINALS),
    ],
)
def test_max_terminals_reads_the_environment(monkeypatch, raw, expected):
    monkeypatch.delenv("LAB_MAX_TERMINALS", raising=False)
    if raw is not None:
        monkeypatch.setenv("LAB_MAX_TERMINALS", raw)

    assert max_terminals() == expected


def test_create_is_refused_at_the_cap(manager, monkeypatch):
    monkeypatch.setenv("LAB_MAX_TERMINALS", "2")
    manager.terminals = {"1": object(), "2": object()}

    with pytest.raises(web.HTTPError) as excinfo:
        manager.create()

    assert excinfo.value.status_code == 429
    assert excinfo.value.log_message == "terminal-limit"


def test_create_delegates_below_the_cap(manager, monkeypatch):
    monkeypatch.setenv("LAB_MAX_TERMINALS", "2")
    manager.terminals = {"1": object()}
    monkeypatch.setattr(TerminalManager, "create", lambda self, **kwargs: {"name": "2"})

    model = manager.create()

    assert model == {"name": "2"}
