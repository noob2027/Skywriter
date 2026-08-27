"""Application-owned native AUTO mission-start authorization and state."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from skywriter.application.arm import NormalArmSnapshot, NormalArmState
from skywriter.application.connected import (
    CancellationView,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
)
from skywriter.application.telemetry import NativeStatusText, TelemetryLinkKind
from skywriter.domain.compiled import MissionCommand


class NativeAutoStartState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    RUNNING = "running"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    WRONG_TARGET = "wrong_target"
    WRONG_ACK = "wrong_ack"
    STALE_LINK = "stale_link"
    LINK_LOST = "link_lost"
    ACKNOWLEDGED_NO_AUTO_TELEMETRY = "acknowledged_no_auto_telemetry"
    ACKNOWLEDGED_NO_MISSION_PROGRESS = "acknowledged_no_mission_progress"
    UNEXPECTED_MODE = "unexpected_mode"
    MISSION_MISMATCH = "mission_mismatch"
    DISARMED = "disarmed"
    TELEMETRY_DISAGREEMENT = "telemetry_disagreement"
    BLOCKED_WRONG_LINK = "blocked_wrong_link"
    BLOCKED_DISARMED = "blocked_disarmed"
    BLOCKED_MISSION = "blocked_mission"
    BLOCKED_ARM = "blocked_arm"
    BLOCKED_IDENTITY = "blocked_identity"
    BLOCKED_BUSY = "blocked_busy"
    BLOCKED_SEQUENCE = "blocked_sequence"
    BLOCKED_ALREADY_AUTO = "blocked_already_auto"


_PROTOCOL_RESULTS = frozenset(
    {
        NativeAutoStartState.RUNNING,
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
    }
)


@dataclass(frozen=True, slots=True)
class NativeAutoStartCommandResult:
    state: NativeAutoStartState
    detail: str
    requested_at_s: float
    completed_at_s: float
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    auto_observed_at_s: float | None = None
    progress_observed_at_s: float | None = None
    progress_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.state not in _PROTOCOL_RESULTS:
            raise ValueError("AUTO-start result must contain a terminal protocol state")
        if not self.detail:
            raise ValueError("AUTO-start detail must not be empty")
        if not math.isfinite(self.requested_at_s) or not math.isfinite(self.completed_at_s):
            raise ValueError("AUTO-start result times must be finite")
        if self.completed_at_s < self.requested_at_s:
            raise ValueError("completed_at_s must not precede requested_at_s")
        for name in ("auto_observed_at_s", "progress_observed_at_s"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < self.requested_at_s):
                raise ValueError(f"{name} must be finite and follow the request")
        if self.progress_sequence is not None and self.progress_sequence < 1:
            raise ValueError("progress_sequence must identify an executable mission item")
        if self.state is NativeAutoStartState.RUNNING and (
            self.auto_observed_at_s is None
            or self.progress_observed_at_s is None
            or self.progress_sequence is None
        ):
            raise ValueError("Running requires later AUTO and mission-progress telemetry")
        object.__setattr__(self, "native_messages", tuple(self.native_messages))


class NativeAutoStartGateway(Protocol):
    """Closed gateway for the one pinned native mission-start action."""

    def request_native_auto_start(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativeAutoStartCommandResult: ...


@dataclass(frozen=True, slots=True)
class NativeAutoStartAuthorization:
    vehicle_identity: str
    system_id: int
    component_id: int
    mission_revision: int
    expected_mission_digest: str
    arm_revision: int
    first_executable_sequence: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class NativeAutoStartSnapshot:
    revision: int = 0
    state: NativeAutoStartState = NativeAutoStartState.IDLE
    detail: str = "Start Mission is blocked until Arm and exact mission evidence are current."
    authorization: NativeAutoStartAuthorization | None = None
    request_available: bool = False
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    repeated_request_ignored: bool = False
    requested_at_s: float | None = None
    completed_at_s: float | None = None
    auto_observed_at_s: float | None = None
    progress_observed_at_s: float | None = None
    progress_sequence: int | None = None

    def __post_init__(self) -> None:
        if self.state is NativeAutoStartState.RUNNING and (
            self.auto_observed_at_s is None
            or self.progress_observed_at_s is None
            or self.progress_sequence is None
        ):
            raise ValueError("Running presentation requires AUTO and progress telemetry")
        if self.state in (NativeAutoStartState.PENDING, NativeAutoStartState.RUNNING) and (
            self.request_available
        ):
            raise ValueError("pending or Running state cannot enable another request")


class NativeAutoStartService:
    """Fail-closed Task 102 use case; run its gateway call from a worker."""

    def __init__(self, *, target_valid_for_s: float = 3.0) -> None:
        if not math.isfinite(target_valid_for_s) or target_valid_for_s <= 0:
            raise ValueError("target_valid_for_s must be a positive finite number")
        self._target_valid_for_s = target_valid_for_s
        self._snapshot = NativeAutoStartSnapshot()

    @property
    def snapshot(self) -> NativeAutoStartSnapshot:
        return self._snapshot

    def synchronize_context(
        self,
        connected: ConnectedMissionSnapshot,
        arm: NormalArmSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
    ) -> NativeAutoStartSnapshot:
        if not isinstance(command_channel_idle, bool):
            raise TypeError("command_channel_idle must be a boolean")
        if self._snapshot.state is NativeAutoStartState.PENDING:
            return self._snapshot
        if self._snapshot.state is NativeAutoStartState.RUNNING:
            running_failure = _running_context_failure(
                connected,
                now_s=now_s,
                target_valid_for_s=self._target_valid_for_s,
            )
            if running_failure is None:
                return self._snapshot
            state, detail = running_failure
            self._snapshot = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                state=state,
                detail=detail,
            )
            return self._snapshot
        gate = _start_gate(
            connected,
            arm,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(gate[0], NativeAutoStartState):
            state, detail = gate
            if (
                self._snapshot.state is not state
                or self._snapshot.request_available
                or self._snapshot.authorization is not None
            ):
                self._snapshot = NativeAutoStartSnapshot(
                    revision=self._snapshot.revision + 1,
                    state=state,
                    detail=detail,
                )
            return self._snapshot
        _, authorization = gate
        if self._snapshot.authorization not in (None, authorization):
            self._snapshot = NativeAutoStartSnapshot(
                revision=self._snapshot.revision + 1,
                state=NativeAutoStartState.BLOCKED_MISSION,
                detail="Mission or Arm evidence changed; reverify and review before starting.",
            )
            return self._snapshot
        if self._snapshot.authorization is None:
            self._snapshot = NativeAutoStartSnapshot(
                revision=self._snapshot.revision + 1,
                state=NativeAutoStartState.IDLE,
                detail="Current armed vehicle and exact mission evidence permit native AUTO start.",
                authorization=authorization,
                request_available=True,
            )
        return self._snapshot

    def request_native_auto_start(
        self,
        gateway: NativeAutoStartGateway,
        connected: ConnectedMissionSnapshot,
        arm: NormalArmSnapshot,
        *,
        now_s: float,
        command_channel_idle: bool,
        cancellation: CancellationView,
    ) -> NativeAutoStartSnapshot:
        """Run only the pinned start action; callers invoke this from a worker."""

        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if self._snapshot.state is NativeAutoStartState.PENDING:
            self._snapshot = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                repeated_request_ignored=True,
                detail="A native mission-start request is pending; repeated request ignored.",
            )
            return self._snapshot
        gate = _start_gate(
            connected,
            arm,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(gate[0], NativeAutoStartState):
            state, detail = gate
            self._snapshot = NativeAutoStartSnapshot(
                revision=self._snapshot.revision + 1,
                state=state,
                detail=detail,
            )
            return self._snapshot

        target, authorization = gate
        self._snapshot = NativeAutoStartSnapshot(
            revision=self._snapshot.revision + 1,
            state=NativeAutoStartState.PENDING,
            detail="Waiting for the exact ACK, armed AUTO mode, and native mission progress.",
            authorization=authorization,
            requested_at_s=now_s,
        )
        result = gateway.request_native_auto_start(
            target,
            expected_item_count=authorization.last_sequence + 1,
            target_valid_for_s=self._target_valid_for_s,
            cancellation=cancellation,
        )
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            state=result.state,
            detail=result.detail,
            request_available=False,
            ack_result=result.ack_result,
            native_messages=result.native_messages,
            requested_at_s=result.requested_at_s,
            completed_at_s=result.completed_at_s,
            auto_observed_at_s=result.auto_observed_at_s,
            progress_observed_at_s=result.progress_observed_at_s,
            progress_sequence=result.progress_sequence,
        )
        return self._snapshot


def _start_gate(
    connected: ConnectedMissionSnapshot,
    arm: NormalArmSnapshot,
    *,
    now_s: float,
    target_valid_for_s: float,
    command_channel_idle: bool,
) -> tuple[ConnectedTarget, NativeAutoStartAuthorization] | tuple[NativeAutoStartState, str]:
    if not connected.link_connected:
        return NativeAutoStartState.LINK_LOST, "A connected SiK link is required."
    if connected.link_kind is not TelemetryLinkKind.SIK:
        return NativeAutoStartState.BLOCKED_WRONG_LINK, "Start Mission requires SiK."
    if (
        connected.verification_state is not ConnectedVerificationState.SIK_VERIFIED
        or connected.expected_package is None
        or connected.transfer_evidence is None
        or connected.mission_revision is None
        or connected.compiled is None
    ):
        return (
            NativeAutoStartState.BLOCKED_MISSION,
            "Exact current mission verification is required.",
        )
    target = connected.selected_target
    telemetry = connected.telemetry
    package = connected.expected_package
    if target is None or target.vehicle != package.vehicle:
        return NativeAutoStartState.BLOCKED_IDENTITY, "Selected target identity is unresolved."
    if target.link_kind is not TelemetryLinkKind.SIK:
        return NativeAutoStartState.BLOCKED_WRONG_LINK, "Selected target is not on SiK."
    if telemetry is None or (
        telemetry.vehicle_identity != target.vehicle.value
        or telemetry.target_system != target.system_id
        or telemetry.target_component != target.component_id
        or telemetry.link_kind is not TelemetryLinkKind.SIK
    ):
        return NativeAutoStartState.BLOCKED_IDENTITY, "Telemetry is not from the selected target."
    heartbeat = telemetry.heartbeat.value
    if heartbeat is None:
        return NativeAutoStartState.STALE_LINK, "Selected-target armed state is unavailable."
    if target.armed != heartbeat.armed:
        return (
            NativeAutoStartState.TELEMETRY_DISAGREEMENT,
            "Selected-target heartbeat sources disagree about armed state.",
        )
    if not target.armed:
        return NativeAutoStartState.BLOCKED_DISARMED, "Vehicle must be telemetry-confirmed Armed."
    if not target.is_fresh(now_s, target_valid_for_s) or not telemetry.command_gate_fresh(now_s):
        return NativeAutoStartState.STALE_LINK, "Selected-target telemetry is stale."
    if heartbeat.mode_number == 3:
        return NativeAutoStartState.BLOCKED_ALREADY_AUTO, "Vehicle is already in AUTO."
    if not command_channel_idle:
        return NativeAutoStartState.BLOCKED_BUSY, "Another command transaction owns the channel."
    if arm.state is not NormalArmState.ARMED or arm.authorization is None:
        return NativeAutoStartState.BLOCKED_ARM, "Current Task 101 Armed evidence is required."
    review = arm.authorization.review_context
    if (
        review.vehicle_identity != target.vehicle.value
        or review.system_id != target.system_id
        or review.component_id != target.component_id
        or review.mission_revision != connected.mission_revision
        or review.expected_mission_digest != connected.transfer_evidence.expected_digest
    ):
        return (
            NativeAutoStartState.BLOCKED_ARM,
            "Armed evidence does not match this mission/target.",
        )

    items = package.items
    compiled_items = connected.compiled.items
    sequences = tuple(item.sequence for item in items)
    if (
        len(items) < 3
        or not compiled_items
        or sequences != tuple(range(len(items)))
        or items[1].command != int(MissionCommand.NAV_TAKEOFF)
        or compiled_items[0].command is not MissionCommand.NAV_TAKEOFF
    ):
        return (
            NativeAutoStartState.BLOCKED_SEQUENCE,
            "Verified native mission does not have Home 0 followed by Takeoff 1.",
        )
    authorization = NativeAutoStartAuthorization(
        vehicle_identity=target.vehicle.value,
        system_id=target.system_id,
        component_id=target.component_id,
        mission_revision=connected.mission_revision,
        expected_mission_digest=connected.transfer_evidence.expected_digest,
        arm_revision=arm.revision,
        first_executable_sequence=1,
        last_sequence=len(items) - 1,
    )
    return target, authorization


def _running_context_failure(
    connected: ConnectedMissionSnapshot,
    *,
    now_s: float,
    target_valid_for_s: float,
) -> tuple[NativeAutoStartState, str] | None:
    if not connected.link_connected:
        return (
            NativeAutoStartState.LINK_LOST,
            "Command link was lost; onboard behavior remains native.",
        )
    if connected.link_kind is not TelemetryLinkKind.SIK:
        return NativeAutoStartState.BLOCKED_WRONG_LINK, "Running target is no longer on SiK."
    if connected.verification_state is not ConnectedVerificationState.SIK_VERIFIED:
        return (
            NativeAutoStartState.MISSION_MISMATCH,
            "Current mission verification was invalidated.",
        )
    target = connected.selected_target
    telemetry = connected.telemetry
    package = connected.expected_package
    if target is None or telemetry is None or package is None:
        return NativeAutoStartState.BLOCKED_IDENTITY, "Selected-target telemetry is unavailable."
    if (
        target.vehicle != package.vehicle
        or telemetry.vehicle_identity != target.vehicle.value
        or telemetry.target_system != target.system_id
        or telemetry.target_component != target.component_id
        or telemetry.link_kind is not TelemetryLinkKind.SIK
    ):
        return NativeAutoStartState.BLOCKED_IDENTITY, "Running target identity changed."
    if not target.is_fresh(now_s, target_valid_for_s) or not telemetry.command_gate_fresh(now_s):
        return NativeAutoStartState.STALE_LINK, "Running telemetry became stale."
    heartbeat = telemetry.heartbeat.value
    if heartbeat is None:
        return NativeAutoStartState.STALE_LINK, "Running heartbeat is unavailable."
    if not heartbeat.armed or not target.armed:
        return NativeAutoStartState.DISARMED, "Vehicle disarmed while mission state was Running."
    if heartbeat.mode_number != 3:
        return NativeAutoStartState.UNEXPECTED_MODE, "Vehicle left AUTO while mission was Running."
    progress = telemetry.mission.value
    if progress is not None:
        observed = (
            progress.current_sequence
            if progress.current_sequence is not None
            else progress.last_reached_sequence
        )
        if observed is not None and not 1 <= observed < len(package.items):
            return (
                NativeAutoStartState.MISSION_MISMATCH,
                "Mission progress is outside verified bounds.",
            )
    return None
