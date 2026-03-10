"""Audio capture tools: freeze, read, and analyze AudioCapture ring buffers."""

from __future__ import annotations

import json
import struct
import subprocess
import time
from pathlib import Path

from sbl_debugger.session.manager import SessionManager


def register_tools(mcp, manager: SessionManager) -> None:
    """Register audio capture tools with the MCP server."""

    @mcp.tool()
    def audio_capture(
        name: str,
        symbol: str = "s_audio_capture",
        output: str = "/tmp/sbl_capture.bin",
        analyze: bool = True,
    ) -> dict:
        """Capture audio from an AudioCapture ring buffer.

        Freezes the buffer (non-halting — audio keeps playing), reads
        both channels, writes to a binary file, and optionally runs
        spectral analysis. The target is halted briefly for the bulk
        memory read, then resumed automatically.

        Works with sbl::debug::AudioCapture<N> ring buffer instances.

        Args:
            name: Session name.
            symbol: AudioCapture variable name (default: s_audio_capture).
            output: Output file path for the binary capture.
            analyze: If true, run sbl_audio_analyze.py and include results.
        """
        try:
            session = manager.get(name)

            # ── 1. Resolve addresses (need target halted for print_expr) ──
            # Check if target is running — if so, halt briefly for symbol lookup
            was_running = session.target_state.is_running
            if was_running:
                _halt_target(session)

            addrs = _resolve_capture_addrs(session, symbol)
            if "error" in addrs:
                if was_running:
                    _continue_target(session)
                return addrs

            # Resume before the non-halting freeze
            if was_running:
                _continue_target(session)
                time.sleep(0.05)  # Let a few audio blocks run

            # ── 2. Freeze via OpenOCD TCL (non-halting) ──
            frozen_addr = addrs["frozen_addr"]
            ok = session.openocd.write_memory_tcl(frozen_addr, b"\x01")
            if not ok:
                return {"error": "Failed to freeze capture via TCL"}

            time.sleep(0.01)  # One audio block to finish current write

            # ── 3. Read state via TCL (non-halting) ──
            state_bytes = session.openocd.read_memory_tcl(
                addrs["frozen_addr"], 8
            )
            if not state_bytes or len(state_bytes) < 8:
                # Fallback: halt and read via GDB
                _halt_target(session)
                ready = _read_field_gdb(session, f"{symbol}.ready_")
                pos = _read_field_gdb(session, f"{symbol}.pos_")
            else:
                ready = bool(state_bytes[1])  # ready_ at offset +1
                pos = struct.unpack_from("<H", state_bytes, 2)[0]  # pos_ at +2

            if not ready:
                # Unfreeze and bail
                session.openocd.write_memory_tcl(frozen_addr, b"\x00")
                return {
                    "error": "Buffer not ready — audio callback hasn't "
                    "filled the buffer yet. Wait a moment and retry."
                }

            # ── 4. Halt, read buffers, resume ──
            _halt_target(session)

            max_frames = addrs["max_frames"]
            buf_size = max_frames * 4  # float32

            left_data = session.openocd.read_memory_tcl(
                addrs["buf_left_addr"], buf_size, timeout=10.0
            )
            right_data = session.openocd.read_memory_tcl(
                addrs["buf_right_addr"], buf_size, timeout=10.0
            )

            if not left_data or not right_data:
                # Fallback to GDB memory read
                left_data = _read_memory_gdb(
                    session, addrs["buf_left_addr"], buf_size
                )
                right_data = _read_memory_gdb(
                    session, addrs["buf_right_addr"], buf_size
                )

            # ── 5. Unfreeze and resume ──
            session.openocd.write_memory_tcl(frozen_addr, b"\x00")
            _continue_target(session)

            if not left_data or not right_data:
                return {"error": "Failed to read capture buffers"}

            # ── 6. Unroll ring buffer and write file ──
            left_floats = _bytes_to_floats(left_data)
            right_floats = _bytes_to_floats(right_data)

            # Ring buffer unroll: [pos..end) then [0..pos) = chronological
            left_ordered = left_floats[pos:] + left_floats[:pos]
            right_ordered = right_floats[pos:] + right_floats[:pos]

            # Write binary: left then right (same layout as old format)
            out_path = Path(output)
            with open(out_path, "wb") as f:
                for sample in left_ordered:
                    f.write(struct.pack("<f", sample))
                for sample in right_ordered:
                    f.write(struct.pack("<f", sample))

            result = {
                "name": name,
                "symbol": symbol,
                "output": str(out_path),
                "max_frames": max_frames,
                "pos": pos,
                "file_size": out_path.stat().st_size,
            }

            # ── 7. Analyze ──
            if analyze:
                analysis = _run_analysis(out_path)
                if analysis:
                    result["analysis"] = analysis

            return result

        except (ValueError, RuntimeError) as e:
            # Try to unfreeze and resume on error
            try:
                session = manager.get(name)
                session.openocd.write_memory_tcl(frozen_addr, b"\x00")
                _continue_target(session)
            except Exception:
                pass
            return {"error": str(e)}


def _resolve_capture_addrs(session, symbol: str) -> dict:
    """Resolve AudioCapture field addresses via GDB print_expr."""
    try:
        # Get base address and field offsets
        base_result = session.bridge.command(
            f'-var-create - * "&{symbol}"'
        )
        if base_result.is_error:
            return {"error": f"Symbol '{symbol}' not found: {base_result.error_msg}"}

        base_val = base_result.payload.get("value", "")
        session.bridge.command("-var-delete -")

        # Parse base address
        base_addr = _parse_hex(base_val)
        if base_addr is None:
            return {"error": f"Could not parse address from: {base_val}"}

        # Get max_frames from the type
        mf_result = session.bridge.command(
            f'-var-create - * "{symbol}.max_frames"'
        )
        if mf_result.is_error:
            max_frames = 4096  # Default
        else:
            max_frames = int(mf_result.payload.get("value", "4096"))
            session.bridge.command("-var-delete -")

        # AudioCapture<N> memory layout:
        #   offset 0: frozen_ (bool, 1 byte)
        #   offset 1: ready_  (bool, 1 byte)
        #   offset 2: pos_    (uint16_t, 2 bytes)
        #   offset 4: written_ (uint32_t, 4 bytes)
        #   offset 8: buf_left_[MaxFrames]  (float array)
        #   offset 8 + MaxFrames*4: buf_right_[MaxFrames]
        return {
            "base_addr": base_addr,
            "frozen_addr": base_addr,
            "ready_addr": base_addr + 1,
            "pos_addr": base_addr + 2,
            "buf_left_addr": base_addr + 8,
            "buf_right_addr": base_addr + 8 + max_frames * 4,
            "max_frames": max_frames,
        }
    except (ValueError, RuntimeError) as e:
        return {"error": f"Address resolution failed: {e}"}


def _halt_target(session) -> None:
    """Halt the target via GDB, falling back to TCL."""
    result = session.bridge.command("-exec-interrupt", timeout=3.0)
    if result.is_error:
        session.openocd.tcl_command("halt")
    time.sleep(0.05)
    session.bridge.drain_events()


def _continue_target(session) -> None:
    """Resume target execution."""
    session.bridge.command("-exec-continue", timeout=3.0)
    session.bridge.drain_events()


def _read_field_gdb(session, expression: str):
    """Read a field value via GDB var-create."""
    result = session.bridge.command(f'-var-create - * "{expression}"')
    if result.is_error:
        return None
    val = result.payload.get("value", "")
    session.bridge.command("-var-delete -")
    if val == "true":
        return True
    if val == "false":
        return False
    try:
        return int(val)
    except ValueError:
        return val


def _read_memory_gdb(session, address: int, length: int) -> bytes | None:
    """Read memory via GDB as fallback."""
    result = session.bridge.command(
        f"-data-read-memory-bytes 0x{address:08x} {length}",
        timeout=30.0,
    )
    if result.is_error:
        return None
    payload = result.payload
    if not isinstance(payload, dict):
        return None
    memory = payload.get("memory", [])
    if not memory:
        return None
    hex_str = memory[0].get("contents", "")
    if not hex_str:
        return None
    return bytes.fromhex(hex_str)


def _parse_hex(text: str) -> int | None:
    """Extract a hex address from a GDB response like '0x20001edc <sym>'."""
    text = text.strip()
    for token in text.split():
        if token.startswith("0x") or token.startswith("0X"):
            try:
                return int(token, 16)
            except ValueError:
                continue
    return None


def _bytes_to_floats(data: bytes) -> list[float]:
    """Convert raw bytes to list of float32 values."""
    count = len(data) // 4
    return list(struct.unpack(f"<{count}f", data[: count * 4]))


def _run_analysis(capture_path: Path) -> dict | None:
    """Run sbl_audio_analyze.py on a capture file."""
    # Find the analysis script relative to the workspace
    # Try several locations
    candidates = [
        capture_path.parent / "../../tools/audio/sbl_audio_analyze.py",
        Path("/home/octo/projects/sound-byte-labs/tools/audio/sbl_audio_analyze.py"),
    ]

    script = None
    for c in candidates:
        resolved = c.resolve()
        if resolved.exists():
            script = resolved
            break

    if not script:
        return None

    try:
        result = subprocess.run(
            ["python3", str(script), str(capture_path), "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None
