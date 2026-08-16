"""Immutable beginner-mission values and editing operations.

The mission model intentionally contains no protocol, connection, compilation, or
verification state.  Takeoff is represented exactly once by ``MissionSettings``;
all entries in ``Mission.actions`` are post-takeoff actions in creation order.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TypeAlias
from uuid import uuid4

CURRENT_MISSION_SCHEMA_VERSION = 1


class CircleDirection(StrEnum):
    """The only Circle direction supported by the beginner mission contract."""

    CLOCKWISE = "clockwise"


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """A decimal-degree geographic coordinate."""

    latitude_deg: float
    longitude_deg: float


@dataclass(frozen=True, slots=True)
class MissionSettings:
    """The unique Takeoff setup and mission-wide settings."""

    takeoff_altitude_m: float
    cruise_speed_m_s: float
    obstacle_warning_acknowledged: bool


@dataclass(frozen=True, slots=True)
class ProceedAction:
    """Proceed to a point at a relative altitude."""

    point: GeoPoint
    altitude_m: float


@dataclass(frozen=True, slots=True)
class HoldAction:
    """Hold at a point for a positive duration."""

    point: GeoPoint
    altitude_m: float
    hold_time_s: float


@dataclass(frozen=True, slots=True)
class CircleAction:
    """Circle a point once in the supported clockwise direction."""

    point: GeoPoint
    altitude_m: float
    radius_m: float
    turns: int = 1
    direction: CircleDirection = CircleDirection.CLOCKWISE


@dataclass(frozen=True, slots=True)
class LandAction:
    """Approach and land at the selected point."""

    point: GeoPoint
    approach_altitude_m: float


MissionAction: TypeAlias = ProceedAction | HoldAction | CircleAction | LandAction


class MissionEditError(ValueError):
    """Raised when an edit would violate the closed-mission ordering contract."""


@dataclass(frozen=True, slots=True)
class Mission:
    """An immutable mission draft whose actions retain creation order."""

    settings: MissionSettings
    actions: tuple[MissionAction, ...] = ()
    id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = CURRENT_MISSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Accept a sequence at construction boundaries while storing one immutable shape.
        object.__setattr__(self, "actions", tuple(self.actions))

    @classmethod
    def create(cls, settings: MissionSettings, *, mission_id: str | None = None) -> Mission:
        """Create an empty draft with an optional caller-provided stable ID."""

        if mission_id is None:
            return cls(settings=settings)
        return cls(settings=settings, id=mission_id)

    @property
    def is_closed(self) -> bool:
        """Whether the draft currently ends in Land and cannot be appended to."""

        return bool(self.actions) and isinstance(self.actions[-1], LandAction)

    def append_action(self, action: MissionAction) -> Mission:
        """Append an action unless Land has already closed the mission."""

        self._require_action(action)
        if self.is_closed:
            raise MissionEditError("remove Land before appending another action")
        if isinstance(action, LandAction) and any(
            isinstance(existing, LandAction) for existing in self.actions
        ):
            raise MissionEditError("Land must be unique")
        return replace(self, actions=(*self.actions, action))

    def replace_action(self, index: int, action: MissionAction) -> Mission:
        """Replace an action without permitting duplicate or non-final Land."""

        self._require_action(action)
        self._require_index(index)
        if isinstance(action, LandAction):
            if index != len(self.actions) - 1:
                raise MissionEditError("Land must be the final action")
            if any(
                other_index != index and isinstance(existing, LandAction)
                for other_index, existing in enumerate(self.actions)
            ):
                raise MissionEditError("Land must be unique")

        updated = list(self.actions)
        updated[index] = action
        return replace(self, actions=tuple(updated))

    def delete_action(self, index: int) -> Mission:
        """Delete an action; deleting Land reopens the mission."""

        self._require_index(index)
        return replace(self, actions=self.actions[:index] + self.actions[index + 1 :])

    def move_action(self, index: int, point: GeoPoint) -> Mission:
        """Move an action's coordinate while retaining its order and other fields."""

        self._require_index(index)
        if not isinstance(point, GeoPoint):
            raise TypeError("point must be a GeoPoint")
        action = self.actions[index]
        return self.replace_action(index, replace(action, point=point))

    def move_action_point(self, index: int, point: GeoPoint) -> Mission:
        """Explicit alias for coordinate-drag integrations."""

        return self.move_action(index, point)

    def remove_land(self) -> Mission:
        """Remove the final Land action, reopening the mission if it was closed."""

        if not self.is_closed:
            return self
        return replace(self, actions=self.actions[:-1])

    def undo_last_action(self) -> Mission:
        """Remove the most recently created action when one exists."""

        if not self.actions:
            return self
        return replace(self, actions=self.actions[:-1])

    def clear_actions(self) -> Mission:
        """Return an empty draft while retaining settings and stable mission ID."""

        if not self.actions:
            return self
        return replace(self, actions=())

    def _require_index(self, index: int) -> None:
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("action index must be an integer")
        if index < 0 or index >= len(self.actions):
            raise IndexError("action index out of range")

    @staticmethod
    def _require_action(action: object) -> None:
        if not isinstance(action, ProceedAction | HoldAction | CircleAction | LandAction):
            raise TypeError("action must be a supported beginner mission action")
