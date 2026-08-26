"""Main desktop window for the complete offline mission workflow."""

import logging

from PySide6.QtWidgets import QMainWindow, QSizePolicy, QTabWidget

from skywriter.application import (
    ApplicationSnapshot,
    ApplicationStarted,
    ViewName,
    ViewSelected,
    reduce_snapshot,
)
from skywriter.config import DEFAULT_CONFIG, ApplicationConfig
from skywriter.result import is_ok
from skywriter.ui.connected import ConnectedMissionWidget
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

    def __init__(self, config: ApplicationConfig = DEFAULT_CONFIG) -> None:
        super().__init__()
        self._snapshot = ApplicationSnapshot()
        started = reduce_snapshot(self._snapshot, ApplicationStarted())
        if is_ok(started):
            self._snapshot = started.value

        self.setObjectName("mainWindow")
        self.setWindowTitle(f"{config.name} {config.version}")
        self.setMinimumSize(1100, 720)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("primaryViews")
        self._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._mission_workspace = OfflineMissionWorkspace()
        self._tabs.addTab(self._mission_workspace, "Builder")
        self._connected_mission = ConnectedMissionWidget()
        self._tabs.addTab(self._connected_mission, "Connected")
        self._preflight_telemetry = PreflightTelemetryWidget()
        self._flight_telemetry = FlightTelemetryWidget()
        self._tabs.addTab(self._preflight_telemetry, "Preflight")
        self._tabs.addTab(self._flight_telemetry, "Flight")
        self._tabs.currentChanged.connect(self._select_view)
        self.setCentralWidget(self._tabs)
        self.statusBar().showMessage("Offline mission builder ready — no vehicle link")

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
