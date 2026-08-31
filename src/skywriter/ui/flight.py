"""Flight telemetry plus the dedicated Task 102–104 native flight intents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from skywriter.application.auto_start import NativeAutoStartSnapshot, NativeAutoStartState
from skywriter.application.land_here_now import (
    NativeLandHereNowSnapshot,
    NativeLandHereNowState,
)
from skywriter.application.pause_resume import (
    NativePauseResumeSnapshot,
    NativePauseResumeState,
)
from skywriter.application.telemetry import (
    TelemetryFreshness,
    TelemetryMapLayers,
    TelemetryRoute,
    TelemetrySnapshot,
    build_map_layers,
)
from skywriter.ui.auto_start_worker import NativeAutoStartWorkerHandoff
from skywriter.ui.land_here_now_worker import NativeLandHereNowWorkerHandoff
from skywriter.ui.pause_resume_worker import NativePauseResumeWorkerHandoff
from skywriter.ui.telemetry import (
    NativeMessagesList,
    TelemetryCard,
    TelemetryMapLayersWidget,
    render_signal,
)


@dataclass(frozen=True, slots=True)
class NativeAutoStartRequested:
    pass


@dataclass(frozen=True, slots=True)
class NativePauseRequested:
    pass


@dataclass(frozen=True, slots=True)
class NativeResumeRequested:
    pass


@dataclass(frozen=True, slots=True)
class LandHereNowConfirmationRequested:
    pass


@dataclass(frozen=True, slots=True)
class LandHereNowConfirmed:
    pass


@dataclass(frozen=True, slots=True)
class LandHereNowCancelled:
    pass


FlightIntent: TypeAlias = (
    NativeAutoStartRequested
    | NativePauseRequested
    | NativeResumeRequested
    | LandHereNowConfirmationRequested
    | LandHereNowConfirmed
    | LandHereNowCancelled
)


class FlightTelemetryWidget(QWidget):
    """Display telemetry and emit only the approved typed native flight intents."""

    intent_emitted = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._auto_start = NativeAutoStartSnapshot()
        self._auto_start_worker: NativeAutoStartWorkerHandoff | None = None
        self._pause_resume = NativePauseResumeSnapshot()
        self._pause_resume_worker: NativePauseResumeWorkerHandoff | None = None
        self._land_here_now = NativeLandHereNowSnapshot()
        self._land_here_now_worker: NativeLandHereNowWorkerHandoff | None = None
        self._begin_land_confirmation: Callable[[], NativeLandHereNowSnapshot] | None = None
        self._cancel_land_confirmation: Callable[[], NativeLandHereNowSnapshot] | None = None
        self._telemetry_native_messages: tuple[tuple[int, str], ...] = ()
        self.setObjectName("flightTelemetryView")
        root = QVBoxLayout(self)
        heading = QLabel("Flight telemetry")
        heading.setObjectName("viewHeading")
        heading.setStyleSheet("font-size: 24px; font-weight: 700; color: #173f3d;")
        disclaimer = QLabel(
            "TELEMETRY IS READ-ONLY EXCEPT FOR THE DEDICATED CONTROLS BELOW. Start, Pause, "
            "Resume, and Land Here Now are independently gated and acknowledged; SKYWriter "
            "never streams substitute navigation."
        )
        disclaimer.setObjectName("flightTelemetryDisclaimer")
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(
            "padding: 9px; background: #e5f0f6; color: #17435a; font-weight: 600;"
        )
        root.addWidget(heading)
        root.addWidget(disclaimer)

        start_panel = QFrame()
        start_panel.setObjectName("nativeAutoStartPanel")
        start_panel.setFrameShape(QFrame.Shape.StyledPanel)
        start_layout = QVBoxLayout(start_panel)
        start_heading = QLabel("Native AUTO mission start")
        start_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        start_warning = QLabel(
            "START MISSION CHANGES FLIGHT STATE. It is enabled only for the current armed "
            "same-target vehicle and exact verified mission. An accepted acknowledgment is not "
            "shown as Running without later armed AUTO and mission-progress telemetry."
        )
        start_warning.setObjectName("nativeAutoStartWarning")
        start_warning.setWordWrap(True)
        start_warning.setStyleSheet(
            "padding: 9px; background: #ffe2d5; color: #762500; font-weight: 700;"
        )
        self._auto_start_status = QLabel()
        self._auto_start_status.setObjectName("nativeAutoStartStatus")
        self._auto_start_status.setWordWrap(True)
        self._auto_start_detail = QLabel()
        self._auto_start_detail.setObjectName("nativeAutoStartDetail")
        self._auto_start_detail.setWordWrap(True)
        self._auto_start_button = QPushButton("Start verified mission in AUTO")
        self._auto_start_button.setObjectName("nativeAutoStartButton")
        self._auto_start_button.clicked.connect(self._emit_auto_start_request)
        start_layout.addWidget(start_heading)
        start_layout.addWidget(start_warning)
        start_layout.addWidget(self._auto_start_status)
        start_layout.addWidget(self._auto_start_detail)
        start_layout.addWidget(self._auto_start_button)
        root.addWidget(start_panel)

        pause_panel = QFrame()
        pause_panel.setObjectName("nativePauseResumePanel")
        pause_panel.setFrameShape(QFrame.Shape.StyledPanel)
        pause_layout = QVBoxLayout(pause_panel)
        pause_heading = QLabel("Native mission Pause / Resume")
        pause_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        pause_warning = QLabel(
            "PAUSE AND RESUME CHANGE FLIGHT BEHAVIOR. Pause is available only with current "
            "native Active mission telemetry. Resume is available only after SKYWriter has "
            "positively observed the pinned Paused mission state."
        )
        pause_warning.setObjectName("nativePauseResumeWarning")
        pause_warning.setWordWrap(True)
        pause_warning.setStyleSheet(
            "padding: 9px; background: #fff0c7; color: #604400; font-weight: 700;"
        )
        self._pause_resume_status = QLabel()
        self._pause_resume_status.setObjectName("nativePauseResumeStatus")
        self._pause_resume_status.setWordWrap(True)
        self._pause_resume_detail = QLabel()
        self._pause_resume_detail.setObjectName("nativePauseResumeDetail")
        self._pause_resume_detail.setWordWrap(True)
        control_grid = QGridLayout()
        self._pause_button = QPushButton("Pause native mission")
        self._pause_button.setObjectName("nativePauseButton")
        self._pause_button.clicked.connect(self._emit_pause_request)
        self._resume_button = QPushButton("Resume native mission")
        self._resume_button.setObjectName("nativeResumeButton")
        self._resume_button.clicked.connect(self._emit_resume_request)
        control_grid.addWidget(self._pause_button, 0, 0)
        control_grid.addWidget(self._resume_button, 0, 1)
        pause_layout.addWidget(pause_heading)
        pause_layout.addWidget(pause_warning)
        pause_layout.addWidget(self._pause_resume_status)
        pause_layout.addWidget(self._pause_resume_detail)
        pause_layout.addLayout(control_grid)
        root.addWidget(pause_panel)

        land_panel = QFrame()
        land_panel.setObjectName("nativeLandHereNowPanel")
        land_panel.setFrameShape(QFrame.Shape.StyledPanel)
        land_panel.setStyleSheet(
            "QFrame#nativeLandHereNowPanel { border: 2px solid #a52222; background: #fff7f7; }"
        )
        land_layout = QVBoxLayout(land_panel)
        land_heading = QLabel("Land Here Now — current aircraft location")
        land_heading.setObjectName("nativeLandHereNowHeading")
        land_heading.setStyleSheet("font-size: 16px; font-weight: 700; color: #7b1010;")
        land_warning = QLabel(
            "THIS IS NOT THE PLANNED CLICKED LAND POINT. Land Here Now abandons all remaining "
            "mission progress and asks ArduCopter to land at the aircraft's current location."
        )
        land_warning.setObjectName("nativeLandHereNowWarning")
        land_warning.setWordWrap(True)
        land_warning.setStyleSheet(
            "padding: 10px; background: #ffd9d9; color: #710e0e; font-weight: 800;"
        )
        self._land_here_now_status = QLabel()
        self._land_here_now_status.setObjectName("nativeLandHereNowStatus")
        self._land_here_now_status.setWordWrap(True)
        self._land_here_now_detail = QLabel()
        self._land_here_now_detail.setObjectName("nativeLandHereNowDetail")
        self._land_here_now_detail.setWordWrap(True)
        self._land_here_now_button = QPushButton("Land Here Now…")
        self._land_here_now_button.setObjectName("landHereNowButton")
        self._land_here_now_button.clicked.connect(self._request_land_confirmation)
        self._land_confirmation = QWidget()
        self._land_confirmation.setObjectName("landHereNowConfirmation")
        confirmation_layout = QVBoxLayout(self._land_confirmation)
        confirmation_warning = QLabel(
            "CONFIRM: abandon the remaining mission and land at the aircraft's current location."
        )
        confirmation_warning.setObjectName("landHereNowConfirmationWarning")
        confirmation_warning.setWordWrap(True)
        confirmation_warning.setStyleSheet("color: #710e0e; font-weight: 800;")
        confirmation_buttons = QGridLayout()
        self._land_confirm_button = QPushButton("Confirm: abandon mission and land here now")
        self._land_confirm_button.setObjectName("landHereNowConfirmButton")
        self._land_confirm_button.clicked.connect(self._confirm_land_here_now)
        self._land_cancel_button = QPushButton("Cancel")
        self._land_cancel_button.setObjectName("landHereNowCancelButton")
        self._land_cancel_button.clicked.connect(self._cancel_land_here_now)
        confirmation_buttons.addWidget(self._land_confirm_button, 0, 0)
        confirmation_buttons.addWidget(self._land_cancel_button, 0, 1)
        confirmation_layout.addWidget(confirmation_warning)
        confirmation_layout.addLayout(confirmation_buttons)
        land_layout.addWidget(land_heading)
        land_layout.addWidget(land_warning)
        land_layout.addWidget(self._land_here_now_status)
        land_layout.addWidget(self._land_here_now_detail)
        land_layout.addWidget(self._land_here_now_button)
        land_layout.addWidget(self._land_confirmation)
        root.addWidget(land_panel)

        grid = QGridLayout()
        self._connection = TelemetryCard("Vehicle / link", "flightConnection")
        self._state = TelemetryCard("Mode / armed state", "flightVehicleState")
        self._position = TelemetryCard("Altitude / position", "flightPosition")
        self._speed = TelemetryCard("Ground speed", "flightSpeed")
        self._battery = TelemetryCard("Battery", "flightBattery")
        self._mission = TelemetryCard("Mission progress", "flightMissionProgress")
        for index, card in enumerate(
            (
                self._connection,
                self._state,
                self._position,
                self._speed,
                self._battery,
                self._mission,
            )
        ):
            grid.addWidget(card, index // 3, index % 3)
        root.addLayout(grid)

        splitter = QSplitter()
        self._map = TelemetryMapLayersWidget()
        splitter.addWidget(self._map)
        messages_panel = QWidget()
        messages_layout = QVBoxLayout(messages_panel)
        messages_heading = QLabel("Native messages")
        messages_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        self._messages = NativeMessagesList()
        messages_layout.addWidget(messages_heading)
        messages_layout.addWidget(self._messages)
        splitter.addWidget(messages_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)
        self.render_auto_start(self._auto_start)
        self.render_pause_resume(self._pause_resume)
        self.render_land_here_now(self._land_here_now)
        self.render_snapshot(None, now_s=0.0)

    def _emit_auto_start_request(self) -> None:
        self._auto_start_button.setEnabled(False)
        self.intent_emitted.emit(NativeAutoStartRequested())
        if self._auto_start_worker is not None:
            self._auto_start_worker.submit()

    def bind_native_auto_start_operation(
        self,
        operation: Callable[[], NativeAutoStartSnapshot],
    ) -> NativeAutoStartWorkerHandoff:
        """Bind the blocking application operation to the background worker."""

        if self._auto_start_worker is not None:
            raise RuntimeError("a native AUTO-start operation is already bound")
        worker = NativeAutoStartWorkerHandoff(operation)
        worker.snapshot_ready.connect(self._render_worker_auto_start_snapshot)
        worker.operation_failed.connect(self._render_worker_failure)
        self._auto_start_worker = worker
        return worker

    def _emit_pause_request(self) -> None:
        self._pause_button.setEnabled(False)
        self._resume_button.setEnabled(False)
        self.intent_emitted.emit(NativePauseRequested())
        if self._pause_resume_worker is not None:
            self._pause_resume_worker.submit_pause()

    def _emit_resume_request(self) -> None:
        self._pause_button.setEnabled(False)
        self._resume_button.setEnabled(False)
        self.intent_emitted.emit(NativeResumeRequested())
        if self._pause_resume_worker is not None:
            self._pause_resume_worker.submit_resume()

    def bind_native_pause_resume_operations(
        self,
        pause_operation: Callable[[], NativePauseResumeSnapshot],
        resume_operation: Callable[[], NativePauseResumeSnapshot],
    ) -> NativePauseResumeWorkerHandoff:
        """Bind both blocking application operations to one exclusive worker."""

        if self._pause_resume_worker is not None:
            raise RuntimeError("native Pause/Resume operations are already bound")
        worker = NativePauseResumeWorkerHandoff(pause_operation, resume_operation)
        worker.snapshot_ready.connect(self._render_worker_pause_resume_snapshot)
        worker.operation_failed.connect(self._render_pause_resume_worker_failure)
        self._pause_resume_worker = worker
        return worker

    def _request_land_confirmation(self) -> None:
        self._set_land_controls_enabled(False)
        self.intent_emitted.emit(LandHereNowConfirmationRequested())
        if self._begin_land_confirmation is not None:
            self.render_land_here_now(self._begin_land_confirmation())

    def _confirm_land_here_now(self) -> None:
        self._set_land_controls_enabled(False)
        self.intent_emitted.emit(LandHereNowConfirmed())
        if self._land_here_now_worker is not None:
            self._land_here_now_worker.submit()

    def _cancel_land_here_now(self) -> None:
        self._set_land_controls_enabled(False)
        self.intent_emitted.emit(LandHereNowCancelled())
        if self._cancel_land_confirmation is not None:
            self.render_land_here_now(self._cancel_land_confirmation())

    def bind_land_here_now_operations(
        self,
        begin_confirmation: Callable[[], NativeLandHereNowSnapshot],
        cancel_confirmation: Callable[[], NativeLandHereNowSnapshot],
        confirm_operation: Callable[[], NativeLandHereNowSnapshot],
    ) -> NativeLandHereNowWorkerHandoff:
        """Bind synchronous confirmation state and one blocking confirmed request."""

        if self._land_here_now_worker is not None:
            raise RuntimeError("Land Here Now operations are already bound")
        self._begin_land_confirmation = begin_confirmation
        self._cancel_land_confirmation = cancel_confirmation
        worker = NativeLandHereNowWorkerHandoff(confirm_operation)
        worker.snapshot_ready.connect(self._render_worker_land_here_now_snapshot)
        worker.operation_failed.connect(self._render_land_here_now_worker_failure)
        self._land_here_now_worker = worker
        return worker

    def _render_worker_auto_start_snapshot(self, snapshot: object) -> None:
        if isinstance(snapshot, NativeAutoStartSnapshot):
            self.render_auto_start(snapshot)

    def _render_worker_failure(self, detail: str) -> None:
        self._auto_start_status.setText("Worker failed — onboard state uncertain")
        self._auto_start_detail.setText(detail)
        self._auto_start_button.setEnabled(False)

    def _render_worker_pause_resume_snapshot(self, snapshot: object) -> None:
        if isinstance(snapshot, NativePauseResumeSnapshot):
            self.render_pause_resume(snapshot)

    def _render_pause_resume_worker_failure(self, detail: str) -> None:
        self._pause_resume_status.setText("Worker failed — onboard state uncertain")
        self._pause_resume_detail.setText(detail)
        self._pause_button.setEnabled(False)
        self._resume_button.setEnabled(False)

    def _render_worker_land_here_now_snapshot(self, snapshot: object) -> None:
        if isinstance(snapshot, NativeLandHereNowSnapshot):
            self.render_land_here_now(snapshot)

    def _render_land_here_now_worker_failure(self, detail: str) -> None:
        self._land_here_now_status.setText("Worker failed — onboard state uncertain")
        self._land_here_now_detail.setText(detail)
        self._set_land_controls_enabled(False)

    @property
    def auto_start_snapshot(self) -> NativeAutoStartSnapshot:
        return self._auto_start

    def render_auto_start(self, snapshot: NativeAutoStartSnapshot) -> None:
        self._auto_start = snapshot
        self._auto_start_status.setText(_auto_start_state_text(snapshot.state))
        repeated = (
            " Repeated activation was ignored while the original request remained active."
            if snapshot.repeated_request_ignored
            else ""
        )
        self._auto_start_detail.setText(f"{snapshot.detail}{repeated}")
        self._auto_start_button.setEnabled(
            snapshot.request_available
            and snapshot.state is not NativeAutoStartState.PENDING
            and snapshot.state is not NativeAutoStartState.RUNNING
        )
        self._render_native_messages()

    @property
    def pause_resume_snapshot(self) -> NativePauseResumeSnapshot:
        return self._pause_resume

    def render_pause_resume(self, snapshot: NativePauseResumeSnapshot) -> None:
        self._pause_resume = snapshot
        self._pause_resume_status.setText(_pause_resume_state_text(snapshot.state))
        repeated = (
            " Repeated activation was ignored while the original request remained active."
            if snapshot.repeated_request_ignored
            else ""
        )
        self._pause_resume_detail.setText(f"{snapshot.detail}{repeated}")
        pending = snapshot.state in (
            NativePauseResumeState.PAUSE_PENDING,
            NativePauseResumeState.RESUME_PENDING,
        )
        self._pause_button.setEnabled(snapshot.pause_available and not pending)
        self._resume_button.setEnabled(snapshot.resume_available and not pending)
        self._render_native_messages()

    @property
    def land_here_now_snapshot(self) -> NativeLandHereNowSnapshot:
        return self._land_here_now

    def render_land_here_now(self, snapshot: NativeLandHereNowSnapshot) -> None:
        self._land_here_now = snapshot
        self._land_here_now_status.setText(_land_here_now_state_text(snapshot.state))
        repeated = (
            " Repeated activation was ignored while the original request remained active."
            if snapshot.repeated_request_ignored
            else ""
        )
        self._land_here_now_detail.setText(f"{snapshot.detail}{repeated}")
        self._land_confirmation.setVisible(
            snapshot.state is NativeLandHereNowState.CONFIRMATION_REQUIRED
        )
        self._land_here_now_button.setEnabled(snapshot.request_available)
        self._land_confirm_button.setEnabled(snapshot.confirm_available)
        self._land_cancel_button.setEnabled(snapshot.cancel_available)
        self._render_native_messages()

    def _set_land_controls_enabled(self, enabled: bool) -> None:
        self._land_here_now_button.setEnabled(enabled)
        self._land_confirm_button.setEnabled(enabled)
        self._land_cancel_button.setEnabled(enabled)

    @property
    def map_layers_widget(self) -> TelemetryMapLayersWidget:
        return self._map

    def render_snapshot(
        self,
        snapshot: TelemetrySnapshot | None,
        *,
        now_s: float,
        route: TelemetryRoute | None = None,
    ) -> None:
        route = route or TelemetryRoute()
        if snapshot is None:
            for card in (
                self._connection,
                self._state,
                self._position,
                self._speed,
                self._battery,
                self._mission,
            ):
                card.set_value("Unavailable", TelemetryFreshness.UNAVAILABLE)
            self._map.set_layers(build_map_layers_placeholder(route))
            self._telemetry_native_messages = ()
            self._render_native_messages()
            return
        heartbeat_freshness = snapshot.heartbeat.freshness(now_s)
        self._connection.set_value(
            f"{snapshot.vehicle_identity} · {snapshot.link_kind.value.upper()} · "
            f"{snapshot.connection_state(now_s).value}",
            heartbeat_freshness,
        )
        render_signal(
            self._state,
            snapshot.heartbeat,
            now_s=now_s,
            formatter=lambda value: f"{value.mode_name} · {'Armed' if value.armed else 'Disarmed'}",
        )
        render_signal(
            self._position,
            snapshot.position,
            now_s=now_s,
            formatter=lambda value: (
                f"{value.relative_altitude_m:.1f} m Above Home · "
                f"{value.altitude_msl_m:.1f} m MSL · "
                f"{value.point.latitude_deg:.6f}, {value.point.longitude_deg:.6f}"
            ),
        )
        render_signal(
            self._speed,
            snapshot.position,
            now_s=now_s,
            formatter=lambda value: f"{value.ground_speed_m_s:.2f} m/s",
        )
        render_signal(
            self._battery,
            snapshot.battery,
            now_s=now_s,
            formatter=lambda value: (
                f"{_measurement(value.voltage_v, 'V')} · "
                f"{_measurement(value.current_a, 'A')} · "
                f"{_measurement(value.remaining_percent, '%')}"
            ),
        )
        render_signal(
            self._mission,
            snapshot.mission,
            now_s=now_s,
            formatter=lambda value: (
                f"current {_available(value.current_sequence)} · "
                f"reached {_available(value.last_reached_sequence)} · "
                f"total {_available(value.total_items)}"
            ),
        )
        self._map.set_layers(build_map_layers(snapshot, route, now_s=now_s))
        self._telemetry_native_messages = tuple(
            (message.severity, message.text) for message in snapshot.native_messages
        )
        self._render_native_messages()
        self._messages.scrollToBottom()

    def _render_native_messages(self) -> None:
        auto_start_messages = tuple(
            (message.severity, message.text) for message in self._auto_start.native_messages
        )
        pause_resume_messages = tuple(
            (message.severity, message.text) for message in self._pause_resume.native_messages
        )
        land_here_now_messages = tuple(
            (message.severity, message.text) for message in self._land_here_now.native_messages
        )
        self._messages.render_messages(
            (
                *self._telemetry_native_messages,
                *auto_start_messages,
                *pause_resume_messages,
                *land_here_now_messages,
            )
        )
        self._messages.scrollToBottom()


def build_map_layers_placeholder(route: TelemetryRoute) -> TelemetryMapLayers:
    return TelemetryMapLayers(None, None, None, (), route.points)


def _available(value: object | None) -> str:
    return "unavailable" if value is None else str(value)


def _measurement(value: int | float | None, unit: str) -> str:
    return "unavailable" if value is None else f"{value:g} {unit}"


def _auto_start_state_text(state: NativeAutoStartState) -> str:
    return {
        NativeAutoStartState.IDLE: "Ready for one native mission-start request",
        NativeAutoStartState.PENDING: "Start Mission pending — controls locked",
        NativeAutoStartState.RUNNING: "Running — ACK, armed AUTO, and progress confirmed",
        NativeAutoStartState.REJECTED: "Mission start rejected by ArduCopter",
        NativeAutoStartState.UNSUPPORTED: "Native mission start unsupported",
        NativeAutoStartState.TIMED_OUT: "Acknowledgment timed out — state uncertain",
        NativeAutoStartState.CANCELLED: "Request cancelled — state uncertain",
        NativeAutoStartState.WRONG_TARGET: "Wrong-target acknowledgment — blocked",
        NativeAutoStartState.WRONG_ACK: "Unrelated or misaddressed acknowledgment — blocked",
        NativeAutoStartState.STALE_LINK: "SiK telemetry stale — state uncertain",
        NativeAutoStartState.LINK_LOST: "SiK link lost — onboard behavior remains native",
        NativeAutoStartState.ACKNOWLEDGED_NO_AUTO_TELEMETRY: (
            "Acknowledged, but AUTO telemetry is absent — state uncertain"
        ),
        NativeAutoStartState.ACKNOWLEDGED_NO_MISSION_PROGRESS: (
            "AUTO observed, but mission progress is absent — state uncertain"
        ),
        NativeAutoStartState.UNEXPECTED_MODE: "Vehicle is not in expected AUTO mode",
        NativeAutoStartState.MISSION_MISMATCH: "Mission evidence or progress mismatched",
        NativeAutoStartState.DISARMED: "Vehicle disarmed — Running is not claimed",
        NativeAutoStartState.TELEMETRY_DISAGREEMENT: "Armed telemetry disagrees — blocked",
        NativeAutoStartState.BLOCKED_WRONG_LINK: "SiK link required",
        NativeAutoStartState.BLOCKED_DISARMED: "Telemetry-confirmed Armed state required",
        NativeAutoStartState.BLOCKED_MISSION: "Exact current mission verification required",
        NativeAutoStartState.BLOCKED_ARM: "Current Task 101 Armed evidence required",
        NativeAutoStartState.BLOCKED_IDENTITY: "Same-target identity unresolved",
        NativeAutoStartState.BLOCKED_BUSY: "Command channel busy",
        NativeAutoStartState.BLOCKED_SEQUENCE: "Verified native start sequence is invalid",
        NativeAutoStartState.BLOCKED_ALREADY_AUTO: "Vehicle is already in AUTO",
    }[state]


def _pause_resume_state_text(state: NativePauseResumeState) -> str:
    return {
        NativePauseResumeState.IDLE: "Pause blocked — current native mission state required",
        NativePauseResumeState.RUNNING: "Running confirmed — Pause is available",
        NativePauseResumeState.PAUSE_PENDING: "Pause pending — controls locked",
        NativePauseResumeState.PAUSED: "Paused confirmed — Resume is available",
        NativePauseResumeState.RESUME_PENDING: "Resume pending — controls locked",
        NativePauseResumeState.REJECTED: "Pause/Resume rejected by ArduCopter",
        NativePauseResumeState.UNSUPPORTED: "Native Pause/Resume unsupported",
        NativePauseResumeState.TIMED_OUT: "Acknowledgment timed out — state uncertain",
        NativePauseResumeState.CANCELLED: "Request cancelled — state uncertain",
        NativePauseResumeState.WRONG_TARGET: "Wrong-target acknowledgment — blocked",
        NativePauseResumeState.WRONG_ACK: "Unrelated or misaddressed acknowledgment — blocked",
        NativePauseResumeState.STALE_LINK: "SiK telemetry stale — state uncertain",
        NativePauseResumeState.LINK_LOST: "SiK link lost — onboard behavior remains native",
        NativePauseResumeState.ACKNOWLEDGED_NO_PAUSED_TELEMETRY: (
            "Pause acknowledged, but Paused telemetry is absent — state uncertain"
        ),
        NativePauseResumeState.ACKNOWLEDGED_NO_RUNNING_TELEMETRY: (
            "Resume acknowledged, but Active telemetry is absent — state uncertain"
        ),
        NativePauseResumeState.UNEXPECTED_MODE: "Vehicle left expected AUTO mode",
        NativePauseResumeState.MISSION_COMPLETED: "Mission Complete — controls disabled",
        NativePauseResumeState.LANDING: "Vehicle Landing — controls disabled",
        NativePauseResumeState.DISARMED: "Vehicle Disarmed / On Ground — controls disabled",
        NativePauseResumeState.MISSION_MISMATCH: "Mission evidence or progress mismatched",
        NativePauseResumeState.TELEMETRY_DISAGREEMENT: (
            "Acknowledgment and mission-state telemetry disagree"
        ),
        NativePauseResumeState.BLOCKED_WRONG_LINK: "SiK link required",
        NativePauseResumeState.BLOCKED_MISSION: "Exact current mission verification required",
        NativePauseResumeState.BLOCKED_AUTO_START: "Task 102 Running evidence required",
        NativePauseResumeState.BLOCKED_IDENTITY: "Same-target identity unresolved",
        NativePauseResumeState.BLOCKED_BUSY: "Command channel busy",
        NativePauseResumeState.BLOCKED_NOT_RUNNING: "Current Active mission state required",
        NativePauseResumeState.BLOCKED_NOT_PAUSED: "Positively observed Paused state required",
    }[state]


def _land_here_now_state_text(state: NativeLandHereNowState) -> str:
    return {
        NativeLandHereNowState.IDLE: "Land Here Now blocked — airborne mission evidence required",
        NativeLandHereNowState.AVAILABLE: "Available — deliberate confirmation required",
        NativeLandHereNowState.CONFIRMATION_REQUIRED: "Confirmation required — no command sent",
        NativeLandHereNowState.PENDING: "Native Land pending — controls locked",
        NativeLandHereNowState.LANDING: "Landing confirmed by ACK and native telemetry",
        NativeLandHereNowState.LANDED: "Landed confirmed by native On Ground telemetry",
        NativeLandHereNowState.CONFIRMATION_CANCELLED: "Confirmation cancelled — no command sent",
        NativeLandHereNowState.REJECTED: "Native Land rejected by ArduCopter",
        NativeLandHereNowState.UNSUPPORTED: "Native Land unsupported",
        NativeLandHereNowState.TIMED_OUT: "Acknowledgment timed out — state uncertain",
        NativeLandHereNowState.CANCELLED: "Request cancelled — state uncertain",
        NativeLandHereNowState.WRONG_TARGET: "Wrong-target acknowledgment — blocked",
        NativeLandHereNowState.WRONG_ACK: "Unrelated or misaddressed acknowledgment — blocked",
        NativeLandHereNowState.STALE_LINK: "SiK telemetry stale — state uncertain",
        NativeLandHereNowState.LINK_LOST: "SiK link lost — onboard behavior remains native",
        NativeLandHereNowState.ACKNOWLEDGED_NO_LANDING_TELEMETRY: (
            "Land acknowledged, but landing telemetry is absent — state uncertain"
        ),
        NativeLandHereNowState.UNEXPECTED_MODE: "Vehicle is not in expected AUTO/Land mode",
        NativeLandHereNowState.MISSION_COMPLETED: "Mission Complete — action disabled",
        NativeLandHereNowState.DISARMED: "Vehicle Disarmed — action disabled",
        NativeLandHereNowState.MISSION_MISMATCH: "Mission evidence or progress mismatched",
        NativeLandHereNowState.TELEMETRY_DISAGREEMENT: (
            "Acknowledgment and landing telemetry disagree"
        ),
        NativeLandHereNowState.ALREADY_LANDING: "Vehicle already Landing — action disabled",
        NativeLandHereNowState.ALREADY_LANDED: "Vehicle already Landed — action disabled",
        NativeLandHereNowState.BLOCKED_WRONG_LINK: "SiK link required",
        NativeLandHereNowState.BLOCKED_MISSION: "Exact current mission verification required",
        NativeLandHereNowState.BLOCKED_AUTO_START: "Task 102 Running evidence required",
        NativeLandHereNowState.BLOCKED_IDENTITY: "Same-target identity unresolved",
        NativeLandHereNowState.BLOCKED_BUSY: "Command channel busy",
        NativeLandHereNowState.BLOCKED_NOT_AIRBORNE: "Fresh In Air telemetry required",
        NativeLandHereNowState.BLOCKED_CONFIRMATION: "Fresh deliberate confirmation required",
    }[state]
