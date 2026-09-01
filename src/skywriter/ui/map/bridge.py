"""Strict versioned messages for the isolated Python-to-map-content boundary."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, TypeAlias, TypeVar, cast

from PySide6.QtCore import Property, QObject, Signal, Slot

from skywriter.domain.mission import GeoPoint

BRIDGE_SCHEMA_VERSION = 2
JsonObject: TypeAlias = dict[str, object]
EnumT = TypeVar("EnumT", bound=StrEnum)
_DECIMAL_COORDINATE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")


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


class ProviderState(StrEnum):
    """Observable basemap states reported by mounted map content."""

    OFFLINE = "offline"
    LOADING = "loading"
    ONLINE = "online"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ProviderStatusChanged:
    """Correlated tile outcome counters for one deliberate provider attempt."""

    provider: TileProvider
    attempt_id: int
    state: ProviderState
    requested_tiles: int
    loaded_tiles: int
    error_tiles: int
    pending_tiles: int


@dataclass(frozen=True, slots=True)
class MapReady:
    """Proof that packaged Leaflet mounted into a visible map container."""

    leaflet_version: str
    container_width_px: float
    container_height_px: float


MapIntent: TypeAlias = (
    MapClicked | PointDragged | PointSelected | ViewportChanged | ProviderStatusChanged | MapReady
)


class RenderActionKind(StrEnum):
    """Closed action discriminators understood by map content."""

    PROCEED = "proceed"
    HOLD = "hold"
    CIRCLE = "circle"
    LAND = "land"


class TileProvider(StrEnum):
    """Closed basemap choices accepted by the isolated map content."""

    OFFLINE = "offline"
    OPENSTREETMAP = "openstreetmap"


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
    tile_provider: TileProvider = TileProvider.OFFLINE
    tile_attempt_id: int = 0
    drag_threshold_px: int = 10


class MapBridge(QObject):
    """Validate inbound JSON and publish typed map-only intents."""

    intent_received = Signal(object)
    message_rejected = Signal(str)
    render_message = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._current_render_message = encode_render_message(RenderModel())

    @Property(str, notify=render_message)
    def current_render_message(self) -> str:
        """Return the latest sanitized snapshot for late QWebChannel subscribers."""

        return self._current_render_message

    @Slot(str, result=str)
    def receive_message(self, payload: str) -> str:
        """Validate and synchronously emit one intent before acknowledging map content."""

        try:
            intent = parse_map_intent(payload)
        except MapBridgeError as error:
            self.message_rejected.emit(str(error))
            return "rejected"
        self.intent_received.emit(intent)
        return "accepted"

    def publish_render_model(self, model: RenderModel) -> None:
        """Send a sanitized, versioned render message to a connected map host."""

        self._current_render_message = encode_render_message(model)
        self.render_message.emit(self._current_render_message)


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
        "provider_status_changed": {
            "schema_version",
            "type",
            "provider",
            "attempt_id",
            "state",
            "requested_tiles",
            "loaded_tiles",
            "error_tiles",
            "pending_tiles",
        },
        "map_ready": {
            "schema_version",
            "type",
            "leaflet_version",
            "container_width_px",
            "container_height_px",
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
    if message_type == "provider_status_changed":
        provider = _expect_enum(TileProvider, root["provider"], "$.provider")
        state = _expect_enum(ProviderState, root["state"], "$.state")
        attempt_id = _expect_index(root["attempt_id"], "$.attempt_id")
        requested = _expect_index(root["requested_tiles"], "$.requested_tiles")
        loaded = _expect_index(root["loaded_tiles"], "$.loaded_tiles")
        errors = _expect_index(root["error_tiles"], "$.error_tiles")
        pending = _expect_index(root["pending_tiles"], "$.pending_tiles")
        if loaded + errors + pending != requested:
            raise MapBridgeError("$: tile counters must add up to requested_tiles")
        if provider is TileProvider.OFFLINE:
            if state is not ProviderState.OFFLINE or any(
                (attempt_id, requested, loaded, errors, pending)
            ):
                raise MapBridgeError("$: offline provider status must have zero counters")
        elif state is ProviderState.OFFLINE or attempt_id < 1:
            raise MapBridgeError("$: network provider status requires a positive attempt_id")
        return ProviderStatusChanged(
            provider=provider,
            attempt_id=attempt_id,
            state=state,
            requested_tiles=requested,
            loaded_tiles=loaded,
            error_tiles=errors,
            pending_tiles=pending,
        )
    if message_type == "map_ready":
        leaflet_version = _expect_string(root["leaflet_version"], "$.leaflet_version")
        if leaflet_version != "1.9.4":
            raise MapBridgeError("$.leaflet_version: expected pinned Leaflet 1.9.4")
        width = _expect_number(root["container_width_px"], "$.container_width_px")
        height = _expect_number(root["container_height_px"], "$.container_height_px")
        if width <= 0 or height <= 0:
            raise MapBridgeError("$: mounted map container must have positive dimensions")
        return MapReady(leaflet_version, width, height)

    south_west = _point_from_value(root["south_west"], "$.south_west")
    north_east = _point_from_value(root["north_east"], "$.north_east")
    if south_west.latitude_deg > north_east.latitude_deg:
        raise MapBridgeError("$: south_west latitude must not exceed north_east latitude")
    if south_west.longitude_deg > north_east.longitude_deg:
        raise MapBridgeError("$: south_west longitude must not exceed north_east longitude")
    return ViewportChanged(south_west=south_west, north_east=north_east)


def encode_render_message(model: RenderModel) -> str:
    """Encode a typed render model into strict JSON for map content."""

    if isinstance(model.drag_threshold_px, bool) or not isinstance(model.drag_threshold_px, int):
        raise MapBridgeError("drag_threshold_px must be an integer")
    if model.drag_threshold_px < 1:
        raise MapBridgeError("drag_threshold_px must be positive")
    if isinstance(model.tile_attempt_id, bool) or not isinstance(model.tile_attempt_id, int):
        raise MapBridgeError("tile_attempt_id must be an integer")
    if model.tile_attempt_id < 0:
        raise MapBridgeError("tile_attempt_id must not be negative")
    if model.tile_provider is TileProvider.OFFLINE and model.tile_attempt_id != 0:
        raise MapBridgeError("offline tile provider requires tile_attempt_id zero")
    if model.tile_provider is TileProvider.OPENSTREETMAP and model.tile_attempt_id < 1:
        raise MapBridgeError("OpenStreetMap requires a positive tile_attempt_id")
    sequences: set[int] = set()
    actions: list[JsonObject] = []
    for expected_sequence, action in enumerate(model.actions, start=1):
        if action.sequence != expected_sequence or action.sequence in sequences:
            raise MapBridgeError("render sequences must be contiguous creation order")
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
            "tile_provider": model.tile_provider.value,
            "tile_attempt_id": model.tile_attempt_id,
            "drag_threshold_px": model.drag_threshold_px,
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


def _expect_enum(enum_type: type[EnumT], value: object, path: str) -> EnumT:
    parsed = _expect_string(value, path)
    try:
        return enum_type(parsed)
    except ValueError as error:
        raise MapBridgeError(f"{path}: unknown value {parsed!r}") from error


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


def parse_coordinate_input(latitude_text: str, longitude_text: str) -> GeoPoint:
    """Parse strict decimal latitude/longitude text for an explicit recenter action."""

    latitude = _parse_decimal_coordinate(latitude_text, "Latitude", -90.0, 90.0)
    longitude = _parse_decimal_coordinate(longitude_text, "Longitude", -180.0, 180.0)
    return GeoPoint(latitude, longitude)


def _parse_decimal_coordinate(text: str, label: str, minimum: float, maximum: float) -> float:
    stripped = text.strip()
    if not stripped:
        raise MapBridgeError(f"{label} is required.")
    if _DECIMAL_COORDINATE.fullmatch(stripped) is None:
        raise MapBridgeError(f"{label} must be a decimal number without symbols or letters.")
    value = float(stripped)
    if not minimum <= value <= maximum:
        raise MapBridgeError(f"{label} must be between {minimum:g} and {maximum:g} degrees.")
    return value
