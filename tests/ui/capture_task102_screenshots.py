"""Capture meaningful Task 102 native AUTO-start states from the production widget."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from skywriter.application.auto_start import NativeAutoStartSnapshot, NativeAutoStartState
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
from skywriter.ui.flight import FlightTelemetryWidget

OUTPUT_ROOT = Path("docs/screenshots/task-102")
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
    telemetry: TelemetrySnapshot,
    start: NativeAutoStartSnapshot,
    now_s: float,
    filename: str,
) -> None:
    widget = FlightTelemetryWidget()
    widget.render_snapshot(telemetry, now_s=now_s)
    widget.render_auto_start(start)
    widget.resize(1500, 1200)
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
    application = create_application(["skywriter-task-102-screenshots"])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\arial.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise RuntimeError("Windows Arial font was unavailable for screenshot rendering")
    application.setFont(QFont(families[0], 10))
    telemetry, now_s = fixture_snapshot()
    if telemetry.heartbeat.value is None or telemetry.heartbeat.observed_at_s is None:
        raise RuntimeError("telemetry fixture must contain a heartbeat")
    if telemetry.mission.value is None or telemetry.mission.observed_at_s is None:
        raise RuntimeError("telemetry fixture must contain mission progress")
    armed_stabilize = replace(
        telemetry,
        heartbeat=TimedSignal(
            replace(telemetry.heartbeat.value, armed=True, mode_number=0, mode_name="Stabilize"),
            telemetry.heartbeat.observed_at_s,
            telemetry.heartbeat.valid_for_s,
        ),
        mission=TimedSignal(
            replace(
                telemetry.mission.value,
                current_sequence=1,
                last_reached_sequence=None,
            ),
            telemetry.mission.observed_at_s,
            telemetry.mission.valid_for_s,
        ),
    )
    armed_auto = replace(
        armed_stabilize,
        heartbeat=TimedSignal(
            replace(telemetry.heartbeat.value, armed=True, mode_number=3, mode_name="Auto"),
            telemetry.heartbeat.observed_at_s,
            telemetry.heartbeat.valid_for_s,
        ),
        mission=TimedSignal(
            replace(
                telemetry.mission.value,
                current_sequence=2,
                last_reached_sequence=2,
            ),
            telemetry.mission.observed_at_s,
            telemetry.mission.valid_for_s,
        ),
    )
    link_lost = replace(armed_stabilize, link_connected=False)
    mission_mismatch = replace(
        armed_auto,
        mission=TimedSignal(
            replace(telemetry.mission.value, current_sequence=9),
            telemetry.mission.observed_at_s,
            telemetry.mission.valid_for_s,
        ),
    )
    denied = NativeStatusText(2, "Flight mode change failed", 0, 0, now_s)
    states = (
        (
            armed_stabilize,
            NativeAutoStartSnapshot(
                state=NativeAutoStartState.PENDING,
                detail="Waiting for the exact ACK, armed AUTO, and mission progress.",
                requested_at_s=now_s,
            ),
            "01-pending.png",
        ),
        (
            armed_auto,
            NativeAutoStartSnapshot(
                state=NativeAutoStartState.RUNNING,
                detail="Native mission start acknowledged; AUTO and progress confirmed.",
                ack_result=0,
                requested_at_s=now_s,
                completed_at_s=now_s + 0.8,
                auto_observed_at_s=now_s + 0.4,
                progress_observed_at_s=now_s + 0.8,
                progress_sequence=2,
            ),
            "02-running-confirmed.png",
        ),
        (
            armed_stabilize,
            NativeAutoStartSnapshot(
                state=NativeAutoStartState.REJECTED,
                detail="ArduCopter rejected native mission start with MAV_RESULT 2.",
                ack_result=2,
                native_messages=(denied,),
            ),
            "03-native-rejected.png",
        ),
        (
            armed_stabilize,
            NativeAutoStartSnapshot(
                state=NativeAutoStartState.TIMED_OUT,
                detail="No matching native mission-start ACK arrived before the deadline.",
            ),
            "04-timeout.png",
        ),
        (
            link_lost,
            NativeAutoStartSnapshot(
                state=NativeAutoStartState.LINK_LOST,
                detail="SiK link was lost; onboard behavior remains native and state is uncertain.",
            ),
            "05-link-loss.png",
        ),
        (
            armed_stabilize,
            NativeAutoStartSnapshot(
                state=NativeAutoStartState.UNEXPECTED_MODE,
                detail="Start was acknowledged, but fresh telemetry remained Stabilize.",
                ack_result=0,
            ),
            "06-unexpected-mode.png",
        ),
        (
            mission_mismatch,
            NativeAutoStartSnapshot(
                state=NativeAutoStartState.MISSION_MISMATCH,
                detail="Mission progress sequence 9 is outside the exact verified mission.",
                ack_result=0,
            ),
            "07-mission-mismatch.png",
        ),
    )
    for state_telemetry, start, filename in states:
        capture(state_telemetry, start, now_s + 0.1, filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
