"""Peripheral register tools: SVD-aware register decoding."""

from __future__ import annotations

import struct

from sbl_debugger.session.manager import SessionManager
from sbl_debugger.svd.peripheral_db import CECROPS_AVAILABLE, PeripheralDb
from sbl_debugger.svd.loader import load_peripheral_db
from sbl_debugger.targets import TARGET_PROFILES


def _ensure_svd(session) -> PeripheralDb:
    """Lazy-load PeripheralDb into session.svd.

    Raises ValueError with an actionable message on failure.
    """
    if session.svd is not None:
        return session.svd

    if not CECROPS_AVAILABLE:
        raise ValueError(
            "cecrops is not installed. Install it with: "
            "pip install -e tools/cecrops"
        )

    # Get MCU name from target profile
    profile = TARGET_PROFILES.get(session.target)
    mcu = profile.mcu if profile else None
    if not mcu:
        raise ValueError(
            f"No MCU defined for target '{session.target}'. "
            "SVD peripheral decoding requires an MCU with a cecrops manifest."
        )

    db = load_peripheral_db(mcu)
    if db is None:
        import os
        hw_path = os.environ.get("SBL_HW_PATH", "(not set)")
        raise ValueError(
            f"Could not load SVD for MCU '{mcu}'. "
            f"Check that SBL_HW_PATH={hw_path} contains "
            f"mcu/arm/{mcu}/cecrops.json and .cache/*.svd"
        )

    session.svd = db
    return db


def register_tools(mcp, manager: SessionManager) -> None:
    """Register peripheral tools with the MCP server."""

    @mcp.tool()
    def list_peripherals(
        name: str,
        filter: str | None = None,
    ) -> dict:
        """List available peripherals from the target's SVD.

        Returns peripheral names, base addresses, register counts, and
        groups. Use the optional filter (regex) to narrow results.

        Args:
            name: Session name.
            filter: Optional regex to filter peripheral names (case-insensitive).
        """
        try:
            session = manager.get(name)
            db = _ensure_svd(session)
            peripherals = db.list_peripherals(filter)
            return {
                "name": name,
                "device": db.device_name,
                "peripherals": peripherals,
                "count": len(peripherals),
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def list_registers(name: str, peripheral: str) -> dict:
        """List all registers and field definitions for a peripheral.

        Pure SVD metadata — does not read from the target.

        Args:
            name: Session name.
            peripheral: Peripheral name (e.g., "GPIOB", "RCC"). Case-insensitive.
        """
        try:
            session = manager.get(name)
            db = _ensure_svd(session)
            registers = db.list_registers(peripheral)
            return {
                "name": name,
                "peripheral": peripheral,
                "registers": registers,
                "count": len(registers),
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def read_peripheral_register(
        name: str,
        peripheral: str,
        register: str,
    ) -> dict:
        """Read a single peripheral register and decode all bitfields.

        Reads the register from target memory using its SVD-defined
        address, then decodes the raw value into named fields with
        bit positions and values.

        Args:
            name: Session name.
            peripheral: Peripheral name (e.g., "GPIOB"). Case-insensitive.
            register: Register name (e.g., "MODER"). Case-insensitive.
        """
        try:
            session = manager.get(name)
            db = _ensure_svd(session)
            address = db.get_register_address(peripheral, register)

            # Read 4 bytes from the register address
            result = session.bridge.command(
                f"-data-read-memory-bytes 0x{address:08X} 4"
            )
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from memory read"}

            memory = payload.get("memory", [])
            if not memory:
                return {"error": "No memory data returned"}

            hex_data = memory[0].get("contents", "")
            raw_bytes = bytes.fromhex(hex_data)
            raw_value = struct.unpack_from("<I", raw_bytes)[0]

            decoded = db.decode_register(peripheral, register, raw_value)
            return {
                "name": name,
                "peripheral": decoded.peripheral,
                "register": decoded.register,
                "address": f"0x{decoded.address:08X}",
                "raw": f"0x{decoded.raw_value:08X}",
                "fields": [
                    {
                        "name": f.name,
                        "value": f.value,
                        "bits": f.bit_range,
                        "description": f.description,
                        "access": f.access,
                    }
                    for f in decoded.fields
                ],
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def read_peripheral(name: str, peripheral: str) -> dict:
        """Read all registers of a peripheral with decoded bitfields.

        Performs a bulk memory read when registers are compact (< 4KB span),
        falling back to individual reads for sparse layouts.

        Args:
            name: Session name.
            peripheral: Peripheral name (e.g., "RCC", "GPIOB"). Case-insensitive.
        """
        try:
            session = manager.get(name)
            db = _ensure_svd(session)

            # Get peripheral info for address calculation
            p = db._get_peripheral(peripheral)
            if not p.registers:
                return {
                    "name": name,
                    "peripheral": p.name,
                    "base_address": f"0x{p.base_address:08X}",
                    "registers": [],
                }

            # Calculate span to decide bulk vs individual reads
            first_offset = p.registers[0].address_offset
            last_reg = p.registers[-1]
            last_end = last_reg.address_offset + (last_reg.size // 8)
            span = last_end - first_offset

            if span <= 4096:
                # Bulk read: single memory read for the whole block
                start_addr = p.base_address + first_offset
                result = session.bridge.command(
                    f"-data-read-memory-bytes 0x{start_addr:08X} {span}"
                )
                if result.is_error:
                    return {"error": result.error_msg}

                payload = result.payload
                if not isinstance(payload, dict):
                    return {"error": "Unexpected response from memory read"}

                memory = payload.get("memory", [])
                if not memory:
                    return {"error": "No memory data returned"}

                hex_data = memory[0].get("contents", "")
                bulk_bytes = bytes.fromhex(hex_data)

                registers = []
                for r in p.registers:
                    offset_in_buf = r.address_offset - first_offset
                    reg_size = r.size // 8
                    if offset_in_buf + reg_size > len(bulk_bytes):
                        continue
                    chunk = bulk_bytes[offset_in_buf: offset_in_buf + reg_size]
                    if reg_size == 4:
                        raw_value = struct.unpack_from("<I", chunk)[0]
                    elif reg_size == 2:
                        raw_value = struct.unpack_from("<H", chunk)[0]
                    else:
                        raw_value = chunk[0]

                    decoded = db.decode_register(peripheral, r.name, raw_value)
                    registers.append(_decoded_to_dict(decoded))
            else:
                # Sparse layout: individual reads per register
                registers = []
                for r in p.registers:
                    addr = p.base_address + r.address_offset
                    reg_size = r.size // 8
                    result = session.bridge.command(
                        f"-data-read-memory-bytes 0x{addr:08X} {reg_size}"
                    )
                    if result.is_error:
                        continue  # skip unreadable registers

                    payload = result.payload
                    if not isinstance(payload, dict):
                        continue

                    memory = payload.get("memory", [])
                    if not memory:
                        continue

                    hex_data = memory[0].get("contents", "")
                    raw_bytes = bytes.fromhex(hex_data)
                    if reg_size == 4:
                        raw_value = struct.unpack_from("<I", raw_bytes)[0]
                    elif reg_size == 2:
                        raw_value = struct.unpack_from("<H", raw_bytes)[0]
                    else:
                        raw_value = raw_bytes[0]

                    decoded = db.decode_register(peripheral, r.name, raw_value)
                    registers.append(_decoded_to_dict(decoded))

            return {
                "name": name,
                "peripheral": p.name,
                "base_address": f"0x{p.base_address:08X}",
                "registers": registers,
                "count": len(registers),
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}


def _decoded_to_dict(decoded) -> dict:
    """Convert a DecodedRegister to a serializable dict."""
    return {
        "register": decoded.register,
        "address": f"0x{decoded.address:08X}",
        "raw": f"0x{decoded.raw_value:08X}",
        "fields": [
            {
                "name": f.name,
                "value": f.value,
                "bits": f.bit_range,
            }
            for f in decoded.fields
        ],
    }
