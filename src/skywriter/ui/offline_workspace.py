"""Production composition for SKYWriter's complete offline mission workflow."""

from __future__ import annotations

import math
import os
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from skywriter.application import OfflineMissionError, OfflineMissionService
from skywriter.domain.compiled import CompiledMissionItem
from skywriter.domain.mission import MissionEditError, MissionSettings
from skywriter.domain.validation import MissionValidationError
from skywriter.infrastructure.json_repository import (
    JsonMissionRepository,
    MissionJsonError,
    MissionRepositoryError,
)
from skywriter.ui.mission_builder import (
    OBSTACLE_WARNING_TEXT,
    ActionAppendRequested,
    ActionDeleteRequested,
    ActionMoveRequested,
    ActionReplaceRequested,
    ActionSelected,
    ClearRequested,
    MissionBuilderSnapshot,
    MissionBuilderWidget,
    RemoveLandRequested,
    TakeoffRequested,
    UndoRequested,
)

_WORKFLOW_ERRORS = (
    IndexError,
    MissionEditError,
    MissionJsonError,
    MissionRepositoryError,
    MissionValidationError,
    OfflineMissionError,
    TypeError,
    ValueError,
)


class MissionSettingsDialog(QDialog):
    """Edit mission-wide Takeoff settings with the same required warning."""

    settings_confirmed = Signal(object)

    def __init__(self, settings: MissionSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("missionSettingsDialog")
        self.setWindowTitle("Edit Takeoff settings")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Takeoff altitude Above Home (m)"))
        self._altitude = QLineEdit(f"{settings.takeoff_altitude_m:g}")
        self._altitude.setObjectName("editTakeoffAltitudeInput")
        layout.addWidget(self._altitude)
        layout.addWidget(QLabel("Mission cruise speed (m/s)"))
        self._speed = QLineEdit(f"{settings.cruise_speed_m_s:g}")
        self._speed.setObjectName("editCruiseSpeedInput")
        layout.addWidget(self._speed)
        warning = QLabel(OBSTACLE_WARNING_TEXT)
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self._acknowledgment = QCheckBox("I acknowledge this obstacle warning.")
        self._acknowledgment.setObjectName("editObstacleWarningCheck")
        self._acknowledgment.setChecked(settings.obstacle_warning_acknowledged)
        layout.addWidget(self._acknowledgment)
        self._error = QLabel()
        self._error.setObjectName("settingsError")
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #a52620;")
        self._error.setVisible(False)
        layout.addWidget(self._error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _confirm(self) -> None:
        try:
            altitude = _finite_number(self._altitude.text(), "Takeoff altitude")
            speed = _positive_number(self._speed.text(), "Cruise speed")
            if not self._acknowledgment.isChecked():
                raise ValueError("Acknowledge the obstacle warning before saving settings.")
        except ValueError as error:
            self._error.setText(str(error))
            self._error.setVisible(True)
            return
        self.settings_confirmed.emit(MissionSettings(altitude, speed, True))
        self.accept()


class OfflineMissionWorkspace(QWidget):
    """Wire the real editor, JSON repository, and compiler into one offline flow."""

    def __init__(
        self,
        service: OfflineMissionService | None = None,
        parent: QWidget | None = None,
        *,
        save_path_picker: Callable[[str], str | os.PathLike[str] | None] | None = None,
        load_path_picker: Callable[[], str | os.PathLike[str] | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("offlineMissionWorkspace")
        self._service = service or OfflineMissionService(JsonMissionRepository())
        self._save_path_picker = save_path_picker
        self._load_path_picker = load_path_picker
        self._build_ui()
        self._builder.intent_emitted.connect(self._on_builder_intent)
        self._render()

    @property
    def service(self) -> OfflineMissionService:
        return self._service

    @property
    def builder(self) -> MissionBuilderWidget:
        return self._builder

    def new_mission(self) -> None:
        self._service.new_mission()
        self._builder.reset_transient_editor()
        self._render()

    def update_settings(self, settings: MissionSettings) -> None:
        self._run(lambda: self._service.update_settings(settings))

    def save_mission(self, path: str | os.PathLike[str]) -> None:
        self._run(lambda: self._service.save(path))

    def load_mission(self, path: str | os.PathLike[str]) -> None:
        self._run(lambda: self._service.load(path), reset_transient_editor=True)

    def compile_preview(self) -> None:
        self._run(self._service.compile_preview)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        toolbar = QHBoxLayout()
        self._new_button = QPushButton("New")
        self._new_button.setObjectName("newMissionButton")
        self._edit_settings_button = QPushButton("Edit settings")
        self._edit_settings_button.setObjectName("editMissionSettingsButton")
        self._save_button = QPushButton("Save")
        self._save_button.setObjectName("saveMissionButton")
        self._load_button = QPushButton("Load")
        self._load_button.setObjectName("loadMissionButton")
        self._compile_button = QPushButton("Review & Compile")
        self._compile_button.setObjectName("compileMissionButton")
        for button in (
            self._new_button,
            self._edit_settings_button,
            self._save_button,
            self._load_button,
            self._compile_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch()
        root.addLayout(toolbar)
        state_row = QHBoxLayout()
        self._status = QLabel()
        self._status.setObjectName("offlineWorkflowStatus")
        self._status.setStyleSheet("font-weight: 600; color: #163f3d;")
        self._path = QLabel()
        self._path.setObjectName("missionPathStatus")
        self._path.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        state_row.addWidget(self._status, 2)
        state_row.addWidget(self._path, 3)
        root.addLayout(state_row)
        self._validation = QLabel()
        self._validation.setObjectName("structuralValidationStatus")
        self._validation.setWordWrap(True)
        root.addWidget(self._validation)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("offlineWorkflowSplitter")
        self._builder = MissionBuilderWidget()
        splitter.addWidget(self._builder)
        self._preview = self._build_preview_panel()
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)
        self._new_button.clicked.connect(self.new_mission)
        self._edit_settings_button.clicked.connect(self._open_settings_editor)
        self._save_button.clicked.connect(self._choose_save_path)
        self._load_button.clicked.connect(self._choose_load_path)
        self._compile_button.clicked.connect(self.compile_preview)

    def _build_preview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("compiledPreviewPanel")
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(260)
        layout = QVBoxLayout(panel)
        heading = QLabel("Deterministic native sequence")
        heading.setStyleSheet("font-size: 18px; font-weight: 700; color: #163f3d;")
        layout.addWidget(heading)
        explanation = QLabel(
            "Read-only output from the accepted mission compiler. Sequence numbers, "
            "commands, coordinates, parameters, and frames are shown exactly as compiled."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self._compiled_items = QListWidget()
        self._compiled_items.setObjectName("compiledMissionItems")
        self._compiled_items.setAccessibleName("Compiled native mission sequence")
        self._compiled_items.setWordWrap(True)
        self._compiled_items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._compiled_items.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._compiled_items.setSpacing(4)
        layout.addWidget(self._compiled_items, 1)
        disclaimer = QLabel(
            "OFFLINE STRUCTURAL REVIEW ONLY — no vehicle connection or upload. "
            "Passing validation does not establish obstacle clearance or flight safety."
        )
        disclaimer.setObjectName("compiledPreviewDisclaimer")
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(
            "padding: 10px; background: #fff3cd; color: #664d03; font-weight: 600;"
        )
        layout.addWidget(disclaimer)
        panel.setVisible(False)
        return panel

    def _on_builder_intent(self, intent: object) -> None:
        if isinstance(intent, TakeoffRequested):
            self._run(lambda: self._service.update_settings(intent.settings))
        elif isinstance(intent, ActionAppendRequested):
            self._run(lambda: self._service.append_action(intent.action))
        elif isinstance(intent, ActionReplaceRequested):
            self._run(lambda: self._service.replace_action(intent.index, intent.action))
        elif isinstance(intent, ActionDeleteRequested):
            self._run(lambda: self._service.delete_action(intent.index))
        elif isinstance(intent, ActionMoveRequested):
            self._run(lambda: self._service.move_action(intent.index, intent.point))
        elif isinstance(intent, ActionSelected):
            self._run(lambda: self._service.select_action(intent.index))
        elif isinstance(intent, UndoRequested):
            self._run(self._service.undo)
        elif isinstance(intent, ClearRequested):
            self._run(self._service.clear_actions)
        elif isinstance(intent, RemoveLandRequested):
            self._run(self._service.remove_land)
        else:
            self._render("The builder returned an unsupported action.")

    def _run(
        self,
        operation: Callable[[], object],
        *,
        reset_transient_editor: bool = False,
    ) -> None:
        try:
            operation()
        except _WORKFLOW_ERRORS as error:
            self._render(str(error))
            return
        if reset_transient_editor:
            self._builder.reset_transient_editor()
        self._render()

    def _render(self, error_message: str | None = None) -> None:
        snapshot = self._service.snapshot
        mission = snapshot.mission
        self._builder.render_snapshot(
            MissionBuilderSnapshot(
                settings=None if mission is None else mission.settings,
                actions=() if mission is None else mission.actions,
                selected_index=snapshot.selected_index,
                error_message=error_message,
            )
        )
        self._edit_settings_button.setEnabled(mission is not None)
        self._save_button.setEnabled(snapshot.can_save)
        self._compile_button.setEnabled(snapshot.can_compile)
        self._preview.setVisible(snapshot.compiled_preview is not None)
        self._compiled_items.clear()
        if snapshot.compiled_preview is not None:
            for item in snapshot.compiled_preview.items:
                self._compiled_items.addItem(_format_compiled_item(item))
        if mission is None:
            self._status.setText("New mission — confirm Takeoff setup")
            self._validation.setText(
                "Structural review starts after Takeoff settings are confirmed."
            )
        elif error_message:
            self._status.setText("Workflow action failed — mission state was preserved")
            self._validation.setText(error_message)
        elif snapshot.compiled_preview is not None:
            count = len(snapshot.compiled_preview.items)
            self._status.setText(f"Compiled preview — {count} native items · offline only")
            self._validation.setText(_SAFETY_DISCLAIMER)
        elif snapshot.can_compile:
            self._status.setText("Complete draft — ready for deterministic preview")
            self._validation.setText(_SAFETY_DISCLAIMER)
        else:
            self._status.setText(
                f"Draft — {len(mission.actions)} confirmed point(s) · add final Land"
            )
            self._validation.setText(
                "Structural findings: "
                + "; ".join(
                    f"{finding.path}: {finding.message}" for finding in snapshot.complete_findings
                )
            )
        path_text = "Unsaved mission" if snapshot.source_path is None else str(snapshot.source_path)
        if snapshot.is_dirty:
            path_text += "  •  unsaved changes"
        self._path.setText(path_text)

    def _open_settings_editor(self) -> None:
        mission = self._service.snapshot.mission
        if mission is None:
            return
        dialog = MissionSettingsDialog(mission.settings, self)
        dialog.settings_confirmed.connect(self.update_settings)
        dialog.exec()

    def _choose_save_path(self) -> None:
        source_path = self._service.snapshot.source_path
        initial = "" if source_path is None else str(source_path)
        if self._save_path_picker is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save mission", initial, "SKYWriter mission (*.json)"
            )
        else:
            selected = self._save_path_picker(initial)
            path = "" if selected is None else str(selected)
        if path:
            self.save_mission(path)

    def _choose_load_path(self) -> None:
        if self._load_path_picker is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Load mission", "", "SKYWriter mission (*.json)"
            )
        else:
            selected = self._load_path_picker()
            path = "" if selected is None else str(selected)
        if path:
            self.load_mission(path)


_SAFETY_DISCLAIMER = (
    "Structural validation passed. This does not establish obstacle clearance or flight safety."
)


def _format_compiled_item(item: CompiledMissionItem) -> str:
    return (
        f"{item.sequence:02d}  {item.command.name} ({int(item.command)})\n"
        f"frame={item.frame.name} ({int(item.frame)})\n"
        f"mission_type={item.mission_type.name} ({int(item.mission_type)})  "
        f"current={str(item.current).lower()}  "
        f"autocontinue={str(item.autocontinue).lower()}\n"
        f"params: p1={item.param1:g}  p2={item.param2:g}  "
        f"p3={item.param3:g}  p4={item.param4:g}\n"
        f"position: lat_e7={item.latitude_e7}  lon_e7={item.longitude_e7}  "
        f"alt_m={item.altitude_m:g}"
    )


def _finite_number(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{label} must be a number.") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite.")
    return parsed


def _positive_number(value: str, label: str) -> float:
    parsed = _finite_number(value, label)
    if parsed <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return parsed
