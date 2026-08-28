"""Flight telemetry plus the dedicated Task 102 native mission-start intent."""

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
from skywriter.application.telemetry import (
    TelemetryFreshness,
    TelemetryMapLayers,
    TelemetryRoute,
    TelemetrySnapshot,
    build_map_layers,
)
from skywriter.ui.auto_start_worker import NativeAutoStartWorkerHandoff
from skywriter.ui.telemetry import (
    NativeMessagesList,
    TelemetryCard,
    TelemetryMapLayersWidget,
    render_signal,
)


@dataclass(frozen=True, slots=True)
class NativeAutoStartRequested:
    pass


FlightIntent: TypeAlias = NativeAutoStartRequested


class FlightTelemetryWidget(QWidget):
    """Display telemetry and emit only the typed native mission-start intent."""

    intent_emitted = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._auto_start = NativeAutoStartSnapshot()
        self._auto_start_worker: NativeAutoStartWorkerHandoff | None = None
        self._telemetry_native_messages: tuple[tuple[int, str], ...] = ()
        self.setObjectName("flightTelemetryView")
        root = QVBoxLayout(self)
        heading = QLabel("Flight telemetry")
        heading.setObjectName("viewHeading")
        heading.setStyleSheet("font-size: 24px; font-weight: 700; color: #173f3d;")
        disclaimer = QLabel(
            "TELEMETRY IS READ-ONLY. The only vehicle action here is the gated, acknowledged "
            "native AUTO mission start below; SKYWriter never streams substitute navigation."
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

    def _render_worker_auto_start_snapshot(self, snapshot: object) -> None:
        if isinstance(snapshot, NativeAutoStartSnapshot):
            self.render_auto_start(snapshot)

    def _render_worker_failure(self, detail: str) -> None:
        self._auto_start_status.setText("Worker failed — onboard state uncertain")
        self._auto_start_detail.setText(detail)
        self._auto_start_button.setEnabled(False)

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
        command_messages = tuple(
            (message.severity, message.text) for message in self._auto_start.native_messages
        )
        self._messages.render_messages((*self._telemetry_native_messages, *command_messages))
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
