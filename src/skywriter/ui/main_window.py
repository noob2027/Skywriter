"""Main desktop window for the complete offline mission workflow."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QTabWidget, QVBoxLayout, QWidget

from skywriter.application import (
    ApplicationSnapshot,
    ApplicationStarted,
    ViewName,
    ViewSelected,
    reduce_snapshot,
)
from skywriter.config import DEFAULT_CONFIG, ApplicationConfig
from skywriter.result import is_ok
from skywriter.ui.offline_workspace import OfflineMissionWorkspace

LOGGER = logging.getLogger("skywriter.ui")


class PlaceholderView(QWidget):
    """A clearly labeled placeholder for a later bounded task."""

    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName(f"{title.lower()}View")

        heading = QLabel(title)
        heading.setObjectName("viewHeading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("font-size: 26px; font-weight: 600;")

        detail = QLabel(description)
        detail.setObjectName("viewDescription")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        detail.setStyleSheet("color: #5d6673; font-size: 14px;")

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addStretch()


class MainWindow(QMainWindow):
    """Top-level SKYWriter window with the production offline Builder."""

    _VIEW_ORDER = (ViewName.BUILDER, ViewName.PREFLIGHT, ViewName.FLIGHT)

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
        self._tabs.addTab(
            PlaceholderView("Preflight", "Readiness workspace placeholder — Task 001 foundation."),
            "Preflight",
        )
        self._tabs.addTab(
            PlaceholderView("Flight", "Operations workspace placeholder — Task 001 foundation."),
            "Flight",
        )
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

    def _select_view(self, index: int) -> None:
        if not 0 <= index < len(self._VIEW_ORDER):
            LOGGER.warning("Ignoring unknown view index", extra={"view_index": index})
            return

        result = reduce_snapshot(self._snapshot, ViewSelected(self._VIEW_ORDER[index]))
        if is_ok(result):
            self._snapshot = result.value
            LOGGER.info("Selected application view", extra={"view": self._snapshot.active_view})
