"""Immutable, transport-neutral compiled mission values.

The numeric values mirror the MAVLink mission fields needed by SKYWriter, but
the domain deliberately does not import a MAVLink library.  The enums are
closed to the beginner mission whitelist so unsupported command IDs cannot be
constructed through the normal compiled-mission API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum


class MissionFrame(IntEnum):
    """The sole coordinate frame approved for compiled beginner missions."""

    GLOBAL_RELATIVE_ALT_INT = 6


class MissionCommand(IntEnum):
    """Closed command whitelist for compiled beginner missions."""

    NAV_WAYPOINT = 16
    NAV_LOITER_TURNS = 18
    NAV_LOITER_TIME = 19
    NAV_LAND = 21
    NAV_TAKEOFF = 22
    DO_CHANGE_SPEED = 178


class MissionType(IntEnum):
    """The only mission-protocol collection SKYWriter compiles."""

    MISSION = 0


class SpeedType(IntEnum):
    """The only speed setpoint type used by the beginner mission compiler."""

    GROUNDSPEED = 1


# Task 005A must verify these exact assumptions against the selected stock
# ArduCopter release and its pinned MAVLink dialect before connected work starts.
COMPATIBILITY_ASSUMPTIONS: tuple[str, ...] = (
    "ArduCopter accepts MAV_FRAME_GLOBAL_RELATIVE_ALT_INT (6) for every compiled item, "
    "including DO_CHANGE_SPEED, with MISSION_ITEM_INT integer coordinates.",
    "NAV_TAKEOFF with zero latitude and longitude uses the established home/launch "
    "location, and zero params 1-4 retain the pinned vehicle defaults.",
    "DO_CHANGE_SPEED uses SPEED_TYPE_GROUNDSPEED (1), param2 in m/s, param3=-1 for no "
    "throttle change, and zero for the remaining unused fields.",
    "NAV_WAYPOINT and NAV_LOITER_TIME use zero for unused delay, radius, heading, and "
    "cross-track fields without changing the intended Copter behavior.",
    "NAV_LOITER_TURNS uses param1=1 and a positive param3 radius for one clockwise turn; "
    "any vehicle-side radius normalization must be documented rather than compiler-clamped.",
    "NAV_LAND uses zero params and altitude with the selected landing coordinates "
    "after an approach waypoint at the same coordinates.",
    "Selected coordinates are passed through exactly, including zero-degree latitude or "
    "longitude; the pinned target must document any current-location sentinel behavior.",
    "Structurally valid finite altitudes and positive speed, time, and radius values are "
    "not bounded or normalized by the compiler; target acceptance remains to be tested.",
    "The first item has current=true, later items current=false, every item has "
    "autocontinue=true, and mission_type is MAV_MISSION_TYPE_MISSION (0).",
)


@dataclass(frozen=True, slots=True)
class CompiledMissionItem:
    """One immutable mission item at the integer-coordinate boundary."""

    sequence: int
    frame: MissionFrame
    command: MissionCommand
    current: bool
    autocontinue: bool
    param1: float
    param2: float
    param3: float
    param4: float
    latitude_e7: int
    longitude_e7: int
    altitude_m: float
    mission_type: MissionType

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if not isinstance(self.frame, MissionFrame):
            raise TypeError("frame must be a MissionFrame")
        if not isinstance(self.command, MissionCommand):
            raise TypeError("command must be a MissionCommand")
        if not isinstance(self.current, bool):
            raise TypeError("current must be a boolean")
        if not isinstance(self.autocontinue, bool):
            raise TypeError("autocontinue must be a boolean")
        for name in ("param1", "param2", "param3", "param4", "altitude_m"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            ):
                raise TypeError(f"{name} must be a finite number")
            object.__setattr__(self, name, float(value))
        _require_int32(self.latitude_e7, "latitude_e7")
        _require_int32(self.longitude_e7, "longitude_e7")
        if not isinstance(self.mission_type, MissionType):
            raise TypeError("mission_type must be a MissionType")


@dataclass(frozen=True, slots=True)
class CompiledMission:
    """A deterministic sequence of immutable, transport-neutral mission items."""

    items: tuple[CompiledMissionItem, ...]

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if not items:
            raise ValueError("compiled mission must contain at least one item")
        if not all(isinstance(item, CompiledMissionItem) for item in items):
            raise TypeError("items must contain only CompiledMissionItem values")
        expected_sequences = tuple(range(len(items)))
        actual_sequences = tuple(item.sequence for item in items)
        if actual_sequences != expected_sequences:
            raise ValueError("compiled mission sequences must start at zero with no gaps")
        object.__setattr__(self, "items", items)


def _require_int32(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not -(2**31) <= value <= 2**31 - 1:
        raise ValueError(f"{name} must fit a signed 32-bit mission field")
