from __future__ import annotations

from typing import cast

from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from skywriter.application.connected import ConnectedMissionSnapshot, ConnectedTarget
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.serial_ports import SerialPortInfo
from skywriter.main import create_application
from skywriter.ui.connected import (
    ConnectedIntent,
    ConnectedMissionWidget,
    DiscoverSikRequested,
    DiscoverUsbRequested,
    InspectMissionRequested,
    RefreshPortsRequested,
    TargetSelectionRequested,
)


def test_connected_panel_renders_identity_and_emits_only_typed_intents() -> None:
    create_application(["skywriter-connected-widget-test"])
    widget = ConnectedMissionWidget()
    received: list[ConnectedIntent] = []
    widget.intent_emitted.connect(lambda value: received.append(cast(ConnectedIntent, value)))
    widget.set_serial_ports((SerialPortInfo("COM7", "USB Serial Device", "Matek"),))
    refresh_ports = widget.findChild(QPushButton, "refreshSerialPortsButton")
    serial_port = widget.findChild(QComboBox, "serialPortSelection")
    link_kind = widget.findChild(QComboBox, "serialLinkKindSelection")
    baudrate = widget.findChild(QComboBox, "serialBaudrateSelection")
    discover = widget.findChild(QPushButton, "discoverSelectedLinkButton")
    assert all(
        control is not None
        for control in (refresh_ports, serial_port, link_kind, baudrate, discover)
    )
    assert serial_port is not None
    assert link_kind is not None
    assert baudrate is not None
    assert discover is not None
    assert refresh_ports is not None
    assert serial_port.currentData() is None
    assert not discover.isEnabled()
    refresh_ports.click()
    serial_port.setCurrentIndex(1)
    assert discover.isEnabled()
    assert baudrate.currentData() == 115200
    discover.click()
    link_kind.setCurrentIndex(1)
    assert baudrate.currentData() == 57600
    discover.click()
    candidate = ConnectedTarget(
        VehicleIdentity("mavlink-system-1-component-1"),
        1,
        1,
        TelemetryLinkKind.USB,
        2,
        3,
        0,
        100.0,
    )
    widget.render_snapshot(
        ConnectedMissionSnapshot(
            candidates=(candidate,),
            selected_target=candidate,
            link_kind=TelemetryLinkKind.USB,
            link_connected=True,
        )
    )

    inspect = widget.findChild(QPushButton, "inspectOnboardMissionButton")
    selection = widget.findChild(QComboBox, "connectedTargetSelection")
    assert inspect is not None and selection is not None
    inspect.click()
    selection.activated.emit(1)

    assert received == [
        RefreshPortsRequested(),
        DiscoverUsbRequested("COM7", 115200),
        DiscoverSikRequested("COM7", 57600),
        InspectMissionRequested(),
        TargetSelectionRequested(1, 1),
    ]
    safety = widget.findChild(QLabel, "connectedSafetyBoundary")
    assert safety is not None
    assert "no Arm" in safety.text()


def test_connected_panel_has_no_prohibited_flight_or_parameter_controls() -> None:
    create_application(["skywriter-connected-confinement-test"])
    widget = ConnectedMissionWidget()
    labels = " ".join(button.text().lower() for button in widget.findChildren(QPushButton))

    for prohibited in (
        "arm",
        "auto",
        "pause",
        "resume",
        "land",
        "rtl",
        "parameter",
        "mode",
    ):
        assert prohibited not in labels
