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
    def test_returns_running_when_thread_info_says_running(self):
        """When no events and -thread-info shows running threads → running."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        thread_info = {"threads": [{"id": "1", "state": "running"}]}

        def mock_command(cmd, timeout=5.0):
            if "-thread-info" in cmd:
                return MiResult(message="done", payload=thread_info)
            return MiResult(message="done")

        with patch.object(MiBridge, "drain_events", return_value=[]), \
             patch.object(MiBridge, "command", side_effect=mock_command):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "running"

    def test_returns_unknown_when_no_info_available(self):
        """When no events, no thread info → unknown (not falsely 'running')."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        def mock_command(cmd, timeout=5.0):
            if "-thread-info" in cmd:
                return MiResult(message="error", payload={"msg": "No threads"})
            return MiResult(message="done")

        with patch.object(MiBridge, "drain_events", return_value=[]), \
             patch.object(MiBridge, "command", side_effect=mock_command):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "unknown"

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

    def test_halted_persists_across_tool_calls(self):
        """After halt sets state, snapshot sees halted even with no new events."""
        _, mgr, tools = _setup_tools()
        session = _mock_attach(mgr)

        # Simulate halt setting the persistent state
        stop = StopEvent(
            reason="signal-received",
            frame=FrameInfo(func="main", line=42, address="0x08000150"),
        )
        session.target_state.set_halted(stop)

        def mock_command(cmd, timeout=5.0):
            if "register-names" in cmd:
                return MiResult(message="done", payload={"register-names": []})
            elif "-stack-list-frames" in cmd:
                return MiResult(message="done", payload={"stack": []})
            elif "-stack-list-variables" in cmd:
                return MiResult(message="done", payload={"variables": []})
            return MiResult(message="done")

        with patch.object(MiBridge, "drain_events", return_value=[]), \
             patch.object(MiBridge, "command", side_effect=mock_command):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "halted"
        assert result["reason"] == "signal-received"
        assert result["frame"]["func"] == "main"


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


class TestSnapshotTclFallback:
    def test_snapshot_falls_back_to_tcl_registers(self):
        """When GDB returns all-zero registers, falls back to OpenOCD TCL."""
        _, mgr, tools = _setup_tools()
        session = _mock_attach(mgr)

        # Set halted state
        stop = StopEvent(
            reason="signal-received",
            frame=FrameInfo(func="main", line=10, address="0x08000100"),
        )
        session.target_state.set_halted(stop)

        # GDB returns all-zero registers (desynced)
        zero_names = {"register-names": ["r0", "r1", "sp", "lr", "pc", "xpsr"]}
        zero_values = {"register-values": [
            {"number": "0", "value": "0x00000000"},
            {"number": "1", "value": "0x00000000"},
            {"number": "2", "value": "0x00000000"},
            {"number": "3", "value": "0x00000000"},
            {"number": "4", "value": "0x00000000"},
            {"number": "5", "value": "0x00000000"},
        ]}
        bt = {"stack": []}

        def mock_cmd(cmd, timeout=5.0):
            if "register-names" in cmd:
                return MiResult(message="done", payload=zero_names)
            elif "register-values" in cmd:
                return MiResult(message="done", payload=zero_values)
            elif "-stack-list-frames" in cmd:
                return MiResult(message="done", payload=bt)
            elif "-stack-list-variables" in cmd:
                return MiResult(message="done", payload={"variables": []})
            return MiResult(message="done")

        # TCL returns real register values
        tcl_regs = {
            "r0": "0x20000100",
            "r1": "0x00000042",
            "sp": "0x20020000",
            "lr": "0x08000151",
            "pc": "0x08000300",
            "xpsr": "0x61000000",
            "msp": "0x20020000",  # Not a core register — should be filtered
        }

        with patch.object(MiBridge, "drain_events", return_value=[]), \
             patch.object(MiBridge, "command", side_effect=mock_cmd), \
             patch.object(OpenOcdProcess, "read_registers_tcl", return_value=tcl_regs):
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "halted"
        assert "registers" in result
        assert result["registers"]["pc"] == "0x08000300"
        assert result["registers"]["sp"] == "0x20020000"
        # Non-core registers should be filtered out
        assert "msp" not in result["registers"]

    def test_snapshot_no_fallback_when_registers_nonzero(self):
        """When GDB returns non-zero registers, no TCL fallback needed."""
        _, mgr, tools = _setup_tools()
        session = _mock_attach(mgr)

        stop = StopEvent(
            reason="signal-received",
            frame=FrameInfo(func="main", line=10, address="0x08000100"),
        )
        session.target_state.set_halted(stop)

        names = {"register-names": ["r0", "pc"]}
        values = {"register-values": [
            {"number": "0", "value": "0x20000100"},
            {"number": "1", "value": "0x08000150"},
        ]}

        def mock_cmd(cmd, timeout=5.0):
            if "register-names" in cmd:
                return MiResult(message="done", payload=names)
            elif "register-values" in cmd:
                return MiResult(message="done", payload=values)
            elif "-stack-list-frames" in cmd:
                return MiResult(message="done", payload={"stack": []})
            elif "-stack-list-variables" in cmd:
                return MiResult(message="done", payload={"variables": []})
            return MiResult(message="done")

        with patch.object(MiBridge, "drain_events", return_value=[]), \
             patch.object(MiBridge, "command", side_effect=mock_cmd), \
             patch.object(OpenOcdProcess, "read_registers_tcl") as mock_tcl:
            result = tools["debug_snapshot"].fn(name="daisy")

        assert result["state"] == "halted"
        assert result["registers"]["r0"] == "0x20000100"
        # TCL should NOT have been called
        mock_tcl.assert_not_called()


class TestSnapshotToolRegistration:
    def test_tool_registered(self):
        _, _, tools = _setup_tools()
        assert "debug_snapshot" in tools
