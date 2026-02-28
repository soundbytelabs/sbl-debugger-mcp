"""Tests for breakpoint tools (mocked sessions)."""

from unittest.mock import MagicMock, patch

import pytest

from mcp.server.fastmcp import FastMCP

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import MiResult
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.tools import breakpoints as breakpoint_tools
from sbl_debugger.tools.breakpoints import _parse_breakpoint
from sbl_debugger.targets import get_profile


def _setup_tools():
    """Create MCP server and manager with breakpoint tools registered."""
    mcp = FastMCP("test")
    mgr = SessionManager()
    breakpoint_tools.register_tools(mcp, mgr)
    tools = {t.name: t for t in mcp._tool_manager._tools.values()}
    return mcp, mgr, tools


def _mock_attach(manager, name="daisy"):
    """Attach with fully mocked OpenOCD + GDB."""
    profile = get_profile(name)
    with patch.object(OpenOcdProcess, "start"), \
         patch.object(OpenOcdProcess, "is_alive", new_callable=lambda: property(lambda self: True)), \
         patch.object(MiBridge, "start"), \
         patch.object(MiBridge, "connect", return_value=MiResult(message="connected")), \
         patch.object(MiBridge, "is_connected", new_callable=lambda: property(lambda self: True)):
        return manager.attach(target_profile=profile, target_name=name)


# -- Helper tests --

class TestParseBreakpoint:
    def test_full_breakpoint(self):
        bkpt = {
            "number": "1",
            "type": "breakpoint",
            "enabled": "y",
            "addr": "0x08000150",
            "func": "main",
            "fullname": "/src/main.cpp",
            "line": "42",
            "times": "3",
        }
        result = _parse_breakpoint(bkpt)
        assert result["number"] == 1
        assert result["type"] == "breakpoint"
        assert result["enabled"] is True
        assert result["address"] == "0x08000150"
        assert result["func"] == "main"
        assert result["file"] == "/src/main.cpp"
        assert result["line"] == 42
        assert result["hit_count"] == 3

    def test_minimal_breakpoint(self):
        bkpt = {"number": "2", "type": "breakpoint"}
        result = _parse_breakpoint(bkpt)
        assert result["number"] == 2
        assert "address" not in result
        assert "func" not in result
        assert "file" not in result

    def test_disabled_breakpoint(self):
        bkpt = {"number": "1", "type": "breakpoint", "enabled": "n"}
        result = _parse_breakpoint(bkpt)
        assert result["enabled"] is False

    def test_watchpoint_with_what(self):
        bkpt = {
            "number": "3",
            "type": "hw watchpoint",
            "what": "*(int*)0x20000000",
        }
        result = _parse_breakpoint(bkpt)
        assert result["type"] == "hw watchpoint"
        assert result["what"] == "*(int*)0x20000000"

    def test_file_fallback(self):
        """Uses 'file' when 'fullname' is absent."""
        bkpt = {"number": "1", "type": "breakpoint", "file": "main.cpp"}
        result = _parse_breakpoint(bkpt)
        assert result["file"] == "main.cpp"


# -- Error handling --

class TestBreakpointToolErrors:
    def test_breakpoint_set_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["breakpoint_set"].fn(name="nope", location="main")
        assert "error" in result

    def test_breakpoint_delete_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["breakpoint_delete"].fn(name="nope", number=1)
        assert "error" in result

    def test_breakpoint_list_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["breakpoint_list"].fn(name="nope")
        assert "error" in result

    def test_watchpoint_set_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["watchpoint_set"].fn(name="nope", expression="x")
        assert "error" in result


# -- breakpoint_set --

class TestBreakpointSet:
    def test_set_by_function(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"bkpt": {
            "number": "1", "type": "breakpoint", "enabled": "y",
            "addr": "0x08000150", "func": "main",
            "fullname": "/src/main.cpp", "line": "42",
        }}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["breakpoint_set"].fn(name="daisy", location="main")

        mock_cmd.assert_called_once_with("-break-insert main")
        assert result["number"] == 1
        assert result["func"] == "main"
        assert result["line"] == 42

    def test_set_by_file_line(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"bkpt": {
            "number": "2", "type": "breakpoint",
            "addr": "0x08000200", "file": "init.cpp", "line": "10",
        }}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["breakpoint_set"].fn(name="daisy", location="init.cpp:10")

        mock_cmd.assert_called_once_with("-break-insert init.cpp:10")
        assert result["number"] == 2

    def test_set_by_address(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"bkpt": {
            "number": "3", "type": "breakpoint",
            "addr": "0x08000150",
        }}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["breakpoint_set"].fn(name="daisy", location="*0x08000150")

        mock_cmd.assert_called_once_with("-break-insert *0x08000150")
        assert result["address"] == "0x08000150"

    def test_set_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No symbol table"}),
        ):
            result = tools["breakpoint_set"].fn(name="daisy", location="nonexistent")

        assert "error" in result


# -- breakpoint_delete --

class TestBreakpointDelete:
    def test_delete_success(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done"),
        ) as mock_cmd:
            result = tools["breakpoint_delete"].fn(name="daisy", number=1)

        mock_cmd.assert_called_once_with("-break-delete 1")
        assert result["deleted"] == 1

    def test_delete_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No breakpoint number 99"}),
        ):
            result = tools["breakpoint_delete"].fn(name="daisy", number=99)

        assert "error" in result


# -- breakpoint_list --

class TestBreakpointList:
    def test_list_breakpoints(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"BreakpointTable": {"body": [
            {"bkpt": {
                "number": "1", "type": "breakpoint", "enabled": "y",
                "addr": "0x08000150", "func": "main", "line": "42", "times": "0",
            }},
            {"bkpt": {
                "number": "2", "type": "breakpoint", "enabled": "n",
                "addr": "0x08000200", "func": "init", "line": "10", "times": "5",
            }},
        ]}}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["breakpoint_list"].fn(name="daisy")

        assert result["count"] == 2
        bps = result["breakpoints"]
        assert bps[0]["number"] == 1
        assert bps[0]["enabled"] is True
        assert bps[0]["hit_count"] == 0
        assert bps[1]["number"] == 2
        assert bps[1]["enabled"] is False
        assert bps[1]["hit_count"] == 5

    def test_list_empty(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"BreakpointTable": {"body": []}}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["breakpoint_list"].fn(name="daisy")

        assert result["count"] == 0
        assert result["breakpoints"] == []

    def test_list_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Failed"}),
        ):
            result = tools["breakpoint_list"].fn(name="daisy")

        assert "error" in result


# -- watchpoint_set --

class TestWatchpointSet:
    def test_write_watchpoint(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"wpt": {"number": "4", "exp": "my_var"}}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["watchpoint_set"].fn(name="daisy", expression="my_var")

        mock_cmd.assert_called_once_with("-break-watch my_var")
        assert result["number"] == 4
        assert result["type"] == "write"
        assert result["expression"] == "my_var"

    def test_read_watchpoint(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"hw-rwpt": {"number": "5", "exp": "*(int*)0x20000000"}}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["watchpoint_set"].fn(
                name="daisy", expression="*(int*)0x20000000", type="read"
            )

        mock_cmd.assert_called_once_with("-break-watch -r *(int*)0x20000000")
        assert result["type"] == "read"

    def test_access_watchpoint(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"hw-awpt": {"number": "6", "exp": "counter"}}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["watchpoint_set"].fn(
                name="daisy", expression="counter", type="access"
            )

        mock_cmd.assert_called_once_with("-break-watch -a counter")
        assert result["type"] == "access"

    def test_watchpoint_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No symbol \"x\""}),
        ):
            result = tools["watchpoint_set"].fn(name="daisy", expression="x")

        assert "error" in result


# -- Tool registration --

class TestBreakpointToolRegistration:
    def test_all_tools_registered(self):
        mcp, _, tools = _setup_tools()
        expected = {
            "breakpoint_set", "breakpoint_delete",
            "breakpoint_list", "watchpoint_set",
        }
        assert expected.issubset(set(tools.keys()))
