"""GDB server port allocation."""

from __future__ import annotations

import socket

# Default OpenOCD GDB server port
DEFAULT_GDB_PORT = 3333

# Range to scan for available ports
_PORT_RANGE_START = 3333
_PORT_RANGE_END = 3343  # 10 slots


def find_available_port(start: int = _PORT_RANGE_START) -> int:
    """Find an available TCP port for the GDB server.

    Scans from start through _PORT_RANGE_END.
    Raises RuntimeError if no port is available.
    """
    for port in range(start, _PORT_RANGE_END + 1):
        if _is_port_available(port):
            return port
    raise RuntimeError(
        f"No available GDB server port in range {start}-{_PORT_RANGE_END}"
    )


def _is_port_available(port: int) -> bool:
    """Check if a TCP port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False
