from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from skywriter.application.connected import (
    CancellationView,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
    MissionTransferEvidence,
)
from skywriter.application.prearm import (
    MAV_SYS_STATUS_PREARM_CHECK,
    NativePrearmAssessment,
    PrearmCommandResult,
    PrearmReadinessService,
    PrearmRequestState,
)
from skywriter.application.telemetry import (
    HeartbeatTelemetry,
    NativeStatusText,
    SensorStatusTelemetry,
    TelemetryLinkKind,
    TelemetrySnapshot,
    TimedSignal,
)
from skywriter.compatibility.arducopter_4_6_3 import (
    HomeSnapshot,
    NativeMissionPackage,
    VehicleIdentity,
    prepare_native_mission,
)
from skywriter.domain.compiled import (
    CompiledMission,
    CompiledMissionItem,
    MissionCommand,
    MissionFrame,
    MissionType,
)


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class FakeGateway:
    def __init__(self, result: PrearmCommandResult) -> None:
        self.result = result
        self.calls = 0
        self.on_request: Callable[[], None] | None = None

    def request_prearm_checks(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> PrearmCommandResult:
        self.calls += 1
        if self.on_request is not None:
            self.on_request()
        return self.result


def compiled() -> CompiledMission:
    return CompiledMission(
        (
            CompiledMissionItem(
                0,
                MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                MissionCommand.NAV_TAKEOFF,
                True,
                True,
                0,
                0,
                0,
                0,
                515007292,
                -1246254,
                3,
                MissionType.MISSION,
            ),
            CompiledMissionItem(
                1,
                MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                MissionCommand.NAV_LAND,
                False,
                True,
                0,
                0,
                0,
                0,
                515007292,
                -1246254,
                0,
                MissionType.MISSION,
            ),
        )
    )


def connected(
    *,
    now_s: float = 100.0,
    sensor_healthy: bool = True,
) -> ConnectedMissionSnapshot:
    vehicle = VehicleIdentity("mavlink-system-1-component-1")
    target = ConnectedTarget(vehicle, 1, 1, TelemetryLinkKind.SIK, 2, 3, 0, now_s)
    logical = compiled()
    package = prepare_native_mission(
        logical,
        target_vehicle=vehicle,
        home=HomeSnapshot(vehicle, 515007292, -1246254, 15.0, now_s, 60.0, True),
        now_s=now_s,
    )
    assert isinstance(package, NativeMissionPackage)
    health = MAV_SYS_STATUS_PREARM_CHECK if sensor_healthy else 0
    telemetry = TelemetrySnapshot(
        vehicle.value,
        1,
        1,
        TelemetryLinkKind.SIK,
        True,
        TimedSignal(HeartbeatTelemetry(False, 0, "Stabilize", 3, 2, 3), now_s, 3.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(10.0),
        TimedSignal.unavailable(60.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        TimedSignal(
            SensorStatusTelemetry(
                MAV_SYS_STATUS_PREARM_CHECK,
                MAV_SYS_STATUS_PREARM_CHECK,
                health,
            ),
            now_s,
            5.0,
        ),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
    )
    return ConnectedMissionSnapshot(
        mission_revision=7,
        compiled=logical,
        candidates=(target,),
        selected_target=target,
        link_kind=TelemetryLinkKind.SIK,
        link_connected=True,
        expected_package=package,
        transfer_evidence=MissionTransferEvidence(3, 1, True, *("a" * 64,) * 3),
        verification_state=ConnectedVerificationState.SIK_VERIFIED,
        telemetry=telemetry,
    )


def result(
    state: PrearmRequestState = PrearmRequestState.ACCEPTED,
    *,
    messages: tuple[NativeStatusText, ...] = (),
) -> PrearmCommandResult:
    ack_result = {
        PrearmRequestState.ACCEPTED: 0,
        PrearmRequestState.REJECTED: 1,
        PrearmRequestState.UNSUPPORTED: 3,
    }.get(state)
    return PrearmCommandResult(state, state.value, 100.0, 100.5, ack_result, messages)


def test_accepted_request_requires_healthy_native_evidence_and_explicit_review() -> None:
    service = PrearmReadinessService()
    context = connected()
    gateway = FakeGateway(result())

    service.request_prearm_checks(
        gateway,
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )

    assert service.snapshot.request_state is PrearmRequestState.ACCEPTED
    assert service.snapshot.native_assessment is NativePrearmAssessment.HEALTHY
    assert not service.snapshot.application_gate_ready
    service.acknowledge_review(True, context, now_s=100.5)
    assert service.snapshot.application_gate_ready
    assert service.application_gate_ready_at(context, now_s=100.5)
    assert gateway.calls == 1


def test_native_failure_and_telemetry_disagreement_never_open_application_gate() -> None:
    failure = NativeStatusText(2, "PreArm: GPS not healthy", 0, 0, 100.4)
    service = PrearmReadinessService()
    context = connected(sensor_healthy=False)
    service.request_prearm_checks(
        FakeGateway(result(messages=(failure,))),
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )
    assert service.snapshot.native_assessment is NativePrearmAssessment.FAILED
    service.acknowledge_review(True, context, now_s=100.5)
    assert not service.snapshot.application_gate_ready

    service = PrearmReadinessService()
    context = connected(sensor_healthy=True)
    service.request_prearm_checks(
        FakeGateway(result(messages=(failure,))),
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )
    assert service.snapshot.native_assessment is NativePrearmAssessment.CONFLICTING
    service.acknowledge_review(True, context, now_s=100.5)
    assert not service.snapshot.application_gate_ready


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: replace(value, link_kind=TelemetryLinkKind.USB),
            PrearmRequestState.BLOCKED_WRONG_LINK,
        ),
        (
            lambda value: replace(value, link_connected=False),
            PrearmRequestState.LINK_LOST,
        ),
        (
            lambda value: replace(
                value,
                verification_state=ConnectedVerificationState.REVERIFY_REQUIRED,
            ),
            PrearmRequestState.BLOCKED_MISSION,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(value.selected_target, base_mode=128),
            ),
            PrearmRequestState.BLOCKED_ARMED,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(value.selected_target, observed_at_s=90.0),
            ),
            PrearmRequestState.STALE_LINK,
        ),
    ],
)
def test_every_application_gate_fails_before_transmission(
    mutate: Callable[[ConnectedMissionSnapshot], ConnectedMissionSnapshot],
    expected: PrearmRequestState,
) -> None:
    service = PrearmReadinessService()
    gateway = FakeGateway(result())

    service.request_prearm_checks(
        gateway,
        mutate(connected()),
        now_s=100.0,
        cancellation=NeverCancelled(),
    )

    assert service.snapshot.request_state is expected
    assert gateway.calls == 0


def test_repeated_request_is_visible_and_does_not_send_twice() -> None:
    service = PrearmReadinessService()
    context = connected()
    gateway = FakeGateway(result())

    def repeat() -> None:
        service.request_prearm_checks(
            gateway,
            context,
            now_s=100.0,
            cancellation=NeverCancelled(),
        )

    gateway.on_request = repeat
    service.request_prearm_checks(
        gateway,
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )

    assert gateway.calls == 1
    assert service.snapshot.request_state is PrearmRequestState.ACCEPTED
    assert service.snapshot.repeated_request_ignored


def test_mission_edit_or_verified_digest_change_invalidates_review_acknowledgment() -> None:
    service = PrearmReadinessService()
    context = connected()
    service.request_prearm_checks(
        FakeGateway(result()),
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )
    service.acknowledge_review(True, context, now_s=100.5)
    assert service.snapshot.review_acknowledged

    changed = replace(
        context,
        mission_revision=8,
        compiled=None,
        verification_state=ConnectedVerificationState.UNVERIFIED,
    )
    service.synchronize_context(changed, now_s=100.5)
    assert not service.snapshot.review_acknowledged
    assert service.snapshot.request_state is PrearmRequestState.BLOCKED_MISSION

    service = PrearmReadinessService()
    context = connected()
    service.request_prearm_checks(
        FakeGateway(result()),
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )
    service.acknowledge_review(True, context, now_s=100.5)
    assert context.transfer_evidence is not None
    changed_digest = replace(
        context,
        transfer_evidence=replace(context.transfer_evidence, expected_digest="b" * 64),
    )
    assert not service.application_gate_ready_at(changed_digest, now_s=100.5)
    assert not service.snapshot.review_acknowledged


@pytest.mark.parametrize(
    "state",
    [
        PrearmRequestState.REJECTED,
        PrearmRequestState.UNSUPPORTED,
        PrearmRequestState.TIMED_OUT,
        PrearmRequestState.WRONG_TARGET,
        PrearmRequestState.WRONG_ACK,
        PrearmRequestState.LINK_LOST,
        PrearmRequestState.CANCELLED,
    ],
)
def test_nonaccepted_terminal_states_remain_distinct_and_never_ready(
    state: PrearmRequestState,
) -> None:
    service = PrearmReadinessService()
    context = connected()
    service.request_prearm_checks(
        FakeGateway(result(state)),
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )
    service.acknowledge_review(True, context, now_s=100.5)

    assert service.snapshot.request_state is state
    assert service.snapshot.review_acknowledged
    assert not service.snapshot.application_gate_ready
