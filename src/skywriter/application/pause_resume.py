"""Application-owned native AUTO mission Pause/Resume authorization and state."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from skywriter.application.auto_start import NativeAutoStartSnapshot, NativeAutoStartState
from skywriter.application.connected import (
    CancellationView,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
)
from skywriter.application.telemetry import (
    NativeStatusText,
    TelemetryFreshness,
    TelemetryLinkKind,
)
from skywriter.domain.compiled import MissionCommand

ARDUCOPTER_AUTO_MODE = 3
ARDUCOPTER_LAND_MODE = 9
MAV_MISSION_STATE_ACTIVE = 3
MAV_MISSION_STATE_PAUSED = 4
MAV_MISSION_STATE_COMPLETE = 5
MAV_LANDED_STATE_ON_GROUND = 1
MAV_LANDED_STATE_LANDING = 4


class NativePauseResumeAction(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"


class NativePauseResumeState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSE_PENDING = "pause_pending"
    PAUSED = "paused"
    RESUME_PENDING = "resume_pending"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    WRONG_TARGET = "wrong_target"
    WRONG_ACK = "wrong_ack"
    STALE_LINK = "stale_link"
    LINK_LOST = "link_lost"
    ACKNOWLEDGED_NO_PAUSED_TELEMETRY = "acknowledged_no_paused_telemetry"
    ACKNOWLEDGED_NO_RUNNING_TELEMETRY = "acknowledged_no_running_telemetry"
    UNEXPECTED_MODE = "unexpected_mode"
    MISSION_COMPLETED = "mission_completed"
    LANDING = "landing"
    DISARMED = "disarmed"
    MISSION_MISMATCH = "mission_mismatch"
    TELEMETRY_DISAGREEMENT = "telemetry_disagreement"
    BLOCKED_WRONG_LINK = "blocked_wrong_link"
    BLOCKED_MISSION = "blocked_mission"
    BLOCKED_AUTO_START = "blocked_auto_start"
    BLOCKED_IDENTITY = "blocked_identity"
    BLOCKED_BUSY = "blocked_busy"
    BLOCKED_NOT_RUNNING = "blocked_not_running"
    BLOCKED_NOT_PAUSED = "blocked_not_paused"


_PROTOCOL_RESULTS = frozenset(
    {
        NativePauseResumeState.RUNNING,
        NativePauseResumeState.PAUSED,
        NativePauseResumeState.REJECTED,
        NativePauseResumeState.UNSUPPORTED,
        NativePauseResumeState.TIMED_OUT,
        NativePauseResumeState.CANCELLED,
        NativePauseResumeState.WRONG_TARGET,
        NativePauseResumeState.WRONG_ACK,
        NativePauseResumeState.STALE_LINK,
        NativePauseResumeState.LINK_LOST,
        NativePauseResumeState.ACKNOWLEDGED_NO_PAUSED_TELEMETRY,
        NativePauseResumeState.ACKNOWLEDGED_NO_RUNNING_TELEMETRY,
        NativePauseResumeState.UNEXPECTED_MODE,
        NativePauseResumeState.MISSION_COMPLETED,
        NativePauseResumeState.LANDING,
        NativePauseResumeState.DISARMED,
        NativePauseResumeState.MISSION_MISMATCH,
        NativePauseResumeState.TELEMETRY_DISAGREEMENT,
    }
)


@dataclass(frozen=True, slots=True)
class NativePauseResumeCommandResult:
    action: NativePauseResumeAction
    state: NativePauseResumeState
    detail: str
    requested_at_s: float
    completed_at_s: float
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    state_observed_at_s: float | None = None
    progress_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.state not in _PROTOCOL_RESULTS:
            raise ValueError("Pause/Resume result must contain a terminal protocol state")
        if not self.detail:
            raise ValueError("Pause/Resume detail must not be empty")
        if not math.isfinite(self.requested_at_s) or not math.isfinite(self.completed_at_s):
            raise ValueError("Pause/Resume result times must be finite")
        if self.completed_at_s < self.requested_at_s:
            raise ValueError("completed_at_s must not precede requested_at_s")
        if self.state_observed_at_s is not None and (
            not math.isfinite(self.state_observed_at_s)
            or self.state_observed_at_s < self.requested_at_s
        ):
            raise ValueError("state_observed_at_s must be finite and follow the request")
        if self.progress_sequence is not None and self.progress_sequence < 1:
            raise ValueError("progress_sequence must identify an executable mission item")
        expected = {
            NativePauseResumeAction.PAUSE: NativePauseResumeState.PAUSED,
            NativePauseResumeAction.RESUME: NativePauseResumeState.RUNNING,
        }[self.action]
        if self.state is expected and (
            self.state_observed_at_s is None or self.progress_sequence is None
        ):
            raise ValueError(f"{expected.value} requires later pinned mission-state telemetry")
        object.__setattr__(self, "native_messages", tuple(self.native_messages))


class NativePauseResumeGateway(Protocol):
    """Closed gateway with only the two pinned command-193 actions."""

    def request_native_pause(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativePauseResumeCommandResult: ...

    def request_native_resume(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativePauseResumeCommandResult: ...


@dataclass(frozen=True, slots=True)
class NativePauseResumeAuthorization:
    vehicle_identity: str
    system_id: int
    component_id: int
    mission_revision: int
    expected_mission_digest: str
    auto_start_revision: int
    first_executable_sequence: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class NativePauseResumeSnapshot:
    revision: int = 0
    state: NativePauseResumeState = NativePauseResumeState.IDLE
    detail: str = "Pause is blocked until current native mission-running telemetry is observed."
    authorization: NativePauseResumeAuthorization | None = None
    pause_available: bool = False
    resume_available: bool = False
    last_action: NativePauseResumeAction | None = None
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    repeated_request_ignored: bool = False
    requested_at_s: float | None = None
    completed_at_s: float | None = None
    state_observed_at_s: float | None = None
    progress_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.pause_available and self.resume_available:
            raise ValueError("Pause and Resume cannot be available together")
        if self.pause_available and self.state is not NativePauseResumeState.RUNNING:
            raise ValueError("Pause is available only in telemetry-confirmed Running")
        if self.resume_available and self.state is not NativePauseResumeState.PAUSED:
            raise ValueError("Resume is available only in telemetry-confirmed Paused")
        if self.state in (
            NativePauseResumeState.PAUSE_PENDING,
            NativePauseResumeState.RESUME_PENDING,
        ) and (self.pause_available or self.resume_available):
            raise ValueError("pending command state cannot enable another control")
        if self.state in (NativePauseResumeState.RUNNING, NativePauseResumeState.PAUSED) and (
            self.state_observed_at_s is None or self.progress_sequence is None
        ):
            raise ValueError("Running/Paused presentation requires pinned mission-state telemetry")
        object.__setattr__(self, "native_messages", tuple(self.native_messages))


@dataclass(frozen=True, slots=True)
class _ObservedControlContext:
    target: ConnectedTarget
    authorization: NativePauseResumeAuthorization
    state: NativePauseResumeState
    detail: str
    observed_at_s: float
    progress_sequence: int


class NativePauseResumeService:
    """Fail-closed Task 103 use case; run gateway calls from a worker."""

    def __init__(self, *, target_valid_for_s: float = 3.0) -> None:
        if not math.isfinite(target_valid_for_s) or target_valid_for_s <= 0:
            raise ValueError("target_valid_for_s must be a positive finite number")
        self._target_valid_for_s = target_valid_for_s
        self._snapshot = NativePauseResumeSnapshot()

    @property
    def snapshot(self) -> NativePauseResumeSnapshot:
        return self._snapshot

    def synchronize_context(
        self,
        connected: ConnectedMissionSnapshot,
        auto_start: NativeAutoStartSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
    ) -> NativePauseResumeSnapshot:
        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if not isinstance(command_channel_idle, bool):
            raise TypeError("command_channel_idle must be a boolean")
        if self._snapshot.state in (
            NativePauseResumeState.PAUSE_PENDING,
            NativePauseResumeState.RESUME_PENDING,
        ):
            return self._snapshot
        context = _control_context(
            connected,
            auto_start,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(context, tuple):
            state, detail = context
            self._snapshot = NativePauseResumeSnapshot(
                revision=self._snapshot.revision + 1,
                state=state,
                detail=detail,
            )
            return self._snapshot
        if self._snapshot.authorization not in (None, context.authorization):
            self._snapshot = NativePauseResumeSnapshot(
                revision=self._snapshot.revision + 1,
                state=NativePauseResumeState.MISSION_MISMATCH,
                detail="Mission, target, or AUTO-start evidence changed; controls are blocked.",
            )
            return self._snapshot
        self._snapshot = NativePauseResumeSnapshot(
            revision=self._snapshot.revision + 1,
            state=context.state,
            detail=context.detail,
            authorization=context.authorization,
            pause_available=context.state is NativePauseResumeState.RUNNING,
            resume_available=context.state is NativePauseResumeState.PAUSED,
            state_observed_at_s=context.observed_at_s,
            progress_sequence=context.progress_sequence,
        )
        return self._snapshot

    def request_native_pause(
        self,
        gateway: NativePauseResumeGateway,
        connected: ConnectedMissionSnapshot,
        auto_start: NativeAutoStartSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
        cancellation: CancellationView,
    ) -> NativePauseResumeSnapshot:
        return self._request(
            NativePauseResumeAction.PAUSE,
            gateway,
            connected,
            auto_start,
            now_s=now_s,
            command_channel_idle=command_channel_idle,
            cancellation=cancellation,
        )

    def request_native_resume(
        self,
        gateway: NativePauseResumeGateway,
        connected: ConnectedMissionSnapshot,
        auto_start: NativeAutoStartSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
        cancellation: CancellationView,
    ) -> NativePauseResumeSnapshot:
        return self._request(
            NativePauseResumeAction.RESUME,
            gateway,
            connected,
            auto_start,
            now_s=now_s,
            command_channel_idle=command_channel_idle,
            cancellation=cancellation,
        )

    def _request(
        self,
        action: NativePauseResumeAction,
        gateway: NativePauseResumeGateway,
        connected: ConnectedMissionSnapshot,
        auto_start: NativeAutoStartSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
        cancellation: CancellationView,
    ) -> NativePauseResumeSnapshot:
        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if self._snapshot.state in (
            NativePauseResumeState.PAUSE_PENDING,
            NativePauseResumeState.RESUME_PENDING,
        ):
            self._snapshot = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                repeated_request_ignored=True,
                detail="A Pause/Resume transaction is pending; repeated activation was ignored.",
            )
            return self._snapshot
        if action is NativePauseResumeAction.RESUME and (
            self._snapshot.state is not NativePauseResumeState.PAUSED
            or self._snapshot.authorization is None
            or self._snapshot.state_observed_at_s is None
        ):
            self._snapshot = NativePauseResumeSnapshot(
                revision=self._snapshot.revision + 1,
                state=NativePauseResumeState.BLOCKED_NOT_PAUSED,
                detail="Resume requires a positively observed pinned Paused state.",
            )
            return self._snapshot

        context = _control_context(
            connected,
            auto_start,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(context, tuple):
            state, detail = context
            self._snapshot = NativePauseResumeSnapshot(
                revision=self._snapshot.revision + 1,
                state=state,
                detail=detail,
            )
            return self._snapshot
        required = {
            NativePauseResumeAction.PAUSE: NativePauseResumeState.RUNNING,
            NativePauseResumeAction.RESUME: NativePauseResumeState.PAUSED,
        }[action]
        if context.state is not required:
            state = (
                NativePauseResumeState.BLOCKED_NOT_RUNNING
                if action is NativePauseResumeAction.PAUSE
                else NativePauseResumeState.BLOCKED_NOT_PAUSED
            )
            self._snapshot = NativePauseResumeSnapshot(
                revision=self._snapshot.revision + 1,
                state=state,
                detail=f"{action.value.title()} requires current {required.value} telemetry.",
            )
            return self._snapshot
        if self._snapshot.authorization not in (None, context.authorization):
            self._snapshot = NativePauseResumeSnapshot(
                revision=self._snapshot.revision + 1,
                state=NativePauseResumeState.MISSION_MISMATCH,
                detail="Mission, target, or AUTO-start evidence changed before transmission.",
            )
            return self._snapshot

        pending = {
            NativePauseResumeAction.PAUSE: NativePauseResumeState.PAUSE_PENDING,
            NativePauseResumeAction.RESUME: NativePauseResumeState.RESUME_PENDING,
        }[action]
        self._snapshot = NativePauseResumeSnapshot(
            revision=self._snapshot.revision + 1,
            state=pending,
            detail=(
                f"Waiting for the exact {action.value} ACK and later pinned mission-state "
                "telemetry."
            ),
            authorization=context.authorization,
            last_action=action,
            requested_at_s=now_s,
            state_observed_at_s=context.observed_at_s,
            progress_sequence=context.progress_sequence,
        )
        method = (
            gateway.request_native_pause
            if action is NativePauseResumeAction.PAUSE
            else gateway.request_native_resume
        )
        result = method(
            context.target,
            expected_item_count=context.authorization.last_sequence + 1,
            target_valid_for_s=self._target_valid_for_s,
            cancellation=cancellation,
        )
        self._snapshot = NativePauseResumeSnapshot(
            revision=self._snapshot.revision + 1,
            state=result.state,
            detail=result.detail,
            authorization=context.authorization,
            pause_available=result.state is NativePauseResumeState.RUNNING,
            resume_available=result.state is NativePauseResumeState.PAUSED,
            last_action=result.action,
            ack_result=result.ack_result,
            native_messages=result.native_messages,
            repeated_request_ignored=self._snapshot.repeated_request_ignored,
            requested_at_s=result.requested_at_s,
            completed_at_s=result.completed_at_s,
            state_observed_at_s=result.state_observed_at_s,
            progress_sequence=result.progress_sequence,
        )
        return self._snapshot


def _control_context(
    connected: ConnectedMissionSnapshot,
    auto_start: NativeAutoStartSnapshot,
    *,
    now_s: float,
    target_valid_for_s: float,
    command_channel_idle: bool,
) -> _ObservedControlContext | tuple[NativePauseResumeState, str]:
    if not connected.link_connected:
        return NativePauseResumeState.LINK_LOST, (
            "Command link was lost; onboard behavior remains native."
        )
    if connected.link_kind is not TelemetryLinkKind.SIK:
        return NativePauseResumeState.BLOCKED_WRONG_LINK, "Pause/Resume requires SiK."
    if (
        connected.verification_state is not ConnectedVerificationState.SIK_VERIFIED
        or connected.expected_package is None
        or connected.transfer_evidence is None
        or connected.mission_revision is None
    ):
        return NativePauseResumeState.BLOCKED_MISSION, (
            "Exact current mission verification is required."
        )
    target = connected.selected_target
    telemetry = connected.telemetry
    package = connected.expected_package
    if target is None or target.vehicle != package.vehicle:
        return NativePauseResumeState.BLOCKED_IDENTITY, "Selected target identity is unresolved."
    if target.link_kind is not TelemetryLinkKind.SIK:
        return NativePauseResumeState.BLOCKED_WRONG_LINK, "Selected target is not on SiK."
    if telemetry is None or (
        telemetry.vehicle_identity != target.vehicle.value
        or telemetry.target_system != target.system_id
        or telemetry.target_component != target.component_id
        or telemetry.link_kind is not TelemetryLinkKind.SIK
    ):
        return NativePauseResumeState.BLOCKED_IDENTITY, (
            "Telemetry is not from the selected target."
        )
    if not target.is_fresh(now_s, target_valid_for_s) or not telemetry.command_gate_fresh(now_s):
        return NativePauseResumeState.STALE_LINK, "Selected-target telemetry is stale."
    heartbeat = telemetry.heartbeat.value
    if heartbeat is None:
        return NativePauseResumeState.STALE_LINK, "Selected-target heartbeat is unavailable."
    if target.armed != heartbeat.armed:
        return NativePauseResumeState.TELEMETRY_DISAGREEMENT, (
            "Selected-target heartbeat sources disagree about armed state."
        )
    progress = telemetry.mission.value
    if telemetry.mission.freshness(now_s) is not TelemetryFreshness.FRESH or progress is None:
        return NativePauseResumeState.BLOCKED_NOT_RUNNING, (
            "Current native mission-state telemetry is required."
        )
    if progress.mission_state == MAV_MISSION_STATE_COMPLETE:
        return NativePauseResumeState.MISSION_COMPLETED, "Native mission reports Complete."
    extended = telemetry.extended_state
    if extended.freshness(now_s) is TelemetryFreshness.FRESH and extended.value is not None:
        if extended.value.landed_state == MAV_LANDED_STATE_LANDING:
            return NativePauseResumeState.LANDING, "Vehicle telemetry reports Landing."
        if extended.value.landed_state == MAV_LANDED_STATE_ON_GROUND:
            return NativePauseResumeState.DISARMED, "Vehicle telemetry reports On Ground."
    if not target.armed or not heartbeat.armed:
        return NativePauseResumeState.DISARMED, "Vehicle disarmed; flight controls are blocked."
    if heartbeat.mode_number == ARDUCOPTER_LAND_MODE:
        return NativePauseResumeState.LANDING, "Vehicle entered native Land mode."
    if heartbeat.mode_number != ARDUCOPTER_AUTO_MODE:
        return NativePauseResumeState.UNEXPECTED_MODE, "Vehicle is no longer in AUTO."
    if auto_start.state is not NativeAutoStartState.RUNNING or auto_start.authorization is None:
        return NativePauseResumeState.BLOCKED_AUTO_START, (
            "Telemetry-confirmed Task 102 Running evidence is required."
        )
    start = auto_start.authorization
    if (
        start.vehicle_identity != target.vehicle.value
        or start.system_id != target.system_id
        or start.component_id != target.component_id
        or start.mission_revision != connected.mission_revision
        or start.expected_mission_digest != connected.transfer_evidence.expected_digest
    ):
        return NativePauseResumeState.BLOCKED_AUTO_START, (
            "AUTO-start evidence does not match this mission/target."
        )
    if not command_channel_idle:
        return NativePauseResumeState.BLOCKED_BUSY, (
            "Another command transaction owns the channel."
        )
    sequence = (
        progress.current_sequence
        if progress.current_sequence is not None
        else progress.last_reached_sequence
    )
    if (
        sequence is None
        or not start.first_executable_sequence <= sequence <= start.last_sequence
        or (progress.total_items is not None and progress.total_items != len(package.items))
    ):
        return NativePauseResumeState.MISSION_MISMATCH, (
            "Mission progress is outside the exact verified mission bounds."
        )
    if sequence == start.last_sequence and package.items[sequence].command == int(
        MissionCommand.NAV_LAND
    ):
        return NativePauseResumeState.LANDING, "Verified mission is executing native Land."
    mission_state = progress.mission_state
    if mission_state is None:
        return NativePauseResumeState.BLOCKED_NOT_RUNNING, ("Native mission state is unavailable.")
    observed = {
        MAV_MISSION_STATE_ACTIVE: NativePauseResumeState.RUNNING,
        MAV_MISSION_STATE_PAUSED: NativePauseResumeState.PAUSED,
    }.get(mission_state)
    if observed is None:
        return NativePauseResumeState.BLOCKED_NOT_RUNNING, (
            "Native mission telemetry is neither Active nor Paused."
        )
    assert telemetry.mission.observed_at_s is not None
    authorization = NativePauseResumeAuthorization(
        vehicle_identity=target.vehicle.value,
        system_id=target.system_id,
        component_id=target.component_id,
        mission_revision=connected.mission_revision,
        expected_mission_digest=connected.transfer_evidence.expected_digest,
        auto_start_revision=auto_start.revision,
        first_executable_sequence=start.first_executable_sequence,
        last_sequence=start.last_sequence,
    )
    detail = (
        "Current armed AUTO mission telemetry permits native Pause."
        if observed is NativePauseResumeState.RUNNING
        else "Pinned mission-state telemetry positively confirms Paused; Resume is available."
    )
    return _ObservedControlContext(
        target,
        authorization,
        observed,
        detail,
        telemetry.mission.observed_at_s,
        sequence,
    )
