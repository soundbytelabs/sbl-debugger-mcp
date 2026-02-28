"""Tests for OpenOCD process management (mocked — no real OpenOCD)."""

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from sbl_debugger.process.openocd import OpenOcdProcess


def _make_mock_popen(stderr_lines=None, returncode=None):
    """Create a mock Popen that yields stderr lines."""
    mock = MagicMock()
    mock.pid = 12345

    if stderr_lines is None:
        stderr_lines = [
            b"Info : Listening on port 3333 for gdb connections\n",
        ]

    mock.stderr = iter(stderr_lines)
    mock.stdout = MagicMock()

    # poll() returns None (alive) unless returncode is set
    if returncode is not None:
        mock.poll.return_value = returncode
        mock.returncode = returncode
    else:
        mock.poll.return_value = None

    return mock


class TestOpenOcdProcess:
    @patch("sbl_debugger.process.openocd.shutil.which", return_value="/usr/bin/openocd")
    @patch("sbl_debugger.process.openocd.subprocess.Popen")
    def test_start_and_ready(self, mock_popen_cls, mock_which):
        mock_proc = _make_mock_popen()
        mock_popen_cls.return_value = mock_proc

        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg", gdb_port=3333)
        ocd.start(timeout=2.0)

        assert ocd.is_alive
        assert ocd.pid == 12345
        assert ocd.gdb_port == 3333

        # Verify OpenOCD was called with correct args
        call_args = mock_popen_cls.call_args[0][0]
        assert "openocd" in call_args[0]
        assert "interface/stlink.cfg" in call_args
        assert "target/stm32h7x.cfg" in call_args
        assert "gdb_port 3333" in call_args

        ocd.stop()

    @patch("sbl_debugger.process.openocd.shutil.which", return_value="/usr/bin/openocd")
    @patch("sbl_debugger.process.openocd.subprocess.Popen")
    def test_stop(self, mock_popen_cls, mock_which):
        mock_proc = _make_mock_popen()
        mock_popen_cls.return_value = mock_proc

        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg")
        ocd.start(timeout=2.0)
        ocd.stop()

        mock_proc.terminate.assert_called_once()
        assert not ocd.is_alive

    @patch("sbl_debugger.process.openocd.shutil.which", return_value="/usr/bin/openocd")
    @patch("sbl_debugger.process.openocd.subprocess.Popen")
    def test_stop_timeout_kills(self, mock_popen_cls, mock_which):
        mock_proc = _make_mock_popen()

        wait_calls = [0]
        def wait_side_effect(timeout=None):
            wait_calls[0] += 1
            if wait_calls[0] == 1:
                raise subprocess.TimeoutExpired("openocd", 5)
        mock_proc.wait.side_effect = wait_side_effect

        mock_popen_cls.return_value = mock_proc

        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg")
        ocd.start(timeout=2.0)
        ocd.stop()

        mock_proc.kill.assert_called_once()

    @patch("sbl_debugger.process.openocd.shutil.which", return_value=None)
    def test_start_openocd_not_found(self, mock_which):
        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg")
        with pytest.raises(RuntimeError, match="openocd not found"):
            ocd.start()

    @patch("sbl_debugger.process.openocd.shutil.which", return_value="/usr/bin/openocd")
    @patch("sbl_debugger.process.openocd.subprocess.Popen")
    def test_start_process_dies(self, mock_popen_cls, mock_which):
        mock_proc = _make_mock_popen(
            stderr_lines=[b"Error: unable to find CMSIS-DAP device\n"],
            returncode=1,
        )
        mock_popen_cls.return_value = mock_proc

        ocd = OpenOcdProcess("cmsis-dap.cfg", "rp2040.cfg")
        with pytest.raises(RuntimeError, match="OpenOCD exited with code 1"):
            ocd.start(timeout=1.0)

    @patch("sbl_debugger.process.openocd.shutil.which", return_value="/usr/bin/openocd")
    @patch("sbl_debugger.process.openocd.subprocess.Popen")
    def test_double_start_raises(self, mock_popen_cls, mock_which):
        mock_proc = _make_mock_popen()
        mock_popen_cls.return_value = mock_proc

        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg")
        ocd.start(timeout=2.0)

        with pytest.raises(RuntimeError, match="already running"):
            ocd.start()

        ocd.stop()

    @patch("sbl_debugger.process.openocd.shutil.which", return_value="/usr/bin/openocd")
    @patch("sbl_debugger.process.openocd.subprocess.Popen")
    def test_stderr_captured(self, mock_popen_cls, mock_which):
        mock_proc = _make_mock_popen(stderr_lines=[
            b"Info : STLINK V3 detected\n",
            b"Info : Target voltage: 3.3V\n",
            b"Info : Listening on port 3333 for gdb connections\n",
        ])
        mock_popen_cls.return_value = mock_proc

        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg")
        ocd.start(timeout=2.0)

        # Give stderr reader thread time
        time.sleep(0.1)

        lines = ocd.stderr_output
        assert any("STLINK V3" in l for l in lines)
        assert any("Listening on port" in l for l in lines)
        ocd.stop()

    def test_stop_when_not_started(self):
        """stop() is safe to call when not started."""
        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg")
        ocd.stop()  # Should not raise

    @patch("sbl_debugger.process.openocd.shutil.which", return_value="/usr/bin/openocd")
    @patch("sbl_debugger.process.openocd.subprocess.Popen")
    def test_disables_telnet_and_tcl(self, mock_popen_cls, mock_which):
        mock_proc = _make_mock_popen()
        mock_popen_cls.return_value = mock_proc

        ocd = OpenOcdProcess("stlink.cfg", "stm32h7x.cfg")
        ocd.start(timeout=2.0)

        call_args = mock_popen_cls.call_args[0][0]
        assert "telnet_port disabled" in call_args
        assert "tcl_port disabled" in call_args
        ocd.stop()
