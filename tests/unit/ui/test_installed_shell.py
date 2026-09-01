"""Screen-level installed-shell gates and bounded-layout regressions."""

from typing import TypeVar, cast

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from skywriter.domain.mission import GeoPoint
from skywriter.main import create_application
from skywriter.ui import MainWindow
from skywriter.ui.installed_acceptance import _is_exact_or_closest_available

TWidget = TypeVar("TWidget", bound=QWidget)


def test_installed_acceptance_geometry_allows_exact_or_closest_physical_size() -> None:
    requested = QSize(1498, 758)

    assert _is_exact_or_closest_available(requested, requested, QSize(1920, 1040))
    assert _is_exact_or_closest_available(QSize(1028, 749), requested, QSize(1024, 749))
    assert not _is_exact_or_closest_available(QSize(1028, 600), requested, QSize(1024, 749))


def child(parent: QWidget, widget_type: type[TWidget], name: str) -> TWidget:
    result = parent.findChild(widget_type, name)
    assert result is not None
    return result


def test_installed_shell_hardware_controls_are_fail_closed_with_visible_reason() -> None:
    app = create_application(["skywriter-installed-shell-gates"])
    window = MainWindow()
    window.resize(1366, 768)
    window.show()
    tabs = child(window, QTabWidget, "primaryViews")

    controls_by_tab = {
        1: (
            "discoverUsbButton",
            "discoverSikButton",
            "inspectOnboardMissionButton",
            "uploadAndVerifyButton",
            "refreshConnectedTelemetryButton",
            "reverifyConnectedMissionButton",
            "disconnectConnectedButton",
        ),
        2: ("requestNativePrearmButton", "normalArmButton"),
        3: (
            "nativeAutoStartButton",
            "nativePauseButton",
            "nativeResumeButton",
            "landHereNowButton",
            "landHereNowConfirmButton",
        ),
    }
    gate_by_tab = {
        1: "connectedInteractionGate",
        2: "preflightInteractionGate",
        3: "flightInteractionGate",
    }
    for tab_index, control_names in controls_by_tab.items():
        tabs.setCurrentIndex(tab_index)
        app.processEvents()
        gate = child(window, QLabel, gate_by_tab[tab_index])
        assert gate.isVisible()
        assert "no production vehicle controller" in gate.text()
        assert "no hardware access was attempted" in gate.text()
        for name in control_names:
            control = child(window, QPushButton, name)
            assert not control.isEnabled()
            assert "supervised hardware gate" in control.toolTip()
    window.close()


def test_builder_validation_is_visible_at_owner_and_common_window_sizes() -> None:
    app = create_application(["skywriter-installed-shell-layout"])
    window = MainWindow()
    window.resize(1498, 758)
    window.show()
    workspace = window.mission_workspace
    child(workspace, QLineEdit, "takeoffAltitudeInput").setText("25")
    child(workspace, QLineEdit, "cruiseSpeedInput").setText("6")
    child(workspace, QCheckBox, "obstacleWarningCheck").setChecked(True)
    child(workspace, QPushButton, "confirmTakeoffButton").click()
    workspace.builder.begin_pending(GeoPoint(38.0, -77.0))

    for width, height in ((1498, 758), (1366, 768)):
        window.resize(width, height)
        app.processEvents()
        child(workspace, QPushButton, "confirmActionButton").click()
        app.processEvents()
        error = child(workspace, QLabel, "pendingPointValidationError")
        altitude = child(workspace, QLineEdit, "actionAltitudeInput")
        sidebar = child(workspace, QScrollArea, "missionSidebarScroll")
        viewport = sidebar.viewport()
        viewport_rect = QRect(viewport.mapToGlobal(QPoint()), viewport.size())
        error_rect = QRect(error.mapToGlobal(QPoint()), error.size())
        assert window.size().width() == width
        assert window.size().height() == height
        assert error.isVisible()
        assert viewport_rect.contains(error_rect)
        assert altitude.hasFocus()
        assert sidebar.verticalScrollBar().value() <= sidebar.verticalScrollBar().maximum()

    app = cast(QApplication, QApplication.instance())
    app.processEvents()
    window.close()
