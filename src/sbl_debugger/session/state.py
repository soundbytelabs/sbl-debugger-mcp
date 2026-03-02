"""Persistent target execution state tracking.

Every tool that changes execution state (halt, continue, step, reset, load)
updates the TargetState. Tools that need to know the state (snapshot, status)
read from it, avoiding the "consumed stop event" bug where drain_events()
returns empty and the tool assumes the target is running.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sbl_debugger.bridge.types import StopEvent


class TargetState:
    """Thread-safe persistent target state.

    Updated by execution tools, queried by inspection/snapshot tools.
    The state machine is simple:
        unknown -> halted | running
        halted <-> running
    """

    def __init__(self) -> None:
        self._state: str = "unknown"
        self._last_stop: StopEvent | None = None
        self._lock = threading.Lock()

    def set_running(self) -> None:
        """Mark target as running (after continue or step-start)."""
        with self._lock:
            self._state = "running"
            self._last_stop = None

    def set_halted(self, stop: StopEvent | None = None) -> None:
        """Mark target as halted, optionally with the stop event details."""
        with self._lock:
            self._state = "halted"
            if stop is not None:
                self._last_stop = stop

    @property
    def state(self) -> str:
        """Current state: 'running', 'halted', or 'unknown'."""
        with self._lock:
            return self._state

    @property
    def is_halted(self) -> bool:
        with self._lock:
            return self._state == "halted"

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state == "running"

    @property
    def last_stop(self) -> StopEvent | None:
        """The most recent stop event, if target is halted."""
        with self._lock:
            return self._last_stop
