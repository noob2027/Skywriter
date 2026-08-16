"""Draft and complete mission structural-validation tests."""

from dataclasses import replace
from typing import cast

import pytest

from skywriter.domain.mission import (
    CURRENT_MISSION_SCHEMA_VERSION,
    CircleAction,
    CircleDirection,
    GeoPoint,
    HoldAction,
    LandAction,
    Mission,
    MissionAction,
    MissionSettings,
    ProceedAction,
)
from skywriter.domain.validation import (
    MissionValidationError,
    ValidationCode,
    ValidationMode,
    is_valid_mission,
    require_valid_mission,
    validate_complete,
    validate_draft,
)


def valid_settings() -> MissionSettings:
    return MissionSettings(25.0, 7.5, True)


def valid_mixed_mission() -> Mission:
    return Mission(
        schema_version=CURRENT_MISSION_SCHEMA_VERSION,
        id="mixed-mission",
        settings=valid_settings(),
        actions=(
            ProceedAction(GeoPoint(38.0, -77.0), 30.0),
            HoldAction(GeoPoint(38.1, -77.1), 35.0, 10.0),
            CircleAction(GeoPoint(38.2, -77.2), 40.0, 15.0),
            LandAction(GeoPoint(38.3, -77.3), 12.0),
        ),
    )


def codes(mission: Mission, mode: ValidationMode = ValidationMode.DRAFT) -> set[ValidationCode]:
    findings = (
        validate_draft(mission) if mode is ValidationMode.DRAFT else validate_complete(mission)
    )
    return {finding.code for finding in findings}


def test_each_action_and_a_mixed_complete_mission_are_valid() -> None:
    mission = valid_mixed_mission()

    assert validate_draft(mission) == ()
    assert validate_complete(mission) == ()
    assert is_valid_mission(mission, ValidationMode.COMPLETE)
    assert require_valid_mission(mission, ValidationMode.COMPLETE) is mission


def test_draft_permits_missing_land_but_complete_mode_requires_it() -> None:
    draft = Mission(valid_settings(), actions=valid_mixed_mission().actions[:-1])

    assert validate_draft(draft) == ()
    assert codes(draft, ValidationMode.COMPLETE) == {ValidationCode.LAND_REQUIRED}


def test_duplicate_and_nonfinal_land_are_reported() -> None:
    land = LandAction(GeoPoint(1.0, 2.0), 5.0)
    invalid = Mission(
        valid_settings(),
        actions=(land, ProceedAction(GeoPoint(3.0, 4.0), 6.0), land),
    )

    assert ValidationCode.DUPLICATE_LAND in codes(invalid)
    assert ValidationCode.LAND_NOT_LAST in codes(invalid)


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        (GeoPoint(float("nan"), 0.0), ValidationCode.INVALID_LATITUDE),
        (GeoPoint(91.0, 0.0), ValidationCode.INVALID_LATITUDE),
        (GeoPoint(0.0, float("inf")), ValidationCode.INVALID_LONGITUDE),
        (GeoPoint(0.0, -181.0), ValidationCode.INVALID_LONGITUDE),
    ],
)
def test_invalid_coordinates_are_reported(point: GeoPoint, expected: ValidationCode) -> None:
    mission = Mission(valid_settings(), actions=(ProceedAction(point, 10.0),))

    assert expected in codes(mission)


@pytest.mark.parametrize("altitude", [float("nan"), float("inf"), True])
def test_all_action_altitudes_must_be_finite_numbers(altitude: float) -> None:
    mission = Mission(
        valid_settings(),
        actions=(ProceedAction(GeoPoint(1.0, 2.0), altitude),),
    )

    assert ValidationCode.INVALID_ALTITUDE in codes(mission)


@pytest.mark.parametrize(
    ("mission", "expected"),
    [
        (
            Mission(MissionSettings(10.0, 0.0, True)),
            ValidationCode.INVALID_CRUISE_SPEED,
        ),
        (
            Mission(MissionSettings(float("nan"), 2.0, True)),
            ValidationCode.INVALID_TAKEOFF_ALTITUDE,
        ),
        (
            Mission(MissionSettings(10.0, 2.0, False)),
            ValidationCode.OBSTACLE_WARNING_NOT_ACKNOWLEDGED,
        ),
        (
            Mission(
                valid_settings(),
                actions=(HoldAction(GeoPoint(1.0, 2.0), 3.0, 0.0),),
            ),
            ValidationCode.INVALID_HOLD_TIME,
        ),
        (
            Mission(
                valid_settings(),
                actions=(CircleAction(GeoPoint(1.0, 2.0), 3.0, -1.0),),
            ),
            ValidationCode.INVALID_CIRCLE_RADIUS,
        ),
    ],
)
def test_structurally_positive_and_acknowledged_values_are_required(
    mission: Mission, expected: ValidationCode
) -> None:
    assert expected in codes(mission)


def test_circle_is_exactly_one_clockwise_turn() -> None:
    wrong_turns = Mission(
        valid_settings(),
        actions=(CircleAction(GeoPoint(1.0, 2.0), 3.0, 4.0, turns=2),),
    )
    wrong_direction = Mission(
        valid_settings(),
        actions=(
            CircleAction(
                GeoPoint(1.0, 2.0),
                3.0,
                4.0,
                direction=cast(CircleDirection, "counterclockwise"),
            ),
        ),
    )

    assert ValidationCode.INVALID_CIRCLE_TURNS in codes(wrong_turns)
    assert ValidationCode.INVALID_CIRCLE_DIRECTION in codes(wrong_direction)


def test_unknown_actions_schema_and_empty_id_are_rejected() -> None:
    unknown = cast(MissionAction, object())
    mission = replace(
        Mission(valid_settings(), actions=(unknown,)),
        schema_version=99,
        id=" ",
    )

    assert codes(mission) == {
        ValidationCode.UNKNOWN_ACTION,
        ValidationCode.UNSUPPORTED_SCHEMA_VERSION,
        ValidationCode.INVALID_MISSION_ID,
    }
    with pytest.raises(MissionValidationError) as raised:
        require_valid_mission(mission)
    assert raised.value.findings


def test_structural_validation_does_not_invent_operational_envelope_bounds() -> None:
    mission = Mission(
        MissionSettings(
            takeoff_altitude_m=-5.0,
            cruise_speed_m_s=1_000_000.0,
            obstacle_warning_acknowledged=True,
        ),
        actions=(CircleAction(GeoPoint(90.0, 180.0), altitude_m=-100.0, radius_m=1_000_000.0),),
    )

    assert validate_draft(mission) == ()
