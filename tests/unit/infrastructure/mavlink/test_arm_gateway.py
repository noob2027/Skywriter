from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from skywriter.application.arm import NormalArmState
from skywriter.application.connected import ConnectedTarget
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.mavlink.arm import (
    MAV_CMD_COMPONENT_ARM_DISARM,
    NativeNormalArmGateway,
    NormalArmProtocolPolicy,
)
from skywriter.infrastructure.mavlink.connection import (
    IncomingMessage,
    MavlinkAddress,
    PymavlinkNormalArmLink,
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


class FakeArmLink:
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

    def send_normal_arm(self, target: MavlinkAddress) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.sent.append(target)


def target(*, observed_at_s: float = 100.0, base_mode: int = 0) -> ConnectedTarget:
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


def ack(result: int, *, command: int = MAV_CMD_COMPONENT_ARM_DISARM) -> IncomingMessage:
    return message(
        "COMMAND_ACK",
        {
            "command": command,
            "result": result,
            "target_system": LOCAL.system_id,
            "target_component": LOCAL.component_id,
        },
    )


def heartbeat(*, armed: bool, source: MavlinkAddress = TARGET) -> IncomingMessage:
    return message("HEARTBEAT", {"base_mode": 128 if armed else 0}, source=source)


def gateway(
    events: list[Event],
    *,
    policy: NormalArmProtocolPolicy | None = None,
) -> tuple[NativeNormalArmGateway, FakeArmLink, FakeClock, Cancellation]:
    clock = FakeClock()
    link = FakeArmLink(clock, events)
    cancellation = Cancellation()
    return NativeNormalArmGateway(link, clock=clock, policy=policy), link, clock, cancellation


def test_accepted_ack_requires_later_selected_target_armed_telemetry() -> None:
    command, link, _, cancellation = gateway([heartbeat(armed=True), ack(0), heartbeat(armed=True)])

    result = command.request_normal_arm(target(), target_valid_for_s=3.0, cancellation=cancellation)

    assert result.state is NormalArmState.ARMED
    assert result.ack_result == 0
    assert result.armed_observed_at_s is not None
    assert link.sent == [TARGET]


def test_accepted_ack_without_armed_telemetry_never_reports_armed() -> None:
    policy = NormalArmProtocolPolicy(ack_timeout_s=1.0, telemetry_timeout_s=1.0, max_poll_s=0.1)
    command, _, _, cancellation = gateway([ack(0), None, None, None], policy=policy)

    result = command.request_normal_arm(
        target(), target_valid_for_s=0.25, cancellation=cancellation
    )

    assert result.state is NormalArmState.ACKNOWLEDGED_NO_ARMED_TELEMETRY
    assert result.ack_result == 0
    assert result.armed_observed_at_s is None


def test_accepted_ack_with_fresh_disarmed_telemetry_is_disagreement() -> None:
    policy = NormalArmProtocolPolicy(ack_timeout_s=1.0, telemetry_timeout_s=0.3, max_poll_s=0.1)
    command, _, _, cancellation = gateway(
        [ack(0), heartbeat(armed=False), heartbeat(armed=False), heartbeat(armed=False)],
        policy=policy,
    )

    result = command.request_normal_arm(target(), target_valid_for_s=3.0, cancellation=cancellation)

    assert result.state is NormalArmState.TELEMETRY_DISAGREEMENT
    assert "remained disarmed" in result.detail


@pytest.mark.parametrize(
    ("ack_result", "expected"),
    [
        (1, NormalArmState.REJECTED),
        (2, NormalArmState.REJECTED),
        (3, NormalArmState.UNSUPPORTED),
        (4, NormalArmState.REJECTED),
        (5, NormalArmState.REJECTED),
        (6, NormalArmState.REJECTED),
    ],
)
def test_negative_and_unsupported_ack_results_remain_distinct(
    ack_result: int, expected: NormalArmState
) -> None:
    command, _, _, cancellation = gateway(
        [
            ack(ack_result),
            message("STATUSTEXT", {"severity": 2, "text": "PreArm: GPS not healthy"}),
            None,
        ]
    )

    result = command.request_normal_arm(target(), target_valid_for_s=3.0, cancellation=cancellation)

    assert result.state is expected
    assert result.ack_result == ack_result
    assert tuple(item.text for item in result.native_messages) == ("PreArm: GPS not healthy",)


def test_wrong_target_wrong_command_and_misaddressed_ack_fail_closed() -> None:
    wrong_target, _, _, cancellation = gateway(
        [
            message(
                "COMMAND_ACK",
                {"command": 400, "result": 0},
                source=MavlinkAddress(2, 1),
            )
        ]
    )
    assert (
        wrong_target.request_normal_arm(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is NormalArmState.WRONG_TARGET
    )

    wrong_command, _, _, cancellation = gateway([ack(0, command=401)])
    assert (
        wrong_command.request_normal_arm(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is NormalArmState.WRONG_ACK
    )

    misaddressed = message(
        "COMMAND_ACK",
        {"command": 400, "result": 0, "target_system": 42, "target_component": 1},
    )
    wrong_gcs, _, _, cancellation = gateway([misaddressed])
    assert (
        wrong_gcs.request_normal_arm(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is NormalArmState.WRONG_ACK
    )


def test_timeout_cancellation_disconnect_and_stale_link_are_distinct() -> None:
    policy = NormalArmProtocolPolicy(ack_timeout_s=0.5, telemetry_timeout_s=0.5, max_poll_s=0.1)
    command, _, _, cancellation = gateway([None] * 5, policy=policy)
    assert (
        command.request_normal_arm(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is NormalArmState.TIMED_OUT
    )

    def cancel() -> IncomingMessage | None:
        cancellation.cancelled = True
        return None

    command, _, _, cancellation = gateway([cancel, None], policy=policy)
    assert (
        command.request_normal_arm(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is NormalArmState.CANCELLED
    )

    command, _, _, cancellation = gateway([ConnectionError("radio lost")], policy=policy)
    assert (
        command.request_normal_arm(
            target(), target_valid_for_s=3.0, cancellation=cancellation
        ).state
        is NormalArmState.LINK_LOST
    )

    command, link, _, cancellation = gateway([], policy=policy)
    result = command.request_normal_arm(
        target(observed_at_s=90.0), target_valid_for_s=3.0, cancellation=cancellation
    )
    assert result.state is NormalArmState.STALE_LINK
    assert link.sent == []


def test_armed_before_request_and_disconnected_link_send_nothing() -> None:
    command, link, _, cancellation = gateway([])
    result = command.request_normal_arm(
        target(base_mode=128), target_valid_for_s=3.0, cancellation=cancellation
    )
    assert result.state is NormalArmState.TELEMETRY_DISAGREEMENT
    assert link.sent == []

    command, link, _, cancellation = gateway([])
    link.connected = False
    result = command.request_normal_arm(target(), target_valid_for_s=3.0, cancellation=cancellation)
    assert result.state is NormalArmState.LINK_LOST
    assert link.sent == []


def test_closed_gateway_and_link_expose_only_normal_arm_surface() -> None:
    gateway_public = {
        name.lower() for name in dir(NativeNormalArmGateway) if not name.startswith("_")
    }
    link_public = {name.lower() for name in dir(FakeArmLink) if not name.startswith("_")}
    assert gateway_public == {"request_normal_arm"}
    assert link_public == {
        "descriptor",
        "is_connected",
        "local_address",
        "receive",
        "send_normal_arm",
    }
    prohibited = ("disarm", "mode", "auto", "parameter", "rtl", "land", "generic", "setpoint")
    assert not any(
        fragment in name for name in gateway_public | link_public for fragment in prohibited
    )


def test_concrete_link_emits_only_exact_normal_command_with_no_caller_parameters() -> None:
    sent: list[tuple[object, ...]] = []

    class Mav:
        def command_long_send(self, *values: object) -> None:
            sent.append(values)

    class Connection:
        mav = Mav()

        def close(self) -> None:
            pass

    link = PymavlinkNormalArmLink(
        Connection(),
        TransportDescriptor("tcp:127.0.0.1:5760", TransportKind.SIK),
    )
    link.send_normal_arm(TARGET)

    assert sent == [(1, 1, MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0)]
    public = {name.lower() for name in dir(PymavlinkNormalArmLink) if not name.startswith("_")}
    assert public == {"close", "is_connected", "receive", "send_normal_arm"}

    source_root = Path(__file__).parents[4] / "src/skywriter"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
    assert "2989" not in source
    assert "21196" not in source
