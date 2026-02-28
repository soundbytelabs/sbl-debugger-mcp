"""GDB/MI bridge — thread-safe wrapper around pygdbmi."""

from __future__ import annotations

import shutil
import threading
import time

from pygdbmi.gdbcontroller import GdbController

from sbl_debugger.bridge.types import MiResult, StopEvent

# Default GDB binary
_DEFAULT_GDB = "gdb-multiarch"


class MiBridge:
    """Thread-safe GDB/MI interface.

    Wraps pygdbmi's GdbController with a lock (one MI command at a time),
    typed result parsing, and helpers for common embedded debug operations.
    """

    def __init__(self, gdb_command: str | None = None) -> None:
        self._gdb: GdbController | None = None
        self._lock = threading.Lock()
        self._gdb_command = gdb_command or _DEFAULT_GDB
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        """Launch the GDB subprocess."""
        gdb_path = shutil.which(self._gdb_command)
        if gdb_path is None:
            raise RuntimeError(f"{self._gdb_command} not found on PATH")

        self._gdb = GdbController(
            command=[gdb_path, "--nx", "--quiet", "--interpreter=mi3"],
            time_to_check_for_additional_output_sec=0.02,
        )

    def stop(self) -> None:
        """Exit GDB."""
        if self._gdb is not None:
            try:
                self._gdb.exit()
            except Exception:
                pass
            self._gdb = None
        self._connected = False

    def connect(self, host: str = "localhost", port: int = 3333) -> MiResult:
        """Connect GDB to a remote target (OpenOCD GDB server)."""
        result = self.command(
            f"-target-select remote {host}:{port}",
            timeout=10.0,
        )
        if not result.is_error:
            self._connected = True
        return result

    def load_symbols(self, elf_path: str) -> MiResult:
        """Load ELF symbols into GDB."""
        return self.command(
            f"-file-exec-and-symbols {elf_path}",
            timeout=10.0,
        )

    def command(self, cmd: str, timeout: float = 5.0) -> MiResult:
        """Send an MI command and return the parsed result.

        Thread-safe — acquires the lock for the duration of the command.
        """
        if self._gdb is None:
            raise RuntimeError("GDB is not running")

        with self._lock:
            responses = self._gdb.write(cmd, timeout_sec=timeout)
            return MiResult.from_responses(responses)

    def monitor(self, cmd: str, timeout: float = 10.0) -> MiResult:
        """Send an OpenOCD monitor command via GDB."""
        # MI requires escaping the console command
        escaped = cmd.replace('"', '\\"')
        return self.command(
            f'-interpreter-exec console "monitor {escaped}"',
            timeout=timeout,
        )

    def drain_events(self) -> list[dict]:
        """Non-blocking read of pending async events from GDB.

        Returns raw pygdbmi response dicts with type=="notify".
        """
        if self._gdb is None:
            return []

        with self._lock:
            try:
                responses = self._gdb.get_gdb_response(
                    timeout_sec=0.05,
                    raise_error_on_timeout=False,
                )
            except Exception:
                return []
            return [r for r in responses if r.get("type") == "notify"]

    def wait_for_stop(self, timeout: float = 30.0) -> StopEvent | None:
        """Block until a *stopped event arrives from GDB.

        Returns the parsed StopEvent, or None if timeout expires.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = self.drain_events()
            for e in events:
                if e.get("message") == "stopped":
                    payload = e.get("payload", {})
                    if isinstance(payload, dict):
                        return StopEvent.from_mi(payload)
            time.sleep(0.05)
        return None
