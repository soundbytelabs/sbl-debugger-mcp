"""Tests for peripheral tools (mocked sessions + synthetic SVD)."""

from unittest.mock import patch

import pytest

from mcp.server.fastmcp import FastMCP

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import MiResult
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.svd.peripheral_db import PeripheralDb
from sbl_debugger.tools import peripheral as peripheral_tools
from sbl_debugger.targets import get_profile

from cecrops.parser import BitField, Device, Peripheral, Register


def _setup_tools():
    """Create MCP server and manager with peripheral tools registered."""
    mcp = FastMCP("test")
    mgr = SessionManager()
    peripheral_tools.register_tools(mcp, mgr)
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


def _make_device() -> Device:
    """Build a synthetic Device for testing."""
    return Device(
        name="STM32H750",
        version="1.0",
        description="Test MCU",
        vendor="st",
        cpu_name="CM7",
        cpu_arch="arm-cortex-m7",
        peripherals=[
            Peripheral(
                name="GPIOB",
                description="General purpose I/O port B",
                base_address=0x58020400,
                group_name="GPIO",
                registers=[
                    Register(
                        name="MODER",
                        description="GPIO port mode register",
                        address_offset=0x00,
                        size=32,
                        fields=[
                            BitField(name="MODE0", description="Pin 0 mode", bit_offset=0, bit_width=2),
                            BitField(name="MODE1", description="Pin 1 mode", bit_offset=2, bit_width=2),
                        ],
                    ),
                    Register(
                        name="IDR",
                        description="GPIO port input data register",
                        address_offset=0x10,
                        size=32,
                        access="read-only",
                        fields=[
                            BitField(name="ID0", description="Pin 0", bit_offset=0, bit_width=1, access="read-only"),
                        ],
                    ),
                ],
            ),
            Peripheral(
                name="RCC",
                description="Reset and clock control",
                base_address=0x58024400,
                group_name="RCC",
                registers=[
                    Register(
                        name="CR",
                        description="Clock control",
                        address_offset=0x00,
                        size=32,
                        fields=[
                            BitField(name="HSION", description="HSI enable", bit_offset=0, bit_width=1),
                        ],
                    ),
                    Register(
                        name="CFGR",
                        description="Clock configuration",
                        address_offset=0x10,
                        size=32,
                        fields=[],
                    ),
                ],
            ),
        ],
    )


def _inject_svd(manager, name="daisy"):
    """Inject a synthetic PeripheralDb into the session."""
    session = manager.get(name)
    session.svd = PeripheralDb(_make_device())


# -- Error handling for all tools --

class TestPeripheralToolErrors:
    def test_list_peripherals_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["list_peripherals"].fn(name="nope")
        assert "error" in result

    def test_list_registers_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["list_registers"].fn(name="nope", peripheral="GPIO")
        assert "error" in result

    def test_read_peripheral_register_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["read_peripheral_register"].fn(
            name="nope", peripheral="GPIO", register="MODER"
        )
        assert "error" in result

    def test_read_peripheral_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["read_peripheral"].fn(name="nope", peripheral="GPIO")
        assert "error" in result


# -- SVD lazy loading --

class TestSvdLazyLoading:
    def test_no_cecrops_returns_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch("sbl_debugger.tools.peripheral.CECROPS_AVAILABLE", False):
            result = tools["list_peripherals"].fn(name="daisy")

        assert "error" in result
        assert "cecrops" in result["error"].lower()

    def test_no_mcu_in_profile_returns_error(self):
        """Custom target with no MCU defined."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr, "pico")  # pico has no mcu field

        result = tools["list_peripherals"].fn(name="pico")
        assert "error" in result
        assert "No MCU" in result["error"]

    def test_svd_load_failure_returns_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch("sbl_debugger.tools.peripheral.load_peripheral_db", return_value=None):
            result = tools["list_peripherals"].fn(name="daisy")

        assert "error" in result
        assert "Could not load SVD" in result["error"]

    def test_lazy_load_caches_in_session(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mock_db = PeripheralDb(_make_device())
        with patch("sbl_debugger.tools.peripheral.load_peripheral_db", return_value=mock_db) as mock_load:
            tools["list_peripherals"].fn(name="daisy")
            tools["list_peripherals"].fn(name="daisy")

        # Should only call load once — second call uses cached session.svd
        mock_load.assert_called_once()

    def test_pre_loaded_svd_used(self):
        """If session.svd is already set, no loading happens."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        with patch("sbl_debugger.tools.peripheral.load_peripheral_db") as mock_load:
            result = tools["list_peripherals"].fn(name="daisy")

        mock_load.assert_not_called()
        assert "peripherals" in result


# -- list_peripherals --

class TestListPeripherals:
    def test_list_all(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["list_peripherals"].fn(name="daisy")

        assert result["device"] == "STM32H750"
        assert result["count"] == 2
        names = [p["name"] for p in result["peripherals"]]
        assert "GPIOB" in names
        assert "RCC" in names

    def test_list_with_filter(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["list_peripherals"].fn(name="daisy", filter="GPIO")

        assert result["count"] == 1
        assert result["peripherals"][0]["name"] == "GPIOB"

    def test_includes_metadata(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["list_peripherals"].fn(name="daisy", filter="GPIOB")

        p = result["peripherals"][0]
        assert p["base_address"] == "0x58020400"
        assert p["registers"] == 2
        assert p["group"] == "GPIO"


# -- list_registers --

class TestListRegisters:
    def test_list_registers(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["list_registers"].fn(name="daisy", peripheral="GPIOB")

        assert result["count"] == 2
        assert result["registers"][0]["name"] == "MODER"
        assert result["registers"][1]["name"] == "IDR"

    def test_includes_fields(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["list_registers"].fn(name="daisy", peripheral="GPIOB")

        moder = result["registers"][0]
        assert "fields" in moder
        assert moder["fields"][0]["name"] == "MODE0"

    def test_invalid_peripheral(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["list_registers"].fn(name="daisy", peripheral="NOPE")

        assert "error" in result
        assert "Unknown peripheral" in result["error"]


# -- read_peripheral_register --

class TestReadPeripheralRegister:
    def test_reads_and_decodes(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        # MODE0=0b11 (analog), MODE1=0b10 (alt func)
        # Little-endian: 0x0000000B → "0b000000000000000000000000_00001011"
        payload = {
            "memory": [{"begin": "0x58020400", "contents": "0b000000"}]
        }
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["read_peripheral_register"].fn(
                name="daisy", peripheral="GPIOB", register="MODER"
            )

        assert result["peripheral"] == "GPIOB"
        assert result["register"] == "MODER"
        assert result["address"] == "0x58020400"

        # Verify the MI command used the correct address
        call_args = mock_cmd.call_args[0][0]
        assert "0x58020400" in call_args
        assert "4" in call_args

        fields_by_name = {f["name"]: f for f in result["fields"]}
        assert "MODE0" in fields_by_name
        assert "MODE1" in fields_by_name

    def test_reads_correct_address(self):
        """IDR at offset 0x10 from GPIOB base 0x58020400."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        payload = {"memory": [{"contents": "03000000"}]}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["read_peripheral_register"].fn(
                name="daisy", peripheral="GPIOB", register="IDR"
            )

        call_args = mock_cmd.call_args[0][0]
        assert "0x58020410" in call_args
        assert result["address"] == "0x58020410"

    def test_decodes_field_values(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        # 0x0000000B = MODE0=11, MODE1=10
        payload = {"memory": [{"contents": "0b000000"}]}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_peripheral_register"].fn(
                name="daisy", peripheral="GPIOB", register="MODER"
            )

        fields_by_name = {f["name"]: f for f in result["fields"]}
        assert fields_by_name["MODE0"]["value"] == 3
        assert fields_by_name["MODE1"]["value"] == 2

    def test_invalid_peripheral(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["read_peripheral_register"].fn(
            name="daisy", peripheral="NOPE", register="CR"
        )
        assert "error" in result

    def test_invalid_register(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["read_peripheral_register"].fn(
            name="daisy", peripheral="GPIOB", register="NOPE"
        )
        assert "error" in result

    def test_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Cannot access memory"}),
        ):
            result = tools["read_peripheral_register"].fn(
                name="daisy", peripheral="GPIOB", register="MODER"
            )

        assert "error" in result

    def test_empty_memory_response(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        payload = {"memory": []}
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_peripheral_register"].fn(
                name="daisy", peripheral="GPIOB", register="MODER"
            )

        assert "error" in result


# -- read_peripheral --

class TestReadPeripheral:
    def test_bulk_read_all_registers(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        # RCC has CR at offset 0x00 and CFGR at offset 0x10
        # Span = 0x10 + 4 = 0x14 = 20 bytes
        # CR raw = 0x00000005, then 12 bytes padding, CFGR raw = 0x00000000
        hex_data = "05000000" + "00" * 12 + "00000000"
        payload = {"memory": [{"contents": hex_data}]}

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ) as mock_cmd:
            result = tools["read_peripheral"].fn(name="daisy", peripheral="RCC")

        assert result["peripheral"] == "RCC"
        assert result["base_address"] == "0x58024400"
        assert result["count"] == 2

        # Should be a single bulk read, not individual reads
        assert mock_cmd.call_count == 1
        call_args = mock_cmd.call_args[0][0]
        assert "0x58024400" in call_args

        regs = result["registers"]
        assert regs[0]["register"] == "CR"
        assert regs[0]["raw"] == "0x00000005"
        assert regs[1]["register"] == "CFGR"

    def test_includes_decoded_fields(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        # HSION=1 in CR
        hex_data = "01000000" + "00" * 12 + "00000000"
        payload = {"memory": [{"contents": hex_data}]}

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done", payload=payload),
        ):
            result = tools["read_peripheral"].fn(name="daisy", peripheral="RCC")

        cr_fields = result["registers"][0]["fields"]
        assert cr_fields[0]["name"] == "HSION"
        assert cr_fields[0]["value"] == 1

    def test_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Failed"}),
        ):
            result = tools["read_peripheral"].fn(name="daisy", peripheral="RCC")

        assert "error" in result

    def test_invalid_peripheral(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)
        _inject_svd(mgr)

        result = tools["read_peripheral"].fn(name="daisy", peripheral="NOPE")
        assert "error" in result


# -- Tool registration --

class TestPeripheralToolRegistration:
    def test_all_tools_registered(self):
        _, _, tools = _setup_tools()
        expected = {
            "list_peripherals", "list_registers",
            "read_peripheral_register", "read_peripheral",
        }
        assert expected.issubset(set(tools.keys()))
