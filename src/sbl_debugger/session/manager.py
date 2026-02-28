"""Session manager — thread-safe registry of named debug sessions."""

from __future__ import annotations

import threading

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.process.ports import find_available_port
from sbl_debugger.session.session import DebugSession
from sbl_debugger.targets import TargetProfile


class SessionManager:
    """Thread-safe registry of named debug sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, DebugSession] = {}
        self._lock = threading.Lock()

    def attach(
        self,
        target_profile: TargetProfile,
        target_name: str,
        name: str | None = None,
        elf_path: str | None = None,
    ) -> DebugSession:
        """Create a new debug session.

        Launches OpenOCD, starts GDB, connects, and optionally loads ELF symbols.

        Args:
            target_profile: Resolved target profile with OpenOCD configs.
            target_name: Profile name (e.g., "daisy") or "custom".
            name: Optional session name. Defaults to target_name.
            elf_path: Optional ELF file path for symbol loading.

        Returns:
            The new DebugSession.

        Raises:
            ValueError: If session name already exists.
            RuntimeError: If OpenOCD or GDB fails to start/connect.
        """
        if name is None:
            name = target_name

        with self._lock:
            if name in self._sessions:
                raise ValueError(f"Session '{name}' already exists")

        # Find an available port (outside lock — no I/O under lock)
        gdb_port = find_available_port()

        # Launch OpenOCD
        openocd = OpenOcdProcess(
            interface=target_profile.openocd_interface,
            target_cfg=target_profile.openocd_target,
            gdb_port=gdb_port,
        )
        try:
            openocd.start()
        except RuntimeError:
            openocd.stop()
            raise

        # Launch GDB and connect
        bridge = MiBridge()
        try:
            bridge.start()

            # Load symbols first (if provided) so GDB has context when connecting
            if elf_path:
                result = bridge.load_symbols(elf_path)
                if result.is_error:
                    raise RuntimeError(
                        f"Failed to load ELF: {result.error_msg}"
                    )

            result = bridge.connect(port=gdb_port)
            if result.is_error:
                raise RuntimeError(
                    f"GDB failed to connect to OpenOCD: {result.error_msg}"
                )
        except Exception:
            bridge.stop()
            openocd.stop()
            raise

        session = DebugSession(
            name=name,
            target=target_name,
            openocd=openocd,
            bridge=bridge,
            elf_path=elf_path,
        )

        with self._lock:
            # Double-check name wasn't taken while we were starting processes
            if name in self._sessions:
                session.shutdown()
                raise ValueError(f"Session '{name}' already exists")
            self._sessions[name] = session

        return session

    def detach(self, name: str) -> None:
        """Close and unregister a debug session."""
        with self._lock:
            session = self._sessions.pop(name, None)
            if session is None:
                raise ValueError(f"No session named '{name}'")

        session.shutdown()

    def get(self, name: str) -> DebugSession:
        """Get a session by name."""
        with self._lock:
            session = self._sessions.get(name)
            if session is None:
                raise ValueError(f"No session named '{name}'")
            return session

    def list(self) -> list[DebugSession]:
        """List all active sessions."""
        with self._lock:
            return list(self._sessions.values())

    def detach_all(self) -> None:
        """Close all sessions. Used during server shutdown."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            try:
                session.shutdown()
            except Exception:
                pass
