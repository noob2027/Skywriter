"""Dedicated acknowledged native Land Here Now transaction."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from skywriter.application.connected import CancellationView, ConnectedTarget
from skywriter.application.land_here_now import (
    MAV_LANDED_STATE_IN_AIR,
    NativeLandHereNowCommandResult,
    NativeLandHereNowState,
)
from skywriter.application.pause_resume import (
    ARDUCOPTER_LAND_MODE,
    MAV_LANDED_STATE_LANDING,
    MAV_LANDED_STATE_ON_GROUND,
)
from skywriter.application.telemetry import NativeStatusText, TelemetryLinkKind
from skywriter.infrastructure.mavlink.connection import (
    MAV_CMD_NAV_LAND,
    MAV_MODE_FLAG_SAFETY_ARMED,
    Clock,
    IncomingMessage,
    MavlinkAddress,
    TransportDescriptor,
    TransportKind,
)

MAV_RESULT_ACCEPTED = 0
MAV_RESULT_UNSUPPORTED = 3


class NativeLandHereNowLink(Protocol):
    """Closed surface for fixed native Land and read-only landing-state proof."""

    descriptor: TransportDescriptor
    local_address: MavlinkAddress

    def is_connected(self) -> bool: ...

    def receive(self, timeout_s: float) -> IncomingMessage | None: ...

    def send_native_land_here_now(self, target: MavlinkAddress) -> None: ...

    def request_native_landing_state(self, target: MavlinkAddress) -> None: ...


@dataclass(frozen=True, slots=True)
class NativeLandHereNowProtocolPolicy:
    ack_timeout_s: float = 5.0
    telemetry_timeout_s: float = 15.0
    telemetry_request_interval_s: float = 1.0
    negative_capture_s: float = 0.75
    max_poll_s: float = 0.5

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")


class NativeLandHereNowGateway:
    """Send only fixed native Land and require post-ACK landing telemetry."""

    def __init__(
        self,
        link: NativeLandHereNowLink,
        *,
        clock: Clock,
        policy: NativeLandHereNowProtocolPolicy | None = None,
    ) -> None:
        if link.descriptor.kind is not TransportKind.SIK:
            raise ValueError("native Land Here Now requires an explicitly classified SiK link")
        self._link = link
        self._clock = clock
        self._policy = policy or NativeLandHereNowProtocolPolicy()

    def request_native_land_here_now(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativeLandHereNowCommandResult:
        requested_at_s = self._clock.now()
        if target.link_kind is not TelemetryLinkKind.SIK:
            return self._result(
                NativeLandHereNowState.WRONG_TARGET,
                "Selected target is not on the active SiK command link.",
                requested_at_s,
            )
        if not target.armed:
            return self._result(
                NativeLandHereNowState.DISARMED,
                "Selected target was disarmed before transmission; no request was sent.",
                requested_at_s,
            )
        if not self._link.is_connected():
            return self._result(
                NativeLandHereNowState.LINK_LOST,
                "SiK command link is disconnected.",
                requested_at_s,
            )
        if not target.is_fresh(requested_at_s, target_valid_for_s):
            return self._result(
                NativeLandHereNowState.STALE_LINK,
                "Selected-target heartbeat was stale before native Land transmission.",
                requested_at_s,
            )

        address = MavlinkAddress(target.system_id, target.component_id)
        try:
            self._link.send_native_land_here_now(address)
        except ConnectionError as error:
            return self._result(NativeLandHereNowState.LINK_LOST, str(error), requested_at_s)

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
            if isinstance(incoming, NativeLandHereNowCommandResult):
                return incoming
            if incoming is None:
                continue
            observed_at_s = self._clock.now()
            if incoming.name == "HEARTBEAT" and incoming.source == address:
                last_heartbeat_s = observed_at_s
                base_mode = _integer_or_none(incoming, "base_mode")
                if base_mode is not None and not base_mode & MAV_MODE_FLAG_SAFETY_ARMED:
                    return self._result(
                        NativeLandHereNowState.DISARMED,
                        "Vehicle disarmed before the native Land acknowledgment.",
                        requested_at_s,
                        messages=messages,
                    )
                continue
            status = _status_text(incoming, observed_at_s=observed_at_s, target=address)
            if status is not None:
                messages.append(status)
                continue
            if incoming.name != "COMMAND_ACK":
                continue
            if incoming.source != address:
                return self._result(
                    NativeLandHereNowState.WRONG_TARGET,
                    "Received native Land acknowledgment from a different target.",
                    requested_at_s,
                    messages=messages,
                )
            if _integer_or_none(incoming, "command") != MAV_CMD_NAV_LAND:
                return self._result(
                    NativeLandHereNowState.WRONG_ACK,
                    "Received an acknowledgment for a different command.",
                    requested_at_s,
                    messages=messages,
                )
            if not _ack_addresses_local_gcs(incoming, self._link.local_address):
                return self._result(
                    NativeLandHereNowState.WRONG_ACK,
                    "Native Land acknowledgment addressed a different GCS.",
                    requested_at_s,
                    messages=messages,
                )
            result = _integer_or_none(incoming, "result")
            if result is None or result < 0:
                return self._result(
                    NativeLandHereNowState.WRONG_ACK,
                    "Native Land acknowledgment omitted a valid result.",
                    requested_at_s,
                    messages=messages,
                )
            if result == MAV_RESULT_ACCEPTED:
                return self._await_landing_state(
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
            if isinstance(captured, NativeLandHereNowCommandResult):
                return captured
            messages = list(captured)
            if result == MAV_RESULT_UNSUPPORTED:
                return self._result(
                    NativeLandHereNowState.UNSUPPORTED,
                    "Target reported native Land as unsupported.",
                    requested_at_s,
                    ack_result=result,
                    messages=messages,
                )
            return self._result(
                NativeLandHereNowState.REJECTED,
                f"ArduCopter rejected native Land with MAV_RESULT {result}.",
                requested_at_s,
                ack_result=result,
                messages=messages,
            )
        return self._result(
            NativeLandHereNowState.TIMED_OUT,
            "No matching native Land acknowledgment arrived before the deadline.",
            requested_at_s,
            messages=messages,
        )

    def _await_landing_state(
        self,
        target: MavlinkAddress,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        ack_result: int,
        messages: list[NativeStatusText],
    ) -> NativeLandHereNowCommandResult:
        deadline_s = self._clock.now() + self._policy.telemetry_timeout_s
        next_state_request_s = self._clock.now()
        land_mode_observed_at_s: float | None = None
        landed_state_observed_at_s: float | None = None
        landed_state: int | None = None
        observed_opposite = False
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
            if self._clock.now() >= next_state_request_s:
                try:
                    self._link.request_native_landing_state(target)
                except ConnectionError as error:
                    return self._result(
                        NativeLandHereNowState.LINK_LOST,
                        str(error),
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                next_state_request_s = self._clock.now() + self._policy.telemetry_request_interval_s
            incoming = self._receive(requested_at_s, messages, ack_result=ack_result)
            if isinstance(incoming, NativeLandHereNowCommandResult):
                return incoming
            if incoming is None:
                continue
            observed_at_s = self._clock.now()
            if incoming.name == "HEARTBEAT" and incoming.source == target:
                last_heartbeat_s = observed_at_s
                base_mode = _integer_or_none(incoming, "base_mode")
                custom_mode = _integer_or_none(incoming, "custom_mode")
                if base_mode is not None and not base_mode & MAV_MODE_FLAG_SAFETY_ARMED:
                    if landed_state == MAV_LANDED_STATE_ON_GROUND:
                        return self._result(
                            NativeLandHereNowState.LANDED,
                            "Native Land acknowledged; On Ground telemetry confirmed.",
                            requested_at_s,
                            ack_result=ack_result,
                            messages=messages,
                            land_mode_observed_at_s=land_mode_observed_at_s,
                            landed_state_observed_at_s=landed_state_observed_at_s,
                            landed_state=landed_state,
                        )
                    return self._result(
                        NativeLandHereNowState.DISARMED,
                        "Native Land was acknowledged, but the vehicle disarmed before proof.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                if custom_mode == ARDUCOPTER_LAND_MODE:
                    land_mode_observed_at_s = observed_at_s
                    if landed_state == MAV_LANDED_STATE_LANDING:
                        return self._landing_result(
                            requested_at_s,
                            ack_result,
                            messages,
                            land_mode_observed_at_s,
                            landed_state_observed_at_s,
                        )
                elif custom_mode is not None:
                    observed_opposite = True
                continue
            if incoming.name == "EXTENDED_SYS_STATE" and incoming.source == target:
                value = _integer_or_none(incoming, "landed_state")
                if value is None:
                    continue
                landed_state = value
                landed_state_observed_at_s = observed_at_s
                if value == MAV_LANDED_STATE_ON_GROUND:
                    return self._result(
                        NativeLandHereNowState.LANDED,
                        "Native Land acknowledged; On Ground telemetry confirmed.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                        land_mode_observed_at_s=land_mode_observed_at_s,
                        landed_state_observed_at_s=landed_state_observed_at_s,
                        landed_state=value,
                    )
                if value == MAV_LANDED_STATE_LANDING and land_mode_observed_at_s is not None:
                    return self._landing_result(
                        requested_at_s,
                        ack_result,
                        messages,
                        land_mode_observed_at_s,
                        landed_state_observed_at_s,
                    )
                if value == MAV_LANDED_STATE_IN_AIR:
                    observed_opposite = True
                continue
            status = _status_text(incoming, observed_at_s=observed_at_s, target=target)
            if status is not None:
                messages.append(status)
        if observed_opposite:
            return self._result(
                NativeLandHereNowState.TELEMETRY_DISAGREEMENT,
                "Native Land was acknowledged, but later telemetry did not confirm Landing.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
                land_mode_observed_at_s=land_mode_observed_at_s,
                landed_state_observed_at_s=landed_state_observed_at_s,
                landed_state=landed_state,
            )
        return self._result(
            NativeLandHereNowState.ACKNOWLEDGED_NO_LANDING_TELEMETRY,
            "Native Land was acknowledged, but resulting landing telemetry was absent.",
            requested_at_s,
            ack_result=ack_result,
            messages=messages,
        )

    def _landing_result(
        self,
        requested_at_s: float,
        ack_result: int,
        messages: list[NativeStatusText],
        land_mode_observed_at_s: float,
        landed_state_observed_at_s: float | None,
    ) -> NativeLandHereNowCommandResult:
        assert landed_state_observed_at_s is not None
        return self._result(
            NativeLandHereNowState.LANDING,
            "Native Land acknowledged; Land mode and Landing state telemetry confirmed.",
            requested_at_s,
            ack_result=ack_result,
            messages=messages,
            land_mode_observed_at_s=land_mode_observed_at_s,
            landed_state_observed_at_s=landed_state_observed_at_s,
            landed_state=MAV_LANDED_STATE_LANDING,
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
    ) -> tuple[NativeStatusText, ...] | NativeLandHereNowCommandResult:
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
            if isinstance(incoming, NativeLandHereNowCommandResult):
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
    ) -> IncomingMessage | None | NativeLandHereNowCommandResult:
        try:
            return self._link.receive(self._policy.max_poll_s)
        except ConnectionError as error:
            return self._result(
                NativeLandHereNowState.LINK_LOST,
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
    ) -> NativeLandHereNowCommandResult | None:
        if cancellation.is_cancelled():
            return self._result(
                NativeLandHereNowState.CANCELLED,
                "Native Land request was cancelled; onboard state is not assumed.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        if not self._link.is_connected():
            return self._result(
                NativeLandHereNowState.LINK_LOST,
                "SiK link was lost; onboard behavior remains native.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        age_s = self._clock.now() - last_heartbeat_s
        if age_s < 0 or age_s > target_valid_for_s:
            state = (
                NativeLandHereNowState.ACKNOWLEDGED_NO_LANDING_TELEMETRY
                if accepted
                else NativeLandHereNowState.STALE_LINK
            )
            return self._result(
                state,
                "Selected-target heartbeat became stale during native Land.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        return None

    def _result(
        self,
        state: NativeLandHereNowState,
        detail: str,
        requested_at_s: float,
        *,
        ack_result: int | None = None,
        messages: list[NativeStatusText] | tuple[NativeStatusText, ...] = (),
        land_mode_observed_at_s: float | None = None,
        landed_state_observed_at_s: float | None = None,
        landed_state: int | None = None,
    ) -> NativeLandHereNowCommandResult:
        return NativeLandHereNowCommandResult(
            state=state,
            detail=detail,
            requested_at_s=requested_at_s,
            completed_at_s=max(requested_at_s, self._clock.now()),
            ack_result=ack_result,
            native_messages=tuple(messages),
            land_mode_observed_at_s=land_mode_observed_at_s,
            landed_state_observed_at_s=landed_state_observed_at_s,
            landed_state=landed_state,
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


def _ack_addresses_local_gcs(message: IncomingMessage, local: MavlinkAddress) -> bool:
    target_system = _integer_or_none(message, "target_system")
    target_component = _integer_or_none(message, "target_component")
    return target_system in (None, 0, local.system_id) and target_component in (
        None,
        0,
        local.component_id,
    )


def _integer_or_none(message: IncomingMessage, field: str) -> int | None:
    value = message.fields.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
