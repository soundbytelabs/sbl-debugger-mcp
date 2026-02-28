"""Session tools: debug_attach, debug_detach, debug_sessions, debug_status."""

from __future__ import annotations

from sbl_debugger.bridge.types import StopEvent
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.targets import TargetProfile, get_profile, list_profiles
from sbl_debugger.tools.inspection import read_source_context


def register_tools(mcp, manager: SessionManager) -> None:
    """Register session management tools with the MCP server."""

    @mcp.tool()
    def debug_attach(
        target: str,
        elf: str | None = None,
        name: str | None = None,
        interface: str | None = None,
        target_cfg: str | None = None,
    ) -> dict:
        """Attach to a debug target. Launches OpenOCD and GDB, connects via SWD.

        Use a predefined target profile (daisy, pico, pico2) or provide
        custom OpenOCD interface and target configs.

        The target is halted after attach.

        Args:
            target: Target profile name (daisy, pico, pico2) or "custom".
            elf: Optional path to ELF file for symbol loading.
            name: Optional session name. Defaults to target name.
            interface: OpenOCD interface config (required if target="custom").
            target_cfg: OpenOCD target config (required if target="custom").
        """
        try:
            if target == "custom":
                if not interface or not target_cfg:
                    return {
                        "error": "Custom target requires 'interface' and 'target_cfg' parameters"
                    }
                profile = TargetProfile(
                    description="Custom target",
                    openocd_interface=interface,
                    openocd_target=target_cfg,
                )
            else:
                profile = get_profile(target)

            session = manager.attach(
                target_profile=profile,
                target_name=target,
                name=name,
                elf_path=elf,
            )

            # Halt the target after attach
            halt_result = session.bridge.monitor("reset halt")

            result = {
                "status": "attached",
                "state": "halted",
                **session.to_dict(),
            }

            # Include frame info if we got a stop event
            events = session.bridge.drain_events()
            for e in events:
                if e.get("message") == "stopped":
                    payload = e.get("payload", {})
                    if isinstance(payload, dict):
                        stop = StopEvent.from_mi(payload)
                        if stop.frame:
                            result["frame"] = stop.frame.to_dict()

            return result
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def debug_detach(name: str) -> dict:
        """Detach from a debug target. Shuts down GDB and OpenOCD.

        Args:
            name: Session name.
        """
        try:
            manager.detach(name)
            return {"status": "detached", "name": name}
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def debug_sessions() -> dict:
        """List all active debug sessions."""
        sessions = manager.list()
        return {
            "sessions": [s.to_dict() for s in sessions],
            "count": len(sessions),
        }

    @mcp.tool()
    def debug_status(name: str) -> dict:
        """Get the current state of a debug target.

        Checks for pending GDB events (e.g., target stopped at breakpoint)
        and returns the latest known state.

        Args:
            name: Session name.
        """
        try:
            session = manager.get(name)

            if not session.is_alive:
                return {
                    "name": name,
                    "state": "disconnected",
                    "error": "Session is no longer alive (OpenOCD or GDB died)",
                }

            # Drain any pending events
            events = session.bridge.drain_events()

            # Look for the most recent stop event
            last_stop = None
            is_running = True
            for e in events:
                msg = e.get("message")
                if msg == "stopped":
                    payload = e.get("payload", {})
                    if isinstance(payload, dict):
                        last_stop = StopEvent.from_mi(payload)
                    is_running = False
                elif msg == "running":
                    is_running = True
                    last_stop = None

            result: dict = {
                "name": name,
                "state": "running" if is_running else "halted",
            }

            if last_stop:
                result["reason"] = last_stop.reason
                if last_stop.frame:
                    result["frame"] = last_stop.frame.to_dict()
                    source = read_source_context(
                        last_stop.frame.file, last_stop.frame.line
                    )
                    if source:
                        result["source"] = source

            return result
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def debug_targets() -> dict:
        """List available predefined target profiles."""
        return {"targets": list_profiles()}
