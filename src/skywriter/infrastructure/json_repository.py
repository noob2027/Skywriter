"""Strict, versioned JSON persistence for pure beginner missions."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias, cast

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
    ValidationMode,
    require_valid_mission,
)

JsonObject: TypeAlias = dict[str, object]


class MissionJsonError(ValueError):
    """Raised for malformed, unsupported, or semantically invalid mission JSON."""


class MissionRepositoryError(OSError):
    """Raised when mission storage cannot be read or atomically replaced."""


class JsonMissionRepository:
    """Load and atomically save editable mission documents."""

    def load(self, path: str | os.PathLike[str]) -> Mission:
        source = Path(path)
        try:
            document = source.read_text(encoding="utf-8")
        except OSError as error:
            raise MissionRepositoryError(f"could not read mission file: {source}") from error
        return deserialize_mission(document)

    def save(self, path: str | os.PathLike[str], mission: Mission) -> None:
        destination = Path(path)
        document = serialize_mission(mission)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(document)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        except OSError as error:
            raise MissionRepositoryError(
                f"could not atomically write mission file: {destination}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


# Compatibility-friendly descriptive alias; both names identify the same repository contract.
MissionJsonRepository = JsonMissionRepository


def serialize_mission(mission: Mission) -> str:
    """Serialize a structurally valid draft as canonical human-readable JSON."""

    try:
        require_valid_mission(mission, ValidationMode.DRAFT)
    except MissionValidationError as error:
        raise MissionJsonError(f"mission is not a valid draft: {error}") from error
    try:
        return (
            json.dumps(
                mission_to_document(mission),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )
    except (TypeError, ValueError) as error:
        raise MissionJsonError("mission cannot be encoded as JSON") from error


def deserialize_mission(document: str) -> Mission:
    """Parse strict JSON and structurally revalidate the resulting editable mission."""

    try:
        value = json.loads(
            document,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite_constant,
        )
    except (json.JSONDecodeError, MissionJsonError) as error:
        if isinstance(error, MissionJsonError):
            raise
        raise MissionJsonError(f"invalid mission JSON: {error.msg}") from error
    return mission_from_document(value)


def mission_to_document(mission: Mission) -> JsonObject:
    """Convert a mission to the exact version-1 persistence document."""

    return {
        "schema_version": mission.schema_version,
        "id": mission.id,
        "settings": {
            "takeoff_altitude_m": mission.settings.takeoff_altitude_m,
            "cruise_speed_m_s": mission.settings.cruise_speed_m_s,
            "obstacle_warning_acknowledged": mission.settings.obstacle_warning_acknowledged,
        },
        "actions": [_action_to_document(action) for action in mission.actions],
    }


def mission_from_document(value: object) -> Mission:
    """Decode an already-parsed strict version-1 document."""

    root = _expect_object(value, "$")
    _require_keys(root, {"schema_version", "id", "settings", "actions"}, "$")

    schema_version = _expect_int(root["schema_version"], "$.schema_version")
    if schema_version != CURRENT_MISSION_SCHEMA_VERSION:
        raise MissionJsonError(
            f"$.schema_version: unsupported schema version {schema_version}; "
            f"expected {CURRENT_MISSION_SCHEMA_VERSION}"
        )
    mission_id = _expect_string(root["id"], "$.id")
    settings = _settings_from_document(root["settings"])
    actions_value = root["actions"]
    if not isinstance(actions_value, list):
        raise MissionJsonError("$.actions: expected an array")
    actions = tuple(
        _action_from_document(action, index) for index, action in enumerate(actions_value)
    )
    mission = Mission(
        schema_version=schema_version,
        id=mission_id,
        settings=settings,
        actions=actions,
    )
    try:
        return require_valid_mission(mission, ValidationMode.DRAFT)
    except MissionValidationError as error:
        raise MissionJsonError(f"loaded mission is not a valid draft: {error}") from error


def load_mission(path: str | os.PathLike[str]) -> Mission:
    """Convenience wrapper using ``JsonMissionRepository``."""

    return JsonMissionRepository().load(path)


def save_mission(path: str | os.PathLike[str], mission: Mission) -> None:
    """Convenience wrapper using ``JsonMissionRepository``."""

    JsonMissionRepository().save(path, mission)


def _settings_from_document(value: object) -> MissionSettings:
    settings = _expect_object(value, "$.settings")
    _require_keys(
        settings,
        {"takeoff_altitude_m", "cruise_speed_m_s", "obstacle_warning_acknowledged"},
        "$.settings",
    )
    acknowledgment = settings["obstacle_warning_acknowledged"]
    if not isinstance(acknowledgment, bool):
        raise MissionJsonError("$.settings.obstacle_warning_acknowledged: expected a boolean")
    return MissionSettings(
        takeoff_altitude_m=_expect_number(
            settings["takeoff_altitude_m"], "$.settings.takeoff_altitude_m"
        ),
        cruise_speed_m_s=_expect_number(
            settings["cruise_speed_m_s"], "$.settings.cruise_speed_m_s"
        ),
        obstacle_warning_acknowledged=acknowledgment,
    )


def _action_to_document(action: MissionAction) -> JsonObject:
    point = {
        "latitude_deg": action.point.latitude_deg,
        "longitude_deg": action.point.longitude_deg,
    }
    if isinstance(action, ProceedAction):
        return {"type": "proceed", "point": point, "altitude_m": action.altitude_m}
    if isinstance(action, HoldAction):
        return {
            "type": "hold",
            "point": point,
            "altitude_m": action.altitude_m,
            "hold_time_s": action.hold_time_s,
        }
    if isinstance(action, CircleAction):
        return {
            "type": "circle",
            "point": point,
            "altitude_m": action.altitude_m,
            "radius_m": action.radius_m,
            "turns": action.turns,
            "direction": action.direction.value,
        }
    if isinstance(action, LandAction):
        return {
            "type": "land",
            "point": point,
            "approach_altitude_m": action.approach_altitude_m,
        }
    raise TypeError("unsupported mission action")


def _action_from_document(value: object, index: int) -> MissionAction:
    path = f"$.actions[{index}]"
    action = _expect_object(value, path)
    if "type" not in action:
        raise MissionJsonError(f"{path}: missing field: type")
    discriminator = _expect_string(action["type"], f"{path}.type")

    expected_fields: dict[str, set[str]] = {
        "proceed": {"type", "point", "altitude_m"},
        "hold": {"type", "point", "altitude_m", "hold_time_s"},
        "circle": {
            "type",
            "point",
            "altitude_m",
            "radius_m",
            "turns",
            "direction",
        },
        "land": {"type", "point", "approach_altitude_m"},
    }
    if discriminator not in expected_fields:
        raise MissionJsonError(f"{path}.type: unknown action {discriminator!r}")
    _require_keys(action, expected_fields[discriminator], path)
    point = _point_from_document(action["point"], f"{path}.point")

    if discriminator == "proceed":
        return ProceedAction(point, _expect_number(action["altitude_m"], f"{path}.altitude_m"))
    if discriminator == "hold":
        return HoldAction(
            point,
            _expect_number(action["altitude_m"], f"{path}.altitude_m"),
            _expect_number(action["hold_time_s"], f"{path}.hold_time_s"),
        )
    if discriminator == "circle":
        turns = _expect_int(action["turns"], f"{path}.turns")
        direction_value = _expect_string(action["direction"], f"{path}.direction")
        try:
            direction = CircleDirection(direction_value)
        except ValueError as error:
            raise MissionJsonError(
                f"{path}.direction: unknown direction {direction_value!r}"
            ) from error
        return CircleAction(
            point,
            _expect_number(action["altitude_m"], f"{path}.altitude_m"),
            _expect_number(action["radius_m"], f"{path}.radius_m"),
            turns=turns,
            direction=direction,
        )
    return LandAction(
        point,
        _expect_number(action["approach_altitude_m"], f"{path}.approach_altitude_m"),
    )


def _point_from_document(value: object, path: str) -> GeoPoint:
    point = _expect_object(value, path)
    _require_keys(point, {"latitude_deg", "longitude_deg"}, path)
    return GeoPoint(
        latitude_deg=_expect_number(point["latitude_deg"], f"{path}.latitude_deg"),
        longitude_deg=_expect_number(point["longitude_deg"], f"{path}.longitude_deg"),
    )


def _strict_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise MissionJsonError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise MissionJsonError(f"non-finite JSON number is not allowed: {value}")


def _expect_object(value: object, path: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise MissionJsonError(f"{path}: expected an object")
    if not all(isinstance(key, str) for key in value):
        raise MissionJsonError(f"{path}: object keys must be strings")
    return cast(JsonObject, value)


def _expect_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MissionJsonError(f"{path}: expected a number")
    return float(value)


def _expect_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MissionJsonError(f"{path}: expected an integer")
    return value


def _expect_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise MissionJsonError(f"{path}: expected a string")
    return value


def _require_keys(value: JsonObject, expected: set[str], path: str) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise MissionJsonError(f"{path}: missing field(s): {', '.join(sorted(missing))}")
    if unknown:
        raise MissionJsonError(f"{path}: unknown field(s): {', '.join(sorted(unknown))}")
