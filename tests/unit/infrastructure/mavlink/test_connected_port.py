from __future__ import annotations

from collections.abc import Mapping

from skywriter.application.telemetry import TelemetryFreshness, TelemetryLinkKind
from skywriter.infrastructure.mavlink.connected import ConnectedMavlinkPort
from skywriter.infrastructure.mavlink.connection import (
    IncomingMessage,
    MavlinkAddress,
    TransportDescriptor,
    TransportKind,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def now(self) -> float:
        return self.value


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class ReceiveOnlyScriptedMissionLink:
    local_address = MavlinkAddress(255, 190)

    def __init__(self, clock: FakeClock, events: list[IncomingMessage | None]) -> None:
        self.descriptor = TransportDescriptor("scripted", TransportKind.USB)
        self.clock = clock
        self.events = events

    def is_connected(self) -> bool:
        return True

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        if not self.events:
            self.clock.value += timeout_s
            return None
        event = self.events.pop(0)
        if event is None:
            self.clock.value += timeout_s
        return event

    def send_mission_count(self, target: MavlinkAddress, *, count: int, mission_type: int) -> None:
        raise AssertionError("not used")

    def send_mission_item_int(
        self, target: MavlinkAddress, *, item: Mapping[str, int | float]
    ) -> None:
        raise AssertionError("not used")

    def send_mission_request_list(self, target: MavlinkAddress, *, mission_type: int) -> None:
        raise AssertionError("not used")

    def send_mission_request_int(
        self, target: MavlinkAddress, *, sequence: int, mission_type: int
    ) -> None:
        raise AssertionError("not used")

    def send_mission_ack(self, target: MavlinkAddress, *, result: int, mission_type: int) -> None:
        raise AssertionError("not used")


def message(name: str, fields: dict[str, object]) -> IncomingMessage:
    return IncomingMessage(name, MavlinkAddress(1, 1), fields)


def heartbeat() -> IncomingMessage:
    return message(
        "HEARTBEAT",
        {
            "type": 2,
            "autopilot": 3,
            "base_mode": 0,
            "custom_mode": 0,
            "system_status": 3,
        },
    )


def test_port_composes_discovery_and_read_only_telemetry_for_one_target() -> None:
    clock = FakeClock()
    link = ReceiveOnlyScriptedMissionLink(clock, [heartbeat(), None])
    port = ConnectedMavlinkPort(link, clock=clock)
    cancellation = NeverCancelled()

    targets = port.discover(duration_s=1.0, cancellation=cancellation)
    assert len(targets) == 1
    assert targets[0].link_kind is TelemetryLinkKind.USB

    link.events.extend(
        [
            heartbeat(),
            message(
                "HOME_POSITION",
                {
                    "latitude": 515007292,
                    "longitude": -1246254,
                    "altitude": 15000,
                },
            ),
            None,
        ]
    )
    snapshot = port.collect_telemetry(targets[0], duration_s=1.0, cancellation=cancellation)

    assert snapshot.vehicle_identity == targets[0].vehicle.value
    assert snapshot.heartbeat.freshness(clock.now()) is TelemetryFreshness.FRESH
    assert snapshot.home.freshness(clock.now()) is TelemetryFreshness.FRESH


def test_connected_adapter_exposes_no_generic_command_or_parameter_surface() -> None:
    public = {name.lower() for name in dir(ConnectedMavlinkPort) if not name.startswith("_")}
    assert public == {
        "collect_telemetry",
        "discover",
        "download_mission",
        "is_connected",
        "link_kind",
        "upload_and_verify",
    }
    assert not any(
        fragment in name
        for name in public
        for fragment in ("command", "parameter", "arm", "mode", "rtl", "land")
    )
