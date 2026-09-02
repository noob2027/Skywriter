from __future__ import annotations

import sys
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from skywriter.infrastructure.mavlink.connection import (
    CancellationToken,
    IncomingMessage,
    MavlinkAddress,
    MissionLink,
    PymavlinkMissionLink,
    TargetSelectionError,
    TransportDescriptor,
    TransportKind,
    TransportOpenError,
    TransportOpenFailureCode,
    discover_targets,
    open_pymavlink_link,
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


def test_selected_serial_baud_crosses_the_pymavlink_open_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_connection(endpoint: str, **options: object) -> RawConnection:
        observed.update(endpoint=endpoint, **options)
        return RawConnection([])

    fake_mavutil = SimpleNamespace(
        set_dialect=lambda dialect: observed.update(selected_dialect=dialect),
        mavlink=SimpleNamespace(WIRE_PROTOCOL_VERSION="2.0"),
        mavlink_connection=fake_connection,
    )
    monkeypatch.setitem(
        sys.modules,
        "pymavlink",
        SimpleNamespace(__version__="2.4.41", mavutil=fake_mavutil),
    )
    monkeypatch.delenv("SKYWRITER_PACKAGED_SMOKE_TEST", raising=False)
    monkeypatch.delenv("MAVLINK20", raising=False)

    link = open_pymavlink_link(TransportDescriptor("COM7", TransportKind.SIK, 57600))

    assert link.descriptor.baudrate == 57600
    assert observed == {
        "selected_dialect": "ardupilotmega",
        "endpoint": "COM7",
        "source_system": 255,
        "source_component": 190,
        "dialect": "ardupilotmega",
        "baud": 57600,
    }
    link.close()


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            PermissionError("Access is denied"),
            TransportOpenFailureCode.BUSY,
            "Close Mission Planner",
        ),
        (
            FileNotFoundError("No such file or directory"),
            TransportOpenFailureCode.UNAVAILABLE,
            "Refresh the port list",
        ),
    ],
)
def test_serial_open_failures_are_typed_for_busy_and_disappeared_ports(
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    code: TransportOpenFailureCode,
    message: str,
) -> None:
    fake_mavutil = SimpleNamespace(
        set_dialect=lambda _dialect: None,
        mavlink=SimpleNamespace(WIRE_PROTOCOL_VERSION="2.0"),
        mavlink_connection=lambda _endpoint, **_options: (_ for _ in ()).throw(error),
    )
    monkeypatch.setitem(
        sys.modules,
        "pymavlink",
        SimpleNamespace(__version__="2.4.41", mavutil=fake_mavutil),
    )
    monkeypatch.delenv("SKYWRITER_PACKAGED_SMOKE_TEST", raising=False)
    monkeypatch.delenv("MAVLINK20", raising=False)

    with pytest.raises(TransportOpenError) as raised:
        open_pymavlink_link(TransportDescriptor("COM7", TransportKind.USB, 115200))

    assert raised.value.code is code
    assert message in raised.value.detail


def test_link_contract_exposes_only_closed_mission_service_sends() -> None:
    send_methods = {name for name in MissionLink.__dict__ if name.startswith("send_")}

    assert send_methods == {
        "send_mission_count",
        "send_mission_item_int",
        "send_mission_request_list",
        "send_mission_request_int",
        "send_mission_ack",
    }


class RawMessage:
    def __init__(self, name: str, system_id: int, component_id: int) -> None:
        self._name = name
        self._system_id = system_id
        self._component_id = component_id

    def get_type(self) -> str:
        return self._name

    def get_srcSystem(self) -> int:
        return self._system_id

    def get_srcComponent(self) -> int:
        return self._component_id

    def to_dict(self) -> dict[str, object]:
        return {
            "mavpackettype": self._name,
            "type": 2,
            "autopilot": 3,
            "base_mode": 0,
        }


class RawConnection:
    def __init__(self, messages: list[RawMessage]) -> None:
        self.messages = messages
        self.close_count = 0
        self.mav: Any = object()

    def recv_match(self, *, blocking: bool, timeout: float) -> RawMessage | None:
        del blocking, timeout
        return self.messages.pop(0) if self.messages else None

    def close(self) -> None:
        self.close_count += 1


def test_pymavlink_boundary_discards_bad_data_before_valid_target_and_closes_once() -> None:
    connection = RawConnection([RawMessage("BAD_DATA", 0, 0), RawMessage("HEARTBEAT", 1, 1)])
    link = PymavlinkMissionLink(
        connection,
        TransportDescriptor("tcp:127.0.0.1:26000", TransportKind.USB),
    )

    received = link.receive(1.0)
    assert received is not None
    assert received.name == "HEARTBEAT"
    assert received.source == MavlinkAddress(1, 1)
    link.close()
    link.close()
    assert connection.close_count == 1
