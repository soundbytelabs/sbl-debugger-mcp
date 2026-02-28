"""Breakpoint tools: set, delete, list breakpoints and watchpoints."""

from __future__ import annotations

from sbl_debugger.session.manager import SessionManager


def _parse_breakpoint(bkpt: dict) -> dict:
    """Parse a GDB/MI breakpoint dict into a clean response."""
    result: dict = {
        "number": int(bkpt.get("number", 0)),
        "type": bkpt.get("type", "breakpoint"),
        "enabled": bkpt.get("enabled", "y") == "y",
    }
    if "addr" in bkpt:
        result["address"] = bkpt["addr"]
    if "func" in bkpt:
        result["func"] = bkpt["func"]
    if "fullname" in bkpt or "file" in bkpt:
        result["file"] = bkpt.get("fullname") or bkpt.get("file")
    if "line" in bkpt:
        result["line"] = int(bkpt["line"])
    if "times" in bkpt:
        result["hit_count"] = int(bkpt["times"])
    if "what" in bkpt:
        result["what"] = bkpt["what"]
    return result


def register_tools(mcp, manager: SessionManager) -> None:
    """Register breakpoint tools with the MCP server."""

    @mcp.tool()
    def breakpoint_set(name: str, location: str) -> dict:
        """Set a breakpoint at a location.

        Args:
            name: Session name.
            location: Where to break — function name (e.g., "main"),
                      file:line (e.g., "main.cpp:42"), or *address
                      (e.g., "*0x08000150").
        """
        try:
            session = manager.get(name)
            result = session.bridge.command(f"-break-insert {location}")
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from breakpoint insert"}

            bkpt = payload.get("bkpt", {})
            return {"name": name, **_parse_breakpoint(bkpt)}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def breakpoint_delete(name: str, number: int) -> dict:
        """Delete a breakpoint by its number.

        Args:
            name: Session name.
            number: Breakpoint number (from breakpoint_set or breakpoint_list).
        """
        try:
            session = manager.get(name)
            result = session.bridge.command(f"-break-delete {number}")
            if result.is_error:
                return {"error": result.error_msg}
            return {"name": name, "deleted": number}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def breakpoint_list(name: str) -> dict:
        """List all breakpoints and watchpoints.

        Args:
            name: Session name.
        """
        try:
            session = manager.get(name)
            result = session.bridge.command("-break-list")
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from breakpoint list"}

            table = payload.get("BreakpointTable", {})
            body = table.get("body", [])

            breakpoints = []
            for entry in body:
                # MI wraps each: {"bkpt": {...}}
                bkpt = entry.get("bkpt", entry) if isinstance(entry, dict) else entry
                breakpoints.append(_parse_breakpoint(bkpt))

            return {"name": name, "breakpoints": breakpoints, "count": len(breakpoints)}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def watchpoint_set(
        name: str,
        expression: str,
        type: str = "write",
    ) -> dict:
        """Set a hardware watchpoint on a memory location or variable.

        Triggers when the watched location is accessed. Hardware watchpoints
        are limited (typically 4 on Cortex-M).

        Args:
            name: Session name.
            expression: What to watch — variable name or "*address"
                        (e.g., "my_var", "*(int*)0x20000000").
            type: Access type — "write" (default), "read", or "access" (read or write).
        """
        try:
            session = manager.get(name)

            if type == "read":
                cmd = f"-break-watch -r {expression}"
            elif type == "access":
                cmd = f"-break-watch -a {expression}"
            else:
                cmd = f"-break-watch {expression}"

            result = session.bridge.command(cmd)
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from watchpoint set"}

            # GDB returns different keys depending on watch type
            wp = (
                payload.get("wpt")
                or payload.get("hw-rwpt")
                or payload.get("hw-awpt")
                or {}
            )
            response: dict = {
                "name": name,
                "number": int(wp.get("number", 0)),
                "type": type,
                "expression": wp.get("exp", expression),
            }
            return response
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
