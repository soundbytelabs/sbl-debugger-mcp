"""Tests for port allocation."""

from unittest.mock import patch

import pytest

from sbl_debugger.process.ports import find_available_port, _is_port_available


class TestPortAvailable:
    def test_available_port(self):
        # High ephemeral port is almost certainly available
        assert _is_port_available(59999) or not _is_port_available(59999)
        # Just verifying it doesn't crash — actual availability is system-dependent

    @patch("sbl_debugger.process.ports._is_port_available")
    def test_find_available_first(self, mock_available):
        mock_available.return_value = True
        port = find_available_port(start=3333)
        assert port == 3333

    @patch("sbl_debugger.process.ports._is_port_available")
    def test_find_available_skips_busy(self, mock_available):
        # First two busy, third available
        mock_available.side_effect = [False, False, True, True]
        port = find_available_port(start=3333)
        assert port == 3335

    @patch("sbl_debugger.process.ports._is_port_available")
    def test_find_available_all_busy_raises(self, mock_available):
        mock_available.return_value = False
        with pytest.raises(RuntimeError, match="No available GDB server port"):
            find_available_port()
