"""Injectable MAVLink connection and heartbeat target-selection boundary."""

from __future__ import annotations

import importlib.metadata
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity

PINNED_PYMAVLINK_VERSION = "2.4.41"
PINNED_DIALECT = "ardupilotmega"
MAVLINK2_WIRE_PROTOCOL = "2.0"
MAV_MODE_FLAG_SAFETY_ARMED = 128
MAV_MISSION_ACCEPTED = 0
MAV_MISSION_OPERATION_CANCELLED = 15
MAV_MISSION_TYPE_MISSION = 0


class TransportKind(StrEnum):
    """Physical-link classification supplied by connection setup."""

    USB = "usb"
    SIK = "sik"


@dataclass(frozen=True, slots=True)
class TransportDescriptor:
    """Explicit endpoint classification; connector shape is not inferred."""

    endpoint: str
    kind: TransportKind

    def __post_init__(self) -> None:
        if not self.endpoint.strip():
            raise ValueError("endpoint must not be empty")
        if not isinstance(self.kind, TransportKind):
            raise TypeError("kind must be a TransportKind")


@dataclass(frozen=True, slots=True)
class MavlinkAddress:
    system_id: int
    component_id: int

    def __post_init__(self) -> None:
        for name in ("system_id", "component_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if not 1 <= value <= 255:
                raise ValueError(f"{name} must be between 1 and 255")


DEFAULT_GCS_ADDRESS = MavlinkAddress(255, 190)


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """Transport-neutral view of a received MAVLink message."""

    name: str
    source: MavlinkAddress
    fields: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("message name must not be empty")
        object.__setattr__(self, "fields", dict(self.fields))

    def integer(self, field: str) -> int:
        value = self.fields.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{self.name}.{field} must be an integer")
        return value

    def number(self, field: str) -> float:
        value = self.fields.get(field)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{self.name}.{field} must be numeric")
        return float(value)


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float: ...


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()


@runtime_checkable
class Cancellation(Protocol):
    def is_cancelled(self) -> bool: ...


class CancellationToken:
    """Thread-safe cancellation flag suitable for a worker-owned transaction."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


@runtime_checkable
class MissionLink(Protocol):
    """Closed send surface: no generic message or vehicle-command emission."""

    descriptor: TransportDescriptor
    local_address: MavlinkAddress

    def is_connected(self) -> bool: ...

    def receive(self, timeout_s: float) -> IncomingMessage | None: ...

    def send_mission_count(
        self, target: MavlinkAddress, *, count: int, mission_type: int
    ) -> None: ...

    def send_mission_item_int(
        self, target: MavlinkAddress, *, item: Mapping[str, int | float]
    ) -> None: ...

    def send_mission_request_list(self, target: MavlinkAddress, *, mission_type: int) -> None: ...

    def send_mission_request_int(
        self, target: MavlinkAddress, *, sequence: int, mission_type: int
    ) -> None: ...

    def send_mission_ack(
        self, target: MavlinkAddress, *, result: int, mission_type: int
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    address: MavlinkAddress
    vehicle: VehicleIdentity
    transport: TransportKind
    vehicle_type: int
    autopilot_type: int
    base_mode: int
    observed_at_s: float

    @property
    def armed(self) -> bool:
        return bool(self.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)


@dataclass(frozen=True, slots=True)
class UploadAuthorization:
    """Application-supplied, short-lived USB/disarmed upload approval."""

    target: TargetCandidate
    approved: bool
    valid_for_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.approved, bool):
            raise TypeError("approved must be a boolean")
        if self.valid_for_s <= 0:
            raise ValueError("valid_for_s must be positive")

    def issue(self, now_s: float, expected_vehicle: VehicleIdentity) -> str | None:
        if not self.approved:
            return "application did not approve mission upload"
        if self.target.transport is not TransportKind.USB:
            return "mission upload is restricted to an explicitly classified USB link"
        if self.target.armed:
            return "mission upload requires a disarmed target"
        if self.target.vehicle != expected_vehicle:
            return "upload authorization belongs to a different vehicle"
        age_s = now_s - self.target.observed_at_s
        if age_s < 0 or age_s > self.valid_for_s:
            return "upload authorization heartbeat is stale"
        return None


class TargetSelectionError(RuntimeError):
    pass


def candidate_from_heartbeat(
    message: IncomingMessage, *, transport: TransportKind, observed_at_s: float
) -> TargetCandidate:
    if message.name != "HEARTBEAT":
        raise ValueError("target candidates require HEARTBEAT messages")
    address = message.source
    return TargetCandidate(
        address=address,
        vehicle=VehicleIdentity(
            f"mavlink-system-{address.system_id}-component-{address.component_id}"
        ),
        transport=transport,
        vehicle_type=message.integer("type"),
        autopilot_type=message.integer("autopilot"),
        base_mode=message.integer("base_mode"),
        observed_at_s=observed_at_s,
    )


def discover_targets(
    link: MissionLink,
    *,
    clock: Clock,
    duration_s: float,
    cancellation: Cancellation | None = None,
) -> tuple[TargetCandidate, ...]:
    """Collect heartbeat identities for a bounded window; never auto-select one."""

    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    cancellation = cancellation or NeverCancelled()
    deadline_s = clock.now() + duration_s
    candidates: dict[MavlinkAddress, TargetCandidate] = {}
    while clock.now() < deadline_s:
        if cancellation.is_cancelled() or not link.is_connected():
            break
        message = link.receive(max(0.0, deadline_s - clock.now()))
        if message is None:
            break
        if message.name == "HEARTBEAT" and message.source != link.local_address:
            candidate = candidate_from_heartbeat(
                message,
                transport=link.descriptor.kind,
                observed_at_s=clock.now(),
            )
            candidates[candidate.address] = candidate
    return tuple(
        sorted(
            candidates.values(),
            key=lambda item: (item.address.system_id, item.address.component_id),
        )
    )


def select_target(
    candidates: tuple[TargetCandidate, ...], address: MavlinkAddress
) -> TargetCandidate:
    matches = tuple(candidate for candidate in candidates if candidate.address == address)
    if len(matches) != 1:
        raise TargetSelectionError(
            f"expected exactly one heartbeat target at {address.system_id}:{address.component_id}; "
            f"found {len(matches)}"
        )
    return matches[0]


class PymavlinkMissionLink:
    """pymavlink-backed implementation limited to the mission service."""

    def __init__(
        self,
        connection: Any,
        descriptor: TransportDescriptor,
        *,
        local_address: MavlinkAddress = DEFAULT_GCS_ADDRESS,
    ) -> None:
        self._connection = connection
        self.descriptor = descriptor
        self.local_address = local_address
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        self._connected = False
        close = getattr(self._connection, "close", None)
        if callable(close):
            close()

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        self._require_connected()
        try:
            raw = self._connection.recv_match(blocking=True, timeout=max(0.0, timeout_s))
        except (EOFError, OSError) as error:
            self._connected = False
            raise ConnectionError("MAVLink connection closed while receiving") from error
        if raw is None:
            return None
        fields = raw.to_dict()
        fields.pop("mavpackettype", None)
        return IncomingMessage(
            name=str(raw.get_type()),
            source=MavlinkAddress(raw.get_srcSystem(), raw.get_srcComponent()),
            fields=fields,
        )

    def send_mission_count(self, target: MavlinkAddress, *, count: int, mission_type: int) -> None:
        self._emit(
            lambda: self._connection.mav.mission_count_send(
                target_system=target.system_id,
                target_component=target.component_id,
                count=count,
                mission_type=mission_type,
            )
        )

    def send_mission_item_int(
        self, target: MavlinkAddress, *, item: Mapping[str, int | float]
    ) -> None:
        self._emit(
            lambda: self._connection.mav.mission_item_int_send(
                target_system=target.system_id,
                target_component=target.component_id,
                **dict(item),
            )
        )

    def send_mission_request_list(self, target: MavlinkAddress, *, mission_type: int) -> None:
        self._emit(
            lambda: self._connection.mav.mission_request_list_send(
                target_system=target.system_id,
                target_component=target.component_id,
                mission_type=mission_type,
            )
        )

    def send_mission_request_int(
        self, target: MavlinkAddress, *, sequence: int, mission_type: int
    ) -> None:
        self._emit(
            lambda: self._connection.mav.mission_request_int_send(
                target_system=target.system_id,
                target_component=target.component_id,
                seq=sequence,
                mission_type=mission_type,
            )
        )

    def send_mission_ack(self, target: MavlinkAddress, *, result: int, mission_type: int) -> None:
        self._emit(
            lambda: self._connection.mav.mission_ack_send(
                target_system=target.system_id,
                target_component=target.component_id,
                type=result,
                mission_type=mission_type,
            )
        )

    def _emit(self, action: Callable[[], None]) -> None:
        self._require_connected()
        try:
            action()
        except (EOFError, OSError) as error:
            self._connected = False
            raise ConnectionError("MAVLink connection closed while sending") from error

    def _require_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("MAVLink connection is closed")


def open_pymavlink_link(
    descriptor: TransportDescriptor,
    *,
    local_address: MavlinkAddress = DEFAULT_GCS_ADDRESS,
) -> PymavlinkMissionLink:
    """Open the explicitly supplied endpoint under the exact pinned library/dialect."""

    installed = importlib.metadata.version("pymavlink")
    if installed != PINNED_PYMAVLINK_VERSION:
        raise RuntimeError(f"pymavlink {PINNED_PYMAVLINK_VERSION} is required; found {installed}")
    configured = os.environ.get("MAVLINK20")
    if configured not in (None, "1"):
        raise RuntimeError("MAVLINK20 must be unset or '1'")
    os.environ["MAVLINK20"] = "1"
    from pymavlink import mavutil

    mavutil.set_dialect(PINNED_DIALECT)
    if str(mavutil.mavlink.WIRE_PROTOCOL_VERSION) != MAVLINK2_WIRE_PROTOCOL:
        raise RuntimeError("pymavlink did not activate the required MAVLink2 dialect")
    connection = mavutil.mavlink_connection(
        descriptor.endpoint,
        source_system=local_address.system_id,
        source_component=local_address.component_id,
        dialect=PINNED_DIALECT,
    )
    return PymavlinkMissionLink(connection, descriptor, local_address=local_address)
