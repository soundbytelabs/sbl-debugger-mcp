"""Tests for inspection tools (mocked sessions)."""

from unittest.mock import MagicMock, patch

import pytest

from mcp.server.fastmcp import FastMCP

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import MiResult
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.tools import inspection as inspection_tools
from sbl_debugger.tools.inspection import _format_memory, _parse_hex_string
from sbl_debugger.targets import get_profile


def _setup_tools():
    """Create MCP server and manager with inspection tools registered."""
    mcp = FastMCP("test")
    mgr = SessionManager()
    inspection_tools.register_tools(mcp, mgr)
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

class TestParseHexString:
    def test_plain_hex(self):
        assert _parse_hex_string("deadbeef") == b"\xde\xad\xbe\xef"

    def test_with_0x_prefix(self):
        assert _parse_hex_string("0xdeadbeef") == b"\xde\xad\xbe\xef"

    def test_with_spaces(self):
        assert _parse_hex_string("de ad be ef") == b"\xde\xad\xbe\xef"


class TestFormatMemory:
    def test_hex(self):
        data = b"\xde\xad\xbe\xef"
        assert _format_memory(data, "hex") == "deadbeef"

    def test_u8(self):
        data = b"\x01\x02\x03"
        assert _format_memory(data, "u8") == [1, 2, 3]

    def test_u16_little_endian(self):
        # 0x0201, 0x0403
        data = b"\x01\x02\x03\x04"
        assert _format_memory(data, "u16") == [0x0201, 0x0403]

    def test_u32_little_endian(self):
        # 0x04030201
        data = b"\x01\x02\x03\x04"
        assert _format_memory(data, "u32") == [0x04030201]

    def test_u16_truncates_odd_byte(self):
        data = b"\x01\x02\x03"
        # Only first 2 bytes form a u16
        assert _format_memory(data, "u16") == [0x0201]

    def test_u32_truncates_short_data(self):
        data = b"\x01\x02"
        # Not enough for a u32
        assert _format_memory(data, "u32") == []

    def test_unknown_format_falls_back_to_hex(self):
        data = b"\xab\xcd"
        assert _format_memory(data, "unknown") == "abcd"


# -- Error handling for all tools --

class TestInspectionToolErrors:
    def test_read_registers_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["read_registers"].fn(name="nope")
        assert "error" in result

    def test_write_register_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["write_register"].fn(name="nope", register="r0", value="0")
        assert "error" in result

    def test_read_memory_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["read_memory"].fn(name="nope", address="0x0", length=4)
        assert "error" in result

    def test_write_memory_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["write_memory"].fn(name="nope", address="0x0", data="00")
        assert "error" in result

    def test_backtrace_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["backtrace"].fn(name="nope")
        assert "error" in result

    def test_read_locals_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["read_locals"].fn(name="nope")
        assert "error" in result

    def test_print_expr_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["print_expr"].fn(name="nope", expression="x")
        assert "error" in result

    def test_disassemble_nonexistent(self):
        _, _, tools = _setup_tools()
        result = tools["disassemble"].fn(name="nope")
        assert "error" in result


# -- read_registers --

class TestReadRegisters:
    def _mock_register_commands(self, names_payload, values_payload):
        """Return a side_effect for bridge.command that handles both MI commands."""
        def mock_cmd(cmd, timeout=5.0):
            if "register-names" in cmd:
                return MiResult(message="done", payload=names_payload)
            elif "register-values" in cmd:
                return MiResult(message="done", payload=values_payload)
            return MiResult(message="done")
        return mock_cmd

    def test_read_all_registers(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        names = {"register-names": ["r0", "r1", "r2", "", "sp", "lr", "pc"]}
        values = {"register-values": [
            {"number": "0", "value": "0x00000000"},
            {"number": "1", "value": "0x20000100"},
            {"number": "2", "value": "0x00000042"},
            {"number": "4", "value": "0x20020000"},
            {"number": "5", "value": "0x08000151"},
            {"number": "6", "value": "0x08000150"},
        ]}
        with patch.object(MiBridge, "command", side_effect=self._mock_register_commands(names, values)):
            result = tools["read_registers"].fn(name="daisy")

        assert "registers" in result
        regs = result["registers"]
        assert regs["r0"] == "0x00000000"
        assert regs["sp"] == "0x20020000"
        assert regs["pc"] == "0x08000150"
        # Empty name (index 3) should not appear
        assert "" not in regs

    def test_read_specific_registers(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        names = {"register-names": ["r0", "r1", "sp", "lr", "pc"]}
        values = {"register-values": [
            {"number": "2", "value": "0x20020000"},
            {"number": "4", "value": "0x08000150"},
        ]}
        with patch.object(MiBridge, "command", side_effect=self._mock_register_commands(names, values)):
            result = tools["read_registers"].fn(name="daisy", registers=["sp", "pc"])

        regs = result["registers"]
        assert "sp" in regs
        assert "pc" in regs

    def test_read_registers_no_match(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        names = {"register-names": ["r0", "r1"]}
        with patch.object(MiBridge, "command", side_effect=self._mock_register_commands(names, {})):
            result = tools["read_registers"].fn(name="daisy", registers=["xyzzy"])

        assert "error" in result
        assert "No matching" in result["error"]

    def test_read_registers_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No registers"}),
        ):
            result = tools["read_registers"].fn(name="daisy")

        assert "error" in result


# -- write_register --

class TestWriteRegister:
    def test_write_register_success(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done"),
        ) as mock_cmd:
            result = tools["write_register"].fn(name="daisy", register="r0", value="0x42")

        assert result["register"] == "r0"
        assert result["value"] == "0x42"
        # Verify the console command was sent
        call_args = mock_cmd.call_args[0][0]
        assert "set $r0 = 0x42" in call_args

    def test_write_register_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Invalid register"}),
        ):
            result = tools["write_register"].fn(name="daisy", register="bad", value="0")

        assert "error" in result


# -- read_memory --

class TestReadMemory:
    def test_read_memory_hex(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {
            "memory": [{"begin": "0x20000000", "contents": "deadbeef00000000"}]
        }
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_memory"].fn(
                name="daisy", address="0x20000000", length=8, format="hex"
            )

        assert result["data"] == "deadbeef00000000"
        assert result["length"] == 8

    def test_read_memory_u32(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        # 0x04030201, 0x08070605
        payload = {
            "memory": [{"begin": "0x20000000", "contents": "0102030405060708"}]
        }
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_memory"].fn(
                name="daisy", address="0x20000000", length=8, format="u32"
            )

        assert result["data"] == [0x04030201, 0x08070605]

    def test_read_memory_u8(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"memory": [{"begin": "0x0", "contents": "0102ff"}]}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_memory"].fn(
                name="daisy", address="0x0", length=3, format="u8"
            )

        assert result["data"] == [1, 2, 255]

    def test_read_memory_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Cannot access memory"}),
        ):
            result = tools["read_memory"].fn(
                name="daisy", address="0xFFFFFFFF", length=4
            )

        assert "error" in result

    def test_read_memory_empty_response(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"memory": []}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_memory"].fn(
                name="daisy", address="0x0", length=4
            )

        assert "error" in result


# -- write_memory --

class TestWriteMemory:
    def test_write_memory_success(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done"),
        ) as mock_cmd:
            result = tools["write_memory"].fn(
                name="daisy", address="0x20000000", data="deadbeef"
            )

        assert result["bytes_written"] == 4
        call_args = mock_cmd.call_args[0][0]
        assert "deadbeef" in call_args
        assert "0x20000000" in call_args

    def test_write_memory_cleans_hex(self):
        """0x prefixes and spaces are stripped from data."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done"),
        ) as mock_cmd:
            result = tools["write_memory"].fn(
                name="daisy", address="0x20000000", data="0xde ad 0xbe ef"
            )

        assert result["bytes_written"] == 4
        call_args = mock_cmd.call_args[0][0]
        assert "deadbeef" in call_args

    def test_write_memory_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Cannot access memory"}),
        ):
            result = tools["write_memory"].fn(
                name="daisy", address="0x08000000", data="00"
            )

        assert "error" in result


# -- backtrace --

class TestBacktrace:
    def test_backtrace_parses_frames(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {
            "stack": [
                {"frame": {
                    "level": "0", "addr": "0x08000150",
                    "func": "sbl::driver::adc::read_blocking",
                    "file": "adc.cpp", "fullname": "/src/adc.cpp", "line": "87",
                }},
                {"frame": {
                    "level": "1", "addr": "0x08000200",
                    "func": "main",
                    "file": "main.cpp", "fullname": "/src/main.cpp", "line": "42",
                }},
            ]
        }
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["backtrace"].fn(name="daisy")

        assert result["depth"] == 2
        frames = result["frames"]
        assert frames[0]["level"] == 0
        assert frames[0]["func"] == "sbl::driver::adc::read_blocking"
        assert frames[0]["line"] == 87
        assert frames[1]["level"] == 1
        assert frames[1]["func"] == "main"

    def test_backtrace_respects_max_frames(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload={"stack": []}),
        ) as mock_cmd:
            tools["backtrace"].fn(name="daisy", max_frames=5)

        # Should request frames 0-4
        call_args = mock_cmd.call_args[0][0]
        assert "-stack-list-frames 0 4" == call_args

    def test_backtrace_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No stack"}),
        ):
            result = tools["backtrace"].fn(name="daisy")

        assert "error" in result


# -- read_locals --

class TestReadLocals:
    def test_read_locals_parses_variables(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {
            "variables": [
                {"name": "x", "value": "42"},
                {"name": "ptr", "value": "0x20001000"},
                {"name": "flag", "value": "true"},
            ]
        }
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_locals"].fn(name="daisy")

        assert len(result["variables"]) == 3
        assert result["variables"][0] == {"name": "x", "value": "42"}
        assert result["variables"][1] == {"name": "ptr", "value": "0x20001000"}

    def test_read_locals_empty(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"variables": []}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_locals"].fn(name="daisy")

        assert result["variables"] == []

    def test_read_locals_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No frame"}),
        ):
            result = tools["read_locals"].fn(name="daisy")

        assert "error" in result


# -- print_expr --

class TestPrintExpr:
    def test_evaluate_expression(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"value": "42"}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["print_expr"].fn(name="daisy", expression="x + 1")

        assert result["value"] == "42"
        assert result["expression"] == "x + 1"

    def test_evaluate_pointer_deref(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"value": "{field1 = 0, field2 = 100}"}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["print_expr"].fn(name="daisy", expression="*my_struct_ptr")

        assert "field1" in result["value"]

    def test_evaluate_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No symbol \"x\""}),
        ):
            result = tools["print_expr"].fn(name="daisy", expression="x")

        assert "error" in result

    def test_expression_with_quotes_escaped(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload={"value": "1"}),
        ) as mock_cmd:
            tools["print_expr"].fn(name="daisy", expression='sizeof("hello")')

        call_args = mock_cmd.call_args[0][0]
        assert '\\"hello\\"' in call_args


# -- disassemble --

class TestDisassemble:
    def test_disassemble_at_pc(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {
            "asm_insns": [
                {"address": "0x08000150", "func-name": "main", "offset": "0", "inst": "push {r7, lr}"},
                {"address": "0x08000152", "func-name": "main", "offset": "2", "inst": "add r7, sp, #0"},
                {"address": "0x08000154", "func-name": "main", "offset": "4", "inst": "movs r0, #0"},
            ]
        }
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["disassemble"].fn(name="daisy")

        # Default address should be $pc
        call_args = mock_cmd.call_args[0][0]
        assert "$pc" in call_args

        assert len(result["instructions"]) == 3
        assert result["instructions"][0]["inst"] == "push {r7, lr}"
        assert result["instructions"][0]["address"] == "0x08000150"
        assert result["instructions"][0]["func"] == "main"

    def test_disassemble_at_address(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        payload = {"asm_insns": [
            {"address": "0x08001000", "func-name": "init", "offset": "0", "inst": "bx lr"},
        ]}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["disassemble"].fn(name="daisy", address="0x08001000", count=5)

        call_args = mock_cmd.call_args[0][0]
        assert "0x08001000" in call_args
        assert result["start"] == "0x08001000"

    def test_disassemble_limits_count(self):
        """If GDB returns more instructions than requested, truncate."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        insns = [
            {"address": f"0x{0x08000150 + i*2:08x}", "func-name": "f", "offset": str(i*2), "inst": "nop"}
            for i in range(20)
        ]
        payload = {"asm_insns": insns}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["disassemble"].fn(name="daisy", count=5)

        assert len(result["instructions"]) == 5

    def test_disassemble_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Cannot disassemble"}),
        ):
            result = tools["disassemble"].fn(name="daisy")

        assert "error" in result


# -- Tool registration --

class TestInspectionToolRegistration:
    def test_all_tools_registered(self):
        mcp, _, tools = _setup_tools()
        expected = {
            "read_registers", "write_register",
            "read_memory", "write_memory",
            "backtrace", "read_locals",
            "print_expr", "disassemble",
        }
        assert expected.issubset(set(tools.keys()))
