from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from skywriter.compatibility.arducopter_4_6_3 import (
    HomeSnapshot,
    HomeUnresolved,
    HomeUnresolvedReason,
    NativeMissionItem,
    NativeMissionPackage,
    VehicleIdentity,
    canonicalize_expected,
    prepare_native_mission,
)
from skywriter.domain.compiled import (
    CompiledMission,
    CompiledMissionItem,
    MissionCommand,
    MissionFrame,
    MissionType,
)
from skywriter.infrastructure.mavlink.connection import (
    CancellationToken,
    IncomingMessage,
    MavlinkAddress,
    TargetCandidate,
    TransportDescriptor,
    TransportKind,
    UploadAuthorization,
)
from skywriter.infrastructure.mavlink.mission_protocol import (
    MissionFailureCode,
    MissionProtocol,
    MissionProtocolError,
    ProtocolPolicy,
)
from skywriter.infrastructure.mavlink.verification import MissionVerificationError

TARGET = MavlinkAddress(1, 1)
LOCAL = MavlinkAddress(255, 190)
VEHICLE = VehicleIdentity("mavlink-system-1-component-1")
POLICY = ProtocolPolicy(
    response_timeout_s=0.2,
    item_timeout_s=0.1,
    max_retries=2,
    operation_timeout_s=5.0,
)


class FakeClock:
    def __init__(self, now_s: float = 100.0) -> None:
        self.value = now_s

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


ScriptEvent = IncomingMessage | None | Callable[["ScriptedLink"], IncomingMessage | None]


class ScriptedLink:
    local_address = LOCAL

    def __init__(
        self,
        clock: FakeClock,
        events: list[ScriptEvent],
        *,
        transport: TransportKind = TransportKind.USB,
    ) -> None:
        self.clock = clock
        self.events = events
        self.descriptor = TransportDescriptor("scripted", transport)
        self.connected = True
        self.sent: list[tuple[str, tuple[object, ...]]] = []

    def is_connected(self) -> bool:
        return self.connected

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        if not self.connected:
            raise ConnectionError("scripted disconnect")
        if not self.events:
            self.clock.advance(timeout_s)
            return None
        event = self.events.pop(0)
        if event is None:
            self.clock.advance(timeout_s)
            return None
        if callable(event):
            return event(self)
        return event

    def send_mission_count(self, target: MavlinkAddress, *, count: int, mission_type: int) -> None:
        self.sent.append(("MISSION_COUNT", (target, count, mission_type)))

    def send_mission_item_int(
        self, target: MavlinkAddress, *, item: Mapping[str, int | float]
    ) -> None:
        self.sent.append(("MISSION_ITEM_INT", (target, dict(item))))

    def send_mission_request_list(self, target: MavlinkAddress, *, mission_type: int) -> None:
        self.sent.append(("MISSION_REQUEST_LIST", (target, mission_type)))

    def send_mission_request_int(
        self, target: MavlinkAddress, *, sequence: int, mission_type: int
    ) -> None:
        self.sent.append(("MISSION_REQUEST_INT", (target, sequence, mission_type)))

    def send_mission_ack(self, target: MavlinkAddress, *, result: int, mission_type: int) -> None:
        self.sent.append(("MISSION_ACK", (target, result, mission_type)))


def compiled_mission() -> CompiledMission:
    return CompiledMission(
        (
            CompiledMissionItem(
                sequence=0,
                frame=MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                command=MissionCommand.NAV_TAKEOFF,
                current=True,
                autocontinue=True,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                latitude_e7=473977420,
                longitude_e7=85455940,
                altitude_m=30,
                mission_type=MissionType.MISSION,
            ),
            CompiledMissionItem(
                sequence=1,
                frame=MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                command=MissionCommand.NAV_LAND,
                current=False,
                autocontinue=True,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                latitude_e7=473977500,
                longitude_e7=85456000,
                altitude_m=0,
                mission_type=MissionType.MISSION,
            ),
        )
    )


def home(*, captured_at_s: float = 99.0) -> HomeSnapshot:
    return HomeSnapshot(
        vehicle=VEHICLE,
        latitude_e7=473977420,
        longitude_e7=85455940,
        altitude_m=488.25,
        captured_at_s=captured_at_s,
        valid_for_s=10.0,
        authoritative=True,
    )


def package(now_s: float = 100.0) -> NativeMissionPackage:
    result = prepare_native_mission(
        compiled_mission(), target_vehicle=VEHICLE, home=home(), now_s=now_s
    )
    assert isinstance(result, NativeMissionPackage)
    return result


def authorization(
    *,
    observed_at_s: float = 99.5,
    transport: TransportKind = TransportKind.USB,
    armed: bool = False,
    approved: bool = True,
    vehicle: VehicleIdentity = VEHICLE,
) -> UploadAuthorization:
    return UploadAuthorization(
        target=TargetCandidate(
            address=TARGET,
            vehicle=vehicle,
            transport=transport,
            vehicle_type=2,
            autopilot_type=3,
            base_mode=128 if armed else 0,
            observed_at_s=observed_at_s,
        ),
        approved=approved,
        valid_for_s=2.0,
    )


def incoming(name: str, **fields: object) -> IncomingMessage:
    defaults: dict[str, object] = {
        "target_system": LOCAL.system_id,
        "target_component": LOCAL.component_id,
        "mission_type": 0,
    }
    defaults.update(fields)
    return IncomingMessage(name, TARGET, defaults)


def request(sequence: int, *, legacy: bool = True, **fields: object) -> IncomingMessage:
    return incoming("MISSION_REQUEST" if legacy else "MISSION_REQUEST_INT", seq=sequence, **fields)


def ack(result: int = 0, **fields: object) -> IncomingMessage:
    return incoming("MISSION_ACK", type=result, **fields)


def count(value: int, **fields: object) -> IncomingMessage:
    return incoming("MISSION_COUNT", count=value, **fields)


def item_message(item: NativeMissionItem, **overrides: object) -> IncomingMessage:
    fields: dict[str, object] = {
        "seq": item.sequence,
        "frame": item.frame,
        "command": item.command,
        "current": int(item.current),
        "autocontinue": int(item.autocontinue),
        "param1": item.param1,
        "param2": item.param2,
        "param3": item.param3,
        "param4": item.param4,
        "x": item.latitude_e7,
        "y": item.longitude_e7,
        "z": item.altitude_m,
    }
    fields.update(overrides)
    return incoming("MISSION_ITEM_INT", **fields)


def success_events(
    native_package: NativeMissionPackage,
    *,
    legacy: bool = True,
    downloaded: tuple[NativeMissionItem, ...] | None = None,
) -> list[ScriptEvent]:
    readback = downloaded or canonicalize_expected(native_package)
    return [
        *(request(sequence, legacy=legacy) for sequence in range(len(native_package.items))),
        ack(),
        count(len(readback)),
        *(item_message(item) for item in readback),
    ]


def protocol(
    events: Sequence[ScriptEvent],
    *,
    transport: TransportKind = TransportKind.USB,
    token: CancellationToken | None = None,
) -> tuple[MissionProtocol, ScriptedLink, FakeClock]:
    clock = FakeClock()
    link = ScriptedLink(clock, list(events), transport=transport)
    adapter = MissionProtocol(link, clock=clock, policy=POLICY, cancellation=token)
    return adapter, link, clock


def assert_failure(
    error: pytest.ExceptionInfo[MissionProtocolError], code: MissionFailureCode
) -> None:
    assert error.value.code is code


@pytest.mark.parametrize(
    ("gate", "link_transport"),
    [
        (authorization(approved=False), TransportKind.USB),
        (authorization(transport=TransportKind.SIK), TransportKind.SIK),
        (authorization(armed=True), TransportKind.USB),
        (authorization(observed_at_s=90.0), TransportKind.USB),
        (
            authorization(vehicle=VehicleIdentity("mavlink-system-2-component-1")),
            TransportKind.USB,
        ),
        (authorization(), TransportKind.SIK),
    ],
)
def test_upload_gate_fails_closed(gate: UploadAuthorization, link_transport: TransportKind) -> None:
    adapter, link, _ = protocol([], transport=link_transport)

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload(package(), authorization=gate)

    assert_failure(error, MissionFailureCode.AUTHORIZATION)
    assert link.sent == []


@pytest.mark.parametrize(
    "unresolved",
    [
        HomeUnresolved(HomeUnresolvedReason.UNCONNECTED, "not connected"),
        HomeUnresolved(HomeUnresolvedReason.UNAVAILABLE, "no home"),
    ],
)
def test_home_unresolved_never_produces_an_upload(
    unresolved: HomeUnresolved,
) -> None:
    adapter, link, _ = protocol([])

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload_and_verify(
            compiled_mission(), home=unresolved, authorization=authorization()
        )

    assert_failure(error, MissionFailureCode.HOME_UNRESOLVED)
    assert link.sent == []


def test_success_handles_legacy_requests_but_sends_only_int_items() -> None:
    native = package()
    adapter, link, _ = protocol(success_events(native, legacy=True))

    result = adapter.upload_and_verify(
        compiled_mission(), home=home(), authorization=authorization()
    )

    assert result.item_count == len(native.items)
    assert result.used_legacy_requests is True
    assert len(result.evidence.evidence_digest) == 64
    names = [name for name, _ in link.sent]
    assert names.count("MISSION_ITEM_INT") == len(native.items)
    assert "MISSION_ITEM" not in names
    assert names[-1] == "MISSION_ACK"


def test_success_handles_request_int() -> None:
    native = package()
    adapter, _, _ = protocol(success_events(native, legacy=False))

    result = adapter.upload_and_verify(
        compiled_mission(), home=home(), authorization=authorization()
    )

    assert result.used_legacy_requests is False


def test_duplicate_upload_request_resends_same_int_item() -> None:
    native = package()
    events = [request(0), request(0), request(1), request(2), ack()]
    adapter, link, _ = protocol(events)

    result = adapter.upload(native, authorization=authorization())

    assert result.used_legacy_requests is True
    sequences = [
        cast(dict[str, object], payload[1])["seq"]
        for name, payload in link.sent
        if name == "MISSION_ITEM_INT"
    ]
    assert sequences == [0, 0, 1, 2]


def test_timeout_retries_last_known_upload_message_then_fails() -> None:
    adapter, link, _ = protocol([None, None, None])

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload(package(), authorization=authorization())

    assert_failure(error, MissionFailureCode.TIMEOUT)
    assert [name for name, _ in link.sent] == ["MISSION_COUNT"] * 3


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (request(1), MissionFailureCode.WRONG_SEQUENCE),
        (request(0, mission_type=1), MissionFailureCode.WRONG_MISSION_TYPE),
        (ack(1), MissionFailureCode.NEGATIVE_ACK),
        (
            IncomingMessage(
                "MISSION_REQUEST",
                MavlinkAddress(2, 1),
                {"target_system": 255, "target_component": 190, "mission_type": 0, "seq": 0},
            ),
            MissionFailureCode.WRONG_TARGET,
        ),
        (
            request(0, target_system=254),
            MissionFailureCode.WRONG_TARGET,
        ),
    ],
)
def test_upload_rejects_adverse_protocol_responses(
    event: IncomingMessage, code: MissionFailureCode
) -> None:
    adapter, _, _ = protocol([event])

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload(package(), authorization=authorization())

    assert_failure(error, code)


def test_ack_before_all_items_is_not_success() -> None:
    adapter, _, _ = protocol([request(0), ack()])

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload(package(), authorization=authorization())

    assert_failure(error, MissionFailureCode.UNEXPECTED_MESSAGE)


def test_cancellation_aborts_and_never_returns_success() -> None:
    token = CancellationToken()

    def cancel(link: ScriptedLink) -> None:
        token.cancel()
        link.clock.advance(0.01)
        return None

    adapter, link, _ = protocol([request(0), cancel], token=token)

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload(package(), authorization=authorization())

    assert_failure(error, MissionFailureCode.CANCELLED)
    cancellation_acks = [
        payload for name, payload in link.sent if name == "MISSION_ACK" and payload[1] == 15
    ]
    assert len(cancellation_acks) == 1


def test_disconnect_aborts() -> None:
    def disconnect(link: ScriptedLink) -> None:
        link.connected = False
        raise ConnectionError("scripted disconnect")

    adapter, _, _ = protocol([disconnect])

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload(package(), authorization=authorization())

    assert_failure(error, MissionFailureCode.DISCONNECTED)


def test_armed_heartbeat_aborts_mid_upload() -> None:
    armed = IncomingMessage("HEARTBEAT", TARGET, {"type": 2, "autopilot": 3, "base_mode": 128})
    adapter, _, _ = protocol([request(0), armed])

    with pytest.raises(MissionProtocolError) as error:
        adapter.upload(package(), authorization=authorization())

    assert_failure(error, MissionFailureCode.ARMED)


def test_download_retries_duplicate_and_finishes_with_accepted_ack() -> None:
    native = canonicalize_expected(package())
    events: list[ScriptEvent] = [None, count(len(native)), item_message(native[0])]
    events.extend([item_message(native[0]), item_message(native[1]), item_message(native[2])])
    adapter, link, _ = protocol(events)

    result = adapter.download(TARGET)

    assert result.items == native
    assert [name for name, _ in link.sent].count("MISSION_REQUEST_LIST") == 2
    assert [name for name, _ in link.sent][-1] == "MISSION_ACK"


@pytest.mark.parametrize(
    ("event", "code"),
    [
        (ack(1), MissionFailureCode.NEGATIVE_ACK),
        (count(1, mission_type=1), MissionFailureCode.WRONG_MISSION_TYPE),
        (count(1, target_component=191), MissionFailureCode.WRONG_TARGET),
    ],
)
def test_download_rejects_bad_count_responses(
    event: IncomingMessage, code: MissionFailureCode
) -> None:
    adapter, _, _ = protocol([event])

    with pytest.raises(MissionProtocolError) as error:
        adapter.download(TARGET)

    assert_failure(error, code)


@pytest.mark.parametrize(
    ("item", "code"),
    [
        (incoming("MISSION_ITEM", seq=0), MissionFailureCode.WRONG_ITEM_ENCODING),
        (
            item_message(replace(canonicalize_expected(package())[0], sequence=1)),
            MissionFailureCode.WRONG_SEQUENCE,
        ),
        (
            item_message(canonicalize_expected(package())[0], mission_type=1),
            MissionFailureCode.WRONG_MISSION_TYPE,
        ),
    ],
)
def test_download_rejects_non_int_wrong_sequence_and_wrong_type(
    item: IncomingMessage, code: MissionFailureCode
) -> None:
    adapter, _, _ = protocol([count(1), item])

    with pytest.raises(MissionProtocolError) as error:
        adapter.download(TARGET)

    assert_failure(error, code)


def test_download_timeout_is_bounded() -> None:
    adapter, link, _ = protocol([count(1), None, None, None])

    with pytest.raises(MissionProtocolError) as error:
        adapter.download(TARGET)

    assert_failure(error, MissionFailureCode.TIMEOUT)
    assert [name for name, _ in link.sent].count("MISSION_REQUEST_INT") == 3


def test_acknowledged_upload_with_mismatched_readback_is_failure_with_digest() -> None:
    native = package()
    readback = list(canonicalize_expected(native))
    readback[1] = replace(readback[1], latitude_e7=readback[1].latitude_e7 + 1)
    adapter, _, _ = protocol(success_events(native, downloaded=tuple(readback)))

    with pytest.raises(MissionVerificationError) as error:
        adapter.upload_and_verify(compiled_mission(), home=home(), authorization=authorization())

    failure = error.value.failure
    assert failure.acknowledged is True
    assert failure.comparison.verified is False
    assert any(
        mismatch.location == "mission[0]" and mismatch.field == "latitude_e7"
        for mismatch in failure.mismatches
    )
    assert len(failure.evidence.evidence_digest) == 64


def test_adapter_source_has_only_closed_mission_emission_paths() -> None:
    source_root = Path(__file__).parents[4] / "src" / "skywriter" / "infrastructure" / "mavlink"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))
    forbidden = (
        "param_set_send",
        "command_long_send",
        "command_int_send",
        "set_mode_send",
        "mission_set_current_send",
        "set_position_target",
    )

    assert not any(fragment in source.lower() for fragment in forbidden)
