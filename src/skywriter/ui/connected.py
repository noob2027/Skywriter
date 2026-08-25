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
from skywriter.compatibility.arducopter_4_6_3 import NativeMissionItem


@dataclass(frozen=True, slots=True)
class DiscoverUsbRequested:
    pass


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
    pass


@dataclass(frozen=True, slots=True)
class TelemetryRefreshRequested:
    pass


@dataclass(frozen=True, slots=True)
class ReverifyMissionRequested:
    pass


ConnectedIntent: TypeAlias = (
    DiscoverUsbRequested
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
        self._build_ui()
        self.render_snapshot(self._snapshot)

    @property
    def snapshot(self) -> ConnectedMissionSnapshot:
        return self._snapshot

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

        has_target = snapshot.selected_target is not None
        is_usb = snapshot.link_kind is not None and snapshot.link_kind.value == "usb"
        is_sik = snapshot.link_kind is not None and snapshot.link_kind.value == "sik"
        self._inspect.setEnabled(has_target and is_usb)
        self._replacement.setEnabled(snapshot.onboard is not None and is_usb)
        self._upload.setEnabled(snapshot.replacement_confirmed and is_usb)
        self._refresh.setEnabled(has_target)
        self._reverify.setEnabled(
            is_sik and snapshot.verification_state is ConnectedVerificationState.REVERIFY_REQUIRED
        )
        self._disconnect.setEnabled(snapshot.link_connected)

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
        root.addWidget(status)

        discovery = QHBoxLayout()
        self._discover_usb = QPushButton("Discover USB")
        self._discover_usb.setObjectName("discoverUsbButton")
        self._discover_sik = QPushButton("Discover SiK")
        self._discover_sik.setObjectName("discoverSikButton")
        self._target = QComboBox()
        self._target.setObjectName("connectedTargetSelection")
        discovery.addWidget(self._discover_usb)
        discovery.addWidget(self._discover_sik)
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

        self._discover_usb.clicked.connect(lambda: self.intent_emitted.emit(DiscoverUsbRequested()))
        self._discover_sik.clicked.connect(lambda: self.intent_emitted.emit(DiscoverSikRequested()))
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
