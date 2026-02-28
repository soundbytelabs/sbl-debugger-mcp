"""Tests for advanced tools: load and monitor."""

from unittest.mock import patch

import pytest

from mcp.server.fastmcp import FastMCP

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import MiResult
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.targets import get_profile
from sbl_debugger.tools import advanced as advanced_tools


def _setup_tools():
    """Create MCP server and manager with advanced tools registered."""
    mcp = FastMCP("test")
    mgr = SessionManager()
    advanced_tools.register_tools(mcp, mgr)
    tools = {t.name: t for t in mcp._tool_manager._tools.values()}
    return mcp, mgr, tools


def _mock_attach(manager, name="daisy", elf_path=None):
    """Attach with fully mocked OpenOCD + GDB."""
    profile = get_profile(name)
    with patch.object(OpenOcdProcess, "start"), \
         patch.object(OpenOcdProcess, "is_alive", new_callable=lambda: property(lambda self: True)), \
         patch.object(MiBridge, "start"), \
         patch.object(MiBridge, "load_symbols", return_value=MiResult(message="done")), \
         patch.object(MiBridge, "connect", return_value=MiResult(message="connected")), \
         patch.object(MiBridge, "is_connected", new_callable=lambda: property(lambda self: True)):
        return manager.attach(
            target_profile=profile, target_name=name, elf_path=elf_path
        )


# -- Load tool --

class TestLoadErrors:
    def test_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["load"].fn(name="nope")
        assert "error" in result

    def test_no_elf_path(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        result = tools["load"].fn(name="daisy")
        assert "error" in result
        assert "No ELF" in result["error"]


class TestLoad:
    def test_load_with_explicit_elf(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        dl_payload = {
            "total-size": "8192",
            "transfer-rate": "4096",
            "write-rate": "2048",
        }

        def mock_cmd(cmd, timeout=5.0):
            if "-target-download" in cmd:
                return MiResult(message="done", payload=dl_payload)
            return MiResult(message="done")

        with patch.object(MiBridge, "load_symbols", return_value=MiResult(message="done")), \
             patch.object(MiBridge, "command", side_effect=mock_cmd), \
             patch.object(MiBridge, "monitor", return_value=MiResult(message="done")), \
             patch.object(MiBridge, "drain_events", return_value=[]):
            result = tools["load"].fn(name="daisy", elf="/path/to/firmware.elf")

        assert result["status"] == "flashed"
        assert result["elf"] == "/path/to/firmware.elf"
        assert result["total_bytes"] == 8192
        assert result["state"] == "halted"

    def test_load_uses_session_elf(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr, elf_path="/session/firmware.elf")

        def mock_cmd(cmd, timeout=5.0):
            return MiResult(message="done", payload={})

        with patch.object(MiBridge, "load_symbols", return_value=MiResult(message="done")), \
             patch.object(MiBridge, "command", side_effect=mock_cmd), \
             patch.object(MiBridge, "monitor", return_value=MiResult(message="done")), \
             patch.object(MiBridge, "drain_events", return_value=[]):
            result = tools["load"].fn(name="daisy")

        assert result["status"] == "flashed"
        assert result["elf"] == "/session/firmware.elf"

    def test_load_symbol_failure(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "load_symbols",
            return_value=MiResult(message="error", payload={"msg": "No such file"}),
        ):
            result = tools["load"].fn(name="daisy", elf="/bad/path.elf")

        assert "error" in result
        assert "Symbol load failed" in result["error"]

    def test_load_download_failure(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(MiBridge, "load_symbols", return_value=MiResult(message="done")), \
             patch.object(
                 MiBridge, "command",
                 return_value=MiResult(message="error", payload={"msg": "Flash write failed"}),
             ):
            result = tools["load"].fn(name="daisy", elf="/path/firmware.elf")

        assert "error" in result
        assert "Download failed" in result["error"]

    def test_load_updates_session_elf(self):
        _, mgr, tools = _setup_tools()
        session = _mock_attach(mgr, elf_path="/old/firmware.elf")

        def mock_cmd(cmd, timeout=5.0):
            return MiResult(message="done", payload={})

        with patch.object(MiBridge, "load_symbols", return_value=MiResult(message="done")), \
             patch.object(MiBridge, "command", side_effect=mock_cmd), \
             patch.object(MiBridge, "monitor", return_value=MiResult(message="done")), \
             patch.object(MiBridge, "drain_events", return_value=[]):
            tools["load"].fn(name="daisy", elf="/new/firmware.elf")

        assert session.elf_path == "/new/firmware.elf"


# -- Monitor tool --

class TestMonitorErrors:
    def test_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["monitor"].fn(name="nope", command="flash banks")
        assert "error" in result


class TestMonitor:
    def test_monitor_returns_output(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "monitor",
            return_value=MiResult(
                message="done",
                console_output=["#0 : stm32h7x at 0x08000000, size 0x00020000"],
            ),
        ):
            result = tools["monitor"].fn(name="daisy", command="flash banks")

        assert result["command"] == "flash banks"
        assert len(result["output"]) == 1
        assert "stm32h7x" in result["output"][0]

    def test_monitor_empty_output(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "monitor",
            return_value=MiResult(message="done", console_output=[]),
        ):
            result = tools["monitor"].fn(name="daisy", command="reset init")

        assert result["output"] == []

    def test_monitor_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "monitor",
            return_value=MiResult(message="error", payload={"msg": "Unknown command"}),
        ):
            result = tools["monitor"].fn(name="daisy", command="bad_cmd")

        assert "error" in result


# -- Registration --

class TestAdvancedToolRegistration:
    def test_all_tools_registered(self):
        _, _, tools = _setup_tools()
        assert "load" in tools
        assert "monitor" in tools
