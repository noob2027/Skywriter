from __future__ import annotations

from collections.abc import Mapping

import pytest

from skywriter.infrastructure.mavlink.connection import (
    CancellationToken,
    IncomingMessage,
    MavlinkAddress,
    MissionLink,
    TargetSelectionError,
    TransportDescriptor,
    TransportKind,
    discover_targets,
    select_target,
)


class FakeClock:
    def __init__(self, now_s: float = 10.0) -> None:
        self.value = now_s

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class DiscoveryLink:
    descriptor = TransportDescriptor("COM-test", TransportKind.USB)
    local_address = MavlinkAddress(255, 190)

    def __init__(self, clock: FakeClock, messages: list[IncomingMessage]) -> None:
        self.clock = clock
        self.messages = messages

    def is_connected(self) -> bool:
        return True

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        if self.messages:
            self.clock.advance(0.01)
            return self.messages.pop(0)
        self.clock.advance(timeout_s)
        return None

    def send_mission_count(self, target: MavlinkAddress, *, count: int, mission_type: int) -> None:
        raise AssertionError("discovery must not send")

    def send_mission_item_int(
        self, target: MavlinkAddress, *, item: Mapping[str, int | float]
    ) -> None:
        raise AssertionError("discovery must not send")

    def send_mission_request_list(self, target: MavlinkAddress, *, mission_type: int) -> None:
        raise AssertionError("discovery must not send")

    def send_mission_request_int(
        self, target: MavlinkAddress, *, sequence: int, mission_type: int
    ) -> None:
        raise AssertionError("discovery must not send")

    def send_mission_ack(self, target: MavlinkAddress, *, result: int, mission_type: int) -> None:
        raise AssertionError("discovery must not send")


def heartbeat(system_id: int, component_id: int, *, base_mode: int = 0) -> IncomingMessage:
    return IncomingMessage(
        "HEARTBEAT",
        MavlinkAddress(system_id, component_id),
        {"type": 2, "autopilot": 3, "base_mode": base_mode},
    )


def test_discovery_collects_and_updates_candidates_without_auto_selecting() -> None:
    clock = FakeClock()
    link = DiscoveryLink(
        clock,
        [heartbeat(2, 1), heartbeat(1, 1), heartbeat(2, 1, base_mode=128)],
    )

    candidates = discover_targets(link, clock=clock, duration_s=1.0)

    assert [candidate.address for candidate in candidates] == [
        MavlinkAddress(1, 1),
        MavlinkAddress(2, 1),
    ]
    assert candidates[1].armed is True
    assert all(candidate.transport is TransportKind.USB for candidate in candidates)
    selected = select_target(candidates, MavlinkAddress(1, 1))
    assert selected.vehicle.value == "mavlink-system-1-component-1"


def test_explicit_selection_rejects_missing_target() -> None:
    with pytest.raises(TargetSelectionError):
        select_target((), MavlinkAddress(1, 1))


def test_discovery_cancellation_is_bounded_and_sends_nothing() -> None:
    token = CancellationToken()
    token.cancel()
    clock = FakeClock()
    link = DiscoveryLink(clock, [heartbeat(1, 1)])

    assert discover_targets(link, clock=clock, duration_s=1.0, cancellation=token) == ()
    assert link.messages


def test_transport_classification_is_explicit_not_inferred_from_endpoint() -> None:
    usb = TransportDescriptor("radio-looking-name", TransportKind.USB)
    sik = TransportDescriptor("COM4", TransportKind.SIK)

    assert usb.kind is TransportKind.USB
    assert sik.kind is TransportKind.SIK


def test_link_contract_exposes_only_closed_mission_service_sends() -> None:
    send_methods = {name for name in MissionLink.__dict__ if name.startswith("send_")}

    assert send_methods == {
        "send_mission_count",
        "send_mission_item_int",
        "send_mission_request_list",
        "send_mission_request_int",
        "send_mission_ack",
    }
