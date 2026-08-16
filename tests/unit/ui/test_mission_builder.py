"""Headless mission-builder interaction tests with a test-owned contract fake."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TypeVar, cast

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
)

from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    ProceedAction,
)
from skywriter.main import create_application
from skywriter.ui.mission_builder import (
    OBSTACLE_WARNING_TEXT,
    ActionAppendRequested,
    ActionDeleteRequested,
    ActionKind,
    ActionMoveRequested,
    ActionReplaceRequested,
    ActionSelected,
    ClearRequested,
    MissionBuilderIntent,
    MissionBuilderSnapshot,
    MissionBuilderWidget,
    RemoveLandRequested,
    TakeoffRequested,
    UndoRequested,
)

TWidget = TypeVar("TWidget", bound=QWidget)


class FakeMissionAdapter:
    """Test-owned adapter that acknowledges intents with new render snapshots."""

    def __init__(self, widget: MissionBuilderWidget) -> None:
        self.widget = widget
        self.snapshot = MissionBuilderSnapshot()
        self.received: list[MissionBuilderIntent] = []
        widget.intent_emitted.connect(self.handle)

    def handle(self, value: object) -> None:
        intent = cast(MissionBuilderIntent, value)
        self.received.append(intent)
        settings = self.snapshot.settings
        actions = self.snapshot.actions
        selected = self.snapshot.selected_index
        if isinstance(intent, TakeoffRequested):
            settings = intent.settings
        elif isinstance(intent, ActionAppendRequested):
            actions = (*actions, intent.action)
            selected = None
        elif isinstance(intent, ActionReplaceRequested):
            mutable = list(actions)
            mutable[intent.index] = intent.action
            actions = tuple(mutable)
            selected = None
        elif isinstance(intent, ActionDeleteRequested):
            actions = actions[: intent.index] + actions[intent.index + 1 :]
            selected = None
        elif isinstance(intent, ActionMoveRequested):
            mutable = list(actions)
            mutable[intent.index] = replace(mutable[intent.index], point=intent.point)
            actions = tuple(mutable)
        elif isinstance(intent, ActionSelected):
            selected = intent.index
        elif isinstance(intent, UndoRequested):
            actions = actions[:-1]
            selected = None
        elif isinstance(intent, ClearRequested):
            actions = ()
            selected = None
        elif isinstance(intent, RemoveLandRequested):
            if actions and isinstance(actions[-1], LandAction):
                actions = actions[:-1]
            selected = None
        self.snapshot = MissionBuilderSnapshot(settings, actions, selected)
        self.widget.render_snapshot(self.snapshot)


def child(parent: QWidget, widget_type: type[TWidget], name: str) -> TWidget:
    result = parent.findChild(widget_type, name)
    assert result is not None
    return result


def make_builder() -> tuple[MissionBuilderWidget, FakeMissionAdapter]:
    app = create_application(["skywriter-task-003-test"])
    widget = MissionBuilderWidget()
    adapter = FakeMissionAdapter(widget)
    widget.resize(1200, 760)
    widget.show()
    app.processEvents()
    return widget, adapter


def confirm_takeoff(widget: MissionBuilderWidget) -> None:
    child(widget, QLineEdit, "takeoffAltitudeInput").setText("25")
    child(widget, QLineEdit, "cruiseSpeedInput").setText("6")
    child(widget, QCheckBox, "obstacleWarningCheck").setChecked(True)
    child(widget, QPushButton, "confirmTakeoffButton").click()


def commit_action(
    widget: MissionBuilderWidget,
    point: GeoPoint,
    kind: ActionKind,
    altitude: str,
    *,
    detail: str | None = None,
) -> None:
    send_map_click(widget, point)
    combo = child(widget, QComboBox, "actionKindInput")
    combo.setCurrentIndex(combo.findData(kind))
    child(widget, QLineEdit, "actionAltitudeInput").setText(altitude)
    if kind is ActionKind.HOLD:
        child(widget, QLineEdit, "holdTimeInput").setText(detail or "")
    elif kind is ActionKind.CIRCLE:
        child(widget, QLineEdit, "circleRadiusInput").setText(detail or "")
    child(widget, QPushButton, "confirmActionButton").click()


def send_map_click(widget: MissionBuilderWidget, point: GeoPoint) -> None:
    widget.map_canvas.bridge.receive_message(
        json.dumps(
            {
                "schema_version": 1,
                "type": "map_clicked",
                "point": {
                    "latitude_deg": point.latitude_deg,
                    "longitude_deg": point.longitude_deg,
                },
            }
        )
    )


def send_point_drag(widget: MissionBuilderWidget, index: int, point: GeoPoint) -> None:
    widget.map_canvas.bridge.receive_message(
        json.dumps(
            {
                "schema_version": 1,
                "type": "point_dragged",
                "index": index,
                "point": {
                    "latitude_deg": point.latitude_deg,
                    "longitude_deg": point.longitude_deg,
                },
            }
        )
    )


def test_takeoff_requires_fields_and_exact_warning_acknowledgment() -> None:
    widget, adapter = make_builder()
    error = child(widget, QLabel, "builderError")
    warning = child(widget, QLabel, "obstacleWarningText")

    child(widget, QPushButton, "confirmTakeoffButton").click()
    assert "must be a number" in error.text()
    assert adapter.received == []

    child(widget, QLineEdit, "takeoffAltitudeInput").setText("25")
    child(widget, QLineEdit, "cruiseSpeedInput").setText("6")
    child(widget, QPushButton, "confirmTakeoffButton").click()
    assert "Acknowledge" in error.text()
    assert warning.text() == OBSTACLE_WARNING_TEXT

    child(widget, QCheckBox, "obstacleWarningCheck").setChecked(True)
    child(widget, QPushButton, "confirmTakeoffButton").click()

    assert adapter.snapshot.settings is not None
    assert child(widget, QPushButton, "primaryActionButton").text() == "Land"
    assert child(widget, QWidget, "takeoffPanel").isHidden()
    widget.close()


def test_pending_cancel_does_not_commit_a_point_and_required_fields_are_enforced() -> None:
    widget, adapter = make_builder()
    confirm_takeoff(widget)

    send_map_click(widget, GeoPoint(38.0, -77.0))
    assert widget.pending_point == GeoPoint(38.0, -77.0)
    assert not child(widget, QWidget, "pendingPointPanel").isHidden()
    child(widget, QPushButton, "cancelPendingButton").click()
    assert widget.pending_point is None
    assert adapter.snapshot.actions == ()

    send_map_click(widget, GeoPoint(38.0, -77.0))
    child(widget, QComboBox, "actionKindInput").setCurrentIndex(
        child(widget, QComboBox, "actionKindInput").findData(ActionKind.HOLD)
    )
    child(widget, QLineEdit, "actionAltitudeInput").setText("30")
    child(widget, QPushButton, "confirmActionButton").click()
    assert "Hold time must be a number" in child(widget, QLabel, "builderError").text()
    assert adapter.snapshot.actions == ()
    widget.close()


def test_complete_flow_renders_route_labels_circle_cues_and_land_closure() -> None:
    widget, adapter = make_builder()
    confirm_takeoff(widget)

    commit_action(widget, GeoPoint(38.00, -77.00), ActionKind.PROCEED, "30")
    commit_action(widget, GeoPoint(38.01, -77.01), ActionKind.HOLD, "32", detail="8")
    send_map_click(widget, GeoPoint(38.02, -77.02))
    combo = child(widget, QComboBox, "actionKindInput")
    combo.setCurrentIndex(combo.findData(ActionKind.CIRCLE))
    assert not child(widget, QLabel, "circleDirectionCue").isHidden()
    child(widget, QLineEdit, "actionAltitudeInput").setText("35")
    child(widget, QLineEdit, "circleRadiusInput").setText("15")
    child(widget, QPushButton, "confirmActionButton").click()
    commit_action(widget, GeoPoint(38.03, -77.03), ActionKind.LAND, "10")

    assert isinstance(adapter.snapshot.actions[0], ProceedAction)
    assert isinstance(adapter.snapshot.actions[1], HoldAction)
    assert isinstance(adapter.snapshot.actions[2], CircleAction)
    assert isinstance(adapter.snapshot.actions[3], LandAction)
    action_list = child(widget, QListWidget, "missionActionList")
    assert action_list.count() == 4
    assert "Hold" in action_list.item(1).text()
    assert "Circle CW" in action_list.item(2).text()
    assert "Land" in action_list.item(3).text()
    summary = child(widget, QLabel, "missionSummary").text()
    assert "Circle once clockwise" in summary
    assert "then Land there" in summary
    assert not child(widget, QPushButton, "primaryActionButton").isEnabled()
    assert not child(widget, QPushButton, "removeLandButton").isHidden()

    prior_count = len(adapter.received)
    send_map_click(widget, GeoPoint(38.04, -77.04))
    assert len(adapter.received) == prior_count
    assert widget.pending_point is None
    assert "Remove Land" in child(widget, QLabel, "builderError").text()
    widget.close()


def test_selection_edit_drag_delete_undo_clear_and_remove_land_reopen() -> None:
    widget, adapter = make_builder()
    confirm_takeoff(widget)
    commit_action(widget, GeoPoint(38.00, -77.00), ActionKind.PROCEED, "30")
    commit_action(widget, GeoPoint(38.01, -77.01), ActionKind.HOLD, "32", detail="8")
    commit_action(widget, GeoPoint(38.02, -77.02), ActionKind.LAND, "10")

    child(widget, QPushButton, "removeLandButton").click()
    assert len(adapter.snapshot.actions) == 2
    assert child(widget, QPushButton, "primaryActionButton").isEnabled()
    commit_action(widget, GeoPoint(38.03, -77.03), ActionKind.PROCEED, "40")

    action_list = child(widget, QListWidget, "missionActionList")
    action_list.setCurrentRow(0)
    child(widget, QLineEdit, "actionAltitudeInput").setText("45")
    child(widget, QPushButton, "confirmActionButton").click()
    first = cast(ProceedAction, adapter.snapshot.actions[0])
    assert first.altitude_m == 45.0

    send_point_drag(widget, 0, GeoPoint(39.0, -76.0))
    assert adapter.snapshot.actions[0].point == GeoPoint(39.0, -76.0)

    action_list.setCurrentRow(1)
    child(widget, QPushButton, "deleteActionButton").click()
    assert len(adapter.snapshot.actions) == 2
    child(widget, QPushButton, "undoActionButton").click()
    assert len(adapter.snapshot.actions) == 1
    child(widget, QPushButton, "clearMissionButton").click()
    assert adapter.snapshot.actions == ()
    widget.close()


def test_land_primary_control_selects_land_for_current_pending_point() -> None:
    widget, adapter = make_builder()
    confirm_takeoff(widget)
    send_map_click(widget, GeoPoint(38.0, -77.0))

    child(widget, QPushButton, "primaryActionButton").click()

    assert child(widget, QComboBox, "actionKindInput").currentData() == ActionKind.LAND.value
    child(widget, QLineEdit, "actionAltitudeInput").setText("9")
    child(widget, QPushButton, "confirmActionButton").click()
    assert isinstance(adapter.snapshot.actions[-1], LandAction)
    widget.close()


def test_land_edit_is_locked_and_generic_delete_or_undo_cannot_reopen() -> None:
    widget, adapter = make_builder()
    confirm_takeoff(widget)
    commit_action(widget, GeoPoint(38.00, -77.00), ActionKind.PROCEED, "30")
    commit_action(widget, GeoPoint(38.01, -77.01), ActionKind.LAND, "10")

    action_list = child(widget, QListWidget, "missionActionList")
    action_list.setCurrentRow(1)
    kind_input = child(widget, QComboBox, "actionKindInput")
    delete_button = child(widget, QPushButton, "deleteActionButton")
    undo_button = child(widget, QPushButton, "undoActionButton")

    assert not kind_input.isEnabled()
    assert kind_input.currentData() == ActionKind.LAND.value
    assert not delete_button.isEnabled()
    assert not undo_button.isEnabled()

    prior_count = len(adapter.received)
    delete_button.click()
    undo_button.click()
    assert len(adapter.received) == prior_count
    assert isinstance(adapter.snapshot.actions[-1], LandAction)

    kind_input.setCurrentIndex(kind_input.findData(ActionKind.PROCEED.value))
    child(widget, QLineEdit, "actionAltitudeInput").setText("12")
    child(widget, QPushButton, "confirmActionButton").click()
    edited_land = adapter.snapshot.actions[-1]
    assert edited_land.approach_altitude_m == 12.0

    child(widget, QPushButton, "removeLandButton").click()
    assert len(adapter.snapshot.actions) == 1
    assert not adapter.snapshot.is_closed
    widget.close()


def test_clear_mission_explicitly_resets_a_closed_mission() -> None:
    widget, adapter = make_builder()
    confirm_takeoff(widget)
    commit_action(widget, GeoPoint(38.00, -77.00), ActionKind.PROCEED, "30")
    commit_action(widget, GeoPoint(38.01, -77.01), ActionKind.LAND, "10")

    clear_button = child(widget, QPushButton, "clearMissionButton")
    assert clear_button.text() == "Clear mission"
    clear_button.click()

    assert adapter.snapshot.actions == ()
    assert not adapter.snapshot.is_closed
    widget.close()
