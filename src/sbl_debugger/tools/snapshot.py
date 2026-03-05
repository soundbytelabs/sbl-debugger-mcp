"""Snapshot tool: combined state in a single call."""

from __future__ import annotations

from sbl_debugger.bridge.types import FrameInfo, StopEvent
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.tools.inspection import CORE_REGISTERS, read_source_context


def _update_state_from_events(session, events: list[dict]) -> None:
    """Process GDB async events and update target state."""
    for e in events:
        msg = e.get("message")
        if msg == "stopped":
            payload = e.get("payload", {})
            if isinstance(payload, dict):
                stop = StopEvent.from_mi(payload)
                session.target_state.set_halted(stop)
        elif msg == "running":
            session.target_state.set_running()


def _query_thread_state(session) -> None:
    """Actively query GDB for target state via -thread-info.

    Updates session.target_state based on thread states.
    """
    try:
        result = session.bridge.command("-thread-info", timeout=2.0)
        if result.is_error:
            return
        payload = result.payload
        if not isinstance(payload, dict):
            return
        threads = payload.get("threads", [])
        for t in threads:
            if t.get("state") == "stopped":
                session.target_state.set_halted()
                return
        # If we got threads but none are stopped, target is running
        if threads:
            session.target_state.set_running()
    except Exception:
        pass


def register_tools(mcp, manager: SessionManager) -> None:
    """Register snapshot tools with the MCP server."""

    @mcp.tool()
    def debug_snapshot(name: str) -> dict:
        """Get a complete snapshot of the target state.

        Returns current frame, core registers, backtrace, local variables,
        and source context in a single call. Use this after halting, stepping,
        or hitting a breakpoint.

        Args:
            name: Session name.
        """
        try:
            session = manager.get(name)

            # 1. Check persistent state first
            state = session.target_state.state

            # 2. Drain any pending events (may update state)
            events = session.bridge.drain_events()
            _update_state_from_events(session, events)

            # 3. If state is still unknown or running, actively query GDB
            if session.target_state.state in ("unknown", "running"):
                _query_thread_state(session)

            # 4. Act on confirmed state
            current_state = session.target_state.state
            if current_state == "running":
                return {"name": name, "state": "running"}
            if current_state == "unknown":
                return {"name": name, "state": "unknown"}

            result: dict = {"name": name, "state": "halted"}

            # Use last_stop from target_state (persists across tool calls)
            last_stop = session.target_state.last_stop
            if last_stop:
                result["reason"] = last_stop.reason
                if last_stop.frame:
                    result["frame"] = last_stop.frame.to_dict()
                    source = read_source_context(
                        last_stop.frame.file, last_stop.frame.line
                    )
                    if source:
                        result["source"] = source

            # 2. Core registers
            _read_core_registers(session, result)

            # 3. Backtrace
            _read_backtrace(session, result)

            # 4. Local variables
            _read_locals(session, result)

            return result
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}


def _read_core_registers_gdb(session) -> dict[str, str] | None:
    """Read core registers via GDB. Returns dict or None on failure."""
    try:
        names_result = session.bridge.command("-data-list-register-names")
        if names_result.is_error:
            return None

        payload = names_result.payload
        if not isinstance(payload, dict):
            return None
        all_names = payload.get("register-names", [])
        named = {i: n for i, n in enumerate(all_names) if n}

        core_set = set(CORE_REGISTERS)
        indices = [i for i, n in named.items() if n in core_set]
        if not indices:
            return None

        idx_str = " ".join(str(i) for i in indices)
        values_result = session.bridge.command(
            f"-data-list-register-values x {idx_str}"
        )
        if values_result.is_error:
            return None

        vpayload = values_result.payload
        if not isinstance(vpayload, dict):
            return None

        reg_values = {}
        for entry in vpayload.get("register-values", []):
            num = int(entry["number"])
            reg_name = named.get(num, f"reg{num}")
            reg_values[reg_name] = entry["value"]

        return reg_values if reg_values else None
    except Exception:
        return None


def _all_registers_zero(regs: dict[str, str]) -> bool:
    """Detect desynced GDB: all core registers read as 0x0."""
    zero_values = {"0x0", "0x00000000", "0x0000000000000000"}
    return all(v in zero_values for v in regs.values())


def _read_core_registers(session, result: dict) -> None:
    """Read core registers, falling back to OpenOCD TCL if GDB is desynced."""
    regs = _read_core_registers_gdb(session)

    # Detect desynced GDB: all core registers are 0x0
    if regs and _all_registers_zero(regs):
        tcl_regs = session.openocd.read_registers_tcl()
        if tcl_regs:
            # Filter to core registers only
            core_set = set(CORE_REGISTERS)
            filtered = {k: v for k, v in tcl_regs.items() if k in core_set}
            if filtered:
                regs = filtered

    if regs:
        result["registers"] = regs


def _read_backtrace(session, result: dict) -> None:
    """Read backtrace and add to result dict."""
    try:
        bt_result = session.bridge.command("-stack-list-frames 0 19")
        if bt_result.is_error:
            return

        payload = bt_result.payload
        if not isinstance(payload, dict):
            return

        stack = payload.get("stack", [])
        frames = []
        for entry in stack:
            frame_data = (
                entry.get("frame", entry) if isinstance(entry, dict) else entry
            )
            frame = FrameInfo.from_mi(frame_data)
            level = frame_data.get("level", str(len(frames)))
            frames.append({"level": int(level), **frame.to_dict()})

        result["backtrace"] = frames
    except Exception:
        pass  # Non-fatal


def _read_locals(session, result: dict) -> None:
    """Read local variables and add to result dict."""
    try:
        locals_result = session.bridge.command(
            "-stack-list-variables --all-values"
        )
        if locals_result.is_error:
            return

        payload = locals_result.payload
        if not isinstance(payload, dict):
            return

        variables = []
        for var in payload.get("variables", []):
            variables.append({
                "name": var.get("name", "?"),
                "value": var.get("value", "?"),
            })

        result["locals"] = variables
    except Exception:
        pass  # Non-fatal
