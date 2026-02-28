"""Tests for execution control tools (mocked sessions)."""

from unittest.mock import MagicMock, patch

import pytest

from mcp.server.fastmcp import FastMCP

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import MiResult, StopEvent, FrameInfo
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.tools import execution as execution_tools
from sbl_debugger.tools.execution import _stop_from_result
from sbl_debugger.targets import get_profile


def _setup_tools():
    """Create MCP server and manager with execution tools registered."""
    mcp = FastMCP("test")
    mgr = SessionManager()
    execution_tools.register_tools(mcp, mgr)
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

class TestStopFromResult:
    def test_extracts_stop_event(self):
        result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "42", "addr": "0x08000150"},
                },
            }],
        )
        stop = _stop_from_result(result)
        assert stop is not None
        assert stop.reason == "end-stepping-range"
        assert stop.frame.func == "main"
        assert stop.frame.line == 42

    def test_returns_none_when_no_events(self):
        result = MiResult(message="running")
        assert _stop_from_result(result) is None

    def test_returns_none_when_no_stop_event(self):
        result = MiResult(
            message="running",
            events=[{"type": "notify", "message": "running", "payload": {"thread-id": "1"}}],
        )
        assert _stop_from_result(result) is None

    def test_ignores_non_dict_payload(self):
        result = MiResult(
            message="running",
            events=[{"type": "notify", "message": "stopped", "payload": "garbage"}],
        )
        assert _stop_from_result(result) is None


# -- Tool error handling --

class TestExecutionToolErrors:
    def test_halt_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["halt"].fn(name="nope")
        assert "error" in result

    def test_continue_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["continue_execution"].fn(name="nope")
        assert "error" in result

    def test_wait_for_halt_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["wait_for_halt"].fn(name="nope")
        assert "error" in result

    def test_step_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["step"].fn(name="nope")
        assert "error" in result

    def test_step_over_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["step_over"].fn(name="nope")
        assert "error" in result

    def test_step_out_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["step_out"].fn(name="nope")
        assert "error" in result

    def test_step_instruction_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["step_instruction"].fn(name="nope")
        assert "error" in result

    def test_run_to_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["run_to"].fn(name="nope", location="main")
        assert "error" in result

    def test_reset_nonexistent_session(self):
        _, _, tools = _setup_tools()
        result = tools["reset"].fn(name="nope")
        assert "error" in result


# -- Halt --

class TestHalt:
    def test_halt_returns_frame(self):
        _, mgr, tools = _setup_tools()
        session = _mock_attach(mgr)

        stop_event = StopEvent(
            reason="signal-received",
            frame=FrameInfo(func="main", line=10, address="0x08000100"),
        )
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done"),
        ), patch.object(
            MiBridge, "wait_for_stop",
            return_value=stop_event,
        ):
            result = tools["halt"].fn(name="daisy")

        assert result["state"] == "halted"
        assert result["reason"] == "signal-received"
        assert result["frame"]["func"] == "main"

    def test_halt_stop_in_result_events(self):
        """Stop event arrives in the same response batch."""
        _, mgr, tools = _setup_tools()
        session = _mock_attach(mgr)

        mi_result = MiResult(
            message="done",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "signal-received",
                    "frame": {"func": "delay_loop", "line": "5", "addr": "0x08000200"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result):
            result = tools["halt"].fn(name="daisy")

        assert result["state"] == "halted"
        assert result["frame"]["func"] == "delay_loop"

    def test_halt_no_stop_event(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="done"),
        ), patch.object(
            MiBridge, "wait_for_stop",
            return_value=None,
        ):
            result = tools["halt"].fn(name="daisy")

        assert result["state"] == "unknown"
        assert "warning" in result

    def test_halt_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "The program is not being run."}),
        ):
            result = tools["halt"].fn(name="daisy")

        assert "error" in result
        assert "not being run" in result["error"]


# -- Continue --

class TestContinueExecution:
    def test_continue_returns_running(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="running"),
        ):
            result = tools["continue_execution"].fn(name="daisy")

        assert result["state"] == "running"
        assert result["name"] == "daisy"

    def test_continue_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "The program is not being run."}),
        ):
            result = tools["continue_execution"].fn(name="daisy")

        assert "error" in result


# -- Wait for halt --

class TestWaitForHalt:
    def test_wait_returns_stop_event(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        stop = StopEvent(
            reason="breakpoint-hit",
            frame=FrameInfo(func="main", line=42, address="0x08000150"),
        )
        with patch.object(MiBridge, "wait_for_stop", return_value=stop):
            result = tools["wait_for_halt"].fn(name="daisy")

        assert result["state"] == "halted"
        assert result["reason"] == "breakpoint-hit"
        assert result["frame"]["func"] == "main"

    def test_wait_timeout(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(MiBridge, "wait_for_stop", return_value=None):
            result = tools["wait_for_halt"].fn(name="daisy", timeout=0.1)

        assert result["state"] == "running"
        assert result["timeout"] is True


# -- Step commands --

class TestStep:
    def test_step_returns_frame(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "43", "addr": "0x08000154"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            result = tools["step"].fn(name="daisy")

        mock_cmd.assert_called_once_with("-exec-step")
        assert result["state"] == "halted"
        assert result["frame"]["line"] == 43

    def test_step_with_count(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "50", "addr": "0x08000170"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            result = tools["step"].fn(name="daisy", count=5)

        mock_cmd.assert_called_once_with("-exec-step 5")
        assert result["state"] == "halted"
        assert result["frame"]["line"] == 50

    def test_step_count_1_sends_no_count(self):
        """count=1 should not append the count to the command."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "43", "addr": "0x08000154"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            tools["step"].fn(name="daisy", count=1)

        mock_cmd.assert_called_once_with("-exec-step")

    def test_step_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "Cannot execute"}),
        ):
            result = tools["step"].fn(name="daisy")

        assert "error" in result

    def test_step_waits_for_stop(self):
        """If stop event not in result batch, waits via wait_for_stop."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        stop = StopEvent(
            reason="end-stepping-range",
            frame=FrameInfo(func="init", line=10),
        )
        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="running"),
        ), patch.object(
            MiBridge, "wait_for_stop",
            return_value=stop,
        ):
            result = tools["step"].fn(name="daisy")

        assert result["state"] == "halted"
        assert result["frame"]["func"] == "init"


class TestStepOver:
    def test_step_over_sends_exec_next(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "44", "addr": "0x08000158"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            result = tools["step_over"].fn(name="daisy")

        mock_cmd.assert_called_once_with("-exec-next")
        assert result["state"] == "halted"

    def test_step_over_with_count(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "47", "addr": "0x08000164"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            result = tools["step_over"].fn(name="daisy", count=3)

        mock_cmd.assert_called_once_with("-exec-next 3")
        assert result["state"] == "halted"


class TestStepOut:
    def test_step_out_sends_exec_finish(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "function-finished",
                    "frame": {"func": "main", "line": "20", "addr": "0x08000120"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            result = tools["step_out"].fn(name="daisy")

        mock_cmd.assert_called_once_with("-exec-finish")
        assert result["state"] == "halted"
        assert result["reason"] == "function-finished"


class TestStepInstruction:
    def test_step_instruction_sends_exec_step_instruction(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "42", "addr": "0x08000152"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            result = tools["step_instruction"].fn(name="daisy")

        mock_cmd.assert_called_once_with("-exec-step-instruction")
        assert result["state"] == "halted"

    def test_step_instruction_with_count(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "42", "addr": "0x08000160"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result) as mock_cmd:
            result = tools["step_instruction"].fn(name="daisy", count=10)

        mock_cmd.assert_called_once_with("-exec-step-instruction 10")
        assert result["state"] == "halted"


# -- Run to --

class TestRunTo:
    def test_run_to_sets_temp_breakpoint_and_continues(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        bp_result = MiResult(
            message="done",
            payload={"bkpt": {"number": "1", "type": "breakpoint"}},
        )
        cont_result = MiResult(message="running")
        stop = StopEvent(
            reason="breakpoint-hit",
            frame=FrameInfo(func="target_func", line=100, address="0x08001000"),
        )

        call_count = [0]
        def mock_command(cmd, timeout=5.0):
            call_count[0] += 1
            if "-break-insert" in cmd:
                return bp_result
            elif "-exec-continue" in cmd:
                return cont_result
            return MiResult(message="done")

        with patch.object(MiBridge, "command", side_effect=mock_command), \
             patch.object(MiBridge, "wait_for_stop", return_value=stop):
            result = tools["run_to"].fn(name="daisy", location="target_func")

        assert result["state"] == "halted"
        assert result["reason"] == "breakpoint-hit"
        assert result["frame"]["func"] == "target_func"
        assert call_count[0] == 2  # break-insert + exec-continue

    def test_run_to_breakpoint_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "command",
            return_value=MiResult(message="error", payload={"msg": "No symbol table"}),
        ):
            result = tools["run_to"].fn(name="daisy", location="nonexistent")

        assert "error" in result

    def test_run_to_timeout(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        bp_result = MiResult(message="done", payload={"bkpt": {"number": "1"}})
        cont_result = MiResult(message="running")

        call_count = [0]
        def mock_command(cmd, timeout=5.0):
            call_count[0] += 1
            if "-break-insert" in cmd:
                return bp_result
            return cont_result

        with patch.object(MiBridge, "command", side_effect=mock_command), \
             patch.object(MiBridge, "wait_for_stop", return_value=None):
            result = tools["run_to"].fn(name="daisy", location="main")

        assert result["state"] == "running"
        assert "warning" in result


# -- Reset --

class TestReset:
    def test_reset_halt(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "monitor",
            return_value=MiResult(message="done"),
        ) as mock_mon, patch.object(
            MiBridge, "drain_events",
            return_value=[],
        ):
            result = tools["reset"].fn(name="daisy", halt=True)

        mock_mon.assert_called_once_with("reset halt")
        assert result["state"] == "halted"
        assert result["name"] == "daisy"

    def test_reset_run(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "monitor",
            return_value=MiResult(message="done"),
        ) as mock_mon:
            result = tools["reset"].fn(name="daisy", halt=False)

        mock_mon.assert_called_once_with("reset run")
        assert result["state"] == "running"

    def test_reset_halt_with_frame(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        events = [{
            "type": "notify",
            "message": "stopped",
            "payload": {
                "reason": "signal-received",
                "frame": {"func": "Reset_Handler", "line": "1", "addr": "0x08000000"},
            },
        }]
        with patch.object(
            MiBridge, "monitor",
            return_value=MiResult(message="done"),
        ), patch.object(
            MiBridge, "drain_events",
            return_value=events,
        ):
            result = tools["reset"].fn(name="daisy", halt=True)

        assert result["state"] == "halted"
        assert result["frame"]["func"] == "Reset_Handler"

    def test_reset_mi_error(self):
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        with patch.object(
            MiBridge, "monitor",
            return_value=MiResult(message="error", payload={"msg": "Target not responding"}),
        ):
            result = tools["reset"].fn(name="daisy")

        assert "error" in result


# -- Source context in responses --

class TestSourceContextInResponses:
    def test_step_includes_source_when_file_exists(self):
        """Step response includes source context when the source file exists."""
        import os
        import tempfile
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        # Create a temp source file
        content = "line1\nline2\nline3\nline4\nline5\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".c", delete=False) as f:
            f.write(content)
            src_path = f.name

        try:
            mi_result = MiResult(
                message="running",
                events=[{
                    "type": "notify",
                    "message": "stopped",
                    "payload": {
                        "reason": "end-stepping-range",
                        "frame": {"func": "main", "line": "3", "addr": "0x08000154",
                                  "fullname": src_path, "file": "test.c"},
                    },
                }],
            )
            with patch.object(MiBridge, "command", return_value=mi_result):
                result = tools["step"].fn(name="daisy")

            assert "source" in result
            assert any(e.get("current") for e in result["source"])
            current_line = [e for e in result["source"] if e.get("current")][0]
            assert current_line["text"] == "line3"
        finally:
            os.unlink(src_path)

    def test_step_no_source_when_file_missing(self):
        """Step response omits source when file doesn't exist on disk."""
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        mi_result = MiResult(
            message="running",
            events=[{
                "type": "notify",
                "message": "stopped",
                "payload": {
                    "reason": "end-stepping-range",
                    "frame": {"func": "main", "line": "3", "addr": "0x08000154",
                              "fullname": "/nonexistent/main.c", "file": "main.c"},
                },
            }],
        )
        with patch.object(MiBridge, "command", return_value=mi_result):
            result = tools["step"].fn(name="daisy")

        assert "source" not in result

    def test_halt_includes_source(self):
        """Halt response includes source context."""
        import os
        import tempfile
        _, mgr, tools = _setup_tools()
        _mock_attach(mgr)

        content = "void loop() {\n    update();\n}\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".c", delete=False) as f:
            f.write(content)
            src_path = f.name

        try:
            stop_event = StopEvent(
                reason="signal-received",
                frame=FrameInfo(func="loop", line=2, address="0x08000100", file=src_path),
            )
            with patch.object(
                MiBridge, "command",
                return_value=MiResult(message="done"),
            ), patch.object(
                MiBridge, "wait_for_stop",
                return_value=stop_event,
            ):
                result = tools["halt"].fn(name="daisy")

            assert "source" in result
        finally:
            os.unlink(src_path)


# -- Tool registration --

class TestExecutionToolRegistration:
    def test_all_tools_registered(self):
        mcp, _, tools = _setup_tools()
        expected = {
            "halt", "continue_execution", "wait_for_halt",
            "step", "step_over", "step_out", "step_instruction",
            "run_to", "reset",
        }
        assert expected.issubset(set(tools.keys()))
