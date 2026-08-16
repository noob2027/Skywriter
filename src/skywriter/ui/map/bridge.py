"""Strict versioned messages for the isolated Python-to-map-content boundary."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, TypeAlias, cast

from PySide6.QtCore import QObject, Signal, Slot

from skywriter.domain.mission import GeoPoint

BRIDGE_SCHEMA_VERSION = 1
JsonObject: TypeAlias = dict[str, object]


class MapBridgeError(ValueError):
    """Raised when untrusted map content sends an invalid bridge message."""


@dataclass(frozen=True, slots=True)
class MapClicked:
    """A map-content click at a decimal-degree point."""

    point: GeoPoint


@dataclass(frozen=True, slots=True)
class PointDragged:
    """A coordinate drag for an existing presentation sequence."""

    index: int
    point: GeoPoint


@dataclass(frozen=True, slots=True)
class PointSelected:
    """Selection of an existing presentation sequence."""

    index: int


@dataclass(frozen=True, slots=True)
class ViewportChanged:
    """A sanitized viewport change from map content."""

    south_west: GeoPoint
    north_east: GeoPoint


MapIntent: TypeAlias = MapClicked | PointDragged | PointSelected | ViewportChanged


class RenderActionKind(StrEnum):
    """Closed action discriminators understood by map content."""

    PROCEED = "proceed"
    HOLD = "hold"
    CIRCLE = "circle"
    LAND = "land"


@dataclass(frozen=True, slots=True)
class RenderAction:
    """Sanitized action data sent to map content."""

    sequence: int
    kind: RenderActionKind
    point: GeoPoint
    altitude_m: float
    hold_time_s: float | None = None
    radius_m: float | None = None
    selected: bool = False


@dataclass(frozen=True, slots=True)
class RenderModel:
    """Complete map render payload without application or vehicle state."""

    actions: tuple[RenderAction, ...] = ()
    pending_point: GeoPoint | None = None


class MapBridge(QObject):
    """Validate inbound JSON and publish typed map-only intents."""

    intent_received = Signal(object)
    message_rejected = Signal(str)
    render_message = Signal(str)

    @Slot(str)
    def receive_message(self, payload: str) -> None:
        """Validate one untrusted JSON message without propagating exceptions to JS."""

        try:
            intent = parse_map_intent(payload)
        except MapBridgeError as error:
            self.message_rejected.emit(str(error))
            return
        self.intent_received.emit(intent)

    def publish_render_model(self, model: RenderModel) -> None:
        """Send a sanitized, versioned render message to a connected map host."""

        self.render_message.emit(encode_render_message(model))


def parse_map_intent(payload: str) -> MapIntent:
    """Parse one strict map-content message into a typed intent."""

    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except json.JSONDecodeError as error:
        raise MapBridgeError(f"invalid bridge JSON: {error.msg}") from error

    root = _expect_object(value, "$")
    if "type" not in root:
        raise MapBridgeError("$: missing field: type")
    message_type = _expect_string(root["type"], "$.type")
    expected_fields: dict[str, set[str]] = {
        "map_clicked": {"schema_version", "type", "point"},
        "point_dragged": {"schema_version", "type", "index", "point"},
        "point_selected": {"schema_version", "type", "index"},
        "viewport_changed": {
            "schema_version",
            "type",
            "south_west",
            "north_east",
        },
    }
    if message_type not in expected_fields:
        raise MapBridgeError(f"$.type: unknown map intent {message_type!r}")
    _require_keys(root, expected_fields[message_type], "$")
    version = _expect_index(root["schema_version"], "$.schema_version", allow_zero=False)
    if version != BRIDGE_SCHEMA_VERSION:
        raise MapBridgeError(
            f"$.schema_version: expected {BRIDGE_SCHEMA_VERSION}, received {version}"
        )

    if message_type == "map_clicked":
        return MapClicked(_point_from_value(root["point"], "$.point"))
    if message_type == "point_dragged":
        return PointDragged(
            _expect_index(root["index"], "$.index"),
            _point_from_value(root["point"], "$.point"),
        )
    if message_type == "point_selected":
        return PointSelected(_expect_index(root["index"], "$.index"))

    south_west = _point_from_value(root["south_west"], "$.south_west")
    north_east = _point_from_value(root["north_east"], "$.north_east")
    if south_west.latitude_deg > north_east.latitude_deg:
        raise MapBridgeError("$: south_west latitude must not exceed north_east latitude")
    if south_west.longitude_deg > north_east.longitude_deg:
        raise MapBridgeError("$: south_west longitude must not exceed north_east longitude")
    return ViewportChanged(south_west=south_west, north_east=north_east)


def encode_render_message(model: RenderModel) -> str:
    """Encode a typed render model into strict JSON for map content."""

    sequences: set[int] = set()
    actions: list[JsonObject] = []
    for action in model.actions:
        if action.sequence < 1 or action.sequence in sequences:
            raise MapBridgeError("render sequences must be unique positive integers")
        sequences.add(action.sequence)
        _validate_point(action.point, f"actions[{action.sequence - 1}].point")
        if not _is_finite_number(action.altitude_m):
            raise MapBridgeError("render altitude must be finite")
        document: JsonObject = {
            "sequence": action.sequence,
            "kind": action.kind.value,
            "point": _point_to_value(action.point),
            "altitude_m": action.altitude_m,
            "selected": action.selected,
        }
        if action.kind is RenderActionKind.HOLD:
            if not _is_positive_finite_number(action.hold_time_s):
                raise MapBridgeError("Hold render data requires positive hold_time_s")
            document["hold_time_s"] = action.hold_time_s
        elif action.hold_time_s is not None:
            raise MapBridgeError("hold_time_s is only valid for Hold render data")
        if action.kind is RenderActionKind.CIRCLE:
            if not _is_positive_finite_number(action.radius_m):
                raise MapBridgeError("Circle render data requires positive radius_m")
            document["radius_m"] = action.radius_m
            document["turns"] = 1
            document["direction"] = "clockwise"
        elif action.radius_m is not None:
            raise MapBridgeError("radius_m is only valid for Circle render data")
        actions.append(document)

    pending: JsonObject | None = None
    if model.pending_point is not None:
        _validate_point(model.pending_point, "pending_point")
        pending = _point_to_value(model.pending_point)
    return json.dumps(
        {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "type": "render_mission",
            "actions": actions,
            "pending_point": pending,
        },
        separators=(",", ":"),
        allow_nan=False,
    )


def _point_from_value(value: object, path: str) -> GeoPoint:
    point = _expect_object(value, path)
    _require_keys(point, {"latitude_deg", "longitude_deg"}, path)
    parsed = GeoPoint(
        _expect_number(point["latitude_deg"], f"{path}.latitude_deg"),
        _expect_number(point["longitude_deg"], f"{path}.longitude_deg"),
    )
    _validate_point(parsed, path)
    return parsed


def _point_to_value(point: GeoPoint) -> JsonObject:
    return {"latitude_deg": point.latitude_deg, "longitude_deg": point.longitude_deg}


def _validate_point(point: GeoPoint, path: str) -> None:
    if not _is_finite_number(point.latitude_deg) or not -90 <= point.latitude_deg <= 90:
        raise MapBridgeError(f"{path}.latitude_deg: expected a finite coordinate -90..90")
    if not _is_finite_number(point.longitude_deg) or not -180 <= point.longitude_deg <= 180:
        raise MapBridgeError(f"{path}.longitude_deg: expected a finite coordinate -180..180")


def _strict_object(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise MapBridgeError(f"duplicate bridge field: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> NoReturn:
    raise MapBridgeError(f"non-finite bridge number is not allowed: {value}")


def _expect_object(value: object, path: str) -> JsonObject:
    if not isinstance(value, dict):
        raise MapBridgeError(f"{path}: expected an object")
    return cast(JsonObject, value)


def _expect_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise MapBridgeError(f"{path}: expected a string")
    return value


def _expect_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise MapBridgeError(f"{path}: expected a finite number")
    return float(value)


def _expect_index(value: object, path: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MapBridgeError(f"{path}: expected an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise MapBridgeError(f"{path}: expected an integer >= {minimum}")
    return value


def _require_keys(value: JsonObject, expected: set[str], path: str) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise MapBridgeError(f"{path}: missing field(s): {', '.join(sorted(missing))}")
    if unknown:
        raise MapBridgeError(f"{path}: unknown field(s): {', '.join(sorted(unknown))}")


def _is_finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_positive_finite_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )
