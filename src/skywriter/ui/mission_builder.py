"""Offline beginner mission-builder presentation using typed intent contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias, cast

from PySide6.QtCore import Qt, QTimer, Signal
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
from skywriter.ui.map import (
    MapBridgeError,
    MissionMapHost,
    ProviderState,
    ProviderStatusChanged,
    TileProvider,
    parse_coordinate_input,
)

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
class _PendingCommit:
    intent: ActionAppendRequested | ActionReplaceRequested
    prior_actions: tuple[MissionAction, ...]


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

    def __init__(self, map_host: MissionMapHost | None = None) -> None:
        super().__init__()
        self.setObjectName("missionBuilder")
        self.setAccessibleName("Mission builder")
        self.setMinimumSize(680, 480)
        self._snapshot = MissionBuilderSnapshot()
        self._pending_point: GeoPoint | None = None
        self._editing_index: int | None = None
        self._pending_commit: _PendingCommit | None = None
        self._build_ui(map_host)
        self._connect_signals()
        self.render_snapshot(self._snapshot)

    @property
    def snapshot(self) -> MissionBuilderSnapshot:
        return self._snapshot

    @property
    def pending_point(self) -> GeoPoint | None:
        return self._pending_point

    @property
    def editing_index(self) -> int | None:
        return self._editing_index

    @property
    def map_canvas(self) -> MissionMapHost:
        return self._map

    def render_snapshot(self, snapshot: MissionBuilderSnapshot) -> None:
        """Render a complete immutable snapshot supplied by the host adapter."""

        self._snapshot = snapshot
        commit_failed = self._pending_commit is not None and snapshot.error_message is not None
        commit_succeeded = self._commit_is_reflected(snapshot)
        if commit_failed:
            self._finish_pending_commit()
            self._show_pending_error(
                snapshot.error_message or "The point was rejected.", self._action_kind
            )
        elif commit_succeeded:
            committed = self._pending_commit
            self._finish_pending_commit()
            self._clear_pending()
            if committed is not None:
                if isinstance(committed.intent, ActionAppendRequested):
                    index = len(snapshot.actions)
                    action = committed.intent.action
                else:
                    index = committed.intent.index + 1
                    action = committed.intent.action
                self._show_success(f"Point {index} confirmed as {_action_name(action)}.")

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
        if self._success.isVisible():
            self._action_list.scrollToBottom()
        self._render_summary()
        if snapshot.error_message and not commit_failed:
            self._show_error(snapshot.error_message)
        elif not self._pending_point:
            self._clear_error()
        if snapshot.selected_index is not None and self._valid_index(snapshot.selected_index):
            if not (commit_failed and self._editing_index == snapshot.selected_index):
                self._begin_edit(snapshot.selected_index)
        elif self._editing_index is not None and not self._valid_index(self._editing_index):
            self._clear_pending()
        self._refresh_map()

    def reset_transient_editor(self) -> None:
        """Clear pending/edit UI after a successful New or Load operation."""

        self._finish_pending_commit()
        self._clear_pending()
        self._clear_success()

    def begin_pending(self, point: GeoPoint) -> None:
        """Public map-adapter hook for a validated decimal-degree click."""

        self._on_map_clicked(point)

    def _build_ui(self, map_host: MissionMapHost | None) -> None:
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
        offline_badge = QLabel("VEHICLE LINK OFFLINE")
        offline_badge.setObjectName("offlineBadge")
        offline_badge.setAccessibleName("Vehicle link offline")
        header.addWidget(offline_badge)
        root.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(18)
        map_panel = QFrame()
        map_panel.setObjectName("mapPanel")
        map_layout = QVBoxLayout(map_panel)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(8)
        map_toolbar = QHBoxLayout()
        provider_label = QLabel("Basemap")
        provider_label.setObjectName("mapProviderLabel")
        map_toolbar.addWidget(provider_label)
        self._map_provider = QComboBox()
        self._map_provider.setObjectName("mapProviderInput")
        self._map_provider.setAccessibleName("Basemap provider")
        self._map_provider.addItem("No basemap (offline)", TileProvider.OFFLINE.value)
        self._map_provider.addItem(
            "OpenStreetMap Standard (network)", TileProvider.OPENSTREETMAP.value
        )
        map_toolbar.addWidget(self._map_provider)
        self._map_retry = QPushButton("Retry")
        self._map_retry.setObjectName("mapProviderRetryButton")
        self._map_retry.setAccessibleName("Retry selected basemap provider")
        self._map_retry.setEnabled(False)
        map_toolbar.addWidget(self._map_retry)
        map_toolbar.addStretch()
        map_layout.addLayout(map_toolbar)
        self._map_provider_status = QLabel("Offline — local planning grid; no network requests.")
        self._map_provider_status.setObjectName("mapProviderStatus")
        self._map_provider_status.setWordWrap(True)
        self._map_provider_status.setProperty("providerState", ProviderState.OFFLINE.value)
        map_layout.addWidget(self._map_provider_status)

        coordinate_toolbar = QHBoxLayout()
        coordinate_toolbar.addWidget(QLabel("Go to"))
        self._map_latitude = QLineEdit()
        self._map_latitude.setObjectName("mapLatitudeInput")
        self._map_latitude.setAccessibleName("Map latitude in decimal degrees")
        self._map_latitude.setPlaceholderText("Latitude −90..90")
        self._map_latitude.setMaximumWidth(150)
        coordinate_toolbar.addWidget(self._map_latitude)
        self._map_longitude = QLineEdit()
        self._map_longitude.setObjectName("mapLongitudeInput")
        self._map_longitude.setAccessibleName("Map longitude in decimal degrees")
        self._map_longitude.setPlaceholderText("Longitude −180..180")
        self._map_longitude.setMaximumWidth(160)
        coordinate_toolbar.addWidget(self._map_longitude)
        self._map_go = QPushButton("Go / recenter")
        self._map_go.setObjectName("mapGoToCoordinatesButton")
        coordinate_toolbar.addWidget(self._map_go)
        self._map_authoritative_center = QPushButton("Center Home / Vehicle")
        self._map_authoritative_center.setObjectName("mapAuthoritativeCenterButton")
        self._map_authoritative_center.setEnabled(False)
        self._map_authoritative_center.setToolTip(
            "Unavailable: this isolated mission builder has no authoritative current "
            "Home or Vehicle point."
        )
        coordinate_toolbar.addWidget(self._map_authoritative_center)
        coordinate_toolbar.addStretch()
        map_layout.addLayout(coordinate_toolbar)
        self._map_coordinate_feedback = QLabel()
        self._map_coordinate_feedback.setObjectName("mapCoordinateFeedback")
        self._map_coordinate_feedback.setVisible(False)
        map_layout.addWidget(self._map_coordinate_feedback)
        self._map = map_host or MissionMapHost()
        map_layout.addWidget(self._map, 1)
        content.addWidget(map_panel, 3)

        self._side_scroll = QScrollArea()
        self._side_scroll.setObjectName("missionSidebarScroll")
        self._side_scroll.setWidgetResizable(True)
        self._side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._side_scroll.setMinimumWidth(410)
        self._side_scroll.setMaximumWidth(430)
        self._side_scroll.setFrameShape(QFrame.Shape.NoFrame)
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
        self._side_scroll.setWidget(side)
        content.addWidget(self._side_scroll, 1)
        root.addLayout(content, 1)

    def _build_takeoff_panel(self) -> QFrame:
        panel = _card("takeoffPanel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        layout.addWidget(_section_title("1  Takeoff setup"))
        altitude_label = _field_label("Takeoff altitude Above Home (m)")
        layout.addWidget(altitude_label)
        self._takeoff_altitude = _number_input(
            "takeoffAltitudeInput", "e.g. 25", "Takeoff altitude Above Home in meters"
        )
        altitude_label.setBuddy(self._takeoff_altitude)
        layout.addWidget(self._takeoff_altitude)
        speed_label = _field_label("Mission cruise speed (m/s)")
        layout.addWidget(speed_label)
        self._cruise_speed = _number_input(
            "cruiseSpeedInput", "e.g. 6", "Mission cruise speed in meters per second"
        )
        speed_label.setBuddy(self._cruise_speed)
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
        self._takeoff_error = _inline_error("takeoffValidationError", "Takeoff validation error")
        layout.addWidget(self._takeoff_error)
        self._confirm_takeoff = QPushButton("Confirm Takeoff")
        self._confirm_takeoff.setObjectName("confirmTakeoffButton")
        self._confirm_takeoff.setAccessibleName("Confirm Takeoff settings")
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
        self._clear = QPushButton("Clear mission")
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
        self._success = QLabel()
        self._success.setObjectName("builderSuccess")
        self._success.setAccessibleName("Mission builder success")
        self._success.setWordWrap(True)
        self._success.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._success.setVisible(False)
        layout.addWidget(self._success)
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
        action_label = _field_label("Action")
        layout.addWidget(action_label)
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
        action_label.setBuddy(self._action_kind)
        layout.addWidget(self._action_kind)
        self._altitude_label = _field_label("Altitude Above Home (m)")
        layout.addWidget(self._altitude_label)
        self._altitude = _number_input(
            "actionAltitudeInput", "e.g. 30", "Point altitude Above Home in meters"
        )
        self._altitude_label.setBuddy(self._altitude)
        layout.addWidget(self._altitude)
        self._hold_label = _field_label("Hold time (seconds)")
        self._hold_time = _number_input("holdTimeInput", "e.g. 10", "Hold time in seconds")
        self._hold_label.setBuddy(self._hold_time)
        layout.addWidget(self._hold_label)
        layout.addWidget(self._hold_time)
        self._radius_label = _field_label("Circle radius (meters)")
        self._radius = _number_input("circleRadiusInput", "e.g. 15", "Circle radius in meters")
        self._radius_label.setBuddy(self._radius)
        layout.addWidget(self._radius_label)
        layout.addWidget(self._radius)
        cue = QLabel("Circle is one clockwise turn")
        cue.setObjectName("circleDirectionCue")
        layout.addWidget(cue)
        self._circle_cue = cue
        self._pending_error = _inline_error(
            "pendingPointValidationError", "Pending point validation error"
        )
        layout.addWidget(self._pending_error)
        actions = QHBoxLayout()
        self._cancel_pending = QPushButton("Cancel")
        self._cancel_pending.setObjectName("cancelPendingButton")
        self._cancel_pending.setAccessibleName("Cancel pending mission point")
        self._confirm_action = QPushButton("Confirm point")
        self._confirm_action.setObjectName("confirmActionButton")
        self._confirm_action.setAccessibleName("Confirm pending mission point")
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
        self._map_provider.currentIndexChanged.connect(self._on_map_provider_changed)
        self._map_retry.clicked.connect(self._map.retry_tiles)
        self._map_go.clicked.connect(self._on_go_to_coordinates)
        self._map_latitude.returnPressed.connect(self._on_go_to_coordinates)
        self._map_longitude.returnPressed.connect(self._on_go_to_coordinates)
        self._map.map_clicked.connect(self._on_map_clicked)
        self._map.point_selected.connect(self._on_canvas_selected)
        self._map.point_dragged.connect(self._on_point_dragged)
        self._map.provider_status_changed.connect(self._on_provider_status_changed)
        QWidget.setTabOrder(self._action_kind, self._altitude)
        QWidget.setTabOrder(self._altitude, self._hold_time)
        QWidget.setTabOrder(self._hold_time, self._radius)
        QWidget.setTabOrder(self._radius, self._cancel_pending)
        QWidget.setTabOrder(self._cancel_pending, self._confirm_action)

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
        except ValueError as error:
            self._show_takeoff_error(str(error), self._takeoff_altitude)
            return
        try:
            speed_m_s = _parse_positive(self._cruise_speed.text(), "Cruise speed")
        except ValueError as error:
            self._show_takeoff_error(str(error), self._cruise_speed)
            return
        if not self._warning_ack.isChecked():
            self._show_takeoff_error(
                "Acknowledge the obstacle warning before confirming Takeoff.",
                self._warning_ack,
            )
            return
        self._takeoff_error.setVisible(False)
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
        for field in (self._altitude, self._hold_time, self._radius, self._action_kind):
            field.setAccessibleDescription("")
        self._pending_panel.setVisible(True)
        self._pending_error.setVisible(False)
        self._clear_success()
        self._clear_error()
        self._refresh_map()
        self._action_kind.setFocus()
        self._ensure_visible(self._pending_panel)

    def _on_confirm_action(self) -> None:
        if self._pending_commit is not None:
            return
        if self._pending_point is None:
            self._show_error("Click the map before confirming a point.")
            return
        try:
            altitude_m = _parse_finite(self._altitude.text(), "Altitude")
        except ValueError as error:
            self._show_pending_error(str(error), self._altitude)
            return

        kind = self._current_action_kind()
        if self._editing_land():
            kind = ActionKind.LAND
        action: MissionAction
        if kind is ActionKind.PROCEED:
            action = ProceedAction(self._pending_point, altitude_m)
        elif kind is ActionKind.HOLD:
            try:
                hold_time_s = _parse_positive(self._hold_time.text(), "Hold time")
            except ValueError as error:
                self._show_pending_error(str(error), self._hold_time)
                return
            action = HoldAction(self._pending_point, altitude_m, hold_time_s)
        elif kind is ActionKind.CIRCLE:
            try:
                radius_m = _parse_positive(self._radius.text(), "Circle radius")
            except ValueError as error:
                self._show_pending_error(str(error), self._radius)
                return
            action = CircleAction(self._pending_point, altitude_m, radius_m)
        else:
            action = LandAction(self._pending_point, altitude_m)

        editing_index = self._editing_index
        intent: ActionAppendRequested | ActionReplaceRequested
        if editing_index is None:
            intent = ActionAppendRequested(action)
        else:
            intent = ActionReplaceRequested(editing_index, action)
        self._pending_commit = _PendingCommit(intent, self._snapshot.actions)
        self._pending_error.setVisible(False)
        self._confirm_action.setEnabled(False)
        self._cancel_pending.setEnabled(False)
        self._confirm_action.setText("Confirming…")
        self.intent_emitted.emit(intent)

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
        if isinstance(self._snapshot.actions[index], LandAction):
            self._show_error("Use Remove Land and reopen to remove the final Land point.")
            return
        self._clear_pending()
        self.intent_emitted.emit(ActionDeleteRequested(index))

    def _on_clear(self) -> None:
        self._clear_pending()
        self.intent_emitted.emit(ClearRequested())

    def _on_undo(self) -> None:
        if self._snapshot.is_closed:
            self._show_error("Use Remove Land and reopen before undoing mission points.")
            return
        self._clear_pending()
        self.intent_emitted.emit(UndoRequested())

    def _on_remove_land(self) -> None:
        self._clear_pending()
        self.intent_emitted.emit(RemoveLandRequested())

    def _on_map_provider_changed(self) -> None:
        value = cast(str, self._map_provider.currentData())
        provider = TileProvider(value)
        self._map_retry.setEnabled(provider is TileProvider.OPENSTREETMAP)
        self._map.set_tile_provider(provider)

    def _on_provider_status_changed(self, value: object) -> None:
        if not isinstance(value, ProviderStatusChanged):
            return
        self._map_provider_status.setProperty("providerState", value.state.value)
        if value.state is ProviderState.OFFLINE:
            message = "Offline — local planning grid; no network requests."
        elif value.state is ProviderState.LOADING:
            message = (
                "Loading OpenStreetMap Standard… "
                f"{value.loaded_tiles}/{value.requested_tiles} visible tiles received."
            )
        elif value.state is ProviderState.ONLINE:
            message = (
                f"Online — OpenStreetMap Standard is visible ({value.loaded_tiles} tiles received)."
            )
        elif value.state is ProviderState.PARTIAL:
            message = (
                f"Partial map — {value.loaded_tiles} tiles received, "
                f"{value.error_tiles} failed, {value.pending_tiles} unanswered. "
                "Check access to tile.openstreetmap.org, then Retry."
            )
        else:
            message = (
                "OpenStreetMap unavailable — no visible tiles were received "
                f"({value.error_tiles} failed, {value.pending_tiles} unanswered). "
                "Check internet, TLS/DNS, or firewall access to "
                "tile.openstreetmap.org, then Retry."
            )
        self._map_provider_status.setText(message)
        self._map_provider_status.style().unpolish(self._map_provider_status)
        self._map_provider_status.style().polish(self._map_provider_status)

    def _on_go_to_coordinates(self) -> None:
        try:
            point = parse_coordinate_input(self._map_latitude.text(), self._map_longitude.text())
        except MapBridgeError as error:
            self._map_coordinate_feedback.setProperty("valid", False)
            self._map_coordinate_feedback.setText(str(error))
            self._map_coordinate_feedback.setVisible(True)
            return
        self._map.recenter(point)
        self._map_coordinate_feedback.setProperty("valid", True)
        self._map_coordinate_feedback.setText(
            f"Centered on operator-entered coordinates: {_format_point(point)}."
        )
        self._map_coordinate_feedback.setVisible(True)

    def _begin_edit(self, index: int) -> None:
        action = self._snapshot.actions[index]
        self._editing_index = index
        self._pending_point = action.point
        self._hold_time.clear()
        self._radius.clear()
        self._pending_error.setVisible(False)
        self._clear_success()
        self._pending_title.setText(f"Edit point {index + 1}")
        self._pending_coordinates.setText(_format_point(action.point))
        if isinstance(action, ProceedAction):
            self._set_action_kind(ActionKind.PROCEED)
            self._action_kind.setEnabled(True)
            altitude_m = action.altitude_m
        elif isinstance(action, HoldAction):
            self._set_action_kind(ActionKind.HOLD)
            self._action_kind.setEnabled(True)
            altitude_m = action.altitude_m
            self._hold_time.setText(f"{action.hold_time_s:g}")
        elif isinstance(action, CircleAction):
            self._set_action_kind(ActionKind.CIRCLE)
            self._action_kind.setEnabled(True)
            altitude_m = action.altitude_m
            self._radius.setText(f"{action.radius_m:g}")
        else:
            self._set_action_kind(ActionKind.LAND)
            self._action_kind.setEnabled(False)
            altitude_m = action.approach_altitude_m
        self._altitude.setText(f"{altitude_m:g}")
        self._pending_panel.setVisible(True)
        self._pending_error.setVisible(False)
        self._refresh_map()
        self._ensure_visible(self._pending_panel)

    def _clear_pending(self) -> None:
        self._pending_point = None
        self._editing_index = None
        self._action_kind.setEnabled(True)
        self._pending_panel.setVisible(False)
        self._pending_error.setVisible(False)
        for field in (self._altitude, self._hold_time, self._radius, self._action_kind):
            field.setAccessibleDescription("")
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
        selected_is_land = has_selection and isinstance(
            self._snapshot.actions[cast(int, selected)], LandAction
        )
        self._delete.setEnabled(has_selection and not selected_is_land)
        self._undo.setEnabled(has_actions and not self._snapshot.is_closed)
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
        self._ensure_visible(self._error)

    def _clear_error(self) -> None:
        self._error.clear()
        self._error.setVisible(False)

    def _show_takeoff_error(self, message: str, field: QWidget) -> None:
        self._takeoff_error.setText(message)
        self._takeoff_error.setVisible(True)
        self._focus_invalid(field, message)
        self._ensure_visible(self._takeoff_error)

    def _show_pending_error(self, message: str, field: QWidget) -> None:
        self._pending_error.setText(message)
        self._pending_error.setVisible(True)
        self._focus_invalid(field, message)
        self._ensure_visible(self._pending_error)

    def _focus_invalid(self, field: QWidget, message: str) -> None:
        field.setAccessibleDescription(message)
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        if isinstance(field, QLineEdit):
            field.selectAll()
        self._ensure_visible(field)

    def _ensure_visible(self, widget: QWidget) -> None:
        QTimer.singleShot(
            0,
            self,
            lambda: self._side_scroll.ensureWidgetVisible(widget, 12, 12),
        )

    def _show_success(self, message: str) -> None:
        self._success.setText(message)
        self._success.setVisible(True)
        self._action_list.scrollToBottom()
        self._success.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_visible(self._success)

    def _clear_success(self) -> None:
        self._success.clear()
        self._success.setVisible(False)

    def _finish_pending_commit(self) -> None:
        self._pending_commit = None
        self._confirm_action.setText("Confirm point")
        self._confirm_action.setEnabled(True)
        self._cancel_pending.setEnabled(True)

    def _commit_is_reflected(self, snapshot: MissionBuilderSnapshot) -> bool:
        commit = self._pending_commit
        if commit is None or snapshot.error_message is not None:
            return False
        intent = commit.intent
        if isinstance(intent, ActionAppendRequested):
            return (
                len(snapshot.actions) == len(commit.prior_actions) + 1
                and snapshot.actions[:-1] == commit.prior_actions
                and snapshot.actions[-1] == intent.action
            )
        return (
            len(snapshot.actions) == len(commit.prior_actions)
            and 0 <= intent.index < len(snapshot.actions)
            and snapshot.actions[intent.index] == intent.action
        )

    def _valid_index(self, index: int) -> bool:
        return 0 <= index < len(self._snapshot.actions)

    def _editing_land(self) -> bool:
        return self._editing_index is not None and isinstance(
            self._snapshot.actions[self._editing_index], LandAction
        )


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


def _inline_error(name: str, accessible_name: str) -> QLabel:
    label = QLabel()
    label.setObjectName(name)
    label.setAccessibleName(accessible_name)
    label.setWordWrap(True)
    label.setProperty("role", "inlineError")
    label.setVisible(False)
    return label


def _number_input(name: str, placeholder: str, accessible_name: str) -> QLineEdit:
    field = QLineEdit()
    field.setObjectName(name)
    field.setAccessibleName(accessible_name)
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


def _action_name(action: MissionAction) -> str:
    if isinstance(action, ProceedAction):
        return "Proceed"
    if isinstance(action, HoldAction):
        return "Hold"
    if isinstance(action, CircleAction):
        return "Circle"
    return "Land"


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
QLabel#mapProviderStatus {
    border-radius: 7px;
    background: #edf3f2;
    color: #365c59;
    padding: 6px 9px;
}
QLabel#mapProviderStatus[providerState="loading"] {
    background: #fff3cd;
    color: #664d03;
}
QLabel#mapProviderStatus[providerState="online"] {
    background: #dff3e5;
    color: #205c35;
}
QLabel#mapProviderStatus[providerState="partial"],
QLabel#mapProviderStatus[providerState="unavailable"] {
    background: #fff0ed;
    color: #8d2e25;
}
QLabel#mapCoordinateFeedback[valid="true"] { color: #205c35; font-weight: 600; }
QLabel#mapCoordinateFeedback[valid="false"] { color: #8d2e25; font-weight: 600; }
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
QLabel[role="inlineError"] {
    background: #fff0ed;
    color: #8d2e25;
    border: 1px solid #efc6bf;
    border-radius: 8px;
    padding: 7px;
    font-weight: 600;
}
QLabel#builderSuccess {
    background: #e7f5ee;
    color: #155b3d;
    border: 1px solid #a8d8c1;
    border-radius: 8px;
    padding: 8px;
    font-weight: 700;
}
QLabel#missionSummary {
    background: #f1f6f5;
    border-radius: 8px;
    color: #365c59;
    padding: 10px;
}
QLabel#circleDirectionCue { color: #9c5a14; font-weight: 700; }
"""
