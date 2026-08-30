from __future__ import annotations


class FakeDocker:
    def __init__(self, *, ping_error: Exception | None = None) -> None:
        self._ping_error = ping_error

    def ping(self) -> bool:
        if self._ping_error is not None:
            raise self._ping_error
        return True
