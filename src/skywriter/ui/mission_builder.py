"""Offline beginner mission-builder presentation using typed intent contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    MissionAction,
    MissionSettings,
    ProceedAction,
)
from skywriter.ui.map import MissionMapCanvas

OBSTACLE_WARNING_TEXT = (
    "Verify clearance from power lines, rooftops, trees, cables, poles, and other "
    "obstacles. The map does not guarantee obstacle clearance."
)


class ActionKind(StrEnum):
    """Beginner action choices exposed by the pending-point editor."""

    PROCEED = "proceed"
    HOLD = "hold"
    CIRCLE = "circle"
    LAND = "land"


@dataclass(frozen=True, slots=True)
class MissionBuilderSnapshot:
    """Sanitized render state supplied by an application adapter or test fake."""

    settings: MissionSettings | None = None
    actions: tuple[MissionAction, ...] = ()
    selected_index: int | None = None
    error_message: str | None = None

    @property
    def is_closed(self) -> bool:
        return bool(self.actions) and isinstance(self.actions[-1], LandAction)


@dataclass(frozen=True, slots=True)
class TakeoffRequested:
    settings: MissionSettings


@dataclass(frozen=True, slots=True)
class ActionAppendRequested:
    action: MissionAction


@dataclass(frozen=True, slots=True)
class ActionReplaceRequested:
    index: int
    action: MissionAction


@dataclass(frozen=True, slots=True)
class ActionDeleteRequested:
    index: int


@dataclass(frozen=True, slots=True)
class ActionMoveRequested:
    index: int
    point: GeoPoint


@dataclass(frozen=True, slots=True)
class ActionSelected:
    index: int


@dataclass(frozen=True, slots=True)
class UndoRequested:
    pass


@dataclass(frozen=True, slots=True)
class ClearRequested:
    pass


@dataclass(frozen=True, slots=True)
class RemoveLandRequested:
    pass


MissionBuilderIntent: TypeAlias = (
    TakeoffRequested
    | ActionAppendRequested
    | ActionReplaceRequested
    | ActionDeleteRequested
    | ActionMoveRequested
    | ActionSelected
    | UndoRequested
    | ClearRequested
    | RemoveLandRequested
)


class MissionBuilderWidget(QWidget):
    """Render mission state and emit typed user intents without owning domain state."""

    intent_emitted = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("missionBuilder")
        self.setAccessibleName("Mission builder")
        self.setMinimumSize(980, 680)
        self._snapshot = MissionBuilderSnapshot()
        self._pending_point: GeoPoint | None = None
        self._editing_index: int | None = None
        self._build_ui()
        self._connect_signals()
        self.render_snapshot(self._snapshot)

    @property
    def snapshot(self) -> MissionBuilderSnapshot:
        return self._snapshot

    @property
    def pending_point(self) -> GeoPoint | None:
        return self._pending_point

    @property
    def map_canvas(self) -> MissionMapCanvas:
        return self._map

    def render_snapshot(self, snapshot: MissionBuilderSnapshot) -> None:
        """Render a complete immutable snapshot supplied by the host adapter."""

        self._snapshot = snapshot
        settings_ready = snapshot.settings is not None
        self._takeoff_panel.setVisible(not settings_ready)
        self._mission_panel.setVisible(settings_ready)
        self._primary_action.setText("Land" if settings_ready else "Takeoff")
        self._primary_action.setEnabled(not snapshot.is_closed)
        self._primary_action.setToolTip(
            "Choose Land for the current pending point"
            if settings_ready
            else "Complete Takeoff setup first"
        )
        if snapshot.is_closed:
            self._primary_action.setText("Land added")
        self._remove_land.setVisible(snapshot.is_closed)
        self._rebuild_action_list()
        self._render_summary()
        if snapshot.error_message:
            self._show_error(snapshot.error_message)
        elif not self._pending_point:
            self._clear_error()
        if snapshot.selected_index is not None and self._valid_index(snapshot.selected_index):
            self._begin_edit(snapshot.selected_index)
        elif self._editing_index is not None and not self._valid_index(self._editing_index):
            self._clear_pending()
        self._refresh_map()

    def begin_pending(self, point: GeoPoint) -> None:
        """Public map-adapter hook for a validated decimal-degree click."""

        self._on_map_clicked(point)

    def _build_ui(self) -> None:
        self.setStyleSheet(_STYLE_SHEET)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 22)
        root.setSpacing(16)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Build a simple mission")
        title.setObjectName("builderTitle")
        subtitle = QLabel("Takeoff first, then click the route in creation order.")
        subtitle.setObjectName("builderSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        offline_badge = QLabel("OFFLINE  •  NO VEHICLE LINK")
        offline_badge.setObjectName("offlineBadge")
        offline_badge.setAccessibleName("Offline, no vehicle link")
        header.addWidget(offline_badge)
        root.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(18)
        self._map = MissionMapCanvas()
        content.addWidget(self._map, 3)

        side_scroll = QScrollArea()
        side_scroll.setObjectName("missionSidebarScroll")
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setMinimumWidth(370)
        side_scroll.setMaximumWidth(430)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side = QWidget()
        side.setObjectName("missionSidebar")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(2, 2, 8, 2)
        side_layout.setSpacing(12)

        self._primary_action = QPushButton("Takeoff")
        self._primary_action.setObjectName("primaryActionButton")
        self._primary_action.setAccessibleName("Primary mission action")
        side_layout.addWidget(self._primary_action)

        self._takeoff_panel = self._build_takeoff_panel()
        side_layout.addWidget(self._takeoff_panel)
        self._mission_panel = self._build_mission_panel()
        side_layout.addWidget(self._mission_panel)
        self._pending_panel = self._build_pending_panel()
        side_layout.addWidget(self._pending_panel)

        self._error = QLabel()
        self._error.setObjectName("builderError")
        self._error.setAccessibleName("Mission builder error")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        side_layout.addWidget(self._error)
        side_layout.addStretch()
        side_scroll.setWidget(side)
        content.addWidget(side_scroll, 1)
        root.addLayout(content, 1)

    def _build_takeoff_panel(self) -> QFrame:
        panel = _card("takeoffPanel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        layout.addWidget(_section_title("1  Takeoff setup"))
        layout.addWidget(_field_label("Takeoff altitude Above Home (m)"))
        self._takeoff_altitude = _number_input("takeoffAltitudeInput", "e.g. 25")
        layout.addWidget(self._takeoff_altitude)
        layout.addWidget(_field_label("Mission cruise speed (m/s)"))
        self._cruise_speed = _number_input("cruiseSpeedInput", "e.g. 6")
        layout.addWidget(self._cruise_speed)
        warning = QLabel(OBSTACLE_WARNING_TEXT)
        warning.setObjectName("obstacleWarningText")
        warning.setWordWrap(True)
        warning.setAccessibleName("Obstacle warning")
        layout.addWidget(warning)
        self._warning_ack = QCheckBox("I acknowledge this obstacle warning.")
        self._warning_ack.setObjectName("obstacleWarningCheck")
        self._warning_ack.setAccessibleName("Acknowledge obstacle warning")
        layout.addWidget(self._warning_ack)
        self._confirm_takeoff = QPushButton("Confirm Takeoff")
        self._confirm_takeoff.setObjectName("confirmTakeoffButton")
        layout.addWidget(self._confirm_takeoff)
        return panel

    def _build_mission_panel(self) -> QFrame:
        panel = _card("missionPanel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(9)
        layout.addWidget(_section_title("Mission points"))
        guidance = QLabel("Click the map, then choose Proceed, Hold, Circle, or Land.")
        guidance.setObjectName("missionGuidance")
        guidance.setWordWrap(True)
        layout.addWidget(guidance)
        self._action_list = QListWidget()
        self._action_list.setObjectName("missionActionList")
        self._action_list.setAccessibleName("Confirmed mission points")
        self._action_list.setMinimumHeight(130)
        layout.addWidget(self._action_list)

        controls = QHBoxLayout()
        self._delete = QPushButton("Delete")
        self._delete.setObjectName("deleteActionButton")
        self._undo = QPushButton("Undo")
        self._undo.setObjectName("undoActionButton")
        self._clear = QPushButton("Clear")
        self._clear.setObjectName("clearMissionButton")
        controls.addWidget(self._delete)
        controls.addWidget(self._undo)
        controls.addWidget(self._clear)
        layout.addLayout(controls)

        self._remove_land = QPushButton("Remove Land and reopen")
        self._remove_land.setObjectName("removeLandButton")
        layout.addWidget(self._remove_land)
        self._summary = QLabel()
        self._summary.setObjectName("missionSummary")
        self._summary.setAccessibleName("Plain-language mission summary")
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        layout.addWidget(self._summary)
        return panel

    def _build_pending_panel(self) -> QFrame:
        panel = _card("pendingPointPanel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(9)
        self._pending_title = _section_title("Pending point")
        layout.addWidget(self._pending_title)
        self._pending_coordinates = QLabel()
        self._pending_coordinates.setObjectName("pendingCoordinates")
        layout.addWidget(self._pending_coordinates)
        layout.addWidget(_field_label("Action"))
        self._action_kind = QComboBox()
        self._action_kind.setObjectName("actionKindInput")
        self._action_kind.setAccessibleName("Pending point action")
        for label, kind in (
            ("Proceed", ActionKind.PROCEED),
            ("Hold", ActionKind.HOLD),
            ("Circle", ActionKind.CIRCLE),
            ("Land", ActionKind.LAND),
        ):
            self._action_kind.addItem(label, kind.value)
        layout.addWidget(self._action_kind)
        self._altitude_label = _field_label("Altitude Above Home (m)")
        layout.addWidget(self._altitude_label)
        self._altitude = _number_input("actionAltitudeInput", "e.g. 30")
        layout.addWidget(self._altitude)
        self._hold_label = _field_label("Hold time (seconds)")
        self._hold_time = _number_input("holdTimeInput", "e.g. 10")
        layout.addWidget(self._hold_label)
        layout.addWidget(self._hold_time)
        self._radius_label = _field_label("Circle radius (meters)")
        self._radius = _number_input("circleRadiusInput", "e.g. 15")
        layout.addWidget(self._radius_label)
        layout.addWidget(self._radius)
        cue = QLabel("Circle is one clockwise turn")
        cue.setObjectName("circleDirectionCue")
        layout.addWidget(cue)
        self._circle_cue = cue
        actions = QHBoxLayout()
        self._cancel_pending = QPushButton("Cancel")
        self._cancel_pending.setObjectName("cancelPendingButton")
        self._confirm_action = QPushButton("Confirm point")
        self._confirm_action.setObjectName("confirmActionButton")
        actions.addWidget(self._cancel_pending)
        actions.addWidget(self._confirm_action)
        layout.addLayout(actions)
        panel.setVisible(False)
        return panel

    def _connect_signals(self) -> None:
        self._primary_action.clicked.connect(self._on_primary_action)
        self._confirm_takeoff.clicked.connect(self._on_confirm_takeoff)
        self._action_kind.currentIndexChanged.connect(self._update_action_fields)
        self._confirm_action.clicked.connect(self._on_confirm_action)
        self._cancel_pending.clicked.connect(self._clear_pending)
        self._action_list.currentRowChanged.connect(self._on_action_selected)
        self._delete.clicked.connect(self._on_delete)
        self._undo.clicked.connect(self._on_undo)
        self._clear.clicked.connect(self._on_clear)
        self._remove_land.clicked.connect(self._on_remove_land)
        self._map.map_clicked.connect(self._on_map_clicked)
        self._map.point_selected.connect(self._on_canvas_selected)
        self._map.point_dragged.connect(self._on_point_dragged)

    def _on_primary_action(self) -> None:
        if self._snapshot.settings is None:
            self._takeoff_altitude.setFocus()
            return
        if self._snapshot.is_closed:
            self._show_error("Remove Land before adding another point.")
            return
        if self._pending_point is None:
            self._show_error("Click the map to choose the landing point first.")
            return
        self._set_action_kind(ActionKind.LAND)
        self._pending_panel.setVisible(True)
        self._altitude.setFocus()

    def _on_confirm_takeoff(self) -> None:
        try:
            altitude_m = _parse_finite(self._takeoff_altitude.text(), "Takeoff altitude")
            speed_m_s = _parse_positive(self._cruise_speed.text(), "Cruise speed")
            if not self._warning_ack.isChecked():
                raise ValueError("Acknowledge the obstacle warning before confirming Takeoff.")
        except ValueError as error:
            self._show_error(str(error))
            return
        self._clear_error()
        self.intent_emitted.emit(
            TakeoffRequested(
                MissionSettings(
                    takeoff_altitude_m=altitude_m,
                    cruise_speed_m_s=speed_m_s,
                    obstacle_warning_acknowledged=True,
                )
            )
        )

    def _on_map_clicked(self, point: object) -> None:
        if not isinstance(point, GeoPoint):
            self._show_error("The map returned an invalid coordinate.")
            return
        if self._snapshot.settings is None:
            self._show_error("Confirm Takeoff before adding map points.")
            return
        if self._snapshot.is_closed:
            self._show_error("Remove Land before adding another point.")
            return
        self._editing_index = None
        self._pending_point = point
        self._pending_title.setText(f"Pending point {len(self._snapshot.actions) + 1}")
        self._pending_coordinates.setText(_format_point(point))
        self._set_action_kind(ActionKind.PROCEED)
        self._altitude.clear()
        self._hold_time.clear()
        self._radius.clear()
        self._pending_panel.setVisible(True)
        self._clear_error()
        self._refresh_map()
        self._action_kind.setFocus()

    def _on_confirm_action(self) -> None:
        if self._pending_point is None:
            self._show_error("Click the map before confirming a point.")
            return
        try:
            altitude_m = _parse_finite(self._altitude.text(), "Altitude")
            kind = self._current_action_kind()
            action: MissionAction
            if kind is ActionKind.PROCEED:
                action = ProceedAction(self._pending_point, altitude_m)
            elif kind is ActionKind.HOLD:
                action = HoldAction(
                    self._pending_point,
                    altitude_m,
                    _parse_positive(self._hold_time.text(), "Hold time"),
                )
            elif kind is ActionKind.CIRCLE:
                action = CircleAction(
                    self._pending_point,
                    altitude_m,
                    _parse_positive(self._radius.text(), "Circle radius"),
                )
            else:
                action = LandAction(self._pending_point, altitude_m)
        except ValueError as error:
            self._show_error(str(error))
            return

        editing_index = self._editing_index
        self._clear_pending()
        if editing_index is None:
            self.intent_emitted.emit(ActionAppendRequested(action))
        else:
            self.intent_emitted.emit(ActionReplaceRequested(editing_index, action))

    def _on_action_selected(self, index: int) -> None:
        if not self._valid_index(index):
            return
        self.intent_emitted.emit(ActionSelected(index))
        self._begin_edit(index)

    def _on_canvas_selected(self, index: int) -> None:
        if not self._valid_index(index):
            return
        self.intent_emitted.emit(ActionSelected(index))
        self._begin_edit(index)

    def _on_point_dragged(self, index: int, point: object) -> None:
        if not self._valid_index(index) or not isinstance(point, GeoPoint):
            self._show_error("The map returned an invalid coordinate drag.")
            return
        self.intent_emitted.emit(ActionMoveRequested(index, point))

    def _on_delete(self) -> None:
        index = self._action_list.currentRow()
        if not self._valid_index(index):
            self._show_error("Select a mission point to delete.")
            return
        self._clear_pending()
        self.intent_emitted.emit(ActionDeleteRequested(index))

    def _on_clear(self) -> None:
        self._clear_pending()
        self.intent_emitted.emit(ClearRequested())

    def _on_undo(self) -> None:
        self._clear_pending()
        self.intent_emitted.emit(UndoRequested())

    def _on_remove_land(self) -> None:
        self._clear_pending()
        self.intent_emitted.emit(RemoveLandRequested())

    def _begin_edit(self, index: int) -> None:
        action = self._snapshot.actions[index]
        self._editing_index = index
        self._pending_point = action.point
        self._pending_title.setText(f"Edit point {index + 1}")
        self._pending_coordinates.setText(_format_point(action.point))
        if isinstance(action, ProceedAction):
            self._set_action_kind(ActionKind.PROCEED)
            altitude_m = action.altitude_m
        elif isinstance(action, HoldAction):
            self._set_action_kind(ActionKind.HOLD)
            altitude_m = action.altitude_m
            self._hold_time.setText(f"{action.hold_time_s:g}")
        elif isinstance(action, CircleAction):
            self._set_action_kind(ActionKind.CIRCLE)
            altitude_m = action.altitude_m
            self._radius.setText(f"{action.radius_m:g}")
        else:
            self._set_action_kind(ActionKind.LAND)
            altitude_m = action.approach_altitude_m
        self._altitude.setText(f"{altitude_m:g}")
        self._pending_panel.setVisible(True)
        self._refresh_map()

    def _clear_pending(self) -> None:
        self._pending_point = None
        self._editing_index = None
        self._pending_panel.setVisible(False)
        self._clear_error()
        self._refresh_map()

    def _update_action_fields(self) -> None:
        kind = self._current_action_kind()
        is_hold = kind is ActionKind.HOLD
        is_circle = kind is ActionKind.CIRCLE
        self._hold_label.setVisible(is_hold)
        self._hold_time.setVisible(is_hold)
        self._radius_label.setVisible(is_circle)
        self._radius.setVisible(is_circle)
        self._circle_cue.setVisible(is_circle)
        self._altitude_label.setText(
            "Approach altitude Above Home (m)"
            if kind is ActionKind.LAND
            else "Altitude Above Home (m)"
        )

    def _set_action_kind(self, kind: ActionKind) -> None:
        index = self._action_kind.findData(kind.value)
        self._action_kind.setCurrentIndex(index)
        self._update_action_fields()

    def _current_action_kind(self) -> ActionKind:
        value = self._action_kind.currentData()
        return ActionKind(cast(str, value))

    def _rebuild_action_list(self) -> None:
        selected = self._snapshot.selected_index
        self._action_list.blockSignals(True)
        self._action_list.clear()
        for index, action in enumerate(self._snapshot.actions):
            self._action_list.addItem(_action_list_text(index, action))
        if selected is not None and self._valid_index(selected):
            self._action_list.setCurrentRow(selected)
        self._action_list.blockSignals(False)
        has_actions = bool(self._snapshot.actions)
        has_selection = selected is not None and self._valid_index(selected)
        self._delete.setEnabled(has_selection)
        self._undo.setEnabled(has_actions)
        self._clear.setEnabled(has_actions)

    def _render_summary(self) -> None:
        settings = self._snapshot.settings
        if settings is None:
            self._summary.setText("Confirm Takeoff to begin the mission summary.")
            return
        lines = [
            f"Take off to {settings.takeoff_altitude_m:g} m Above Home at "
            f"{settings.cruise_speed_m_s:g} m/s."
        ]
        for index, action in enumerate(self._snapshot.actions):
            lines.append(_action_summary(index, action))
        if not self._snapshot.is_closed:
            lines.append("Mission is open — add Land when the route is complete.")
        self._summary.setText("\n".join(lines))

    def _refresh_map(self) -> None:
        pending = self._pending_point if self._editing_index is None else None
        selected = (
            self._editing_index
            if self._editing_index is not None
            else self._snapshot.selected_index
        )
        self._map.render_mission(self._snapshot.actions, pending, selected)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    def _clear_error(self) -> None:
        self._error.clear()
        self._error.setVisible(False)

    def _valid_index(self, index: int) -> bool:
        return 0 <= index < len(self._snapshot.actions)


def _card(name: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName(name)
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return frame


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "sectionTitle")
    return label


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "fieldLabel")
    return label


def _number_input(name: str, placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setObjectName(name)
    field.setAccessibleName(name.replace("Input", "").replace("Action", "Action "))
    field.setPlaceholderText(placeholder)
    field.setClearButtonEnabled(True)
    return field


def _parse_finite(text: str, label: str) -> float:
    try:
        value = float(text.strip())
    except ValueError as error:
        raise ValueError(f"{label} must be a number.") from error
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


def _parse_positive(text: str, label: str) -> float:
    value = _parse_finite(text, label)
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def _format_point(point: GeoPoint) -> str:
    return f"{point.latitude_deg:.6f}, {point.longitude_deg:.6f}"


def _action_list_text(index: int, action: MissionAction) -> str:
    sequence = index + 1
    if isinstance(action, ProceedAction):
        return f"{sequence}. Proceed  •  {action.altitude_m:g} m"
    if isinstance(action, HoldAction):
        return f"{sequence}. Hold  •  {action.altitude_m:g} m  •  {action.hold_time_s:g} s"
    if isinstance(action, CircleAction):
        return f"{sequence}. Circle CW  •  {action.altitude_m:g} m  •  r {action.radius_m:g} m"
    return f"{sequence}. Land  •  approach {action.approach_altitude_m:g} m"


def _action_summary(index: int, action: MissionAction) -> str:
    sequence = index + 1
    point = _format_point(action.point)
    if isinstance(action, ProceedAction):
        return f"{sequence}. Proceed to {point} at {action.altitude_m:g} m Above Home."
    if isinstance(action, HoldAction):
        return (
            f"{sequence}. Hold at {point}, {action.altitude_m:g} m Above Home, "
            f"for {action.hold_time_s:g} seconds."
        )
    if isinstance(action, CircleAction):
        return (
            f"{sequence}. Circle once clockwise at {point}, {action.altitude_m:g} m "
            f"Above Home, radius {action.radius_m:g} m."
        )
    return (
        f"{sequence}. Approach {point} at {action.approach_altitude_m:g} m Above Home, "
        "then Land there."
    )


_STYLE_SHEET = """
QWidget#missionBuilder {
    background: #f5f7f6;
    color: #183b3a;
    font-size: 13px;
}
QLabel#builderTitle { font-size: 28px; font-weight: 700; color: #123b39; }
QLabel#builderSubtitle { color: #607674; font-size: 14px; }
QLabel#offlineBadge {
    background: #dcebe8;
    color: #176b68;
    border: 1px solid #b8d6d1;
    border-radius: 11px;
    padding: 7px 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QWidget#missionSidebar { background: transparent; }
QFrame#takeoffPanel, QFrame#missionPanel, QFrame#pendingPointPanel {
    background: #ffffff;
    border: 1px solid #d9e3e1;
    border-radius: 12px;
    padding: 12px;
}
QLabel[role="sectionTitle"] { font-size: 17px; font-weight: 700; color: #163f3d; }
QLabel[role="fieldLabel"] { font-size: 12px; font-weight: 600; color: #496663; }
QLineEdit, QComboBox, QListWidget {
    background: #fbfdfc;
    border: 1px solid #bfd0cd;
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: #176b68;
}
QLineEdit:focus, QComboBox:focus, QListWidget:focus { border: 2px solid #23827d; }
QPushButton {
    background: #e5efed;
    border: 1px solid #bfd0cd;
    border-radius: 7px;
    padding: 8px 10px;
    color: #204c49;
    font-weight: 600;
}
QPushButton:hover { background: #d7e7e4; }
QPushButton:disabled { color: #91a29f; background: #eef2f1; }
QPushButton#primaryActionButton, QPushButton#confirmTakeoffButton,
QPushButton#confirmActionButton {
    background: #176b68;
    border-color: #176b68;
    color: white;
    font-weight: 700;
}
QPushButton#primaryActionButton { font-size: 17px; padding: 12px; }
QPushButton#removeLandButton { background: #f6e4df; color: #8a3c28; border-color: #e7c1b6; }
QLabel#builderError {
    background: #fff0ed;
    color: #8d2e25;
    border: 1px solid #efc6bf;
    border-radius: 8px;
    padding: 9px;
}
QLabel#missionSummary {
    background: #f1f6f5;
    border-radius: 8px;
    color: #365c59;
    padding: 10px;
}
QLabel#circleDirectionCue { color: #9c5a14; font-weight: 700; }
"""
