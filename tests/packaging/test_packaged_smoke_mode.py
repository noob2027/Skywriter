"""Fail-closed packaged startup smoke-mode tests."""

import pytest

from skywriter.infrastructure.mavlink.connection import (
    PACKAGED_SMOKE_TEST_ENVIRONMENT,
    TransportDescriptor,
    TransportKind,
    open_pymavlink_link,
)


def test_packaged_smoke_mode_blocks_vehicle_open_before_dependency_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PACKAGED_SMOKE_TEST_ENVIRONMENT, "1")

    with pytest.raises(RuntimeError, match="vehicle I/O is disabled"):
        open_pymavlink_link(TransportDescriptor("COM1", TransportKind.USB))
