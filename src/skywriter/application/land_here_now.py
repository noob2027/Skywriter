"""Application-owned confirmation and state for native Land Here Now."""

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
from skywriter.application.pause_resume import (
    ARDUCOPTER_AUTO_MODE,
    ARDUCOPTER_LAND_MODE,
    MAV_LANDED_STATE_LANDING,
    MAV_LANDED_STATE_ON_GROUND,
    MAV_MISSION_STATE_ACTIVE,
    MAV_MISSION_STATE_COMPLETE,
    MAV_MISSION_STATE_PAUSED,
)
from skywriter.application.telemetry import (
    NativeStatusText,
    TelemetryFreshness,
    TelemetryLinkKind,
)
from skywriter.domain.compiled import MissionCommand

MAV_LANDED_STATE_IN_AIR = 2


class NativeLandHereNowState(StrEnum):
    IDLE = "idle"
    AVAILABLE = "available"
    CONFIRMATION_REQUIRED = "confirmation_required"
    PENDING = "pending"
    LANDING = "landing"
    LANDED = "landed"
    CONFIRMATION_CANCELLED = "confirmation_cancelled"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    WRONG_TARGET = "wrong_target"
    WRONG_ACK = "wrong_ack"
    STALE_LINK = "stale_link"
    LINK_LOST = "link_lost"
    ACKNOWLEDGED_NO_LANDING_TELEMETRY = "acknowledged_no_landing_telemetry"
    UNEXPECTED_MODE = "unexpected_mode"
    MISSION_COMPLETED = "mission_completed"
    DISARMED = "disarmed"
    MISSION_MISMATCH = "mission_mismatch"
    TELEMETRY_DISAGREEMENT = "telemetry_disagreement"
    ALREADY_LANDING = "already_landing"
    ALREADY_LANDED = "already_landed"
    BLOCKED_WRONG_LINK = "blocked_wrong_link"
    BLOCKED_MISSION = "blocked_mission"
    BLOCKED_AUTO_START = "blocked_auto_start"
    BLOCKED_IDENTITY = "blocked_identity"
    BLOCKED_BUSY = "blocked_busy"
    BLOCKED_NOT_AIRBORNE = "blocked_not_airborne"
    BLOCKED_CONFIRMATION = "blocked_confirmation"


_PROTOCOL_RESULTS = frozenset(
    {
        NativeLandHereNowState.LANDING,
        NativeLandHereNowState.LANDED,
        NativeLandHereNowState.REJECTED,
        NativeLandHereNowState.UNSUPPORTED,
        NativeLandHereNowState.TIMED_OUT,
        NativeLandHereNowState.CANCELLED,
        NativeLandHereNowState.WRONG_TARGET,
        NativeLandHereNowState.WRONG_ACK,
        NativeLandHereNowState.STALE_LINK,
        NativeLandHereNowState.LINK_LOST,
        NativeLandHereNowState.ACKNOWLEDGED_NO_LANDING_TELEMETRY,
        NativeLandHereNowState.UNEXPECTED_MODE,
        NativeLandHereNowState.DISARMED,
        NativeLandHereNowState.TELEMETRY_DISAGREEMENT,
        NativeLandHereNowState.ALREADY_LANDING,
        NativeLandHereNowState.ALREADY_LANDED,
    }
)


@dataclass(frozen=True, slots=True)
class NativeLandHereNowCommandResult:
    state: NativeLandHereNowState
    detail: str
    requested_at_s: float
    completed_at_s: float
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    land_mode_observed_at_s: float | None = None
    landed_state_observed_at_s: float | None = None
    landed_state: int | None = None

    def __post_init__(self) -> None:
        if self.state not in _PROTOCOL_RESULTS:
            raise ValueError("Land Here Now result must contain a terminal protocol state")
        if not self.detail:
            raise ValueError("Land Here Now detail must not be empty")
        if not math.isfinite(self.requested_at_s) or not math.isfinite(self.completed_at_s):
            raise ValueError("Land Here Now result times must be finite")
        if self.completed_at_s < self.requested_at_s:
            raise ValueError("completed_at_s must not precede requested_at_s")
        for name in ("land_mode_observed_at_s", "landed_state_observed_at_s"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < self.requested_at_s):
                raise ValueError(f"{name} must be finite and follow the request")
        if self.state is NativeLandHereNowState.LANDING and (
            self.land_mode_observed_at_s is None
            or self.landed_state_observed_at_s is None
            or self.landed_state != MAV_LANDED_STATE_LANDING
        ):
            raise ValueError("Landing requires later Land mode and Landing state telemetry")
        if self.state is NativeLandHereNowState.LANDED and (
            self.landed_state_observed_at_s is None
            or self.landed_state != MAV_LANDED_STATE_ON_GROUND
        ):
            raise ValueError("Landed requires later On Ground telemetry")
        object.__setattr__(self, "native_messages", tuple(self.native_messages))


class NativeLandHereNowGateway(Protocol):
    """Closed gateway for one fixed native Land action."""

    def request_native_land_here_now(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativeLandHereNowCommandResult: ...


@dataclass(frozen=True, slots=True)
class NativeLandHereNowAuthorization:
    vehicle_identity: str
    system_id: int
    component_id: int
    mission_revision: int
    expected_mission_digest: str
    auto_start_revision: int
    first_executable_sequence: int
    last_sequence: int
    progress_sequence: int
    mission_state: int


@dataclass(frozen=True, slots=True)
class NativeLandHereNowSnapshot:
    revision: int = 0
    state: NativeLandHereNowState = NativeLandHereNowState.IDLE
    detail: str = "Land Here Now is blocked until current airborne mission telemetry is observed."
    authorization: NativeLandHereNowAuthorization | None = None
    request_available: bool = False
    confirm_available: bool = False
    cancel_available: bool = False
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    repeated_request_ignored: bool = False
    confirmation_requested_at_s: float | None = None
    requested_at_s: float | None = None
    completed_at_s: float | None = None
    land_mode_observed_at_s: float | None = None
    landed_state_observed_at_s: float | None = None
    landed_state: int | None = None

    def __post_init__(self) -> None:
        confirming = self.state is NativeLandHereNowState.CONFIRMATION_REQUIRED
        if self.confirm_available != confirming or self.cancel_available != confirming:
            raise ValueError("confirmation controls are available only during confirmation")
        if confirming and (self.authorization is None or self.confirmation_requested_at_s is None):
            raise ValueError("confirmation requires exact authorization and a timestamp")
        if self.state is NativeLandHereNowState.PENDING and (
            self.request_available or self.confirm_available or self.cancel_available
        ):
            raise ValueError("pending Land Here Now cannot enable controls")
        if self.state is NativeLandHereNowState.LANDING and (
            self.land_mode_observed_at_s is None
            or self.landed_state_observed_at_s is None
            or self.landed_state != MAV_LANDED_STATE_LANDING
        ):
            raise ValueError("Landing presentation requires Land mode and Landing telemetry")
        object.__setattr__(self, "native_messages", tuple(self.native_messages))


@dataclass(frozen=True, slots=True)
class _LandContext:
    target: ConnectedTarget
    authorization: NativeLandHereNowAuthorization


class NativeLandHereNowService:
    """Fail-closed two-step Task 104 use case; run only confirmation off-thread."""

    def __init__(
        self,
        *,
        target_valid_for_s: float = 3.0,
        confirmation_valid_for_s: float = 10.0,
    ) -> None:
        for name, value in (
            ("target_valid_for_s", target_valid_for_s),
            ("confirmation_valid_for_s", confirmation_valid_for_s),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        self._target_valid_for_s = target_valid_for_s
        self._confirmation_valid_for_s = confirmation_valid_for_s
        self._snapshot = NativeLandHereNowSnapshot()

    @property
    def snapshot(self) -> NativeLandHereNowSnapshot:
        return self._snapshot

    def synchronize_context(
        self,
        connected: ConnectedMissionSnapshot,
        auto_start: NativeAutoStartSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
    ) -> NativeLandHereNowSnapshot:
        _validate_now_and_idle(now_s, command_channel_idle)
        if self._snapshot.state is NativeLandHereNowState.PENDING:
            return self._snapshot
        context = _land_context(
            connected,
            auto_start,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(context, tuple):
            return self._replace_blocked(*context)
        if self._snapshot.state is NativeLandHereNowState.CONFIRMATION_REQUIRED:
            if self._snapshot.authorization != context.authorization:
                return self._replace_blocked(
                    NativeLandHereNowState.MISSION_MISMATCH,
                    "Mission, target, or flight evidence changed; confirmation was cleared.",
                )
            assert self._snapshot.confirmation_requested_at_s is not None
            if now_s - self._snapshot.confirmation_requested_at_s <= self._confirmation_valid_for_s:
                return self._snapshot
            self._snapshot = NativeLandHereNowSnapshot(
                revision=self._snapshot.revision + 1,
                state=NativeLandHereNowState.AVAILABLE,
                detail="Confirmation expired; review the landing warning again.",
                authorization=context.authorization,
                request_available=True,
            )
            return self._snapshot
        self._snapshot = NativeLandHereNowSnapshot(
            revision=self._snapshot.revision + 1,
            state=NativeLandHereNowState.AVAILABLE,
            detail="Airborne verified mission permits deliberate native Land confirmation.",
            authorization=context.authorization,
            request_available=True,
        )
        return self._snapshot

    def begin_confirmation(
        self,
        connected: ConnectedMissionSnapshot,
        auto_start: NativeAutoStartSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
    ) -> NativeLandHereNowSnapshot:
        _validate_now_and_idle(now_s, command_channel_idle)
        if self._snapshot.state is NativeLandHereNowState.PENDING:
            return self._record_duplicate()
        context = _land_context(
            connected,
            auto_start,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(context, tuple):
            return self._replace_blocked(*context)
        self._snapshot = NativeLandHereNowSnapshot(
            revision=self._snapshot.revision + 1,
            state=NativeLandHereNowState.CONFIRMATION_REQUIRED,
            detail=(
                "This abandons all remaining mission progress and lands at the aircraft's "
                "current location. Confirm only if that is deliberate."
            ),
            authorization=context.authorization,
            confirm_available=True,
            cancel_available=True,
            confirmation_requested_at_s=now_s,
        )
        return self._snapshot

    def cancel_confirmation(self) -> NativeLandHereNowSnapshot:
        if self._snapshot.state is NativeLandHereNowState.PENDING:
            return self._record_duplicate()
        if self._snapshot.state is not NativeLandHereNowState.CONFIRMATION_REQUIRED:
            return self._replace_blocked(
                NativeLandHereNowState.BLOCKED_CONFIRMATION,
                "There is no active Land Here Now confirmation to cancel.",
            )
        self._snapshot = NativeLandHereNowSnapshot(
            revision=self._snapshot.revision + 1,
            state=NativeLandHereNowState.CONFIRMATION_CANCELLED,
            detail="Land Here Now confirmation cancelled; no vehicle command was sent.",
        )
        return self._snapshot

    def confirm_native_land_here_now(
        self,
        gateway: NativeLandHereNowGateway,
        connected: ConnectedMissionSnapshot,
        auto_start: NativeAutoStartSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
        cancellation: CancellationView,
    ) -> NativeLandHereNowSnapshot:
        _validate_now_and_idle(now_s, command_channel_idle)
        if self._snapshot.state is NativeLandHereNowState.PENDING:
            return self._record_duplicate()
        if (
            self._snapshot.state is not NativeLandHereNowState.CONFIRMATION_REQUIRED
            or self._snapshot.authorization is None
            or self._snapshot.confirmation_requested_at_s is None
        ):
            return self._replace_blocked(
                NativeLandHereNowState.BLOCKED_CONFIRMATION,
                "A fresh deliberate confirmation is required before native Land.",
            )
        if now_s - self._snapshot.confirmation_requested_at_s > self._confirmation_valid_for_s:
            return self._replace_blocked(
                NativeLandHereNowState.BLOCKED_CONFIRMATION,
                "Land Here Now confirmation expired; no command was sent.",
            )
        context = _land_context(
            connected,
            auto_start,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(context, tuple):
            return self._replace_blocked(*context)
        if context.authorization != self._snapshot.authorization:
            return self._replace_blocked(
                NativeLandHereNowState.MISSION_MISMATCH,
                "Mission, target, or flight evidence changed after confirmation.",
            )
        confirmation_requested_at_s = self._snapshot.confirmation_requested_at_s
        self._snapshot = NativeLandHereNowSnapshot(
            revision=self._snapshot.revision + 1,
            state=NativeLandHereNowState.PENDING,
            detail="Waiting for the exact native Land ACK and later landing telemetry.",
            authorization=context.authorization,
            confirmation_requested_at_s=confirmation_requested_at_s,
            requested_at_s=now_s,
        )
        result = gateway.request_native_land_here_now(
            context.target,
            target_valid_for_s=self._target_valid_for_s,
            cancellation=cancellation,
        )
        self._snapshot = NativeLandHereNowSnapshot(
            revision=self._snapshot.revision + 1,
            state=result.state,
            detail=result.detail,
            authorization=context.authorization,
            ack_result=result.ack_result,
            native_messages=result.native_messages,
            repeated_request_ignored=self._snapshot.repeated_request_ignored,
            confirmation_requested_at_s=confirmation_requested_at_s,
            requested_at_s=result.requested_at_s,
            completed_at_s=result.completed_at_s,
            land_mode_observed_at_s=result.land_mode_observed_at_s,
            landed_state_observed_at_s=result.landed_state_observed_at_s,
            landed_state=result.landed_state,
        )
        return self._snapshot

    def _replace_blocked(
        self, state: NativeLandHereNowState, detail: str
    ) -> NativeLandHereNowSnapshot:
        self._snapshot = NativeLandHereNowSnapshot(
            revision=self._snapshot.revision + 1,
            state=state,
            detail=detail,
        )
        return self._snapshot

    def _record_duplicate(self) -> NativeLandHereNowSnapshot:
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            repeated_request_ignored=True,
            detail="Native Land transaction is pending; repeated activation was ignored.",
        )
        return self._snapshot


def _validate_now_and_idle(now_s: float, command_channel_idle: bool) -> None:
    if not math.isfinite(now_s):
        raise ValueError("now_s must be finite")
    if not isinstance(command_channel_idle, bool):
        raise TypeError("command_channel_idle must be a boolean")


def _land_context(
    connected: ConnectedMissionSnapshot,
    auto_start: NativeAutoStartSnapshot,
    *,
    now_s: float,
    target_valid_for_s: float,
    command_channel_idle: bool,
) -> _LandContext | tuple[NativeLandHereNowState, str]:
    if not connected.link_connected:
        return NativeLandHereNowState.LINK_LOST, (
            "Command link was lost; onboard behavior remains native."
        )
    if connected.link_kind is not TelemetryLinkKind.SIK:
        return NativeLandHereNowState.BLOCKED_WRONG_LINK, "Land Here Now requires SiK."
    if (
        connected.verification_state is not ConnectedVerificationState.SIK_VERIFIED
        or connected.expected_package is None
        or connected.transfer_evidence is None
        or connected.mission_revision is None
    ):
        return NativeLandHereNowState.BLOCKED_MISSION, (
            "Exact current mission verification is required."
        )
    target = connected.selected_target
    telemetry = connected.telemetry
    package = connected.expected_package
    if target is None or target.vehicle != package.vehicle:
        return NativeLandHereNowState.BLOCKED_IDENTITY, "Selected target identity is unresolved."
    if target.link_kind is not TelemetryLinkKind.SIK:
        return NativeLandHereNowState.BLOCKED_WRONG_LINK, "Selected target is not on SiK."
    if telemetry is None or (
        telemetry.vehicle_identity != target.vehicle.value
        or telemetry.target_system != target.system_id
        or telemetry.target_component != target.component_id
        or telemetry.link_kind is not TelemetryLinkKind.SIK
    ):
        return NativeLandHereNowState.BLOCKED_IDENTITY, (
            "Telemetry is not from the selected target."
        )
    if not target.is_fresh(now_s, target_valid_for_s) or not telemetry.command_gate_fresh(now_s):
        return NativeLandHereNowState.STALE_LINK, "Selected-target telemetry is stale."
    heartbeat = telemetry.heartbeat.value
    if heartbeat is None:
        return NativeLandHereNowState.STALE_LINK, "Selected-target heartbeat is unavailable."
    if target.armed != heartbeat.armed:
        return NativeLandHereNowState.TELEMETRY_DISAGREEMENT, (
            "Selected-target heartbeat sources disagree about armed state."
        )
    extended = telemetry.extended_state
    if extended.freshness(now_s) is not TelemetryFreshness.FRESH or extended.value is None:
        return NativeLandHereNowState.BLOCKED_NOT_AIRBORNE, (
            "Fresh native airborne state is required."
        )
    if extended.value.landed_state == MAV_LANDED_STATE_ON_GROUND:
        return NativeLandHereNowState.ALREADY_LANDED, "Vehicle already reports On Ground."
    if extended.value.landed_state == MAV_LANDED_STATE_LANDING:
        return NativeLandHereNowState.ALREADY_LANDING, "Vehicle already reports Landing."
    if extended.value.landed_state != MAV_LANDED_STATE_IN_AIR:
        return NativeLandHereNowState.BLOCKED_NOT_AIRBORNE, (
            "Vehicle is not positively telemetry-confirmed In Air."
        )
    if not target.armed or not heartbeat.armed:
        return NativeLandHereNowState.DISARMED, "Vehicle is disarmed; native Land is blocked."
    if heartbeat.mode_number == ARDUCOPTER_LAND_MODE:
        return NativeLandHereNowState.ALREADY_LANDING, "Vehicle is already in native Land mode."
    if heartbeat.mode_number != ARDUCOPTER_AUTO_MODE:
        return NativeLandHereNowState.UNEXPECTED_MODE, (
            "Land Here Now is available only during the verified AUTO mission."
        )
    progress = telemetry.mission.value
    if telemetry.mission.freshness(now_s) is not TelemetryFreshness.FRESH or progress is None:
        return NativeLandHereNowState.MISSION_MISMATCH, (
            "Fresh native mission-state telemetry is required."
        )
    if progress.mission_state == MAV_MISSION_STATE_COMPLETE:
        return NativeLandHereNowState.MISSION_COMPLETED, "Native mission already reports Complete."
    if progress.mission_state not in (MAV_MISSION_STATE_ACTIVE, MAV_MISSION_STATE_PAUSED):
        return NativeLandHereNowState.MISSION_MISMATCH, (
            "Mission telemetry is neither Active nor Paused."
        )
    if auto_start.state is not NativeAutoStartState.RUNNING or auto_start.authorization is None:
        return NativeLandHereNowState.BLOCKED_AUTO_START, (
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
        return NativeLandHereNowState.BLOCKED_AUTO_START, (
            "AUTO-start evidence does not match this mission/target."
        )
    if not command_channel_idle:
        return NativeLandHereNowState.BLOCKED_BUSY, (
            "Another command transaction owns the channel."
        )
    sequence = (
        progress.current_sequence
        if progress.current_sequence is not None
        else progress.last_reached_sequence
    )
    expected_execution_total = len(package.items) - 1
    if (
        sequence is None
        or not start.first_executable_sequence <= sequence <= start.last_sequence
        or (progress.total_items is not None and progress.total_items != expected_execution_total)
    ):
        return NativeLandHereNowState.MISSION_MISMATCH, (
            "Mission progress is outside the exact verified mission bounds."
        )
    if sequence == start.last_sequence and package.items[sequence].command == int(
        MissionCommand.NAV_LAND
    ):
        return NativeLandHereNowState.ALREADY_LANDING, (
            "Verified mission is already executing its planned native Land."
        )
    authorization = NativeLandHereNowAuthorization(
        vehicle_identity=target.vehicle.value,
        system_id=target.system_id,
        component_id=target.component_id,
        mission_revision=connected.mission_revision,
        expected_mission_digest=connected.transfer_evidence.expected_digest,
        auto_start_revision=auto_start.revision,
        first_executable_sequence=start.first_executable_sequence,
        last_sequence=start.last_sequence,
        progress_sequence=sequence,
        mission_state=progress.mission_state,
    )
    return _LandContext(target, authorization)
