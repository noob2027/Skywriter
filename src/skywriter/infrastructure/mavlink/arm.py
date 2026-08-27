"""Dedicated normal-arm transaction for pinned stock ArduCopter 4.6.3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from skywriter.application.arm import NormalArmCommandResult, NormalArmState
from skywriter.application.connected import CancellationView, ConnectedTarget
from skywriter.application.telemetry import NativeStatusText, TelemetryLinkKind
from skywriter.infrastructure.mavlink.connection import (
    MAV_CMD_COMPONENT_ARM_DISARM as MAV_CMD_COMPONENT_ARM_DISARM,
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


class NormalArmLink(Protocol):
    """Closed link surface for exactly one normal Arm action."""

    descriptor: TransportDescriptor
    local_address: MavlinkAddress

    def is_connected(self) -> bool: ...

    def receive(self, timeout_s: float) -> IncomingMessage | None: ...

    def send_normal_arm(self, target: MavlinkAddress) -> None: ...


@dataclass(frozen=True, slots=True)
class NormalArmProtocolPolicy:
    ack_timeout_s: float = 5.0
    telemetry_timeout_s: float = 5.0
    negative_capture_s: float = 0.75
    max_poll_s: float = 0.5

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


class NativeNormalArmGateway:
    """Send only the normal command and require a later armed heartbeat."""

    def __init__(
        self,
        link: NormalArmLink,
        *,
        clock: Clock,
        policy: NormalArmProtocolPolicy | None = None,
    ) -> None:
        if link.descriptor.kind is not TransportKind.SIK:
            raise ValueError("normal Arm requires an explicitly classified SiK link")
        self._link = link
        self._clock = clock
        self._policy = policy or NormalArmProtocolPolicy()

    def request_normal_arm(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NormalArmCommandResult:
        requested_at_s = self._clock.now()
        if target.link_kind is not TelemetryLinkKind.SIK:
            return self._result(
                NormalArmState.WRONG_TARGET,
                "Selected target is not on the active SiK command link.",
                requested_at_s,
            )
        if target.armed:
            return self._result(
                NormalArmState.TELEMETRY_DISAGREEMENT,
                "Selected target was already armed before transmission; no request was sent.",
                requested_at_s,
            )
        if not self._link.is_connected():
            return self._result(
                NormalArmState.LINK_LOST,
                "SiK command link is disconnected.",
                requested_at_s,
            )
        if not target.is_fresh(requested_at_s, target_valid_for_s):
            return self._result(
                NormalArmState.STALE_LINK,
                "Selected-target heartbeat was stale before command transmission.",
                requested_at_s,
            )

        address = MavlinkAddress(target.system_id, target.component_id)
        try:
            self._link.send_normal_arm(address)
        except ConnectionError as error:
            return self._result(NormalArmState.LINK_LOST, str(error), requested_at_s)

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
            if isinstance(incoming, NormalArmCommandResult):
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
                    NormalArmState.WRONG_TARGET,
                    "Received command acknowledgment from a different target.",
                    requested_at_s,
                    messages=messages,
                )
            if _integer_or_none(incoming, "command") != MAV_CMD_COMPONENT_ARM_DISARM:
                return self._result(
                    NormalArmState.WRONG_ACK,
                    "Received an acknowledgment for a different command.",
                    requested_at_s,
                    messages=messages,
                )
            if not _ack_addresses_local_gcs(incoming, self._link.local_address):
                return self._result(
                    NormalArmState.WRONG_ACK,
                    "Command acknowledgment was addressed to a different GCS.",
                    requested_at_s,
                    messages=messages,
                )
            result = _integer_or_none(incoming, "result")
            if result is None or result < 0:
                return self._result(
                    NormalArmState.WRONG_ACK,
                    "Command acknowledgment omitted a valid result.",
                    requested_at_s,
                    messages=messages,
                )
            if result == MAV_RESULT_ACCEPTED:
                return self._await_armed_telemetry(
                    address,
                    cancellation,
                    requested_at_s=requested_at_s,
                    last_heartbeat_s=last_heartbeat_s,
                    target_valid_for_s=target_valid_for_s,
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
            if isinstance(captured, NormalArmCommandResult):
                return captured
            messages = list(captured)
            if result == MAV_RESULT_UNSUPPORTED:
                return self._result(
                    NormalArmState.UNSUPPORTED,
                    "Target reported normal Arm as unsupported.",
                    requested_at_s,
                    ack_result=result,
                    messages=messages,
                )
            return self._result(
                NormalArmState.REJECTED,
                f"ArduCopter rejected normal Arm with MAV_RESULT {result}.",
                requested_at_s,
                ack_result=result,
                messages=messages,
            )
        return self._result(
            NormalArmState.TIMED_OUT,
            "No matching normal Arm acknowledgment arrived before the bounded deadline.",
            requested_at_s,
            messages=messages,
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
    ) -> tuple[NativeStatusText, ...] | NormalArmCommandResult:
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
            if isinstance(incoming, NormalArmCommandResult):
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

    def _await_armed_telemetry(
        self,
        target: MavlinkAddress,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        ack_result: int,
        messages: list[NativeStatusText],
    ) -> NormalArmCommandResult:
        deadline_s = self._clock.now() + self._policy.telemetry_timeout_s
        disarmed_observed = False
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
            if isinstance(incoming, NormalArmCommandResult):
                return incoming
            if incoming is None:
                continue
            observed_at_s = self._clock.now()
            if incoming.name == "HEARTBEAT" and incoming.source == target:
                last_heartbeat_s = observed_at_s
                base_mode = _integer_or_none(incoming, "base_mode")
                if base_mode is None:
                    continue
                if base_mode & MAV_MODE_FLAG_SAFETY_ARMED:
                    return self._result(
                        NormalArmState.ARMED,
                        "Normal Arm acknowledged and confirmed by fresh selected-target telemetry.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                        armed_observed_at_s=observed_at_s,
                    )
                disarmed_observed = True
                continue
            status = _status_text(incoming, observed_at_s=observed_at_s, target=target)
            if status is not None:
                messages.append(status)
        if disarmed_observed:
            return self._result(
                NormalArmState.TELEMETRY_DISAGREEMENT,
                "Arm was acknowledged, but fresh selected-target telemetry remained disarmed.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        return self._result(
            NormalArmState.ACKNOWLEDGED_NO_ARMED_TELEMETRY,
            "Arm was acknowledged, but no fresh selected-target armed telemetry arrived.",
            requested_at_s,
            ack_result=ack_result,
            messages=messages,
        )

    def _receive(
        self,
        requested_at_s: float,
        messages: list[NativeStatusText],
        *,
        ack_result: int | None = None,
    ) -> IncomingMessage | None | NormalArmCommandResult:
        try:
            return self._link.receive(self._policy.max_poll_s)
        except ConnectionError as error:
            return self._result(
                NormalArmState.LINK_LOST,
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
    ) -> NormalArmCommandResult | None:
        if cancellation.is_cancelled():
            return self._result(
                NormalArmState.CANCELLED,
                "Normal Arm request was cancelled; vehicle state is not assumed.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        if not self._link.is_connected():
            return self._result(
                NormalArmState.LINK_LOST,
                "SiK link was lost during normal Arm; vehicle state is uncertain.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        age_s = self._clock.now() - last_heartbeat_s
        if age_s < 0 or age_s > target_valid_for_s:
            if accepted:
                return self._result(
                    NormalArmState.ACKNOWLEDGED_NO_ARMED_TELEMETRY,
                    "Arm was acknowledged, then selected-target telemetry became stale.",
                    requested_at_s,
                    ack_result=ack_result,
                    messages=messages,
                )
            return self._result(
                NormalArmState.STALE_LINK,
                "Selected-target heartbeat became stale before acknowledgment.",
                requested_at_s,
                messages=messages,
            )
        return None

    def _result(
        self,
        state: NormalArmState,
        detail: str,
        requested_at_s: float,
        *,
        ack_result: int | None = None,
        messages: list[NativeStatusText] | tuple[NativeStatusText, ...] = (),
        armed_observed_at_s: float | None = None,
    ) -> NormalArmCommandResult:
        return NormalArmCommandResult(
            state=state,
            detail=detail,
            requested_at_s=requested_at_s,
            completed_at_s=max(requested_at_s, self._clock.now()),
            ack_result=ack_result,
            native_messages=tuple(messages),
            armed_observed_at_s=armed_observed_at_s,
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
