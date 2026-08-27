"""Dedicated MAV_CMD_RUN_PREARM_CHECKS transaction for pinned ArduCopter 4.6.3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from skywriter.application.connected import CancellationView, ConnectedTarget
from skywriter.application.prearm import PrearmCommandResult, PrearmRequestState
from skywriter.application.telemetry import NativeStatusText, TelemetryLinkKind
from skywriter.infrastructure.mavlink.connection import (
    MAV_CMD_RUN_PREARM_CHECKS as MAV_CMD_RUN_PREARM_CHECKS,
)
from skywriter.infrastructure.mavlink.connection import (
    Clock,
    IncomingMessage,
    MavlinkAddress,
    TransportDescriptor,
    TransportKind,
)

MAV_RESULT_ACCEPTED = 0
MAV_RESULT_UNSUPPORTED = 3


class PrearmCommandLink(Protocol):
    """Closed link surface for exactly one approved command."""

    descriptor: TransportDescriptor
    local_address: MavlinkAddress

    def is_connected(self) -> bool: ...

    def receive(self, timeout_s: float) -> IncomingMessage | None: ...

    def send_prearm_checks(self, target: MavlinkAddress) -> None: ...


@dataclass(frozen=True, slots=True)
class PrearmProtocolPolicy:
    ack_timeout_s: float = 5.0
    post_ack_capture_s: float = 0.75
    max_poll_s: float = 0.5

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


class NativePrearmGateway:
    """Send only command 401 and correlate its exact target-owned acknowledgment."""

    def __init__(
        self,
        link: PrearmCommandLink,
        *,
        clock: Clock,
        policy: PrearmProtocolPolicy | None = None,
    ) -> None:
        if link.descriptor.kind is not TransportKind.SIK:
            raise ValueError("native pre-arm requests require an explicitly classified SiK link")
        self._link = link
        self._clock = clock
        self._policy = policy or PrearmProtocolPolicy()

    def request_prearm_checks(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> PrearmCommandResult:
        requested_at_s = self._clock.now()
        if target.link_kind is not TelemetryLinkKind.SIK:
            return self._result(
                PrearmRequestState.WRONG_TARGET,
                "Selected target is not on the active SiK command link.",
                requested_at_s,
            )
        if not self._link.is_connected():
            return self._result(
                PrearmRequestState.LINK_LOST,
                "SiK command link is disconnected.",
                requested_at_s,
            )
        if not target.is_fresh(requested_at_s, target_valid_for_s):
            return self._result(
                PrearmRequestState.STALE_LINK,
                "Selected-target heartbeat was stale before command transmission.",
                requested_at_s,
            )

        address = MavlinkAddress(target.system_id, target.component_id)
        try:
            self._link.send_prearm_checks(address)
        except ConnectionError as error:
            return self._result(
                PrearmRequestState.LINK_LOST,
                str(error),
                requested_at_s,
            )

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
            )
            if interrupted is not None:
                return interrupted
            try:
                message = self._link.receive(
                    min(self._policy.max_poll_s, max(0.0, deadline_s - self._clock.now()))
                )
            except ConnectionError as error:
                return self._result(
                    PrearmRequestState.LINK_LOST,
                    str(error),
                    requested_at_s,
                    messages=messages,
                )
            if message is None:
                continue
            observed_at_s = self._clock.now()
            if message.name == "HEARTBEAT" and message.source == address:
                last_heartbeat_s = observed_at_s
                continue
            status = _status_text(message, observed_at_s=observed_at_s, target=address)
            if status is not None:
                messages.append(status)
                continue
            if message.name != "COMMAND_ACK":
                continue
            command = _integer_or_none(message, "command")
            if message.source != address:
                return self._result(
                    PrearmRequestState.WRONG_TARGET,
                    "Received command acknowledgment from a different target.",
                    requested_at_s,
                    messages=messages,
                )
            if command != MAV_CMD_RUN_PREARM_CHECKS:
                return self._result(
                    PrearmRequestState.WRONG_ACK,
                    "Received an acknowledgment for a different command.",
                    requested_at_s,
                    messages=messages,
                )
            if not _ack_addresses_local_gcs(message, self._link.local_address):
                return self._result(
                    PrearmRequestState.WRONG_ACK,
                    "Command acknowledgment was addressed to a different GCS.",
                    requested_at_s,
                    messages=messages,
                )
            result = _integer_or_none(message, "result")
            if result is None or result < 0:
                return self._result(
                    PrearmRequestState.WRONG_ACK,
                    "Command acknowledgment omitted a valid result.",
                    requested_at_s,
                    messages=messages,
                )
            captured = self._capture_after_ack(
                address,
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                messages=messages,
            )
            if isinstance(captured, PrearmCommandResult):
                return captured
            if result == MAV_RESULT_ACCEPTED:
                return self._result(
                    PrearmRequestState.ACCEPTED,
                    "ArduCopter accepted and ran the native request; this is not arm approval.",
                    requested_at_s,
                    ack_result=result,
                    messages=captured,
                )
            if result == MAV_RESULT_UNSUPPORTED:
                return self._result(
                    PrearmRequestState.UNSUPPORTED,
                    "Target reported MAV_CMD_RUN_PREARM_CHECKS as unsupported.",
                    requested_at_s,
                    ack_result=result,
                    messages=captured,
                )
            return self._result(
                PrearmRequestState.REJECTED,
                f"Target rejected the native pre-arm request with MAV_RESULT {result}.",
                requested_at_s,
                ack_result=result,
                messages=captured,
            )
        return self._result(
            PrearmRequestState.TIMED_OUT,
            "No matching COMMAND_ACK arrived before the bounded deadline.",
            requested_at_s,
            messages=messages,
        )

    def _capture_after_ack(
        self,
        target: MavlinkAddress,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        messages: list[NativeStatusText],
    ) -> tuple[NativeStatusText, ...] | PrearmCommandResult:
        deadline_s = self._clock.now() + self._policy.post_ack_capture_s
        while self._clock.now() < deadline_s:
            interrupted = self._interruption(
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                messages=messages,
            )
            if interrupted is not None:
                return interrupted
            try:
                message = self._link.receive(
                    min(self._policy.max_poll_s, max(0.0, deadline_s - self._clock.now()))
                )
            except ConnectionError as error:
                return self._result(
                    PrearmRequestState.LINK_LOST,
                    str(error),
                    requested_at_s,
                    messages=messages,
                )
            if message is None:
                continue
            observed_at_s = self._clock.now()
            if message.name == "HEARTBEAT" and message.source == target:
                last_heartbeat_s = observed_at_s
            status = _status_text(message, observed_at_s=observed_at_s, target=target)
            if status is not None:
                messages.append(status)
        return tuple(messages)

    def _interruption(
        self,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        messages: list[NativeStatusText],
    ) -> PrearmCommandResult | None:
        if cancellation.is_cancelled():
            return self._result(
                PrearmRequestState.CANCELLED,
                "Native pre-arm request was cancelled.",
                requested_at_s,
                messages=messages,
            )
        if not self._link.is_connected():
            return self._result(
                PrearmRequestState.LINK_LOST,
                "SiK command link was lost while awaiting acknowledgment.",
                requested_at_s,
                messages=messages,
            )
        age_s = self._clock.now() - last_heartbeat_s
        if age_s < 0 or age_s > target_valid_for_s:
            return self._result(
                PrearmRequestState.STALE_LINK,
                "Selected-target heartbeat became stale while awaiting acknowledgment.",
                requested_at_s,
                messages=messages,
            )
        return None

    def _result(
        self,
        state: PrearmRequestState,
        detail: str,
        requested_at_s: float,
        *,
        ack_result: int | None = None,
        messages: list[NativeStatusText] | tuple[NativeStatusText, ...] = (),
    ) -> PrearmCommandResult:
        return PrearmCommandResult(
            state=state,
            detail=detail,
            requested_at_s=requested_at_s,
            completed_at_s=max(requested_at_s, self._clock.now()),
            ack_result=ack_result,
            native_messages=tuple(messages),
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
