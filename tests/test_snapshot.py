"""Tests for the debug_snapshot tool."""

from unittest.mock import patch

import pytest

from mcp.server.fastmcp import FastMCP

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import FrameInfo, MiResult, StopEvent
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.targets import get_profile
from sbl_debugger.tools import snapshot as snapshot_tools


def _setup_tools():
    """Create MCP server and manager with snapshot tools registered."""
    mcp = FastMCP("test")
    mgr = SessionManager()
    snapshot_tools.register_tools(mcp, mgr)
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


class TestSnapshotErrors:
    def test_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["debug_snapshot"].fn(name="nope")
        assert "error" in result


class TestSnapshotRunning:
    def test_returns_running_when_no_stop_event(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(MiBridge, "drain_events", return_value=[]):
            result = tools["debug_snapshot"].fn(name="daisy")

        # No stop events → assume running
        assert result["state"] == "running"

    def test_returns_running_after_running_event(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        events = [
            {"type": "notify", "message": "stopped", "payload": {
                "reason": "breakpoint-hit",
                "frame": {"func": "main", "line": "10"},
            }},
            {"type": "notify", "message": "running", "payload": {"thread-id": "1"}},
        ]
        with patch.object(MiBridge, "drain_events", return_value=events):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "running"


class TestSnapshotHalted:
    def _make_stop_events(self):
        return [{
            "type": "notify",
            "message": "stopped",
            "payload": {
                "reason": "breakpoint-hit",
                "frame": {
                    "func": "main",
                    "file": "main.cpp",
                    "fullname": "/src/main.cpp",
                    "line": "42",
                    "addr": "0x08000150",
                },
            },
        }]

    def _mock_register_commands(self):
        """Side effect for bridge.command that handles register + bt + locals queries."""
        names = {"register-names": ["r0", "r1", "r2", "r3", "", "r12", "sp", "lr", "pc", "xpsr"]}
        values = {"register-values": [
            {"number": "0", "value": "0x00000000"},
            {"number": "1", "value": "0x20000100"},
            {"number": "2", "value": "0x00000042"},
            {"number": "3", "value": "0x00000000"},
            {"number": "5", "value": "0xDEADBEEF"},
            {"number": "6", "value": "0x20020000"},
            {"number": "7", "value": "0x08000151"},
            {"number": "8", "value": "0x08000150"},
            {"number": "9", "value": "0x61000000"},
        ]}
        bt = {"stack": [
            {"frame": {"level": "0", "addr": "0x08000150", "func": "main",
                       "file": "main.cpp", "fullname": "/src/main.cpp", "line": "42"}},
            {"frame": {"level": "1", "addr": "0x08000008", "func": "Reset_Handler"}},
        ]}
        locals_payload = {"variables": [
            {"name": "x", "value": "42"},
            {"name": "flag", "value": "true"},
        ]}

        def mock_cmd(cmd, timeout=5.0):
            if "register-names" in cmd:
                return MiResult(message="done", payload=names)
            elif "register-values" in cmd:
                return MiResult(message="done", payload=values)
            elif "-stack-list-frames" in cmd:
                return MiResult(message="done", payload=bt)
            elif "-stack-list-variables" in cmd:
                return MiResult(message="done", payload=locals_payload)
            return MiResult(message="done")
        return mock_cmd

    def test_snapshot_includes_all_sections(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(MiBridge, "drain_events", return_value=self._make_stop_events()), \
             patch.object(MiBridge, "command", side_effect=self._mock_register_commands()):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "halted"
        assert result["reason"] == "breakpoint-hit"
        assert result["frame"]["func"] == "main"
        assert "registers" in result
        assert result["registers"]["r0"] == "0x00000000"
        assert result["registers"]["pc"] == "0x08000150"
        assert "backtrace" in result
        assert len(result["backtrace"]) == 2
        assert result["backtrace"][0]["func"] == "main"
        assert "locals" in result
        assert result["locals"][0] == {"name": "x", "value": "42"}

    def test_snapshot_without_frame_still_returns_registers(self):
        """Stop event without frame info should still attempt registers/bt/locals."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        events = [{
            "type": "notify",
            "message": "stopped",
            "payload": {"reason": "signal-received"},
        }]

        with patch.object(MiBridge, "drain_events", return_value=events), \
             patch.object(MiBridge, "command", side_effect=self._mock_register_commands()):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "halted"
        assert "registers" in result
        assert "backtrace" in result

    def test_snapshot_graceful_on_register_error(self):
        """If register read fails, snapshot still returns other data."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        bt = {"stack": [{"frame": {"level": "0", "func": "main"}}]}
        locals_payload = {"variables": [{"name": "x", "value": "1"}]}

        def mock_cmd(cmd, timeout=5.0):
            if "register" in cmd:
                return MiResult(message="error", payload={"msg": "No registers"})
            elif "-stack-list-frames" in cmd:
                return MiResult(message="done", payload=bt)
            elif "-stack-list-variables" in cmd:
                return MiResult(message="done", payload=locals_payload)
            return MiResult(message="done")

        with patch.object(MiBridge, "drain_events", return_value=self._make_stop_events()), \
             patch.object(MiBridge, "command", side_effect=mock_cmd):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "halted"
        assert "registers" not in result  # Failed gracefully
        assert "backtrace" in result
        assert "locals" in result

    def test_snapshot_graceful_on_backtrace_error(self):
        """If backtrace fails, snapshot still returns other data."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        names = {"register-names": ["r0", "pc"]}
        values = {"register-values": [
            {"number": "0", "value": "0x0"},
            {"number": "1", "value": "0x08000150"},
        ]}

        def mock_cmd(cmd, timeout=5.0):
            if "register-names" in cmd:
                return MiResult(message="done", payload=names)
            elif "register-values" in cmd:
                return MiResult(message="done", payload=values)
            elif "-stack-list-frames" in cmd:
                return MiResult(message="error", payload={"msg": "No stack"})
            elif "-stack-list-variables" in cmd:
                return MiResult(message="error", payload={"msg": "No frame"})
            return MiResult(message="done")

        with patch.object(MiBridge, "drain_events", return_value=self._make_stop_events()), \
             patch.object(MiBridge, "command", side_effect=mock_cmd):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "halted"
        assert "registers" in result
        assert "backtrace" not in result  # Failed gracefully
        assert "locals" not in result


class TestSnapshotToolRegistration:
    def test_tool_registered(self):
        _, _, tools = _setup_tools()
        assert "debug_snapshot" in tools
