"""Concrete Task 009 composition of accepted MAVLink boundaries."""

from __future__ import annotations

from dataclasses import replace

from skywriter.application.connected import (
    CancellationView,
    ConnectedFailureCode,
    ConnectedPortFailure,
    ConnectedTarget,
    MissionReadback,
    MissionTransferEvidence,
)
from skywriter.application.telemetry import TelemetryLinkKind, TelemetrySnapshot
from skywriter.compatibility.arducopter_4_6_3 import NativeMissionPackage
from skywriter.infrastructure.mavlink.connection import (
    Clock,
    MavlinkAddress,
    MissionLink,
    TargetCandidate,
    TransportKind,
    UploadAuthorization,
    discover_targets,
)
from skywriter.infrastructure.mavlink.mission_protocol import (
    MissionFailureCode,
    MissionProtocol,
    MissionProtocolError,
    ProtocolPolicy,
)
from skywriter.infrastructure.mavlink.telemetry import (
    TelemetryAdapter,
    TelemetryFreshnessPolicy,
    TelemetryIngestCode,
    TelemetryPoller,
)
from skywriter.infrastructure.mavlink.verification import (
    MissionVerificationError,
    verify_acknowledged_upload,
)


class ConnectedMavlinkPort:
    """One selected-link adapter with no command or parameter-write surface."""

    def __init__(
        self,
        link: MissionLink,
        *,
        clock: Clock,
        protocol_policy: ProtocolPolicy | None = None,
        telemetry_policy: TelemetryFreshnessPolicy | None = None,
    ) -> None:
        self._link = link
        self._clock = clock
        self._protocol_policy = protocol_policy
        self._telemetry_policy = telemetry_policy
        self._candidates: dict[MavlinkAddress, TargetCandidate] = {}

    @property
    def link_kind(self) -> TelemetryLinkKind:
        return _telemetry_link_kind(self._link.descriptor.kind)

    def is_connected(self) -> bool:
        return self._link.is_connected()

    def discover(
        self, *, duration_s: float, cancellation: CancellationView
    ) -> tuple[ConnectedTarget, ...]:
        try:
            candidates = discover_targets(
                self._link,
                clock=self._clock,
                duration_s=duration_s,
                cancellation=cancellation,
            )
        except ConnectionError as error:
            raise ConnectedPortFailure(ConnectedFailureCode.DISCONNECTED, str(error)) from error
        if cancellation.is_cancelled():
            raise ConnectedPortFailure(
                ConnectedFailureCode.CANCELLED, "target discovery was cancelled"
            )
        self._candidates = {candidate.address: candidate for candidate in candidates}
        return tuple(_connected_target(candidate) for candidate in candidates)

    def download_mission(
        self, target: ConnectedTarget, *, cancellation: CancellationView
    ) -> MissionReadback:
        candidate = self._candidate(target)
        protocol = MissionProtocol(
            self._link,
            clock=self._clock,
            policy=self._protocol_policy,
            cancellation=cancellation,
        )
        try:
            downloaded = protocol.download(candidate.address)
        except MissionProtocolError as error:
            raise _port_failure(error) from error
        return MissionReadback(downloaded.items, downloaded.opaque_id)

    def upload_and_verify(
        self,
        package: NativeMissionPackage,
        target: ConnectedTarget,
        *,
        approved: bool,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> MissionTransferEvidence:
        candidate = self._candidate(target)
        protocol = MissionProtocol(
            self._link,
            clock=self._clock,
            policy=self._protocol_policy,
            cancellation=cancellation,
        )
        authorization = UploadAuthorization(
            candidate,
            approved=approved,
            valid_for_s=target_valid_for_s,
        )
        try:
            acknowledgement = protocol.upload(package, authorization=authorization)
            downloaded = protocol.download(candidate.address)
            verified = verify_acknowledged_upload(
                package,
                downloaded.items,
                opaque_id=acknowledgement.opaque_id,
                used_legacy_requests=acknowledgement.used_legacy_requests,
            )
        except MissionVerificationError as error:
            raise ConnectedPortFailure(
                ConnectedFailureCode.READBACK_MISMATCH,
                str(error),
                source_code="normalized_readback_mismatch",
                mismatches=error.failure.mismatches,
            ) from error
        except MissionProtocolError as error:
            raise _port_failure(error) from error
        return MissionTransferEvidence(
            item_count=verified.item_count,
            opaque_id=verified.opaque_id,
            used_legacy_requests=verified.used_legacy_requests,
            expected_digest=verified.evidence.expected_digest,
            downloaded_digest=verified.evidence.downloaded_digest,
            evidence_digest=verified.evidence.evidence_digest,
        )

    def collect_telemetry(
        self,
        target: ConnectedTarget,
        *,
        duration_s: float,
        cancellation: CancellationView,
    ) -> TelemetrySnapshot:
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")
        candidate = self._candidate(target)
        adapter = TelemetryAdapter(candidate, policy=self._telemetry_policy)
        poller = TelemetryPoller(
            self._link,
            adapter,
            clock=self._clock,
            cancellation=cancellation,
            policy=self._telemetry_policy,
        )
        deadline_s = self._clock.now() + duration_s
        while self._clock.now() < deadline_s:
            result = poller.poll_once(deadline_s - self._clock.now())
            if result.code is TelemetryIngestCode.CANCELLED:
                raise ConnectedPortFailure(ConnectedFailureCode.CANCELLED, result.detail)
            if result.code is TelemetryIngestCode.DISCONNECTED:
                raise ConnectedPortFailure(ConnectedFailureCode.DISCONNECTED, result.detail)
        return poller.snapshot()

    def _candidate(self, target: ConnectedTarget) -> TargetCandidate:
        if target.link_kind is not self.link_kind:
            raise ConnectedPortFailure(
                ConnectedFailureCode.WRONG_LINK,
                "selected target link does not match the active connection",
            )
        address = MavlinkAddress(target.system_id, target.component_id)
        candidate = self._candidates.get(address)
        if candidate is None or candidate.vehicle != target.vehicle:
            raise ConnectedPortFailure(
                ConnectedFailureCode.WRONG_IDENTITY,
                "selected target was not discovered on this connection",
            )
        return replace(
            candidate,
            vehicle_type=target.vehicle_type,
            autopilot_type=target.autopilot_type,
            base_mode=target.base_mode,
            observed_at_s=target.observed_at_s,
        )


def _connected_target(candidate: TargetCandidate) -> ConnectedTarget:
    return ConnectedTarget(
        vehicle=candidate.vehicle,
        system_id=candidate.address.system_id,
        component_id=candidate.address.component_id,
        link_kind=_telemetry_link_kind(candidate.transport),
        vehicle_type=candidate.vehicle_type,
        autopilot_type=candidate.autopilot_type,
        base_mode=candidate.base_mode,
        observed_at_s=candidate.observed_at_s,
    )


def _telemetry_link_kind(kind: TransportKind) -> TelemetryLinkKind:
    if kind is TransportKind.USB:
        return TelemetryLinkKind.USB
    if kind is TransportKind.SIK:
        return TelemetryLinkKind.SIK
    raise AssertionError(f"unsupported transport kind {kind}")


def _port_failure(error: MissionProtocolError) -> ConnectedPortFailure:
    mapping = {
        MissionFailureCode.CANCELLED: ConnectedFailureCode.CANCELLED,
        MissionFailureCode.DISCONNECTED: ConnectedFailureCode.DISCONNECTED,
        MissionFailureCode.WRONG_TARGET: ConnectedFailureCode.WRONG_IDENTITY,
        MissionFailureCode.AUTHORIZATION: ConnectedFailureCode.PROTOCOL,
        MissionFailureCode.HOME_UNRESOLVED: ConnectedFailureCode.HOME_UNRESOLVED,
        MissionFailureCode.ARMED: ConnectedFailureCode.ARMED,
    }
    code = mapping.get(error.code, ConnectedFailureCode.PROTOCOL)
    return ConnectedPortFailure(
        code,
        f"{error.phase}: {error.detail}",
        source_code=error.code.value,
    )
