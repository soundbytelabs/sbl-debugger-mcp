"""SVD loading — resolves MCU directory and builds PeripheralDb."""

from __future__ import annotations

import os
from pathlib import Path

from .peripheral_db import CECROPS_AVAILABLE, PeripheralDb

# Well-known MCU architecture paths to search
_MCU_ARCH_DIRS = ["mcu/arm"]


def resolve_mcu_dir(mcu_name: str) -> Path | None:
    """Find the MCU directory under SBL_HW_PATH.

    Searches SBL_HW_PATH/<arch>/<mcu_name> for each known arch dir.
    Returns the path if found and it contains a cecrops.json, else None.
    """
    hw_path = os.environ.get("SBL_HW_PATH")
    if not hw_path:
        return None

    hw_root = Path(hw_path)
    if not hw_root.is_dir():
        return None

    for arch_dir in _MCU_ARCH_DIRS:
        candidate = hw_root / arch_dir / mcu_name
        if (candidate / "cecrops.json").is_file():
            return candidate

    return None


def load_peripheral_db(mcu_name: str) -> PeripheralDb | None:
    """Load a PeripheralDb for an MCU by name.

    Resolves the MCU directory from SBL_HW_PATH, parses the SVD file
    using cecrops, applies errata patches, and returns a PeripheralDb.

    Returns None if:
    - cecrops is not installed
    - SBL_HW_PATH is not set
    - MCU directory or cecrops.json not found
    - No cached SVD file available

    Raises on parse/patch errors (those indicate real problems).
    """
    if not CECROPS_AVAILABLE:
        return None

    mcu_dir = resolve_mcu_dir(mcu_name)
    if mcu_dir is None:
        return None

    return _load_from_dir(mcu_dir)


def _load_from_dir(mcu_dir: Path) -> PeripheralDb | None:
    """Load PeripheralDb from an MCU directory with cecrops.json."""
    from cecrops.manifest import load_manifest
    from cecrops.parser import parse_svd
    from cecrops.patches import apply_patches

    manifest = load_manifest(mcu_dir / "cecrops.json")

    # Find the cached SVD file
    cache_dir = mcu_dir / ".cache"
    svd_files = list(cache_dir.glob("*.svd")) if cache_dir.is_dir() else []
    if not svd_files:
        return None

    # Use the first (typically only) SVD file
    device = parse_svd(svd_files[0])

    # Apply errata patches
    if manifest.patches:
        apply_patches(device.peripherals, manifest.patches, verbose=False)

    return PeripheralDb(device)
