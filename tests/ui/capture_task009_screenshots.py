"""Capture Task 009 connected mission states from the production widget."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from skywriter.application.connected import (
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
    MissionReadback,
)
from skywriter.application.telemetry import TelemetryLinkKind
from skywriter.compatibility.arducopter_4_6_3 import NativeMissionItem, VehicleIdentity
from skywriter.main import create_application
from skywriter.ui.connected import ConnectedMissionWidget

OUTPUT_ROOT = Path("docs/screenshots/task-009")


def target(kind: TelemetryLinkKind) -> ConnectedTarget:
    return ConnectedTarget(
        VehicleIdentity("mavlink-system-1-component-1"),
        1,
        1,
        kind,
        2,
        3,
        0,
        100.0,
    )


def onboard() -> MissionReadback:
    return MissionReadback(
        tuple(
            NativeMissionItem(
                sequence=sequence,
                frame=0 if sequence == 0 else 3,
                command=16 if sequence == 0 else command,
                current=False,
                autocontinue=True,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                latitude_e7=515007292,
                longitude_e7=-1246254,
                altitude_m=15.0 if sequence == 0 else 3.0,
                mission_type=0,
            )
            for sequence, command in enumerate((16, 22, 178, 16, 19, 18, 16, 21))
        )
    )


def capture(widget: ConnectedMissionWidget, filename: str) -> None:
    widget.resize(1440, 860)
    widget.show()
    for _ in range(5):
        create_application().processEvents()
        QTest.qWait(40)
    path = OUTPUT_ROOT / filename
    image = widget.grab().toImage()
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"failed to save {path}")
    widget.close()


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    application = create_application(["skywriter-task-009-screenshots"])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\arial.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise RuntimeError("Windows Arial font was unavailable for screenshot rendering")
    application.setFont(QFont(families[0], 10))

    usb = target(TelemetryLinkKind.USB)
    approval = ConnectedMissionWidget()
    approval.render_snapshot(
        ConnectedMissionSnapshot(
            candidates=(usb,),
            selected_target=usb,
            link_kind=TelemetryLinkKind.USB,
            link_connected=True,
            onboard=onboard(),
        )
    )
    capture(approval, "01-usb-replacement-review.png")

    sik = target(TelemetryLinkKind.SIK)
    verified = ConnectedMissionWidget()
    verified.render_snapshot(
        ConnectedMissionSnapshot(
            candidates=(sik,),
            selected_target=sik,
            link_kind=TelemetryLinkKind.SIK,
            link_connected=True,
            onboard=onboard(),
            verification_state=ConnectedVerificationState.SIK_VERIFIED,
        )
    )
    capture(verified, "02-sik-verified.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
