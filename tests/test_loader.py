"""Tests for SVD loader — MCU directory resolution and PeripheralDb loading."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sbl_debugger.svd.loader import resolve_mcu_dir, load_peripheral_db, _load_from_dir


# -- resolve_mcu_dir --

class TestResolveMcuDir:
    def test_resolves_with_valid_env(self, tmp_path):
        """SBL_HW_PATH set, MCU dir exists with cecrops.json."""
        mcu_dir = tmp_path / "mcu" / "arm" / "stm32h750"
        mcu_dir.mkdir(parents=True)
        (mcu_dir / "cecrops.json").write_text("{}")

        with patch.dict(os.environ, {"SBL_HW_PATH": str(tmp_path)}):
            result = resolve_mcu_dir("stm32h750")

        assert result == mcu_dir

    def test_returns_none_without_env(self):
        """SBL_HW_PATH not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove SBL_HW_PATH if present
            os.environ.pop("SBL_HW_PATH", None)
            result = resolve_mcu_dir("stm32h750")

        assert result is None

    def test_returns_none_nonexistent_dir(self, tmp_path):
        """SBL_HW_PATH set but points to nonexistent directory."""
        with patch.dict(os.environ, {"SBL_HW_PATH": str(tmp_path / "nope")}):
            result = resolve_mcu_dir("stm32h750")

        assert result is None

    def test_returns_none_no_cecrops_json(self, tmp_path):
        """MCU dir exists but no cecrops.json."""
        mcu_dir = tmp_path / "mcu" / "arm" / "stm32h750"
        mcu_dir.mkdir(parents=True)

        with patch.dict(os.environ, {"SBL_HW_PATH": str(tmp_path)}):
            result = resolve_mcu_dir("stm32h750")

        assert result is None

    def test_returns_none_unknown_mcu(self, tmp_path):
        """SBL_HW_PATH valid but MCU doesn't exist."""
        (tmp_path / "mcu" / "arm").mkdir(parents=True)

        with patch.dict(os.environ, {"SBL_HW_PATH": str(tmp_path)}):
            result = resolve_mcu_dir("nonexistent_mcu")

        assert result is None


# -- load_peripheral_db --

class TestLoadPeripheralDb:
    def test_returns_none_when_cecrops_unavailable(self):
        """When cecrops is not installed, returns None."""
        with patch("sbl_debugger.svd.loader.CECROPS_AVAILABLE", False):
            result = load_peripheral_db("stm32h750")

        assert result is None

    def test_returns_none_when_mcu_dir_not_found(self):
        """When MCU dir can't be resolved, returns None."""
        with patch("sbl_debugger.svd.loader.resolve_mcu_dir", return_value=None):
            result = load_peripheral_db("stm32h750")

        assert result is None

    def test_delegates_to_load_from_dir(self, tmp_path):
        """When MCU dir is found, delegates to _load_from_dir."""
        mock_db = MagicMock()
        with patch("sbl_debugger.svd.loader.resolve_mcu_dir", return_value=tmp_path), \
             patch("sbl_debugger.svd.loader._load_from_dir", return_value=mock_db) as mock_load:
            result = load_peripheral_db("stm32h750")

        assert result is mock_db
        mock_load.assert_called_once_with(tmp_path)


# -- _load_from_dir --

class TestLoadFromDir:
    def test_returns_none_when_no_svd_cache(self, tmp_path):
        """No .cache/ directory or no SVD files."""
        # Create minimal cecrops.json
        import json
        manifest = {
            "schemaVersion": "0.1",
            "mcu": "test",
            "source": {"url": "http://example.com/test.svd"},
            "outputs": {"test": {"peripherals": ["TEST"], "output": "reg/test.hpp"}},
        }
        (tmp_path / "cecrops.json").write_text(json.dumps(manifest))

        result = _load_from_dir(tmp_path)
        assert result is None

    def test_loads_svd_and_applies_patches(self, tmp_path):
        """Full pipeline: parse SVD, apply patches, return PeripheralDb."""
        import json

        # Create cecrops.json with a delete patch
        manifest = {
            "schemaVersion": "0.1",
            "mcu": "test",
            "source": {"url": "http://example.com/test.svd"},
            "outputs": {"test": {"peripherals": ["TEST"], "output": "reg/test.hpp"}},
            "patches": {
                "TEST": {
                    "description": "Remove bogus register",
                    "registers": {
                        "BOGUS": {"delete": True},
                    },
                },
            },
        }
        (tmp_path / "cecrops.json").write_text(json.dumps(manifest))

        # Create minimal SVD file
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        svd_content = """<?xml version="1.0" encoding="utf-8"?>
<device>
  <name>TEST_MCU</name>
  <version>1.0</version>
  <description>Test</description>
  <cpu><name>CM7</name></cpu>
  <peripherals>
    <peripheral>
      <name>TEST</name>
      <description>Test peripheral</description>
      <baseAddress>0x40000000</baseAddress>
      <registers>
        <register>
          <name>CR</name>
          <description>Control</description>
          <addressOffset>0x00</addressOffset>
          <size>32</size>
          <fields>
            <field>
              <name>EN</name>
              <description>Enable</description>
              <bitOffset>0</bitOffset>
              <bitWidth>1</bitWidth>
            </field>
          </fields>
        </register>
        <register>
          <name>BOGUS</name>
          <description>Should be patched out</description>
          <addressOffset>0x04</addressOffset>
          <size>32</size>
        </register>
      </registers>
    </peripheral>
  </peripherals>
</device>"""
        (cache_dir / "test.svd").write_text(svd_content)

        result = _load_from_dir(tmp_path)

        assert result is not None
        assert result.device_name == "TEST_MCU"
        # BOGUS register should be patched out
        regs = result.list_registers("TEST")
        reg_names = [r["name"] for r in regs]
        assert "CR" in reg_names
        assert "BOGUS" not in reg_names
