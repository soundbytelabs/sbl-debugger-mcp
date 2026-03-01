"""OpenOCD port allocation — GDB server and TCL control ports."""

from __future__ import annotations

import socket
from dataclasses import dataclass

# Default OpenOCD port ranges
_GDB_PORT_START = 3333
_GDB_PORT_END = 3343   # 10 GDB slots
_TCL_PORT_START = 6666
_TCL_PORT_END = 6676   # 10 TCL slots


@dataclass
class PortPair:
    """A GDB server port and its companion TCL control port."""
    gdb: int
    tcl: int


def find_available_port(start: int = _GDB_PORT_START) -> int:
    """Find an available TCP port for the GDB server.

    Scans from start through _GDB_PORT_END.
    Raises RuntimeError if no port is available.
    """
    for port in range(start, _GDB_PORT_END + 1):
        if _is_port_available(port):
            return port
    raise RuntimeError(
        f"No available GDB server port in range {start}-{_GDB_PORT_END}"
    )


def find_available_ports() -> PortPair:
    """Find an available GDB port and TCL port pair.

    Raises RuntimeError if either port range is exhausted.
    """
    gdb_port = find_available_port()
    for port in range(_TCL_PORT_START, _TCL_PORT_END + 1):
        if _is_port_available(port):
            return PortPair(gdb=gdb_port, tcl=port)
    raise RuntimeError(
        f"No available TCL port in range {_TCL_PORT_START}-{_TCL_PORT_END}"
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
