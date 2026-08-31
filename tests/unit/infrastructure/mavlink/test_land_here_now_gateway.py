from __future__ import annotations

from collections.abc import Callable

import pytest

from skywriter.application.connected import ConnectedTarget
from skywriter.application.land_here_now import NativeLandHereNowState
from skywriter.application.pause_resume import (
    MAV_LANDED_STATE_LANDING,
    MAV_LANDED_STATE_ON_GROUND,
)
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.mavlink.connection import (
    MAV_CMD_NAV_LAND,
    MAV_CMD_REQUEST_MESSAGE,
    MAVLINK_MSG_ID_EXTENDED_SYS_STATE,
    IncomingMessage,
    MavlinkAddress,
    PymavlinkNativeLandHereNowLink,
    TransportDescriptor,
    TransportKind,
)
from skywriter.infrastructure.mavlink.land_here_now import (
    NativeLandHereNowGateway,
    NativeLandHereNowProtocolPolicy,
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


class FakeLandLink:
    descriptor = TransportDescriptor("scripted-sik", TransportKind.SIK)
    local_address = LOCAL

    def __init__(self, clock: FakeClock, events: list[Event]) -> None:
        self.clock = clock
        self.events = events
        self.connected = True
        self.sent: list[MavlinkAddress] = []
        self.state_requests: list[MavlinkAddress] = []

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

    def send_native_land_here_now(self, target: MavlinkAddress) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.sent.append(target)

    def request_native_landing_state(self, target: MavlinkAddress) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.state_requests.append(target)


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
    command: int = MAV_CMD_NAV_LAND,
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


def extended(landed_state: int) -> IncomingMessage:
    return message("EXTENDED_SYS_STATE", {"landed_state": landed_state, "vtol_state": 0})


def gateway(
    events: list[Event],
    *,
    policy: NativeLandHereNowProtocolPolicy | None = None,
) -> tuple[NativeLandHereNowGateway, FakeLandLink, FakeClock, Cancellation]:
    clock = FakeClock()
    link = FakeLandLink(clock, events)
    return NativeLandHereNowGateway(link, clock=clock, policy=policy), link, clock, Cancellation()


def request(
    command: NativeLandHereNowGateway,
    cancellation: Cancellation,
    *,
    selected: ConnectedTarget | None = None,
):
    return command.request_native_land_here_now(
        selected or target(),
        target_valid_for_s=3.0,
        cancellation=cancellation,
    )


@pytest.mark.parametrize(
    "proof",
    [
        [heartbeat(mode=9), extended(MAV_LANDED_STATE_LANDING)],
        [extended(MAV_LANDED_STATE_LANDING), heartbeat(mode=9)],
    ],
)
def test_accepted_ack_requires_later_land_mode_and_landing_state(
    proof: list[IncomingMessage],
) -> None:
    command, link, _, cancellation = gateway([heartbeat(), ack(0), *proof])
    result = request(command, cancellation)
    assert result.state is NativeLandHereNowState.LANDING
    assert result.ack_result == 0
    assert result.land_mode_observed_at_s is not None
    assert result.landed_state_observed_at_s is not None
    assert result.landed_state == MAV_LANDED_STATE_LANDING
    assert link.sent == [TARGET]
    assert link.state_requests == [TARGET]


def test_on_ground_after_ack_is_terminal_landed_proof() -> None:
    command, _, _, cancellation = gateway([ack(0), extended(MAV_LANDED_STATE_ON_GROUND)])
    result = request(command, cancellation)
    assert result.state is NativeLandHereNowState.LANDED
    assert result.landed_state == MAV_LANDED_STATE_ON_GROUND


def test_pre_ack_landing_telemetry_cannot_prove_result() -> None:
    policy = NativeLandHereNowProtocolPolicy(
        ack_timeout_s=0.5,
        telemetry_timeout_s=0.3,
        telemetry_request_interval_s=0.1,
        negative_capture_s=0.2,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(
        [heartbeat(mode=9), extended(MAV_LANDED_STATE_LANDING), ack(0)],
        policy=policy,
    )
    result = request(command, cancellation)
    assert result.state is NativeLandHereNowState.ACKNOWLEDGED_NO_LANDING_TELEMETRY


def test_duplicate_action_ack_after_acceptance_is_ignored() -> None:
    command, _, _, cancellation = gateway(
        [ack(0), ack(0), heartbeat(mode=9), extended(MAV_LANDED_STATE_LANDING)]
    )
    assert request(command, cancellation).state is NativeLandHereNowState.LANDING


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([ack(4)], NativeLandHereNowState.REJECTED),
        ([ack(3)], NativeLandHereNowState.UNSUPPORTED),
        ([ack(0, command=300)], NativeLandHereNowState.WRONG_ACK),
        (
            [ack(0, source=MavlinkAddress(2, 1))],
            NativeLandHereNowState.WRONG_TARGET,
        ),
        ([ack(0, target_system=7)], NativeLandHereNowState.WRONG_ACK),
    ],
)
def test_ack_failure_classes_remain_distinct(
    events: list[Event], expected: NativeLandHereNowState
) -> None:
    policy = NativeLandHereNowProtocolPolicy(
        ack_timeout_s=0.5,
        telemetry_timeout_s=0.5,
        telemetry_request_interval_s=0.1,
        negative_capture_s=0.2,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(events, policy=policy)
    assert request(command, cancellation).state is expected


def test_timeout_and_post_ack_telemetry_disagreement_are_distinct() -> None:
    policy = NativeLandHereNowProtocolPolicy(
        ack_timeout_s=0.3,
        telemetry_timeout_s=0.3,
        telemetry_request_interval_s=0.1,
        negative_capture_s=0.2,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway([], policy=policy)
    assert request(command, cancellation).state is NativeLandHereNowState.TIMED_OUT

    command, _, _, cancellation = gateway(
        [ack(0), heartbeat(mode=3), extended(2), None], policy=policy
    )
    assert request(command, cancellation).state is NativeLandHereNowState.TELEMETRY_DISAGREEMENT


def test_negative_ack_captures_native_status_text() -> None:
    policy = NativeLandHereNowProtocolPolicy(
        ack_timeout_s=0.5,
        telemetry_timeout_s=0.5,
        telemetry_request_interval_s=0.1,
        negative_capture_s=0.3,
        max_poll_s=0.1,
    )
    command, _, _, cancellation = gateway(
        [
            ack(4),
            message("STATUSTEXT", {"severity": 6, "text": "Failed to enter Land"}),
            None,
            None,
        ],
        policy=policy,
    )
    result = request(command, cancellation)
    assert result.state is NativeLandHereNowState.REJECTED
    assert [item.text for item in result.native_messages] == ["Failed to enter Land"]


def test_link_loss_and_cancellation_are_explicit() -> None:
    command, link, _, cancellation = gateway([ack(0), lambda: _disconnect(link)])
    assert request(command, cancellation).state is NativeLandHereNowState.LINK_LOST

    command, _, _, cancellation = gateway([lambda: _cancel(cancellation)])
    assert request(command, cancellation).state is NativeLandHereNowState.CANCELLED


def _disconnect(link: FakeLandLink) -> None:
    link.connected = False
    raise ConnectionError("radio lost")


def _cancel(cancellation: Cancellation) -> None:
    cancellation.cancelled = True
    return None


def test_disarmed_stale_and_wrong_transport_send_nothing() -> None:
    command, link, _, cancellation = gateway([])
    assert (
        request(command, cancellation, selected=target(base_mode=0)).state
        is NativeLandHereNowState.DISARMED
    )
    assert (
        request(command, cancellation, selected=target(observed_at_s=90.0)).state
        is NativeLandHereNowState.STALE_LINK
    )
    assert link.sent == []
    wrong = FakeLandLink(FakeClock(), [])
    wrong.descriptor = TransportDescriptor("usb", TransportKind.USB)
    with pytest.raises(ValueError, match="SiK"):
        NativeLandHereNowGateway(wrong, clock=FakeClock())


def test_closed_gateway_and_link_expose_only_dedicated_surface() -> None:
    gateway_public = {
        name.lower() for name in dir(NativeLandHereNowGateway) if not name.startswith("_")
    }
    link_public = {name.lower() for name in dir(FakeLandLink) if not name.startswith("_")}
    assert gateway_public == {"request_native_land_here_now"}
    assert link_public == {
        "descriptor",
        "is_connected",
        "local_address",
        "receive",
        "request_native_landing_state",
        "send_native_land_here_now",
    }


def test_concrete_link_emits_fixed_land_and_read_only_state_request() -> None:
    sent: list[tuple[object, ...]] = []

    class Mav:
        def command_long_send(self, *values: object) -> None:
            sent.append(values)

    class Connection:
        mav = Mav()

        def close(self) -> None:
            pass

    link = PymavlinkNativeLandHereNowLink(
        Connection(), TransportDescriptor("tcp:127.0.0.1:5760", TransportKind.SIK)
    )
    link.send_native_land_here_now(TARGET)
    link.request_native_landing_state(TARGET)
    assert sent == [
        (1, 1, MAV_CMD_NAV_LAND, 0, 0, 0, 0, 0, 0, 0, 0),
        (
            1,
            1,
            MAV_CMD_REQUEST_MESSAGE,
            0,
            MAVLINK_MSG_ID_EXTENDED_SYS_STATE,
            0,
            0,
            0,
            0,
            0,
            0,
        ),
    ]
    public = {
        name.lower() for name in dir(PymavlinkNativeLandHereNowLink) if not name.startswith("_")
    }
    assert public == {
        "close",
        "is_connected",
        "receive",
        "request_native_landing_state",
        "send_native_land_here_now",
    }
