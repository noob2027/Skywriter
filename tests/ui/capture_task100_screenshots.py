"""Capture meaningful Task 100 Preflight review states from the production widget."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from skywriter.application.prearm import (
    MAV_SYS_STATUS_PREARM_CHECK,
    NativePrearmAssessment,
    PrearmReadinessSnapshot,
    PrearmRequestState,
)
from skywriter.application.telemetry import (
    NativeStatusText,
    SensorStatusTelemetry,
    TelemetrySnapshot,
    TimedSignal,
)
from skywriter.compatibility.arducopter_4_6_3 import VehicleIdentity
from skywriter.infrastructure.mavlink.connection import (
    IncomingMessage,
    MavlinkAddress,
    TargetCandidate,
    TransportKind,
)
from skywriter.infrastructure.mavlink.telemetry import TelemetryAdapter
from skywriter.main import create_application
from skywriter.ui.preflight import PreflightTelemetryWidget

OUTPUT_ROOT = Path("docs/screenshots/task-100")
FIXTURE = Path("tests/fixtures/telemetry/arducopter-4.6.3.jsonl")
TARGET = MavlinkAddress(1, 1)


def fixture_snapshot() -> tuple[TelemetrySnapshot, float]:
    adapter = TelemetryAdapter(
        TargetCandidate(
            address=TARGET,
            vehicle=VehicleIdentity("mavlink-system-1-component-1"),
            transport=TransportKind.SIK,
            vehicle_type=2,
            autopilot_type=3,
            base_mode=89,
            observed_at_s=99.0,
        )
    )
    last_observed = 100.0
    for index, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines()):
        document = json.loads(line)
        last_observed = 100.0 + index / 10
        result = adapter.ingest(
            IncomingMessage(
                name=str(document["name"]),
                source=MavlinkAddress(
                    int(document["source_system"]), int(document["source_component"])
                ),
                fields=document["fields"],
            ),
            observed_at_s=last_observed,
        )
        if not result.accepted:
            raise RuntimeError(f"fixture message rejected: {result}")
    return adapter.snapshot(link_connected=True), last_observed


def capture(readiness: PrearmReadinessSnapshot, now_s: float, filename: str) -> None:
    widget = PreflightTelemetryWidget()
    widget.render_readiness(readiness, now_s=now_s)
    widget.resize(1500, 1060)
    widget.show()
    for _ in range(5):
        create_application().processEvents()
        QTest.qWait(40)
    image = widget.grab().toImage()
    path = OUTPUT_ROOT / filename
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"failed to save {path}")
    widget.close()


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    application = create_application(["skywriter-task-100-screenshots"])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\arial.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise RuntimeError("Windows Arial font was unavailable for screenshot rendering")
    application.setFont(QFont(families[0], 10))
    telemetry, now_s = fixture_snapshot()
    if telemetry.sensors.value is None or telemetry.sensors.observed_at_s is None:
        raise RuntimeError("telemetry fixture must contain native sensor flags")
    sensors = telemetry.sensors.value
    healthy_telemetry = replace(
        telemetry,
        sensors=TimedSignal(
            SensorStatusTelemetry(
                present_flags=sensors.present_flags | MAV_SYS_STATUS_PREARM_CHECK,
                enabled_flags=sensors.enabled_flags | MAV_SYS_STATUS_PREARM_CHECK,
                health_flags=sensors.health_flags | MAV_SYS_STATUS_PREARM_CHECK,
            ),
            telemetry.sensors.observed_at_s,
            telemetry.sensors.valid_for_s,
        ),
    )
    failure = NativeStatusText(2, "PreArm: GPS 1: not healthy", 0, 0, now_s)

    states = (
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.BLOCKED_MISSION,
                detail="Exact current same-vehicle SiK mission verification is required.",
                telemetry=healthy_telemetry,
            ),
            "01-gate-blocked.png",
        ),
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.PENDING,
                detail="Waiting for the matching native acknowledgment.",
                telemetry=healthy_telemetry,
                repeated_request_ignored=True,
                requested_at_s=now_s,
            ),
            "02-pending-repeated-request.png",
        ),
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.ACCEPTED,
                detail="ArduCopter accepted and ran the request; this is not arm approval.",
                telemetry=healthy_telemetry,
                ack_result=0,
                native_assessment=NativePrearmAssessment.HEALTHY,
            ),
            "03-accepted-awaiting-review.png",
        ),
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.ACCEPTED,
                detail="Operator reviewed the current native result and observations.",
                telemetry=healthy_telemetry,
                ack_result=0,
                native_assessment=NativePrearmAssessment.HEALTHY,
                review_acknowledged=True,
            ),
            "04-reviewed-application-gate.png",
        ),
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.ACCEPTED,
                detail="The request ran, but native observations report a failure.",
                telemetry=healthy_telemetry,
                ack_result=0,
                native_messages=(failure,),
                native_assessment=NativePrearmAssessment.CONFLICTING,
            ),
            "05-native-failure-conflict.png",
        ),
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.UNSUPPORTED,
                detail="Target reported command 401 as unsupported.",
                telemetry=telemetry,
                ack_result=3,
            ),
            "06-unsupported.png",
        ),
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.TIMED_OUT,
                detail="No matching acknowledgment arrived before the bounded deadline.",
                telemetry=telemetry,
            ),
            "07-timeout.png",
        ),
        (
            PrearmReadinessSnapshot(
                request_state=PrearmRequestState.WRONG_ACK,
                detail="An unrelated or misaddressed command acknowledgment was received.",
                telemetry=telemetry,
            ),
            "08-wrong-ack.png",
        ),
    )
    for readiness, filename in states:
        capture(readiness, now_s + 0.1, filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
