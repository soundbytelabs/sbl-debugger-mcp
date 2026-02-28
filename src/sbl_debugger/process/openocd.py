"""OpenOCD subprocess lifecycle management."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time


class OpenOcdProcess:
    """Manages an OpenOCD subprocess.

    Launches OpenOCD with the given interface and target configs,
    waits for it to be ready (listening on the GDB port), and
    provides graceful shutdown.
    """

    def __init__(
        self,
        interface: str,
        target_cfg: str,
        gdb_port: int = 3333,
    ) -> None:
        self._interface = interface
        self._target_cfg = target_cfg
        self._gdb_port = gdb_port
        self._proc: subprocess.Popen | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._ready_event = threading.Event()

    @property
    def gdb_port(self) -> int:
        return self._gdb_port

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def stderr_output(self) -> list[str]:
        """Captured stderr lines from OpenOCD."""
        return list(self._stderr_lines)

    def start(self, timeout: float = 10.0) -> None:
        """Launch OpenOCD and wait for it to be ready.

        Raises RuntimeError if OpenOCD fails to start or doesn't become
        ready within the timeout.
        """
        if self.is_alive:
            raise RuntimeError("OpenOCD is already running")

        openocd_path = shutil.which("openocd")
        if openocd_path is None:
            raise RuntimeError("openocd not found on PATH")

        cmd = [
            openocd_path,
            "-f", f"interface/{self._interface}",
            "-f", f"target/{self._target_cfg}",
            "-c", f"gdb_port {self._gdb_port}",
            # Disable telnet and TCL ports to avoid contention
            "-c", "telnet_port disabled",
            "-c", "tcl_port disabled",
        ]

        self._ready_event.clear()
        self._stderr_lines.clear()

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Background thread reads stderr for readiness detection
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            daemon=True,
            name="openocd-stderr",
        )
        self._stderr_thread.start()

        # Wait for OpenOCD to report it's listening
        if not self._ready_event.wait(timeout=timeout):
            # Check if process died
            if self._proc.poll() is not None:
                stderr = "\n".join(self._stderr_lines)
                raise RuntimeError(
                    f"OpenOCD exited with code {self._proc.returncode}:\n{stderr}"
                )
            self.stop()
            raise RuntimeError(
                f"OpenOCD did not become ready within {timeout}s"
            )

    def stop(self, timeout: float = 5.0) -> None:
        """Graceful shutdown: SIGTERM -> wait -> SIGKILL."""
        if self._proc is None:
            return

        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)

        self._proc = None

    def _read_stderr(self) -> None:
        """Read OpenOCD stderr, detect readiness."""
        assert self._proc is not None
        assert self._proc.stderr is not None

        for raw_line in self._proc.stderr:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            self._stderr_lines.append(line)

            # OpenOCD prints this when the GDB server is ready
            if "Listening on port" in line and str(self._gdb_port) in line:
                self._ready_event.set()
