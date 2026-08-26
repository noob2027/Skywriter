"""Pytest fixtures for the pinned stock ArduCopter SITL harness."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.sitl.pinned import (
    CleanMissionState,
    SitlEndpoint,
    SitlReadiness,
    SitlTargetIdentity,
    pinned_sitl_session,
)


def _required_path(name: str) -> Path:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(
            "exact stock SITL is exercised by the Ubuntu harness job; "
            f"set {name} to run it explicitly"
        )
    return Path(value)


@pytest.fixture(scope="session")
def sitl_readiness() -> Iterator[SitlReadiness]:
    if not sys.platform.startswith("linux"):
        pytest.skip("the approved stock ArduCopter 4.6.3 artifact is Linux x86_64 only")
    binary = _required_path("SKYWRITER_SITL_BINARY")
    startup_defaults = _required_path("SKYWRITER_SITL_STARTUP_DEFAULTS")
    output_dir = _required_path("SKYWRITER_SITL_EVIDENCE")
    base_port_text = os.environ.get("SKYWRITER_SITL_BASE_PORT")
    base_port = int(base_port_text) if base_port_text is not None else None
    with pinned_sitl_session(
        binary,
        startup_defaults,
        output_dir,
        preferred_base_port=base_port,
    ) as readiness:
        yield readiness


@pytest.fixture(scope="session")
def sitl_endpoint(sitl_readiness: SitlReadiness) -> SitlEndpoint:
    return sitl_readiness.endpoint


@pytest.fixture(scope="session")
def sitl_target_identity(sitl_readiness: SitlReadiness) -> SitlTargetIdentity:
    return sitl_readiness.target_identity


@pytest.fixture(scope="session")
def sitl_clean_mission_state(sitl_readiness: SitlReadiness) -> CleanMissionState:
    return sitl_readiness.clean_mission_state
