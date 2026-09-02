"""Screen-level installed-shell gates and bounded-layout regressions."""

from typing import TypeVar, cast

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from skywriter.application import OfflineMissionService
from skywriter.domain.mission import GeoPoint, LandAction, MissionSettings
from skywriter.infrastructure.json_repository import JsonMissionRepository
from skywriter.infrastructure.mavlink.connection import vehicle_io_audit_snapshot
from skywriter.infrastructure.serial_ports import (
    SerialPortInfo,
    StaticSerialPortEnumerator,
)
from skywriter.main import create_application
from skywriter.ui import MainWindow
from skywriter.ui.installed_acceptance import _is_exact_or_closest_available
from skywriter.ui.offline_workspace import OfflineMissionWorkspace

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


def test_installed_shell_binds_connected_and_preflight_but_keeps_flight_unbound() -> None:
    app = create_application(["skywriter-installed-shell-gates"])
    window = MainWindow(
        serial_port_enumerator=StaticSerialPortEnumerator(
            (SerialPortInfo("COM42", "Acceptance serial fixture"),)
        )
    )
    window.resize(1366, 768)
    window.show()
    tabs = child(window, QTabWidget, "primaryViews")
    before = vehicle_io_audit_snapshot()

    tabs.setCurrentIndex(1)
    app.processEvents()
    assert not child(window, QLabel, "connectedInteractionGate").isVisible()
    refresh = child(window, QPushButton, "refreshSerialPortsButton")
    ports = child(window, QComboBox, "serialPortSelection")
    discover = child(window, QPushButton, "discoverSelectedLinkButton")
    assert refresh.isEnabled()
    assert not discover.isEnabled()
    refresh.click()
    for _ in range(100):
        app.processEvents()
        if ports.count() == 2 and not window.connected_controller.busy:
            break
        QTest.qWait(10)
    assert ports.count() == 2
    assert ports.currentData() is None
    assert "COM42" in ports.itemText(1)
    assert "Acceptance serial fixture" in ports.itemText(1)
    assert vehicle_io_audit_snapshot() == before

    tabs.setCurrentIndex(2)
    app.processEvents()
    preflight_gate = child(window, QLabel, "preflightInteractionGate")
    readiness = window.preflight_controller.readiness_service.snapshot
    arm = window.preflight_controller.arm_service.snapshot
    assert not preflight_gate.isVisible()
    assert not window.connected_controller.service.snapshot.link_connected
    assert window.connected_controller.service.snapshot.selected_target is None
    assert not readiness.application_gate_ready
    assert not arm.request_available
    # The dedicated request surface is production-bound, but this acceptance does
    # not click it. Review and normal Arm stay closed until current native evidence.
    assert child(window, QPushButton, "requestNativePrearmButton").isEnabled()
    assert not child(window, QCheckBox, "acknowledgeNativePrearmReview").isEnabled()
    assert not child(window, QPushButton, "normalArmButton").isEnabled()
    assert vehicle_io_audit_snapshot() == before

    tabs.setCurrentIndex(3)
    app.processEvents()
    flight_gate = child(window, QLabel, "flightInteractionGate")
    assert flight_gate.isVisible()
    assert "Task 111" in flight_gate.text()
    assert "Flight remains deliberately unbound" in flight_gate.text()
    assert not hasattr(window, "flight_controller")
    for name in (
        "nativeAutoStartButton",
        "nativePauseButton",
        "nativeResumeButton",
        "landHereNowButton",
        "landHereNowConfirmButton",
        "landHereNowCancelButton",
    ):
        control = child(window, QPushButton, name)
        assert not control.isEnabled()
        assert control.toolTip() == flight_gate.text()
    assert vehicle_io_audit_snapshot() == before
    window.close()


def test_main_window_feeds_current_builder_compile_and_invalidates_on_edit() -> None:
    create_application(["skywriter-connected-builder-feed"])
    service = OfflineMissionService(JsonMissionRepository())
    service.update_settings(MissionSettings(15.0, 5.0, True))
    service.append_action(LandAction(GeoPoint(38.8895, -77.0353), 8.0))
    service.compile_preview()
    workspace = OfflineMissionWorkspace(service)
    window = MainWindow(
        mission_workspace=workspace,
        serial_port_enumerator=StaticSerialPortEnumerator(()),
    )

    connected = window.connected_controller.service.snapshot
    assert connected.compiled == service.snapshot.compiled_preview
    assert connected.mission_revision == service.snapshot.revision

    workspace.new_mission()
    invalidated = window.connected_controller.service.snapshot
    assert invalidated.compiled is None
    assert invalidated.mission_revision == workspace.service.snapshot.revision
    assert invalidated.expected_package is None
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
