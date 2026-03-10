"""OpenOCD subprocess lifecycle management."""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time


class OpenOcdProcess:
    """Manages an OpenOCD subprocess.

    Launches OpenOCD with the given interface and target configs,
    waits for it to be ready (listening on the GDB port), and
    provides graceful shutdown.

    Exposes a TCL port for direct command access that bypasses GDB.
    This is critical for halting a target when GDB is unresponsive.
    """

    def __init__(
        self,
        interface: str,
        target_cfg: str,
        gdb_port: int = 3333,
        tcl_port: int = 6666,
    ) -> None:
        self._interface = interface
        self._target_cfg = target_cfg
        self._gdb_port = gdb_port
        self._tcl_port = tcl_port
        self._proc: subprocess.Popen | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._ready_event = threading.Event()

    @property
    def gdb_port(self) -> int:
        return self._gdb_port

    @property
    def tcl_port(self) -> int:
        return self._tcl_port

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
            "-c", "telnet_port disabled",
            # TCL port enabled for direct command access (halt fallback)
            "-c", f"tcl_port {self._tcl_port}",
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

    def tcl_command(self, cmd: str, timeout: float = 3.0) -> str:
        """Send a command directly to OpenOCD's TCL port.

        Bypasses GDB entirely — uses a raw TCP socket to OpenOCD's
        TCL server. Essential for halting a target when GDB is
        unresponsive after -exec-continue.

        OpenOCD TCL protocol: send command + \\x1a, receive response + \\x1a.

        Returns the response text, or raises RuntimeError on failure.
        """
        if not self.is_alive:
            raise RuntimeError("OpenOCD is not running")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(("127.0.0.1", self._tcl_port))
                # OpenOCD TCL protocol uses \x1a as command delimiter
                s.sendall(cmd.encode("utf-8") + b"\x1a")
                # Read response until \x1a delimiter
                data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\x1a" in data:
                        break
                return data.rstrip(b"\x1a").decode("utf-8", errors="replace").strip()
        except (OSError, socket.timeout) as e:
            raise RuntimeError(f"TCL command '{cmd}' failed: {e}")

    def read_memory_tcl(
        self, address: int, length: int, timeout: float = 3.0
    ) -> bytes | None:
        """Read target memory via OpenOCD TCL port.

        Uses 'read_memory' TCL command (available in OpenOCD 0.12+).
        Falls back to 'mdw' for word-aligned reads.

        Returns raw bytes or None on failure.
        """
        try:
            # Read as 32-bit words (most efficient)
            word_count = (length + 3) // 4
            output = self.tcl_command(
                f"read_memory 0x{address:08x} 32 {word_count}",
                timeout=timeout,
            )
            if not output:
                return None

            # Output is space-separated hex values
            raw = b""
            for word_str in output.split():
                word_str = word_str.strip()
                if word_str:
                    val = int(word_str, 0)
                    raw += val.to_bytes(4, byteorder="little")
            return raw[:length] if raw else None
        except (RuntimeError, ValueError):
            return None

    def write_memory_tcl(
        self, address: int, data: bytes, timeout: float = 3.0
    ) -> bool:
        """Write to target memory via OpenOCD TCL port.

        Works on a running target — no halt needed. Uses the Debug Access
        Port (DAP) which has direct bus access independent of the CPU core.

        Args:
            address: Target memory address.
            data: Raw bytes to write.
            timeout: TCL command timeout.

        Returns True on success, False on failure.
        """
        try:
            # Write byte-by-byte using mwb (memory write byte)
            for i, byte_val in enumerate(data):
                self.tcl_command(
                    f"mwb 0x{address + i:08x} 0x{byte_val:02x}",
                    timeout=timeout,
                )
            return True
        except RuntimeError:
            return False

    def read_registers_tcl(self, timeout: float = 3.0) -> dict[str, str] | None:
        """Read ARM core registers via OpenOCD TCL port.

        Parses OpenOCD 'reg' output format:
          (0) r0 (/32): 0x00000000
          (1) r1 (/32): 0x00000001

        Returns dict of {name: hex_value} or None on failure.
        """
        import re

        try:
            output = self.tcl_command("reg", timeout=timeout)
        except RuntimeError:
            return None

        if not output:
            return None

        regs: dict[str, str] = {}
        # Match lines like: (0) r0 (/32): 0x00000000
        pattern = re.compile(r"\(\d+\)\s+(\w+)\s+\(/\d+\):\s+(0x[0-9a-fA-F]+)")
        for line in output.splitlines():
            m = pattern.match(line.strip())
            if m:
                name, value = m.group(1), m.group(2)
                regs[name] = value

        return regs if regs else None

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
