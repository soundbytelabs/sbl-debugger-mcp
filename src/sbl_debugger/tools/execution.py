"""Execution control tools: halt, continue, step, reset."""

from __future__ import annotations

import time

from pygdbmi.constants import GdbTimeoutError

from sbl_debugger.bridge.types import ConnectionLostError, FrameInfo, MiResult, StopEvent
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.session.session import DebugSession
from sbl_debugger.tools.inspection import read_source_context


def _stop_from_result(result: MiResult) -> StopEvent | None:
    """Extract a stop event from MI result events, if present."""
    for e in result.events:
        if e.get("message") == "stopped":
            payload = e.get("payload", {})
            if isinstance(payload, dict):
                return StopEvent.from_mi(payload)
    return None


def _add_source(result: dict, stop: StopEvent | None) -> None:
    """Add source context to a result dict if frame has file/line info."""
    if stop and stop.frame:
        source = read_source_context(stop.frame.file, stop.frame.line)
        if source:
            result["source"] = source


def _resync_gdb(session: DebugSession) -> bool:
    """Resynchronize GDB after an OpenOCD-level halt.

    Uses 'monitor halt' through GDB so GDB sees the halt and updates
    its internal state. Unlike TCL halt (which bypasses GDB entirely)
    or -exec-interrupt (which is a no-op if GDB doesn't think the
    target is running), 'monitor halt' goes GDB -> OpenOCD -> SWD
    and GDB processes the response.

    Polls with increasing delays (50, 100, 200, 300, 400ms — ~1.5s total)
    to give GDB time to process async events.
    """
    try:
        session.bridge.monitor("halt", timeout=3.0)
    except Exception:
        return False

    # Poll with increasing delays: 50, 100, 200, 300, 400ms (~1.5s total)
    for attempt in range(5):
        time.sleep(0.05 * (attempt + 1))
        try:
            session.bridge.drain_events()
            result = session.bridge.command("-thread-info", timeout=2.0)
            if result.is_error:
                continue
            payload = result.payload
            if isinstance(payload, dict):
                threads = payload.get("threads", [])
                for t in threads:
                    if t.get("state") == "stopped":
                        return True
        except Exception:
            continue
    return False


def _query_current_frame(session: DebugSession) -> StopEvent | None:
    """Query GDB for current frame when no async stop event available."""
    try:
        result = session.bridge.command("-stack-info-frame", timeout=2.0)
        if result.is_error:
            return None
        payload = result.payload
        if isinstance(payload, dict):
            frame_data = payload.get("frame", {})
            if frame_data:
                frame = FrameInfo.from_mi(frame_data)
                return StopEvent(reason="signal-received", frame=frame)
    except Exception:
        pass
    return None


def _reconnect_gdb(session: DebugSession) -> bool:
    """Reconnect GDB to a surviving OpenOCD after connection loss.

    When OpenOCD loses and re-establishes its SWD connection (e.g., during
    DMA/SAI peripheral reconfiguration), GDB's connection to OpenOCD drops.
    OpenOCD stays alive — we can disconnect GDB and reconnect to force a
    clean state rebuild.

    This is the same pattern used in load() after flashing.
    """
    if not session.openocd.is_alive:
        return False

    try:
        # Disconnect GDB from the dead connection
        try:
            session.bridge.disconnect()
        except Exception:
            pass  # Already disconnected

        # Let OpenOCD re-establish SWD
        time.sleep(0.2)

        # Reconnect GDB to the surviving OpenOCD
        result = session.bridge.connect(port=session.openocd.gdb_port)
        if result.is_error:
            return False

        # Reload symbols so GDB has debug context
        if session.elf_path:
            session.bridge.load_symbols(session.elf_path)

        # Drain stale events and update state
        session.bridge.drain_events()
        session.target_state.set_halted()
        return True
    except Exception:
        return False


def _step_command(
    manager: SessionManager, name: str, mi_cmd: str, count: int = 1
) -> dict:
    """Common logic for step-like commands.

    For count > 1, loops single steps to avoid GDB's unreliable multi-step.
    Each step waits up to 10s for the target to stop. Aborts early on
    breakpoint hits or unexpected stop reasons.
    """
    try:
        session = manager.get(name)
        last_stop = None

        for i in range(count):
            # Always send single-step commands
            result = session.bridge.command(mi_cmd)
            if result.is_error:
                return {"error": result.error_msg}

            # Step commands usually produce *stopped in the same response batch
            stop = _stop_from_result(result)
            if stop is None:
                # Didn't arrive yet — wait with increased timeout
                stop = session.bridge.wait_for_stop(timeout=10.0)

            if stop is None:
                session.target_state.set_running()
                response: dict = {"name": name, "state": "running"}
                if i > 0:
                    response["completed_steps"] = i
                return response

            last_stop = stop
            session.target_state.set_halted(stop)

            # Abort early on breakpoint hit or non-step stop reason
            if stop.reason not in ("end-stepping-range",):
                break

        result_dict: dict = {
            "name": name,
            "state": "halted",
            **last_stop.to_dict(),
        }
        if count > 1:
            result_dict["completed_steps"] = i + 1
        _add_source(result_dict, last_stop)
        return result_dict
    except ConnectionLostError:
        # SWD connection dropped during a step (e.g., stepping over
        # DMA/SAI peripheral init). Reconnect and report where we landed.
        if _reconnect_gdb(session):
            stop = _query_current_frame(session)
            if stop:
                session.target_state.set_halted(stop)
                result_dict = {
                    "name": name,
                    "state": "halted",
                    "recovered": True,
                    **stop.to_dict(),
                }
                if count > 1:
                    result_dict["completed_steps"] = i
                _add_source(result_dict, stop)
                return result_dict
            return {
                "name": name,
                "state": "halted",
                "recovered": True,
                "warning": "Reconnected after connection loss but could not read frame",
            }
        return {"error": "Lost connection to target during step. Detach and reattach."}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}


def _tcl_resume_fallback(session: DebugSession, name: str) -> dict:
    """Resume the target via OpenOCD TCL when GDB's -exec-continue fails.

    Mirrors the halt() function's TCL fallback pattern. Sends 'resume'
    directly via OpenOCD's TCL port, bypassing GDB. Then notifies GDB
    via 'monitor resume' so it updates its internal state.

    This handles the common case where GDB is desynchronized after a
    TCL halt or a load/reconnect, and -exec-continue either errors or
    doesn't actually resume the target.
    """
    try:
        session.openocd.tcl_command("resume")
    except RuntimeError:
        return {
            "name": name,
            "state": "unknown",
            "error": "GDB continue failed and OpenOCD TCL resume also failed",
        }

    # Target is now running (OpenOCD confirmed via SWD).
    session.target_state.set_running()

    # Try to notify GDB so it knows the target is running.
    # This is best-effort — the target IS running regardless.
    try:
        session.bridge.monitor("resume", timeout=1.0)
    except Exception:
        pass

    # Drain any async events GDB emits after the monitor command
    try:
        time.sleep(0.05)
        session.bridge.drain_events()
    except Exception:
        pass

    return {"name": name, "state": "running", "method": "openocd_tcl"}


def register_tools(mcp, manager: SessionManager) -> None:
    """Register execution control tools with the MCP server."""

    @mcp.tool()
    def halt(name: str) -> dict:
        """Halt execution on a running target.

        Sends an interrupt and waits for the target to stop.
        If GDB's interrupt doesn't work (e.g., stuck in ISR context),
        falls back to OpenOCD's SWD-level halt.

        Args:
            name: Session name.
        """
        try:
            session = manager.get(name)

            # First attempt: GDB -exec-interrupt
            # pygdbmi raises GdbTimeoutError (a ValueError subclass) which
            # we catch specifically so we can still try the TCL fallback.
            try:
                result = session.bridge.command("-exec-interrupt")
                if result.is_error:
                    pass  # GDB responded but with error — try fallback
                else:
                    stop = _stop_from_result(result)
                    if stop is None:
                        stop = session.bridge.wait_for_stop(timeout=3.0)
                    if stop is not None:
                        session.target_state.set_halted(stop)
                        response = {"name": name, "state": "halted", **stop.to_dict()}
                        _add_source(response, stop)
                        return response
            except GdbTimeoutError:
                # GDB is unresponsive — fall through to TCL fallback.
                pass

            # Fallback: halt via OpenOCD TCL port (SWD-level, bypasses GDB)
            try:
                session.openocd.tcl_command("halt")
            except RuntimeError:
                return {
                    "name": name,
                    "state": "unknown",
                    "warning": "GDB interrupt failed and OpenOCD TCL halt also failed",
                }

            # Target IS halted (OpenOCD confirmed via SWD)
            session.target_state.set_halted()

            # Try to resync GDB so subsequent commands work
            resynced = _resync_gdb(session)

            # Try to get frame info
            stop = session.bridge.wait_for_stop(timeout=0.5)
            if stop is None and resynced:
                stop = _query_current_frame(session)

            if stop is not None:
                session.target_state.set_halted(stop)
                response = {
                    "name": name,
                    "state": "halted",
                    "method": "openocd_tcl",
                    **stop.to_dict(),
                }
                _add_source(response, stop)
                return response

            return {
                "name": name,
                "state": "halted",
                "method": "openocd_tcl",
                "warning": "Target halted via OpenOCD but GDB did not report stop event. GDB may be desynchronized.",
            }
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def continue_execution(name: str) -> dict:
        """Resume execution on a halted target.

        Returns immediately — use wait_for_halt or debug_status to
        check when/if the target stops.

        Args:
            name: Session name.
        """
        try:
            session = manager.get(name)

            # If halted via TCL (no last_stop), resync GDB first
            if session.target_state.is_halted and session.target_state.last_stop is None:
                _resync_gdb(session)

            result = session.bridge.command("-exec-continue")
            if result.is_error:
                # GDB refused to continue — try resync + retry
                if _resync_gdb(session):
                    result = session.bridge.command("-exec-continue")
                    if result.is_error:
                        # GDB still won't continue — fall back to TCL resume
                        return _tcl_resume_fallback(session, name)
                else:
                    return _tcl_resume_fallback(session, name)

            # Check if target resumed:
            # 1. GDB returns ^running as the result message
            # 2. *running notify event in the same response batch
            # 3. Drain any additional events after a brief delay
            running = result.message == "running" or any(
                e.get("message") == "running" for e in result.events
            )

            if not running:
                # Events might arrive slightly after the result
                time.sleep(0.05)
                events = session.bridge.drain_events()
                running = any(
                    e.get("message") == "running" for e in events
                )

            if not running:
                # Last resort: check thread state directly
                try:
                    ti = session.bridge.command("-thread-info", timeout=1.0)
                    if not ti.is_error and isinstance(ti.payload, dict):
                        threads = ti.payload.get("threads", [])
                        running = any(
                            t.get("state") == "running" for t in threads
                        )
                except Exception:
                    pass

            if running:
                session.target_state.set_running()
                return {"name": name, "state": "running"}

            # GDB thinks target didn't resume — verify via OpenOCD TCL
            # and force resume if needed (mirrors halt's TCL fallback)
            return _tcl_resume_fallback(session, name)
        except ConnectionLostError:
            session = manager.get(name)
            if _reconnect_gdb(session):
                return _tcl_resume_fallback(session, name)
            return {"error": "Lost connection to target. Detach and reattach."}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def wait_for_halt(name: str, timeout: float = 30.0) -> dict:
        """Block until the target halts (e.g., hits a breakpoint).

        Use after continue_execution to wait for the target to stop.

        Args:
            name: Session name.
            timeout: Max seconds to wait. Default 30.
        """
        try:
            session = manager.get(name)
            stop = session.bridge.wait_for_stop(timeout=timeout)
            if stop is None:
                return {"name": name, "state": "running", "timeout": True}
            session.target_state.set_halted(stop)
            response = {"name": name, "state": "halted", **stop.to_dict()}
            _add_source(response, stop)
            return response
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def step(name: str, count: int = 1) -> dict:
        """Step one or more source lines (into functions).

        Args:
            name: Session name.
            count: Number of lines to step. Default 1.
        """
        return _step_command(manager, name, "-exec-step", count)

    @mcp.tool()
    def step_over(name: str, count: int = 1) -> dict:
        """Step one or more source lines (over function calls).

        Args:
            name: Session name.
            count: Number of lines to step. Default 1.
        """
        return _step_command(manager, name, "-exec-next", count)

    @mcp.tool()
    def step_out(name: str) -> dict:
        """Step out of the current function.

        Resumes execution until the current function returns.

        Args:
            name: Session name.
        """
        return _step_command(manager, name, "-exec-finish")

    @mcp.tool()
    def step_instruction(name: str, count: int = 1) -> dict:
        """Step one or more machine instructions.

        Args:
            name: Session name.
            count: Number of instructions to step. Default 1.
        """
        return _step_command(manager, name, "-exec-step-instruction", count)

    @mcp.tool()
    def run_to(name: str, location: str) -> dict:
        """Run to a specific location (function name, file:line, or address).

        Sets a temporary breakpoint and continues execution.

        Args:
            name: Session name.
            location: Where to stop — function name, file:line, or *address.
        """
        try:
            session = manager.get(name)

            # Insert a temporary breakpoint
            bp_result = session.bridge.command(f"-break-insert -t {location}")
            if bp_result.is_error:
                return {"error": bp_result.error_msg}

            # Continue execution
            cont_result = session.bridge.command("-exec-continue")
            if cont_result.is_error:
                return {"error": cont_result.error_msg}
            session.target_state.set_running()

            # Wait for the target to hit the temp breakpoint
            stop = session.bridge.wait_for_stop(timeout=30.0)
            if stop is None:
                return {
                    "name": name,
                    "state": "running",
                    "warning": "Target did not reach location within timeout",
                }

            session.target_state.set_halted(stop)
            response = {"name": name, "state": "halted", **stop.to_dict()}
            _add_source(response, stop)
            return response
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def reset(name: str, halt: bool = True) -> dict:
        """Reset the target.

        Args:
            name: Session name.
            halt: If true (default), halt after reset. If false, run immediately.
        """
        try:
            session = manager.get(name)
            cmd = "reset halt" if halt else "reset run"
            result = session.bridge.monitor(cmd)
            if result.is_error:
                return {"error": result.error_msg}

            state = "halted" if halt else "running"
            response: dict = {"name": name, "state": state}

            if halt:
                session.target_state.set_halted()
                events = session.bridge.drain_events()
                for e in events:
                    if e.get("message") == "stopped":
                        payload = e.get("payload", {})
                        if isinstance(payload, dict):
                            stop = StopEvent.from_mi(payload)
                            session.target_state.set_halted(stop)
                            if stop.frame:
                                response["frame"] = stop.frame.to_dict()
                            _add_source(response, stop)
            else:
                session.target_state.set_running()

            return response
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
