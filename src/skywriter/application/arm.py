"""Application-owned normal-arm authorization and presentation state."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from skywriter.application.connected import (
    CancellationView,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
)
from skywriter.application.prearm import PrearmReadinessService, PrearmReviewContext
from skywriter.application.telemetry import NativeStatusText, TelemetryLinkKind


class NormalArmState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    ARMED = "armed"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    WRONG_TARGET = "wrong_target"
    WRONG_ACK = "wrong_ack"
    STALE_LINK = "stale_link"
    LINK_LOST = "link_lost"
    ACKNOWLEDGED_NO_ARMED_TELEMETRY = "acknowledged_no_armed_telemetry"
    TELEMETRY_DISAGREEMENT = "telemetry_disagreement"
    BLOCKED_WRONG_LINK = "blocked_wrong_link"
    BLOCKED_ARMED = "blocked_armed"
    BLOCKED_MISSION = "blocked_mission"
    BLOCKED_READINESS = "blocked_readiness"
    BLOCKED_IDENTITY = "blocked_identity"
    BLOCKED_BUSY = "blocked_busy"


_PROTOCOL_RESULTS = frozenset(
    {
        NormalArmState.ARMED,
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
    }
)


@dataclass(frozen=True, slots=True)
class NormalArmCommandResult:
    state: NormalArmState
    detail: str
    requested_at_s: float
    completed_at_s: float
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    armed_observed_at_s: float | None = None

    def __post_init__(self) -> None:
        if self.state not in _PROTOCOL_RESULTS:
            raise ValueError("normal-arm result must contain a terminal protocol state")
        if not self.detail:
            raise ValueError("normal-arm detail must not be empty")
        if not math.isfinite(self.requested_at_s) or not math.isfinite(self.completed_at_s):
            raise ValueError("normal-arm result times must be finite")
        if self.completed_at_s < self.requested_at_s:
            raise ValueError("completed_at_s must not precede requested_at_s")
        if self.ack_result is not None and self.ack_result < 0:
            raise ValueError("ack_result must be non-negative when available")
        if self.armed_observed_at_s is not None:
            if not math.isfinite(self.armed_observed_at_s):
                raise ValueError("armed_observed_at_s must be finite")
            if self.armed_observed_at_s < self.requested_at_s:
                raise ValueError("armed telemetry must follow the request")
        if self.state is NormalArmState.ARMED and self.armed_observed_at_s is None:
            raise ValueError("Armed requires selected-target armed telemetry")
        object.__setattr__(self, "native_messages", tuple(self.native_messages))


class NormalArmGateway(Protocol):
    """The only vehicle action available to the Task 101 use case."""

    def request_normal_arm(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NormalArmCommandResult: ...


@dataclass(frozen=True, slots=True)
class NormalArmAuthorization:
    review_context: PrearmReviewContext
    readiness_revision: int


@dataclass(frozen=True, slots=True)
class NormalArmSnapshot:
    revision: int = 0
    state: NormalArmState = NormalArmState.IDLE
    detail: str = "Normal Arm is blocked until the current readiness review is complete."
    authorization: NormalArmAuthorization | None = None
    request_available: bool = False
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    repeated_request_ignored: bool = False
    requested_at_s: float | None = None
    completed_at_s: float | None = None
    armed_observed_at_s: float | None = None

    def __post_init__(self) -> None:
        if self.state is NormalArmState.ARMED and self.armed_observed_at_s is None:
            raise ValueError("Armed presentation requires selected-target telemetry proof")
        if self.state in (NormalArmState.PENDING, NormalArmState.ARMED) and self.request_available:
            raise ValueError("pending or Armed state cannot enable another request")


class NormalArmService:
    """Fail-closed Task 101 use case; invoke blocking gateway work from a worker."""

    def __init__(self, *, target_valid_for_s: float = 3.0) -> None:
        if not math.isfinite(target_valid_for_s) or target_valid_for_s <= 0:
            raise ValueError("target_valid_for_s must be a positive finite number")
        self._target_valid_for_s = target_valid_for_s
        self._snapshot = NormalArmSnapshot()

    @property
    def snapshot(self) -> NormalArmSnapshot:
        return self._snapshot

    def synchronize_context(
        self,
        connected: ConnectedMissionSnapshot,
        readiness: PrearmReadinessService,
        *,
        now_s: float,
        command_channel_idle: bool,
    ) -> NormalArmSnapshot:
        if not isinstance(command_channel_idle, bool):
            raise TypeError("command_channel_idle must be a boolean")
        if self._snapshot.state is NormalArmState.PENDING:
            return self._snapshot
        gate = _arm_gate(
            connected,
            readiness,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(gate[0], NormalArmState):
            state, detail = gate
            if (
                self._snapshot.state is not state
                or self._snapshot.request_available
                or self._snapshot.authorization is not None
            ):
                self._snapshot = NormalArmSnapshot(
                    revision=self._snapshot.revision + 1,
                    state=state,
                    detail=detail,
                )
            return self._snapshot

        _, authorization = gate
        if self._snapshot.authorization not in (None, authorization):
            self._snapshot = NormalArmSnapshot(
                revision=self._snapshot.revision + 1,
                state=NormalArmState.BLOCKED_READINESS,
                detail="Readiness evidence changed; run and review native checks again.",
            )
            return self._snapshot
        if self._snapshot.authorization is None:
            self._snapshot = NormalArmSnapshot(
                revision=self._snapshot.revision + 1,
                state=NormalArmState.IDLE,
                detail="Current mission, SiK telemetry, and readiness review permit normal Arm.",
                authorization=authorization,
                request_available=True,
            )
        return self._snapshot

    def request_normal_arm(
        self,
        gateway: NormalArmGateway,
        connected: ConnectedMissionSnapshot,
        readiness: PrearmReadinessService,
        *,
        now_s: float,
        command_channel_idle: bool,
        cancellation: CancellationView,
    ) -> NormalArmSnapshot:
        """Run the dedicated normal request; callers must invoke this from a worker."""

        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if self._snapshot.state is NormalArmState.PENDING:
            self._snapshot = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                repeated_request_ignored=True,
                detail="A normal Arm request is already pending; repeated request ignored.",
            )
            return self._snapshot

        gate = _arm_gate(
            connected,
            readiness,
            now_s=now_s,
            target_valid_for_s=self._target_valid_for_s,
            command_channel_idle=command_channel_idle,
        )
        if isinstance(gate[0], NormalArmState):
            state, detail = gate
            self._snapshot = NormalArmSnapshot(
                revision=self._snapshot.revision + 1,
                state=state,
                detail=detail,
            )
            return self._snapshot

        target, authorization = gate
        self._snapshot = NormalArmSnapshot(
            revision=self._snapshot.revision + 1,
            state=NormalArmState.PENDING,
            detail="Waiting for the exact acknowledgment and fresh armed telemetry.",
            authorization=authorization,
            requested_at_s=now_s,
        )
        result = gateway.request_normal_arm(
            target,
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
            armed_observed_at_s=result.armed_observed_at_s,
        )
        return self._snapshot


def _arm_gate(
    connected: ConnectedMissionSnapshot,
    readiness: PrearmReadinessService,
    *,
    now_s: float,
    target_valid_for_s: float,
    command_channel_idle: bool,
) -> tuple[ConnectedTarget, NormalArmAuthorization] | tuple[NormalArmState, str]:
    if not connected.link_connected:
        return NormalArmState.LINK_LOST, "A connected SiK link is required."
    if connected.link_kind is not TelemetryLinkKind.SIK:
        return NormalArmState.BLOCKED_WRONG_LINK, "Normal Arm requires SiK."
    if (
        connected.verification_state is not ConnectedVerificationState.SIK_VERIFIED
        or connected.expected_package is None
        or connected.transfer_evidence is None
        or connected.mission_revision is None
        or connected.compiled is None
    ):
        return NormalArmState.BLOCKED_MISSION, "Exact current mission verification is required."
    target = connected.selected_target
    telemetry = connected.telemetry
    if target is None or target.vehicle != connected.expected_package.vehicle:
        return NormalArmState.BLOCKED_IDENTITY, "Selected target identity is unresolved."
    if target.link_kind is not TelemetryLinkKind.SIK:
        return NormalArmState.BLOCKED_WRONG_LINK, "Selected target is not on SiK."
    if telemetry is None or (
        telemetry.vehicle_identity != target.vehicle.value
        or telemetry.target_system != target.system_id
        or telemetry.target_component != target.component_id
        or telemetry.link_kind is not TelemetryLinkKind.SIK
    ):
        return NormalArmState.BLOCKED_IDENTITY, "Telemetry is not from the selected target."
    heartbeat = telemetry.heartbeat.value
    if heartbeat is None:
        return NormalArmState.STALE_LINK, "Selected-target armed state is unavailable."
    if target.armed != heartbeat.armed:
        return (
            NormalArmState.TELEMETRY_DISAGREEMENT,
            "Selected-target heartbeat sources disagree about armed state.",
        )
    if target.armed:
        return NormalArmState.BLOCKED_ARMED, "Vehicle was already armed before this request."
    if not target.is_fresh(now_s, target_valid_for_s) or not telemetry.command_gate_fresh(now_s):
        return NormalArmState.STALE_LINK, "Selected-target telemetry is stale."
    if not command_channel_idle:
        return NormalArmState.BLOCKED_BUSY, "Another command transaction owns the channel."
    if not readiness.application_gate_ready_at(connected, now_s=now_s):
        return (
            NormalArmState.BLOCKED_READINESS,
            "Current Task 100 readiness evidence and explicit review are required.",
        )
    review = readiness.snapshot
    if review.context is None:
        return NormalArmState.BLOCKED_READINESS, "Readiness review context is unavailable."
    authorization = NormalArmAuthorization(review.context, review.revision)
    return target, authorization
