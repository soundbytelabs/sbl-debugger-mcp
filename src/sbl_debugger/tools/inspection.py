"""Inspection tools: registers, memory, backtrace, locals, expressions, disassembly."""

from __future__ import annotations

import os
import struct

from sbl_debugger.bridge.types import FrameInfo, MiResult
from sbl_debugger.session.manager import SessionManager

# Core registers returned by debug_snapshot (not all 50+ GDB regs)
CORE_REGISTERS = ["r0", "r1", "r2", "r3", "r12", "sp", "lr", "pc", "xpsr"]


def read_source_context(
    file: str | None,
    line: int | None,
    context: int = 2,
) -> list[dict] | None:
    """Read source lines around a location.

    Returns a list of dicts with 'line', 'text', and optionally 'current': True
    for the active line. Returns None if file is unavailable or line is unknown.
    """
    if not file or not line:
        return None
    if not os.path.isfile(file):
        return None

    try:
        with open(file, "r", errors="replace") as f:
            all_lines = f.readlines()
    except OSError:
        return None

    total = len(all_lines)
    start = max(1, line - context)
    end = min(total, line + context)

    result = []
    for i in range(start, end + 1):
        entry: dict = {"line": i, "text": all_lines[i - 1].rstrip("\n\r")}
        if i == line:
            entry["current"] = True
        result.append(entry)
    return result


def _parse_hex_string(hex_str: str) -> bytes:
    """Convert a hex string (with or without 0x prefix) to bytes."""
    clean = hex_str.replace("0x", "").replace(" ", "")
    return bytes.fromhex(clean)


def _format_memory(raw_bytes: bytes, fmt: str) -> list | str:
    """Format raw memory bytes into the requested representation."""
    if fmt == "hex":
        return raw_bytes.hex()
    elif fmt == "u8":
        return list(raw_bytes)
    elif fmt == "u16":
        # Little-endian 16-bit words
        count = len(raw_bytes) // 2
        return list(struct.unpack(f"<{count}H", raw_bytes[: count * 2]))
    elif fmt == "u32":
        # Little-endian 32-bit words
        count = len(raw_bytes) // 4
        return list(struct.unpack(f"<{count}I", raw_bytes[: count * 4]))
    else:
        return raw_bytes.hex()


def register_tools(mcp, manager: SessionManager) -> None:
    """Register inspection tools with the MCP server."""

    @mcp.tool()
    def read_registers(
        name: str,
        registers: list[str] | None = None,
    ) -> dict:
        """Read CPU registers.

        Returns core register values. Optionally filter to specific
        registers by name (e.g., ["r0", "sp", "pc"]).

        Args:
            name: Session name.
            registers: Optional list of register names to read.
        """
        try:
            session = manager.get(name)

            # Get register names (indexed list, empty strings for unnamed)
            names_result = session.bridge.command("-data-list-register-names")
            if names_result.is_error:
                return {"error": names_result.error_msg}

            payload = names_result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from register names query"}
            all_names = payload.get("register-names", [])

            # Build index→name map for named registers
            named = {i: n for i, n in enumerate(all_names) if n}

            # Determine which indices to read
            if registers:
                # Filter to requested register names
                name_set = set(registers)
                indices = [i for i, n in named.items() if n in name_set]
                if not indices:
                    return {"error": f"No matching registers found for: {registers}"}
            else:
                indices = list(named.keys())

            # Get values for selected indices
            idx_str = " ".join(str(i) for i in indices)
            values_result = session.bridge.command(
                f"-data-list-register-values x {idx_str}"
            )
            if values_result.is_error:
                return {"error": values_result.error_msg}

            vpayload = values_result.payload
            if not isinstance(vpayload, dict):
                return {"error": "Unexpected response from register values query"}

            # Build name→value dict
            reg_values = {}
            for entry in vpayload.get("register-values", []):
                num = int(entry["number"])
                reg_name = named.get(num, f"reg{num}")
                reg_values[reg_name] = entry["value"]

            return {"name": name, "registers": reg_values}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def write_register(name: str, register: str, value: str) -> dict:
        """Write a single CPU register.

        Args:
            name: Session name.
            register: Register name (e.g., "r0", "sp", "pc").
            value: Value to write (hex or decimal).
        """
        try:
            session = manager.get(name)
            escaped = f"set ${register} = {value}"
            result = session.bridge.command(
                f'-interpreter-exec console "{escaped}"'
            )
            if result.is_error:
                return {"error": result.error_msg}
            return {"name": name, "register": register, "value": value}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def read_memory(
        name: str,
        address: str,
        length: int,
        format: str = "hex",
    ) -> dict:
        """Read target memory.

        Args:
            name: Session name.
            address: Start address (hex string like "0x20000000" or symbol).
            length: Number of bytes to read.
            format: Output format — "hex", "u8", "u16", or "u32".
        """
        try:
            session = manager.get(name)
            result = session.bridge.command(
                f"-data-read-memory-bytes {address} {length}"
            )
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from memory read"}

            memory = payload.get("memory", [])
            if not memory:
                return {"error": "No memory data returned"}

            # Concatenate all memory regions
            hex_data = "".join(region.get("contents", "") for region in memory)
            raw_bytes = _parse_hex_string(hex_data)

            return {
                "name": name,
                "address": address,
                "length": len(raw_bytes),
                "data": _format_memory(raw_bytes, format),
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def write_memory(name: str, address: str, data: str) -> dict:
        """Write to target memory.

        Args:
            name: Session name.
            address: Start address (hex string like "0x20000000").
            data: Hex string of bytes to write (e.g., "deadbeef").
        """
        try:
            session = manager.get(name)
            # Clean up the hex data (remove spaces, 0x prefix)
            clean = data.replace("0x", "").replace(" ", "")
            result = session.bridge.command(
                f"-data-write-memory-bytes {address} {clean}"
            )
            if result.is_error:
                return {"error": result.error_msg}
            return {
                "name": name,
                "address": address,
                "bytes_written": len(clean) // 2,
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def backtrace(name: str, max_frames: int = 20) -> dict:
        """Get the call stack (backtrace).

        Args:
            name: Session name.
            max_frames: Maximum number of stack frames to return. Default 20.
        """
        try:
            session = manager.get(name)
            result = session.bridge.command(
                f"-stack-list-frames 0 {max_frames - 1}"
            )
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from backtrace"}

            stack = payload.get("stack", [])
            frames = []
            for entry in stack:
                # MI wraps each frame: {"frame": {level, addr, func, ...}}
                frame_data = entry.get("frame", entry) if isinstance(entry, dict) else entry
                frame = FrameInfo.from_mi(frame_data)
                level = frame_data.get("level", str(len(frames)))
                frame_dict = {"level": int(level), **frame.to_dict()}
                frames.append(frame_dict)

            return {"name": name, "frames": frames, "depth": len(frames)}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def read_locals(name: str) -> dict:
        """List local variables in the current stack frame.

        Args:
            name: Session name.
        """
        try:
            session = manager.get(name)
            result = session.bridge.command(
                "-stack-list-variables --all-values"
            )
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from locals query"}

            variables = []
            for var in payload.get("variables", []):
                variables.append({
                    "name": var.get("name", "?"),
                    "value": var.get("value", "?"),
                })

            return {"name": name, "variables": variables}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def print_expr(name: str, expression: str) -> dict:
        """Evaluate a C/C++ expression in the target context.

        Can read variables, dereference pointers, access struct fields, etc.

        Args:
            name: Session name.
            expression: C expression to evaluate (e.g., "my_var", "*ptr", "arr[3]").
        """
        try:
            session = manager.get(name)
            # Escape quotes in the expression
            escaped = expression.replace('"', '\\"')
            result = session.bridge.command(
                f'-data-evaluate-expression "{escaped}"'
            )
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from expression evaluation"}

            return {
                "name": name,
                "expression": expression,
                "value": payload.get("value", "?"),
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def disassemble(
        name: str,
        address: str | None = None,
        count: int = 10,
    ) -> dict:
        """Disassemble instructions at an address (or the current PC).

        Args:
            name: Session name.
            address: Start address or symbol. Defaults to current PC.
            count: Number of instructions to disassemble. Default 10.
        """
        try:
            session = manager.get(name)
            start = address if address else "$pc"
            # Estimate bytes: Thumb-2 instructions are 2-4 bytes, use 4*count
            byte_range = count * 4
            result = session.bridge.command(
                f"-data-disassemble -s {start} -e {start}+{byte_range} -- 0"
            )
            if result.is_error:
                return {"error": result.error_msg}

            payload = result.payload
            if not isinstance(payload, dict):
                return {"error": "Unexpected response from disassemble"}

            instructions = []
            for insn in payload.get("asm_insns", []):
                instructions.append({
                    "address": insn.get("address", "?"),
                    "func": insn.get("func-name", "?"),
                    "offset": insn.get("offset", "?"),
                    "inst": insn.get("inst", "?"),
                })

            return {
                "name": name,
                "start": start,
                "instructions": instructions[:count],
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
