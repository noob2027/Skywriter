"""Pure structural validation for editable and complete missions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from skywriter.domain.mission import (
    CURRENT_MISSION_SCHEMA_VERSION,
    CircleAction,
    CircleDirection,
    GeoPoint,
    HoldAction,
    LandAction,
    Mission,
    MissionSettings,
    ProceedAction,
)


class ValidationMode(StrEnum):
    """Structural rules appropriate to the current mission lifecycle stage."""

    DRAFT = "draft"
    COMPLETE = "complete"


class ValidationSeverity(StrEnum):
    """Severity of a validation finding."""

    ERROR = "error"


class ValidationCode(StrEnum):
    """Stable machine-readable structural validation categories."""

    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    INVALID_MISSION_ID = "invalid_mission_id"
    INVALID_SETTINGS = "invalid_settings"
    INVALID_TAKEOFF_ALTITUDE = "invalid_takeoff_altitude"
    INVALID_CRUISE_SPEED = "invalid_cruise_speed"
    OBSTACLE_WARNING_NOT_ACKNOWLEDGED = "obstacle_warning_not_acknowledged"
    UNKNOWN_ACTION = "unknown_action"
    INVALID_POINT = "invalid_point"
    INVALID_LATITUDE = "invalid_latitude"
    INVALID_LONGITUDE = "invalid_longitude"
    INVALID_ALTITUDE = "invalid_altitude"
    INVALID_HOLD_TIME = "invalid_hold_time"
    INVALID_CIRCLE_RADIUS = "invalid_circle_radius"
    INVALID_CIRCLE_TURNS = "invalid_circle_turns"
    INVALID_CIRCLE_DIRECTION = "invalid_circle_direction"
    LAND_REQUIRED = "land_required"
    LAND_NOT_LAST = "land_not_last"
    DUPLICATE_LAND = "duplicate_land"


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """A presentation-neutral structural validation result."""

    path: str
    code: ValidationCode
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR


class MissionValidationError(ValueError):
    """Raised when a caller requires a structurally valid mission."""

    def __init__(self, findings: tuple[ValidationFinding, ...]) -> None:
        self.findings = findings
        summary = "; ".join(f"{finding.path}: {finding.message}" for finding in findings)
        super().__init__(summary or "mission validation failed")


def validate_mission(
    mission: Mission, mode: ValidationMode = ValidationMode.DRAFT
) -> tuple[ValidationFinding, ...]:
    """Return all deterministic structural findings for ``mission``."""

    if not isinstance(mode, ValidationMode):
        raise TypeError("mode must be a ValidationMode")

    findings: list[ValidationFinding] = []
    if (
        isinstance(mission.schema_version, bool)
        or not isinstance(mission.schema_version, int)
        or mission.schema_version != CURRENT_MISSION_SCHEMA_VERSION
    ):
        findings.append(
            _finding(
                "schema_version",
                ValidationCode.UNSUPPORTED_SCHEMA_VERSION,
                f"schema version must be {CURRENT_MISSION_SCHEMA_VERSION}",
            )
        )
    if not isinstance(mission.id, str) or not mission.id.strip():
        findings.append(
            _finding("id", ValidationCode.INVALID_MISSION_ID, "mission ID must be non-empty")
        )

    _validate_settings(mission.settings, findings)

    land_indexes = [
        index for index, action in enumerate(mission.actions) if isinstance(action, LandAction)
    ]
    if len(land_indexes) > 1:
        findings.append(_finding("actions", ValidationCode.DUPLICATE_LAND, "Land must be unique"))
    for index in land_indexes:
        if index != len(mission.actions) - 1:
            findings.append(
                _finding(
                    f"actions[{index}]",
                    ValidationCode.LAND_NOT_LAST,
                    "Land must be the final action",
                )
            )
    if mode is ValidationMode.COMPLETE and len(land_indexes) != 1:
        findings.append(
            _finding(
                "actions",
                ValidationCode.LAND_REQUIRED,
                "a complete mission requires exactly one final Land",
            )
        )

    for index, action in enumerate(mission.actions):
        _validate_action(action, index, findings)

    return tuple(findings)


def validate_draft(mission: Mission) -> tuple[ValidationFinding, ...]:
    """Validate an editable draft, permitting Land to be absent."""

    return validate_mission(mission, ValidationMode.DRAFT)


def validate_complete(mission: Mission) -> tuple[ValidationFinding, ...]:
    """Validate a complete/uploadable mission, requiring final Land."""

    return validate_mission(mission, ValidationMode.COMPLETE)


def require_valid_mission(mission: Mission, mode: ValidationMode = ValidationMode.DRAFT) -> Mission:
    """Return ``mission`` or raise ``MissionValidationError`` with all findings."""

    findings = validate_mission(mission, mode)
    if findings:
        raise MissionValidationError(findings)
    return mission


def is_valid_mission(mission: Mission, mode: ValidationMode = ValidationMode.DRAFT) -> bool:
    """Whether a mission has no structural findings in the requested mode."""

    return not validate_mission(mission, mode)


def _validate_settings(settings: object, findings: list[ValidationFinding]) -> None:
    if not isinstance(settings, MissionSettings):
        findings.append(
            _finding(
                "settings", ValidationCode.INVALID_SETTINGS, "settings must be MissionSettings"
            )
        )
        return
    if not _is_finite_number(settings.takeoff_altitude_m):
        findings.append(
            _finding(
                "settings.takeoff_altitude_m",
                ValidationCode.INVALID_TAKEOFF_ALTITUDE,
                "takeoff altitude must be a finite number",
            )
        )
    if not _is_positive_finite_number(settings.cruise_speed_m_s):
        findings.append(
            _finding(
                "settings.cruise_speed_m_s",
                ValidationCode.INVALID_CRUISE_SPEED,
                "cruise speed must be a positive finite number",
            )
        )
    if settings.obstacle_warning_acknowledged is not True:
        findings.append(
            _finding(
                "settings.obstacle_warning_acknowledged",
                ValidationCode.OBSTACLE_WARNING_NOT_ACKNOWLEDGED,
                "obstacle warning acknowledgment is required",
            )
        )


def _validate_action(action: object, index: int, findings: list[ValidationFinding]) -> None:
    path = f"actions[{index}]"
    if not isinstance(action, ProceedAction | HoldAction | CircleAction | LandAction):
        findings.append(
            _finding(path, ValidationCode.UNKNOWN_ACTION, "action type is not supported")
        )
        return

    _validate_point(action.point, f"{path}.point", findings)
    if isinstance(action, LandAction):
        if not _is_finite_number(action.approach_altitude_m):
            findings.append(
                _finding(
                    f"{path}.approach_altitude_m",
                    ValidationCode.INVALID_ALTITUDE,
                    "approach altitude must be a finite number",
                )
            )
        return

    if not _is_finite_number(action.altitude_m):
        findings.append(
            _finding(
                f"{path}.altitude_m",
                ValidationCode.INVALID_ALTITUDE,
                "altitude must be a finite number",
            )
        )
    if isinstance(action, HoldAction) and not _is_positive_finite_number(action.hold_time_s):
        findings.append(
            _finding(
                f"{path}.hold_time_s",
                ValidationCode.INVALID_HOLD_TIME,
                "hold time must be a positive finite number",
            )
        )
    if isinstance(action, CircleAction):
        if not _is_positive_finite_number(action.radius_m):
            findings.append(
                _finding(
                    f"{path}.radius_m",
                    ValidationCode.INVALID_CIRCLE_RADIUS,
                    "Circle radius must be a positive finite number",
                )
            )
        if isinstance(action.turns, bool) or not isinstance(action.turns, int) or action.turns != 1:
            findings.append(
                _finding(
                    f"{path}.turns",
                    ValidationCode.INVALID_CIRCLE_TURNS,
                    "Circle must contain exactly one turn",
                )
            )
        if action.direction is not CircleDirection.CLOCKWISE:
            findings.append(
                _finding(
                    f"{path}.direction",
                    ValidationCode.INVALID_CIRCLE_DIRECTION,
                    "Circle direction must be clockwise",
                )
            )


def _validate_point(point: object, path: str, findings: list[ValidationFinding]) -> None:
    if not isinstance(point, GeoPoint):
        findings.append(_finding(path, ValidationCode.INVALID_POINT, "point must be GeoPoint"))
        return
    if not _is_finite_number(point.latitude_deg) or not -90.0 <= point.latitude_deg <= 90.0:
        findings.append(
            _finding(
                f"{path}.latitude_deg",
                ValidationCode.INVALID_LATITUDE,
                "latitude must be finite and between -90 and 90 degrees",
            )
        )
    if not _is_finite_number(point.longitude_deg) or not -180.0 <= point.longitude_deg <= 180.0:
        findings.append(
            _finding(
                f"{path}.longitude_deg",
                ValidationCode.INVALID_LONGITUDE,
                "longitude must be finite and between -180 and 180 degrees",
            )
        )


def _is_finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_positive_finite_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _finding(path: str, code: ValidationCode, message: str) -> ValidationFinding:
    return ValidationFinding(path=path, code=code, message=message)
