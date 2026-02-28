"""Snapshot tool: combined state in a single call."""

from __future__ import annotations

from sbl_debugger.bridge.types import FrameInfo, StopEvent
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.tools.inspection import CORE_REGISTERS, read_source_context


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

            # 1. Drain events to determine current state
            events = session.bridge.drain_events()
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

            if is_running:
                return {"name": name, "state": "running"}

            result: dict = {"name": name, "state": "halted"}

            if last_stop:
                result["reason"] = last_stop.reason
                if last_stop.frame:
                    result["frame"] = last_stop.frame.to_dict()
                    # Source context from frame
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


def _read_core_registers(session, result: dict) -> None:
    """Read core registers and add to result dict."""
    try:
        names_result = session.bridge.command("-data-list-register-names")
        if names_result.is_error:
            return

        payload = names_result.payload
        if not isinstance(payload, dict):
            return
        all_names = payload.get("register-names", [])
        named = {i: n for i, n in enumerate(all_names) if n}

        # Filter to core registers only
        core_set = set(CORE_REGISTERS)
        indices = [i for i, n in named.items() if n in core_set]
        if not indices:
            return

        idx_str = " ".join(str(i) for i in indices)
        values_result = session.bridge.command(
            f"-data-list-register-values x {idx_str}"
        )
        if values_result.is_error:
            return

        vpayload = values_result.payload
        if not isinstance(vpayload, dict):
            return

        reg_values = {}
        for entry in vpayload.get("register-values", []):
            num = int(entry["number"])
            reg_name = named.get(num, f"reg{num}")
            reg_values[reg_name] = entry["value"]

        result["registers"] = reg_values
    except Exception:
        pass  # Non-fatal — snapshot still useful without registers


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
