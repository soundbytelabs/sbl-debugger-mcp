"""Debug session — owns an OpenOCD process and GDB/MI bridge."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.process.openocd import OpenOcdProcess


@dataclass
class DebugSession:
    """A named debug session with its OpenOCD and GDB processes."""

    name: str
    target: str  # profile name or "custom"
    openocd: OpenOcdProcess
    bridge: MiBridge
    elf_path: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    @property
    def is_alive(self) -> bool:
        return self.openocd.is_alive and self.bridge.is_connected

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.created_at

    def to_dict(self) -> dict:
        result: dict = {
            "name": self.name,
            "target": self.target,
            "alive": self.is_alive,
            "gdb_port": self.openocd.gdb_port,
            "uptime_seconds": round(self.uptime, 1),
        }
        if self.elf_path:
            result["elf"] = self.elf_path
        return result

    def shutdown(self) -> None:
        """Clean up both GDB and OpenOCD."""
        self.bridge.stop()
        self.openocd.stop()
