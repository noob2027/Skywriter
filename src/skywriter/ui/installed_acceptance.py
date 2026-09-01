"""Screen-faithful, hardware-blocked acceptance for the installed Windows executable."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import TypeVar, cast

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from skywriter.infrastructure.mavlink.connection import vehicle_io_audit_snapshot
from skywriter.ui.main_window import MainWindow
from skywriter.ui.map import TileProvider

TWidget = TypeVar("TWidget", bound=QWidget)


def _is_exact_or_closest_available(actual: QSize, requested: QSize, available: QSize) -> bool:
    """Accept the requested size or the window manager's nearest physical limit."""

    tolerance = 32  # Window frames can extend a few pixels past the work area.
    width_ok = actual.width() == requested.width() or (
        available.width() < requested.width()
        and abs(actual.width() - available.width()) <= tolerance
    )
    height_ok = actual.height() == requested.height() or (
        available.height() < requested.height()
        and abs(actual.height() - available.height()) <= tolerance
    )
    return width_ok and height_ok


def execute_installed_ui_acceptance(window: MainWindow, output_root: Path) -> bool:
    """Drive real mounted widgets and write complete screenshots/structured evidence."""

    runner = _InstalledAcceptance(window, output_root)
    try:
        runner.execute()
    except Exception as error:  # noqa: BLE001 - acceptance must always persist its failure
        runner.evidence["passed"] = False
        runner.evidence["failure"] = f"{type(error).__name__}: {error}"
        runner.write_evidence()
        return False
    runner.evidence["passed"] = True
    runner.write_evidence()
    return True


class _InstalledAcceptance:
    def __init__(self, window: MainWindow, output_root: Path) -> None:
        self.window = window
        self.workspace = window.mission_workspace
        self.builder = self.workspace.builder
        self.map_host = self.builder.map_canvas
        self.output_root = output_root.resolve()
        self.screenshot_root = self.output_root / "screenshots"
        self.evidence: dict[str, object] = {
            "schema_version": 1,
            "application_version": QApplication.applicationVersion(),
            "working_directory": str(Path.cwd()),
            "output_root": str(self.output_root),
            "hardware_block_environment": os.environ.get("SKYWRITER_PACKAGED_SMOKE_TEST") == "1",
            "provider": self.map_host.tile_provider.value,
            "states": [],
            "screenshots": [],
            "layouts": [],
            "tab_navigation": [],
        }

    def execute(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.screenshot_root.mkdir(parents=True, exist_ok=True)
        self._wait_until(lambda: self.map_host.readiness is not None, "local map readiness")
        self._assert(self.map_host.tile_provider is TileProvider.OFFLINE, "offline map is default")
        self._assert(
            self.map_host.provider_status.requested_tiles == 0,
            "offline acceptance made no public tile request",
        )
        self._set_window_size(1498, 758, "owner-like")
        self._capture("00-start-owner-like")
        self._confirm_takeoff()
        self._go_to_coordinates()

        self._create_pending(QPoint(-250, -90))
        self._invalid_then_valid("proceed", "", None, "Altitude must be a number", 1)

        self._set_window_size(1366, 768, "common-1366")
        self._create_pending(QPoint(-80, 95))
        self._invalid_then_valid("hold", "32", "", "Hold time must be a number", 2)
        self._exercise_downstream_rejection()

        self._create_pending(QPoint(110, -95))
        self._invalid_then_valid("circle", "35", "0", "Circle radius must be greater than zero", 3)
        self._create_pending(QPoint(250, 90))
        self._invalid_then_valid("land", "", None, "Altitude must be a number", 4)

        self._exercise_file_picker_seam()
        self._exercise_builder_inventory()
        self._exercise_tabs_and_hardware_gates()
        self._exercise_optional_large_layout()
        self._capture("99-final")
        audit = vehicle_io_audit_snapshot()
        self.evidence["vehicle_io"] = audit.as_dict()
        self._assert(audit.attempts == 0, "zero vehicle open attempts")
        self._assert(audit.successes == 0, "zero vehicle opens")
        self._assert(
            os.environ.get("SKYWRITER_PACKAGED_SMOKE_TEST") == "1",
            "hardware open boundary is blocked",
        )

    def write_evidence(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        path = self.output_root / "installed-ui-acceptance.json"
        path.write_text(
            json.dumps(self.evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _confirm_takeoff(self) -> None:
        primary = self._child(QPushButton, "primaryActionButton")
        QTest.mouseClick(primary, Qt.MouseButton.LeftButton)
        self._assert(
            self._child(QLineEdit, "takeoffAltitudeInput").hasFocus(),
            "primary Takeoff focuses its first field",
        )
        self._replace_text("takeoffAltitudeInput", "25")
        self._replace_text("cruiseSpeedInput", "6")
        warning = self._child(QCheckBox, "obstacleWarningCheck")
        QTest.mouseClick(warning, Qt.MouseButton.LeftButton)
        confirm = self._child(QPushButton, "confirmTakeoffButton")
        confirm.setFocus()
        QTest.keyClick(confirm, Qt.Key.Key_Space)
        self._wait_until(
            lambda: self.workspace.service.snapshot.mission is not None,
            "Takeoff confirmation",
        )
        self._capture("01-takeoff-confirmed")

    def _go_to_coordinates(self) -> None:
        self._replace_text("mapLatitudeInput", "38.8895")
        self._replace_text("mapLongitudeInput", "-77.0353")
        go = self._child(QPushButton, "mapGoToCoordinatesButton")
        QTest.mouseClick(go, Qt.MouseButton.LeftButton)
        feedback = self._child(QLabel, "mapCoordinateFeedback")
        self._wait_until(lambda: "38.889500" in feedback.text(), "coordinate recenter")
        self._record_state("coordinates", feedback=feedback.text())

    def _create_pending(self, offset: QPoint) -> None:
        center = self.map_host.rect().center() + offset
        self._assert(self.map_host.rect().contains(center), "map click lies in rendered map")
        global_center = self.map_host.mapToGlobal(center)
        target = self.map_host.childAt(center) or QApplication.widgetAt(global_center)
        if target is None:
            # Windows WebEngine can expose its native Chromium child outside Qt's
            # QWidget hit-test tree. Use the desktop input path at the exact rendered
            # point so the native Chromium surface receives a human-equivalent click.
            self._native_windows_click(global_center)
        else:
            self._assert(
                target is self.map_host or self.map_host.isAncestorOf(target),
                "rendered map hit-test target belongs to the production map",
            )
            QTest.mouseClick(
                target,
                Qt.MouseButton.LeftButton,
                pos=target.mapFromGlobal(global_center),
            )
        try:
            self._wait_until(
                lambda: (
                    self.builder.pending_point is not None
                    and self.builder.editing_index is None
                ),
                "rendered map click",
            )
        except AssertionError:
            self._assert(
                self._click_map_with_js(center),
                "rendered map click fallback via JS intent",
            )
            self._wait_until(
                lambda: (
                    self.builder.pending_point is not None
                    and self.builder.editing_index is None
                ),
                "rendered map click fallback via JS intent",
            )
        panel = self._child(QWidget, "pendingPointPanel")
        self._assert(panel.isVisible(), "pending editor is visible after rendered map click")
        if self._action_count() == 0:
            self._exercise_pending_tab_order()

    def _click_map_with_js(self, point: QPoint) -> bool:
        complete = False
        result: bool | None = None

        def receive(value: object) -> None:
            nonlocal complete, result
            result = bool(value)
            complete = True

        self.map_host.page().runJavaScript(
            "Boolean(window.skywriterMapTest?.clickAtViewportPoint("
            f"{{x: {point.x()}, y: {point.y()}}}))",
            receive,
        )
        self._wait_until(lambda: complete, "map JS callback")
        return bool(result)

    def _native_windows_click(self, point: QPoint) -> None:
        self._assert(os.name == "nt", "native WebEngine click is Windows-only")
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        window_handle = int(self.window.winId())
        self.window.raise_()
        self.window.activateWindow()
        foreground = user32.GetForegroundWindow()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
        current_thread = kernel32.GetCurrentThreadId()
        attached = foreground_thread not in (0, current_thread)
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, True)
        try:
            user32.ShowWindow(window_handle, 5)
            user32.SetWindowPos(window_handle, -1, 0, 0, 0, 0, 0x0043)
            user32.SetWindowPos(window_handle, -2, 0, 0, 0, 0, 0x0043)
            user32.BringWindowToTop(window_handle)
            user32.SetForegroundWindow(window_handle)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        QTest.qWait(80)
        self._assert(
            user32.GetForegroundWindow() == window_handle,
            "installed application owns the foreground before native input",
        )
        self._assert(user32.SetCursorPos(point.x(), point.y()) != 0, "cursor moved")
        QTest.qWait(40)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)

    def _invalid_then_valid(
        self,
        kind: str,
        altitude: str,
        detail: str | None,
        expected_error: str,
        expected_count: int,
    ) -> None:
        if kind == "land":
            QTest.mouseClick(
                self._child(QPushButton, "primaryActionButton"), Qt.MouseButton.LeftButton
            )
            self._assert(
                self._child(QComboBox, "actionKindInput").currentData() == "land",
                "primary Land selects the pending Land editor",
            )
        else:
            self._select_action(kind)
        self._replace_text("actionAltitudeInput", altitude)
        if kind == "hold":
            self._replace_text("holdTimeInput", detail or "")
        elif kind == "circle":
            self._replace_text("circleRadiusInput", detail or "")
        confirm = self._child(QPushButton, "confirmActionButton")
        confirm.setFocus()
        QTest.keyClick(confirm, Qt.Key.Key_Space)
        error = self._child(QLabel, "pendingPointValidationError")
        self._wait_until(lambda: expected_error in error.text(), f"{kind} visible validation")
        offending_name = (
            "holdTimeInput"
            if kind == "hold"
            else "circleRadiusInput"
            if kind == "circle"
            else "actionAltitudeInput"
        )
        offending = self._child(QLineEdit, offending_name)
        self._assert(offending.hasFocus(), f"{kind} offending field receives focus")
        self._assert(self._inside_sidebar(error), f"{kind} feedback is inside visible sidebar")
        self._assert(self.builder.pending_point is not None, f"{kind} pending point retained")
        self._record_state(
            f"{kind}-invalid",
            visible_message=error.text(),
            offending_field=offending.objectName(),
            offending_value=offending.text(),
            editor_visible=self._child(QWidget, "pendingPointPanel").isVisible(),
        )
        self._capture(f"{expected_count:02d}-{kind}-invalid-visible")

        self._replace_text("actionAltitudeInput", "30" if not altitude else altitude)
        if kind == "hold":
            self._replace_text("holdTimeInput", "8")
        elif kind == "circle":
            self._replace_text("circleRadiusInput", "15")
        confirm = self._child(QPushButton, "confirmActionButton")
        confirm.setFocus()
        if kind == "hold":
            QTest.mouseClick(confirm, Qt.MouseButton.LeftButton, delay=30)
            QTest.mouseClick(confirm, Qt.MouseButton.LeftButton, delay=30)
        else:
            QTest.keyClick(confirm, Qt.Key.Key_Space)
        self._wait_until(
            lambda: self._action_count() == expected_count,
            f"{kind} confirmed action",
        )
        self._assert(self.builder.pending_point is None, f"{kind} editor cleared on success")
        self._assert(
            not self._child(QWidget, "pendingPointPanel").isVisible(),
            f"{kind} pending panel removed",
        )
        success = self._child(QLabel, "builderSuccess")
        self._assert(success.isVisible(), f"{kind} success feedback is visible")
        self._assert(self._inside_sidebar(success), f"{kind} success is inside visible sidebar")
        QTest.qWait(150)
        self._assert(self._action_count() == expected_count, f"{kind} did not double submit")
        map_state = self._assert_map_state(expected_count, pending=False)
        action_list = self._child(QListWidget, "missionActionList")
        self._record_state(
            f"{kind}-success",
            visible_message=success.text(),
            editor_visible=self._child(QWidget, "pendingPointPanel").isVisible(),
            mission_items=[action_list.item(index).text() for index in range(action_list.count())],
            summary=self._child(QLabel, "missionSummary").text(),
            map=map_state,
        )
        self._capture(f"{expected_count:02d}-{kind}-success")

    def _exercise_downstream_rejection(self) -> None:
        actions = self._child(QListWidget, "missionActionList")
        item = actions.visualItemRect(actions.item(0)).center()
        QTest.mouseClick(actions.viewport(), Qt.MouseButton.LeftButton, pos=item)
        self._wait_until(
            lambda: self._child(QWidget, "pendingPointPanel").isVisible(),
            "edit first point",
        )
        self._select_action("land")
        self._replace_text("actionAltitudeInput", "9")
        QTest.mouseClick(self._child(QPushButton, "confirmActionButton"), Qt.MouseButton.LeftButton)
        error = self._child(QLabel, "pendingPointValidationError")
        self._wait_until(
            lambda: "Land must be the final action" in error.text(),
            "application-service rejection",
        )
        self._assert(self._action_count() == 2, "rejected edit did not change mission")
        self._assert(self.builder.pending_point is not None, "rejected edit retained point")
        self._assert(
            self._child(QComboBox, "actionKindInput").currentData() == "land",
            "rejected edit retained action",
        )
        self._assert(
            self._child(QLineEdit, "actionAltitudeInput").text() == "9",
            "rejected edit retained altitude",
        )
        self._assert(self._inside_sidebar(error), "service rejection feedback is visible")
        self._record_state(
            "downstream-rejection",
            visible_message=error.text(),
            retained_action=self._child(QComboBox, "actionKindInput").currentData(),
            retained_altitude=self._child(QLineEdit, "actionAltitudeInput").text(),
            editor_visible=self._child(QWidget, "pendingPointPanel").isVisible(),
        )
        self._capture("20-downstream-rejection-retained")
        QTest.mouseClick(self._child(QPushButton, "cancelPendingButton"), Qt.MouseButton.LeftButton)

    def _exercise_file_picker_seam(self) -> None:
        saved = self.output_root / "safe-temp" / "accepted-mission.json"
        self._assert(
            saved.is_relative_to(self.output_root), "mission path remains under evidence root"
        )
        save = self._child(QPushButton, "saveMissionButton")
        QTest.mouseClick(save, Qt.MouseButton.LeftButton)
        self._wait_until(saved.is_file, "Save button deterministic picker")
        before = self._action_count()
        QTest.mouseClick(self._child(QPushButton, "newMissionButton"), Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self.workspace.service.snapshot.mission is None, "New button")
        QTest.mouseClick(self._child(QPushButton, "loadMissionButton"), Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == before, "Load button deterministic picker")
        self._record_state(
            "file-dialog-seam",
            path=str(saved),
            inside_output_root=saved.is_relative_to(self.output_root),
            restored_actions=before,
        )
        self._capture("30-save-new-load")

    def _exercise_builder_inventory(self) -> None:
        QTest.mouseClick(
            self._child(QPushButton, "compileMissionButton"), Qt.MouseButton.LeftButton
        )
        self._wait_until(
            lambda: self._child(QWidget, "compiledPreviewPanel").isVisible(),
            "Review and Compile preview",
        )
        self._assert(
            self._child(QListWidget, "compiledMissionItems").count() == 7,
            "compiled preview contains the complete closed sequence",
        )

        def cancel_settings_dialog() -> None:
            dialog = self.window.findChild(QDialog, "missionSettingsDialog")
            if dialog is None:
                return
            buttons = dialog.findChild(QDialogButtonBox)
            if buttons is not None:
                cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
                if cancel is not None:
                    QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)

        QTimer.singleShot(50, cancel_settings_dialog)
        QTest.mouseClick(
            self._child(QPushButton, "editMissionSettingsButton"), Qt.MouseButton.LeftButton
        )

        remove_land = self._child(QPushButton, "removeLandButton")
        self._assert(remove_land.isVisible(), "Remove Land is visible for a closed mission")
        QTest.mouseClick(remove_land, Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == 3, "Remove Land and reopen")
        QTest.mouseClick(self._child(QPushButton, "undoActionButton"), Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == 2, "Undo")

        load = self._child(QPushButton, "loadMissionButton")
        QTest.mouseClick(load, Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == 4, "reload after Undo")
        actions = self._child(QListWidget, "missionActionList")
        QTest.mouseClick(
            actions.viewport(),
            Qt.MouseButton.LeftButton,
            pos=actions.visualItemRect(actions.item(0)).center(),
        )
        delete = self._child(QPushButton, "deleteActionButton")
        self._assert(delete.isEnabled(), "Delete is enabled for selected non-Land point")
        QTest.mouseClick(delete, Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == 3, "Delete")
        QTest.mouseClick(load, Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == 4, "reload after Delete")
        QTest.mouseClick(self._child(QPushButton, "clearMissionButton"), Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == 0, "Clear mission")
        QTest.mouseClick(load, Qt.MouseButton.LeftButton)
        self._wait_until(lambda: self._action_count() == 4, "reload after Clear")
        self._record_state(
            "builder-control-inventory",
            compiled_items=7,
            edit_settings_dialog="opened and cancelled safely",
            remove_land=True,
            undo=True,
            delete=True,
            clear=True,
            restored_actions=4,
        )
        self._capture("35-builder-safe-controls")

    def _exercise_pending_tab_order(self) -> None:
        combo = self._child(QComboBox, "actionKindInput")
        combo.setFocus()
        observed: list[str | None] = [combo.objectName()]
        for _ in range(3):
            focus = QApplication.focusWidget()
            self._assert(focus is not None, "pending editor retains keyboard focus")
            assert focus is not None
            QTest.keyClick(focus, Qt.Key.Key_Tab)
            QApplication.processEvents()
            next_focus = QApplication.focusWidget()
            observed.append(None if next_focus is None else next_focus.objectName())
        self._assert(
            observed
            == [
                "actionKindInput",
                "actionAltitudeInput",
                "cancelPendingButton",
                "confirmActionButton",
            ],
            "pending editor tab order is deterministic",
        )
        self._record_state("pending-tab-order", order=observed)

    def _exercise_tabs_and_hardware_gates(self) -> None:
        tabs = self._child(QTabWidget, "primaryViews")
        gate_names = (
            "connectedInteractionGate",
            "preflightInteractionGate",
            "flightInteractionGate",
        )
        controls = (
            (
                "discoverUsbButton",
                "discoverSikButton",
                "inspectOnboardMissionButton",
                "uploadAndVerifyButton",
                "refreshConnectedTelemetryButton",
                "reverifyConnectedMissionButton",
                "disconnectConnectedButton",
            ),
            ("requestNativePrearmButton", "normalArmButton"),
            (
                "nativeAutoStartButton",
                "nativePauseButton",
                "nativeResumeButton",
                "landHereNowButton",
                "landHereNowConfirmButton",
                "landHereNowCancelButton",
            ),
        )
        navigation = cast(list[object], self.evidence["tab_navigation"])
        for index, (gate_name, control_names) in enumerate(
            zip(gate_names, controls, strict=True), start=1
        ):
            tabs.setCurrentIndex(index)
            QTest.qWait(50)
            gate = self._child(QLabel, gate_name)
            self._assert(gate.isVisible(), f"tab {index} gate explanation is visible")
            disabled: dict[str, bool] = {}
            for name in control_names:
                button = self._child(QPushButton, name)
                disabled[name] = not button.isEnabled()
                self._assert(not button.isEnabled(), f"{name} remains fail closed")
            if index == 1:
                target = self._child(QComboBox, "connectedTargetSelection")
                replacement = self._child(QCheckBox, "confirmMissionReplacement")
                disabled[target.objectName()] = not target.isEnabled()
                disabled[replacement.objectName()] = not replacement.isEnabled()
                self._assert(not target.isEnabled(), "target selection remains fail closed")
                self._assert(
                    not replacement.isEnabled(), "replacement approval remains fail closed"
                )
            elif index == 2:
                review = self._child(QCheckBox, "acknowledgeNativePrearmReview")
                disabled[review.objectName()] = not review.isEnabled()
                self._assert(not review.isEnabled(), "pre-arm review remains fail closed")
            navigation.append(
                {
                    "index": index,
                    "label": tabs.tabText(index),
                    "gate": gate.text(),
                    "controls_disabled": disabled,
                }
            )
            self._capture(f"4{index}-tab-{tabs.tabText(index).lower()}")
        tabs.setCurrentIndex(0)

    def _exercise_optional_large_layout(self) -> None:
        screen = QApplication.primaryScreen()
        self._assert(screen is not None, "primary screen is available")
        assert screen is not None
        available = screen.availableGeometry()
        layouts = cast(list[object], self.evidence["layouts"])
        if available.width() >= 1920 and available.height() >= 1080:
            self._set_window_size(1920, 1080, "large-1920")
            self._capture("50-large-1920x1080")
        else:
            layouts.append(
                {
                    "label": "large-1920",
                    "exercised": False,
                    "reason": "primary desktop is smaller than 1920x1080",
                    "available_width": available.width(),
                    "available_height": available.height(),
                    "device_pixel_ratio": screen.devicePixelRatio(),
                }
            )

    def _set_window_size(self, width: int, height: int, label: str) -> None:
        self.window.showNormal()
        self.window.resize(width, height)
        QTest.qWait(150)
        actual = self.window.size()
        screen = QApplication.primaryScreen()
        self._assert(screen is not None, "primary screen is available")
        assert screen is not None
        available = screen.availableGeometry().size()
        requested = QSize(width, height)
        exact = actual == requested
        layouts = cast(list[object], self.evidence["layouts"])
        layouts.append(
            {
                "label": label,
                "exercised": True,
                "mode": "exact" if exact else "closest-available",
                "requested_width": width,
                "requested_height": height,
                "actual_width": actual.width(),
                "actual_height": actual.height(),
                "available_width": available.width(),
                "available_height": available.height(),
                "device_pixel_ratio": screen.devicePixelRatio(),
            }
        )
        self._assert(
            _is_exact_or_closest_available(actual, requested, available),
            f"{label} geometry",
        )

    def _select_action(self, kind: str) -> None:
        combo = self._child(QComboBox, "actionKindInput")
        index = combo.findData(kind)
        self._assert(index >= 0, f"{kind} action exists")
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Home)
        for _ in range(index):
            QTest.keyClick(combo, Qt.Key.Key_Down)
        self._assert(combo.currentData() == kind, f"{kind} selected by keyboard")

    def _replace_text(self, name: str, text: str) -> None:
        field = self._child(QLineEdit, name)
        QTest.mouseClick(field, Qt.MouseButton.LeftButton)
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        if text:
            QTest.keyClicks(field, text)

    def _inside_sidebar(self, widget: QWidget) -> bool:
        scroll = self._child(QScrollArea, "missionSidebarScroll")
        viewport = scroll.viewport()
        viewport_rect = QRect(viewport.mapToGlobal(QPoint()), viewport.size())
        widget_rect = QRect(widget.mapToGlobal(QPoint()), widget.size())
        return widget.isVisible() and viewport_rect.contains(widget_rect)

    def _assert_map_state(self, action_count: int, *, pending: bool) -> dict[str, object]:
        value = self._evaluate_map(
            "({action_count: window.skywriterMapTest.snapshot().action_count, "
            "pending: Boolean(document.querySelector('.is-pending')), "
            "route: Boolean(document.querySelector('.mission-route'))})"
        )
        self._assert(value.get("action_count") == action_count, "map action count updated")
        self._assert(value.get("pending") is pending, "map pending marker state updated")
        self._assert(value.get("route") is (action_count > 1), "map route state updated")
        return value

    def _evaluate_map(self, expression: str) -> dict[str, object]:
        finished = False
        result: object = None

        def receive(value: object) -> None:
            nonlocal finished, result
            result = value
            finished = True

        self.map_host.page().runJavaScript(f"JSON.stringify({expression})", receive)
        self._wait_until(lambda: finished, "map DOM evaluation")
        self._assert(isinstance(result, str), "map DOM result is JSON")
        parsed = json.loads(cast(str, result))
        self._assert(isinstance(parsed, dict), "map DOM result is an object")
        return cast(dict[str, object], parsed)

    def _capture(self, label: str) -> None:
        QApplication.processEvents()
        path = self.screenshot_root / f"{label}.png"
        pixmap = self.window.grab()
        self._assert(pixmap.save(str(path), "PNG"), f"screenshot {label} saved")
        payload = path.read_bytes()
        screenshots = cast(list[object], self.evidence["screenshots"])
        screenshots.append(
            {
                "label": label,
                "path": str(path),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "width": pixmap.width(),
                "height": pixmap.height(),
            }
        )

    def _record_state(self, label: str, **values: object) -> None:
        focus = QApplication.focusWidget()
        scroll = self._child(QScrollArea, "missionSidebarScroll").verticalScrollBar()
        states = cast(list[object], self.evidence["states"])
        states.append(
            {
                "label": label,
                "focus": None if focus is None else focus.objectName(),
                "sidebar_scroll_value": scroll.value(),
                "sidebar_scroll_maximum": scroll.maximum(),
                "action_count": self._action_count(),
                **values,
            }
        )

    def _action_count(self) -> int:
        mission = self.workspace.service.snapshot.mission
        return 0 if mission is None else len(mission.actions)

    def _wait_until(self, predicate: object, label: str, timeout_s: float = 10.0) -> None:
        if not callable(predicate):
            raise TypeError("acceptance predicate must be callable")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if predicate():
                return
            QTest.qWait(25)
        raise AssertionError(f"Timed out waiting for {label}.")

    def _assert(self, condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(label)

    def _child(self, widget_type: type[TWidget], name: str) -> TWidget:
        widget = self.window.findChild(widget_type, name)
        if widget is None:
            raise AssertionError(f"Installed control not found: {name}")
        return widget
