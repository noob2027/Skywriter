"""Immutable, presentation-neutral read-only telemetry contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class TelemetryFreshness(StrEnum):
    UNAVAILABLE = "unavailable"
    FRESH = "fresh"
    STALE = "stale"


class TelemetryConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    STALE = "stale"


class TelemetryLinkKind(StrEnum):
    USB = "usb"
    SIK = "sik"


@dataclass(frozen=True, slots=True)
class TimedSignal(Generic[T]):
    """One immutable signal with explicit availability and lifetime."""

    value: T | None
    observed_at_s: float | None
    valid_for_s: float

    def __post_init__(self) -> None:
        if self.valid_for_s <= 0 or not math.isfinite(self.valid_for_s):
            raise ValueError("valid_for_s must be a positive finite number")
        if (self.value is None) != (self.observed_at_s is None):
            raise ValueError("value and observed_at_s must be available together")
        if self.observed_at_s is not None and not math.isfinite(self.observed_at_s):
            raise ValueError("observed_at_s must be finite")

    @classmethod
    def unavailable(cls, valid_for_s: float) -> TimedSignal[T]:
        return cls(None, None, valid_for_s)

    def freshness(self, now_s: float) -> TelemetryFreshness:
        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if self.value is None or self.observed_at_s is None:
            return TelemetryFreshness.UNAVAILABLE
        age_s = now_s - self.observed_at_s
        if age_s < 0 or age_s > self.valid_for_s:
            return TelemetryFreshness.STALE
        return TelemetryFreshness.FRESH


@dataclass(frozen=True, slots=True)
class TelemetryPoint:
    latitude_deg: float
    longitude_deg: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.latitude_deg) or not -90 <= self.latitude_deg <= 90:
            raise ValueError("latitude_deg must be finite and between -90 and 90")
        if not math.isfinite(self.longitude_deg) or not -180 <= self.longitude_deg <= 180:
            raise ValueError("longitude_deg must be finite and between -180 and 180")


@dataclass(frozen=True, slots=True)
class HeartbeatTelemetry:
    armed: bool
    mode_number: int
    mode_name: str
    system_status: int
    vehicle_type: int
    autopilot_type: int


@dataclass(frozen=True, slots=True)
class PositionTelemetry:
    point: TelemetryPoint
    altitude_msl_m: float
    relative_altitude_m: float
    ground_speed_m_s: float
    heading_deg: float | None

    def __post_init__(self) -> None:
        for name in ("altitude_msl_m", "relative_altitude_m", "ground_speed_m_s"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.ground_speed_m_s < 0:
            raise ValueError("ground_speed_m_s must be non-negative")
        if self.heading_deg is not None and not 0 <= self.heading_deg < 360:
            raise ValueError("heading_deg must be unavailable or between 0 and 360")


@dataclass(frozen=True, slots=True)
class BatteryTelemetry:
    battery_id: int
    voltage_v: float | None
    current_a: float | None
    remaining_percent: int | None

    def __post_init__(self) -> None:
        if self.battery_id < 0:
            raise ValueError("battery_id must be non-negative")
        for name in ("voltage_v", "current_a"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite when available")
        if self.voltage_v is not None and self.voltage_v < 0:
            raise ValueError("voltage_v must be non-negative")
        if self.remaining_percent is not None and not 0 <= self.remaining_percent <= 100:
            raise ValueError("remaining_percent must be unavailable or between 0 and 100")


@dataclass(frozen=True, slots=True)
class HomeTelemetry:
    point: TelemetryPoint
    altitude_msl_m: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.altitude_msl_m):
            raise ValueError("altitude_msl_m must be finite")


@dataclass(frozen=True, slots=True)
class MissionProgressTelemetry:
    current_sequence: int | None = None
    total_items: int | None = None
    mission_state: int | None = None
    mission_mode: int | None = None
    last_reached_sequence: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "current_sequence",
            "total_items",
            "mission_state",
            "mission_mode",
            "last_reached_sequence",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative when available")


@dataclass(frozen=True, slots=True)
class GpsTelemetry:
    fix_type: int
    satellites_visible: int | None
    hdop: float | None

    def __post_init__(self) -> None:
        if self.fix_type < 0:
            raise ValueError("fix_type must be non-negative")
        if self.satellites_visible is not None and self.satellites_visible < 0:
            raise ValueError("satellites_visible must be non-negative")
        if self.hdop is not None and (not math.isfinite(self.hdop) or self.hdop < 0):
            raise ValueError("hdop must be a non-negative finite number")


@dataclass(frozen=True, slots=True)
class SensorStatusTelemetry:
    present_flags: int
    enabled_flags: int
    health_flags: int

    def __post_init__(self) -> None:
        if min(self.present_flags, self.enabled_flags, self.health_flags) < 0:
            raise ValueError("sensor status flags must be non-negative")


@dataclass(frozen=True, slots=True)
class EkfTelemetry:
    flags: int
    velocity_variance: float
    horizontal_position_variance: float
    vertical_position_variance: float
    compass_variance: float
    terrain_altitude_variance: float

    def __post_init__(self) -> None:
        if self.flags < 0:
            raise ValueError("flags must be non-negative")
        for name in (
            "velocity_variance",
            "horizontal_position_variance",
            "vertical_position_variance",
            "compass_variance",
            "terrain_altitude_variance",
        ):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class ExtendedStateTelemetry:
    landed_state: int
    vtol_state: int


@dataclass(frozen=True, slots=True)
class NativeStatusText:
    severity: int
    text: str
    message_id: int
    chunk_sequence: int
    observed_at_s: float

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("status text must not be empty")
        if min(self.severity, self.message_id, self.chunk_sequence) < 0:
            raise ValueError("status metadata must be non-negative")
        if not math.isfinite(self.observed_at_s):
            raise ValueError("observed_at_s must be finite")


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    vehicle_identity: str
    target_system: int
    target_component: int
    link_kind: TelemetryLinkKind
    link_connected: bool
    heartbeat: TimedSignal[HeartbeatTelemetry]
    position: TimedSignal[PositionTelemetry]
    battery: TimedSignal[BatteryTelemetry]
    home: TimedSignal[HomeTelemetry]
    mission: TimedSignal[MissionProgressTelemetry]
    gps: TimedSignal[GpsTelemetry]
    sensors: TimedSignal[SensorStatusTelemetry]
    ekf: TimedSignal[EkfTelemetry]
    extended_state: TimedSignal[ExtendedStateTelemetry]
    native_messages: tuple[NativeStatusText, ...] = ()

    def __post_init__(self) -> None:
        if not self.vehicle_identity.strip():
            raise ValueError("vehicle_identity must not be empty")
        if not 1 <= self.target_system <= 255 or not 1 <= self.target_component <= 255:
            raise ValueError("target system/component must be between 1 and 255")

    def connection_state(self, now_s: float) -> TelemetryConnectionState:
        if not self.link_connected:
            return TelemetryConnectionState.DISCONNECTED
        if self.heartbeat.freshness(now_s) is not TelemetryFreshness.FRESH:
            return TelemetryConnectionState.STALE
        return TelemetryConnectionState.CONNECTED

    def command_gate_fresh(self, now_s: float) -> bool:
        """A read-only suitability fact; this adapter never enables a command itself."""

        return self.connection_state(now_s) is TelemetryConnectionState.CONNECTED


@dataclass(frozen=True, slots=True)
class TelemetryRoutePoint:
    sequence: int
    point: TelemetryPoint
    label: str

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("route sequence must be non-negative")
        if not self.label:
            raise ValueError("route label must not be empty")


@dataclass(frozen=True, slots=True)
class TelemetryRoute:
    points: tuple[TelemetryRoutePoint, ...] = ()

    def __post_init__(self) -> None:
        points = tuple(self.points)
        sequences = tuple(point.sequence for point in points)
        if sequences != tuple(sorted(set(sequences))):
            raise ValueError("route sequences must be unique and increasing")
        object.__setattr__(self, "points", points)


@dataclass(frozen=True, slots=True)
class TelemetryMapLayers:
    aircraft: PositionTelemetry | None
    home: HomeTelemetry | None
    current_target: TelemetryRoutePoint | None
    completed_route: tuple[TelemetryRoutePoint, ...]
    remaining_route: tuple[TelemetryRoutePoint, ...]


def build_map_layers(
    snapshot: TelemetrySnapshot,
    route: TelemetryRoute,
    *,
    now_s: float,
) -> TelemetryMapLayers:
    """Compose read-only map layers from telemetry plus a caller-owned route."""

    aircraft = (
        snapshot.position.value
        if snapshot.position.freshness(now_s) is TelemetryFreshness.FRESH
        else None
    )
    home = (
        snapshot.home.value if snapshot.home.freshness(now_s) is TelemetryFreshness.FRESH else None
    )
    progress = (
        snapshot.mission.value
        if snapshot.mission.freshness(now_s) is TelemetryFreshness.FRESH
        else None
    )
    if progress is None:
        return TelemetryMapLayers(aircraft, home, None, (), route.points)

    current_target = next(
        (point for point in route.points if point.sequence == progress.current_sequence),
        None,
    )
    reached = progress.last_reached_sequence
    completed = (
        tuple(point for point in route.points if point.sequence <= reached)
        if reached is not None
        else tuple(
            point
            for point in route.points
            if progress.current_sequence is not None and point.sequence < progress.current_sequence
        )
    )
    completed_sequences = {point.sequence for point in completed}
    remaining = tuple(point for point in route.points if point.sequence not in completed_sequences)
    return TelemetryMapLayers(aircraft, home, current_target, completed, remaining)
