"""Read-only flight telemetry presentation."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QSplitter, QVBoxLayout, QWidget

from skywriter.application.telemetry import (
    TelemetryFreshness,
    TelemetryMapLayers,
    TelemetryRoute,
    TelemetrySnapshot,
    build_map_layers,
)
from skywriter.ui.telemetry import (
    NativeMessagesList,
    TelemetryCard,
    TelemetryMapLayersWidget,
    render_signal,
)


class FlightTelemetryWidget(QWidget):
    """Display flight observations and route progress with no control surface."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("flightTelemetryView")
        root = QVBoxLayout(self)
        heading = QLabel("Flight telemetry")
        heading.setObjectName("viewHeading")
        heading.setStyleSheet("font-size: 24px; font-weight: 700; color: #173f3d;")
        disclaimer = QLabel(
            "READ-ONLY MONITORING — stale or missing telemetry closes future command gates. "
            "No flight command is available in this workspace."
        )
        disclaimer.setObjectName("flightTelemetryDisclaimer")
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(
            "padding: 9px; background: #e5f0f6; color: #17435a; font-weight: 600;"
        )
        root.addWidget(heading)
        root.addWidget(disclaimer)

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
        self.render_snapshot(None, now_s=0.0)

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
            self._messages.render_messages(())
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
        self._messages.render_messages(
            (message.severity, message.text) for message in snapshot.native_messages
        )
        self._messages.scrollToBottom()


def build_map_layers_placeholder(route: TelemetryRoute) -> TelemetryMapLayers:
    return TelemetryMapLayers(None, None, None, (), route.points)


def _available(value: object | None) -> str:
    return "unavailable" if value is None else str(value)


def _measurement(value: int | float | None, unit: str) -> str:
    return "unavailable" if value is None else f"{value:g} {unit}"
