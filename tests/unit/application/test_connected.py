from __future__ import annotations

from dataclasses import replace

from skywriter.application.connected import (
    CancellationView,
    ConnectedFailureCode,
    ConnectedMissionService,
    ConnectedPortFailure,
    ConnectedTarget,
    ConnectedVerificationState,
    MissionReadback,
    MissionTransferEvidence,
)
from skywriter.application.telemetry import (
    HeartbeatTelemetry,
    HomeTelemetry,
    TelemetryLinkKind,
    TelemetryPoint,
    TelemetrySnapshot,
    TimedSignal,
)
from skywriter.compatibility.arducopter_4_6_3 import (
    NativeMissionPackage,
    VehicleIdentity,
    canonicalize_expected,
)
from skywriter.domain.compiled import (
    CompiledMission,
    CompiledMissionItem,
    MissionCommand,
    MissionFrame,
    MissionType,
)


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class FakePort:
    def __init__(self, kind: TelemetryLinkKind, target: ConnectedTarget) -> None:
        self.link_kind = kind
        self.target = target
        self.connected = True
        self.onboard = MissionReadback(())
        self.telemetry = telemetry(target)
        self.uploaded: NativeMissionPackage | None = None

    def is_connected(self) -> bool:
        return self.connected

    def discover(
        self, *, duration_s: float, cancellation: CancellationView
    ) -> tuple[ConnectedTarget, ...]:
        return (self.target,)

    def download_mission(
        self, target: ConnectedTarget, *, cancellation: CancellationView
    ) -> MissionReadback:
        return self.onboard

    def upload_and_verify(
        self,
        package: NativeMissionPackage,
        target: ConnectedTarget,
        *,
        approved: bool,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> MissionTransferEvidence:
        if cancellation.is_cancelled():
            raise ConnectedPortFailure(
                ConnectedFailureCode.CANCELLED, "mission transfer was cancelled"
            )
        self.uploaded = package
        self.onboard = MissionReadback(package.items, opaque_id=7)
        return MissionTransferEvidence(len(package.items), 7, True, *("a" * 64,) * 3)

    def collect_telemetry(
        self,
        target: ConnectedTarget,
        *,
        duration_s: float,
        cancellation: CancellationView,
    ) -> TelemetrySnapshot:
        return self.telemetry


def target(kind: TelemetryLinkKind, *, observed_at_s: float = 100.0) -> ConnectedTarget:
    return ConnectedTarget(
        VehicleIdentity("mavlink-system-1-component-1"),
        1,
        1,
        kind,
        2,
        3,
        0,
        observed_at_s,
    )


def telemetry(selected: ConnectedTarget, *, observed_at_s: float = 100.0) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        selected.vehicle.value,
        selected.system_id,
        selected.component_id,
        selected.link_kind,
        True,
        TimedSignal(HeartbeatTelemetry(False, 0, "Stabilize", 3, 2, 3), observed_at_s, 3.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        TimedSignal(
            HomeTelemetry(TelemetryPoint(-35.363261, 149.165230), 584.0),
            observed_at_s,
            60.0,
        ),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
        TimedSignal.unavailable(5.0),
    )


def compiled() -> CompiledMission:
    return CompiledMission(
        (
            CompiledMissionItem(
                0,
                MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                MissionCommand.NAV_TAKEOFF,
                True,
                True,
                0,
                0,
                0,
                0,
                -353632610,
                1491652300,
                10,
                MissionType.MISSION,
            ),
            CompiledMissionItem(
                1,
                MissionFrame.GLOBAL_RELATIVE_ALT_INT,
                MissionCommand.NAV_LAND,
                False,
                True,
                0,
                0,
                0,
                0,
                -353632600,
                1491652310,
                0,
                MissionType.MISSION,
            ),
        )
    )


def usb_verified_service() -> tuple[ConnectedMissionService, FakePort]:
    service = ConnectedMissionService()
    port = FakePort(TelemetryLinkKind.USB, target(TelemetryLinkKind.USB))
    cancellation = NeverCancelled()
    service.set_compiled(compiled(), mission_revision=3)
    service.discover(port, duration_s=1.0, cancellation=cancellation)
    service.select_target(1, 1, now_s=100.0)
    service.inspect_onboard(port, cancellation=cancellation)
    service.confirm_replacement(True)
    service.refresh_telemetry(port, duration_s=1.0, cancellation=cancellation)
    service.upload_and_verify(port, now_s=100.0, cancellation=cancellation)
    return service, port


def test_usb_upload_then_same_vehicle_sik_reverification_restores_readiness() -> None:
    service, usb = usb_verified_service()
    usb_snapshot = service.snapshot
    assert usb_snapshot.verification_state is ConnectedVerificationState.USB_VERIFIED
    assert service.snapshot.transfer_evidence is not None
    assert usb.uploaded is service.snapshot.expected_package

    service.disconnect()
    sik = FakePort(TelemetryLinkKind.SIK, target(TelemetryLinkKind.SIK))
    assert usb.uploaded is not None
    sik.onboard = MissionReadback(canonicalize_expected(usb.uploaded))
    cancellation = NeverCancelled()
    service.discover(sik, duration_s=1.0, cancellation=cancellation)
    service.select_target(1, 1, now_s=100.0)
    service.refresh_telemetry(sik, duration_s=1.0, cancellation=cancellation)
    service.reverify_over_sik(sik, now_s=100.0, cancellation=cancellation)

    sik_snapshot = service.snapshot
    assert sik_snapshot.verification_state is ConnectedVerificationState.SIK_VERIFIED
    assert sik_snapshot.connected_ready(100.0)
    assert sik_snapshot.reconnect_comparison is not None
    assert sik_snapshot.reconnect_comparison.verified


def test_upload_requires_explicit_existing_mission_replacement_confirmation() -> None:
    service = ConnectedMissionService()
    port = FakePort(TelemetryLinkKind.USB, target(TelemetryLinkKind.USB))
    cancellation = NeverCancelled()
    service.set_compiled(compiled(), mission_revision=1)
    service.discover(port, duration_s=1.0, cancellation=cancellation)
    service.select_target(1, 1, now_s=100.0)
    service.inspect_onboard(port, cancellation=cancellation)
    service.refresh_telemetry(port, duration_s=1.0, cancellation=cancellation)

    service.upload_and_verify(port, now_s=100.0, cancellation=cancellation)

    assert service.snapshot.failure is not None
    assert service.snapshot.failure.code is ConnectedFailureCode.REPLACEMENT_REQUIRED
    assert port.uploaded is None


def test_edit_disconnect_stale_and_wrong_vehicle_each_fail_closed() -> None:
    service, usb = usb_verified_service()
    service.mission_changed(mission_revision=4)
    assert service.snapshot.verification_state is ConnectedVerificationState.UNVERIFIED
    assert service.snapshot.expected_package is None

    service, usb = usb_verified_service()
    service.disconnect()
    assert service.snapshot.verification_state is ConnectedVerificationState.REVERIFY_REQUIRED
    stale = FakePort(
        TelemetryLinkKind.SIK,
        target(TelemetryLinkKind.SIK, observed_at_s=90.0),
    )
    service.discover(stale, duration_s=1.0, cancellation=NeverCancelled())
    service.select_target(1, 1, now_s=100.0)
    assert service.snapshot.failure is not None
    assert service.snapshot.failure.code is ConnectedFailureCode.STALE_IDENTITY

    service, usb = usb_verified_service()
    service.disconnect()
    wrong = FakePort(TelemetryLinkKind.SIK, target(TelemetryLinkKind.SIK))
    wrong.target = replace(wrong.target, vehicle=VehicleIdentity("other-vehicle"))
    service.discover(wrong, duration_s=1.0, cancellation=NeverCancelled())
    service.select_target(1, 1, now_s=100.0)
    assert service.snapshot.failure is not None
    assert service.snapshot.failure.code is ConnectedFailureCode.WRONG_IDENTITY


def test_sik_readback_mismatch_never_restores_readiness() -> None:
    service, usb = usb_verified_service()
    service.disconnect()
    sik = FakePort(TelemetryLinkKind.SIK, target(TelemetryLinkKind.SIK))
    assert usb.uploaded is not None
    normalized = canonicalize_expected(usb.uploaded)
    changed = replace(normalized[-1], altitude_m=1.0)
    sik.onboard = MissionReadback((*normalized[:-1], changed))
    cancellation = NeverCancelled()
    service.discover(sik, duration_s=1.0, cancellation=cancellation)
    service.select_target(1, 1, now_s=100.0)
    service.refresh_telemetry(sik, duration_s=1.0, cancellation=cancellation)
    service.reverify_over_sik(sik, now_s=100.0, cancellation=cancellation)

    assert service.snapshot.verification_state is ConnectedVerificationState.MISMATCH
    assert not service.snapshot.connected_ready(100.0)
    assert service.snapshot.failure is not None
    assert service.snapshot.failure.mismatches


def test_missing_home_and_cancelled_transfer_cannot_produce_verified() -> None:
    cancellation = NeverCancelled()
    service = ConnectedMissionService()
    port = FakePort(TelemetryLinkKind.USB, target(TelemetryLinkKind.USB))
    port.telemetry = replace(port.telemetry, home=TimedSignal.unavailable(60.0))
    service.set_compiled(compiled(), mission_revision=1)
    service.discover(port, duration_s=1.0, cancellation=cancellation)
    service.select_target(1, 1, now_s=100.0)
    service.inspect_onboard(port, cancellation=cancellation)
    service.confirm_replacement(True)
    service.refresh_telemetry(port, duration_s=1.0, cancellation=cancellation)
    service.upload_and_verify(port, now_s=100.0, cancellation=cancellation)
    assert service.snapshot.failure is not None
    assert service.snapshot.failure.code is ConnectedFailureCode.HOME_UNRESOLVED
    assert service.snapshot.expected_package is None

    service, port = usb_verified_service()
    service.mission_changed(mission_revision=4)
    service.set_compiled(compiled(), mission_revision=4)
    service.inspect_onboard(port, cancellation=cancellation)
    service.confirm_replacement(True)
    service.refresh_telemetry(port, duration_s=1.0, cancellation=cancellation)

    class Cancelled:
        def is_cancelled(self) -> bool:
            return True

    service.upload_and_verify(port, now_s=100.0, cancellation=Cancelled())
    assert service.snapshot.failure is not None
    assert service.snapshot.failure.code is ConnectedFailureCode.CANCELLED
    assert service.snapshot.verification_state is ConnectedVerificationState.UNVERIFIED
