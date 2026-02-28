"""Tests for MCP tools (mocked sessions)."""

from unittest.mock import MagicMock, patch

import pytest

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import MiResult
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.session.session import DebugSession
from sbl_debugger.targets import get_profile


def _make_manager_with_session(name="daisy"):
    """Create a SessionManager with one mocked session."""
    mgr = SessionManager()
    profile = get_profile(name)

    with patch.object(OpenOcdProcess, "start"), \
         patch.object(OpenOcdProcess, "is_alive", new_callable=lambda: property(lambda self: True)), \
         patch.object(MiBridge, "start"), \
         patch.object(MiBridge, "connect", return_value=MiResult(message="connected")), \
         patch.object(MiBridge, "is_connected", new_callable=lambda: property(lambda self: True)):
        mgr.attach(target_profile=profile, target_name=name)

    return mgr


class TestToolRegistration:
    """Verify tools register without errors."""

    def test_session_tools_register(self):
        from mcp.server.fastmcp import FastMCP
        from sbl_debugger.tools import session as session_tools

        mcp = FastMCP("test")
        mgr = SessionManager()
        session_tools.register_tools(mcp, mgr)
        # If we get here without error, registration worked


class TestDebugAttachTool:
    def test_attach_unknown_target(self):
        """Attaching to unknown target returns error."""
        from mcp.server.fastmcp import FastMCP
        from sbl_debugger.tools import session as session_tools

        mcp = FastMCP("test")
        mgr = SessionManager()
        session_tools.register_tools(mcp, mgr)

        # Call the registered tool function directly
        # Find it in the registered tools
        tools = {t.name: t for t in mcp._tool_manager._tools.values()}
        attach_fn = tools["debug_attach"].fn
        result = attach_fn(target="nonexistent")
        assert "error" in result

    def test_attach_custom_missing_params(self):
        from mcp.server.fastmcp import FastMCP
        from sbl_debugger.tools import session as session_tools

        mcp = FastMCP("test")
        mgr = SessionManager()
        session_tools.register_tools(mcp, mgr)

        tools = {t.name: t for t in mcp._tool_manager._tools.values()}
        attach_fn = tools["debug_attach"].fn
        result = attach_fn(target="custom")
        assert "error" in result
        assert "interface" in result["error"]


class TestDebugDetachTool:
    def test_detach_nonexistent(self):
        from mcp.server.fastmcp import FastMCP
        from sbl_debugger.tools import session as session_tools

        mcp = FastMCP("test")
        mgr = SessionManager()
        session_tools.register_tools(mcp, mgr)

        tools = {t.name: t for t in mcp._tool_manager._tools.values()}
        detach_fn = tools["debug_detach"].fn
        result = detach_fn(name="nope")
        assert "error" in result


class TestDebugSessionsTool:
    def test_empty_sessions(self):
        from mcp.server.fastmcp import FastMCP
        from sbl_debugger.tools import session as session_tools

        mcp = FastMCP("test")
        mgr = SessionManager()
        session_tools.register_tools(mcp, mgr)

        tools = {t.name: t for t in mcp._tool_manager._tools.values()}
        sessions_fn = tools["debug_sessions"].fn
        result = sessions_fn()
        assert result["count"] == 0
        assert result["sessions"] == []


class TestDebugStatusTool:
    def test_status_nonexistent(self):
        from mcp.server.fastmcp import FastMCP
        from sbl_debugger.tools import session as session_tools

        mcp = FastMCP("test")
        mgr = SessionManager()
        session_tools.register_tools(mcp, mgr)

        tools = {t.name: t for t in mcp._tool_manager._tools.values()}
        status_fn = tools["debug_status"].fn
        result = status_fn(name="nope")
        assert "error" in result


class TestDebugTargetsTool:
    def test_lists_targets(self):
        from mcp.server.fastmcp import FastMCP
        from sbl_debugger.tools import session as session_tools

        mcp = FastMCP("test")
        mgr = SessionManager()
        session_tools.register_tools(mcp, mgr)

        tools = {t.name: t for t in mcp._tool_manager._tools.values()}
        targets_fn = tools["debug_targets"].fn
        result = targets_fn()
        assert "daisy" in result["targets"]
        assert "pico" in result["targets"]
