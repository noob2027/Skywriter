from __future__ import annotations

from collections.abc import Callable

import pytest

from skywriter.application.connected import ConnectedTarget
from skywriter.application.prearm import PrearmRequestState
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.mavlink.connection import (
    IncomingMessage,
    MavlinkAddress,
    PymavlinkPrearmLink,
    TransportDescriptor,
    TransportKind,
)
from skywriter.infrastructure.mavlink.prearm import (
    MAV_CMD_RUN_PREARM_CHECKS,
    NativePrearmGateway,
    PrearmProtocolPolicy,
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


class FakePrearmLink:
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

    def send_prearm_checks(self, target: MavlinkAddress) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.sent.append(target)


def target(*, observed_at_s: float = 100.0) -> ConnectedTarget:
    return ConnectedTarget(
        VehicleIdentity("mavlink-system-1-component-1"),
        TARGET.system_id,
        TARGET.component_id,
        TelemetryLinkKind.SIK,
        2,
        3,
        0,
        observed_at_s,
    )


def message(
    name: str,
    fields: dict[str, object],
    *,
    source: MavlinkAddress = TARGET,
) -> IncomingMessage:
    return IncomingMessage(name, source, fields)


def ack(result: int, *, command: int = MAV_CMD_RUN_PREARM_CHECKS) -> IncomingMessage:
    return message(
        "COMMAND_ACK",
        {
            "command": command,
            "result": result,
            "target_system": LOCAL.system_id,
            "target_component": LOCAL.component_id,
        },
    )


def gateway(
    events: list[Event],
    *,
    policy: PrearmProtocolPolicy | None = None,
) -> tuple[NativePrearmGateway, FakePrearmLink, FakeClock, Cancellation]:
    clock = FakeClock()
    link = FakePrearmLink(clock, events)
    cancellation = Cancellation()
    return (
        NativePrearmGateway(link, clock=clock, policy=policy),
        link,
        clock,
        cancellation,
    )


@pytest.mark.parametrize(
    ("ack_result", "expected"),
    [
        (0, PrearmRequestState.ACCEPTED),
        (1, PrearmRequestState.REJECTED),
        (2, PrearmRequestState.REJECTED),
        (3, PrearmRequestState.UNSUPPORTED),
        (4, PrearmRequestState.REJECTED),
    ],
)
def test_matching_ack_results_are_classified_distinctly(
    ack_result: int, expected: PrearmRequestState
) -> None:
    command, link, _, cancellation = gateway([ack(ack_result), None])

    result = command.request_prearm_checks(
        target(), target_valid_for_s=3.0, cancellation=cancellation
    )

    assert result.state is expected
    assert result.ack_result == ack_result
    assert link.sent == [TARGET]


def test_associated_native_status_text_is_retained_without_becoming_approval() -> None:
    command, _, _, cancellation = gateway(
        [
            message("STATUSTEXT", {"severity": 2, "text": "PreArm: GPS not healthy"}),
            ack(0),
            message("STATUSTEXT", {"severity": 4, "text": "Safety Switch"}),
            None,
        ]
    )

    result = command.request_prearm_checks(
        target(), target_valid_for_s=3.0, cancellation=cancellation
    )

    assert result.state is PrearmRequestState.ACCEPTED
    assert tuple(item.text for item in result.native_messages) == (
        "PreArm: GPS not healthy",
        "Safety Switch",
    )
    assert "not arm approval" in result.detail


def test_wrong_target_wrong_command_and_misaddressed_ack_fail_closed() -> None:
    wrong_source, _, _, cancellation = gateway(
        [
            message(
                "COMMAND_ACK",
                {"command": MAV_CMD_RUN_PREARM_CHECKS, "result": 0},
                source=MavlinkAddress(2, 1),
            )
        ]
    )
    assert (
        wrong_source.request_prearm_checks(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is PrearmRequestState.WRONG_TARGET
    )

    wrong_command, _, _, cancellation = gateway([ack(0, command=400)])
    assert (
        wrong_command.request_prearm_checks(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is PrearmRequestState.WRONG_ACK
    )

    misaddressed = message(
        "COMMAND_ACK",
        {
            "command": MAV_CMD_RUN_PREARM_CHECKS,
            "result": 0,
            "target_system": 42,
            "target_component": 1,
        },
    )
    wrong_gcs, _, _, cancellation = gateway([misaddressed])
    assert (
        wrong_gcs.request_prearm_checks(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is PrearmRequestState.WRONG_ACK
    )


def test_timeout_stale_disconnect_and_cancellation_are_distinct() -> None:
    short = PrearmProtocolPolicy(ack_timeout_s=1.0, post_ack_capture_s=0.1, max_poll_s=0.25)
    command, _, _, cancellation = gateway([None, None, None, None], policy=short)
    result = command.request_prearm_checks(
        target(), target_valid_for_s=10.0, cancellation=cancellation
    )
    assert result.state is PrearmRequestState.TIMED_OUT

    command, _, _, cancellation = gateway([None, None, None, None], policy=short)
    result = command.request_prearm_checks(
        target(), target_valid_for_s=0.3, cancellation=cancellation
    )
    assert result.state is PrearmRequestState.STALE_LINK

    command, _, _, cancellation = gateway([ConnectionError("radio lost")], policy=short)
    result = command.request_prearm_checks(
        target(), target_valid_for_s=3.0, cancellation=cancellation
    )
    assert result.state is PrearmRequestState.LINK_LOST

    def cancel() -> IncomingMessage | None:
        cancellation.cancelled = True
        return None

    command, _, _, cancellation = gateway([cancel, None], policy=short)
    result = command.request_prearm_checks(
        target(), target_valid_for_s=3.0, cancellation=cancellation
    )
    assert result.state is PrearmRequestState.CANCELLED


def test_initial_stale_or_disconnected_link_sends_nothing() -> None:
    command, link, _, cancellation = gateway([])
    result = command.request_prearm_checks(
        target(observed_at_s=90.0), target_valid_for_s=3.0, cancellation=cancellation
    )
    assert result.state is PrearmRequestState.STALE_LINK
    assert link.sent == []

    command, link, _, cancellation = gateway([])
    link.connected = False
    result = command.request_prearm_checks(
        target(), target_valid_for_s=3.0, cancellation=cancellation
    )
    assert result.state is PrearmRequestState.LINK_LOST
    assert link.sent == []


def test_gateway_and_link_protocol_expose_no_generic_or_prohibited_surface() -> None:
    gateway_public = {name.lower() for name in dir(NativePrearmGateway) if not name.startswith("_")}
    link_public = {name.lower() for name in dir(FakePrearmLink) if not name.startswith("_")}
    assert gateway_public == {"request_prearm_checks"}
    assert link_public == {
        "descriptor",
        "is_connected",
        "local_address",
        "receive",
        "send_prearm_checks",
    }
    prohibited = ("arm", "disarm", "mode", "auto", "parameter", "rtl", "land", "generic")
    unapproved_public = (gateway_public | link_public) - {
        "request_prearm_checks",
        "send_prearm_checks",
    }
    assert not any(fragment in name for name in unapproved_public for fragment in prohibited)


def test_concrete_pymavlink_link_emits_only_exact_command_401_with_zero_parameters() -> None:
    sent: list[tuple[object, ...]] = []

    class Mav:
        def command_long_send(self, *values: object) -> None:
            sent.append(values)

    class Connection:
        mav = Mav()

        def close(self) -> None:
            pass

    link = PymavlinkPrearmLink(
        Connection(),
        TransportDescriptor("tcp:127.0.0.1:5760", TransportKind.SIK),
    )
    link.send_prearm_checks(TARGET)

    assert sent == [(1, 1, MAV_CMD_RUN_PREARM_CHECKS, 0, 0, 0, 0, 0, 0, 0, 0)]
    public = {name.lower() for name in dir(PymavlinkPrearmLink) if not name.startswith("_")}
    assert public == {"close", "is_connected", "receive", "send_prearm_checks"}
