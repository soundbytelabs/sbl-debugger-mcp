"""Predefined target profiles for known hardware."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetProfile:
    """OpenOCD + GDB configuration for a known target."""

    description: str
    openocd_interface: str
    openocd_target: str

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "openocd_interface": self.openocd_interface,
            "openocd_target": self.openocd_target,
        }


TARGET_PROFILES: dict[str, TargetProfile] = {
    "daisy": TargetProfile(
        description="Daisy Seed (STM32H750) via ST-LINK V3",
        openocd_interface="stlink.cfg",
        openocd_target="stm32h7x.cfg",
    ),
    "pico": TargetProfile(
        description="Raspberry Pi Pico (RP2040) via Debug Probe",
        openocd_interface="cmsis-dap.cfg",
        openocd_target="rp2040.cfg",
    ),
    "pico2": TargetProfile(
        description="Raspberry Pi Pico 2 (RP2350) via Debug Probe",
        openocd_interface="cmsis-dap.cfg",
        openocd_target="rp2350.cfg",
    ),
}


def get_profile(name: str) -> TargetProfile:
    """Look up a target profile by name.

    Raises ValueError if the profile doesn't exist.
    """
    profile = TARGET_PROFILES.get(name)
    if profile is None:
        available = ", ".join(sorted(TARGET_PROFILES))
        raise ValueError(f"Unknown target '{name}'. Available: {available}")
    return profile


def list_profiles() -> dict[str, dict]:
    """Return all profiles as a serializable dict."""
    return {name: p.to_dict() for name, p in TARGET_PROFILES.items()}
