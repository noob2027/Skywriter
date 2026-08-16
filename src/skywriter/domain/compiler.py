"""Pure deterministic compilation from beginner missions to approved items."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import assert_never

from skywriter.domain.compiled import (
    CompiledMission,
    CompiledMissionItem,
    MissionCommand,
    MissionFrame,
    MissionType,
    SpeedType,
)
from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    Mission,
    MissionAction,
    ProceedAction,
)
from skywriter.domain.validation import ValidationMode, require_valid_mission

_COORDINATE_SCALE = Decimal(10_000_000)
_ZERO_COORDINATE = 0
_ZERO = 0.0


class MissionCompiler:
    """Compile a structurally valid complete mission without side effects."""

    def compile(self, mission: Mission) -> CompiledMission:
        """Return the exact approved native-item sequence for ``mission``.

        Drafts and structurally invalid missions raise ``MissionValidationError``
        from the frozen mission-validation contract.
        """

        require_valid_mission(mission, ValidationMode.COMPLETE)
        items: list[CompiledMissionItem] = []

        self._append_item(
            items,
            command=MissionCommand.NAV_TAKEOFF,
            altitude_m=mission.settings.takeoff_altitude_m,
        )
        self._append_item(
            items,
            command=MissionCommand.DO_CHANGE_SPEED,
            param1=float(SpeedType.GROUNDSPEED),
            param2=mission.settings.cruise_speed_m_s,
            param3=-1.0,
        )

        for action in mission.actions:
            self._compile_action(items, action)

        return CompiledMission(tuple(items))

    def _compile_action(self, items: list[CompiledMissionItem], action: MissionAction) -> None:
        if isinstance(action, ProceedAction):
            self._append_item(
                items,
                command=MissionCommand.NAV_WAYPOINT,
                point=action.point,
                altitude_m=action.altitude_m,
            )
            return
        if isinstance(action, HoldAction):
            self._append_item(
                items,
                command=MissionCommand.NAV_LOITER_TIME,
                point=action.point,
                altitude_m=action.altitude_m,
                param1=action.hold_time_s,
            )
            return
        if isinstance(action, CircleAction):
            self._append_item(
                items,
                command=MissionCommand.NAV_LOITER_TURNS,
                point=action.point,
                altitude_m=action.altitude_m,
                param1=float(action.turns),
                param3=action.radius_m,
            )
            return
        if isinstance(action, LandAction):
            self._append_item(
                items,
                command=MissionCommand.NAV_WAYPOINT,
                point=action.point,
                altitude_m=action.approach_altitude_m,
            )
            self._append_item(
                items,
                command=MissionCommand.NAV_LAND,
                point=action.point,
            )
            return
        assert_never(action)

    @staticmethod
    def _append_item(
        items: list[CompiledMissionItem],
        *,
        command: MissionCommand,
        point: GeoPoint | None = None,
        altitude_m: float = _ZERO,
        param1: float = _ZERO,
        param2: float = _ZERO,
        param3: float = _ZERO,
        param4: float = _ZERO,
    ) -> None:
        latitude_e7, longitude_e7 = _compile_coordinates(point)
        sequence = len(items)
        items.append(
            CompiledMissionItem(
                sequence=sequence,
                frame=MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                command=command,
                current=sequence == 0,
                autocontinue=True,
                param1=float(param1),
                param2=float(param2),
                param3=float(param3),
                param4=float(param4),
                latitude_e7=latitude_e7,
                longitude_e7=longitude_e7,
                altitude_m=float(altitude_m),
                mission_type=MissionType.MISSION,
            )
        )


def _compile_coordinates(point: GeoPoint | None) -> tuple[int, int]:
    if point is None:
        return _ZERO_COORDINATE, _ZERO_COORDINATE
    return _degrees_to_e7(point.latitude_deg), _degrees_to_e7(point.longitude_deg)


def _degrees_to_e7(value: float) -> int:
    """Scale decimal degrees once, rounding a half unit away from zero."""

    scaled = Decimal(str(value)) * _COORDINATE_SCALE
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))
