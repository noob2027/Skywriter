"""Bounded, cancellable MAVLink mission upload/download state machine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from skywriter.compatibility.arducopter_4_6_3 import (
    HomeState,
    HomeUnresolved,
    NativeMissionItem,
    NativeMissionPackage,
    prepare_native_mission,
)
from skywriter.domain.compiled import CompiledMission
from skywriter.infrastructure.mavlink.connection import (
    MAV_MISSION_ACCEPTED,
    MAV_MISSION_OPERATION_CANCELLED,
    MAV_MISSION_TYPE_MISSION,
    MAV_MODE_FLAG_SAFETY_ARMED,
    Cancellation,
    Clock,
    IncomingMessage,
    MavlinkAddress,
    MissionLink,
    NeverCancelled,
    UploadAuthorization,
)
from skywriter.infrastructure.mavlink.verification import (
    VerifiedUpload,
    verify_acknowledged_upload,
)

_PROTOCOL_MESSAGES = frozenset(
    {
        "MISSION_ACK",
        "MISSION_COUNT",
        "MISSION_ITEM",
        "MISSION_ITEM_INT",
        "MISSION_REQUEST",
        "MISSION_REQUEST_INT",
    }
)


class MissionFailureCode(StrEnum):
    AUTHORIZATION = "authorization"
    HOME_UNRESOLVED = "home_unresolved"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    WRONG_TARGET = "wrong_target"
    WRONG_MISSION_TYPE = "wrong_mission_type"
    WRONG_SEQUENCE = "wrong_sequence"
    WRONG_ITEM_ENCODING = "wrong_item_encoding"
    NEGATIVE_ACK = "negative_ack"
    ARMED = "armed"
    MALFORMED_MESSAGE = "malformed_message"
    UNEXPECTED_MESSAGE = "unexpected_message"


class MissionProtocolError(RuntimeError):
    """Typed fail-closed protocol result."""

    def __init__(
        self,
        code: MissionFailureCode,
        phase: str,
        detail: str,
        *,
        sequence: int | None = None,
    ) -> None:
        super().__init__(f"{phase}: {detail}")
        self.code = code
        self.phase = phase
        self.detail = detail
        self.sequence = sequence


@dataclass(frozen=True, slots=True)
class ProtocolPolicy:
    """MAVLink-recommended bounded response windows and retry count."""

    response_timeout_s: float = 1.5
    item_timeout_s: float = 0.25
    max_retries: int = 5
    operation_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.response_timeout_s <= 0 or self.item_timeout_s <= 0:
            raise ValueError("response timeouts must be positive")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int):
            raise TypeError("max_retries must be an integer")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.operation_timeout_s <= 0:
            raise ValueError("operation_timeout_s must be positive")


@dataclass(frozen=True, slots=True)
class UploadAcknowledgement:
    opaque_id: int
    used_legacy_requests: bool


@dataclass(frozen=True, slots=True)
class DownloadedMission:
    items: tuple[NativeMissionItem, ...]
    opaque_id: int


class MissionProtocol:
    """Worker-callable protocol adapter; callers keep it off the UI thread."""

    def __init__(
        self,
        link: MissionLink,
        *,
        clock: Clock,
        policy: ProtocolPolicy | None = None,
        cancellation: Cancellation | None = None,
    ) -> None:
        self._link = link
        self._clock = clock
        self._policy = policy or ProtocolPolicy()
        self._cancellation = cancellation or NeverCancelled()
        self._deadline_s = 0.0
        self._transaction_started = False
        self._cancellation_sent = False
        self._active_target: MavlinkAddress | None = None

    def upload_and_verify(
        self,
        compiled: CompiledMission,
        *,
        home: HomeState,
        authorization: UploadAuthorization,
    ) -> VerifiedUpload:
        """Translate, upload, download, and return only exact normalized success."""

        self._begin()
        issue = authorization.issue(self._clock.now(), authorization.target.vehicle)
        if issue is not None:
            self._fail(MissionFailureCode.AUTHORIZATION, "gate", issue)
        if authorization.target.transport is not self._link.descriptor.kind:
            self._fail(
                MissionFailureCode.AUTHORIZATION,
                "gate",
                "upload authorization transport does not match the active link",
            )
        package = prepare_native_mission(
            compiled,
            target_vehicle=authorization.target.vehicle,
            home=home,
            now_s=self._clock.now(),
        )
        if isinstance(package, HomeUnresolved):
            self._fail(
                MissionFailureCode.HOME_UNRESOLVED,
                "prepare",
                f"{package.reason.value}: {package.detail}",
            )
        acknowledgement = self.upload(package, authorization=authorization, _nested=True)
        downloaded = self.download(authorization.target.address, _nested=True)
        return verify_acknowledged_upload(
            package,
            downloaded.items,
            opaque_id=acknowledgement.opaque_id,
            used_legacy_requests=acknowledgement.used_legacy_requests,
        )

    def upload(
        self,
        package: NativeMissionPackage,
        *,
        authorization: UploadAuthorization,
        _nested: bool = False,
    ) -> UploadAcknowledgement:
        if not _nested:
            self._begin()
        self._check_authorization(package, authorization)
        target = authorization.target.address
        self._active_target = target
        mission_type = MAV_MISSION_TYPE_MISSION

        def send_count() -> None:
            self._link.send_mission_count(
                target, count=len(package.items), mission_type=mission_type
            )

        self._send(send_count, phase="upload-count")
        self._transaction_started = True
        expected_sequence = 0
        last_send: Callable[[], None] = send_count
        retries = 0
        used_legacy = False

        while True:
            timeout = (
                self._policy.item_timeout_s
                if expected_sequence > 0
                else self._policy.response_timeout_s
            )
            message = self._next_protocol_message(target, timeout, phase="upload")
            if message is None:
                if retries >= self._policy.max_retries:
                    self._fail(
                        MissionFailureCode.TIMEOUT,
                        "upload",
                        "vehicle did not request the next mission item or acknowledge upload",
                        sequence=expected_sequence,
                    )
                self._send(last_send, phase="upload-retry")
                retries += 1
                continue
            retries = 0

            if message.name in {"MISSION_REQUEST", "MISSION_REQUEST_INT"}:
                requested = self._message_integer(message, "seq", phase="upload")
                self._require_mission_type(message, phase="upload")
                if requested > expected_sequence or requested >= len(package.items):
                    self._fail(
                        MissionFailureCode.WRONG_SEQUENCE,
                        "upload",
                        f"vehicle requested sequence {requested}; expected {expected_sequence}",
                        sequence=requested,
                    )
                if requested < expected_sequence:
                    item = package.items[requested]
                else:
                    item = package.items[expected_sequence]
                    expected_sequence += 1
                # Stock Copter 4.6.3 requests with the legacy message but accepts
                # the required INT response; the outgoing item contract stays INT-only.
                used_legacy = used_legacy or message.name == "MISSION_REQUEST"

                def send_item(item: NativeMissionItem = item) -> None:
                    self._link.send_mission_item_int(target, item=_item_payload(item))

                self._send(send_item, phase="upload-item")
                last_send = send_item
                continue

            if message.name == "MISSION_ACK":
                self._require_mission_type(message, phase="upload")
                result = self._message_integer(message, "type", phase="upload")
                if result != MAV_MISSION_ACCEPTED:
                    self._fail(
                        MissionFailureCode.NEGATIVE_ACK,
                        "upload",
                        f"vehicle returned MISSION_ACK type {result}",
                    )
                if expected_sequence != len(package.items):
                    self._fail(
                        MissionFailureCode.UNEXPECTED_MESSAGE,
                        "upload",
                        "vehicle accepted upload before requesting every item",
                        sequence=expected_sequence,
                    )
                return UploadAcknowledgement(
                    opaque_id=_optional_integer(message, "opaque_id", default=0),
                    used_legacy_requests=used_legacy,
                )

            self._fail(
                MissionFailureCode.UNEXPECTED_MESSAGE,
                "upload",
                f"unexpected {message.name}",
            )

    def download(self, target: MavlinkAddress, *, _nested: bool = False) -> DownloadedMission:
        if not _nested:
            self._begin()
        self._active_target = target
        mission_type = MAV_MISSION_TYPE_MISSION

        def send_list_request() -> None:
            self._link.send_mission_request_list(target, mission_type=mission_type)

        self._send(send_list_request, phase="download-list")
        self._transaction_started = True
        count_message = self._await_with_retries(
            target,
            expected="MISSION_COUNT",
            resend=send_list_request,
            timeout_s=self._policy.response_timeout_s,
            phase="download-count",
        )
        self._require_mission_type(count_message, phase="download-count")
        count = self._message_integer(count_message, "count", phase="download-count")
        if not 0 <= count <= 65535:
            self._fail(
                MissionFailureCode.MALFORMED_MESSAGE,
                "download-count",
                f"mission count {count} is outside the uint16 range",
            )
        opaque_id = _optional_integer(count_message, "opaque_id", default=0)
        items: list[NativeMissionItem] = []

        for expected_sequence in range(count):

            def send_item_request(sequence: int = expected_sequence) -> None:
                self._link.send_mission_request_int(
                    target, sequence=sequence, mission_type=mission_type
                )

            self._send(send_item_request, phase="download-item-request")
            retries = 0
            while True:
                message = self._next_protocol_message(
                    target, self._policy.item_timeout_s, phase="download-item"
                )
                if message is None:
                    if retries >= self._policy.max_retries:
                        self._fail(
                            MissionFailureCode.TIMEOUT,
                            "download-item",
                            "vehicle did not return the requested INT mission item",
                            sequence=expected_sequence,
                        )
                    self._send(send_item_request, phase="download-item-retry")
                    retries += 1
                    continue
                if message.name == "MISSION_ACK":
                    result = self._message_integer(message, "type", phase="download-item")
                    self._fail(
                        MissionFailureCode.NEGATIVE_ACK,
                        "download-item",
                        f"vehicle ended download with MISSION_ACK type {result}",
                        sequence=expected_sequence,
                    )
                if message.name == "MISSION_ITEM":
                    self._fail(
                        MissionFailureCode.WRONG_ITEM_ENCODING,
                        "download-item",
                        "float MISSION_ITEM cannot satisfy the INT readback contract",
                        sequence=expected_sequence,
                    )
                if message.name != "MISSION_ITEM_INT":
                    self._fail(
                        MissionFailureCode.UNEXPECTED_MESSAGE,
                        "download-item",
                        f"unexpected {message.name}",
                        sequence=expected_sequence,
                    )
                self._require_mission_type(message, phase="download-item")
                actual_sequence = self._message_integer(message, "seq", phase="download-item")
                if actual_sequence < expected_sequence:
                    self._send(send_item_request, phase="download-duplicate")
                    continue
                if actual_sequence != expected_sequence:
                    self._fail(
                        MissionFailureCode.WRONG_SEQUENCE,
                        "download-item",
                        f"received sequence {actual_sequence}; expected {expected_sequence}",
                        sequence=actual_sequence,
                    )
                items.append(_native_item_from_message(message))
                break

        self._send(
            lambda: self._link.send_mission_ack(
                target, result=MAV_MISSION_ACCEPTED, mission_type=mission_type
            ),
            phase="download-ack",
        )
        return DownloadedMission(items=tuple(items), opaque_id=opaque_id)

    def _begin(self) -> None:
        self._deadline_s = self._clock.now() + self._policy.operation_timeout_s
        self._transaction_started = False
        self._cancellation_sent = False
        self._active_target = None
        self._check_common(phase="start")

    def _check_authorization(
        self, package: NativeMissionPackage, authorization: UploadAuthorization
    ) -> None:
        issue = authorization.issue(self._clock.now(), package.vehicle)
        if issue is not None:
            self._fail(MissionFailureCode.AUTHORIZATION, "gate", issue)
        if authorization.target.transport is not self._link.descriptor.kind:
            self._fail(
                MissionFailureCode.AUTHORIZATION,
                "gate",
                "upload authorization transport does not match the active link",
            )

    def _await_with_retries(
        self,
        target: MavlinkAddress,
        *,
        expected: str,
        resend: Callable[[], None],
        timeout_s: float,
        phase: str,
    ) -> IncomingMessage:
        for attempt in range(self._policy.max_retries + 1):
            message = self._next_protocol_message(target, timeout_s, phase=phase)
            if message is not None:
                if message.name == "MISSION_ACK":
                    result = self._message_integer(message, "type", phase=phase)
                    self._fail(
                        MissionFailureCode.NEGATIVE_ACK,
                        phase,
                        f"vehicle returned MISSION_ACK type {result}",
                    )
                if message.name != expected:
                    self._fail(
                        MissionFailureCode.UNEXPECTED_MESSAGE,
                        phase,
                        f"expected {expected}; received {message.name}",
                    )
                return message
            if attempt < self._policy.max_retries:
                self._send(resend, phase=f"{phase}-retry")
        self._fail(
            MissionFailureCode.TIMEOUT,
            phase,
            f"vehicle did not return {expected}",
        )

    def _next_protocol_message(
        self, target: MavlinkAddress, timeout_s: float, *, phase: str
    ) -> IncomingMessage | None:
        wait_deadline = min(self._deadline_s, self._clock.now() + timeout_s)
        while self._clock.now() < wait_deadline:
            self._check_common(phase=phase, target=target)
            try:
                message = self._link.receive(max(0.0, wait_deadline - self._clock.now()))
            except ConnectionError as error:
                self._fail(MissionFailureCode.DISCONNECTED, phase, str(error))
            if message is None:
                self._check_common(phase=phase, target=target)
                return None
            if message.name == "HEARTBEAT":
                if message.source == target:
                    base_mode = self._message_integer(message, "base_mode", phase=phase)
                    if base_mode & MAV_MODE_FLAG_SAFETY_ARMED:
                        self._fail(
                            MissionFailureCode.ARMED,
                            phase,
                            "target became armed during mission transfer",
                        )
                continue
            if message.name not in _PROTOCOL_MESSAGES:
                continue
            if message.source != target:
                self._fail(
                    MissionFailureCode.WRONG_TARGET,
                    phase,
                    f"protocol response came from {message.source.system_id}:"
                    f"{message.source.component_id}, not the selected target",
                )
            self._require_local_recipient(message, phase=phase)
            return message
        self._check_common(phase=phase, target=target)
        return None

    def _require_local_recipient(self, message: IncomingMessage, *, phase: str) -> None:
        for field, expected in (
            ("target_system", self._link.local_address.system_id),
            ("target_component", self._link.local_address.component_id),
        ):
            actual = self._message_integer(message, field, phase=phase)
            if actual != expected:
                self._fail(
                    MissionFailureCode.WRONG_TARGET,
                    phase,
                    f"{message.name}.{field} was {actual}; expected {expected}",
                )

    def _require_mission_type(self, message: IncomingMessage, *, phase: str) -> None:
        mission_type = self._message_integer(message, "mission_type", phase=phase)
        if mission_type != MAV_MISSION_TYPE_MISSION:
            self._fail(
                MissionFailureCode.WRONG_MISSION_TYPE,
                phase,
                f"{message.name} mission_type was {mission_type}",
            )

    def _message_integer(self, message: IncomingMessage, field: str, *, phase: str) -> int:
        try:
            return message.integer(field)
        except ValueError as error:
            self._fail(MissionFailureCode.MALFORMED_MESSAGE, phase, str(error))

    def _send(self, action: Callable[[], None], *, phase: str) -> None:
        self._check_common(phase=phase)
        try:
            action()
        except ConnectionError as error:
            self._fail(MissionFailureCode.DISCONNECTED, phase, str(error))

    def _check_common(self, *, phase: str, target: MavlinkAddress | None = None) -> None:
        target = target or self._active_target
        if self._cancellation.is_cancelled():
            if self._transaction_started and not self._cancellation_sent and target is not None:
                self._cancellation_sent = True
                try:
                    # This is standards-compliant best effort.  Pinned Copter 4.6.3
                    # may ignore explicit cancellation, so local idle never implies remote idle.
                    self._link.send_mission_ack(
                        target,
                        result=MAV_MISSION_OPERATION_CANCELLED,
                        mission_type=MAV_MISSION_TYPE_MISSION,
                    )
                except ConnectionError:
                    pass
            self._fail(
                MissionFailureCode.CANCELLED,
                phase,
                "mission operation was cancelled locally; remote idle is not assumed",
            )
        if not self._link.is_connected():
            self._fail(MissionFailureCode.DISCONNECTED, phase, "MAVLink connection is closed")
        if self._deadline_s and self._clock.now() >= self._deadline_s:
            self._fail(MissionFailureCode.TIMEOUT, phase, "operation deadline expired")

    def _fail(
        self,
        code: MissionFailureCode,
        phase: str,
        detail: str,
        *,
        sequence: int | None = None,
    ) -> NoReturn:
        raise MissionProtocolError(code, phase, detail, sequence=sequence)


def _item_payload(item: NativeMissionItem) -> dict[str, int | float]:
    return {
        "seq": item.sequence,
        "frame": item.frame,
        "command": item.command,
        "current": int(item.current),
        "autocontinue": int(item.autocontinue),
        "param1": item.param1,
        "param2": item.param2,
        "param3": item.param3,
        "param4": item.param4,
        "x": item.latitude_e7,
        "y": item.longitude_e7,
        "z": item.altitude_m,
        "mission_type": item.mission_type,
    }


def _native_item_from_message(message: IncomingMessage) -> NativeMissionItem:
    try:
        current = message.integer("current")
        autocontinue = message.integer("autocontinue")
        if current not in (0, 1) or autocontinue not in (0, 1):
            raise ValueError("current and autocontinue must be zero or one")
        return NativeMissionItem(
            sequence=message.integer("seq"),
            frame=message.integer("frame"),
            command=message.integer("command"),
            current=bool(current),
            autocontinue=bool(autocontinue),
            param1=message.number("param1"),
            param2=message.number("param2"),
            param3=message.number("param3"),
            param4=message.number("param4"),
            latitude_e7=message.integer("x"),
            longitude_e7=message.integer("y"),
            altitude_m=message.number("z"),
            mission_type=message.integer("mission_type"),
        )
    except (TypeError, ValueError) as error:
        raise MissionProtocolError(
            MissionFailureCode.MALFORMED_MESSAGE,
            "download-item",
            str(error),
        ) from error


def _optional_integer(message: IncomingMessage, field: str, *, default: int) -> int:
    value = message.fields.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MissionProtocolError(
            MissionFailureCode.MALFORMED_MESSAGE,
            "protocol",
            f"{message.name}.{field} must be an integer",
        )
    return value
