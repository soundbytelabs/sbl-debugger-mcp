"""Advanced tools: load (flash) and monitor (raw OpenOCD commands)."""

from __future__ import annotations

from sbl_debugger.session.manager import SessionManager


def register_tools(mcp, manager: SessionManager) -> None:
    """Register advanced tools with the MCP server."""

    @mcp.tool()
    def load(name: str, elf: str | None = None) -> dict:
        """Flash firmware to the target.

        Uses the session's ELF (from attach) or a new path. Loads symbols and
        downloads to flash. Resets and halts after flashing.

        Args:
            name: Session name.
            elf: Optional path to ELF file. Uses session ELF if not provided.
        """
        try:
            session = manager.get(name)
            elf_path = elf or session.elf_path
            if not elf_path:
                return {
                    "error": "No ELF path provided and no ELF loaded in session"
                }

            # Load symbols
            sym_result = session.bridge.load_symbols(elf_path)
            if sym_result.is_error:
                return {"error": f"Symbol load failed: {sym_result.error_msg}"}

            # Download to flash
            dl_result = session.bridge.command(
                "-target-download", timeout=60.0
            )
            if dl_result.is_error:
                return {"error": f"Download failed: {dl_result.error_msg}"}

            # Parse download stats from payload
            stats: dict = {"name": name, "elf": elf_path, "status": "flashed"}
            payload = dl_result.payload
            if isinstance(payload, dict):
                if "total-size" in payload:
                    stats["total_bytes"] = int(payload["total-size"])
                if "transfer-rate" in payload:
                    stats["transfer_rate"] = payload["transfer-rate"]
                if "write-rate" in payload:
                    stats["write_rate"] = payload["write-rate"]

            # Update session's ELF path if a new one was provided
            if elf:
                session.elf_path = elf

            # Reset and halt after flash
            session.bridge.monitor("reset halt")
            session.bridge.drain_events()
            stats["state"] = "halted"

            return stats
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def monitor(name: str, command: str) -> dict:
        """Send a raw OpenOCD monitor command.

        For advanced/escape-hatch use. Examples: "flash banks",
        "reset init", "arm semihosting enable".

        Args:
            name: Session name.
            command: OpenOCD command to send.
        """
        try:
            session = manager.get(name)
            result = session.bridge.monitor(command)
            if result.is_error:
                return {"error": result.error_msg}
            return {
                "name": name,
                "command": command,
                "output": result.console_output,
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
