from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from skywriter.application.arm import (
    NormalArmCommandResult,
    NormalArmService,
    NormalArmSnapshot,
    NormalArmState,
)
from skywriter.application.connected import (
    CancellationView,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
    MissionTransferEvidence,
)
from skywriter.application.prearm import (
    MAV_SYS_STATUS_PREARM_CHECK,
    PrearmCommandResult,
    PrearmReadinessService,
    PrearmRequestState,
)
from skywriter.application.telemetry import (
    HeartbeatTelemetry,
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


class FakePrearmGateway:
    def request_prearm_checks(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> PrearmCommandResult:
        return PrearmCommandResult(
            PrearmRequestState.ACCEPTED,
            "accepted, not arm approval",
            100.0,
            100.1,
            0,
        )


class FakeArmGateway:
    def __init__(self, result: NormalArmCommandResult) -> None:
        self.result = result
        self.calls = 0
        self.on_request: Callable[[], None] | None = None

    def request_normal_arm(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NormalArmCommandResult:
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


def connected(*, now_s: float = 100.0) -> ConnectedMissionSnapshot:
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
                MAV_SYS_STATUS_PREARM_CHECK,
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


def reviewed(context: ConnectedMissionSnapshot) -> PrearmReadinessService:
    service = PrearmReadinessService()
    service.request_prearm_checks(
        FakePrearmGateway(),
        context,
        now_s=100.0,
        cancellation=NeverCancelled(),
    )
    service.acknowledge_review(True, context, now_s=100.1)
    assert service.application_gate_ready_at(context, now_s=100.1)
    return service


def arm_result(state: NormalArmState = NormalArmState.ARMED) -> NormalArmCommandResult:
    ack_result = {
        NormalArmState.ARMED: 0,
        NormalArmState.ACKNOWLEDGED_NO_ARMED_TELEMETRY: 0,
        NormalArmState.TELEMETRY_DISAGREEMENT: 0,
        NormalArmState.REJECTED: 1,
        NormalArmState.UNSUPPORTED: 3,
    }.get(state)
    return NormalArmCommandResult(
        state,
        state.value,
        100.1,
        100.2,
        ack_result,
        armed_observed_at_s=100.2 if state is NormalArmState.ARMED else None,
    )


def test_normal_arm_requires_current_review_then_reports_only_telemetry_confirmed_armed() -> None:
    context = connected()
    readiness = reviewed(context)
    service = NormalArmService()
    gateway = FakeArmGateway(arm_result())

    service.synchronize_context(context, readiness, now_s=100.1, command_channel_idle=True)
    assert service.snapshot.request_available
    snapshot = service.request_normal_arm(
        gateway,
        context,
        readiness,
        now_s=100.1,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )

    assert snapshot.state is NormalArmState.ARMED
    assert snapshot.armed_observed_at_s == 100.2
    assert not snapshot.request_available
    assert gateway.calls == 1

    with pytest.raises(ValueError, match="telemetry proof"):
        NormalArmSnapshot(state=NormalArmState.ARMED, detail="invalid")


@pytest.mark.parametrize(
    "state",
    [
        NormalArmState.REJECTED,
        NormalArmState.UNSUPPORTED,
        NormalArmState.TIMED_OUT,
        NormalArmState.CANCELLED,
        NormalArmState.WRONG_TARGET,
        NormalArmState.WRONG_ACK,
        NormalArmState.STALE_LINK,
        NormalArmState.LINK_LOST,
        NormalArmState.ACKNOWLEDGED_NO_ARMED_TELEMETRY,
        NormalArmState.TELEMETRY_DISAGREEMENT,
    ],
)
def test_all_nonarmed_terminal_results_remain_distinct(state: NormalArmState) -> None:
    context = connected()
    readiness = reviewed(context)
    snapshot = NormalArmService().request_normal_arm(
        FakeArmGateway(arm_result(state)),
        context,
        readiness,
        now_s=100.1,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )

    assert snapshot.state is state
    assert snapshot.armed_observed_at_s is None


@pytest.mark.parametrize(
    ("mutate", "idle", "expected"),
    [
        (
            lambda value: replace(value, link_kind=TelemetryLinkKind.USB),
            True,
            NormalArmState.BLOCKED_WRONG_LINK,
        ),
        (
            lambda value: replace(value, link_connected=False),
            True,
            NormalArmState.LINK_LOST,
        ),
        (
            lambda value: replace(
                value, verification_state=ConnectedVerificationState.REVERIFY_REQUIRED
            ),
            True,
            NormalArmState.BLOCKED_MISSION,
        ),
        (
            lambda value: replace(
                value, selected_target=replace(value.selected_target, base_mode=128)
            ),
            True,
            NormalArmState.TELEMETRY_DISAGREEMENT,
        ),
        (
            lambda value: replace(
                value, selected_target=replace(value.selected_target, observed_at_s=90.0)
            ),
            True,
            NormalArmState.STALE_LINK,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(
                    value.selected_target,
                    vehicle=VehicleIdentity("mavlink-system-2-component-1"),
                ),
            ),
            True,
            NormalArmState.BLOCKED_IDENTITY,
        ),
        (lambda value: value, False, NormalArmState.BLOCKED_BUSY),
    ],
)
def test_every_arm_gate_fails_before_transmission(
    mutate: Callable[[ConnectedMissionSnapshot], ConnectedMissionSnapshot],
    idle: bool,
    expected: NormalArmState,
) -> None:
    original = connected()
    service = NormalArmService()
    gateway = FakeArmGateway(arm_result())

    service.request_normal_arm(
        gateway,
        mutate(original),
        reviewed(original),
        now_s=100.1,
        command_channel_idle=idle,
        cancellation=NeverCancelled(),
    )

    assert service.snapshot.state is expected
    assert gateway.calls == 0


def test_already_armed_selected_target_and_telemetry_block_before_gateway() -> None:
    context = connected()
    assert context.selected_target is not None
    assert context.telemetry is not None
    assert context.telemetry.heartbeat.value is not None
    armed = replace(
        context,
        selected_target=replace(context.selected_target, base_mode=128),
        telemetry=replace(
            context.telemetry,
            heartbeat=replace(
                context.telemetry.heartbeat,
                value=replace(context.telemetry.heartbeat.value, armed=True),
            ),
        ),
    )
    gateway = FakeArmGateway(arm_result())

    snapshot = NormalArmService().request_normal_arm(
        gateway,
        armed,
        reviewed(context),
        now_s=100.1,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )

    assert snapshot.state is NormalArmState.BLOCKED_ARMED
    assert gateway.calls == 0


def test_missing_review_mission_change_and_review_revision_change_invalidate_arm() -> None:
    context = connected()
    service = NormalArmService()
    gateway = FakeArmGateway(arm_result())
    unreviewed = PrearmReadinessService()
    service.request_normal_arm(
        gateway,
        context,
        unreviewed,
        now_s=100.1,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert service.snapshot.state.value == NormalArmState.BLOCKED_READINESS.value

    readiness = reviewed(context)
    service.synchronize_context(context, readiness, now_s=100.1, command_channel_idle=True)
    assert service.snapshot.request_available
    changed = replace(
        context,
        mission_revision=8,
        compiled=None,
        verification_state=ConnectedVerificationState.UNVERIFIED,
    )
    service.synchronize_context(changed, readiness, now_s=100.2, command_channel_idle=True)
    assert service.snapshot.state is NormalArmState.BLOCKED_MISSION
    assert not service.snapshot.request_available

    readiness = reviewed(context)
    service.synchronize_context(context, readiness, now_s=100.1, command_channel_idle=True)
    readiness.acknowledge_review(False, context, now_s=100.1)
    service.synchronize_context(context, readiness, now_s=100.1, command_channel_idle=True)
    assert service.snapshot.state is NormalArmState.BLOCKED_READINESS
    assert gateway.calls == 0


def test_double_click_or_repeated_request_during_pending_sends_once() -> None:
    context = connected()
    readiness = reviewed(context)
    service = NormalArmService()
    gateway = FakeArmGateway(arm_result(NormalArmState.REJECTED))

    def repeat() -> None:
        service.request_normal_arm(
            gateway,
            context,
            readiness,
            now_s=100.1,
            command_channel_idle=True,
            cancellation=NeverCancelled(),
        )

    gateway.on_request = repeat
    service.request_normal_arm(
        gateway,
        context,
        readiness,
        now_s=100.1,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )

    assert gateway.calls == 1
    assert service.snapshot.state is NormalArmState.REJECTED
    assert service.snapshot.repeated_request_ignored
