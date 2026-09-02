"""Qt-free frozen-runtime probes used by the Windows installer acceptance."""

from __future__ import annotations

import importlib
from collections.abc import Sequence

from skywriter.infrastructure.serial_ports import PINNED_PYSERIAL_VERSION

PACKAGED_SERIAL_IMPORT_SMOKE_ARGUMENT = "--packaged-serial-import-smoke"


def run_packaged_serial_import_smoke(arguments: Sequence[str]) -> int:
    """Prove the pinned Windows serial modules import without opening a device."""

    if PACKAGED_SERIAL_IMPORT_SMOKE_ARGUMENT not in arguments:
        raise ValueError("packaged serial-import smoke argument is required")
    serial_module = importlib.import_module("serial")
    importlib.import_module("serial.tools.list_ports_windows")
    if getattr(serial_module, "VERSION", None) != PINNED_PYSERIAL_VERSION:
        raise RuntimeError(f"packaged pyserial {PINNED_PYSERIAL_VERSION} runtime is required")
    return 0
