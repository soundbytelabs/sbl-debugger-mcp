"""Tests for GDB/MI bridge types, response parsing, and MI traffic logging."""

import os
import tempfile

import pytest

from sbl_debugger.bridge.mi import MiLogger
from sbl_debugger.bridge.types import FrameInfo, StopEvent, MiResult


class TestFrameInfo:
    def test_from_mi_full(self):
        data = {
            "func": "main",
            "fullname": "/src/main.cpp",
            "file": "main.cpp",
            "line": "42",
            "addr": "0x08000150",
        }
        frame = FrameInfo.from_mi(data)
        assert frame.func == "main"
        assert frame.file == "/src/main.cpp"  # prefers fullname
        assert frame.line == 42
        assert frame.address == "0x08000150"

    def test_from_mi_minimal(self):
        frame = FrameInfo.from_mi({})
        assert frame.func == "??"
        assert frame.file is None
        assert frame.line is None
        assert frame.address is None

    def test_from_mi_falls_back_to_file(self):
        data = {"func": "foo", "file": "foo.c"}
        frame = FrameInfo.from_mi(data)
        assert frame.file == "foo.c"

    def test_to_dict(self):
        frame = FrameInfo(func="main", file="main.cpp", line=42, address="0x08000150")
        d = frame.to_dict()
        assert d == {
            "func": "main",
            "file": "main.cpp",
            "line": 42,
            "address": "0x08000150",
        }

    def test_to_dict_minimal(self):
        frame = FrameInfo(func="??")
        d = frame.to_dict()
        assert d == {"func": "??"}

    def test_frozen(self):
        frame = FrameInfo(func="main")
        with pytest.raises(AttributeError):
            frame.func = "other"


class TestStopEvent:
    def test_from_mi_breakpoint(self):
        payload = {
            "reason": "breakpoint-hit",
            "bkptno": "1",
            "frame": {
                "func": "main",
                "file": "main.cpp",
                "line": "42",
                "addr": "0x08000150",
            },
        }
        stop = StopEvent.from_mi(payload)
        assert stop.reason == "breakpoint-hit"
        assert stop.frame is not None
        assert stop.frame.func == "main"
        assert stop.frame.line == 42

    def test_from_mi_signal(self):
        payload = {"reason": "signal-received", "signal-name": "SIGTRAP"}
        stop = StopEvent.from_mi(payload)
        assert stop.reason == "signal-received"
        assert stop.frame is None

    def test_from_mi_no_reason(self):
        stop = StopEvent.from_mi({})
        assert stop.reason == "unknown"

    def test_to_dict(self):
        frame = FrameInfo(func="main", line=42)
        stop = StopEvent(reason="breakpoint-hit", frame=frame)
        d = stop.to_dict()
        assert d["reason"] == "breakpoint-hit"
        assert d["frame"]["func"] == "main"

    def test_to_dict_no_frame(self):
        stop = StopEvent(reason="exited")
        d = stop.to_dict()
        assert d == {"reason": "exited"}


class TestMiResult:
    def test_from_responses_done(self):
        responses = [
            {"type": "result", "message": "done", "payload": {"key": "val"}, "token": None, "stream": "stdout"},
        ]
        result = MiResult.from_responses(responses)
        assert result.message == "done"
        assert result.payload == {"key": "val"}
        assert not result.is_error

    def test_from_responses_error(self):
        responses = [
            {"type": "result", "message": "error", "payload": {"msg": "Something failed"}, "token": None, "stream": "stdout"},
        ]
        result = MiResult.from_responses(responses)
        assert result.is_error
        assert result.error_msg == "Something failed"

    def test_from_responses_mixed(self):
        responses = [
            {"type": "notify", "message": "thread-group-added", "payload": {"id": "i1"}, "token": None, "stream": "stdout"},
            {"type": "console", "message": None, "payload": "Reading symbols...\n", "token": None, "stream": "stdout"},
            {"type": "result", "message": "done", "payload": None, "token": None, "stream": "stdout"},
        ]
        result = MiResult.from_responses(responses)
        assert result.message == "done"
        assert len(result.console_output) == 1
        assert "Reading symbols" in result.console_output[0]
        assert len(result.events) == 1
        assert result.events[0]["message"] == "thread-group-added"

    def test_from_responses_empty(self):
        result = MiResult.from_responses([])
        assert result.message == "done"
        assert result.payload is None

    def test_error_msg_non_dict_payload(self):
        result = MiResult(message="error", payload="raw string")
        assert result.error_msg is None


class TestMiLogger:
    def test_creates_log_file_with_header(self, tmp_path):
        path = str(tmp_path / "test.log")
        logger = MiLogger(path)
        logger.close()

        content = open(path).read()
        assert content.startswith("# MI traffic log")

    def test_tx_logs_command(self, tmp_path):
        path = str(tmp_path / "test.log")
        logger = MiLogger(path)
        logger.tx("-exec-continue")
        logger.close()

        content = open(path).read()
        assert "TX -exec-continue" in content

    def test_rx_logs_responses(self, tmp_path):
        path = str(tmp_path / "test.log")
        logger = MiLogger(path)
        logger.rx([
            {"type": "result", "message": "done", "payload": {"key": "val"}},
            {"type": "notify", "message": "stopped", "payload": {"reason": "breakpoint-hit"}},
        ])
        logger.close()

        content = open(path).read()
        lines = content.strip().split("\n")
        # Header + 2 RX lines
        assert len(lines) == 3
        assert "RX result|done|" in lines[1]
        assert "RX notify|stopped|" in lines[2]

    def test_tx_rx_timestamps_are_monotonic(self, tmp_path):
        path = str(tmp_path / "test.log")
        logger = MiLogger(path)
        logger.tx("-exec-halt")
        logger.rx([{"type": "result", "message": "done", "payload": None}])
        logger.close()

        lines = open(path).read().strip().split("\n")
        # Extract timestamps from TX and RX lines (skip header)
        tx_time = float(lines[1].split("]")[0].strip("["))
        rx_time = float(lines[2].split("]")[0].strip("["))
        assert rx_time >= tx_time

    def test_empty_rx_no_output(self, tmp_path):
        """rx() with empty list writes nothing."""
        path = str(tmp_path / "test.log")
        logger = MiLogger(path)
        logger.rx([])
        logger.close()

        content = open(path).read()
        lines = content.strip().split("\n")
        assert len(lines) == 1  # Just the header

    def test_close_is_idempotent(self, tmp_path):
        path = str(tmp_path / "test.log")
        logger = MiLogger(path)
        logger.close()
        logger.close()  # Should not raise

    def test_mi_bridge_no_logger_by_default(self):
        """MiBridge has no logger when mi_log=False (default)."""
        from sbl_debugger.bridge.mi import MiBridge
        bridge = MiBridge()
        assert bridge._logger is None

    def test_mi_bridge_creates_logger(self):
        """MiBridge creates logger when mi_log=True."""
        from sbl_debugger.bridge.mi import MiBridge
        bridge = MiBridge(mi_log=True, session_name="test-session")
        try:
            assert bridge._logger is not None
            assert os.path.exists("/tmp/sbl-debugger-mi-test-session.log")
        finally:
            bridge._logger.close()
            os.unlink("/tmp/sbl-debugger-mi-test-session.log")
