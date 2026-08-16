"""Deterministic beginner-mission compiler tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skywriter.domain.compiled import (
    CompiledMissionItem,
    MissionCommand,
    MissionFrame,
    MissionType,
)
from skywriter.domain.compiler import MissionCompiler
from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    LandAction,
    Mission,
    MissionSettings,
    ProceedAction,
)
from skywriter.domain.validation import MissionValidationError
from skywriter.infrastructure.json_repository import mission_from_document

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "missions"


def load_golden_fixture(name: str) -> tuple[Mission, list[dict[str, object]]]:
    raw: object = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("golden fixture root must be an object")
    mission_document = raw.get("mission")
    expected_items = raw.get("expected_items")
    if not isinstance(expected_items, list) or not all(
        isinstance(expected, dict) for expected in expected_items
    ):
        raise TypeError("golden fixture expected_items must be an object array")
    return mission_from_document(mission_document), cast(list[dict[str, object]], expected_items)


def item_document(item: CompiledMissionItem) -> dict[str, object]:
    return {
        "sequence": item.sequence,
        "frame": int(item.frame),
        "command": int(item.command),
        "current": item.current,
        "autocontinue": item.autocontinue,
        "param1": item.param1,
        "param2": item.param2,
        "param3": item.param3,
        "param4": item.param4,
        "latitude_e7": item.latitude_e7,
        "longitude_e7": item.longitude_e7,
        "altitude_m": item.altitude_m,
        "mission_type": int(item.mission_type),
    }


@pytest.mark.parametrize(
    "fixture_name",
    ("proceed.json", "hold.json", "circle.json", "mixed.json"),
)
def test_golden_missions_compile_field_for_field(fixture_name: str) -> None:
    mission, expected_items = load_golden_fixture(fixture_name)

    compiled = MissionCompiler().compile(mission)

    assert [item_document(item) for item in compiled.items] == expected_items


def test_repeated_compilation_is_deterministic_and_has_no_transport_state() -> None:
    mission, _ = load_golden_fixture("mixed.json")
    compiler = MissionCompiler()

    first = compiler.compile(mission)
    second = compiler.compile(mission)

    assert first == second
    assert first is not second
    assert not hasattr(first, "target_system")
    assert not hasattr(first, "acknowledged")


def test_drafts_and_structurally_invalid_missions_are_rejected() -> None:
    settings = MissionSettings(20.0, 6.0, True)
    draft = Mission(settings, actions=(ProceedAction(GeoPoint(1.0, 2.0), 10.0),))
    invalid = Mission(
        MissionSettings(20.0, 0.0, True),
        actions=(LandAction(GeoPoint(1.0, 2.0), 5.0),),
    )

    with pytest.raises(MissionValidationError):
        MissionCompiler().compile(draft)
    with pytest.raises(MissionValidationError):
        MissionCompiler().compile(invalid)


def test_coordinate_conversion_covers_geographic_and_half_unit_boundaries() -> None:
    mission = Mission(
        MissionSettings(20.0, 6.0, True),
        actions=(
            ProceedAction(GeoPoint(-90.0, -180.0), 10.0),
            ProceedAction(GeoPoint(0.00000005, -0.00000005), 11.0),
            LandAction(GeoPoint(90.0, 180.0), 5.0),
        ),
    )

    compiled = MissionCompiler().compile(mission)

    assert (compiled.items[2].latitude_e7, compiled.items[2].longitude_e7) == (
        -900_000_000,
        -1_800_000_000,
    )
    assert (compiled.items[3].latitude_e7, compiled.items[3].longitude_e7) == (1, -1)
    assert (compiled.items[4].latitude_e7, compiled.items[4].longitude_e7) == (
        900_000_000,
        1_800_000_000,
    )


def test_land_approach_and_native_land_have_identical_coordinates() -> None:
    mission, _ = load_golden_fixture("mixed.json")

    approach, land = MissionCompiler().compile(mission).items[-2:]

    assert approach.command is MissionCommand.NAV_WAYPOINT
    assert land.command is MissionCommand.NAV_LAND
    assert (approach.latitude_e7, approach.longitude_e7) == (
        land.latitude_e7,
        land.longitude_e7,
    )
    assert approach.altitude_m == 12.0
    assert land.altitude_m == 0.0


def test_compiler_preserves_structurally_valid_values_without_clamping() -> None:
    mission = Mission(
        MissionSettings(-5.0, 1_000_000.0, True),
        actions=(
            CircleAction(
                GeoPoint(45.0, 90.0),
                altitude_m=-100.0,
                radius_m=1_000_000.0,
            ),
            LandAction(GeoPoint(44.0, 89.0), approach_altitude_m=-10.0),
        ),
    )

    compiled = MissionCompiler().compile(mission)

    assert compiled.items[0].altitude_m == -5.0
    assert compiled.items[1].param2 == 1_000_000.0
    assert compiled.items[2].param3 == 1_000_000.0
    assert compiled.items[2].altitude_m == -100.0
    assert compiled.items[3].altitude_m == -10.0


def test_every_item_uses_exact_frame_flags_and_mission_type() -> None:
    mission, _ = load_golden_fixture("mixed.json")

    items = MissionCompiler().compile(mission).items

    assert all(item.frame is MissionFrame.GLOBAL_RELATIVE_ALT_INT for item in items)
    assert [item.current for item in items] == [True, *([False] * (len(items) - 1))]
    assert all(item.autocontinue for item in items)
    assert all(item.mission_type is MissionType.MISSION for item in items)
