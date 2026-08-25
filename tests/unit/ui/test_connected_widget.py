from __future__ import annotations

from typing import cast

from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from skywriter.application.connected import ConnectedMissionSnapshot, ConnectedTarget
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.main import create_application
from skywriter.ui.connected import (
    ConnectedIntent,
    ConnectedMissionWidget,
    DiscoverUsbRequested,
    InspectMissionRequested,
    TargetSelectionRequested,
)


def test_connected_panel_renders_identity_and_emits_only_typed_intents() -> None:
    create_application(["skywriter-connected-widget-test"])
    widget = ConnectedMissionWidget()
    received: list[ConnectedIntent] = []
    widget.intent_emitted.connect(lambda value: received.append(cast(ConnectedIntent, value)))
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

    discover = widget.findChild(QPushButton, "discoverUsbButton")
    inspect = widget.findChild(QPushButton, "inspectOnboardMissionButton")
    selection = widget.findChild(QComboBox, "connectedTargetSelection")
    assert discover is not None and inspect is not None and selection is not None
    discover.click()
    inspect.click()
    selection.activated.emit(1)

    assert received == [
        DiscoverUsbRequested(),
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
