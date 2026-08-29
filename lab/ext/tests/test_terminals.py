import pytest
from jupyter_server_terminals.terminalmanager import TerminalManager
from tornado import web

from finki_lab.terminals import (
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
    # Given LAB_MAX_TERMINALS set or unset
    monkeypatch.delenv("LAB_MAX_TERMINALS", raising=False)
    if raw is not None:
        monkeypatch.setenv("LAB_MAX_TERMINALS", raw)

    # When / Then
    assert max_terminals() == expected


def test_create_is_refused_at_the_cap(manager, monkeypatch):
    # Given the cap already reached
    monkeypatch.setenv("LAB_MAX_TERMINALS", "2")
    manager.terminals = {"1": object(), "2": object()}

    # When another terminal is requested
    with pytest.raises(web.HTTPError) as excinfo:
        manager.create()

    # Then the request is refused with the contract's code
    assert excinfo.value.status_code == 429
    assert excinfo.value.log_message == "terminal-limit"


def test_create_delegates_below_the_cap(manager, monkeypatch):
    # Given one terminal open out of two allowed
    monkeypatch.setenv("LAB_MAX_TERMINALS", "2")
    manager.terminals = {"1": object()}
    monkeypatch.setattr(TerminalManager, "create", lambda self, **kwargs: {"name": "2"})

    # When another terminal is requested
    model = manager.create()

    # Then the base manager creates it
    assert model == {"name": "2"}
