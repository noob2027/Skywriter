from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from skywriter.application.arm import (
    NormalArmAuthorization,
    NormalArmSnapshot,
    NormalArmState,
)
from skywriter.application.auto_start import (
    NativeAutoStartCommandResult,
    NativeAutoStartService,
    NativeAutoStartSnapshot,
    NativeAutoStartState,
)
from skywriter.application.connected import (
    CancellationView,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
    MissionTransferEvidence,
)
from skywriter.application.prearm import PrearmReviewContext
from skywriter.application.telemetry import (
    HeartbeatTelemetry,
    MissionProgressTelemetry,
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


class FakeAutoStartGateway:
    def __init__(self, result: NativeAutoStartCommandResult) -> None:
        self.result = result
        self.calls = 0
        self.on_request: Callable[[], None] | None = None

    def request_native_auto_start(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativeAutoStartCommandResult:
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


def connected(*, now_s: float = 100.2, mode_number: int = 0) -> ConnectedMissionSnapshot:
    vehicle = VehicleIdentity("mavlink-system-1-component-1")
    target = ConnectedTarget(vehicle, 1, 1, TelemetryLinkKind.SIK, 2, 3, 128, now_s)
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
        TimedSignal(
            HeartbeatTelemetry(
                True, mode_number, "Auto" if mode_number == 3 else "Stabilize", 3, 2, 3
            ),
            now_s,
            3.0,
        ),
        TimedSignal.unavailable(2.0),
        TimedSignal.unavailable(10.0),
        TimedSignal.unavailable(60.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
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


def armed(context: ConnectedMissionSnapshot) -> NormalArmSnapshot:
    assert context.selected_target is not None
    assert context.transfer_evidence is not None
    review = PrearmReviewContext(
        context.selected_target.vehicle.value,
        context.selected_target.system_id,
        context.selected_target.component_id,
        context.mission_revision or 0,
        context.transfer_evidence.expected_digest,
    )
    return NormalArmSnapshot(
        revision=9,
        state=NormalArmState.ARMED,
        detail="telemetry-confirmed Armed",
        authorization=NormalArmAuthorization(review, 8),
        ack_result=0,
        requested_at_s=100.0,
        completed_at_s=100.1,
        armed_observed_at_s=100.1,
    )


def start_result(
    state: NativeAutoStartState = NativeAutoStartState.RUNNING,
) -> NativeAutoStartCommandResult:
    ack_result = {
        NativeAutoStartState.RUNNING: 0,
        NativeAutoStartState.ACKNOWLEDGED_NO_AUTO_TELEMETRY: 0,
        NativeAutoStartState.ACKNOWLEDGED_NO_MISSION_PROGRESS: 0,
        NativeAutoStartState.UNEXPECTED_MODE: 0,
        NativeAutoStartState.MISSION_MISMATCH: 0,
        NativeAutoStartState.DISARMED: 0,
        NativeAutoStartState.REJECTED: 2,
        NativeAutoStartState.UNSUPPORTED: 3,
    }.get(state)
    running = state is NativeAutoStartState.RUNNING
    return NativeAutoStartCommandResult(
        state,
        state.value,
        100.2,
        100.4,
        ack_result,
        auto_observed_at_s=100.3 if running else None,
        progress_observed_at_s=100.4 if running else None,
        progress_sequence=2 if running else None,
    )


def test_start_requires_current_arm_then_reports_only_auto_and_progress_confirmed_running() -> None:
    context = connected()
    arm = armed(context)
    service = NativeAutoStartService()
    gateway = FakeAutoStartGateway(start_result())

    service.synchronize_context(context, arm, now_s=100.2, command_channel_idle=True)
    assert service.snapshot.request_available
    snapshot = service.request_native_auto_start(
        gateway,
        context,
        arm,
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )

    assert snapshot.state is NativeAutoStartState.RUNNING
    assert snapshot.auto_observed_at_s == 100.3
    assert snapshot.progress_sequence == 2
    assert gateway.calls == 1
    with pytest.raises(ValueError, match="AUTO and progress"):
        NativeAutoStartSnapshot(state=NativeAutoStartState.RUNNING, detail="invalid")


@pytest.mark.parametrize(
    "state",
    [
        NativeAutoStartState.REJECTED,
        NativeAutoStartState.UNSUPPORTED,
        NativeAutoStartState.TIMED_OUT,
        NativeAutoStartState.CANCELLED,
        NativeAutoStartState.WRONG_TARGET,
        NativeAutoStartState.WRONG_ACK,
        NativeAutoStartState.STALE_LINK,
        NativeAutoStartState.LINK_LOST,
        NativeAutoStartState.ACKNOWLEDGED_NO_AUTO_TELEMETRY,
        NativeAutoStartState.ACKNOWLEDGED_NO_MISSION_PROGRESS,
        NativeAutoStartState.UNEXPECTED_MODE,
        NativeAutoStartState.MISSION_MISMATCH,
        NativeAutoStartState.DISARMED,
        NativeAutoStartState.TELEMETRY_DISAGREEMENT,
    ],
)
def test_nonrunning_protocol_results_remain_distinct(state: NativeAutoStartState) -> None:
    context = connected()
    snapshot = NativeAutoStartService().request_native_auto_start(
        FakeAutoStartGateway(start_result(state)),
        context,
        armed(context),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert snapshot.state is state
    assert snapshot.progress_sequence is None


@pytest.mark.parametrize(
    ("mutate", "idle", "expected"),
    [
        (
            lambda value: replace(value, link_kind=TelemetryLinkKind.USB),
            True,
            NativeAutoStartState.BLOCKED_WRONG_LINK,
        ),
        (
            lambda value: replace(value, link_connected=False),
            True,
            NativeAutoStartState.LINK_LOST,
        ),
        (
            lambda value: replace(
                value, verification_state=ConnectedVerificationState.REVERIFY_REQUIRED
            ),
            True,
            NativeAutoStartState.BLOCKED_MISSION,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(value.selected_target, base_mode=0),
                telemetry=replace(
                    value.telemetry,
                    heartbeat=replace(
                        value.telemetry.heartbeat,
                        value=replace(value.telemetry.heartbeat.value, armed=False),
                    ),
                ),
            ),
            True,
            NativeAutoStartState.BLOCKED_DISARMED,
        ),
        (
            lambda value: replace(
                value, selected_target=replace(value.selected_target, observed_at_s=90.0)
            ),
            True,
            NativeAutoStartState.STALE_LINK,
        ),
        (lambda value: value, False, NativeAutoStartState.BLOCKED_BUSY),
        (
            lambda value: replace(
                value,
                telemetry=replace(
                    value.telemetry,
                    heartbeat=replace(
                        value.telemetry.heartbeat,
                        value=replace(value.telemetry.heartbeat.value, mode_number=3),
                    ),
                ),
            ),
            True,
            NativeAutoStartState.BLOCKED_ALREADY_AUTO,
        ),
    ],
)
def test_start_gates_fail_before_transmission(
    mutate: Callable[[ConnectedMissionSnapshot], ConnectedMissionSnapshot],
    idle: bool,
    expected: NativeAutoStartState,
) -> None:
    original = connected()
    gateway = FakeAutoStartGateway(start_result())
    snapshot = NativeAutoStartService().request_native_auto_start(
        gateway,
        mutate(original),
        armed(original),
        now_s=100.2,
        command_channel_idle=idle,
        cancellation=NeverCancelled(),
    )
    assert snapshot.state is expected
    assert gateway.calls == 0


def test_arm_mission_identity_and_sequence_changes_fail_closed() -> None:
    context = connected()
    gateway = FakeAutoStartGateway(start_result())
    unarmed_evidence = replace(armed(context), state=NormalArmState.REJECTED)
    result = NativeAutoStartService().request_native_auto_start(
        gateway,
        context,
        unarmed_evidence,
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert result.state is NativeAutoStartState.BLOCKED_ARM

    assert context.expected_package is not None
    items = list(context.expected_package.items)
    items[1] = replace(items[1], command=int(MissionCommand.NAV_WAYPOINT))
    invalid = replace(
        context, expected_package=replace(context.expected_package, items=tuple(items))
    )
    result = NativeAutoStartService().request_native_auto_start(
        gateway,
        invalid,
        armed(context),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert result.state is NativeAutoStartState.BLOCKED_SEQUENCE
    assert gateway.calls == 0


def test_repeated_request_while_pending_sends_once() -> None:
    context = connected()
    arm = armed(context)
    service = NativeAutoStartService()
    gateway = FakeAutoStartGateway(start_result(NativeAutoStartState.REJECTED))

    def repeat() -> None:
        service.request_native_auto_start(
            gateway,
            context,
            arm,
            now_s=100.2,
            command_channel_idle=True,
            cancellation=NeverCancelled(),
        )

    gateway.on_request = repeat
    service.request_native_auto_start(
        gateway,
        context,
        arm,
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert gateway.calls == 1
    assert service.snapshot.repeated_request_ignored


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda value: replace(value, link_connected=False), NativeAutoStartState.LINK_LOST),
        (
            lambda value: replace(
                value, verification_state=ConnectedVerificationState.REVERIFY_REQUIRED
            ),
            NativeAutoStartState.MISSION_MISMATCH,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(value.selected_target, base_mode=0),
                telemetry=replace(
                    value.telemetry,
                    heartbeat=replace(
                        value.telemetry.heartbeat,
                        value=replace(value.telemetry.heartbeat.value, armed=False),
                    ),
                ),
            ),
            NativeAutoStartState.DISARMED,
        ),
        (
            lambda value: replace(
                value,
                telemetry=replace(
                    value.telemetry,
                    heartbeat=replace(
                        value.telemetry.heartbeat,
                        value=replace(value.telemetry.heartbeat.value, mode_number=1),
                    ),
                ),
            ),
            NativeAutoStartState.UNEXPECTED_MODE,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(
                    value.selected_target,
                    vehicle=VehicleIdentity("mavlink-system-2-component-1"),
                ),
            ),
            NativeAutoStartState.BLOCKED_IDENTITY,
        ),
        (
            lambda value: replace(
                value,
                telemetry=replace(
                    value.telemetry,
                    mission=TimedSignal(MissionProgressTelemetry(current_sequence=0), 100.2, 5.0),
                ),
            ),
            NativeAutoStartState.MISSION_MISMATCH,
        ),
    ],
)
def test_running_state_invalidates_on_adverse_context(
    mutate: Callable[[ConnectedMissionSnapshot], ConnectedMissionSnapshot],
    expected: NativeAutoStartState,
) -> None:
    prestart = connected()
    service = NativeAutoStartService()
    service.request_native_auto_start(
        FakeAutoStartGateway(start_result()),
        prestart,
        armed(prestart),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    auto_context = connected(mode_number=3)
    service.synchronize_context(
        mutate(auto_context), armed(prestart), now_s=100.2, command_channel_idle=True
    )
    assert service.snapshot.state is expected
