"""sbl-debugger MCP server — embedded debug via GDB/MI + OpenOCD."""

from __future__ import annotations

import atexit

from mcp.server.fastmcp import FastMCP

from sbl_debugger.session.manager import SessionManager
from sbl_debugger.tools import advanced as advanced_tools
from sbl_debugger.tools import breakpoints as breakpoint_tools
from sbl_debugger.tools import execution as execution_tools
from sbl_debugger.tools import inspection as inspection_tools
from sbl_debugger.tools import session as session_tools
from sbl_debugger.tools import snapshot as snapshot_tools

mcp = FastMCP("sbl-debugger")

# Shared session manager — module-level singleton
_manager = SessionManager()
atexit.register(_manager.detach_all)

# Register tool modules
session_tools.register_tools(mcp, _manager)
execution_tools.register_tools(mcp, _manager)
inspection_tools.register_tools(mcp, _manager)
breakpoint_tools.register_tools(mcp, _manager)
snapshot_tools.register_tools(mcp, _manager)
advanced_tools.register_tools(mcp, _manager)
