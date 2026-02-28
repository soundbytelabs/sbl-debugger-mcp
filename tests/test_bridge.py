"""Tests for GDB/MI bridge types and response parsing."""

import pytest

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
