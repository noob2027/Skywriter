"""Main desktop window for the complete offline mission workflow."""

import logging

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea, QSizePolicy, QTabWidget, QWidget

from skywriter.application import (
    ApplicationSnapshot,
    ApplicationStarted,
    ViewName,
    ViewSelected,
    reduce_snapshot,
)
from skywriter.application.connected import ConnectedMissionService
from skywriter.config import DEFAULT_CONFIG, ApplicationConfig
from skywriter.infrastructure.mavlink.connection import Clock
from skywriter.infrastructure.serial_ports import SerialPortEnumerator
from skywriter.result import is_ok
from skywriter.ui.connected import ConnectedMissionWidget
from skywriter.ui.connected_controller import (
    ConnectedMissionController,
    LinkFactory,
    PortFactory,
)
from skywriter.ui.flight import FlightTelemetryWidget
from skywriter.ui.offline_workspace import OfflineMissionWorkspace
from skywriter.ui.preflight import PreflightTelemetryWidget

LOGGER = logging.getLogger("skywriter.ui")


class MainWindow(QMainWindow):
    """Top-level SKYWriter window with the production offline Builder."""

    _VIEW_ORDER = (
        ViewName.BUILDER,
        ViewName.CONNECTED,
        ViewName.PREFLIGHT,
        ViewName.FLIGHT,
    )

    def __init__(
        self,
        config: ApplicationConfig = DEFAULT_CONFIG,
        *,
        mission_workspace: OfflineMissionWorkspace | None = None,
        connected_service: ConnectedMissionService | None = None,
        serial_port_enumerator: SerialPortEnumerator | None = None,
        connected_link_factory: LinkFactory | None = None,
        connected_port_factory: PortFactory | None = None,
        connected_clock: Clock | None = None,
        connected_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self._snapshot = ApplicationSnapshot()
        started = reduce_snapshot(self._snapshot, ApplicationStarted())
        if is_ok(started):
            self._snapshot = started.value

        self.setObjectName("mainWindow")
        self.setWindowTitle(f"{config.name} {config.version}")
        self.setMinimumSize(900, 580)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("primaryViews")
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._mission_workspace = mission_workspace or OfflineMissionWorkspace()
        self._tabs.addTab(self._mission_workspace, "Builder")
        self._connected_mission = ConnectedMissionWidget()
        self._connected_controller = ConnectedMissionController(
            self._connected_mission,
            service=connected_service,
            serial_ports=serial_port_enumerator,
            link_factory=connected_link_factory,
            port_factory=connected_port_factory,
            clock=connected_clock,
            pool=connected_pool,
        )
        self._mission_workspace.snapshot_changed.connect(self._connected_controller.sync_mission)
        self._connected_controller.sync_mission(self._mission_workspace.service.snapshot)
        self._tabs.addTab(_scroll_view(self._connected_mission, "connectedScrollView"), "Connected")
        self._preflight_telemetry = PreflightTelemetryWidget()
        self._flight_telemetry = FlightTelemetryWidget()
        self._tabs.addTab(
            _scroll_view(self._preflight_telemetry, "preflightScrollView"), "Preflight"
        )
        self._tabs.addTab(_scroll_view(self._flight_telemetry, "flightScrollView"), "Flight")
        command_unavailable = (
            "Unavailable in Task 110: production bindings for Preflight, Arm, AUTO, "
            "Pause/Resume, and Land Here Now remain disabled. The Connected tab is limited to "
            "explicit serial selection, mission transfer/readback, and receive-only telemetry."
        )
        self._preflight_telemetry.set_interaction_unavailable(command_unavailable)
        self._flight_telemetry.set_interaction_unavailable(command_unavailable)
        self._tabs.currentChanged.connect(self._select_view)
        self.setCentralWidget(self._tabs)
        self.statusBar().showMessage(
            "Builder ready — serial access occurs only after explicit Connected selection"
        )

    @property
    def snapshot(self) -> ApplicationSnapshot:
        """Return the current immutable application snapshot."""

        return self._snapshot

    @property
    def mission_workspace(self) -> OfflineMissionWorkspace:
        """Return the production offline workflow mounted in the Builder tab."""

        return self._mission_workspace

    @property
    def preflight_telemetry(self) -> PreflightTelemetryWidget:
        return self._preflight_telemetry

    @property
    def connected_mission(self) -> ConnectedMissionWidget:
        return self._connected_mission

    @property
    def connected_controller(self) -> ConnectedMissionController:
        return self._connected_controller

    @property
    def flight_telemetry(self) -> FlightTelemetryWidget:
        return self._flight_telemetry

    def _select_view(self, index: int) -> None:
        if not 0 <= index < len(self._VIEW_ORDER):
            LOGGER.warning("Ignoring unknown view index", extra={"view_index": index})
            return

        result = reduce_snapshot(self._snapshot, ViewSelected(self._VIEW_ORDER[index]))
        if is_ok(result):
            self._snapshot = result.value
            LOGGER.info("Selected application view", extra={"view": self._snapshot.active_view})

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        self._connected_controller.shutdown()
        super().closeEvent(event)


def _scroll_view(widget: QWidget, name: str) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName(name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(widget)
    return scroll
