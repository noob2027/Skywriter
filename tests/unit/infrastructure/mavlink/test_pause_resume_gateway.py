from __future__ import annotations

from collections.abc import Callable

import pytest

from skywriter.application.connected import ConnectedTarget
from skywriter.application.pause_resume import (
    MAV_MISSION_STATE_ACTIVE,
    MAV_MISSION_STATE_COMPLETE,
    MAV_MISSION_STATE_PAUSED,
    NativePauseResumeAction,
    NativePauseResumeCommandResult,
    NativePauseResumeState,
)
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.mavlink.connection import (
    MAV_CMD_DO_PAUSE_CONTINUE,
    IncomingMessage,
    MavlinkAddress,
    PymavlinkNativePauseResumeLink,
    TransportDescriptor,
    TransportKind,
)
from skywriter.infrastructure.mavlink.pause_resume import (
    NativePauseResumeGateway,
    NativePauseResumeProtocolPolicy,
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


class FakePauseResumeLink:
    descriptor = TransportDescriptor("scripted-sik", TransportKind.SIK)
    local_address = LOCAL

    def __init__(self, clock: FakeClock, events: list[Event]) -> None:
        self.clock = clock
        self.events = events
        self.connected = True
        self.sent: list[tuple[NativePauseResumeAction, MavlinkAddress]] = []

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

    def send_native_pause(self, target: MavlinkAddress) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.sent.append((NativePauseResumeAction.PAUSE, target))

    def send_native_resume(self, target: MavlinkAddress) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.sent.append((NativePauseResumeAction.RESUME, target))


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


def ack(
    result: int,
    *,
    command: int = MAV_CMD_DO_PAUSE_CONTINUE,
    source: MavlinkAddress = TARGET,
    target_system: int = LOCAL.system_id,
) -> IncomingMessage:
    return message(
        "COMMAND_ACK",
        {
            "command": command,
            "result": result,
            "target_system": target_system,
            "target_component": LOCAL.component_id,
        },
        source=source,
    )


def heartbeat(*, mode: int = 3, armed: bool = True) -> IncomingMessage:
    return message(
        "HEARTBEAT",
        {"base_mode": 128 if armed else 0, "custom_mode": mode},
    )


def mission_state(state: int, *, sequence: int = 2, total: int = 3) -> IncomingMessage:
    return message(
        "MISSION_CURRENT",
        {"seq": sequence, "total": total, "mission_state": state, "mission_mode": 1},
    )


def gateway(
    events: list[Event],
    *,
    policy: NativePauseResumeProtocolPolicy | None = None,
) -> tuple[NativePauseResumeGateway, FakePauseResumeLink, FakeClock, Cancellation]:
    clock = FakeClock()
    link = FakePauseResumeLink(clock, events)
    return NativePauseResumeGateway(link, clock=clock, policy=policy), link, clock, Cancellation()


def request(
    command: NativePauseResumeGateway,
    action: NativePauseResumeAction,
    cancellation: Cancellation,
    *,
    selected: ConnectedTarget | None = None,
    item_count: int = 4,
) -> NativePauseResumeCommandResult:
    method = (
        command.request_native_pause
        if action is NativePauseResumeAction.PAUSE
        else command.request_native_resume
    )
    return method(
        selected or target(),
        expected_item_count=item_count,
        target_valid_for_s=3.0,
        cancellation=cancellation,
    )


@pytest.mark.parametrize(
    ("action", "state_value", "expected"),
    [
        (
            NativePauseResumeAction.PAUSE,
            MAV_MISSION_STATE_PAUSED,
            NativePauseResumeState.PAUSED,
        ),
        (
            NativePauseResumeAction.RESUME,
            MAV_MISSION_STATE_ACTIVE,
            NativePauseResumeState.RUNNING,
        ),
    ],
)
def test_accepted_ack_requires_later_exact_mission_state(
    action: NativePauseResumeAction,
    state_value: int,
    expected: NativePauseResumeState,
) -> None:
    command, link, _, cancellation = gateway(
        [heartbeat(), ack(0), heartbeat(), mission_state(state_value)]
    )
    result = request(command, action, cancellation)
    assert result.state is expected
    assert result.ack_result == 0
    assert result.progress_sequence == 2
    assert result.state_observed_at_s is not None
    assert link.sent == [(action, TARGET)]


def test_pre_ack_paused_message_cannot_prove_pause() -> None:
    policy = NativePauseResumeProtocolPolicy(
        ack_timeout_s=0.5,
        telemetry_timeout_s=0.3,
        negative_capture_s=0.2,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(
        [mission_state(MAV_MISSION_STATE_PAUSED), ack(0), heartbeat()],
        policy=policy,
    )
    result = request(command, NativePauseResumeAction.PAUSE, cancellation)
    assert result.state is NativePauseResumeState.ACKNOWLEDGED_NO_PAUSED_TELEMETRY


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([ack(4)], NativePauseResumeState.REJECTED),
        ([ack(3)], NativePauseResumeState.UNSUPPORTED),
        ([ack(0, command=300)], NativePauseResumeState.WRONG_ACK),
        (
            [ack(0, source=MavlinkAddress(2, 1))],
            NativePauseResumeState.WRONG_TARGET,
        ),
        ([ack(0, target_system=7)], NativePauseResumeState.WRONG_ACK),
    ],
)
def test_ack_failure_classes_remain_distinct(
    events: list[Event],
    expected: NativePauseResumeState,
) -> None:
    policy = NativePauseResumeProtocolPolicy(
        ack_timeout_s=0.5,
        telemetry_timeout_s=0.5,
        negative_capture_s=0.2,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(events, policy=policy)
    assert request(command, NativePauseResumeAction.PAUSE, cancellation).state is expected


def test_negative_ack_captures_native_status_text() -> None:
    policy = NativePauseResumeProtocolPolicy(
        ack_timeout_s=0.5,
        telemetry_timeout_s=0.5,
        negative_capture_s=0.3,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(
        [
            ack(4),
            message("STATUSTEXT", {"severity": 6, "text": "Failed to pause"}),
            None,
            None,
        ],
        policy=policy,
    )
    result = request(command, NativePauseResumeAction.PAUSE, cancellation)
    assert result.state is NativePauseResumeState.REJECTED
    assert [item.text for item in result.native_messages] == ["Failed to pause"]


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([ack(0), heartbeat(mode=5)], NativePauseResumeState.UNEXPECTED_MODE),
        ([ack(0), heartbeat(mode=9)], NativePauseResumeState.LANDING),
        ([ack(0), heartbeat(armed=False)], NativePauseResumeState.DISARMED),
        (
            [ack(0), mission_state(MAV_MISSION_STATE_COMPLETE)],
            NativePauseResumeState.MISSION_COMPLETED,
        ),
        (
            [ack(0), mission_state(MAV_MISSION_STATE_PAUSED, sequence=99)],
            NativePauseResumeState.MISSION_MISMATCH,
        ),
        (
            [ack(0), mission_state(MAV_MISSION_STATE_PAUSED, total=9)],
            NativePauseResumeState.MISSION_MISMATCH,
        ),
    ],
)
def test_post_ack_flight_state_races_fail_closed(
    events: list[Event],
    expected: NativePauseResumeState,
) -> None:
    command, _, _, cancellation = gateway(events)
    assert request(command, NativePauseResumeAction.PAUSE, cancellation).state is expected


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (heartbeat(mode=5), NativePauseResumeState.UNEXPECTED_MODE),
        (heartbeat(mode=9), NativePauseResumeState.LANDING),
        (heartbeat(armed=False), NativePauseResumeState.DISARMED),
        (
            mission_state(MAV_MISSION_STATE_COMPLETE),
            NativePauseResumeState.MISSION_COMPLETED,
        ),
        (
            mission_state(MAV_MISSION_STATE_ACTIVE, sequence=3),
            NativePauseResumeState.LANDING,
        ),
        (
            message("EXTENDED_SYS_STATE", {"landed_state": 4}),
            NativePauseResumeState.LANDING,
        ),
    ],
)
def test_pre_ack_flight_state_races_fail_closed(
    event: IncomingMessage,
    expected: NativePauseResumeState,
) -> None:
    command, _, _, cancellation = gateway([event])
    assert request(command, NativePauseResumeAction.PAUSE, cancellation).state is expected


def test_timeout_cancellation_link_loss_and_stale_target_are_distinct() -> None:
    policy = NativePauseResumeProtocolPolicy(
        ack_timeout_s=0.3,
        telemetry_timeout_s=0.3,
        negative_capture_s=0.2,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway([], policy=policy)
    assert (
        request(command, NativePauseResumeAction.PAUSE, cancellation).state
        is NativePauseResumeState.TIMED_OUT
    )

    command, _, _, cancellation = gateway([], policy=policy)
    cancellation.cancelled = True
    assert (
        request(command, NativePauseResumeAction.PAUSE, cancellation).state
        is NativePauseResumeState.CANCELLED
    )

    command, _, _, cancellation = gateway([ConnectionError("radio lost")], policy=policy)
    assert (
        request(command, NativePauseResumeAction.PAUSE, cancellation).state
        is NativePauseResumeState.LINK_LOST
    )

    command, _, _, cancellation = gateway([], policy=policy)
    assert (
        request(
            command,
            NativePauseResumeAction.PAUSE,
            cancellation,
            selected=target(observed_at_s=90.0),
        ).state
        is NativePauseResumeState.STALE_LINK
    )


def test_disarmed_invalid_package_and_wrong_transport_send_nothing() -> None:
    command, link, _, cancellation = gateway([])
    assert (
        request(
            command,
            NativePauseResumeAction.PAUSE,
            cancellation,
            selected=target(base_mode=0),
        ).state
        is NativePauseResumeState.DISARMED
    )
    assert (
        request(command, NativePauseResumeAction.PAUSE, cancellation, item_count=2).state
        is NativePauseResumeState.MISSION_MISMATCH
    )
    assert link.sent == []
    wrong_link = FakePauseResumeLink(FakeClock(), [])
    wrong_link.descriptor = TransportDescriptor("usb", TransportKind.USB)
    with pytest.raises(ValueError, match="SiK"):
        NativePauseResumeGateway(wrong_link, clock=FakeClock())


def test_closed_gateway_and_link_expose_only_dedicated_pause_resume_surface() -> None:
    gateway_public = {
        name.lower() for name in dir(NativePauseResumeGateway) if not name.startswith("_")
    }
    link_public = {name.lower() for name in dir(FakePauseResumeLink) if not name.startswith("_")}
    assert gateway_public == {"request_native_pause", "request_native_resume"}
    assert link_public == {
        "descriptor",
        "is_connected",
        "local_address",
        "receive",
        "send_native_pause",
        "send_native_resume",
    }


def test_concrete_link_emits_only_command_193_with_fixed_selectors() -> None:
    sent: list[tuple[object, ...]] = []

    class Mav:
        def command_long_send(self, *values: object) -> None:
            sent.append(values)

    class Connection:
        mav = Mav()

        def close(self) -> None:
            pass

    link = PymavlinkNativePauseResumeLink(
        Connection(),
        TransportDescriptor("tcp:127.0.0.1:5760", TransportKind.SIK),
    )
    link.send_native_pause(TARGET)
    link.send_native_resume(TARGET)
    assert sent == [
        (1, 1, MAV_CMD_DO_PAUSE_CONTINUE, 0, 0, 0, 0, 0, 0, 0, 0),
        (1, 1, MAV_CMD_DO_PAUSE_CONTINUE, 0, 1, 0, 0, 0, 0, 0, 0),
    ]
    public = {
        name.lower() for name in dir(PymavlinkNativePauseResumeLink) if not name.startswith("_")
    }
    assert public == {
        "close",
        "is_connected",
        "receive",
        "send_native_pause",
        "send_native_resume",
    }
