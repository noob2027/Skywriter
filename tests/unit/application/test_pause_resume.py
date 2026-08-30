from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from skywriter.application.auto_start import (
    NativeAutoStartAuthorization,
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
from skywriter.application.pause_resume import (
    MAV_LANDED_STATE_LANDING,
    MAV_MISSION_STATE_ACTIVE,
    MAV_MISSION_STATE_COMPLETE,
    MAV_MISSION_STATE_PAUSED,
    NativePauseResumeAction,
    NativePauseResumeCommandResult,
    NativePauseResumeService,
    NativePauseResumeSnapshot,
    NativePauseResumeState,
)
from skywriter.application.telemetry import (
    ExtendedStateTelemetry,
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


class FakePauseResumeGateway:
    def __init__(
        self,
        pause_result: NativePauseResumeCommandResult,
        resume_result: NativePauseResumeCommandResult | None = None,
    ) -> None:
        self.pause_result = pause_result
        self.resume_result = resume_result or result(NativePauseResumeAction.RESUME)
        self.pause_calls = 0
        self.resume_calls = 0
        self.on_pause: Callable[[], None] | None = None
        self.on_resume: Callable[[], None] | None = None

    def request_native_pause(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativePauseResumeCommandResult:
        self.pause_calls += 1
        if self.on_pause is not None:
            self.on_pause()
        return self.pause_result

    def request_native_resume(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativePauseResumeCommandResult:
        self.resume_calls += 1
        if self.on_resume is not None:
            self.on_resume()
        return self.resume_result


def compiled() -> CompiledMission:
    def item(
        sequence: int,
        command: MissionCommand,
        *,
        current: bool,
    ) -> CompiledMissionItem:
        return CompiledMissionItem(
            sequence,
            MissionFrame.GLOBAL_RELATIVE_ALT_INT,
            command,
            current,
            True,
            0,
            0,
            0,
            0,
            515007292,
            -1246254,
            3,
            MissionType.MISSION,
        )

    return CompiledMission(
        (
            item(0, MissionCommand.NAV_TAKEOFF, current=True),
            item(1, MissionCommand.NAV_WAYPOINT, current=False),
            item(2, MissionCommand.NAV_LAND, current=False),
        )
    )


def connected(
    *,
    now_s: float = 100.2,
    mission_state: int = MAV_MISSION_STATE_ACTIVE,
    sequence: int = 2,
    mode_number: int = 3,
    armed: bool = True,
    landed_state: int | None = None,
) -> ConnectedMissionSnapshot:
    vehicle = VehicleIdentity("mavlink-system-1-component-1")
    target = ConnectedTarget(
        vehicle,
        1,
        1,
        TelemetryLinkKind.SIK,
        2,
        3,
        128 if armed else 0,
        now_s,
    )
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
                armed,
                mode_number,
                {3: "Auto", 9: "Land"}.get(mode_number, "Other"),
                4,
                2,
                3,
            ),
            now_s,
            3.0,
        ),
        TimedSignal.unavailable(2.0),
        TimedSignal.unavailable(10.0),
        TimedSignal.unavailable(60.0),
        TimedSignal(
            MissionProgressTelemetry(sequence, len(package.items), mission_state, 1),
            now_s,
            5.0,
        ),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        (
            TimedSignal(ExtendedStateTelemetry(landed_state, 0), now_s, 5.0)
            if landed_state is not None
            else TimedSignal.unavailable(5.0)
        ),
    )
    return ConnectedMissionSnapshot(
        mission_revision=7,
        compiled=logical,
        candidates=(target,),
        selected_target=target,
        link_kind=TelemetryLinkKind.SIK,
        link_connected=True,
        expected_package=package,
        transfer_evidence=MissionTransferEvidence(4, 1, True, *(["a" * 64] * 3)),
        verification_state=ConnectedVerificationState.SIK_VERIFIED,
        telemetry=telemetry,
    )


def auto_started(context: ConnectedMissionSnapshot) -> NativeAutoStartSnapshot:
    assert context.selected_target is not None
    assert context.transfer_evidence is not None
    authorization = NativeAutoStartAuthorization(
        context.selected_target.vehicle.value,
        context.selected_target.system_id,
        context.selected_target.component_id,
        context.mission_revision or 0,
        context.transfer_evidence.expected_digest,
        9,
        1,
        3,
    )
    return NativeAutoStartSnapshot(
        revision=11,
        state=NativeAutoStartState.RUNNING,
        detail="running",
        authorization=authorization,
        requested_at_s=99.8,
        completed_at_s=100.0,
        auto_observed_at_s=99.9,
        progress_observed_at_s=100.0,
        progress_sequence=1,
    )


def result(
    action: NativePauseResumeAction,
    state: NativePauseResumeState | None = None,
) -> NativePauseResumeCommandResult:
    state = (
        state
        or {
            NativePauseResumeAction.PAUSE: NativePauseResumeState.PAUSED,
            NativePauseResumeAction.RESUME: NativePauseResumeState.RUNNING,
        }[action]
    )
    success = state in (NativePauseResumeState.PAUSED, NativePauseResumeState.RUNNING)
    ack_result = {
        NativePauseResumeState.PAUSED: 0,
        NativePauseResumeState.RUNNING: 0,
        NativePauseResumeState.REJECTED: 4,
        NativePauseResumeState.UNSUPPORTED: 3,
    }.get(state)
    return NativePauseResumeCommandResult(
        action,
        state,
        state.value,
        100.2,
        100.4,
        ack_result,
        state_observed_at_s=100.4 if success else None,
        progress_sequence=2 if success else None,
    )


def test_pause_then_resume_requires_positive_pinned_states() -> None:
    running = connected()
    auto = auto_started(running)
    gateway = FakePauseResumeGateway(result(NativePauseResumeAction.PAUSE))
    service = NativePauseResumeService()

    service.synchronize_context(running, auto, now_s=100.2, command_channel_idle=True)
    assert service.snapshot.pause_available
    paused = service.request_native_pause(
        gateway,
        running,
        auto,
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert paused.state is NativePauseResumeState.PAUSED
    assert paused.resume_available

    paused_context = connected(mission_state=MAV_MISSION_STATE_PAUSED)
    resumed = service.request_native_resume(
        gateway,
        paused_context,
        auto_started(paused_context),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert resumed.state is NativePauseResumeState.RUNNING
    assert resumed.pause_available
    assert gateway.pause_calls == gateway.resume_calls == 1

    with pytest.raises(ValueError, match="mission-state telemetry"):
        NativePauseResumeSnapshot(
            state=NativePauseResumeState.PAUSED,
            detail="invalid",
            resume_available=True,
        )


def test_resume_without_a_positive_paused_transition_is_blocked_before_send() -> None:
    context = connected(mission_state=MAV_MISSION_STATE_PAUSED)
    gateway = FakePauseResumeGateway(result(NativePauseResumeAction.PAUSE))
    snapshot = NativePauseResumeService().request_native_resume(
        gateway,
        context,
        auto_started(context),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert snapshot.state is NativePauseResumeState.BLOCKED_NOT_PAUSED
    assert gateway.resume_calls == 0


@pytest.mark.parametrize(
    "state",
    [
        NativePauseResumeState.REJECTED,
        NativePauseResumeState.UNSUPPORTED,
        NativePauseResumeState.TIMED_OUT,
        NativePauseResumeState.CANCELLED,
        NativePauseResumeState.WRONG_TARGET,
        NativePauseResumeState.WRONG_ACK,
        NativePauseResumeState.STALE_LINK,
        NativePauseResumeState.LINK_LOST,
        NativePauseResumeState.ACKNOWLEDGED_NO_PAUSED_TELEMETRY,
        NativePauseResumeState.UNEXPECTED_MODE,
        NativePauseResumeState.MISSION_COMPLETED,
        NativePauseResumeState.LANDING,
        NativePauseResumeState.DISARMED,
        NativePauseResumeState.MISSION_MISMATCH,
        NativePauseResumeState.TELEMETRY_DISAGREEMENT,
    ],
)
def test_nonpaused_protocol_results_remain_distinct(state: NativePauseResumeState) -> None:
    context = connected()
    snapshot = NativePauseResumeService().request_native_pause(
        FakePauseResumeGateway(result(NativePauseResumeAction.PAUSE, state)),
        context,
        auto_started(context),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert snapshot.state is state
    assert not snapshot.pause_available
    assert not snapshot.resume_available


@pytest.mark.parametrize(
    ("mutate", "idle", "expected"),
    [
        (
            lambda value: replace(value, link_kind=TelemetryLinkKind.USB),
            True,
            NativePauseResumeState.BLOCKED_WRONG_LINK,
        ),
        (
            lambda value: replace(value, link_connected=False),
            True,
            NativePauseResumeState.LINK_LOST,
        ),
        (
            lambda value: replace(
                value, verification_state=ConnectedVerificationState.REVERIFY_REQUIRED
            ),
            True,
            NativePauseResumeState.BLOCKED_MISSION,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(value.selected_target, observed_at_s=90.0),
            ),
            True,
            NativePauseResumeState.STALE_LINK,
        ),
        (
            lambda value: connected(mode_number=5),
            True,
            NativePauseResumeState.UNEXPECTED_MODE,
        ),
        (
            lambda value: connected(mission_state=MAV_MISSION_STATE_COMPLETE),
            True,
            NativePauseResumeState.MISSION_COMPLETED,
        ),
        (
            lambda value: connected(mode_number=9),
            True,
            NativePauseResumeState.LANDING,
        ),
        (
            lambda value: connected(landed_state=MAV_LANDED_STATE_LANDING),
            True,
            NativePauseResumeState.LANDING,
        ),
        (
            lambda value: connected(armed=False),
            True,
            NativePauseResumeState.DISARMED,
        ),
        (lambda value: value, False, NativePauseResumeState.BLOCKED_BUSY),
    ],
)
def test_pause_context_races_fail_before_transmission(
    mutate: Callable[[ConnectedMissionSnapshot], ConnectedMissionSnapshot],
    idle: bool,
    expected: NativePauseResumeState,
) -> None:
    original = connected()
    changed = mutate(original)
    gateway = FakePauseResumeGateway(result(NativePauseResumeAction.PAUSE))
    snapshot = NativePauseResumeService().request_native_pause(
        gateway,
        changed,
        auto_started(changed),
        now_s=100.2,
        command_channel_idle=idle,
        cancellation=NeverCancelled(),
    )
    assert snapshot.state is expected
    assert gateway.pause_calls == 0


def test_target_and_progress_changes_fail_closed() -> None:
    original = connected()
    assert original.selected_target is not None
    changed_target = replace(
        original,
        selected_target=replace(
            original.selected_target,
            vehicle=VehicleIdentity("mavlink-system-2-component-1"),
        ),
    )
    service = NativePauseResumeService()
    gateway = FakePauseResumeGateway(result(NativePauseResumeAction.PAUSE))
    target_result = service.request_native_pause(
        gateway,
        changed_target,
        auto_started(original),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert target_result.state is NativePauseResumeState.BLOCKED_IDENTITY

    assert original.telemetry is not None
    out_of_bounds = replace(
        original,
        telemetry=replace(
            original.telemetry,
            mission=TimedSignal(MissionProgressTelemetry(99, 4, 3, 1), 100.2, 5.0),
        ),
    )
    progress_result = service.request_native_pause(
        gateway,
        out_of_bounds,
        auto_started(original),
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert progress_result.state is NativePauseResumeState.MISSION_MISMATCH
    assert gateway.pause_calls == 0


def test_repeated_pause_while_pending_sends_once() -> None:
    context = connected()
    auto = auto_started(context)
    service = NativePauseResumeService()
    gateway = FakePauseResumeGateway(
        result(NativePauseResumeAction.PAUSE, NativePauseResumeState.REJECTED)
    )

    def repeat() -> None:
        service.request_native_pause(
            gateway,
            context,
            auto,
            now_s=100.2,
            command_channel_idle=True,
            cancellation=NeverCancelled(),
        )

    gateway.on_pause = repeat
    service.request_native_pause(
        gateway,
        context,
        auto,
        now_s=100.2,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )
    assert gateway.pause_calls == 1
    assert service.snapshot.repeated_request_ignored
