from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from skywriter.application.auto_start import NativeAutoStartCommandResult, NativeAutoStartState
from skywriter.application.connected import ConnectedTarget
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.mavlink.auto_start import (
    MAV_CMD_MISSION_START,
    NativeAutoStartGateway,
    NativeAutoStartProtocolPolicy,
)
from skywriter.infrastructure.mavlink.connection import (
    IncomingMessage,
    MavlinkAddress,
    PymavlinkNativeAutoStartLink,
    TransportDescriptor,
    TransportKind,
)

TARGET = MavlinkAddress(1, 1)
LOCAL = MavlinkAddress(255, 190)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value


class Cancellation:
    def __init__(self) -> None:
        self.cancelled = False

    def is_cancelled(self) -> bool:
        return self.cancelled


Event = IncomingMessage | None | Exception | Callable[[], IncomingMessage | None]


class FakeAutoStartLink:
    descriptor = TransportDescriptor("scripted-sik", TransportKind.SIK)
    local_address = LOCAL

    def __init__(self, clock: FakeClock, events: list[Event]) -> None:
        self.clock = clock
        self.events = events
        self.connected = True
        self.sent: list[MavlinkAddress] = []

    def is_connected(self) -> bool:
        return self.connected

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        if not self.events:
            self.clock.value += timeout_s
            return None
        event = self.events.pop(0)
        self.clock.value += min(timeout_s, 0.1)
        if isinstance(event, Exception):
            raise event
        if callable(event):
            return event()
        return event

    def send_native_auto_start(self, target: MavlinkAddress) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.sent.append(target)


def target(*, observed_at_s: float = 100.0, base_mode: int = 128) -> ConnectedTarget:
    return ConnectedTarget(
        VehicleIdentity("mavlink-system-1-component-1"),
        TARGET.system_id,
        TARGET.component_id,
        TelemetryLinkKind.SIK,
        2,
        3,
        base_mode,
        observed_at_s,
    )


def message(
    name: str,
    fields: dict[str, object],
    *,
    source: MavlinkAddress = TARGET,
) -> IncomingMessage:
    return IncomingMessage(name, source, fields)


def ack(result: int, *, command: int = MAV_CMD_MISSION_START) -> IncomingMessage:
    return message(
        "COMMAND_ACK",
        {
            "command": command,
            "result": result,
            "target_system": LOCAL.system_id,
            "target_component": LOCAL.component_id,
        },
    )


def heartbeat(*, mode: int = 3, armed: bool = True) -> IncomingMessage:
    return message(
        "HEARTBEAT",
        {"base_mode": 128 if armed else 0, "custom_mode": mode},
    )


def progress(sequence: int = 2, *, mission_type: int = 0) -> IncomingMessage:
    return message(
        "MISSION_ITEM_REACHED",
        {"seq": sequence, "mission_type": mission_type},
    )


def gateway(
    events: list[Event],
    *,
    policy: NativeAutoStartProtocolPolicy | None = None,
) -> tuple[NativeAutoStartGateway, FakeAutoStartLink, FakeClock, Cancellation]:
    clock = FakeClock()
    link = FakeAutoStartLink(clock, events)
    cancellation = Cancellation()
    return NativeAutoStartGateway(link, clock=clock, policy=policy), link, clock, cancellation


def request(
    command: NativeAutoStartGateway,
    cancellation: Cancellation,
    *,
    selected: ConnectedTarget | None = None,
    item_count: int = 8,
) -> NativeAutoStartCommandResult:
    return command.request_native_auto_start(
        selected or target(),
        expected_item_count=item_count,
        target_valid_for_s=3.0,
        cancellation=cancellation,
    )


def test_accepted_ack_requires_later_armed_auto_and_valid_progress() -> None:
    command, link, _, cancellation = gateway([heartbeat(), ack(0), heartbeat(), progress(2)])
    result = request(command, cancellation)
    assert result.state is NativeAutoStartState.RUNNING
    assert result.ack_result == 0
    assert result.auto_observed_at_s is not None
    assert result.progress_observed_at_s is not None
    assert result.progress_sequence == 2
    assert link.sent == [TARGET]


def test_ack_or_pre_ack_telemetry_alone_never_reports_running() -> None:
    policy = NativeAutoStartProtocolPolicy(
        ack_timeout_s=0.3,
        telemetry_timeout_s=0.3,
        negative_capture_s=0.1,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(
        [heartbeat(), progress(), ack(0), heartbeat()], policy=policy
    )
    result = request(command, cancellation)
    assert result.state is NativeAutoStartState.ACKNOWLEDGED_NO_MISSION_PROGRESS

    command, _, _, cancellation = gateway([ack(0), progress()], policy=policy)
    result = request(command, cancellation)
    assert result.state is NativeAutoStartState.ACKNOWLEDGED_NO_AUTO_TELEMETRY


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([ack(0), heartbeat(mode=0), progress()], NativeAutoStartState.UNEXPECTED_MODE),
        ([ack(0), heartbeat(armed=False)], NativeAutoStartState.DISARMED),
        ([ack(0), heartbeat(), progress(8)], NativeAutoStartState.MISSION_MISMATCH),
        ([ack(0), heartbeat(), progress(2, mission_type=1)], NativeAutoStartState.MISSION_MISMATCH),
        ([ack(2)], NativeAutoStartState.REJECTED),
        ([ack(3)], NativeAutoStartState.UNSUPPORTED),
    ],
)
def test_native_failures_mode_disarm_and_mission_mismatch_are_distinct(
    events: list[Event], expected: NativeAutoStartState
) -> None:
    policy = NativeAutoStartProtocolPolicy(
        ack_timeout_s=0.3,
        telemetry_timeout_s=0.3,
        negative_capture_s=0.1,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(events, policy=policy)
    assert request(command, cancellation).state is expected


def test_native_rejection_retains_associated_status_text() -> None:
    policy = NativeAutoStartProtocolPolicy(
        ack_timeout_s=0.3,
        telemetry_timeout_s=0.3,
        negative_capture_s=0.2,
        max_poll_s=0.1,
    )
    status = message(
        "STATUSTEXT",
        {"severity": 2, "text": "Flight mode change failed", "id": 0, "chunk_seq": 0},
    )
    command, _, _, cancellation = gateway([ack(2), status], policy=policy)
    result = request(command, cancellation)
    assert result.state is NativeAutoStartState.REJECTED
    assert tuple(item.text for item in result.native_messages) == ("Flight mode change failed",)


def test_wrong_target_command_and_addressed_ack_fail_closed() -> None:
    wrong = MavlinkAddress(2, 1)
    command, _, _, cancellation = gateway(
        [message("COMMAND_ACK", {"command": 300, "result": 0}, source=wrong)]
    )
    assert request(command, cancellation).state is NativeAutoStartState.WRONG_TARGET

    command, _, _, cancellation = gateway([ack(0, command=400)])
    assert request(command, cancellation).state is NativeAutoStartState.WRONG_ACK

    misaddressed = message(
        "COMMAND_ACK",
        {"command": 300, "result": 0, "target_system": 42, "target_component": 190},
    )
    command, _, _, cancellation = gateway([misaddressed])
    assert request(command, cancellation).state is NativeAutoStartState.WRONG_ACK


def test_timeout_cancellation_disconnect_and_stale_link_are_distinct() -> None:
    policy = NativeAutoStartProtocolPolicy(
        ack_timeout_s=0.2,
        telemetry_timeout_s=0.2,
        negative_capture_s=0.1,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway([], policy=policy)
    assert request(command, cancellation).state is NativeAutoStartState.TIMED_OUT

    command, _, _, cancellation = gateway([], policy=policy)
    cancellation.cancelled = True
    assert request(command, cancellation).state is NativeAutoStartState.CANCELLED

    command, _, _, cancellation = gateway([ConnectionError("radio lost")], policy=policy)
    assert request(command, cancellation).state is NativeAutoStartState.LINK_LOST

    command, _, _, cancellation = gateway([], policy=policy)
    assert (
        request(command, cancellation, selected=target(observed_at_s=90.0)).state
        is NativeAutoStartState.STALE_LINK
    )


def test_disarmed_invalid_package_and_wrong_transport_send_nothing() -> None:
    command, link, _, cancellation = gateway([])
    assert (
        request(command, cancellation, selected=target(base_mode=0)).state
        is NativeAutoStartState.DISARMED
    )
    assert (
        request(command, cancellation, item_count=2).state is NativeAutoStartState.MISSION_MISMATCH
    )
    assert link.sent == []
    wrong_link = FakeAutoStartLink(FakeClock(), [])
    wrong_link.descriptor = TransportDescriptor("usb", TransportKind.USB)
    with pytest.raises(ValueError, match="SiK"):
        NativeAutoStartGateway(wrong_link, clock=FakeClock())


def test_closed_gateway_and_link_expose_only_native_start_surface() -> None:
    gateway_public = {
        name.lower() for name in dir(NativeAutoStartGateway) if not name.startswith("_")
    }
    link_public = {name.lower() for name in dir(FakeAutoStartLink) if not name.startswith("_")}
    assert gateway_public == {"request_native_auto_start"}
    assert link_public == {
        "descriptor",
        "is_connected",
        "local_address",
        "receive",
        "send_native_auto_start",
    }
    prohibited = (
        "arm",
        "disarm",
        "pause",
        "resume",
        "land",
        "rtl",
        "parameter",
        "setpoint",
        "generic",
    )
    assert not any(
        fragment in name for name in gateway_public | link_public for fragment in prohibited
    )


def test_concrete_link_emits_only_command_300_with_fixed_zero_parameters() -> None:
    sent: list[tuple[object, ...]] = []

    class Mav:
        def command_long_send(self, *values: object) -> None:
            sent.append(values)

    class Connection:
        mav = Mav()

        def close(self) -> None:
            pass

    link = PymavlinkNativeAutoStartLink(
        Connection(),
        TransportDescriptor("tcp:127.0.0.1:5760", TransportKind.SIK),
    )
    link.send_native_auto_start(TARGET)
    assert sent == [(1, 1, MAV_CMD_MISSION_START, 0, 0, 0, 0, 0, 0, 0, 0)]
    public = {
        name.lower() for name in dir(PymavlinkNativeAutoStartLink) if not name.startswith("_")
    }
    assert public == {"close", "is_connected", "receive", "send_native_auto_start"}

    source_root = Path(__file__).parents[4] / "src/skywriter"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
    for prohibited in (
        "MAV_CMD_DO_SET_MODE",
        "MAV_CMD_NAV_RETURN_TO_LAUNCH",
        "PARAM_SET",
        "SET_POSITION_TARGET",
        "send_command(",
    ):
        assert prohibited not in source
