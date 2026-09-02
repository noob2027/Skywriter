from __future__ import annotations

from types import SimpleNamespace

import pytest
import serial
from serial.tools import list_ports

from skywriter.infrastructure.serial_ports import (
    PINNED_PYSERIAL_VERSION,
    PySerialPortEnumerator,
    SerialEnumerationError,
)


def test_explicit_enumerator_returns_natural_sorted_human_descriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serial, "VERSION", PINNED_PYSERIAL_VERSION)
    observed: list[bool] = []

    def fake_comports(*, include_links: bool) -> list[SimpleNamespace]:
        observed.append(include_links)
        return [
            SimpleNamespace(
                device="COM10",
                description="USB Serial Device",
                manufacturer="Radio vendor",
            ),
            SimpleNamespace(
                device="COM2",
                description="Flight controller (COM2)",
                manufacturer="Controller vendor",
            ),
        ]

    monkeypatch.setattr(list_ports, "comports", fake_comports)

    ports = PySerialPortEnumerator().enumerate()

    assert observed == [False]
    assert [port.device for port in ports] == ["COM2", "COM10"]
    assert ports[0].display_name == "COM2 — Flight controller (COM2) · Controller vendor"
    assert ports[1].display_name == "COM10 — USB Serial Device · Radio vendor"


def test_enumerator_fails_typed_before_host_query_when_exact_pin_is_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serial, "VERSION", "3.4")
    monkeypatch.setattr(
        list_ports,
        "comports",
        lambda **_kwargs: pytest.fail("host enumeration must not run with a wrong pin"),
    )

    with pytest.raises(SerialEnumerationError, match="pyserial 3.5 is required; found 3.4"):
        PySerialPortEnumerator().enumerate()
