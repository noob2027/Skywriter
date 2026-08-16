"""Immutable mission value and editing tests."""

from dataclasses import FrozenInstanceError

import pytest

from skywriter.domain.mission import (
    CircleAction,
    CircleDirection,
    GeoPoint,
    HoldAction,
    LandAction,
    Mission,
    MissionEditError,
    MissionSettings,
    ProceedAction,
)


def settings() -> MissionSettings:
    return MissionSettings(20.0, 6.0, True)


def test_typed_values_use_explicit_si_units_and_are_immutable() -> None:
    point = GeoPoint(38.8895, -77.0353)
    circle = CircleAction(point, altitude_m=30.0, radius_m=12.5)

    assert circle.turns == 1
    assert circle.direction is CircleDirection.CLOCKWISE
    with pytest.raises(FrozenInstanceError):
        circle.radius_m = 20.0  # type: ignore[misc]


def test_actions_append_in_creation_order() -> None:
    first = ProceedAction(GeoPoint(1.0, 2.0), 10.0)
    second = HoldAction(GeoPoint(3.0, 4.0), 20.0, 5.0)

    mission = Mission.create(settings(), mission_id="mission-1")
    updated = mission.append_action(first).append_action(second)

    assert mission.actions == ()
    assert updated.actions == (first, second)
    assert updated.id == "mission-1"


def test_land_closes_mission_until_it_is_removed() -> None:
    land = LandAction(GeoPoint(1.0, 2.0), approach_altitude_m=8.0)
    closed = Mission(settings()).append_action(land)

    assert closed.is_closed
    with pytest.raises(MissionEditError, match="remove Land"):
        closed.append_action(ProceedAction(GeoPoint(3.0, 4.0), 9.0))

    reopened = closed.remove_land()
    assert not reopened.is_closed
    assert reopened.append_action(ProceedAction(GeoPoint(3.0, 4.0), 9.0)).actions


def test_replace_rejects_trailing_or_duplicate_land_attempts() -> None:
    proceed = ProceedAction(GeoPoint(1.0, 2.0), 10.0)
    hold = HoldAction(GeoPoint(3.0, 4.0), 20.0, 5.0)
    land = LandAction(GeoPoint(5.0, 6.0), 7.0)
    mission = Mission(settings(), actions=(proceed, hold, land))

    with pytest.raises(MissionEditError, match="final"):
        mission.replace_action(0, land)


def test_replace_delete_and_move_return_new_missions_without_reordering() -> None:
    proceed = ProceedAction(GeoPoint(1.0, 2.0), 10.0)
    hold = HoldAction(GeoPoint(3.0, 4.0), 20.0, 5.0)
    land = LandAction(GeoPoint(5.0, 6.0), 7.0)
    mission = Mission(settings(), actions=(proceed, hold, land), id="stable")

    replacement = HoldAction(GeoPoint(7.0, 8.0), 25.0, 6.0)
    replaced = mission.replace_action(1, replacement)
    moved = replaced.move_action_point(1, GeoPoint(9.0, 10.0))
    deleted = moved.delete_action(0)

    assert mission.actions == (proceed, hold, land)
    assert replaced.actions == (proceed, replacement, land)
    assert moved.actions[1] == HoldAction(GeoPoint(9.0, 10.0), 25.0, 6.0)
    assert deleted.actions == (moved.actions[1], land)
    assert deleted.id == "stable"


def test_undo_clear_and_remove_land_are_safe_on_empty_or_open_drafts() -> None:
    empty = Mission(settings())
    proceed = ProceedAction(GeoPoint(1.0, 2.0), 10.0)
    open_mission = empty.append_action(proceed)

    assert empty.undo_last_action() is empty
    assert empty.clear_actions() is empty
    assert open_mission.remove_land() is open_mission
    assert open_mission.undo_last_action().actions == ()
    assert open_mission.clear_actions().actions == ()
