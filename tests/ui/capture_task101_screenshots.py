"""Capture meaningful Task 101 normal-arm states from the production widget."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from skywriter.application.arm import NormalArmSnapshot, NormalArmState
from skywriter.application.prearm import (
    NativePrearmAssessment,
    PrearmReadinessSnapshot,
    PrearmRequestState,
)
from skywriter.application.telemetry import NativeStatusText, TelemetrySnapshot, TimedSignal
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

OUTPUT_ROOT = Path("docs/screenshots/task-101")
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
            base_mode=81,
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


def capture(
    readiness: PrearmReadinessSnapshot,
    arm: NormalArmSnapshot,
    now_s: float,
    filename: str,
) -> None:
    widget = PreflightTelemetryWidget()
    widget.render_readiness(readiness, now_s=now_s)
    widget.render_arm(arm)
    widget.resize(1500, 1260)
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
    application = create_application(["skywriter-task-101-screenshots"])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\arial.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise RuntimeError("Windows Arial font was unavailable for screenshot rendering")
    application.setFont(QFont(families[0], 10))
    telemetry, now_s = fixture_snapshot()
    if telemetry.heartbeat.value is None or telemetry.heartbeat.observed_at_s is None:
        raise RuntimeError("telemetry fixture must contain a heartbeat")
    armed_telemetry = replace(
        telemetry,
        heartbeat=TimedSignal(
            replace(telemetry.heartbeat.value, armed=True),
            telemetry.heartbeat.observed_at_s,
            telemetry.heartbeat.valid_for_s,
        ),
    )
    readiness = PrearmReadinessSnapshot(
        request_state=PrearmRequestState.ACCEPTED,
        detail="Current native request accepted and reviewed.",
        telemetry=telemetry,
        ack_result=0,
        native_assessment=NativePrearmAssessment.HEALTHY,
        review_acknowledged=True,
    )
    native_failure = NativeStatusText(2, "PreArm: GPS 1: not healthy", 0, 0, now_s)
    states = (
        (
            NormalArmSnapshot(
                state=NormalArmState.PENDING,
                detail="Waiting for the exact acknowledgment and fresh armed telemetry.",
                requested_at_s=now_s,
            ),
            "01-pending.png",
        ),
        (
            NormalArmSnapshot(
                state=NormalArmState.ARMED,
                detail="Normal Arm acknowledged and confirmed by selected-target telemetry.",
                ack_result=0,
                requested_at_s=now_s,
                completed_at_s=now_s + 0.4,
                armed_observed_at_s=now_s + 0.4,
            ),
            "02-armed-confirmed.png",
        ),
        (
            NormalArmSnapshot(
                state=NormalArmState.REJECTED,
                detail="ArduCopter rejected normal Arm with MAV_RESULT 4.",
                ack_result=4,
                native_messages=(native_failure,),
            ),
            "03-native-rejected.png",
        ),
        (
            NormalArmSnapshot(
                state=NormalArmState.TIMED_OUT,
                detail="No matching normal Arm acknowledgment arrived before the deadline.",
            ),
            "04-timeout.png",
        ),
        (
            NormalArmSnapshot(
                state=NormalArmState.LINK_LOST,
                detail="SiK link was lost during normal Arm; vehicle state is uncertain.",
            ),
            "05-link-loss.png",
        ),
        (
            NormalArmSnapshot(
                state=NormalArmState.TELEMETRY_DISAGREEMENT,
                detail="Arm was acknowledged, but selected-target telemetry remained disarmed.",
                ack_result=0,
            ),
            "06-telemetry-disagreement.png",
        ),
    )
    for arm, filename in states:
        state_readiness = (
            replace(readiness, telemetry=armed_telemetry)
            if arm.state is NormalArmState.ARMED
            else readiness
        )
        capture(state_readiness, arm, now_s + 0.1, filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
