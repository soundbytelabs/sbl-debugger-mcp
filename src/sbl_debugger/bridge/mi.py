"""GDB/MI bridge — thread-safe wrapper around pygdbmi."""

from __future__ import annotations

import io
import shutil
import threading
import time

from pygdbmi.gdbcontroller import GdbController

from sbl_debugger.bridge.types import ConnectionLostError, MiResult, StopEvent

# Default GDB binary
_DEFAULT_GDB = "gdb-multiarch"

# Markers that indicate OpenOCD dropped the SWD connection
_CONNECTION_LOST_MARKERS = ("Remote connection closed", "Remote communication error")


def _responses_indicate_connection_lost(responses: list[dict]) -> bool:
    """Check if GDB responses contain signs of a lost OpenOCD connection."""
    for r in responses:
        # GDB emits log-type messages for connection drops
        if r.get("type") == "log":
            payload = r.get("payload", "")
            if isinstance(payload, str):
                for marker in _CONNECTION_LOST_MARKERS:
                    if marker in payload:
                        return True
        # Also check notify events — thread-group-exited after connection loss
        if r.get("type") == "notify" and r.get("message") == "thread-group-exited":
            # Only a signal if we didn't request a disconnect
            pass
    return False


class MiLogger:
    """Raw MI traffic logger — writes timestamped TX/RX lines to a file.

    Thread-safe: each write is a single file write of a pre-formatted string.
    """

    def __init__(self, path: str) -> None:
        self._file: io.TextIOWrapper = open(path, "w")
        self._start = time.monotonic()
        self._file.write(f"# MI traffic log — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._file.flush()

    def tx(self, cmd: str) -> None:
        """Log a command sent to GDB."""
        elapsed = time.monotonic() - self._start
        self._file.write(f"[{elapsed:10.3f}] TX {cmd}\n")
        self._file.flush()

    def rx(self, responses: list[dict]) -> None:
        """Log raw pygdbmi response dicts received from GDB."""
        elapsed = time.monotonic() - self._start
        for r in responses:
            rtype = r.get("type", "?")
            message = r.get("message", "")
            payload = r.get("payload", "")
            # Compact single-line format: type|message|payload
            self._file.write(f"[{elapsed:10.3f}] RX {rtype}|{message}|{payload}\n")
        self._file.flush()

    def close(self) -> None:
        """Close the log file."""
        if self._file and not self._file.closed:
            self._file.close()


class MiBridge:
    """Thread-safe GDB/MI interface.

    Wraps pygdbmi's GdbController with a lock (one MI command at a time),
    typed result parsing, and helpers for common embedded debug operations.
    """

    def __init__(
        self,
        gdb_command: str | None = None,
        mi_log: bool = False,
        session_name: str = "default",
    ) -> None:
        self._gdb: GdbController | None = None
        self._lock = threading.Lock()
        self._gdb_command = gdb_command or _DEFAULT_GDB
        self._connected = False
        self._logger: MiLogger | None = None
        import os
        if mi_log or os.environ.get("SBL_MI_LOG"):
            self._logger = MiLogger(f"/tmp/sbl-debugger-mi-{session_name}.log")

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
        if self._logger is not None:
            self._logger.close()
            self._logger = None

    def connect(self, host: str = "localhost", port: int = 3333) -> MiResult:
        """Connect GDB to a remote target (OpenOCD GDB server)."""
        result = self.command(
            f"-target-select remote {host}:{port}",
            timeout=10.0,
        )
        if not result.is_error:
            self._connected = True
        return result

    def disconnect(self) -> MiResult:
        """Disconnect GDB from the remote target.

        After disconnect, GDB forgets its target state. A subsequent
        connect() forces GDB to re-read PC, registers, and memory
        from scratch — effectively a state machine reset.
        """
        result = self.command("-target-disconnect", timeout=5.0)
        self._connected = False
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
        Raises ConnectionLostError if GDB reports the remote connection dropped.
        """
        if self._gdb is None:
            raise RuntimeError("GDB is not running")

        with self._lock:
            if self._logger:
                self._logger.tx(cmd)
            responses = self._gdb.write(cmd, timeout_sec=timeout)
            if self._logger:
                self._logger.rx(responses)
            # Detect OpenOCD SWD connection loss — GDB emits a log message
            # with "Remote connection closed" when OpenOCD drops the link.
            if _responses_indicate_connection_lost(responses):
                self._connected = False
                raise ConnectionLostError(
                    "GDB lost connection to OpenOCD (Remote connection closed)"
                )
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
        Raises ConnectionLostError if the connection dropped.
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
            if self._logger and responses:
                self._logger.rx(responses)
            if _responses_indicate_connection_lost(responses):
                self._connected = False
                raise ConnectionLostError(
                    "GDB lost connection to OpenOCD (Remote connection closed)"
                )
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
