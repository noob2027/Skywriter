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
from skywriter.application.land_here_now import (
    MAV_LANDED_STATE_IN_AIR,
    NativeLandHereNowCommandResult,
    NativeLandHereNowService,
    NativeLandHereNowState,
)
from skywriter.application.pause_resume import (
    MAV_LANDED_STATE_LANDING,
    MAV_LANDED_STATE_ON_GROUND,
    MAV_MISSION_STATE_ACTIVE,
    MAV_MISSION_STATE_COMPLETE,
    MAV_MISSION_STATE_PAUSED,
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


class FakeLandGateway:
    def __init__(self, command_result: NativeLandHereNowCommandResult) -> None:
        self.command_result = command_result
        self.calls = 0
        self.on_request: Callable[[], None] | None = None

    def request_native_land_here_now(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativeLandHereNowCommandResult:
        self.calls += 1
        if self.on_request is not None:
            self.on_request()
        return self.command_result


def compiled() -> CompiledMission:
    def item(sequence: int, command: MissionCommand, *, current: bool) -> CompiledMissionItem:
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
    landed_state: int | None = MAV_LANDED_STATE_IN_AIR,
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
            MissionProgressTelemetry(sequence, len(package.items) - 1, mission_state, 1),
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
    return NativeAutoStartSnapshot(
        revision=11,
        state=NativeAutoStartState.RUNNING,
        detail="running",
        authorization=NativeAutoStartAuthorization(
            context.selected_target.vehicle.value,
            context.selected_target.system_id,
            context.selected_target.component_id,
            context.mission_revision or 0,
            context.transfer_evidence.expected_digest,
            9,
            1,
            3,
        ),
        requested_at_s=99.8,
        completed_at_s=100.0,
        auto_observed_at_s=99.9,
        progress_observed_at_s=100.0,
        progress_sequence=1,
    )


def result(
    state: NativeLandHereNowState = NativeLandHereNowState.LANDING,
) -> NativeLandHereNowCommandResult:
    return NativeLandHereNowCommandResult(
        state,
        state.value,
        100.3,
        100.5,
        {
            NativeLandHereNowState.LANDING: 0,
            NativeLandHereNowState.LANDED: 0,
            NativeLandHereNowState.REJECTED: 4,
            NativeLandHereNowState.UNSUPPORTED: 3,
        }.get(state),
        land_mode_observed_at_s=(100.4 if state is NativeLandHereNowState.LANDING else None),
        landed_state_observed_at_s=(
            100.5
            if state in (NativeLandHereNowState.LANDING, NativeLandHereNowState.LANDED)
            else None
        ),
        landed_state=(
            MAV_LANDED_STATE_LANDING
            if state is NativeLandHereNowState.LANDING
            else MAV_LANDED_STATE_ON_GROUND
            if state is NativeLandHereNowState.LANDED
            else None
        ),
    )


def confirm(
    service: NativeLandHereNowService,
    gateway: FakeLandGateway,
    context: ConnectedMissionSnapshot,
    *,
    now_s: float = 100.3,
) -> None:
    service.confirm_native_land_here_now(
        gateway,
        context,
        auto_started(context),
        now_s=now_s,
        command_channel_idle=True,
        cancellation=NeverCancelled(),
    )


def test_two_step_confirm_and_cancel_send_only_after_deliberate_confirmation() -> None:
    context = connected()
    auto = auto_started(context)
    service = NativeLandHereNowService()
    gateway = FakeLandGateway(result())

    ready = service.synchronize_context(context, auto, now_s=100.2, command_channel_idle=True)
    assert ready.state is NativeLandHereNowState.AVAILABLE
    assert ready.request_available

    warning = service.begin_confirmation(context, auto, now_s=100.2, command_channel_idle=True)
    assert warning.state is NativeLandHereNowState.CONFIRMATION_REQUIRED
    assert warning.confirm_available and warning.cancel_available
    assert "abandons all remaining mission progress" in warning.detail
    assert gateway.calls == 0

    cancelled = service.cancel_confirmation()
    assert cancelled.state is NativeLandHereNowState.CONFIRMATION_CANCELLED
    assert gateway.calls == 0

    service.synchronize_context(context, auto, now_s=100.2, command_channel_idle=True)
    service.begin_confirmation(context, auto, now_s=100.2, command_channel_idle=True)
    confirm(service, gateway, context)
    assert service.snapshot.state is NativeLandHereNowState.LANDING
    assert gateway.calls == 1


def test_paused_auto_mission_is_eligible_but_confirmation_is_still_required() -> None:
    context = connected(mission_state=MAV_MISSION_STATE_PAUSED)
    service = NativeLandHereNowService()
    gateway = FakeLandGateway(result())
    service.synchronize_context(
        context, auto_started(context), now_s=100.2, command_channel_idle=True
    )
    confirm(service, gateway, context)
    assert service.snapshot.state is NativeLandHereNowState.BLOCKED_CONFIRMATION
    assert gateway.calls == 0
    service.begin_confirmation(
        context, auto_started(context), now_s=100.2, command_channel_idle=True
    )
    confirm(service, gateway, context)
    assert service.snapshot.state is NativeLandHereNowState.LANDING


def test_confirmation_expires_and_context_change_clears_it_without_send() -> None:
    context = connected()
    auto = auto_started(context)
    service = NativeLandHereNowService(confirmation_valid_for_s=2.0)
    gateway = FakeLandGateway(result())
    service.begin_confirmation(context, auto, now_s=100.2, command_channel_idle=True)
    confirm(service, gateway, context, now_s=102.3)
    assert service.snapshot.state is NativeLandHereNowState.BLOCKED_CONFIRMATION
    assert gateway.calls == 0

    service.begin_confirmation(context, auto, now_s=100.2, command_channel_idle=True)
    changed = replace(context, mission_revision=8)
    confirm(service, gateway, changed)
    assert service.snapshot.state in (
        NativeLandHereNowState.BLOCKED_AUTO_START,
        NativeLandHereNowState.MISSION_MISMATCH,
    )
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("mutate", "idle", "expected"),
    [
        (
            lambda value: replace(value, link_kind=TelemetryLinkKind.USB),
            True,
            NativeLandHereNowState.BLOCKED_WRONG_LINK,
        ),
        (
            lambda value: replace(value, link_connected=False),
            True,
            NativeLandHereNowState.LINK_LOST,
        ),
        (
            lambda value: replace(
                value, verification_state=ConnectedVerificationState.REVERIFY_REQUIRED
            ),
            True,
            NativeLandHereNowState.BLOCKED_MISSION,
        ),
        (
            lambda value: replace(
                value,
                selected_target=replace(value.selected_target, observed_at_s=90.0),
            ),
            True,
            NativeLandHereNowState.STALE_LINK,
        ),
        (
            lambda value: connected(mode_number=5),
            True,
            NativeLandHereNowState.UNEXPECTED_MODE,
        ),
        (
            lambda value: connected(mission_state=MAV_MISSION_STATE_COMPLETE),
            True,
            NativeLandHereNowState.MISSION_COMPLETED,
        ),
        (
            lambda value: connected(mode_number=9),
            True,
            NativeLandHereNowState.ALREADY_LANDING,
        ),
        (
            lambda value: connected(landed_state=MAV_LANDED_STATE_LANDING),
            True,
            NativeLandHereNowState.ALREADY_LANDING,
        ),
        (
            lambda value: connected(landed_state=MAV_LANDED_STATE_ON_GROUND),
            True,
            NativeLandHereNowState.ALREADY_LANDED,
        ),
        (
            lambda value: connected(armed=False),
            True,
            NativeLandHereNowState.DISARMED,
        ),
        (
            lambda value: connected(landed_state=None),
            True,
            NativeLandHereNowState.BLOCKED_NOT_AIRBORNE,
        ),
        (
            lambda value: connected(sequence=3),
            True,
            NativeLandHereNowState.ALREADY_LANDING,
        ),
        (lambda value: value, False, NativeLandHereNowState.BLOCKED_BUSY),
    ],
)
def test_context_races_block_before_confirmation_or_transmission(
    mutate: Callable[[ConnectedMissionSnapshot], ConnectedMissionSnapshot],
    idle: bool,
    expected: NativeLandHereNowState,
) -> None:
    original = connected()
    changed = mutate(original)
    gateway = FakeLandGateway(result())
    snapshot = NativeLandHereNowService().begin_confirmation(
        changed,
        auto_started(changed),
        now_s=100.2,
        command_channel_idle=idle,
    )
    assert snapshot.state is expected
    assert gateway.calls == 0


@pytest.mark.parametrize(
    "state",
    [
        NativeLandHereNowState.REJECTED,
        NativeLandHereNowState.UNSUPPORTED,
        NativeLandHereNowState.TIMED_OUT,
        NativeLandHereNowState.CANCELLED,
        NativeLandHereNowState.WRONG_TARGET,
        NativeLandHereNowState.WRONG_ACK,
        NativeLandHereNowState.STALE_LINK,
        NativeLandHereNowState.LINK_LOST,
        NativeLandHereNowState.ACKNOWLEDGED_NO_LANDING_TELEMETRY,
        NativeLandHereNowState.TELEMETRY_DISAGREEMENT,
        NativeLandHereNowState.DISARMED,
    ],
)
def test_protocol_outcomes_remain_distinct(state: NativeLandHereNowState) -> None:
    context = connected()
    service = NativeLandHereNowService()
    gateway = FakeLandGateway(result(state))
    service.begin_confirmation(
        context, auto_started(context), now_s=100.2, command_channel_idle=True
    )
    confirm(service, gateway, context)
    assert service.snapshot.state is state
    assert not service.snapshot.request_available


def test_repeated_activation_while_pending_sends_once() -> None:
    context = connected()
    auto = auto_started(context)
    service = NativeLandHereNowService()
    gateway = FakeLandGateway(result(NativeLandHereNowState.REJECTED))
    service.begin_confirmation(context, auto, now_s=100.2, command_channel_idle=True)

    def repeat() -> None:
        service.confirm_native_land_here_now(
            gateway,
            context,
            auto,
            now_s=100.3,
            command_channel_idle=True,
            cancellation=NeverCancelled(),
        )

    gateway.on_request = repeat
    confirm(service, gateway, context)
    assert gateway.calls == 1
    assert service.snapshot.repeated_request_ignored
