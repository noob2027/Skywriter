"""Application-owned connected mission state, gates, and use cases."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from skywriter.application.telemetry import (
    TelemetryFreshness,
    TelemetryLinkKind,
    TelemetryPoint,
    TelemetryRoute,
    TelemetryRoutePoint,
    TelemetrySnapshot,
)
from skywriter.compatibility.arducopter_4_6_3 import (
    FieldMismatch,
    HomeSnapshot,
    HomeUnresolved,
    HomeUnresolvedReason,
    NativeMissionItem,
    NativeMissionPackage,
    NativeReadbackVerification,
    VehicleIdentity,
    prepare_native_mission,
    verify_native_readback,
)
from skywriter.domain.compiled import CompiledMission, MissionCommand


class ConnectedVerificationState(StrEnum):
    UNVERIFIED = "unverified"
    USB_VERIFIED = "usb_verified"
    REVERIFY_REQUIRED = "reverify_required"
    SIK_VERIFIED = "sik_verified"
    MISMATCH = "mismatch"


class ConnectedFailureCode(StrEnum):
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    WRONG_IDENTITY = "wrong_identity"
    STALE_IDENTITY = "stale_identity"
    HOME_UNRESOLVED = "home_unresolved"
    REPLACEMENT_REQUIRED = "replacement_required"
    COMPILED_MISSION_REQUIRED = "compiled_mission_required"
    WRONG_LINK = "wrong_link"
    ARMED = "armed"
    PROTOCOL = "protocol"
    READBACK_MISMATCH = "readback_mismatch"
    TELEMETRY_UNAVAILABLE = "telemetry_unavailable"


@dataclass(frozen=True, slots=True)
class ConnectedFailure:
    code: ConnectedFailureCode
    detail: str
    source_code: str | None = None
    mismatches: tuple[FieldMismatch, ...] = ()

    def __post_init__(self) -> None:
        if not self.detail:
            raise ValueError("failure detail must not be empty")


@dataclass(frozen=True, slots=True)
class ConnectedTarget:
    vehicle: VehicleIdentity
    system_id: int
    component_id: int
    link_kind: TelemetryLinkKind
    vehicle_type: int
    autopilot_type: int
    base_mode: int
    observed_at_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.vehicle, VehicleIdentity):
            raise TypeError("vehicle must be a VehicleIdentity")
        for name in ("system_id", "component_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 255:
                raise ValueError(f"{name} must be an integer between 1 and 255")
        for name in ("vehicle_type", "autopilot_type", "base_mode"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not math.isfinite(self.observed_at_s):
            raise ValueError("observed_at_s must be finite")

    @property
    def armed(self) -> bool:
        return bool(self.base_mode & 128)

    def is_fresh(self, now_s: float, valid_for_s: float) -> bool:
        age_s = now_s - self.observed_at_s
        return math.isfinite(now_s) and 0 <= age_s <= valid_for_s


@dataclass(frozen=True, slots=True)
class MissionReadback:
    items: tuple[NativeMissionItem, ...]
    opaque_id: int = 0

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if not all(isinstance(item, NativeMissionItem) for item in items):
            raise TypeError("readback items must be NativeMissionItem values")
        if isinstance(self.opaque_id, bool) or not isinstance(self.opaque_id, int):
            raise TypeError("opaque_id must be an integer")
        object.__setattr__(self, "items", items)

    @property
    def item_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class MissionTransferEvidence:
    item_count: int
    opaque_id: int
    used_legacy_requests: bool
    expected_digest: str
    downloaded_digest: str
    evidence_digest: str

    def __post_init__(self) -> None:
        if self.item_count < 0:
            raise ValueError("item_count must be non-negative")
        for name in ("expected_digest", "downloaded_digest", "evidence_digest"):
            value = getattr(self, name)
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError(f"{name} must be a SHA-256 hex digest")


class ConnectedPortFailure(RuntimeError):
    """Typed adapter failure safe for the application layer to retain."""

    def __init__(
        self,
        code: ConnectedFailureCode,
        detail: str,
        *,
        source_code: str | None = None,
        mismatches: tuple[FieldMismatch, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.source_code = source_code
        self.mismatches = tuple(mismatches)


class CancellationView(Protocol):
    def is_cancelled(self) -> bool: ...


class ConnectedVehiclePort(Protocol):
    """High-level injected port; application code never touches MAVLink packets."""

    @property
    def link_kind(self) -> TelemetryLinkKind: ...

    def is_connected(self) -> bool: ...

    def discover(
        self, *, duration_s: float, cancellation: CancellationView
    ) -> tuple[ConnectedTarget, ...]: ...

    def download_mission(
        self, target: ConnectedTarget, *, cancellation: CancellationView
    ) -> MissionReadback: ...

    def upload_and_verify(
        self,
        package: NativeMissionPackage,
        target: ConnectedTarget,
        *,
        approved: bool,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> MissionTransferEvidence: ...

    def collect_telemetry(
        self,
        target: ConnectedTarget,
        *,
        duration_s: float,
        cancellation: CancellationView,
    ) -> TelemetrySnapshot: ...


@dataclass(frozen=True, slots=True)
class ConnectedMissionSnapshot:
    revision: int = 0
    mission_revision: int | None = None
    compiled: CompiledMission | None = None
    candidates: tuple[ConnectedTarget, ...] = ()
    selected_target: ConnectedTarget | None = None
    link_kind: TelemetryLinkKind | None = None
    link_connected: bool = False
    onboard: MissionReadback | None = None
    replacement_confirmed: bool = False
    expected_package: NativeMissionPackage | None = None
    transfer_evidence: MissionTransferEvidence | None = None
    reconnect_comparison: NativeReadbackVerification | None = None
    verification_state: ConnectedVerificationState = ConnectedVerificationState.UNVERIFIED
    telemetry: TelemetrySnapshot | None = None
    failure: ConnectedFailure | None = None

    def can_upload_at(self, now_s: float, target_valid_for_s: float) -> bool:
        target = self.selected_target
        return bool(
            self.link_connected
            and self.link_kind is TelemetryLinkKind.USB
            and target is not None
            and target.link_kind is TelemetryLinkKind.USB
            and target.is_fresh(now_s, target_valid_for_s)
            and not target.armed
            and self.compiled is not None
            and self.mission_revision is not None
            and self.onboard is not None
            and self.replacement_confirmed
        )

    def can_reverify_at(self, now_s: float, target_valid_for_s: float) -> bool:
        target = self.selected_target
        expected = self.expected_package
        return bool(
            self.link_connected
            and self.link_kind is TelemetryLinkKind.SIK
            and target is not None
            and target.link_kind is TelemetryLinkKind.SIK
            and target.is_fresh(now_s, target_valid_for_s)
            and expected is not None
            and target.vehicle == expected.vehicle
        )

    def connected_ready(self, now_s: float) -> bool:
        target = self.selected_target
        telemetry = self.telemetry
        expected = self.expected_package
        return bool(
            self.verification_state is ConnectedVerificationState.SIK_VERIFIED
            and self.link_connected
            and self.link_kind is TelemetryLinkKind.SIK
            and target is not None
            and expected is not None
            and target.vehicle == expected.vehicle
            and telemetry is not None
            and telemetry.vehicle_identity == target.vehicle.value
            and telemetry.command_gate_fresh(now_s)
        )

    @property
    def telemetry_route(self) -> TelemetryRoute:
        package = self.expected_package
        if package is None:
            return TelemetryRoute()
        return _route_from_package(package)


class ConnectedMissionService:
    """Coordinate accepted connected components without owning transport I/O."""

    def __init__(self, *, target_valid_for_s: float = 3.0) -> None:
        if not math.isfinite(target_valid_for_s) or target_valid_for_s <= 0:
            raise ValueError("target_valid_for_s must be a positive finite number")
        self._target_valid_for_s = target_valid_for_s
        self._snapshot = ConnectedMissionSnapshot()

    @property
    def snapshot(self) -> ConnectedMissionSnapshot:
        return self._snapshot

    def set_compiled(self, compiled: CompiledMission, *, mission_revision: int) -> None:
        if not isinstance(compiled, CompiledMission):
            raise TypeError("compiled must be a CompiledMission")
        if mission_revision < 0:
            raise ValueError("mission_revision must be non-negative")
        if (
            self._snapshot.compiled == compiled
            and self._snapshot.mission_revision == mission_revision
        ):
            return
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            mission_revision=mission_revision,
            compiled=compiled,
            expected_package=None,
            transfer_evidence=None,
            reconnect_comparison=None,
            verification_state=ConnectedVerificationState.UNVERIFIED,
            replacement_confirmed=False,
            failure=None,
        )

    def mission_changed(self, *, mission_revision: int) -> None:
        if mission_revision < 0:
            raise ValueError("mission_revision must be non-negative")
        if self._snapshot.mission_revision == mission_revision and self._snapshot.compiled is None:
            return
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            mission_revision=mission_revision,
            compiled=None,
            expected_package=None,
            transfer_evidence=None,
            reconnect_comparison=None,
            verification_state=ConnectedVerificationState.UNVERIFIED,
            replacement_confirmed=False,
            failure=None,
        )

    def discover(
        self,
        port: ConnectedVehiclePort,
        *,
        duration_s: float,
        cancellation: CancellationView,
    ) -> ConnectedMissionSnapshot:
        if duration_s <= 0 or not math.isfinite(duration_s):
            raise ValueError("duration_s must be a positive finite number")
        try:
            if not port.is_connected():
                raise ConnectedPortFailure(
                    ConnectedFailureCode.DISCONNECTED, "connection is not open"
                )
            candidates = port.discover(duration_s=duration_s, cancellation=cancellation)
        except ConnectedPortFailure as error:
            return self._record_port_failure(error)
        if any(candidate.link_kind is not port.link_kind for candidate in candidates):
            raise ValueError("port returned a candidate from a different link kind")
        verification = self._snapshot.verification_state
        if self._snapshot.expected_package is not None:
            verification = ConnectedVerificationState.REVERIFY_REQUIRED
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            candidates=tuple(candidates),
            selected_target=None,
            link_kind=port.link_kind,
            link_connected=True,
            onboard=None,
            replacement_confirmed=False,
            telemetry=None,
            reconnect_comparison=None,
            verification_state=verification,
            failure=None,
        )
        return self._snapshot

    def select_target(
        self, system_id: int, component_id: int, *, now_s: float
    ) -> ConnectedMissionSnapshot:
        matches = tuple(
            candidate
            for candidate in self._snapshot.candidates
            if (candidate.system_id, candidate.component_id) == (system_id, component_id)
        )
        if len(matches) != 1:
            return self._record_failure(
                ConnectedFailureCode.WRONG_IDENTITY,
                f"expected one target at {system_id}:{component_id}; found {len(matches)}",
            )
        selected = matches[0]
        if not selected.is_fresh(now_s, self._target_valid_for_s):
            return self._record_failure(
                ConnectedFailureCode.STALE_IDENTITY,
                "selected target heartbeat is stale",
            )
        expected = self._snapshot.expected_package
        if expected is not None and selected.vehicle != expected.vehicle:
            return self._record_failure(
                ConnectedFailureCode.WRONG_IDENTITY,
                "reconnected target does not match the USB-verified vehicle",
            )
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            selected_target=selected,
            failure=None,
        )
        return self._snapshot

    def inspect_onboard(
        self, port: ConnectedVehiclePort, *, cancellation: CancellationView
    ) -> ConnectedMissionSnapshot:
        target = self._require_selected_port(port, TelemetryLinkKind.USB)
        if target is None:
            return self._snapshot
        try:
            onboard = port.download_mission(target, cancellation=cancellation)
        except ConnectedPortFailure as error:
            return self._record_port_failure(error)
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            onboard=onboard,
            replacement_confirmed=False,
            failure=None,
        )
        return self._snapshot

    def confirm_replacement(self, confirmed: bool) -> ConnectedMissionSnapshot:
        if not isinstance(confirmed, bool):
            raise TypeError("confirmed must be a boolean")
        if self._snapshot.onboard is None:
            return self._record_failure(
                ConnectedFailureCode.REPLACEMENT_REQUIRED,
                "inspect the onboard mission before confirming replacement",
            )
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            replacement_confirmed=confirmed,
            failure=None,
        )
        return self._snapshot

    def refresh_telemetry(
        self,
        port: ConnectedVehiclePort,
        *,
        duration_s: float,
        cancellation: CancellationView,
    ) -> ConnectedMissionSnapshot:
        target = self._require_selected_port(port)
        if target is None:
            return self._snapshot
        try:
            telemetry = port.collect_telemetry(
                target, duration_s=duration_s, cancellation=cancellation
            )
        except ConnectedPortFailure as error:
            return self._record_port_failure(error)
        if (
            telemetry.vehicle_identity != target.vehicle.value
            or (telemetry.target_system, telemetry.target_component)
            != (target.system_id, target.component_id)
            or telemetry.link_kind is not target.link_kind
        ):
            return self._record_failure(
                ConnectedFailureCode.WRONG_IDENTITY,
                "telemetry snapshot does not belong to the selected target",
            )
        refreshed_target = target
        heartbeat = telemetry.heartbeat
        if heartbeat.value is not None and heartbeat.observed_at_s is not None:
            refreshed_target = replace(
                target,
                base_mode=128 if heartbeat.value.armed else 0,
                vehicle_type=heartbeat.value.vehicle_type,
                autopilot_type=heartbeat.value.autopilot_type,
                observed_at_s=heartbeat.observed_at_s,
            )
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            selected_target=refreshed_target,
            link_connected=telemetry.link_connected,
            telemetry=telemetry,
            failure=None,
        )
        return self._snapshot

    def upload_and_verify(
        self,
        port: ConnectedVehiclePort,
        *,
        now_s: float,
        cancellation: CancellationView,
    ) -> ConnectedMissionSnapshot:
        if not self._snapshot.can_upload_at(now_s, self._target_valid_for_s):
            return self._record_upload_gate_failure(now_s)
        target = self._snapshot.selected_target
        compiled = self._snapshot.compiled
        assert target is not None and compiled is not None
        home = self._authoritative_home(target, now_s=now_s)
        package = prepare_native_mission(
            compiled,
            target_vehicle=target.vehicle,
            home=home,
            now_s=now_s,
        )
        if isinstance(package, HomeUnresolved):
            return self._record_failure(
                ConnectedFailureCode.HOME_UNRESOLVED,
                f"{package.reason.value}: {package.detail}",
            )
        try:
            evidence = port.upload_and_verify(
                package,
                target,
                approved=True,
                target_valid_for_s=self._target_valid_for_s,
                cancellation=cancellation,
            )
        except ConnectedPortFailure as error:
            if error.code is ConnectedFailureCode.READBACK_MISMATCH:
                self._snapshot = replace(
                    self._snapshot,
                    expected_package=package,
                    verification_state=ConnectedVerificationState.MISMATCH,
                )
            return self._record_port_failure(error)
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            expected_package=package,
            transfer_evidence=evidence,
            reconnect_comparison=None,
            verification_state=ConnectedVerificationState.USB_VERIFIED,
            failure=None,
        )
        return self._snapshot

    def disconnect(self) -> ConnectedMissionSnapshot:
        telemetry = self._snapshot.telemetry
        if telemetry is not None:
            telemetry = replace(telemetry, link_connected=False)
        verification = self._snapshot.verification_state
        if self._snapshot.expected_package is not None:
            verification = ConnectedVerificationState.REVERIFY_REQUIRED
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            candidates=(),
            selected_target=None,
            link_kind=None,
            link_connected=False,
            onboard=None,
            replacement_confirmed=False,
            telemetry=telemetry,
            reconnect_comparison=None,
            verification_state=verification,
            failure=None,
        )
        return self._snapshot

    def reverify_over_sik(
        self,
        port: ConnectedVehiclePort,
        *,
        now_s: float,
        cancellation: CancellationView,
    ) -> ConnectedMissionSnapshot:
        if not self._snapshot.can_reverify_at(now_s, self._target_valid_for_s):
            return self._record_failure(
                ConnectedFailureCode.WRONG_LINK,
                "fresh same-vehicle SiK selection and a USB-verified mission are required",
            )
        target = self._snapshot.selected_target
        package = self._snapshot.expected_package
        telemetry = self._snapshot.telemetry
        assert target is not None and package is not None
        if telemetry is None or not telemetry.command_gate_fresh(now_s):
            return self._record_failure(
                ConnectedFailureCode.TELEMETRY_UNAVAILABLE,
                "fresh selected-target telemetry is required before SiK re-verification",
            )
        try:
            readback = port.download_mission(target, cancellation=cancellation)
        except ConnectedPortFailure as error:
            return self._record_port_failure(error)
        comparison = verify_native_readback(package, readback.items)
        if not comparison.verified:
            mismatches = comparison.home.mismatches + comparison.mission.mismatches
            self._snapshot = replace(
                self._snapshot,
                revision=self._snapshot.revision + 1,
                reconnect_comparison=comparison,
                verification_state=ConnectedVerificationState.MISMATCH,
                failure=ConnectedFailure(
                    ConnectedFailureCode.READBACK_MISMATCH,
                    "SiK readback did not match the USB-verified native mission",
                    mismatches=mismatches,
                ),
            )
            return self._snapshot
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            onboard=readback,
            reconnect_comparison=comparison,
            verification_state=ConnectedVerificationState.SIK_VERIFIED,
            failure=None,
        )
        return self._snapshot

    def _authoritative_home(
        self, target: ConnectedTarget, *, now_s: float
    ) -> HomeSnapshot | HomeUnresolved:
        telemetry = self._snapshot.telemetry
        if telemetry is None:
            return HomeUnresolved(HomeUnresolvedReason.UNAVAILABLE, "home telemetry is unavailable")
        if not telemetry.link_connected:
            return HomeUnresolved(
                HomeUnresolvedReason.UNCONNECTED,
                "home observation belongs to a disconnected link",
                target.vehicle,
            )
        if telemetry.vehicle_identity != target.vehicle.value:
            return HomeUnresolved(
                HomeUnresolvedReason.WRONG_VEHICLE,
                "home telemetry belongs to a different vehicle",
                VehicleIdentity(telemetry.vehicle_identity),
            )
        home_signal = telemetry.home
        freshness = home_signal.freshness(now_s)
        if home_signal.value is None or home_signal.observed_at_s is None:
            return HomeUnresolved(
                HomeUnresolvedReason.UNAVAILABLE, "HOME_POSITION is unavailable", target.vehicle
            )
        if freshness is not TelemetryFreshness.FRESH:
            return HomeUnresolved(
                HomeUnresolvedReason.STALE, "HOME_POSITION is stale", target.vehicle
            )
        home = home_signal.value
        altitude_cm = round(home.altitude_msl_m * 100.0)
        return HomeSnapshot(
            vehicle=target.vehicle,
            latitude_e7=round(home.point.latitude_deg * 10_000_000),
            longitude_e7=round(home.point.longitude_deg * 10_000_000),
            altitude_m=altitude_cm / 100.0,
            captured_at_s=home_signal.observed_at_s,
            valid_for_s=home_signal.valid_for_s,
            authoritative=True,
        )

    def _record_upload_gate_failure(self, now_s: float) -> ConnectedMissionSnapshot:
        target = self._snapshot.selected_target
        if self._snapshot.compiled is None:
            return self._record_failure(
                ConnectedFailureCode.COMPILED_MISSION_REQUIRED,
                "a current compiled mission is required",
            )
        if self._snapshot.onboard is None or not self._snapshot.replacement_confirmed:
            return self._record_failure(
                ConnectedFailureCode.REPLACEMENT_REQUIRED,
                "inspect and explicitly confirm onboard mission replacement",
            )
        if target is None or self._snapshot.link_kind is not TelemetryLinkKind.USB:
            return self._record_failure(
                ConnectedFailureCode.WRONG_LINK, "mission upload requires a selected USB target"
            )
        if target.armed:
            return self._record_failure(
                ConnectedFailureCode.ARMED, "mission upload requires a disarmed target"
            )
        if not target.is_fresh(now_s, self._target_valid_for_s):
            return self._record_failure(
                ConnectedFailureCode.STALE_IDENTITY, "selected target heartbeat is stale"
            )
        return self._record_failure(
            ConnectedFailureCode.DISCONNECTED, "mission upload link is disconnected"
        )

    def _require_selected_port(
        self,
        port: ConnectedVehiclePort,
        required_kind: TelemetryLinkKind | None = None,
    ) -> ConnectedTarget | None:
        target = self._snapshot.selected_target
        if not self._snapshot.link_connected or not port.is_connected():
            self._record_failure(ConnectedFailureCode.DISCONNECTED, "connection is not open")
            return None
        if self._snapshot.link_kind is not port.link_kind or (
            required_kind is not None and port.link_kind is not required_kind
        ):
            self._record_failure(
                ConnectedFailureCode.WRONG_LINK,
                f"operation requires {required_kind.value if required_kind else 'selected'} link",
            )
            return None
        if target is None:
            self._record_failure(
                ConnectedFailureCode.WRONG_IDENTITY, "select exactly one target first"
            )
            return None
        return target

    def _record_port_failure(self, error: ConnectedPortFailure) -> ConnectedMissionSnapshot:
        return self._record_failure(
            error.code,
            error.detail,
            source_code=error.source_code,
            mismatches=error.mismatches,
        )

    def _record_failure(
        self,
        code: ConnectedFailureCode,
        detail: str,
        *,
        source_code: str | None = None,
        mismatches: tuple[FieldMismatch, ...] = (),
    ) -> ConnectedMissionSnapshot:
        verification = self._snapshot.verification_state
        if code is ConnectedFailureCode.READBACK_MISMATCH:
            verification = ConnectedVerificationState.MISMATCH
        self._snapshot = replace(
            self._snapshot,
            revision=self._snapshot.revision + 1,
            verification_state=verification,
            failure=ConnectedFailure(code, detail, source_code, tuple(mismatches)),
        )
        return self._snapshot


def _route_from_package(package: NativeMissionPackage) -> TelemetryRoute:
    route_points: list[TelemetryRoutePoint] = []
    home_point = TelemetryPoint(
        package.home.latitude_e7 / 10_000_000,
        package.home.longitude_e7 / 10_000_000,
    )
    coordinate_commands = {
        int(MissionCommand.NAV_WAYPOINT),
        int(MissionCommand.NAV_LOITER_TIME),
        int(MissionCommand.NAV_LOITER_TURNS),
        int(MissionCommand.NAV_LAND),
    }
    for item in package.items:
        if item.sequence == 0:
            point = home_point
            label = "Home"
        elif item.command == int(MissionCommand.NAV_TAKEOFF):
            point = home_point
            label = "Takeoff"
        elif item.command in coordinate_commands:
            point = TelemetryPoint(
                item.latitude_e7 / 10_000_000,
                item.longitude_e7 / 10_000_000,
            )
            label = MissionCommand(item.command).name.replace("NAV_", "").replace("_", " ").title()
        else:
            continue
        route_points.append(TelemetryRoutePoint(item.sequence, point, label))
    return TelemetryRoute(tuple(route_points))
