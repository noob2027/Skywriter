"""Pure ArduCopter 4.6.3 compatibility-envelope tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from skywriter.compatibility.arducopter_4_6_3 import (
    NORMALIZATION_WHITELIST,
    HomeSnapshot,
    HomeUnresolved,
    HomeUnresolvedReason,
    NativeMissionPackage,
    VehicleIdentity,
    canonicalize_expected,
    prepare_native_mission,
    verify_native_readback,
)
from skywriter.domain.compiled import CompiledMission
from skywriter.domain.compiler import MissionCompiler
from skywriter.infrastructure.json_repository import mission_from_document

FIXTURE = Path(__file__).parents[2] / "fixtures" / "missions" / "mixed.json"
VEHICLE = VehicleIdentity("mavlink-system-1-component-1")


def _compiled_mission() -> CompiledMission:
    document = cast(dict[str, object], json.loads(FIXTURE.read_text(encoding="utf-8")))
    return MissionCompiler().compile(mission_from_document(document["mission"]))


def _home(**changes: object) -> HomeSnapshot:
    values: dict[str, object] = {
        "vehicle": VEHICLE,
        "latitude_e7": 515_007_291,
        "longitude_e7": -1_246_254,
        "altitude_m": 15.1,
        "captured_at_s": 100.0,
        "valid_for_s": 5.0,
        "authoritative": True,
    }
    values.update(changes)
    return HomeSnapshot(**values)  # type: ignore[arg-type]


def _package(home: HomeSnapshot | HomeUnresolved | None = None) -> NativeMissionPackage:
    result = prepare_native_mission(
        _compiled_mission(),
        target_vehicle=VEHICLE,
        home=_home() if home is None else home,
        now_s=102.0,
    )
    assert isinstance(result, NativeMissionPackage)
    return result


def test_translation_adds_native_home_and_shifts_the_unchanged_logical_compiler() -> None:
    compiled = _compiled_mission()

    package = _package()

    assert [item.command for item in package.items] == [16, 22, 178, 16, 19, 18, 16, 21]
    assert [item.sequence for item in package.items] == list(range(8))
    assert package.items[0].frame == 0
    assert (package.items[0].latitude_e7, package.items[0].longitude_e7) == (
        515_007_291,
        -1_246_254,
    )
    assert package.items[0].altitude_m == 15.1
    assert all(not item.current for item in package.items)
    assert package.items[1].altitude_m == compiled.items[0].altitude_m
    assert package.items[1].latitude_e7 == compiled.items[0].latitude_e7 == 0
    assert package.items[1].longitude_e7 == compiled.items[0].longitude_e7 == 0
    assert [item.frame for item in package.items[1:]] == [6] * 7


def test_home_unresolved_states_never_produce_uploadable_numeric_coordinates() -> None:
    disconnected = HomeUnresolved(
        HomeUnresolvedReason.UNCONNECTED,
        "no connection owns an authoritative home",
    )
    result = prepare_native_mission(
        _compiled_mission(), target_vehicle=VEHICLE, home=disconnected, now_s=102.0
    )

    assert result is disconnected
    assert not isinstance(result, NativeMissionPackage)


def test_native_package_cannot_be_reconstructed_with_an_expired_home() -> None:
    package = _package()

    with pytest.raises(ValueError, match="home is unresolved: stale"):
        replace(package, validated_at_s=106.0)


@pytest.mark.parametrize(
    ("home", "now_s", "reason"),
    (
        (
            _home(vehicle=VehicleIdentity("different-vehicle")),
            102.0,
            HomeUnresolvedReason.WRONG_VEHICLE,
        ),
        (_home(authoritative=False), 102.0, HomeUnresolvedReason.INVALID),
        (_home(latitude_e7=900_000_001), 102.0, HomeUnresolvedReason.INVALID),
        (_home(longitude_e7=-1_800_000_001), 102.0, HomeUnresolvedReason.INVALID),
        (_home(latitude_e7=0, longitude_e7=0, altitude_m=0.0), 102.0, HomeUnresolvedReason.INVALID),
        (_home(altitude_m=15.101), 102.0, HomeUnresolvedReason.INVALID),
        (_home(valid_for_s=0.0), 102.0, HomeUnresolvedReason.INVALID),
        (_home(captured_at_s=103.0), 102.0, HomeUnresolvedReason.INVALID),
        (_home(valid_for_s=1.0), 102.0, HomeUnresolvedReason.STALE),
    ),
)
def test_invalid_stale_or_wrong_vehicle_home_fails_closed(
    home: HomeSnapshot, now_s: float, reason: HomeUnresolvedReason
) -> None:
    result = prepare_native_mission(
        _compiled_mission(), target_vehicle=VEHICLE, home=home, now_s=now_s
    )

    assert isinstance(result, HomeUnresolved)
    assert result.reason is reason


def test_closed_normalization_whitelist_matches_only_observed_4_6_3_changes() -> None:
    canonical = canonicalize_expected(_package())

    assert len(NORMALIZATION_WHITELIST) == 7
    assert [item.frame for item in canonical] == [0, 3, 0, 3, 3, 3, 3, 3]
    assert canonical[4].param3 == 1.0
    assert canonical[7].param4 == 1.0
    assert canonical[0].altitude_m == 15.09999942779541


def test_exact_canonical_readback_verifies_home_and_logical_items_separately() -> None:
    package = _package()

    result = verify_native_readback(package, canonicalize_expected(package))

    assert result.verified
    assert result.home.verified
    assert result.mission.verified


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("sequence", 99),
        ("frame", 0),
        ("command", 21),
        ("current", True),
        ("autocontinue", False),
        ("param1", 99.0),
        ("param2", 99.0),
        ("param3", 99.0),
        ("param4", 99.0),
        ("latitude_e7", 123),
        ("longitude_e7", 456),
        ("altitude_m", 99.0),
        ("mission_type", 1),
    ),
)
def test_every_unapproved_logical_readback_field_change_fails_closed(
    field: str, value: object
) -> None:
    package = _package()
    downloaded = list(canonicalize_expected(package))
    downloaded[1] = replace(downloaded[1], **{field: value})  # type: ignore[arg-type]

    result = verify_native_readback(package, tuple(downloaded))

    assert not result.verified
    assert result.home.verified
    assert any(mismatch.field == field for mismatch in result.mission.mismatches)


def test_count_change_fails_closed() -> None:
    package = _package()
    downloaded = canonicalize_expected(package)[:-1]

    result = verify_native_readback(package, downloaded)

    assert not result.verified
    assert any(mismatch.field == "count" for mismatch in result.mission.mismatches)


def test_native_home_is_verified_separately_from_unchanged_logical_items() -> None:
    package = _package()
    downloaded = list(canonicalize_expected(package))
    downloaded[0] = replace(downloaded[0], latitude_e7=downloaded[0].latitude_e7 + 1)

    result = verify_native_readback(package, tuple(downloaded))

    assert not result.verified
    assert not result.home.verified
    assert result.mission.verified
    assert result.home.mismatches[0].location == "home"
