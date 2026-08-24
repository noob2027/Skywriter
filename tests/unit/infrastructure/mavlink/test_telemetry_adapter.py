from __future__ import annotations

import json
from pathlib import Path

import pytest

from skywriter.application.telemetry import TelemetryConnectionState, TelemetryFreshness
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.mavlink.connection import (
    CancellationToken,
    IncomingMessage,
    MavlinkAddress,
    TargetCandidate,
    TransportDescriptor,
    TransportKind,
)
from skywriter.infrastructure.mavlink.telemetry import (
    TelemetryAdapter,
    TelemetryIngestCode,
    TelemetryLink,
    TelemetryPoller,
)

FIXTURE = Path(__file__).parents[3] / "fixtures" / "telemetry" / "arducopter-4.6.3.jsonl"
TARGET = MavlinkAddress(1, 1)


class FakeClock:
    def __init__(self, now_s: float = 100.0) -> None:
        self.value = now_s

    def now(self) -> float:
        return self.value


class ReceiveOnlySpy:
    descriptor = TransportDescriptor("COM-telemetry", TransportKind.SIK)

    def __init__(self, messages: list[IncomingMessage | None]) -> None:
        self.messages = messages
        self.connected = True
        self.receive_calls = 0
        self.outbound_frames: list[object] = []

    def is_connected(self) -> bool:
        return self.connected

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        assert 0 <= timeout_s <= 1.0
        self.receive_calls += 1
        return self.messages.pop(0) if self.messages else None

    def send(self, frame: object) -> None:
        self.outbound_frames.append(frame)


def target(*, transport: TransportKind = TransportKind.SIK) -> TargetCandidate:
    return TargetCandidate(
        address=TARGET,
        vehicle=VehicleIdentity("mavlink-system-1-component-1"),
        transport=transport,
        vehicle_type=2,
        autopilot_type=3,
        base_mode=89,
        observed_at_s=99.0,
    )


def fixture_messages() -> list[IncomingMessage]:
    messages = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        document = json.loads(line)
        assert isinstance(document, dict)
        fields = document["fields"]
        assert isinstance(fields, dict)
        messages.append(
            IncomingMessage(
                name=str(document["name"]),
                source=MavlinkAddress(
                    int(document["source_system"]), int(document["source_component"])
                ),
                fields=fields,
            )
        )
    return messages


def ingest_fixture(adapter: TelemetryAdapter, *, start_s: float = 100.0) -> None:
    for index, message in enumerate(fixture_messages()):
        result = adapter.ingest(message, observed_at_s=start_s + index / 10)
        assert result.code is TelemetryIngestCode.ACCEPTED


def test_fixture_parses_every_required_signal_and_native_status() -> None:
    adapter = TelemetryAdapter(target())

    ingest_fixture(adapter)
    snapshot = adapter.snapshot(link_connected=True)

    assert snapshot.connection_state(102.0) is TelemetryConnectionState.CONNECTED
    assert snapshot.heartbeat.value is not None
    assert snapshot.heartbeat.value.mode_name == "Auto"
    assert snapshot.heartbeat.value.armed is False
    assert snapshot.position.value is not None
    assert snapshot.position.value.ground_speed_m_s == 5.0
    assert snapshot.position.value.relative_altitude_m == 1.23
    assert snapshot.battery.value is not None
    assert snapshot.battery.value.voltage_v == pytest.approx(16.34)
    assert snapshot.battery.value.remaining_percent == 77
    assert snapshot.sensors.value is not None
    assert snapshot.home.value is not None
    assert snapshot.home.value.point.latitude_deg == pytest.approx(51.5007291)
    assert snapshot.mission.value is not None
    assert snapshot.mission.value.current_sequence == 4
    assert snapshot.mission.value.last_reached_sequence == 3
    assert snapshot.gps.value is not None and snapshot.gps.value.satellites_visible == 14
    assert snapshot.ekf.value is not None and snapshot.ekf.value.flags == 831
    assert snapshot.extended_state.value is not None
    assert snapshot.native_messages[-1].text == "EKF3 IMU1 origin set"


def test_missing_signals_remain_unavailable_not_healthy() -> None:
    snapshot = TelemetryAdapter(target()).snapshot(link_connected=True)

    assert snapshot.connection_state(100.0) is TelemetryConnectionState.STALE
    assert snapshot.heartbeat.freshness(100.0) is TelemetryFreshness.UNAVAILABLE
    assert snapshot.position.value is None
    assert snapshot.battery.value is None
    assert snapshot.gps.value is None
    assert snapshot.command_gate_fresh(100.0) is False


def test_malformed_message_preserves_last_valid_signal() -> None:
    adapter = TelemetryAdapter(target())
    position = fixture_messages()[1]
    assert adapter.ingest(position, observed_at_s=100.0).accepted
    before = adapter.snapshot(link_connected=True).position
    malformed = IncomingMessage(
        "GLOBAL_POSITION_INT",
        TARGET,
        {**position.fields, "lat": 900_000_001},
    )

    result = adapter.ingest(malformed, observed_at_s=101.0)

    assert result.code is TelemetryIngestCode.MALFORMED
    assert adapter.snapshot(link_connected=True).position == before


def test_wrong_target_and_out_of_order_data_cannot_replace_selected_vehicle() -> None:
    adapter = TelemetryAdapter(target())
    heartbeat = fixture_messages()[0]
    assert adapter.ingest(heartbeat, observed_at_s=100.0).accepted
    wrong = IncomingMessage(heartbeat.name, MavlinkAddress(2, 1), heartbeat.fields)

    wrong_result = adapter.ingest(wrong, observed_at_s=101.0)
    old_result = adapter.ingest(heartbeat, observed_at_s=99.0)

    assert wrong_result.code is TelemetryIngestCode.WRONG_TARGET
    assert old_result.code is TelemetryIngestCode.OUT_OF_ORDER
    assert adapter.snapshot(link_connected=True).heartbeat.observed_at_s == 100.0


def test_sys_status_orders_battery_and_sensor_observations_independently() -> None:
    adapter = TelemetryAdapter(target())
    sys_status = fixture_messages()[2]
    battery_status = fixture_messages()[3]
    assert adapter.ingest(battery_status, observed_at_s=102.0).accepted
    battery = adapter.snapshot(link_connected=True).battery

    result = adapter.ingest(sys_status, observed_at_s=101.0)

    snapshot = adapter.snapshot(link_connected=True)
    assert result.code is TelemetryIngestCode.ACCEPTED
    assert snapshot.battery == battery
    assert snapshot.sensors.observed_at_s == 101.0
    assert snapshot.sensors.value is not None


def test_stale_heartbeat_recovers_only_after_new_selected_target_heartbeat() -> None:
    adapter = TelemetryAdapter(target())
    heartbeat = fixture_messages()[0]
    assert adapter.ingest(heartbeat, observed_at_s=100.0).accepted
    assert adapter.snapshot(link_connected=True).connection_state(104.0) is (
        TelemetryConnectionState.STALE
    )

    assert adapter.ingest(heartbeat, observed_at_s=104.0).accepted

    assert adapter.snapshot(link_connected=True).connection_state(104.0) is (
        TelemetryConnectionState.CONNECTED
    )


def test_receive_only_poller_is_bounded_cancellable_and_emits_zero_frames() -> None:
    token = CancellationToken()
    link = ReceiveOnlySpy([fixture_messages()[0]])
    adapter = TelemetryAdapter(target())
    poller = TelemetryPoller(link, adapter, clock=FakeClock(), cancellation=token)

    accepted = poller.poll_once(30.0)
    token.cancel()
    cancelled = poller.poll_once(1.0)

    assert accepted.code is TelemetryIngestCode.ACCEPTED
    assert cancelled.code is TelemetryIngestCode.CANCELLED
    assert link.receive_calls == 1
    assert link.outbound_frames == []


def test_disconnect_is_reported_without_receive_or_outbound_frame() -> None:
    link = ReceiveOnlySpy([])
    link.connected = False
    poller = TelemetryPoller(link, TelemetryAdapter(target()), clock=FakeClock())

    result = poller.poll_once(1.0)

    assert result.code is TelemetryIngestCode.DISCONNECTED
    assert link.receive_calls == 0
    assert link.outbound_frames == []


def test_poller_rejects_a_link_from_a_different_transport_identity() -> None:
    link = ReceiveOnlySpy([])

    with pytest.raises(ValueError, match="target transport"):
        TelemetryPoller(
            link,
            TelemetryAdapter(target(transport=TransportKind.USB)),
            clock=FakeClock(),
        )


def test_telemetry_link_contract_has_no_outgoing_method() -> None:
    assert not any(name.startswith("send") for name in TelemetryLink.__dict__)


def test_telemetry_source_has_no_outgoing_mavlink_paths() -> None:
    source = (
        Path(__file__).parents[4]
        / "src"
        / "skywriter"
        / "infrastructure"
        / "mavlink"
        / "telemetry.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "_send(",
        ".mav.",
        "command_long",
        "mission_count",
        "request_data_stream",
        "set_message_interval",
        "param_set",
    )

    assert not any(fragment in source.lower() for fragment in forbidden)
