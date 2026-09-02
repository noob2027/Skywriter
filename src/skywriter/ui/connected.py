"""Intent-only connected mission panel; worker-owned I/O stays outside Qt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from skywriter.application.connected import (
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
)
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import NativeMissionItem
from skywriter.infrastructure.serial_ports import SerialPortInfo

USB_DEFAULT_BAUDRATE = 115_200
SIK_DEFAULT_BAUDRATE = 57_600


@dataclass(frozen=True, slots=True)
class RefreshPortsRequested:
    pass


@dataclass(frozen=True, slots=True)
class DiscoverUsbRequested:
    endpoint: str
    baudrate: int


@dataclass(frozen=True, slots=True)
class TargetSelectionRequested:
    system_id: int
    component_id: int


@dataclass(frozen=True, slots=True)
class InspectMissionRequested:
    pass


@dataclass(frozen=True, slots=True)
class ReplacementConfirmationRequested:
    confirmed: bool


@dataclass(frozen=True, slots=True)
class UploadVerificationRequested:
    pass


@dataclass(frozen=True, slots=True)
class DisconnectRequested:
    pass


@dataclass(frozen=True, slots=True)
class DiscoverSikRequested:
    endpoint: str
    baudrate: int


@dataclass(frozen=True, slots=True)
class TelemetryRefreshRequested:
    pass


@dataclass(frozen=True, slots=True)
class ReverifyMissionRequested:
    pass


ConnectedIntent: TypeAlias = (
    RefreshPortsRequested
    | DiscoverUsbRequested
    | TargetSelectionRequested
    | InspectMissionRequested
    | ReplacementConfirmationRequested
    | UploadVerificationRequested
    | DisconnectRequested
    | DiscoverSikRequested
    | TelemetryRefreshRequested
    | ReverifyMissionRequested
)


class ConnectedMissionWidget(QWidget):
    """Render fail-closed connected state and emit typed, non-blocking intents."""

    intent_emitted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("connectedMissionWidget")
        self._snapshot = ConnectedMissionSnapshot()
        self._interaction_unavailable_reason: str | None = None
        self._serial_ports: tuple[SerialPortInfo, ...] = ()
        self._busy = False
        self._busy_detail = ""
        self._build_ui()
        self.render_snapshot(self._snapshot)

    @property
    def snapshot(self) -> ConnectedMissionSnapshot:
        return self._snapshot

    def set_interaction_unavailable(self, reason: str) -> None:
        """Render an honest gate when no production vehicle controller is composed."""

        if not reason.strip():
            raise ValueError("interaction-unavailable reason must not be empty")
        self._interaction_unavailable_reason = reason
        self.render_snapshot(self._snapshot)

    def set_serial_ports(self, ports: tuple[SerialPortInfo, ...]) -> None:
        """Render one explicit enumeration result without selecting a device."""

        if not all(isinstance(port, SerialPortInfo) for port in ports):
            raise TypeError("ports must contain SerialPortInfo values")
        self._serial_ports = tuple(ports)
        self._serial_port.blockSignals(True)
        self._serial_port.clear()
        if ports:
            self._serial_port.addItem("Select one serial port", None)
            for port in ports:
                self._serial_port.addItem(port.display_name, port)
            self._serial_status.setText(
                f"Found {len(ports)} serial port{'s' if len(ports) != 1 else ''}. "
                "Select one; SKYWriter will not choose or open it automatically."
            )
        else:
            self._serial_port.addItem("No serial ports found — refresh after connecting one", None)
            self._serial_status.setText(
                "No serial ports are currently visible. Check the cable or radio, then refresh."
            )
        self._serial_port.setCurrentIndex(0)
        self._serial_port.blockSignals(False)
        self.render_snapshot(self._snapshot)

    def set_busy(self, busy: bool, detail: str = "") -> None:
        """Render the single-owner worker state; Disconnect becomes Cancel while active."""

        self._busy = busy
        self._busy_detail = detail.strip()
        self.render_snapshot(self._snapshot)

    def render_snapshot(self, snapshot: ConnectedMissionSnapshot) -> None:
        self._snapshot = snapshot
        self._target.clear()
        self._target.addItem("Select one discovered vehicle", None)
        for candidate in snapshot.candidates:
            self._target.addItem(_target_label(candidate), candidate)
        if snapshot.selected_target is not None:
            for index in range(1, self._target.count()):
                candidate = self._target.itemData(index)
                if candidate == snapshot.selected_target:
                    self._target.setCurrentIndex(index)
                    break

        self._connection.setText(
            "Disconnected"
            if not snapshot.link_connected
            else f"{snapshot.link_kind.value.upper()} connected"
            if snapshot.link_kind is not None
            else "Connected link is unclassified"
        )
        self._verification.setText(_verification_text(snapshot.verification_state))
        self._telemetry.setText(_telemetry_text(snapshot))
        self._replacement.blockSignals(True)
        self._replacement.setChecked(snapshot.replacement_confirmed)
        self._replacement.blockSignals(False)
        self._mission_items.clear()
        if snapshot.onboard is None:
            self._mission_items.addItem("Onboard mission not inspected")
        elif not snapshot.onboard.items:
            self._mission_items.addItem("Vehicle reports an empty mission")
        else:
            for item in snapshot.onboard.items:
                self._mission_items.addItem(_onboard_item_text(item))
        self._failure.setText(
            ""
            if snapshot.failure is None
            else f"{snapshot.failure.code.value}: {snapshot.failure.detail}"
        )
        self._failure.setVisible(snapshot.failure is not None)

        selected_port = self._serial_port.currentData()
        has_selected_port = isinstance(selected_port, SerialPortInfo)
        if self._busy:
            self._operation_status.setText(self._busy_detail or "Connected operation in progress…")
        elif snapshot.link_connected:
            self._operation_status.setText(
                "One serial link is open. Disconnect it before choosing another port or link kind."
            )
        else:
            self._operation_status.setText(
                "No serial link is open. Refreshing ports never opens a vehicle connection."
            )

        has_target = snapshot.selected_target is not None
        is_usb = snapshot.link_kind is not None and snapshot.link_kind.value == "usb"
        is_sik = snapshot.link_kind is not None and snapshot.link_kind.value == "sik"
        idle = not self._busy
        disconnected = not snapshot.link_connected
        self._refresh_ports.setEnabled(idle and disconnected)
        self._serial_port.setEnabled(idle and disconnected and bool(self._serial_ports))
        self._link_kind.setEnabled(idle and disconnected)
        self._baudrate.setEnabled(idle and disconnected)
        self._discover.setEnabled(idle and disconnected and has_selected_port)
        self._target.setEnabled(idle and snapshot.link_connected and bool(snapshot.candidates))
        self._inspect.setEnabled(idle and has_target and is_usb)
        self._replacement.setEnabled(idle and snapshot.onboard is not None and is_usb)
        self._upload.setEnabled(idle and snapshot.replacement_confirmed and is_usb)
        self._refresh.setEnabled(idle and has_target)
        self._reverify.setEnabled(
            idle
            and is_sik
            and snapshot.verification_state is ConnectedVerificationState.REVERIFY_REQUIRED
        )
        self._disconnect.setText("Cancel and close link" if self._busy else "Disconnect")
        self._disconnect.setEnabled(snapshot.link_connected or self._busy)
        gated = self._interaction_unavailable_reason is not None
        self._interaction_gate.setText(self._interaction_unavailable_reason or "")
        self._interaction_gate.setVisible(gated)
        if gated:
            for control in (
                self._refresh_ports,
                self._serial_port,
                self._link_kind,
                self._baudrate,
                self._discover,
                self._target,
                self._inspect,
                self._replacement,
                self._upload,
                self._refresh,
                self._reverify,
                self._disconnect,
            ):
                control.setEnabled(False)
                control.setToolTip(self._interaction_unavailable_reason or "")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        heading = QLabel("Connected mission verification")
        heading.setStyleSheet("font-size: 22px; font-weight: 700; color: #163f3d;")
        root.addWidget(heading)
        safety = QLabel(
            "MISSION TRANSFER ONLY — USB upload requires a disarmed selected vehicle, "
            "fresh authoritative Home, explicit replacement approval, and exact full readback. "
            "This screen has no Arm, mode, flight-command, parameter, Land, or emergency-return "
            "controls."
        )
        safety.setObjectName("connectedSafetyBoundary")
        safety.setWordWrap(True)
        safety.setStyleSheet("padding: 10px; background: #fff3cd; color: #664d03;")
        root.addWidget(safety)
        self._interaction_gate = QLabel()
        self._interaction_gate.setObjectName("connectedInteractionGate")
        self._interaction_gate.setAccessibleName("Connected controls unavailable explanation")
        self._interaction_gate.setWordWrap(True)
        self._interaction_gate.setStyleSheet(
            "padding: 10px; background: #e5f0f6; color: #17435a; font-weight: 700;"
        )
        self._interaction_gate.setVisible(False)
        root.addWidget(self._interaction_gate)

        status = QFrame()
        status.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(status)
        grid.addWidget(QLabel("Connection"), 0, 0)
        self._connection = QLabel()
        self._connection.setObjectName("connectedLinkStatus")
        grid.addWidget(self._connection, 0, 1)
        grid.addWidget(QLabel("Verification"), 1, 0)
        self._verification = QLabel()
        self._verification.setObjectName("connectedVerificationStatus")
        grid.addWidget(self._verification, 1, 1)
        grid.addWidget(QLabel("Latest telemetry refresh"), 2, 0)
        self._telemetry = QLabel()
        self._telemetry.setObjectName("connectedTelemetryStatus")
        self._telemetry.setWordWrap(True)
        grid.addWidget(self._telemetry, 2, 1)
        root.addWidget(status)

        serial = QFrame()
        serial.setObjectName("serialSelectionPanel")
        serial.setFrameShape(QFrame.Shape.StyledPanel)
        serial_grid = QGridLayout(serial)
        serial_grid.addWidget(QLabel("Available Windows serial ports"), 0, 0)
        self._refresh_ports = QPushButton("Refresh ports")
        self._refresh_ports.setObjectName("refreshSerialPortsButton")
        serial_grid.addWidget(self._refresh_ports, 0, 1)
        self._serial_port = QComboBox()
        self._serial_port.setObjectName("serialPortSelection")
        self._serial_port.setAccessibleName("Explicit serial port selection")
        self._serial_port.addItem("Refresh ports to enumerate connected devices", None)
        serial_grid.addWidget(self._serial_port, 1, 0, 1, 2)
        serial_grid.addWidget(QLabel("Link kind"), 2, 0)
        self._link_kind = QComboBox()
        self._link_kind.setObjectName("serialLinkKindSelection")
        self._link_kind.addItem("USB direct", TelemetryLinkKind.USB.value)
        self._link_kind.addItem("SiK telemetry radio", TelemetryLinkKind.SIK.value)
        serial_grid.addWidget(self._link_kind, 2, 1)
        serial_grid.addWidget(QLabel("Baud"), 3, 0)
        self._baudrate = QComboBox()
        self._baudrate.setObjectName("serialBaudrateSelection")
        self._baudrate.addItem("115200 (USB default)", USB_DEFAULT_BAUDRATE)
        self._baudrate.addItem("57600 (SiK default)", SIK_DEFAULT_BAUDRATE)
        serial_grid.addWidget(self._baudrate, 3, 1)
        self._discover = QPushButton("Open selected port and discover vehicles")
        self._discover.setObjectName("discoverSelectedLinkButton")
        serial_grid.addWidget(self._discover, 4, 0, 1, 2)
        self._serial_status = QLabel(
            "Ports have not been enumerated. Refresh is explicit and does not open hardware."
        )
        self._serial_status.setObjectName("serialPortStatus")
        self._serial_status.setWordWrap(True)
        serial_grid.addWidget(self._serial_status, 5, 0, 1, 2)
        self._operation_status = QLabel()
        self._operation_status.setObjectName("connectedOperationStatus")
        self._operation_status.setWordWrap(True)
        serial_grid.addWidget(self._operation_status, 6, 0, 1, 2)
        root.addWidget(serial)

        discovery = QHBoxLayout()
        self._target = QComboBox()
        self._target.setObjectName("connectedTargetSelection")
        discovery.addWidget(QLabel("Discovered vehicle"))
        discovery.addWidget(self._target, 1)
        root.addLayout(discovery)

        self._mission_items = QListWidget()
        self._mission_items.setObjectName("onboardMissionItems")
        root.addWidget(self._mission_items, 1)
        self._replacement = QCheckBox(
            "I explicitly approve replacing the mission currently shown above."
        )
        self._replacement.setObjectName("confirmMissionReplacement")
        root.addWidget(self._replacement)

        actions = QHBoxLayout()
        self._inspect = QPushButton("Inspect onboard mission")
        self._inspect.setObjectName("inspectOnboardMissionButton")
        self._upload = QPushButton("Upload and verify")
        self._upload.setObjectName("uploadAndVerifyButton")
        self._refresh = QPushButton("Refresh telemetry")
        self._refresh.setObjectName("refreshConnectedTelemetryButton")
        self._reverify = QPushButton("Re-download and compare")
        self._reverify.setObjectName("reverifyConnectedMissionButton")
        self._disconnect = QPushButton("Disconnect")
        self._disconnect.setObjectName("disconnectConnectedButton")
        for button in (
            self._inspect,
            self._upload,
            self._refresh,
            self._reverify,
            self._disconnect,
        ):
            actions.addWidget(button)
        root.addLayout(actions)
        self._failure = QLabel()
        self._failure.setObjectName("connectedFailure")
        self._failure.setWordWrap(True)
        self._failure.setStyleSheet("color: #a52620; font-weight: 600;")
        root.addWidget(self._failure)

        self._refresh_ports.clicked.connect(
            lambda: self.intent_emitted.emit(RefreshPortsRequested())
        )
        self._serial_port.currentIndexChanged.connect(
            lambda _index: self.render_snapshot(self._snapshot)
        )
        self._link_kind.currentIndexChanged.connect(self._link_kind_changed)
        self._discover.clicked.connect(self._discover_selected)
        self._target.activated.connect(self._select_target)
        self._inspect.clicked.connect(lambda: self.intent_emitted.emit(InspectMissionRequested()))
        self._replacement.toggled.connect(
            lambda checked: self.intent_emitted.emit(ReplacementConfirmationRequested(checked))
        )
        self._upload.clicked.connect(
            lambda: self.intent_emitted.emit(UploadVerificationRequested())
        )
        self._refresh.clicked.connect(lambda: self.intent_emitted.emit(TelemetryRefreshRequested()))
        self._reverify.clicked.connect(lambda: self.intent_emitted.emit(ReverifyMissionRequested()))
        self._disconnect.clicked.connect(lambda: self.intent_emitted.emit(DisconnectRequested()))

    def _link_kind_changed(self, _index: int) -> None:
        kind = self._link_kind.currentData()
        default = (
            USB_DEFAULT_BAUDRATE if kind == TelemetryLinkKind.USB.value else SIK_DEFAULT_BAUDRATE
        )
        index = self._baudrate.findData(default)
        if index >= 0:
            self._baudrate.setCurrentIndex(index)
        self.render_snapshot(self._snapshot)

    def _discover_selected(self) -> None:
        selected = self._serial_port.currentData()
        kind = self._link_kind.currentData()
        baudrate = self._baudrate.currentData()
        if not isinstance(selected, SerialPortInfo):
            self._serial_status.setText("Select one enumerated serial port before opening a link.")
            return
        if kind not in {TelemetryLinkKind.USB.value, TelemetryLinkKind.SIK.value} or not isinstance(
            baudrate, int
        ):
            self._serial_status.setText("Select a valid link kind and baud before opening a link.")
            return
        if kind == TelemetryLinkKind.USB.value:
            intent: ConnectedIntent = DiscoverUsbRequested(selected.device, baudrate)
        else:
            intent = DiscoverSikRequested(selected.device, baudrate)
        self.intent_emitted.emit(intent)

    def _select_target(self, index: int) -> None:
        candidate = self._target.itemData(index)
        if isinstance(candidate, ConnectedTarget):
            self.intent_emitted.emit(
                TargetSelectionRequested(candidate.system_id, candidate.component_id)
            )


def _target_label(target: ConnectedTarget) -> str:
    armed = "ARMED — upload blocked" if target.armed else "disarmed"
    return (
        f"{target.system_id}:{target.component_id} · {target.vehicle.value} · "
        f"{target.link_kind.value.upper()} · {armed}"
    )


def _verification_text(state: ConnectedVerificationState) -> str:
    return {
        ConnectedVerificationState.UNVERIFIED: "Not verified",
        ConnectedVerificationState.USB_VERIFIED: "USB upload and full readback verified",
        ConnectedVerificationState.REVERIFY_REQUIRED: "Reconnect requires a new full comparison",
        ConnectedVerificationState.SIK_VERIFIED: "Same-vehicle SiK readback verified",
        ConnectedVerificationState.MISMATCH: "Mismatch — readiness blocked",
    }[state]


def _telemetry_text(snapshot: ConnectedMissionSnapshot) -> str:
    telemetry = snapshot.telemetry
    if telemetry is None:
        return "Not refreshed"
    heartbeat = telemetry.heartbeat.value
    if heartbeat is None:
        return "Incomplete — no selected-vehicle heartbeat was received"
    home = "Home available" if telemetry.home.value is not None else "Home unavailable"
    armed = "ARMED" if heartbeat.armed else "disarmed"
    return f"{heartbeat.mode_name} · {armed} · {home}"


def _onboard_item_text(item: NativeMissionItem) -> str:
    sequence = item.sequence
    command = item.command
    altitude_m = item.altitude_m
    labels = {
        16: "Waypoint",
        18: "Circle",
        19: "Hold",
        21: "Land",
        22: "Takeoff",
        178: "Cruise speed",
    }
    label = (
        "Home"
        if sequence == 0 and command == 16 and item.frame == 0
        else labels.get(command, "Unsupported native item")
        if sequence != 0
        else "Unsupported native item"
    )
    altitude = (
        f" · {altitude_m:g} m" if isinstance(altitude_m, int | float) and command != 178 else ""
    )
    return f"{sequence}. {label}{altitude}"
