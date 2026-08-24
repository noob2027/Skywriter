"""Read-only, target-scoped MAVLink telemetry parsing and polling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar, runtime_checkable

from skywriter.application.telemetry import (
    BatteryTelemetry,
    EkfTelemetry,
    ExtendedStateTelemetry,
    GpsTelemetry,
    HeartbeatTelemetry,
    HomeTelemetry,
    MissionProgressTelemetry,
    NativeStatusText,
    PositionTelemetry,
    SensorStatusTelemetry,
    TelemetryLinkKind,
    TelemetryPoint,
    TelemetrySnapshot,
    TimedSignal,
)
from skywriter.infrastructure.mavlink.connection import (
    MAV_MODE_FLAG_SAFETY_ARMED,
    Cancellation,
    Clock,
    IncomingMessage,
    MavlinkAddress,
    NeverCancelled,
    TargetCandidate,
    TransportDescriptor,
    TransportKind,
)

_COPTER_MODE_NAMES: dict[int, str] = {
    0: "Stabilize",
    1: "Acro",
    2: "Altitude Hold",
    3: "Auto",
    4: "Guided",
    5: "Loiter",
    6: "RTL (native status only)",
    7: "Circle",
    9: "Land",
    11: "Drift",
    13: "Sport",
    14: "Flip",
    15: "Autotune",
    16: "Position Hold",
    17: "Brake",
    18: "Throw",
    19: "ADSB Avoidance",
    20: "Guided No GPS",
    21: "Smart RTL (native status only)",
    22: "Flow Hold",
    23: "Follow",
    24: "Zigzag",
    25: "System ID",
    26: "Autorotate",
    27: "Auto RTL (native status only)",
}
_SUPPORTED_MESSAGES = frozenset(
    {
        "HEARTBEAT",
        "GLOBAL_POSITION_INT",
        "BATTERY_STATUS",
        "SYS_STATUS",
        "HOME_POSITION",
        "MISSION_CURRENT",
        "MISSION_ITEM_REACHED",
        "GPS_RAW_INT",
        "EKF_STATUS_REPORT",
        "EXTENDED_SYS_STATE",
        "STATUSTEXT",
    }
)


class TelemetryIngestCode(StrEnum):
    ACCEPTED = "accepted"
    IGNORED = "ignored"
    WRONG_TARGET = "wrong_target"
    OUT_OF_ORDER = "out_of_order"
    MALFORMED = "malformed"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class TelemetryIngestResult:
    code: TelemetryIngestCode
    message_type: str | None
    detail: str

    @property
    def accepted(self) -> bool:
        return self.code is TelemetryIngestCode.ACCEPTED


@dataclass(frozen=True, slots=True)
class TelemetryFreshnessPolicy:
    heartbeat_s: float = 3.0
    position_s: float = 2.0
    battery_s: float = 10.0
    home_s: float = 60.0
    mission_s: float = 5.0
    gps_s: float = 5.0
    sensors_s: float = 5.0
    ekf_s: float = 5.0
    extended_state_s: float = 5.0
    max_poll_s: float = 1.0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


@runtime_checkable
class TelemetryLink(Protocol):
    """Receive-only link contract; no outgoing frame method is available."""

    descriptor: TransportDescriptor

    def is_connected(self) -> bool: ...

    def receive(self, timeout_s: float) -> IncomingMessage | None: ...


class TelemetryAdapter:
    """Accumulate immutable typed signals for one explicitly selected target."""

    def __init__(
        self,
        target: TargetCandidate,
        *,
        policy: TelemetryFreshnessPolicy | None = None,
        max_native_messages: int = 50,
    ) -> None:
        if max_native_messages <= 0:
            raise ValueError("max_native_messages must be positive")
        self._target = target
        self._policy = policy or TelemetryFreshnessPolicy()
        self._max_native_messages = max_native_messages
        self._heartbeat: TimedSignal[HeartbeatTelemetry] = TimedSignal.unavailable(
            self._policy.heartbeat_s
        )
        self._position: TimedSignal[PositionTelemetry] = TimedSignal.unavailable(
            self._policy.position_s
        )
        self._battery: TimedSignal[BatteryTelemetry] = TimedSignal.unavailable(
            self._policy.battery_s
        )
        self._home: TimedSignal[HomeTelemetry] = TimedSignal.unavailable(self._policy.home_s)
        self._mission: TimedSignal[MissionProgressTelemetry] = TimedSignal.unavailable(
            self._policy.mission_s
        )
        self._gps: TimedSignal[GpsTelemetry] = TimedSignal.unavailable(self._policy.gps_s)
        self._sensors: TimedSignal[SensorStatusTelemetry] = TimedSignal.unavailable(
            self._policy.sensors_s
        )
        self._ekf: TimedSignal[EkfTelemetry] = TimedSignal.unavailable(self._policy.ekf_s)
        self._extended_state: TimedSignal[ExtendedStateTelemetry] = TimedSignal.unavailable(
            self._policy.extended_state_s
        )
        self._native_messages: tuple[NativeStatusText, ...] = ()

    @property
    def target_address(self) -> MavlinkAddress:
        return self._target.address

    @property
    def target_transport(self) -> TransportKind:
        return self._target.transport

    def ingest(self, message: IncomingMessage, *, observed_at_s: float) -> TelemetryIngestResult:
        if not math.isfinite(observed_at_s):
            return TelemetryIngestResult(
                TelemetryIngestCode.MALFORMED,
                message.name,
                "observation time must be finite",
            )
        if message.source != self._target.address:
            return TelemetryIngestResult(
                TelemetryIngestCode.WRONG_TARGET,
                message.name,
                f"received {message.source.system_id}:{message.source.component_id}; "
                f"expected {self._target.address.system_id}:{self._target.address.component_id}",
            )
        if message.name not in _SUPPORTED_MESSAGES:
            return TelemetryIngestResult(
                TelemetryIngestCode.IGNORED,
                message.name,
                "message is outside the read-only telemetry whitelist",
            )
        try:
            updated = self._ingest_supported(message, observed_at_s)
        except (TypeError, ValueError, OverflowError) as error:
            return TelemetryIngestResult(
                TelemetryIngestCode.MALFORMED,
                message.name,
                str(error),
            )
        if not updated:
            return TelemetryIngestResult(
                TelemetryIngestCode.OUT_OF_ORDER,
                message.name,
                "older telemetry did not replace a newer signal",
            )
        return TelemetryIngestResult(
            TelemetryIngestCode.ACCEPTED,
            message.name,
            "typed telemetry snapshot updated",
        )

    def snapshot(self, *, link_connected: bool) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            vehicle_identity=self._target.vehicle.value,
            target_system=self._target.address.system_id,
            target_component=self._target.address.component_id,
            link_kind=_link_kind(self._target.transport),
            link_connected=link_connected,
            heartbeat=self._heartbeat,
            position=self._position,
            battery=self._battery,
            home=self._home,
            mission=self._mission,
            gps=self._gps,
            sensors=self._sensors,
            ekf=self._ekf,
            extended_state=self._extended_state,
            native_messages=self._native_messages,
        )

    def _ingest_supported(self, message: IncomingMessage, observed_at_s: float) -> bool:
        if message.name == "HEARTBEAT":
            if _is_older(observed_at_s, self._heartbeat):
                return False
            mode_number = _integer(message, "custom_mode", minimum=0)
            self._heartbeat = TimedSignal(
                HeartbeatTelemetry(
                    armed=bool(
                        _integer(message, "base_mode", minimum=0, maximum=255)
                        & MAV_MODE_FLAG_SAFETY_ARMED
                    ),
                    mode_number=mode_number,
                    mode_name=_COPTER_MODE_NAMES.get(mode_number, f"Mode {mode_number}"),
                    system_status=_integer(message, "system_status", minimum=0, maximum=255),
                    vehicle_type=_integer(message, "type", minimum=0, maximum=255),
                    autopilot_type=_integer(message, "autopilot", minimum=0, maximum=255),
                ),
                observed_at_s,
                self._policy.heartbeat_s,
            )
            return True
        if message.name == "GLOBAL_POSITION_INT":
            if _is_older(observed_at_s, self._position):
                return False
            latitude_e7 = _integer(message, "lat", minimum=-900_000_000, maximum=900_000_000)
            longitude_e7 = _integer(message, "lon", minimum=-1_800_000_000, maximum=1_800_000_000)
            velocity_north = _integer(message, "vx", minimum=-32_768, maximum=32_767)
            velocity_east = _integer(message, "vy", minimum=-32_768, maximum=32_767)
            heading_cdeg = _integer(message, "hdg", minimum=0, maximum=65_535)
            self._position = TimedSignal(
                PositionTelemetry(
                    point=TelemetryPoint(latitude_e7 / 1e7, longitude_e7 / 1e7),
                    altitude_msl_m=_integer(message, "alt") / 1_000.0,
                    relative_altitude_m=_integer(message, "relative_alt") / 1_000.0,
                    ground_speed_m_s=math.hypot(velocity_north, velocity_east) / 100.0,
                    heading_deg=None if heading_cdeg == 65_535 else heading_cdeg / 100.0,
                ),
                observed_at_s,
                self._policy.position_s,
            )
            return True
        if message.name == "BATTERY_STATUS":
            if _is_older(observed_at_s, self._battery):
                return False
            self._battery = TimedSignal(_battery(message), observed_at_s, self._policy.battery_s)
            return True
        if message.name == "SYS_STATUS":
            battery_is_older = _is_older(observed_at_s, self._battery)
            sensors_are_older = _is_older(observed_at_s, self._sensors)
            if battery_is_older and sensors_are_older:
                return False
            battery = _battery(message)
            sensors = SensorStatusTelemetry(
                present_flags=_integer(message, "onboard_control_sensors_present", minimum=0),
                enabled_flags=_integer(message, "onboard_control_sensors_enabled", minimum=0),
                health_flags=_integer(message, "onboard_control_sensors_health", minimum=0),
            )
            if not battery_is_older:
                self._battery = TimedSignal(battery, observed_at_s, self._policy.battery_s)
            if not sensors_are_older:
                self._sensors = TimedSignal(
                    sensors,
                    observed_at_s,
                    self._policy.sensors_s,
                )
            return True
        if message.name == "HOME_POSITION":
            if _is_older(observed_at_s, self._home):
                return False
            self._home = TimedSignal(
                HomeTelemetry(
                    point=TelemetryPoint(
                        _integer(message, "latitude", minimum=-900_000_000, maximum=900_000_000)
                        / 1e7,
                        _integer(
                            message,
                            "longitude",
                            minimum=-1_800_000_000,
                            maximum=1_800_000_000,
                        )
                        / 1e7,
                    ),
                    altitude_msl_m=_integer(message, "altitude") / 1_000.0,
                ),
                observed_at_s,
                self._policy.home_s,
            )
            return True
        if message.name in {"MISSION_CURRENT", "MISSION_ITEM_REACHED"}:
            if _is_older(observed_at_s, self._mission):
                return False
            previous = self._mission.value or MissionProgressTelemetry()
            if message.name == "MISSION_CURRENT":
                total = _optional_integer(message, "total")
                self._mission = TimedSignal(
                    MissionProgressTelemetry(
                        current_sequence=_integer(message, "seq", minimum=0, maximum=65_535),
                        total_items=None if total in (None, 0, 65_535) else total,
                        mission_state=_optional_nonzero_integer(message, "mission_state"),
                        mission_mode=_optional_nonzero_integer(message, "mission_mode"),
                        last_reached_sequence=previous.last_reached_sequence,
                    ),
                    observed_at_s,
                    self._policy.mission_s,
                )
            else:
                self._mission = TimedSignal(
                    MissionProgressTelemetry(
                        current_sequence=previous.current_sequence,
                        total_items=previous.total_items,
                        mission_state=previous.mission_state,
                        mission_mode=previous.mission_mode,
                        last_reached_sequence=_integer(message, "seq", minimum=0, maximum=65_535),
                    ),
                    observed_at_s,
                    self._policy.mission_s,
                )
            return True
        if message.name == "GPS_RAW_INT":
            if _is_older(observed_at_s, self._gps):
                return False
            satellites = _integer(message, "satellites_visible", minimum=0, maximum=255)
            eph = _integer(message, "eph", minimum=0, maximum=65_535)
            self._gps = TimedSignal(
                GpsTelemetry(
                    fix_type=_integer(message, "fix_type", minimum=0, maximum=255),
                    satellites_visible=None if satellites == 255 else satellites,
                    hdop=None if eph == 65_535 else eph / 100.0,
                ),
                observed_at_s,
                self._policy.gps_s,
            )
            return True
        if message.name == "EKF_STATUS_REPORT":
            if _is_older(observed_at_s, self._ekf):
                return False
            self._ekf = TimedSignal(
                EkfTelemetry(
                    flags=_integer(message, "flags", minimum=0),
                    velocity_variance=_finite_number(message, "velocity_variance"),
                    horizontal_position_variance=_finite_number(message, "pos_horiz_variance"),
                    vertical_position_variance=_finite_number(message, "pos_vert_variance"),
                    compass_variance=_finite_number(message, "compass_variance"),
                    terrain_altitude_variance=_finite_number(message, "terrain_alt_variance"),
                ),
                observed_at_s,
                self._policy.ekf_s,
            )
            return True
        if message.name == "EXTENDED_SYS_STATE":
            if _is_older(observed_at_s, self._extended_state):
                return False
            self._extended_state = TimedSignal(
                ExtendedStateTelemetry(
                    landed_state=_integer(message, "landed_state", minimum=0, maximum=255),
                    vtol_state=_integer(message, "vtol_state", minimum=0, maximum=255),
                ),
                observed_at_s,
                self._policy.extended_state_s,
            )
            return True
        if message.name == "STATUSTEXT":
            if self._native_messages and observed_at_s < self._native_messages[-1].observed_at_s:
                return False
            text = message.fields.get("text")
            if not isinstance(text, str):
                raise ValueError("STATUSTEXT.text must be a string")
            status = NativeStatusText(
                severity=_integer(message, "severity", minimum=0, maximum=255),
                text=text.rstrip("\x00").strip(),
                message_id=_optional_integer(message, "id") or 0,
                chunk_sequence=_optional_integer(message, "chunk_seq") or 0,
                observed_at_s=observed_at_s,
            )
            self._native_messages = (*self._native_messages, status)[-self._max_native_messages :]
            return True
        raise AssertionError(f"unhandled supported telemetry message {message.name}")


class TelemetryPoller:
    """Bounded receive loop seam intended for caller-owned worker execution."""

    def __init__(
        self,
        link: TelemetryLink,
        adapter: TelemetryAdapter,
        *,
        clock: Clock,
        cancellation: Cancellation | None = None,
        policy: TelemetryFreshnessPolicy | None = None,
    ) -> None:
        if link.descriptor.kind is not adapter.target_transport:
            raise ValueError("telemetry target transport does not match the active link")
        self._link = link
        self._adapter = adapter
        self._clock = clock
        self._cancellation = cancellation or NeverCancelled()
        self._policy = policy or TelemetryFreshnessPolicy()

    def poll_once(self, timeout_s: float) -> TelemetryIngestResult:
        if timeout_s < 0 or not math.isfinite(timeout_s):
            raise ValueError("timeout_s must be a non-negative finite number")
        if self._cancellation.is_cancelled():
            return TelemetryIngestResult(
                TelemetryIngestCode.CANCELLED, None, "telemetry polling was cancelled"
            )
        if not self._link.is_connected():
            return TelemetryIngestResult(
                TelemetryIngestCode.DISCONNECTED, None, "telemetry link is disconnected"
            )
        try:
            message = self._link.receive(min(timeout_s, self._policy.max_poll_s))
        except ConnectionError as error:
            return TelemetryIngestResult(TelemetryIngestCode.DISCONNECTED, None, str(error))
        if self._cancellation.is_cancelled():
            return TelemetryIngestResult(
                TelemetryIngestCode.CANCELLED, None, "telemetry polling was cancelled"
            )
        if message is None:
            return TelemetryIngestResult(
                TelemetryIngestCode.IGNORED, None, "no telemetry arrived before timeout"
            )
        return self._adapter.ingest(message, observed_at_s=self._clock.now())

    def snapshot(self) -> TelemetrySnapshot:
        return self._adapter.snapshot(link_connected=self._link.is_connected())


U = TypeVar("U")


def _is_older(observed_at_s: float, current: TimedSignal[U]) -> bool:
    return current.observed_at_s is not None and observed_at_s < current.observed_at_s


def _integer(
    message: IncomingMessage,
    field: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = message.integer(field)
    if minimum is not None and value < minimum:
        raise ValueError(f"{message.name}.{field} is below {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{message.name}.{field} is above {maximum}")
    return value


def _optional_integer(message: IncomingMessage, field: str) -> int | None:
    if field not in message.fields:
        return None
    return _integer(message, field, minimum=0)


def _optional_nonzero_integer(message: IncomingMessage, field: str) -> int | None:
    value = _optional_integer(message, field)
    return None if value in (None, 0) else value


def _finite_number(message: IncomingMessage, field: str) -> float:
    value = message.number(field)
    if not math.isfinite(value):
        raise ValueError(f"{message.name}.{field} must be finite")
    return value


def _battery(message: IncomingMessage) -> BatteryTelemetry:
    if message.name == "BATTERY_STATUS":
        raw_voltages = message.fields.get("voltages")
        if not isinstance(raw_voltages, list | tuple):
            raise ValueError("BATTERY_STATUS.voltages must be an array")
        voltages = []
        for raw in raw_voltages:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ValueError("BATTERY_STATUS.voltages must contain integers")
            if raw not in (0, 65_535):
                if not 0 < raw < 65_535:
                    raise ValueError("BATTERY_STATUS.voltages contains an invalid value")
                voltages.append(raw)
        voltage_v = sum(voltages) / 1_000.0 if voltages else None
        current_ca = _integer(message, "current_battery", minimum=-1, maximum=32_767)
        remaining = _integer(message, "battery_remaining", minimum=-1, maximum=100)
        return BatteryTelemetry(
            battery_id=_integer(message, "id", minimum=0, maximum=255),
            voltage_v=voltage_v,
            current_a=None if current_ca == -1 else current_ca / 100.0,
            remaining_percent=None if remaining == -1 else remaining,
        )
    voltage_mv = _integer(message, "voltage_battery", minimum=0, maximum=65_535)
    current_ca = _integer(message, "current_battery", minimum=-1, maximum=32_767)
    remaining = _integer(message, "battery_remaining", minimum=-1, maximum=100)
    return BatteryTelemetry(
        battery_id=0,
        voltage_v=None if voltage_mv == 65_535 else voltage_mv / 1_000.0,
        current_a=None if current_ca == -1 else current_ca / 100.0,
        remaining_percent=None if remaining == -1 else remaining,
    )


def _link_kind(kind: TransportKind) -> TelemetryLinkKind:
    if kind is TransportKind.USB:
        return TelemetryLinkKind.USB
    if kind is TransportKind.SIK:
        return TelemetryLinkKind.SIK
    raise AssertionError(f"unhandled transport kind {kind}")
