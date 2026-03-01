"""Tests for PeripheralDb — SVD register lookup and decoding."""

import pytest

from sbl_debugger.svd.peripheral_db import (
    DecodedField,
    DecodedRegister,
    PeripheralDb,
    _bit_range_str,
    _truncate,
)

# Import cecrops types for building test fixtures
from cecrops.parser import BitField, Device, Peripheral, Register


def _make_device() -> Device:
    """Build a synthetic Device for testing."""
    return Device(
        name="STM32TEST",
        version="1.0",
        description="Test device",
        vendor="test",
        cpu_name="CM7",
        cpu_arch="arm-cortex-m7",
        peripherals=[
            Peripheral(
                name="GPIOA",
                description="General purpose I/O port A",
                base_address=0x58020000,
                group_name="GPIO",
                registers=[
                    Register(
                        name="MODER",
                        description="GPIO port mode register",
                        address_offset=0x00,
                        size=32,
                        access="read-write",
                        reset_value=0xFFFFFFFF,
                        fields=[
                            BitField(name="MODE0", description="Port x pin 0 mode", bit_offset=0, bit_width=2),
                            BitField(name="MODE1", description="Port x pin 1 mode", bit_offset=2, bit_width=2),
                            BitField(name="MODE15", description="Port x pin 15 mode", bit_offset=30, bit_width=2),
                        ],
                    ),
                    Register(
                        name="IDR",
                        description="GPIO port input data register",
                        address_offset=0x10,
                        size=32,
                        access="read-only",
                        fields=[
                            BitField(name="ID0", description="Pin 0 input", bit_offset=0, bit_width=1, access="read-only"),
                            BitField(name="ID1", description="Pin 1 input", bit_offset=1, bit_width=1, access="read-only"),
                        ],
                    ),
                ],
            ),
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
                            BitField(name="MODE0", description="Port x pin 0 mode", bit_offset=0, bit_width=2),
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
                        description="Clock control register",
                        address_offset=0x00,
                        size=32,
                        fields=[
                            BitField(name="HSION", description="HSI enable", bit_offset=0, bit_width=1),
                            BitField(name="HSIRDY", description="HSI ready", bit_offset=2, bit_width=1, access="read-only"),
                            BitField(name="HSIDIV", description="HSI divider", bit_offset=3, bit_width=2),
                        ],
                    ),
                ],
            ),
            Peripheral(
                name="EMPTY",
                description="Peripheral with no registers",
                base_address=0x50000000,
                group_name="OTHER",
                registers=[],
            ),
            Peripheral(
                name="SMALL",
                description="Peripheral with 16-bit register",
                base_address=0x40000000,
                group_name="OTHER",
                registers=[
                    Register(
                        name="SR",
                        description="Status register",
                        address_offset=0x00,
                        size=16,
                        access="read-only",
                        fields=[],
                    ),
                ],
            ),
        ],
    )


@pytest.fixture
def db() -> PeripheralDb:
    return PeripheralDb(_make_device())


# -- Construction --

class TestPeripheralDbConstruction:
    def test_device_name(self, db):
        assert db.device_name == "STM32TEST"

    def test_peripherals_indexed(self, db):
        periphs = db.list_peripherals()
        assert len(periphs) == 5

    def test_address_index_built(self, db):
        # GPIOA MODER at 0x58020000, GPIOA IDR at 0x58020010
        result = db.lookup_address(0x58020000)
        assert result == ("GPIOA", "MODER")


# -- list_peripherals --

class TestListPeripherals:
    def test_list_all(self, db):
        result = db.list_peripherals()
        names = [p["name"] for p in result]
        assert "GPIOA" in names
        assert "GPIOB" in names
        assert "RCC" in names

    def test_list_with_filter(self, db):
        result = db.list_peripherals("GPIO")
        names = [p["name"] for p in result]
        assert "GPIOA" in names
        assert "GPIOB" in names
        assert "RCC" not in names

    def test_filter_case_insensitive(self, db):
        result = db.list_peripherals("gpio")
        assert len(result) == 2

    def test_filter_no_match(self, db):
        result = db.list_peripherals("NONEXISTENT")
        assert result == []

    def test_includes_base_address(self, db):
        result = db.list_peripherals("RCC")
        assert result[0]["base_address"] == "0x58024400"

    def test_includes_register_count(self, db):
        result = db.list_peripherals("GPIOA")
        assert result[0]["registers"] == 2

    def test_includes_group(self, db):
        result = db.list_peripherals("GPIOA")
        assert result[0]["group"] == "GPIO"


# -- list_registers --

class TestListRegisters:
    def test_list_registers(self, db):
        result = db.list_registers("GPIOA")
        assert len(result) == 2
        assert result[0]["name"] == "MODER"
        assert result[1]["name"] == "IDR"

    def test_includes_offset(self, db):
        result = db.list_registers("GPIOA")
        assert result[0]["offset"] == "0x00"
        assert result[1]["offset"] == "0x10"

    def test_includes_address(self, db):
        result = db.list_registers("GPIOA")
        assert result[0]["address"] == "0x58020000"

    def test_includes_fields(self, db):
        result = db.list_registers("GPIOA")
        fields = result[0]["fields"]
        assert len(fields) == 3
        assert fields[0]["name"] == "MODE0"
        assert fields[0]["bits"] == "[1:0]"

    def test_case_insensitive(self, db):
        result = db.list_registers("gpioa")
        assert len(result) == 2

    def test_invalid_peripheral(self, db):
        with pytest.raises(ValueError, match="Unknown peripheral"):
            db.list_registers("NONEXISTENT")

    def test_no_fields_key_when_empty(self, db):
        result = db.list_registers("SMALL")
        assert "fields" not in result[0]


# -- decode_register --

class TestDecodeRegister:
    def test_decode_moder(self, db):
        # MODE0=0b11 (analog), MODE1=0b10 (alt func), MODE15=0b01 (output)
        raw = 0b01_000000_00000000_00000000_00001011
        decoded = db.decode_register("GPIOA", "MODER", raw)

        assert decoded.peripheral == "GPIOA"
        assert decoded.register == "MODER"
        assert decoded.address == 0x58020000
        assert decoded.raw_value == raw

        fields_by_name = {f.name: f for f in decoded.fields}
        assert fields_by_name["MODE0"].value == 3
        assert fields_by_name["MODE1"].value == 2
        assert fields_by_name["MODE15"].value == 1

    def test_decode_single_bit_fields(self, db):
        # HSION=1, HSIRDY=1
        raw = 0b00000101
        decoded = db.decode_register("RCC", "CR", raw)
        fields_by_name = {f.name: f for f in decoded.fields}
        assert fields_by_name["HSION"].value == 1
        assert fields_by_name["HSIRDY"].value == 1

    def test_decode_all_zeros(self, db):
        decoded = db.decode_register("GPIOA", "MODER", 0)
        for f in decoded.fields:
            assert f.value == 0

    def test_decode_all_ones(self, db):
        decoded = db.decode_register("GPIOA", "MODER", 0xFFFFFFFF)
        fields_by_name = {f.name: f for f in decoded.fields}
        assert fields_by_name["MODE0"].value == 3  # 2-bit max
        assert fields_by_name["MODE15"].value == 3

    def test_decode_preserves_field_metadata(self, db):
        decoded = db.decode_register("GPIOA", "IDR", 0)
        f = decoded.fields[0]
        assert f.name == "ID0"
        assert f.bit_range == "[0]"
        assert f.width == 1
        assert f.access == "read-only"
        assert f.description == "Pin 0 input"

    def test_case_insensitive_peripheral(self, db):
        decoded = db.decode_register("gpioa", "MODER", 0)
        assert decoded.peripheral == "GPIOA"

    def test_case_insensitive_register(self, db):
        decoded = db.decode_register("GPIOA", "moder", 0)
        assert decoded.register == "MODER"

    def test_invalid_peripheral(self, db):
        with pytest.raises(ValueError, match="Unknown peripheral"):
            db.decode_register("NOPE", "MODER", 0)

    def test_invalid_register(self, db):
        with pytest.raises(ValueError, match="Unknown register"):
            db.decode_register("GPIOA", "NOPE", 0)

    def test_register_with_no_fields(self, db):
        decoded = db.decode_register("SMALL", "SR", 0x1234)
        assert decoded.fields == []
        assert decoded.raw_value == 0x1234


# -- get_register_address --

class TestGetRegisterAddress:
    def test_gpioa_moder(self, db):
        assert db.get_register_address("GPIOA", "MODER") == 0x58020000

    def test_gpioa_idr(self, db):
        assert db.get_register_address("GPIOA", "IDR") == 0x58020010

    def test_gpiob_moder(self, db):
        assert db.get_register_address("GPIOB", "MODER") == 0x58020400


# -- lookup_address --

class TestLookupAddress:
    def test_exact_match(self, db):
        assert db.lookup_address(0x58020000) == ("GPIOA", "MODER")

    def test_exact_match_idr(self, db):
        assert db.lookup_address(0x58020010) == ("GPIOA", "IDR")

    def test_no_match(self, db):
        assert db.lookup_address(0x12345678) is None

    def test_rcc_cr(self, db):
        assert db.lookup_address(0x58024400) == ("RCC", "CR")


# -- Helpers --

class TestBitRangeStr:
    def test_single_bit(self):
        f = BitField(name="X", description="", bit_offset=5, bit_width=1)
        assert _bit_range_str(f) == "[5]"

    def test_multi_bit(self):
        f = BitField(name="X", description="", bit_offset=3, bit_width=2)
        assert _bit_range_str(f) == "[4:3]"

    def test_full_register(self):
        f = BitField(name="X", description="", bit_offset=0, bit_width=32)
        assert _bit_range_str(f) == "[31:0]"


class TestTruncate:
    def test_short_text(self):
        assert _truncate("hello", 80) == "hello"

    def test_long_text(self):
        result = _truncate("x" * 100, 20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_strips_newlines(self):
        assert _truncate("hello\nworld", 80) == "hello world"
