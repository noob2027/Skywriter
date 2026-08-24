"""Read-only preflight telemetry presentation."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from skywriter.application.telemetry import TelemetryFreshness, TelemetrySnapshot
from skywriter.ui.telemetry import NativeMessagesList, TelemetryCard, render_signal


class PreflightTelemetryWidget(QWidget):
    """Display native observations without asserting readiness or offering controls."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("preflightTelemetryView")
        root = QVBoxLayout(self)
        heading = QLabel("Preflight telemetry")
        heading.setObjectName("viewHeading")
        heading.setStyleSheet("font-size: 24px; font-weight: 700; color: #173f3d;")
        disclaimer = QLabel(
            "READ-ONLY NATIVE STATUS — unavailable or stale data is not approval. "
            "ArduCopter remains the authority for pre-arm checks and flight readiness."
        )
        disclaimer.setObjectName("preflightTelemetryDisclaimer")
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(
            "padding: 9px; background: #fff2cc; color: #6a4b00; font-weight: 600;"
        )
        root.addWidget(heading)
        root.addWidget(disclaimer)

        identity_row = QHBoxLayout()
        self._identity = TelemetryCard("Vehicle identity", "telemetryIdentity")
        self._connection = TelemetryCard("Connection freshness", "telemetryConnection")
        self._vehicle = TelemetryCard("Native vehicle state", "telemetryVehicleState")
        identity_row.addWidget(self._identity)
        identity_row.addWidget(self._connection)
        identity_row.addWidget(self._vehicle)
        root.addLayout(identity_row)

        grid = QGridLayout()
        self._gps = TelemetryCard("GPS observation", "telemetryGps")
        self._ekf = TelemetryCard("EKF report", "telemetryEkf")
        self._sensors = TelemetryCard("Native sensor flags", "telemetrySensors")
        self._home = TelemetryCard("Home observation", "telemetryHome")
        self._extended = TelemetryCard("Extended state", "telemetryExtendedState")
        self._battery = TelemetryCard("Battery observation", "telemetryBattery")
        for index, card in enumerate(
            (self._gps, self._ekf, self._sensors, self._home, self._extended, self._battery)
        ):
            grid.addWidget(card, index // 3, index % 3)
        root.addLayout(grid)

        messages_label = QLabel("Native status messages")
        messages_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        root.addWidget(messages_label)
        self._messages = NativeMessagesList()
        root.addWidget(self._messages, 1)
        self.render_snapshot(None, now_s=0.0)

    def render_snapshot(self, snapshot: TelemetrySnapshot | None, *, now_s: float) -> None:
        if snapshot is None:
            for card in (
                self._identity,
                self._connection,
                self._vehicle,
                self._gps,
                self._ekf,
                self._sensors,
                self._home,
                self._extended,
                self._battery,
            ):
                card.set_value("Unavailable", TelemetryFreshness.UNAVAILABLE)
            self._messages.render_messages(())
            return

        heartbeat_freshness = snapshot.heartbeat.freshness(now_s)
        self._identity.set_value(
            f"{snapshot.vehicle_identity}\nTarget {snapshot.target_system}:"
            f"{snapshot.target_component} · {snapshot.link_kind.value.upper()}",
            heartbeat_freshness,
        )
        state = snapshot.connection_state(now_s)
        self._connection.set_value(state.value.title(), heartbeat_freshness)
        render_signal(
            self._vehicle,
            snapshot.heartbeat,
            now_s=now_s,
            formatter=lambda value: (
                f"{value.mode_name} · {'Armed' if value.armed else 'Disarmed'} · "
                f"system state {value.system_status}"
            ),
        )
        render_signal(
            self._gps,
            snapshot.gps,
            now_s=now_s,
            formatter=lambda value: (
                f"fix {value.fix_type} · satellites "
                f"{_available(value.satellites_visible)} · HDOP {_available(value.hdop)}"
            ),
        )
        render_signal(
            self._ekf,
            snapshot.ekf,
            now_s=now_s,
            formatter=lambda value: (
                f"reported flags 0x{value.flags:x} · horizontal variance "
                f"{value.horizontal_position_variance:g}"
            ),
        )
        render_signal(
            self._sensors,
            snapshot.sensors,
            now_s=now_s,
            formatter=lambda value: (
                f"present 0x{value.present_flags:x} · enabled 0x{value.enabled_flags:x} · "
                f"health 0x{value.health_flags:x}"
            ),
        )
        render_signal(
            self._home,
            snapshot.home,
            now_s=now_s,
            formatter=lambda value: (
                f"{value.point.latitude_deg:.7f}, {value.point.longitude_deg:.7f} · "
                f"{value.altitude_msl_m:.1f} m MSL"
            ),
        )
        render_signal(
            self._extended,
            snapshot.extended_state,
            now_s=now_s,
            formatter=lambda value: (
                f"landed state {value.landed_state} · VTOL state {value.vtol_state}"
            ),
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
        self._messages.render_messages(
            (message.severity, message.text) for message in snapshot.native_messages
        )
        self._messages.scrollToBottom()


def _available(value: object | None) -> str:
    return "unavailable" if value is None else str(value)


def _measurement(value: int | float | None, unit: str) -> str:
    return "unavailable" if value is None else f"{value:g} {unit}"
