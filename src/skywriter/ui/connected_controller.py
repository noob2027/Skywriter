"""Production composition for explicit, worker-owned connected mission operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from skywriter.application.connected import (
    ConnectedFailureCode,
    ConnectedMissionService,
    ConnectedMissionSnapshot,
    ConnectedVehiclePort,
)
from skywriter.application.mission_service import OfflineMissionSnapshot
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
    open_mission_link,
)
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


class ClosableMissionLink(MissionLink, Protocol):
    def close(self) -> None: ...


LinkFactory = Callable[[TransportDescriptor], ClosableMissionLink]
PortFactory = Callable[[MissionLink, Clock], ConnectedVehiclePort]


@dataclass(frozen=True, slots=True)
class _PortsResult:
    ports: tuple[SerialPortInfo, ...]


@dataclass(frozen=True, slots=True)
class _OpenResult:
    snapshot: ConnectedMissionSnapshot
    link: ClosableMissionLink | None = None
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
        self._link_factory = link_factory or cast(LinkFactory, open_mission_link)
        self._port_factory = port_factory or _default_port_factory
        self._clock = clock or MonotonicClock()
        self._pool = pool or QThreadPool.globalInstance()
        self._active_link: ClosableMissionLink | None = None
        self._active_port: ConnectedVehiclePort | None = None
        self._active_token: CancellationToken | None = None
        self._active_worker: _ConnectedOperation | None = None
        self._busy = False
        self._disconnect_after_operation = False
        self._pending_mission: OfflineMissionSnapshot | None = None
        self._shutting_down = False
        self._widget.intent_emitted.connect(self._handle_intent)
        self._render(self._service.snapshot)

    @property
    def service(self) -> ConnectedMissionService:
        return self._service

    @property
    def busy(self) -> bool:
        return self._busy

    def sync_mission(self, snapshot: OfflineMissionSnapshot) -> None:
        """Feed the authoritative Builder revision, cancelling stale active work."""

        if not isinstance(snapshot, OfflineMissionSnapshot):
            raise TypeError("snapshot must be an OfflineMissionSnapshot")
        if self._busy:
            self._pending_mission = snapshot
            if self._active_token is not None:
                self._active_token.cancel()
            self._widget.set_busy(True, "Mission changed — cancelling stale connected work…")
            return
        self._apply_mission(snapshot)

    def shutdown(self) -> None:
        """Cancel work and release the selected serial handle during application close."""

        self._shutting_down = True
        if self._active_token is not None:
            self._active_token.cancel()
        link = self._active_link
        self._active_link = None
        self._active_port = None
        if link is not None:
            link.close()
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
        if self._reject_if_busy() or self._active_link is not None:
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
        if self._reject_if_busy() or self._active_link is not None:
            return
        descriptor = TransportDescriptor(endpoint, kind, baudrate)
        token = CancellationToken()

        def operation() -> object:
            try:
                link = self._link_factory(descriptor)
            except TransportOpenError as error:
                return _OpenResult(
                    self._service.connection_failed(
                        _connected_open_code(error.code),
                        error.detail,
                        source_code=error.code.value,
                    )
                )
            try:
                port = self._port_factory(link, self._clock)
                snapshot = self._service.discover(
                    port,
                    duration_s=DISCOVERY_TIMEOUT_S,
                    cancellation=token,
                )
                if token.is_cancelled():
                    link.close()
                    return _OpenResult(
                        self._service.connection_failed(
                            ConnectedFailureCode.CANCELLED,
                            "Vehicle discovery was cancelled and the serial port was closed.",
                        )
                    )
                if snapshot.failure is not None:
                    link.close()
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
                    link.close()
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
                return _OpenResult(snapshot, link, port)
            except Exception:
                link.close()
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
            self._widget.set_busy(True, "Cancelling the active operation before closing the link…")
            return
        link = self._active_link
        if link is None:
            self._render(self._service.disconnect())
            return

        def operation() -> object:
            try:
                link.close()
            finally:
                snapshot = self._service.disconnect()
            return _DisconnectedResult(snapshot)

        self._submit(operation, "Closing the selected serial link…")

    def _require_port(self) -> ConnectedVehiclePort | None:
        if self._reject_if_busy():
            return None
        if self._active_link is None or self._active_port is None:
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
        worker = _ConnectedOperation(operation)
        self._active_worker = worker
        worker.signals.completed.connect(self._complete)
        worker.signals.failed.connect(self._fail)
        self._pool.start(worker)

    @Slot(object)
    def _complete(self, result: object) -> None:
        close_connection = False
        if isinstance(result, _PortsResult):
            self._widget.set_serial_ports(result.ports)
            snapshot = self._service.snapshot
        elif isinstance(result, _OpenResult):
            snapshot = result.snapshot
            if result.link is not None and result.port is not None:
                self._active_link = result.link
                self._active_port = result.port
        elif isinstance(result, _SnapshotResult):
            snapshot = result.snapshot
            close_connection = result.close_connection
        elif isinstance(result, _DisconnectedResult):
            self._active_link = None
            self._active_port = None
            snapshot = result.snapshot
        elif isinstance(result, _ClosedFailureResult):
            self._active_link = None
            self._active_port = None
            snapshot = result.snapshot
        else:  # pragma: no cover - defensive injected worker boundary
            snapshot = self._service.connection_failed(
                ConnectedFailureCode.PORT_OPEN_FAILED,
                "Connected worker returned an invalid result and the link was closed.",
            )
            close_connection = True
        disconnect_requested = self._disconnect_after_operation
        self._finish()
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

    def _close_preserving_failure(self) -> None:
        link = self._active_link
        if link is None:
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
            link.close()
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
