"""Tests for session manager (mocked OpenOCD + GDB)."""

from unittest.mock import MagicMock, patch

import pytest

from sbl_debugger.bridge.mi import MiBridge
from sbl_debugger.bridge.types import MiResult
from sbl_debugger.process.openocd import OpenOcdProcess
from sbl_debugger.session.manager import SessionManager
from sbl_debugger.session.session import DebugSession
from sbl_debugger.targets import TargetProfile, get_profile


def _mock_attach(manager, name="daisy", elf=None):
    """Attach with fully mocked OpenOCD + GDB."""
    profile = get_profile(name)

    with patch.object(OpenOcdProcess, "start"), \
         patch.object(OpenOcdProcess, "is_alive", new_callable=lambda: property(lambda self: True)), \
         patch.object(MiBridge, "start"), \
         patch.object(MiBridge, "connect", return_value=MiResult(message="connected")), \
         patch.object(MiBridge, "load_symbols", return_value=MiResult(message="done")), \
         patch.object(MiBridge, "is_connected", new_callable=lambda: property(lambda self: True)):
        return manager.attach(
            target_profile=profile,
            target_name=name,
            elf_path=elf,
        )


class TestDebugSession:
    def test_to_dict(self):
        openocd = MagicMock(spec=OpenOcdProcess)
        openocd.is_alive = True
        openocd.gdb_port = 3333
        bridge = MagicMock(spec=MiBridge)
        bridge.is_connected = True

        session = DebugSession(
            name="test",
            target="daisy",
            openocd=openocd,
            bridge=bridge,
            elf_path="/path/to/elf",
        )

        d = session.to_dict()
        assert d["name"] == "test"
        assert d["target"] == "daisy"
        assert d["gdb_port"] == 3333
        assert d["elf"] == "/path/to/elf"
        assert d["alive"] is True
        assert "uptime_seconds" in d

    def test_shutdown(self):
        openocd = MagicMock(spec=OpenOcdProcess)
        bridge = MagicMock(spec=MiBridge)

        session = DebugSession(
            name="test",
            target="daisy",
            openocd=openocd,
            bridge=bridge,
        )
        session.shutdown()

        bridge.stop.assert_called_once()
        openocd.stop.assert_called_once()


class TestSessionManager:
    def test_attach_and_get(self):
        mgr = SessionManager()
        session = _mock_attach(mgr, "daisy")

        assert session.name == "daisy"
        assert session.target == "daisy"

        retrieved = mgr.get("daisy")
        assert retrieved is session

    def test_attach_with_custom_name(self):
        mgr = SessionManager()
        profile = get_profile("daisy")

        with patch.object(OpenOcdProcess, "start"), \
             patch.object(OpenOcdProcess, "is_alive", new_callable=lambda: property(lambda self: True)), \
             patch.object(MiBridge, "start"), \
             patch.object(MiBridge, "connect", return_value=MiResult(message="connected")), \
             patch.object(MiBridge, "is_connected", new_callable=lambda: property(lambda self: True)):
            session = mgr.attach(
                target_profile=profile,
                target_name="daisy",
                name="my-daisy",
            )

        assert session.name == "my-daisy"
        assert mgr.get("my-daisy") is session

    def test_attach_with_elf(self):
        mgr = SessionManager()
        session = _mock_attach(mgr, "daisy", elf="/path/to/firmware.elf")
        assert session.elf_path == "/path/to/firmware.elf"

    def test_duplicate_name_raises(self):
        mgr = SessionManager()
        _mock_attach(mgr, "daisy")

        with pytest.raises(ValueError, match="already exists"):
            _mock_attach(mgr, "daisy")

    def test_detach(self):
        mgr = SessionManager()
        session = _mock_attach(mgr, "daisy")

        # Patch shutdown so it doesn't try to talk to real processes
        with patch.object(DebugSession, "shutdown"):
            mgr.detach("daisy")

        with pytest.raises(ValueError, match="No session"):
            mgr.get("daisy")

    def test_detach_nonexistent_raises(self):
        mgr = SessionManager()
        with pytest.raises(ValueError, match="No session"):
            mgr.detach("nope")

    def test_get_nonexistent_raises(self):
        mgr = SessionManager()
        with pytest.raises(ValueError, match="No session"):
            mgr.get("nope")

    def test_list(self):
        mgr = SessionManager()
        assert mgr.list() == []

        _mock_attach(mgr, "daisy")
        sessions = mgr.list()
        assert len(sessions) == 1
        assert sessions[0].name == "daisy"

    def test_detach_all(self):
        mgr = SessionManager()
        _mock_attach(mgr, "daisy")

        with patch.object(DebugSession, "shutdown"):
            mgr.detach_all()

        assert mgr.list() == []

    def test_attach_gdb_connect_failure_cleans_up(self):
        """If GDB fails to connect, OpenOCD should be cleaned up."""
        mgr = SessionManager()
        profile = get_profile("daisy")

        with patch.object(OpenOcdProcess, "start"), \
             patch.object(OpenOcdProcess, "stop") as mock_ocd_stop, \
             patch.object(MiBridge, "start"), \
             patch.object(MiBridge, "stop") as mock_gdb_stop, \
             patch.object(MiBridge, "connect", return_value=MiResult(message="error", payload={"msg": "Connection refused"})):
            with pytest.raises(RuntimeError, match="Connection refused"):
                mgr.attach(target_profile=profile, target_name="daisy")

        mock_gdb_stop.assert_called_once()
        mock_ocd_stop.assert_called_once()
        assert mgr.list() == []

    def test_attach_elf_load_failure_cleans_up(self):
        """If ELF loading fails, everything should be cleaned up."""
        mgr = SessionManager()
        profile = get_profile("daisy")

        with patch.object(OpenOcdProcess, "start"), \
             patch.object(OpenOcdProcess, "stop") as mock_ocd_stop, \
             patch.object(MiBridge, "start"), \
             patch.object(MiBridge, "stop") as mock_gdb_stop, \
             patch.object(MiBridge, "load_symbols", return_value=MiResult(message="error", payload={"msg": "No such file"})):
            with pytest.raises(RuntimeError, match="No such file"):
                mgr.attach(
                    target_profile=profile,
                    target_name="daisy",
                    elf_path="/nonexistent.elf",
                )

        mock_gdb_stop.assert_called_once()
        mock_ocd_stop.assert_called_once()
        assert mgr.list() == []
