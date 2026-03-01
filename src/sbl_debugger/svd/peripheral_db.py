"""Peripheral register database built from cecrops SVD data."""

from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from cecrops.parser import BitField, Device, Peripheral, Register

    CECROPS_AVAILABLE = True
except ImportError:
    CECROPS_AVAILABLE = False


@dataclass(frozen=True)
class DecodedField:
    """A single decoded bitfield from a register read."""

    name: str
    value: int
    bit_range: str  # e.g. "[13:12]"
    width: int
    description: str
    access: str  # "read-write", "read-only", etc.


@dataclass(frozen=True)
class DecodedRegister:
    """A fully decoded register with all bitfields extracted."""

    peripheral: str
    register: str
    address: int
    raw_value: int
    fields: list[DecodedField]


class PeripheralDb:
    """Lookup and decode layer over a cecrops Device.

    Provides case-insensitive peripheral/register lookup and bitfield
    decoding from raw memory values.
    """

    def __init__(self, device: Device) -> None:
        self._device = device
        # Case-insensitive name → Peripheral
        self._by_name: dict[str, Peripheral] = {
            p.name.upper(): p for p in device.peripherals
        }
        # Sorted (address, peripheral, register) for address lookup
        self._by_address: list[tuple[int, Peripheral, Register]] = []
        for p in device.peripherals:
            for r in p.registers:
                addr = p.base_address + r.address_offset
                self._by_address.append((addr, p, r))
        self._by_address.sort(key=lambda x: x[0])

    @property
    def device_name(self) -> str:
        return self._device.name

    def list_peripherals(
        self, filter_pattern: str | None = None,
    ) -> list[dict]:
        """List peripherals, optionally filtered by regex pattern."""
        compiled = None
        if filter_pattern:
            compiled = re.compile(filter_pattern, re.IGNORECASE)

        result = []
        for p in self._device.peripherals:
            if compiled and not compiled.search(p.name):
                continue
            result.append({
                "name": p.name,
                "base_address": f"0x{p.base_address:08X}",
                "registers": len(p.registers),
                "group": p.group_name or p.name,
                "description": _truncate(p.description, 80),
            })
        return result

    def list_registers(self, peripheral: str) -> list[dict]:
        """List all registers and their fields for a peripheral."""
        p = self._get_peripheral(peripheral)
        result = []
        for r in p.registers:
            reg_dict: dict = {
                "name": r.name,
                "offset": f"0x{r.address_offset:02X}",
                "address": f"0x{p.base_address + r.address_offset:08X}",
                "size": r.size,
                "access": r.access,
                "description": _truncate(r.description, 80),
            }
            if r.fields:
                reg_dict["fields"] = [
                    {
                        "name": f.name,
                        "bits": _bit_range_str(f),
                        "width": f.bit_width,
                        "access": f.access,
                        "description": _truncate(f.description, 60),
                    }
                    for f in r.fields
                ]
            result.append(reg_dict)
        return result

    def get_register_address(self, peripheral: str, register: str) -> int:
        """Get the absolute memory address of a register."""
        p = self._get_peripheral(peripheral)
        r = self._get_register(p, register)
        return p.base_address + r.address_offset

    def decode_register(
        self, peripheral: str, register: str, raw_value: int,
    ) -> DecodedRegister:
        """Decode a raw register value into named bitfields."""
        p = self._get_peripheral(peripheral)
        r = self._get_register(p, register)
        address = p.base_address + r.address_offset

        fields = []
        for f in r.fields:
            mask = ((1 << f.bit_width) - 1)
            extracted = (raw_value >> f.bit_offset) & mask
            fields.append(DecodedField(
                name=f.name,
                value=extracted,
                bit_range=_bit_range_str(f),
                width=f.bit_width,
                description=f.description,
                access=f.access,
            ))

        return DecodedRegister(
            peripheral=p.name,
            register=r.name,
            address=address,
            raw_value=raw_value,
            fields=fields,
        )

    def lookup_address(self, address: int) -> tuple[str, str] | None:
        """Find which peripheral/register owns a given address.

        Returns (peripheral_name, register_name) or None.
        """
        # Binary search for exact match
        lo, hi = 0, len(self._by_address)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._by_address[mid][0] < address:
                lo = mid + 1
            elif self._by_address[mid][0] > address:
                hi = mid
            else:
                _, p, r = self._by_address[mid]
                return (p.name, r.name)
        return None

    def _get_peripheral(self, name: str) -> Peripheral:
        """Look up a peripheral by name (case-insensitive)."""
        p = self._by_name.get(name.upper())
        if p is None:
            available = sorted(self._by_name.keys())[:10]
            suffix = "..." if len(self._by_name) > 10 else ""
            raise ValueError(
                f"Unknown peripheral '{name}'. "
                f"Available: {', '.join(available)}{suffix}"
            )
        return p

    def _get_register(self, peripheral: Peripheral, name: str) -> Register:
        """Look up a register within a peripheral (case-insensitive)."""
        upper = name.upper()
        for r in peripheral.registers:
            if r.name.upper() == upper:
                return r
        available = [r.name for r in peripheral.registers[:10]]
        suffix = "..." if len(peripheral.registers) > 10 else ""
        raise ValueError(
            f"Unknown register '{name}' in {peripheral.name}. "
            f"Available: {', '.join(available)}{suffix}"
        )


def _bit_range_str(field: BitField) -> str:
    """Format a bitfield's position as '[msb:lsb]' or '[bit]'."""
    if field.bit_width == 1:
        return f"[{field.bit_offset}]"
    msb = field.bit_offset + field.bit_width - 1
    return f"[{msb}:{field.bit_offset}]"


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if too long."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
