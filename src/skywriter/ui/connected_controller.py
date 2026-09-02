"""Production composition for explicit, worker-owned connected mission operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from skywriter.application.arm import (
    NormalArmCommandResult,
    NormalArmService,
    NormalArmSnapshot,
    NormalArmState,
)
from skywriter.application.connected import (
    CancellationView,
    ConnectedFailureCode,
    ConnectedMissionService,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVehiclePort,
)
from skywriter.application.mission_service import OfflineMissionSnapshot
from skywriter.application.prearm import (
    PrearmCommandResult,
    PrearmReadinessService,
    PrearmReadinessSnapshot,
    PrearmRequestState,
)
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.infrastructure.mavlink.arm import (
    NativeNormalArmGateway,
    NormalArmLink,
)
from skywriter.infrastructure.mavlink.connected import ConnectedMavlinkPort
from skywriter.infrastructure.mavlink.connection import (
    CancellationToken,
    Clock,
    MissionLink,
    MonotonicClock,
    TransportDescriptor,
    TransportKind,
    TransportOpenError,
    TransportOpenFailureCode,
    open_installed_session,
)
from skywriter.infrastructure.mavlink.prearm import NativePrearmGateway, PrearmCommandLink
from skywriter.infrastructure.serial_ports import (
    PySerialPortEnumerator,
    SerialEnumerationError,
    SerialPortEnumerator,
    SerialPortInfo,
)
from skywriter.ui.connected import (
    ConnectedMissionWidget,
    DisconnectRequested,
    DiscoverSikRequested,
    DiscoverUsbRequested,
    InspectMissionRequested,
    RefreshPortsRequested,
    ReplacementConfirmationRequested,
    ReverifyMissionRequested,
    TargetSelectionRequested,
    TelemetryRefreshRequested,
    UploadVerificationRequested,
)

DISCOVERY_TIMEOUT_S = 3.0
TELEMETRY_TIMEOUT_S = 5.0


class InstalledVehicleSession(Protocol):
    @property
    def mission_link(self) -> MissionLink: ...

    @property
    def prearm_link(self) -> PrearmCommandLink: ...

    @property
    def normal_arm_link(self) -> NormalArmLink: ...

    def close(self) -> None: ...


LinkFactory = Callable[[TransportDescriptor], InstalledVehicleSession]
PortFactory = Callable[[MissionLink, Clock], ConnectedVehiclePort]


@dataclass(frozen=True, slots=True)
class _PortsResult:
    ports: tuple[SerialPortInfo, ...]


@dataclass(frozen=True, slots=True)
class _OpenResult:
    snapshot: ConnectedMissionSnapshot
    session: InstalledVehicleSession | None = None
    port: ConnectedVehiclePort | None = None


@dataclass(frozen=True, slots=True)
class _SnapshotResult:
    snapshot: ConnectedMissionSnapshot
    close_connection: bool = False


@dataclass(frozen=True, slots=True)
class _DisconnectedResult:
    snapshot: ConnectedMissionSnapshot


@dataclass(frozen=True, slots=True)
class _ClosedFailureResult:
    snapshot: ConnectedMissionSnapshot


@dataclass(frozen=True, slots=True)
class _PrearmResult:
    snapshot: PrearmReadinessSnapshot


@dataclass(frozen=True, slots=True)
class _ArmResult:
    snapshot: NormalArmSnapshot
    connected: ConnectedMissionSnapshot


class _WorkerSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _ConnectedOperation(QRunnable):
    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._operation = operation
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation()
        except Exception as error:  # pragma: no cover - defensive Qt boundary
            self.signals.failed.emit(str(error) or type(error).__name__)
            return
        self.signals.completed.emit(result)


class ConnectedMissionController(QObject):
    """Own exactly one link and one cancellable blocking operation at a time."""

    snapshot_ready = Signal(object)
    busy_changed = Signal(bool, str)
    prearm_snapshot_ready = Signal(object)
    arm_snapshot_ready = Signal(object)

    def __init__(
        self,
        widget: ConnectedMissionWidget,
        *,
        service: ConnectedMissionService | None = None,
        serial_ports: SerialPortEnumerator | None = None,
        link_factory: LinkFactory | None = None,
        port_factory: PortFactory | None = None,
        clock: Clock | None = None,
        pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._service = service or ConnectedMissionService()
        self._serial_ports = serial_ports or PySerialPortEnumerator()
        self._link_factory = link_factory or cast(LinkFactory, open_installed_session)
        self._port_factory = port_factory or _default_port_factory
        self._clock = clock or MonotonicClock()
        self._pool = pool or QThreadPool.globalInstance()
        self._active_session: InstalledVehicleSession | None = None
        self._active_port: ConnectedVehiclePort | None = None
        self._active_token: CancellationToken | None = None
        self._active_worker: _ConnectedOperation | None = None
        self._busy = False
        self._disconnect_after_operation = False
        self._pending_mission: OfflineMissionSnapshot | None = None
        self._armed_interlock = False
        self._shutting_down = False
        self._widget.intent_emitted.connect(self._handle_intent)
        self._render(self._service.snapshot)

    @property
    def service(self) -> ConnectedMissionService:
        return self._service

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def armed_interlock(self) -> bool:
        """Whether this open session has confirmed Armed and must accept no more actions."""

        return self._armed_interlock

    @property
    def clock(self) -> Clock:
        """Expose the shared monotonic time source for synchronous gate rendering."""

        return self._clock

    def request_native_prearm(self, readiness: PrearmReadinessService) -> None:
        """Serialize exactly Task 100's request on the installed session."""

        if not isinstance(readiness, PrearmReadinessService):
            raise TypeError("readiness must be a PrearmReadinessService")
        if self._reject_if_busy():
            return
        if self._armed_interlock:
            self.prearm_snapshot_ready.emit(
                readiness.synchronize_context(
                    self._service.snapshot,
                    now_s=self._clock.now(),
                )
            )
            return
        connected = self._service.snapshot
        session = self._active_session
        if session is None or connected.link_kind is not TelemetryLinkKind.SIK:
            token = CancellationToken()
            snapshot = readiness.request_prearm_checks(
                _UnavailablePrearmGateway(),
                connected,
                now_s=self._clock.now(),
                cancellation=token,
            )
            self.prearm_snapshot_ready.emit(snapshot)
            return
        token = CancellationToken()
        gateway = NativePrearmGateway(session.prearm_link, clock=self._clock)
        self._active_token = token
        self._submit(
            lambda: _PrearmResult(
                readiness.request_prearm_checks(
                    gateway,
                    connected,
                    now_s=self._clock.now(),
                    cancellation=token,
                )
            ),
            "Requesting native pre-arm checks and awaiting the exact acknowledgment…",
        )

    def request_normal_arm(
        self,
        arm: NormalArmService,
        readiness: PrearmReadinessService,
    ) -> None:
        """Serialize exactly Task 101's normal Arm on the installed session."""

        if not isinstance(arm, NormalArmService):
            raise TypeError("arm must be a NormalArmService")
        if not isinstance(readiness, PrearmReadinessService):
            raise TypeError("readiness must be a PrearmReadinessService")
        if self._reject_if_busy():
            return
        if self._armed_interlock:
            self.arm_snapshot_ready.emit(arm.snapshot)
            return
        connected = self._service.snapshot
        session = self._active_session
        if session is None or connected.link_kind is not TelemetryLinkKind.SIK:
            token = CancellationToken()
            snapshot = arm.request_normal_arm(
                _UnavailableNormalArmGateway(),
                connected,
                readiness,
                now_s=self._clock.now(),
                command_channel_idle=True,
                cancellation=token,
            )
            self.arm_snapshot_ready.emit(snapshot)
            return
        token = CancellationToken()
        gateway = NativeNormalArmGateway(session.normal_arm_link, clock=self._clock)
        self._active_token = token
        port = self._active_port
        if port is None:  # pragma: no cover - invariant defended by session ownership
            raise RuntimeError("installed session is missing its connected port")

        def operation() -> object:
            arm_snapshot = arm.request_normal_arm(
                gateway,
                connected,
                readiness,
                now_s=self._clock.now(),
                # The shared controller acquired the sole transaction slot
                # before this worker started; that lease is the idle proof.
                command_channel_idle=True,
                cancellation=token,
            )
            connected_snapshot = self._service.snapshot
            if arm_snapshot.state is NormalArmState.ARMED:
                # The gateway consumes the heartbeat proving Armed. Collect a later
                # receive-only snapshot on the same worker/session so stale disarmed
                # application evidence cannot authorize another request.
                connected_snapshot = self._service.refresh_telemetry(
                    port,
                    duration_s=TELEMETRY_TIMEOUT_S,
                    cancellation=token,
                )
            return _ArmResult(arm_snapshot, connected_snapshot)

        self._submit(
            operation,
            "Requesting normal Arm and awaiting selected-target telemetry proof…",
        )

    def sync_mission(self, snapshot: OfflineMissionSnapshot) -> None:
        """Feed the authoritative Builder revision, cancelling stale active work."""

        if not isinstance(snapshot, OfflineMissionSnapshot):
            raise TypeError("snapshot must be an OfflineMissionSnapshot")
        if self._busy:
            self._pending_mission = snapshot
            if self._active_token is not None:
                self._active_token.cancel()
            detail = "Mission changed — cancelling stale connected work…"
            self._widget.set_busy(True, detail)
            self.busy_changed.emit(True, detail)
            return
        self._apply_mission(snapshot)

    def shutdown(self) -> None:
        """Cancel work and release the selected serial handle during application close."""

        self._shutting_down = True
        if self._active_token is not None:
            self._active_token.cancel()
        session = self._active_session
        self._active_session = None
        self._active_port = None
        if session is not None:
            session.close()
        self._service.disconnect()

    @Slot(object)
    def _handle_intent(self, intent: object) -> None:
        if isinstance(intent, RefreshPortsRequested):
            self._refresh_ports()
        elif isinstance(intent, DiscoverUsbRequested):
            self._open_and_discover(intent.endpoint, TransportKind.USB, intent.baudrate)
        elif isinstance(intent, DiscoverSikRequested):
            self._open_and_discover(intent.endpoint, TransportKind.SIK, intent.baudrate)
        elif isinstance(intent, TargetSelectionRequested):
            self._select_target(intent.system_id, intent.component_id)
        elif isinstance(intent, InspectMissionRequested):
            self._inspect_mission()
        elif isinstance(intent, ReplacementConfirmationRequested):
            if self._reject_if_busy():
                return
            self._render(self._service.confirm_replacement(intent.confirmed))
        elif isinstance(intent, UploadVerificationRequested):
            self._upload_and_verify()
        elif isinstance(intent, TelemetryRefreshRequested):
            self._refresh_telemetry()
        elif isinstance(intent, ReverifyMissionRequested):
            self._reverify_mission()
        elif isinstance(intent, DisconnectRequested):
            self._disconnect()

    def _refresh_ports(self) -> None:
        if self._reject_if_busy() or self._active_session is not None:
            return

        def operation() -> object:
            try:
                return _PortsResult(self._serial_ports.enumerate())
            except SerialEnumerationError as error:
                return _SnapshotResult(
                    self._service.connection_failed(
                        ConnectedFailureCode.SERIAL_ENUMERATION,
                        str(error),
                        source_code="pyserial_enumeration",
                    )
                )

        self._submit(operation, "Refreshing Windows serial ports…")

    def _open_and_discover(self, endpoint: str, kind: TransportKind, baudrate: int) -> None:
        if self._reject_if_busy() or self._active_session is not None:
            return
        descriptor = TransportDescriptor(endpoint, kind, baudrate)
        token = CancellationToken()

        def operation() -> object:
            try:
                session = self._link_factory(descriptor)
            except TransportOpenError as error:
                return _OpenResult(
                    self._service.connection_failed(
                        _connected_open_code(error.code),
                        error.detail,
                        source_code=error.code.value,
                    )
                )
            try:
                port = self._port_factory(session.mission_link, self._clock)
                snapshot = self._service.discover(
                    port,
                    duration_s=DISCOVERY_TIMEOUT_S,
                    cancellation=token,
                )
                if token.is_cancelled():
                    session.close()
                    return _OpenResult(
                        self._service.connection_failed(
                            ConnectedFailureCode.CANCELLED,
                            "Vehicle discovery was cancelled and the serial port was closed.",
                        )
                    )
                if snapshot.failure is not None:
                    session.close()
                    return _OpenResult(
                        self._service.connection_failed(
                            snapshot.failure.code
                            if snapshot.failure.code
                            in {
                                ConnectedFailureCode.CANCELLED,
                                ConnectedFailureCode.DISCONNECTED,
                            }
                            else ConnectedFailureCode.PORT_OPEN_FAILED,
                            snapshot.failure.detail,
                            source_code=snapshot.failure.source_code,
                        )
                    )
                if not snapshot.candidates:
                    session.close()
                    return _OpenResult(
                        self._service.connection_failed(
                            ConnectedFailureCode.NO_HEARTBEAT,
                            f"No vehicle heartbeat was received on {endpoint} at {baudrate} baud "
                            f"within {DISCOVERY_TIMEOUT_S:g} seconds. Check the selected port, "
                            "link kind, baud, cable or radio, and confirm Mission Planner is "
                            "closed.",
                            source_code="discovery_timeout",
                        )
                    )
                return _OpenResult(snapshot, session, port)
            except Exception:
                session.close()
                raise

        self._active_token = token
        self._submit(operation, f"Opening {endpoint} at {baudrate} baud and discovering vehicles…")

    def _select_target(self, system_id: int, component_id: int) -> None:
        port = self._require_port()
        if port is None:
            return
        token = CancellationToken()

        def operation() -> object:
            refreshed = self._service.discover(
                port,
                duration_s=DISCOVERY_TIMEOUT_S,
                cancellation=token,
            )
            if refreshed.failure is not None:
                return _SnapshotResult(refreshed, close_connection=True)
            selected = self._service.select_target(
                system_id,
                component_id,
                now_s=self._clock.now(),
            )
            close = selected.failure is not None and selected.failure.code in {
                ConnectedFailureCode.CANCELLED,
                ConnectedFailureCode.DISCONNECTED,
            }
            return _SnapshotResult(selected, close_connection=close)

        self._active_token = token
        self._submit(operation, "Confirming a fresh heartbeat from the selected vehicle…")

    def _inspect_mission(self) -> None:
        port = self._require_port()
        if port is None:
            return
        token = CancellationToken()
        self._active_token = token
        self._submit(
            lambda: _operation_result(self._service.inspect_onboard(port, cancellation=token)),
            "Downloading the complete onboard mission for inspection…",
        )

    def _upload_and_verify(self) -> None:
        port = self._require_port()
        if port is None:
            return
        token = CancellationToken()

        def operation() -> object:
            prepared = self._service.refresh_telemetry(
                port,
                duration_s=TELEMETRY_TIMEOUT_S,
                cancellation=token,
                require_home=True,
            )
            if prepared.failure is not None:
                return _operation_result(prepared)
            return _operation_result(
                self._service.upload_and_verify(
                    port,
                    now_s=self._clock.now(),
                    cancellation=token,
                )
            )

        self._active_token = token
        self._submit(operation, "Reading fresh identity and Home, then uploading and verifying…")

    def _refresh_telemetry(self) -> None:
        port = self._require_port()
        if port is None:
            return
        token = CancellationToken()
        self._active_token = token
        self._submit(
            lambda: _operation_result(
                self._service.refresh_telemetry(
                    port,
                    duration_s=TELEMETRY_TIMEOUT_S,
                    cancellation=token,
                )
            ),
            "Collecting fresh receive-only telemetry…",
        )

    def _reverify_mission(self) -> None:
        port = self._require_port()
        if port is None:
            return
        token = CancellationToken()

        def operation() -> object:
            refreshed = self._service.refresh_telemetry(
                port,
                duration_s=TELEMETRY_TIMEOUT_S,
                cancellation=token,
            )
            if refreshed.failure is not None:
                return _operation_result(refreshed)
            return _operation_result(
                self._service.reverify_over_sik(
                    port,
                    now_s=self._clock.now(),
                    cancellation=token,
                )
            )

        self._active_token = token
        self._submit(operation, "Collecting fresh SiK telemetry and comparing the full mission…")

    def _disconnect(self) -> None:
        if self._busy:
            self._disconnect_after_operation = True
            if self._active_token is not None:
                self._active_token.cancel()
            detail = "Cancelling the active operation before closing the link…"
            self._widget.set_busy(True, detail)
            self.busy_changed.emit(True, detail)
            return
        session = self._active_session
        if session is None:
            self._render(self._service.disconnect())
            return

        def operation() -> object:
            try:
                session.close()
            finally:
                snapshot = self._service.disconnect()
            return _DisconnectedResult(snapshot)

        self._submit(operation, "Closing the selected serial link…")

    def _require_port(self) -> ConnectedVehiclePort | None:
        if self._reject_if_busy():
            return None
        if self._active_session is None or self._active_port is None:
            self._render(
                self._service.connection_failed(
                    ConnectedFailureCode.DISCONNECTED,
                    "Open and discover an explicitly selected serial port first.",
                )
            )
            return None
        return self._active_port

    def _reject_if_busy(self) -> bool:
        if not self._busy:
            return False
        self._render(self._service.operation_busy())
        return True

    def _submit(self, operation: Callable[[], object], detail: str) -> None:
        if self._busy:
            self._render(self._service.operation_busy())
            return
        self._busy = True
        self._widget.set_busy(True, detail)
        self.busy_changed.emit(True, detail)
        worker = _ConnectedOperation(operation)
        self._active_worker = worker
        worker.signals.completed.connect(self._complete)
        worker.signals.failed.connect(self._fail)
        self._pool.start(worker)

    @Slot(object)
    def _complete(self, result: object) -> None:
        close_connection = False
        prearm_snapshot: PrearmReadinessSnapshot | None = None
        arm_snapshot: NormalArmSnapshot | None = None
        if isinstance(result, _PortsResult):
            self._widget.set_serial_ports(result.ports)
            snapshot = self._service.snapshot
        elif isinstance(result, _OpenResult):
            snapshot = result.snapshot
            if result.session is not None and result.port is not None:
                self._active_session = result.session
                self._active_port = result.port
                self._armed_interlock = False
        elif isinstance(result, _SnapshotResult):
            snapshot = result.snapshot
            close_connection = result.close_connection
        elif isinstance(result, _DisconnectedResult):
            self._active_session = None
            self._active_port = None
            self._armed_interlock = False
            snapshot = result.snapshot
        elif isinstance(result, _ClosedFailureResult):
            self._active_session = None
            self._active_port = None
            self._armed_interlock = False
            snapshot = result.snapshot
        elif isinstance(result, _PrearmResult):
            prearm_snapshot = result.snapshot
            if result.snapshot.request_state is PrearmRequestState.LINK_LOST:
                snapshot = self._service.connection_failed(
                    ConnectedFailureCode.DISCONNECTED,
                    "The SiK link was lost during the native pre-arm request.",
                )
                close_connection = True
            else:
                snapshot = self._service.snapshot
        elif isinstance(result, _ArmResult):
            arm_snapshot = result.snapshot
            snapshot = result.connected
            if result.snapshot.state is NormalArmState.ARMED:
                self._armed_interlock = True
            if snapshot.failure is not None and snapshot.failure.code in {
                ConnectedFailureCode.CANCELLED,
                ConnectedFailureCode.DISCONNECTED,
                ConnectedFailureCode.PORT_UNAVAILABLE,
            }:
                close_connection = True
            if result.snapshot.state is NormalArmState.LINK_LOST:
                snapshot = self._service.connection_failed(
                    ConnectedFailureCode.DISCONNECTED,
                    "The SiK link was lost during normal Arm; vehicle state is uncertain.",
                )
                close_connection = True
        else:  # pragma: no cover - defensive injected worker boundary
            snapshot = self._service.connection_failed(
                ConnectedFailureCode.PORT_OPEN_FAILED,
                "Connected worker returned an invalid result and the link was closed.",
            )
            close_connection = True
        disconnect_requested = self._disconnect_after_operation
        self._finish()
        if prearm_snapshot is not None:
            self.prearm_snapshot_ready.emit(prearm_snapshot)
        if arm_snapshot is not None:
            self.arm_snapshot_ready.emit(arm_snapshot)
        if self._pending_mission is not None:
            pending = self._pending_mission
            self._pending_mission = None
            self._apply_mission(pending, render=False)
            snapshot = self._service.snapshot
        self._render(snapshot)
        if close_connection:
            self._close_preserving_failure()
        elif disconnect_requested:
            self._disconnect()

    @Slot(str)
    def _fail(self, detail: str) -> None:
        snapshot = self._service.connection_failed(
            ConnectedFailureCode.PORT_OPEN_FAILED,
            f"Connected worker failed: {detail}",
            source_code="worker_exception",
        )
        self._finish()
        self._render(snapshot)
        self._close_preserving_failure()

    def _finish(self) -> None:
        self._active_worker = None
        self._active_token = None
        self._busy = False
        self._disconnect_after_operation = False
        self._widget.set_busy(False)
        self.busy_changed.emit(False, "")

    def _close_preserving_failure(self) -> None:
        session = self._active_session
        if session is None:
            return
        failure = self._service.snapshot.failure
        if failure is not None and failure.code in {
            ConnectedFailureCode.CANCELLED,
            ConnectedFailureCode.DISCONNECTED,
            ConnectedFailureCode.PORT_UNAVAILABLE,
            ConnectedFailureCode.PORT_OPEN_FAILED,
        }:
            snapshot = self._service.connection_failed(
                failure.code,
                failure.detail,
                source_code=failure.source_code,
            )
        else:
            snapshot = self._service.connection_failed(
                ConnectedFailureCode.DISCONNECTED,
                "The serial link was closed after the connected operation failed.",
            )

        def operation() -> object:
            session.close()
            return _ClosedFailureResult(snapshot)

        self._submit(operation, "Closing the failed or cancelled serial link…")

    def _apply_mission(self, snapshot: OfflineMissionSnapshot, *, render: bool = True) -> None:
        if (
            snapshot.compiled_preview is not None
            and snapshot.compiled_revision == snapshot.revision
        ):
            self._service.set_compiled(
                snapshot.compiled_preview,
                mission_revision=snapshot.revision,
            )
        else:
            self._service.mission_changed(mission_revision=snapshot.revision)
        if render:
            self._render(self._service.snapshot)

    def _render(self, snapshot: ConnectedMissionSnapshot) -> None:
        if self._shutting_down:
            return
        self._widget.render_snapshot(snapshot)
        self.snapshot_ready.emit(snapshot)


def _default_port_factory(link: MissionLink, clock: Clock) -> ConnectedVehiclePort:
    return ConnectedMavlinkPort(link, clock=clock)


def _connected_open_code(code: TransportOpenFailureCode) -> ConnectedFailureCode:
    return {
        TransportOpenFailureCode.BUSY: ConnectedFailureCode.PORT_BUSY,
        TransportOpenFailureCode.UNAVAILABLE: ConnectedFailureCode.PORT_UNAVAILABLE,
        TransportOpenFailureCode.FAILED: ConnectedFailureCode.PORT_OPEN_FAILED,
    }[code]


def _operation_result(snapshot: ConnectedMissionSnapshot) -> _SnapshotResult:
    failure = snapshot.failure
    close = failure is not None and failure.code in {
        ConnectedFailureCode.CANCELLED,
        ConnectedFailureCode.DISCONNECTED,
        ConnectedFailureCode.PORT_UNAVAILABLE,
    }
    return _SnapshotResult(snapshot, close_connection=close)


class _UnavailablePrearmGateway:
    """Defensive non-I/O gateway used only while Task 100's gate is closed."""

    def request_prearm_checks(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> PrearmCommandResult:
        del target, target_valid_for_s, cancellation
        raise AssertionError("pre-arm gateway reached without an installed SiK session")


class _UnavailableNormalArmGateway:
    """Defensive non-I/O gateway used only while Task 101's gate is closed."""

    def request_normal_arm(
        self,
        target: ConnectedTarget,
        *,
        target_valid_for_s: float,
        cancellation: CancellationView,
    ) -> NormalArmCommandResult:
        del target, target_valid_for_s, cancellation
        raise AssertionError("normal-Arm gateway reached without an installed SiK session")
