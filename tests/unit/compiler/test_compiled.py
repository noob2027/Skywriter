"""Closed, immutable compiled-mission value tests."""

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from skywriter.domain.compiled import (
    COMPATIBILITY_ASSUMPTIONS,
    CompiledMission,
    CompiledMissionItem,
    MissionCommand,
    MissionFrame,
    MissionType,
)


def item(
    sequence: int = 0, command: MissionCommand = MissionCommand.NAV_TAKEOFF
) -> CompiledMissionItem:
    return CompiledMissionItem(
        sequence=sequence,
        frame=MissionFrame.GLOBAL_RELATIVE_ALT_INT,
        command=command,
        current=sequence == 0,
        autocontinue=True,
        param1=0.0,
        param2=0.0,
        param3=0.0,
        param4=0.0,
        latitude_e7=0,
        longitude_e7=0,
        altitude_m=10.0,
        mission_type=MissionType.MISSION,
    )


def test_command_type_is_exactly_the_approved_closed_whitelist() -> None:
    assert {int(command) for command in MissionCommand} == {16, 18, 19, 21, 22, 178}
    assert not hasattr(MissionCommand, "NAV_RETURN_TO_LAUNCH")

    with pytest.raises(ValueError):
        MissionCommand(20)
    with pytest.raises(TypeError, match="MissionCommand"):
        CompiledMissionItem(
            sequence=0,
            frame=MissionFrame.GLOBAL_RELATIVE_ALT_INT,
            command=cast(MissionCommand, 20),
            current=True,
            autocontinue=True,
            param1=0.0,
            param2=0.0,
            param3=0.0,
            param4=0.0,
            latitude_e7=0,
            longitude_e7=0,
            altitude_m=0.0,
            mission_type=MissionType.MISSION,
        )


def test_compiled_values_are_immutable_and_sequences_are_contiguous() -> None:
    compiled = CompiledMission((item(), item(1, MissionCommand.NAV_LAND)))

    with pytest.raises(FrozenInstanceError):
        compiled.items[0].altitude_m = 20.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        compiled.items = ()  # type: ignore[misc]
    with pytest.raises(ValueError, match="no gaps"):
        CompiledMission((item(), item(2, MissionCommand.NAV_LAND)))


def test_compiled_item_rejects_raw_or_nonfinite_protocol_fields() -> None:
    with pytest.raises(TypeError, match="frame"):
        CompiledMissionItem(
            sequence=0,
            frame=cast(MissionFrame, 6),
            command=MissionCommand.NAV_TAKEOFF,
            current=True,
            autocontinue=True,
            param1=0.0,
            param2=0.0,
            param3=0.0,
            param4=0.0,
            latitude_e7=0,
            longitude_e7=0,
            altitude_m=0.0,
            mission_type=MissionType.MISSION,
        )
    with pytest.raises(TypeError, match="param1"):
        CompiledMissionItem(
            sequence=0,
            frame=MissionFrame.GLOBAL_RELATIVE_ALT_INT,
            command=MissionCommand.NAV_TAKEOFF,
            current=True,
            autocontinue=True,
            param1=float("nan"),
            param2=0.0,
            param3=0.0,
            param4=0.0,
            latitude_e7=0,
            longitude_e7=0,
            altitude_m=0.0,
            mission_type=MissionType.MISSION,
        )


def test_pin_verification_assumptions_are_explicit_and_specific() -> None:
    assert len(COMPATIBILITY_ASSUMPTIONS) == 9
    combined = " ".join(COMPATIBILITY_ASSUMPTIONS)
    for required_term in (
        "GLOBAL_RELATIVE_ALT_INT",
        "NAV_TAKEOFF",
        "DO_CHANGE_SPEED",
        "NAV_WAYPOINT",
        "NAV_LOITER_TIME",
        "NAV_LOITER_TURNS",
        "NAV_LAND",
        "mission_type",
    ):
        assert required_term in combined
