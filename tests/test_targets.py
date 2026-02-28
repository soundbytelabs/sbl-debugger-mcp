"""Tests for target profiles."""

import pytest

from sbl_debugger.targets import (
    TARGET_PROFILES,
    TargetProfile,
    get_profile,
    list_profiles,
)


class TestTargetProfile:
    def test_frozen(self):
        p = TargetProfile("desc", "iface.cfg", "target.cfg")
        with pytest.raises(AttributeError):
            p.description = "changed"

    def test_to_dict(self):
        p = TargetProfile("Daisy Seed", "stlink.cfg", "stm32h7x.cfg")
        d = p.to_dict()
        assert d["description"] == "Daisy Seed"
        assert d["openocd_interface"] == "stlink.cfg"
        assert d["openocd_target"] == "stm32h7x.cfg"


class TestGetProfile:
    def test_known_target(self):
        p = get_profile("daisy")
        assert p.openocd_interface == "stlink.cfg"
        assert p.openocd_target == "stm32h7x.cfg"

    def test_all_profiles_exist(self):
        for name in ["daisy", "pico", "pico2"]:
            p = get_profile(name)
            assert p.openocd_interface
            assert p.openocd_target

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError, match="Unknown target 'nope'"):
            get_profile("nope")

    def test_error_lists_available(self):
        with pytest.raises(ValueError, match="daisy"):
            get_profile("bogus")


class TestListProfiles:
    def test_returns_all(self):
        profiles = list_profiles()
        assert "daisy" in profiles
        assert "pico" in profiles
        assert "pico2" in profiles

    def test_values_are_dicts(self):
        profiles = list_profiles()
        for name, data in profiles.items():
            assert "description" in data
            assert "openocd_interface" in data
            assert "openocd_target" in data
