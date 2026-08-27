"""Application-owned native pre-arm request and deliberate review state."""

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
from skywriter.application.telemetry import (
    NativeStatusText,
    TelemetryFreshness,
    TelemetryLinkKind,
    TelemetrySnapshot,
)

MAV_SYS_STATUS_PREARM_CHECK = 1 << 28


class PrearmRequestState(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    TIMED_OUT = "timed_out"
    WRONG_TARGET = "wrong_target"
    WRONG_ACK = "wrong_ack"
    STALE_LINK = "stale_link"
    LINK_LOST = "link_lost"
    CANCELLED = "cancelled"
    BLOCKED_WRONG_LINK = "blocked_wrong_link"
    BLOCKED_ARMED = "blocked_armed"
    BLOCKED_MISSION = "blocked_mission"
    BLOCKED_IDENTITY = "blocked_identity"


class NativePrearmAssessment(StrEnum):
    UNAVAILABLE = "unavailable"
    HEALTHY = "healthy"
    FAILED = "failed"
    CONFLICTING = "conflicting"


_TERMINAL_RESULTS = frozenset(
    {
        PrearmRequestState.ACCEPTED,
        PrearmRequestState.REJECTED,
        PrearmRequestState.UNSUPPORTED,
        PrearmRequestState.TIMED_OUT,
        PrearmRequestState.WRONG_TARGET,
        PrearmRequestState.WRONG_ACK,
        PrearmRequestState.STALE_LINK,
        PrearmRequestState.LINK_LOST,
        PrearmRequestState.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class PrearmCommandResult:
    state: PrearmRequestState
    detail: str
    requested_at_s: float
    completed_at_s: float
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in _TERMINAL_RESULTS:
            raise ValueError("command result must contain a terminal protocol state")
        if not self.detail:
            raise ValueError("command result detail must not be empty")
        if not math.isfinite(self.requested_at_s) or not math.isfinite(self.completed_at_s):
            raise ValueError("command result times must be finite")
        if self.completed_at_s < self.requested_at_s:
            raise ValueError("completed_at_s must not precede requested_at_s")
        if self.ack_result is not None and self.ack_result < 0:
            raise ValueError("ack_result must be non-negative when available")
        object.__setattr__(self, "native_messages", tuple(self.native_messages))


class PrearmCommandGateway(Protocol):
    """The only vehicle command available to the Task 100 use case."""

    def request_prearm_checks(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> PrearmCommandResult: ...


@dataclass(frozen=True, slots=True)
class PrearmReviewContext:
    vehicle_identity: str
    system_id: int
    component_id: int
    mission_revision: int
    expected_mission_digest: str


@dataclass(frozen=True, slots=True)
class PrearmReadinessSnapshot:
    revision: int = 0
    request_state: PrearmRequestState = PrearmRequestState.IDLE
    detail: str = "Native pre-arm checks have not been requested."
    context: PrearmReviewContext | None = None
    telemetry: TelemetrySnapshot | None = None
    ack_result: int | None = None
    native_messages: tuple[NativeStatusText, ...] = ()
    native_assessment: NativePrearmAssessment = NativePrearmAssessment.UNAVAILABLE
    review_acknowledged: bool = False
    repeated_request_ignored: bool = False
    requested_at_s: float | None = None
    completed_at_s: float | None = None

    @property
    def review_available(self) -> bool:
        return self.request_state in _TERMINAL_RESULTS

    @property
    def application_gate_ready(self) -> bool:
        """A reviewed application gate, never a claim that ArduCopter will arm."""

        return bool(
            self.request_state is PrearmRequestState.ACCEPTED
            and self.native_assessment is NativePrearmAssessment.HEALTHY
            and self.review_acknowledged
        )

    @property
    def hardware_safety_text(self) -> str:
        safety_messages = tuple(
            message.text for message in self.native_messages if "safety" in message.text.casefold()
        )
        if safety_messages:
            return " · ".join(safety_messages)
        return "Unavailable — no dedicated native hardware-safety observation received"


class PrearmReadinessService:
    """Fail-closed Task 100 use case; blocking gateway work belongs on a worker."""

    def __init__(self, *, target_valid_for_s: float = 3.0) -> None:
        if not math.isfinite(target_valid_for_s) or target_valid_for_s <= 0:
            raise ValueError("target_valid_for_s must be a positive finite number")
        self._target_valid_for_s = target_valid_for_s
        self._snapshot = PrearmReadinessSnapshot()

    @property
    def snapshot(self) -> PrearmReadinessSnapshot:
        return self._snapshot

    def request_prearm_checks(
        self,
        gateway: PrearmCommandGateway,
        connected: ConnectedMissionSnapshot,
        *,
        now_s: float,
        cancellation: CancellationView,
    ) -> PrearmReadinessSnapshot:
        """Run the dedicated request; callers must invoke this from a worker."""

        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if self._snapshot.request_state is PrearmRequestState.PENDING:
            self._snapshot = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                repeated_request_ignored=True,
                detail="A native pre-arm request is already pending; repeated request ignored.",
            )
            return self._snapshot

        gate = _gate_context(connected, now_s=now_s, target_valid_for_s=self._target_valid_for_s)
        if isinstance(gate[0], PrearmRequestState):
            state, detail = gate
            self._snapshot = PrearmReadinessSnapshot(
                revision=self._snapshot.revision + 1,
                request_state=state,
                detail=detail,
                telemetry=connected.telemetry,
            )
            return self._snapshot

        target, context = gate
        pending_revision = self._snapshot.revision + 1
        self._snapshot = PrearmReadinessSnapshot(
            revision=pending_revision,
            request_state=PrearmRequestState.PENDING,
            detail="Waiting for the matching native COMMAND_ACK and associated status text.",
            context=context,
            telemetry=connected.telemetry,
            requested_at_s=now_s,
        )
        result = gateway.request_prearm_checks(
            target,
            target_valid_for_s=self._target_valid_for_s,
            cancellation=cancellation,
        )
        assessment = _native_assessment(
            connected.telemetry,
            result.native_messages,
            now_s=result.completed_at_s,
        )
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            request_state=result.state,
            detail=result.detail,
            ack_result=result.ack_result,
            native_messages=result.native_messages,
            native_assessment=assessment,
            requested_at_s=result.requested_at_s,
            completed_at_s=result.completed_at_s,
        )
        return self._snapshot

    def acknowledge_review(
        self,
        acknowledged: bool,
        connected: ConnectedMissionSnapshot,
        *,
        now_s: float,
    ) -> PrearmReadinessSnapshot:
        if not isinstance(acknowledged, bool):
            raise TypeError("acknowledged must be a boolean")
        synchronized = self.synchronize_context(connected, now_s=now_s)
        if not acknowledged:
            self._snapshot = replace(
                synchronized,
                revision=synchronized.revision + 1,
                review_acknowledged=False,
            )
            return self._snapshot
        if not synchronized.review_available:
            self._snapshot = replace(
                synchronized,
                revision=synchronized.revision + 1,
                review_acknowledged=False,
                detail="A completed native request must be reviewed before acknowledgment.",
            )
            return self._snapshot
        self._snapshot = replace(
            synchronized,
            revision=synchronized.revision + 1,
            review_acknowledged=True,
        )
        return self._snapshot

    def synchronize_context(
        self,
        connected: ConnectedMissionSnapshot,
        *,
        now_s: float,
    ) -> PrearmReadinessSnapshot:
        gate = _gate_context(connected, now_s=now_s, target_valid_for_s=self._target_valid_for_s)
        if isinstance(gate[0], PrearmRequestState):
            state, detail = gate
            if self._snapshot.review_acknowledged or self._snapshot.context is not None:
                self._snapshot = replace(
                    self._snapshot,
                    revision=self._snapshot.revision + 1,
                    request_state=state,
                    detail=detail,
                    context=None,
                    telemetry=connected.telemetry,
                    review_acknowledged=False,
                )
            return self._snapshot
        _, context = gate
        if self._snapshot.context is not None and self._snapshot.context != context:
            self._snapshot = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                request_state=PrearmRequestState.BLOCKED_MISSION,
                detail="Mission or target context changed; request and review again.",
                context=None,
                telemetry=connected.telemetry,
                review_acknowledged=False,
            )
        return self._snapshot

    def application_gate_ready_at(
        self,
        connected: ConnectedMissionSnapshot,
        *,
        now_s: float,
    ) -> bool:
        self.synchronize_context(connected, now_s=now_s)
        return self._snapshot.application_gate_ready


def _gate_context(
    connected: ConnectedMissionSnapshot,
    *,
    now_s: float,
    target_valid_for_s: float,
) -> tuple[ConnectedTarget, PrearmReviewContext] | tuple[PrearmRequestState, str]:
    if not connected.link_connected:
        return PrearmRequestState.LINK_LOST, "A connected SiK link is required."
    if connected.link_kind is not TelemetryLinkKind.SIK:
        return PrearmRequestState.BLOCKED_WRONG_LINK, "Native pre-arm review requires SiK."
    if (
        connected.verification_state is not ConnectedVerificationState.SIK_VERIFIED
        or connected.expected_package is None
        or connected.transfer_evidence is None
        or connected.mission_revision is None
        or connected.compiled is None
    ):
        return (
            PrearmRequestState.BLOCKED_MISSION,
            "The current mission must have an exact same-vehicle SiK readback verification.",
        )
    target = connected.selected_target
    telemetry = connected.telemetry
    if target is None or target.vehicle != connected.expected_package.vehicle:
        return PrearmRequestState.BLOCKED_IDENTITY, "Selected target identity is unresolved."
    if target.link_kind is not TelemetryLinkKind.SIK:
        return PrearmRequestState.BLOCKED_WRONG_LINK, "Selected target is not on SiK."
    if telemetry is None or (
        telemetry.vehicle_identity != target.vehicle.value
        or telemetry.target_system != target.system_id
        or telemetry.target_component != target.component_id
        or telemetry.link_kind is not TelemetryLinkKind.SIK
    ):
        return (
            PrearmRequestState.BLOCKED_IDENTITY,
            "Fresh telemetry is not from the selected target.",
        )
    if target.armed or (telemetry.heartbeat.value is not None and telemetry.heartbeat.value.armed):
        return PrearmRequestState.BLOCKED_ARMED, "Native pre-arm review requires disarmed state."
    if not target.is_fresh(now_s, target_valid_for_s) or not telemetry.command_gate_fresh(now_s):
        return PrearmRequestState.STALE_LINK, "Selected-target heartbeat is stale."
    context = PrearmReviewContext(
        vehicle_identity=target.vehicle.value,
        system_id=target.system_id,
        component_id=target.component_id,
        mission_revision=connected.mission_revision,
        expected_mission_digest=connected.transfer_evidence.expected_digest,
    )
    return target, context


def _native_assessment(
    telemetry: TelemetrySnapshot | None,
    native_messages: tuple[NativeStatusText, ...],
    *,
    now_s: float,
) -> NativePrearmAssessment:
    failure_text = any(message.text.casefold().startswith("prearm:") for message in native_messages)
    if telemetry is None or telemetry.sensors.freshness(now_s) is not TelemetryFreshness.FRESH:
        return NativePrearmAssessment.FAILED if failure_text else NativePrearmAssessment.UNAVAILABLE
    sensors = telemetry.sensors.value
    assert sensors is not None
    present = bool(sensors.present_flags & MAV_SYS_STATUS_PREARM_CHECK)
    enabled = bool(sensors.enabled_flags & MAV_SYS_STATUS_PREARM_CHECK)
    healthy = bool(sensors.health_flags & MAV_SYS_STATUS_PREARM_CHECK)
    bitmap_healthy = present and enabled and healthy
    if bitmap_healthy and failure_text:
        return NativePrearmAssessment.CONFLICTING
    if not bitmap_healthy:
        return NativePrearmAssessment.FAILED
    return NativePrearmAssessment.HEALTHY
