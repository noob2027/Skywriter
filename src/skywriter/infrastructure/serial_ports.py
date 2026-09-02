"""Explicit, read-only host serial-port enumeration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

PINNED_PYSERIAL_VERSION = "3.5"


class SerialEnumerationError(RuntimeError):
    """The host serial-port inventory could not be read."""


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    """Human-readable immutable description of one currently enumerated port."""

    device: str
    description: str
    manufacturer: str | None = None

    def __post_init__(self) -> None:
        if not self.device.strip():
            raise ValueError("serial device must not be empty")
        if not self.description.strip():
            raise ValueError("serial description must not be empty")

    @property
    def display_name(self) -> str:
        details = self.description.strip()
        manufacturer = "" if self.manufacturer is None else self.manufacturer.strip()
        if manufacturer and manufacturer.casefold() not in details.casefold():
            details = f"{details} · {manufacturer}"
        return f"{self.device} — {details}"


class SerialPortEnumerator(Protocol):
    """Injectable boundary; callers decide when enumeration is allowed."""

    def enumerate(self) -> tuple[SerialPortInfo, ...]: ...


class PySerialPortEnumerator:
    """Enumerate the host only when the operator explicitly requests a refresh."""

    def enumerate(self) -> tuple[SerialPortInfo, ...]:
        try:
            import serial
        except ModuleNotFoundError as error:
            raise SerialEnumerationError(
                f"pyserial {PINNED_PYSERIAL_VERSION} is required for serial-port refresh"
            ) from error
        installed = str(getattr(serial, "VERSION", "unknown"))
        if installed != PINNED_PYSERIAL_VERSION:
            raise SerialEnumerationError(
                f"pyserial {PINNED_PYSERIAL_VERSION} is required; found {installed}"
            )
        try:
            from serial.tools import list_ports

            available = list_ports.comports(include_links=False)
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            raise SerialEnumerationError(f"serial-port refresh failed: {detail}") from error
        ports = tuple(
            SerialPortInfo(
                device=str(port.device),
                description=str(port.description or "Serial device"),
                manufacturer=(
                    None
                    if getattr(port, "manufacturer", None) in (None, "")
                    else str(port.manufacturer)
                ),
            )
            for port in available
        )
        return tuple(sorted(ports, key=lambda port: _natural_device_key(port.device)))


class StaticSerialPortEnumerator:
    """Deterministic injected inventory for tests and hardware-blocked acceptance."""

    def __init__(self, ports: tuple[SerialPortInfo, ...]) -> None:
        if not all(isinstance(port, SerialPortInfo) for port in ports):
            raise TypeError("ports must contain SerialPortInfo values")
        self._ports = tuple(ports)

    def enumerate(self) -> tuple[SerialPortInfo, ...]:
        return self._ports


def _natural_device_key(device: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", device)
    )
