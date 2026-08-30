"""Dedicated native Pause/Resume transaction for stock ArduCopter 4.6.3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from skywriter.application.connected import CancellationView, ConnectedTarget
from skywriter.application.pause_resume import (
    ARDUCOPTER_AUTO_MODE,
    ARDUCOPTER_LAND_MODE,
    MAV_LANDED_STATE_LANDING,
    MAV_LANDED_STATE_ON_GROUND,
    MAV_MISSION_STATE_ACTIVE,
    MAV_MISSION_STATE_COMPLETE,
    MAV_MISSION_STATE_PAUSED,
    NativePauseResumeAction,
    NativePauseResumeCommandResult,
    NativePauseResumeState,
)
from skywriter.application.telemetry import NativeStatusText, TelemetryLinkKind
from skywriter.infrastructure.mavlink.connection import (
    MAV_CMD_DO_PAUSE_CONTINUE as MAV_CMD_DO_PAUSE_CONTINUE,
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


class NativePauseResumeLink(Protocol):
    """Closed link surface for two actions and their fixed read-only state proof."""

    descriptor: TransportDescriptor
    local_address: MavlinkAddress

    def is_connected(self) -> bool: ...

    def receive(self, timeout_s: float) -> IncomingMessage | None: ...

    def send_native_pause(self, target: MavlinkAddress) -> None: ...

    def send_native_resume(self, target: MavlinkAddress) -> None: ...

    def request_native_mission_state(self, target: MavlinkAddress) -> None: ...


@dataclass(frozen=True, slots=True)
class NativePauseResumeProtocolPolicy:
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


class NativePauseResumeGateway:
    """Send only fixed Pause/Resume selectors and require later mission-state proof."""

    def __init__(
        self,
        link: NativePauseResumeLink,
        *,
        clock: Clock,
        policy: NativePauseResumeProtocolPolicy | None = None,
    ) -> None:
        if link.descriptor.kind is not TransportKind.SIK:
            raise ValueError("native Pause/Resume requires an explicitly classified SiK link")
        self._link = link
        self._clock = clock
        self._policy = policy or NativePauseResumeProtocolPolicy()

    def request_native_pause(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativePauseResumeCommandResult:
        return self._request(
            NativePauseResumeAction.PAUSE,
            target,
            expected_item_count=expected_item_count,
            target_valid_for_s=target_valid_for_s,
            cancellation=cancellation,
        )

    def request_native_resume(
        self,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativePauseResumeCommandResult:
        return self._request(
            NativePauseResumeAction.RESUME,
            target,
            expected_item_count=expected_item_count,
            target_valid_for_s=target_valid_for_s,
            cancellation=cancellation,
        )

    def _request(
        self,
        action: NativePauseResumeAction,
        target: ConnectedTarget,
        *,
        expected_item_count: int,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NativePauseResumeCommandResult:
        requested_at_s = self._clock.now()
        if expected_item_count < 3:
            return self._result(
                action,
                NativePauseResumeState.MISSION_MISMATCH,
                "Verified native mission is too short for flight controls.",
                requested_at_s,
            )
        if target.link_kind is not TelemetryLinkKind.SIK:
            return self._result(
                action,
                NativePauseResumeState.WRONG_TARGET,
                "Selected target is not on the active SiK command link.",
                requested_at_s,
            )
        if not target.armed:
            return self._result(
                action,
                NativePauseResumeState.DISARMED,
                "Selected target was disarmed before transmission; no request was sent.",
                requested_at_s,
            )
        if not self._link.is_connected():
            return self._result(
                action,
                NativePauseResumeState.LINK_LOST,
                "SiK command link is disconnected.",
                requested_at_s,
            )
        if not target.is_fresh(requested_at_s, target_valid_for_s):
            return self._result(
                action,
                NativePauseResumeState.STALE_LINK,
                "Selected-target heartbeat was stale before command transmission.",
                requested_at_s,
            )

        address = MavlinkAddress(target.system_id, target.component_id)
        try:
            if action is NativePauseResumeAction.PAUSE:
                self._link.send_native_pause(address)
            else:
                self._link.send_native_resume(address)
        except ConnectionError as error:
            return self._result(
                action,
                NativePauseResumeState.LINK_LOST,
                str(error),
                requested_at_s,
            )

        deadline_s = requested_at_s + self._policy.ack_timeout_s
        last_heartbeat_s = target.observed_at_s
        messages: list[NativeStatusText] = []
        while self._clock.now() < deadline_s:
            interrupted = self._interruption(
                action,
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                messages=messages,
                accepted=False,
            )
            if interrupted is not None:
                return interrupted
            incoming = self._receive(action, requested_at_s, messages)
            if isinstance(incoming, NativePauseResumeCommandResult):
                return incoming
            if incoming is None:
                continue
            observed_at_s = self._clock.now()
            if incoming.name == "HEARTBEAT" and incoming.source == address:
                last_heartbeat_s = observed_at_s
                base_mode = _integer_or_none(incoming, "base_mode")
                custom_mode = _integer_or_none(incoming, "custom_mode")
                if base_mode is not None and not base_mode & MAV_MODE_FLAG_SAFETY_ARMED:
                    return self._result(
                        action,
                        NativePauseResumeState.DISARMED,
                        f"Vehicle disarmed before the native {action.value} acknowledgment.",
                        requested_at_s,
                        messages=messages,
                    )
                if custom_mode == ARDUCOPTER_LAND_MODE:
                    return self._result(
                        action,
                        NativePauseResumeState.LANDING,
                        f"Vehicle entered Land before the native {action.value} acknowledgment.",
                        requested_at_s,
                        messages=messages,
                    )
                if custom_mode is not None and custom_mode != ARDUCOPTER_AUTO_MODE:
                    return self._result(
                        action,
                        NativePauseResumeState.UNEXPECTED_MODE,
                        f"Vehicle left AUTO before the native {action.value} acknowledgment.",
                        requested_at_s,
                        messages=messages,
                    )
                continue
            if incoming.name == "EXTENDED_SYS_STATE" and incoming.source == address:
                landed_state = _integer_or_none(incoming, "landed_state")
                if landed_state == MAV_LANDED_STATE_LANDING:
                    return self._result(
                        action,
                        NativePauseResumeState.LANDING,
                        "Vehicle began Landing before the Pause/Resume acknowledgment.",
                        requested_at_s,
                        messages=messages,
                    )
                if landed_state == MAV_LANDED_STATE_ON_GROUND:
                    return self._result(
                        action,
                        NativePauseResumeState.DISARMED,
                        "Vehicle reported On Ground before the Pause/Resume acknowledgment.",
                        requested_at_s,
                        messages=messages,
                    )
                continue
            if incoming.name == "MISSION_CURRENT" and incoming.source == address:
                sequence = _integer_or_none(incoming, "seq")
                total = _integer_or_none(incoming, "total")
                mission_state = _integer_or_none(incoming, "mission_state")
                if (
                    sequence is None
                    or not 1 <= sequence < expected_item_count
                    # Pinned ArduCopter excludes native sequence-zero Home from
                    # MISSION_CURRENT.total.
                    or (total is not None and total != expected_item_count - 1)
                ):
                    return self._result(
                        action,
                        NativePauseResumeState.MISSION_MISMATCH,
                        "Mission state changed outside exact verified bounds before ACK.",
                        requested_at_s,
                        messages=messages,
                    )
                if mission_state == MAV_MISSION_STATE_COMPLETE:
                    return self._result(
                        action,
                        NativePauseResumeState.MISSION_COMPLETED,
                        "Native mission completed before the Pause/Resume acknowledgment.",
                        requested_at_s,
                        messages=messages,
                        state_observed_at_s=observed_at_s,
                        progress_sequence=sequence,
                    )
                if sequence == expected_item_count - 1:
                    return self._result(
                        action,
                        NativePauseResumeState.LANDING,
                        "Native mission reached its final Land item before acknowledgment.",
                        requested_at_s,
                        messages=messages,
                        state_observed_at_s=observed_at_s,
                        progress_sequence=sequence,
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
                    action,
                    NativePauseResumeState.WRONG_TARGET,
                    f"Received {action.value} acknowledgment from a different target.",
                    requested_at_s,
                    messages=messages,
                )
            if _integer_or_none(incoming, "command") != MAV_CMD_DO_PAUSE_CONTINUE:
                return self._result(
                    action,
                    NativePauseResumeState.WRONG_ACK,
                    "Received an acknowledgment for a different command.",
                    requested_at_s,
                    messages=messages,
                )
            if not _ack_addresses_local_gcs(incoming, self._link.local_address):
                return self._result(
                    action,
                    NativePauseResumeState.WRONG_ACK,
                    f"{action.value.title()} acknowledgment addressed a different GCS.",
                    requested_at_s,
                    messages=messages,
                )
            result = _integer_or_none(incoming, "result")
            if result is None or result < 0:
                return self._result(
                    action,
                    NativePauseResumeState.WRONG_ACK,
                    f"{action.value.title()} acknowledgment omitted a valid result.",
                    requested_at_s,
                    messages=messages,
                )
            if result == MAV_RESULT_ACCEPTED:
                return self._await_resulting_state(
                    action,
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
                action,
                address,
                cancellation,
                requested_at_s=requested_at_s,
                last_heartbeat_s=last_heartbeat_s,
                target_valid_for_s=target_valid_for_s,
                ack_result=result,
                messages=messages,
            )
            if isinstance(captured, NativePauseResumeCommandResult):
                return captured
            messages = list(captured)
            if result == MAV_RESULT_UNSUPPORTED:
                return self._result(
                    action,
                    NativePauseResumeState.UNSUPPORTED,
                    f"Target reported native {action.value} as unsupported.",
                    requested_at_s,
                    ack_result=result,
                    messages=messages,
                )
            return self._result(
                action,
                NativePauseResumeState.REJECTED,
                f"ArduCopter rejected native {action.value} with MAV_RESULT {result}.",
                requested_at_s,
                ack_result=result,
                messages=messages,
            )
        return self._result(
            action,
            NativePauseResumeState.TIMED_OUT,
            f"No matching native {action.value} acknowledgment arrived before the deadline.",
            requested_at_s,
            messages=messages,
        )

    def _await_resulting_state(
        self,
        action: NativePauseResumeAction,
        target: MavlinkAddress,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        expected_item_count: int,
        ack_result: int,
        messages: list[NativeStatusText],
    ) -> NativePauseResumeCommandResult:
        deadline_s = self._clock.now() + self._policy.telemetry_timeout_s
        desired_mission_state = {
            NativePauseResumeAction.PAUSE: MAV_MISSION_STATE_PAUSED,
            NativePauseResumeAction.RESUME: MAV_MISSION_STATE_ACTIVE,
        }[action]
        successful_state = {
            NativePauseResumeAction.PAUSE: NativePauseResumeState.PAUSED,
            NativePauseResumeAction.RESUME: NativePauseResumeState.RUNNING,
        }[action]
        observed_opposite = False
        next_state_request_s = self._clock.now()
        while self._clock.now() < deadline_s:
            interrupted = self._interruption(
                action,
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
                    self._link.request_native_mission_state(target)
                except ConnectionError as error:
                    return self._result(
                        action,
                        NativePauseResumeState.LINK_LOST,
                        str(error),
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                next_state_request_s = self._clock.now() + self._policy.telemetry_request_interval_s
            incoming = self._receive(action, requested_at_s, messages, ack_result=ack_result)
            if isinstance(incoming, NativePauseResumeCommandResult):
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
                        action,
                        NativePauseResumeState.DISARMED,
                        f"Native {action.value} was acknowledged, but telemetry disarmed.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                if custom_mode == ARDUCOPTER_LAND_MODE:
                    return self._result(
                        action,
                        NativePauseResumeState.LANDING,
                        f"Native {action.value} was acknowledged, but vehicle entered Land.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                if custom_mode != ARDUCOPTER_AUTO_MODE:
                    return self._result(
                        action,
                        NativePauseResumeState.UNEXPECTED_MODE,
                        f"Native {action.value} was acknowledged, but vehicle left AUTO.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                continue
            if incoming.name == "EXTENDED_SYS_STATE" and incoming.source == target:
                landed_state = _integer_or_none(incoming, "landed_state")
                if landed_state == MAV_LANDED_STATE_LANDING:
                    return self._result(
                        action,
                        NativePauseResumeState.LANDING,
                        "Vehicle telemetry reports Landing during Pause/Resume.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                if landed_state == MAV_LANDED_STATE_ON_GROUND:
                    return self._result(
                        action,
                        NativePauseResumeState.DISARMED,
                        "Vehicle telemetry reports On Ground during Pause/Resume.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                continue
            if incoming.name == "MISSION_CURRENT" and incoming.source == target:
                sequence = _integer_or_none(incoming, "seq")
                total = _integer_or_none(incoming, "total")
                mission_state = _integer_or_none(incoming, "mission_state")
                if (
                    sequence is None
                    or not 1 <= sequence < expected_item_count
                    # Pinned ArduCopter excludes native sequence-zero Home from
                    # MISSION_CURRENT.total.
                    or (total is not None and total != expected_item_count - 1)
                ):
                    return self._result(
                        action,
                        NativePauseResumeState.MISSION_MISMATCH,
                        "Mission-state telemetry was outside exact verified bounds.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                    )
                if mission_state == MAV_MISSION_STATE_COMPLETE:
                    return self._result(
                        action,
                        NativePauseResumeState.MISSION_COMPLETED,
                        "Native mission completed during Pause/Resume confirmation.",
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                        state_observed_at_s=observed_at_s,
                        progress_sequence=sequence,
                    )
                if mission_state == desired_mission_state:
                    return self._result(
                        action,
                        successful_state,
                        (
                            "Native Pause acknowledged; pinned Paused telemetry confirmed."
                            if action is NativePauseResumeAction.PAUSE
                            else "Native Resume acknowledged; pinned Active telemetry confirmed."
                        ),
                        requested_at_s,
                        ack_result=ack_result,
                        messages=messages,
                        state_observed_at_s=observed_at_s,
                        progress_sequence=sequence,
                    )
                if mission_state in (MAV_MISSION_STATE_ACTIVE, MAV_MISSION_STATE_PAUSED):
                    observed_opposite = True
                continue
            status = _status_text(incoming, observed_at_s=observed_at_s, target=target)
            if status is not None:
                messages.append(status)
        if observed_opposite:
            return self._result(
                action,
                NativePauseResumeState.TELEMETRY_DISAGREEMENT,
                f"Native {action.value} was acknowledged, but mission state did not change.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        state = {
            NativePauseResumeAction.PAUSE: (
                NativePauseResumeState.ACKNOWLEDGED_NO_PAUSED_TELEMETRY
            ),
            NativePauseResumeAction.RESUME: (
                NativePauseResumeState.ACKNOWLEDGED_NO_RUNNING_TELEMETRY
            ),
        }[action]
        return self._result(
            action,
            state,
            f"Native {action.value} was acknowledged, but resulting telemetry was absent.",
            requested_at_s,
            ack_result=ack_result,
            messages=messages,
        )

    def _capture_after_negative_ack(
        self,
        action: NativePauseResumeAction,
        target: MavlinkAddress,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        ack_result: int,
        messages: list[NativeStatusText],
    ) -> tuple[NativeStatusText, ...] | NativePauseResumeCommandResult:
        deadline_s = self._clock.now() + self._policy.negative_capture_s
        while self._clock.now() < deadline_s:
            interrupted = self._interruption(
                action,
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
            incoming = self._receive(action, requested_at_s, messages, ack_result=ack_result)
            if isinstance(incoming, NativePauseResumeCommandResult):
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
        action: NativePauseResumeAction,
        requested_at_s: float,
        messages: list[NativeStatusText],
        *,
        ack_result: int | None = None,
    ) -> IncomingMessage | None | NativePauseResumeCommandResult:
        try:
            return self._link.receive(self._policy.max_poll_s)
        except ConnectionError as error:
            return self._result(
                action,
                NativePauseResumeState.LINK_LOST,
                str(error),
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )

    def _interruption(
        self,
        action: NativePauseResumeAction,
        cancellation: CancellationView,
        *,
        requested_at_s: float,
        last_heartbeat_s: float,
        target_valid_for_s: float,
        messages: list[NativeStatusText],
        accepted: bool,
        ack_result: int | None = None,
    ) -> NativePauseResumeCommandResult | None:
        if cancellation.is_cancelled():
            return self._result(
                action,
                NativePauseResumeState.CANCELLED,
                f"Native {action.value} was cancelled; onboard state is not assumed.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        if not self._link.is_connected():
            return self._result(
                action,
                NativePauseResumeState.LINK_LOST,
                "SiK link was lost; onboard behavior remains native.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        age_s = self._clock.now() - last_heartbeat_s
        if age_s < 0 or age_s > target_valid_for_s:
            if accepted:
                state = {
                    NativePauseResumeAction.PAUSE: (
                        NativePauseResumeState.ACKNOWLEDGED_NO_PAUSED_TELEMETRY
                    ),
                    NativePauseResumeAction.RESUME: (
                        NativePauseResumeState.ACKNOWLEDGED_NO_RUNNING_TELEMETRY
                    ),
                }[action]
            else:
                state = NativePauseResumeState.STALE_LINK
            return self._result(
                action,
                state,
                f"Selected-target heartbeat became stale during native {action.value}.",
                requested_at_s,
                ack_result=ack_result,
                messages=messages,
            )
        return None

    def _result(
        self,
        action: NativePauseResumeAction,
        state: NativePauseResumeState,
        detail: str,
        requested_at_s: float,
        *,
        ack_result: int | None = None,
        messages: list[NativeStatusText] | tuple[NativeStatusText, ...] = (),
        state_observed_at_s: float | None = None,
        progress_sequence: int | None = None,
    ) -> NativePauseResumeCommandResult:
        return NativePauseResumeCommandResult(
            action=action,
            state=state,
            detail=detail,
            requested_at_s=requested_at_s,
            completed_at_s=max(requested_at_s, self._clock.now()),
            ack_result=ack_result,
            native_messages=tuple(messages),
            state_observed_at_s=state_observed_at_s,
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
