"""Dedicated native AUTO mission-start transaction for stock ArduCopter 4.6.3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from skywriter.application.auto_start import (
    NativeAutoStartCommandResult,
    NativeAutoStartState,
)
from skywriter.application.connected import CancellationView, ConnectedTarget
from skywriter.application.telemetry import NativeStatusText, TelemetryLinkKind
from skywriter.infrastructure.mavlink.connection import (
    MAV_CMD_MISSION_START as MAV_CMD_MISSION_START,
)
from skywriter.infrastructure.mavlink.connection import (
    MAV_MODE_FLAG_SAFETY_ARMED,
    Clock,
    IncomingMessage,
    MavlinkAddress,
    TransportDescriptor,
    TransportKind,
)

MAV_RESULT_ACCEPTED = 0
MAV_RESULT_UNSUPPORTED = 3
ARDUCOPTER_AUTO_MODE = 3
MISSION_TYPE_MISSION = 0


class NativeAutoStartLink(Protocol):
    """Closed link surface for exactly one pinned native mission-start action."""

    descriptor: TransportDescriptor
    local_address: MavlinkAddress

    def is_connected(self) -> bool: ...

    def receive(self, timeout_s: float) -> IncomingMessage | None: ...

    def send_native_auto_start(self, target: MavlinkAddress) -> None: ...


@dataclass(frozen=True, slots=True)
class NativeAutoStartProtocolPolicy:
    ack_timeout_s: float = 5.0
    telemetry_timeout_s: float = 15.0
    negative_capture_s: float = 0.75
    max_poll_s: float = 0.5

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


class NativeAutoStartGateway:
    """Send fixed command 300 and require later AUTO plus mission progress."""

    def __init__(
        self,
        link: NativeAutoStartLink,
        *,
        clock: Clock,
        policy: NativeAutoStartProtocolPolicy | None = None,
    ) -> None:
        if link.descriptor.kind is not TransportKind.SIK:
            raise ValueError("native AUTO start requires an explicitly classified SiK link")
        self._link = link
        self._clock = clock
        self._policy = policy or NativeAutoStartProtocolPolicy()

    def request_native_auto_start(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativeAutoStartCommandResult:
        requested_at_s = self._clock.now()
        if expected_item_count < 3:
            return self._result(
                NativeAutoStartState.MISSION_MISMATCH,
                "Verified native mission is too short for Home, Takeoff, and Land.",
                requested_at_s,
            )
        if target.link_kind is not TelemetryLinkKind.SIK:
            return self._result(
                NativeAutoStartState.WRONG_TARGET,
                "Selected target is not on the active SiK command link.",
                requested_at_s,
            )
        if not target.armed:
            return self._result(
                NativeAutoStartState.DISARMED,
                "Selected target was disarmed before transmission; no request was sent.",
                requested_at_s,
            )
        if not self._link.is_connected():
            return self._result(
                NativeAutoStartState.LINK_LOST,
                "SiK command link is disconnected.",
                requested_at_s,
            )
        if not target.is_fresh(requested_at_s, target_valid_for_s):
            return self._result(
                NativeAutoStartState.STALE_LINK,
                "Selected-target heartbeat was stale before command transmission.",
                requested_at_s,
            )

        address = MavlinkAddress(target.system_id, target.component_id)
        try:
            self._link.send_native_auto_start(address)
        except ConnectionError as error:
            return self._result(NativeAutoStartState.LINK_LOST, str(error), requested_at_s)

        deadline_s = requested_at_s + self._policy.ack_timeout_s
        last_heartbeat_s = target.observed_at_s
        messages: list[NativeStatusText] = []
        while self._clock.now() < deadline_s:
            interrupted = self._interruption(
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                messages=messages,
                accepted=False,
            )
            if interrupted is not None:
                return interrupted
            incoming = self._receive(requested_at_s, messages)
            if isinstance(incoming, NativeAutoStartCommandResult):
                return incoming
            if incoming is None:
                continue
            observed_at_s = self._clock.now()
            if incoming.name == "HEARTBEAT" and incoming.source == address:
                last_heartbeat_s = observed_at_s
                continue
            status = _status_text(incoming, observed_at_s=observed_at_s, target=address)
            if status is not None:
                messages.append(status)
                continue
            if incoming.name != "COMMAND_ACK":
                continue
            if incoming.source != address:
                return self._result(
                    NativeAutoStartState.WRONG_TARGET,
                    "Received mission-start acknowledgment from a different target.",
                    requested_at_s,
                    messages=messages,
                )
            if _integer_or_none(incoming, "command") != MAV_CMD_MISSION_START:
                return self._result(
                    NativeAutoStartState.WRONG_ACK,
                    "Received an acknowledgment for a different command.",
                    requested_at_s,
                    messages=messages,
                )
            if not _ack_addresses_local_gcs(incoming, self._link.local_address):
                return self._result(
                    NativeAutoStartState.WRONG_ACK,
                    "Mission-start acknowledgment was addressed to a different GCS.",
                    requested_at_s,
                    messages=messages,
                )
            result = _integer_or_none(incoming, "result")
            if result is None or result < 0:
                return self._result(
                    NativeAutoStartState.WRONG_ACK,
                    "Mission-start acknowledgment omitted a valid result.",
                    requested_at_s,
                    messages=messages,
                )
            if result == MAV_RESULT_ACCEPTED:
                return self._await_running_telemetry(
                    address,
                    cancellation,
                    requested_at_s=requested_at_s,
                    last_heartbeat_s=last_heartbeat_s,
                    target_valid_for_s=target_valid_for_s,
                    expected_item_count=expected_item_count,
                    ack_result=result,
                    messages=messages,
                )
            captured = self._capture_after_negative_ack(
                address,
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                ack_result=result,
                messages=messages,
            )
            if isinstance(captured, NativeAutoStartCommandResult):
                return captured
            messages = list(captured)
            if result == MAV_RESULT_UNSUPPORTED:
                return self._result(
                    NativeAutoStartState.UNSUPPORTED,
                    "Target reported native mission start as unsupported.",
                    requested_at_s,
                    ack_result=result,
                    messages=messages,
                )
            return self._result(
                NativeAutoStartState.REJECTED,
                f"ArduCopter rejected native mission start with MAV_RESULT {result}.",
                requested_at_s,
                ack_result=result,
                messages=messages,
            )
        return self._result(
            NativeAutoStartState.TIMED_OUT,
            "No matching native mission-start acknowledgment arrived before the deadline.",
            requested_at_s,
            messages=messages,
        )

    def _await_running_telemetry(
        self,
        target: MavlinkAddress,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        expected_item_count: int,
        ack_result: int,
        messages: list[NativeStatusText],
    ) -> NativeAutoStartCommandResult:
        deadline_s = self._clock.now() + self._policy.telemetry_timeout_s
        auto_observed_at_s: float | None = None
        progress_observed_at_s: float | None = None
        progress_sequence: int | None = None
        unexpected_mode: int | None = None
        while self._clock.now() < deadline_s:
            interrupted = self._interruption(
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                messages=messages,
                accepted=True,
                ack_result=ack_result,
            )
            if interrupted is not None:
                return interrupted
            incoming = self._receive(requested_at_s, messages, ack_result=ack_result)
            if isinstance(incoming, NativeAutoStartCommandResult):
                return incoming
            if incoming is None:
                continue
            observed_at_s = self._clock.now()
            if incoming.name == "HEARTBEAT" and incoming.source == target:
                last_heartbeat_s = observed_at_s
                base_mode = _integer_or_none(incoming, "base_mode")
                custom_mode = _integer_or_none(incoming, "custom_mode")
                if base_mode is None or custom_mode is None:
                    continue
                if not base_mode & MAV_MODE_FLAG_SAFETY_ARMED:
                    return self._result(
                        NativeAutoStartState.DISARMED,
                        "Mission start was acknowledged, but selected-target telemetry disarmed.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                if custom_mode == ARDUCOPTER_AUTO_MODE:
                    auto_observed_at_s = observed_at_s
                else:
                    unexpected_mode = custom_mode
            elif incoming.name in {"MISSION_CURRENT", "MISSION_ITEM_REACHED"}:
                if incoming.source != target:
                    continue
                sequence = _integer_or_none(incoming, "seq")
                mission_type = _integer_or_none(incoming, "mission_type")
                if mission_type not in (None, MISSION_TYPE_MISSION):
                    return self._result(
                        NativeAutoStartState.MISSION_MISMATCH,
                        "Mission progress reported a different mission type.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                        auto_observed_at_s=auto_observed_at_s,
                    )
                if sequence is None or not 1 <= sequence < expected_item_count:
                    return self._result(
                        NativeAutoStartState.MISSION_MISMATCH,
                        "Mission progress was outside the exact verified mission bounds.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                        auto_observed_at_s=auto_observed_at_s,
                    )
                progress_observed_at_s = observed_at_s
                progress_sequence = sequence
            else:
                status = _status_text(incoming, observed_at_s=observed_at_s, target=target)
                if status is not None:
                    messages.append(status)
            if auto_observed_at_s is not None and progress_observed_at_s is not None:
                return self._result(
                    NativeAutoStartState.RUNNING,
                    "Native mission start acknowledged; armed AUTO and mission progress confirmed.",
                    requested_at_s,
                    ack_result=ack_result,
                    messages=messages,
                    auto_observed_at_s=auto_observed_at_s,
                    progress_observed_at_s=progress_observed_at_s,
                    progress_sequence=progress_sequence,
                )
        if auto_observed_at_s is not None:
            return self._result(
                NativeAutoStartState.ACKNOWLEDGED_NO_MISSION_PROGRESS,
                "Mission start was acknowledged and AUTO observed, but progress was absent.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
                auto_observed_at_s=auto_observed_at_s,
            )
        if unexpected_mode is not None:
            return self._result(
                NativeAutoStartState.UNEXPECTED_MODE,
                f"Mission start was acknowledged, but vehicle mode remained {unexpected_mode}.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
                progress_observed_at_s=progress_observed_at_s,
                progress_sequence=progress_sequence,
            )
        return self._result(
            NativeAutoStartState.ACKNOWLEDGED_NO_AUTO_TELEMETRY,
            "Mission start was acknowledged, but fresh selected-target AUTO telemetry was absent.",
            requested_at_s,
            ack_result=ack_result,
            messages=messages,
            progress_observed_at_s=progress_observed_at_s,
            progress_sequence=progress_sequence,
        )

    def _capture_after_negative_ack(
        self,
        target: MavlinkAddress,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        ack_result: int,
        messages: list[NativeStatusText],
    ) -> tuple[NativeStatusText, ...] | NativeAutoStartCommandResult:
        deadline_s = self._clock.now() + self._policy.negative_capture_s
        while self._clock.now() < deadline_s:
            interrupted = self._interruption(
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                messages=messages,
                accepted=False,
                ack_result=ack_result,
            )
            if interrupted is not None:
                return interrupted
            incoming = self._receive(requested_at_s, messages, ack_result=ack_result)
            if isinstance(incoming, NativeAutoStartCommandResult):
                return incoming
            if incoming is None:
                continue
            observed_at_s = self._clock.now()
            if incoming.name == "HEARTBEAT" and incoming.source == target:
                last_heartbeat_s = observed_at_s
            status = _status_text(incoming, observed_at_s=observed_at_s, target=target)
            if status is not None:
                messages.append(status)
        return tuple(messages)

    def _receive(
        self,
        requested_at_s: float,
        messages: list[NativeStatusText],
        *,
        ack_result: int | None = None,
    ) -> IncomingMessage | None | NativeAutoStartCommandResult:
        try:
            return self._link.receive(self._policy.max_poll_s)
        except ConnectionError as error:
            return self._result(
                NativeAutoStartState.LINK_LOST,
                str(error),
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )

    def _interruption(
        self,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        messages: list[NativeStatusText],
        accepted: bool,
        ack_result: int | None = None,
    ) -> NativeAutoStartCommandResult | None:
        if cancellation.is_cancelled():
            return self._result(
                NativeAutoStartState.CANCELLED,
                "Mission-start request was cancelled; onboard state is not assumed.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        if not self._link.is_connected():
            return self._result(
                NativeAutoStartState.LINK_LOST,
                "SiK link was lost during mission start; onboard behavior remains native.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        age_s = self._clock.now() - last_heartbeat_s
        if age_s < 0 or age_s > target_valid_for_s:
            state = (
                NativeAutoStartState.ACKNOWLEDGED_NO_AUTO_TELEMETRY
                if accepted
                else NativeAutoStartState.STALE_LINK
            )
            return self._result(
                state,
                "Selected-target heartbeat became stale during mission start.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        return None

    def _result(
        self,
        state: NativeAutoStartState,
        detail: str,
        requested_at_s: float,
        *,
        ack_result: int | None = None,
        messages: list[NativeStatusText] | tuple[NativeStatusText, ...] = (),
        auto_observed_at_s: float | None = None,
        progress_observed_at_s: float | None = None,
        progress_sequence: int | None = None,
    ) -> NativeAutoStartCommandResult:
        return NativeAutoStartCommandResult(
            state=state,
            detail=detail,
            requested_at_s=requested_at_s,
            completed_at_s=max(requested_at_s, self._clock.now()),
            ack_result=ack_result,
            native_messages=tuple(messages),
            auto_observed_at_s=auto_observed_at_s,
            progress_observed_at_s=progress_observed_at_s,
            progress_sequence=progress_sequence,
        )


def _status_text(
    message: IncomingMessage,
    *,
    observed_at_s: float,
    target: MavlinkAddress,
) -> NativeStatusText | None:
    if message.name != "STATUSTEXT" or message.source != target:
        return None
    text = message.fields.get("text")
    severity = _integer_or_none(message, "severity")
    if not isinstance(text, str) or not text.rstrip("\x00").strip() or severity is None:
        return None
    return NativeStatusText(
        severity=severity,
        text=text.rstrip("\x00").strip(),
        message_id=_integer_or_none(message, "id") or 0,
        chunk_sequence=_integer_or_none(message, "chunk_seq") or 0,
        observed_at_s=observed_at_s,
    )


def _integer_or_none(message: IncomingMessage, field: str) -> int | None:
    value = message.fields.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ack_addresses_local_gcs(message: IncomingMessage, local: MavlinkAddress) -> bool:
    target_system = _integer_or_none(message, "target_system")
    target_component = _integer_or_none(message, "target_component")
    return target_system in (None, 0, local.system_id) and target_component in (
        None,
        0,
        local.component_id,
    )
