"""Capture meaningful Task 103 Pause/Resume states from the production widget."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from skywriter.application.auto_start import NativeAutoStartSnapshot, NativeAutoStartState
from skywriter.application.pause_resume import (
    MAV_LANDED_STATE_LANDING,
    MAV_MISSION_STATE_ACTIVE,
    MAV_MISSION_STATE_COMPLETE,
    MAV_MISSION_STATE_PAUSED,
    NativePauseResumeAction,
    NativePauseResumeSnapshot,
    NativePauseResumeState,
)
from skywriter.application.telemetry import (
    ExtendedStateTelemetry,
    NativeStatusText,
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
from skywriter.ui.flight import FlightTelemetryWidget

OUTPUT_ROOT = Path("docs/screenshots/task-103")
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
            base_mode=128,
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


def flight_state(
    telemetry: TelemetrySnapshot,
    *,
    mission_state: int,
    mode_number: int = 3,
    mode_name: str = "Auto",
    landed_state: int | None = None,
) -> TelemetrySnapshot:
    assert telemetry.heartbeat.value is not None
    assert telemetry.heartbeat.observed_at_s is not None
    assert telemetry.mission.value is not None
    assert telemetry.mission.observed_at_s is not None
    extended = telemetry.extended_state
    if landed_state is not None:
        extended = TimedSignal(
            ExtendedStateTelemetry(landed_state, 0),
            telemetry.mission.observed_at_s,
            5.0,
        )
    return replace(
        telemetry,
        heartbeat=TimedSignal(
            replace(
                telemetry.heartbeat.value,
                armed=True,
                mode_number=mode_number,
                mode_name=mode_name,
            ),
            telemetry.heartbeat.observed_at_s,
            telemetry.heartbeat.valid_for_s,
        ),
        mission=TimedSignal(
            replace(
                telemetry.mission.value,
                current_sequence=4,
                total_items=8,
                mission_state=mission_state,
                last_reached_sequence=3,
            ),
            telemetry.mission.observed_at_s,
            telemetry.mission.valid_for_s,
        ),
        extended_state=extended,
    )


def capture(
    telemetry: TelemetrySnapshot,
    control: NativePauseResumeSnapshot,
    now_s: float,
    filename: str,
) -> None:
    widget = FlightTelemetryWidget()
    widget.render_snapshot(telemetry, now_s=now_s)
    widget.render_auto_start(
        NativeAutoStartSnapshot(
            state=NativeAutoStartState.RUNNING,
            detail="Native mission start acknowledged; AUTO and progress confirmed.",
            ack_result=0,
            requested_at_s=now_s - 1.0,
            completed_at_s=now_s - 0.8,
            auto_observed_at_s=now_s - 0.9,
            progress_observed_at_s=now_s - 0.8,
            progress_sequence=2,
        )
    )
    widget.render_pause_resume(control)
    widget.resize(1500, 1400)
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
    application = create_application(["skywriter-task-103-screenshots"])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\arial.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise RuntimeError("Windows Arial font was unavailable for screenshot rendering")
    application.setFont(QFont(families[0], 10))
    telemetry, now_s = fixture_snapshot()
    active = flight_state(telemetry, mission_state=MAV_MISSION_STATE_ACTIVE)
    paused = flight_state(telemetry, mission_state=MAV_MISSION_STATE_PAUSED)
    complete = flight_state(telemetry, mission_state=MAV_MISSION_STATE_COMPLETE)
    landing = flight_state(
        telemetry,
        mission_state=MAV_MISSION_STATE_ACTIVE,
        mode_number=9,
        mode_name="Land",
        landed_state=MAV_LANDED_STATE_LANDING,
    )
    rejected_text = NativeStatusText(6, "Failed to pause", 0, 0, now_s)
    states = (
        (
            active,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.PAUSE_PENDING,
                detail="Waiting for exact Pause ACK and later pinned mission-state telemetry.",
                last_action=NativePauseResumeAction.PAUSE,
                requested_at_s=now_s,
                state_observed_at_s=now_s,
                progress_sequence=4,
            ),
            "01-pause-pending.png",
        ),
        (
            paused,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.PAUSED,
                detail="Native Pause acknowledged; pinned Paused telemetry confirmed.",
                resume_available=True,
                last_action=NativePauseResumeAction.PAUSE,
                ack_result=0,
                state_observed_at_s=now_s,
                progress_sequence=4,
            ),
            "02-paused-confirmed.png",
        ),
        (
            paused,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.RESUME_PENDING,
                detail="Waiting for exact Resume ACK and later pinned mission-state telemetry.",
                last_action=NativePauseResumeAction.RESUME,
                requested_at_s=now_s,
                state_observed_at_s=now_s,
                progress_sequence=4,
            ),
            "03-resume-pending.png",
        ),
        (
            active,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.RUNNING,
                detail="Native Resume acknowledged; pinned Active telemetry confirmed.",
                pause_available=True,
                last_action=NativePauseResumeAction.RESUME,
                ack_result=0,
                state_observed_at_s=now_s,
                progress_sequence=4,
            ),
            "04-running-confirmed.png",
        ),
        (
            active,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.REJECTED,
                detail="ArduCopter rejected native Pause with MAV_RESULT 4.",
                last_action=NativePauseResumeAction.PAUSE,
                ack_result=4,
                native_messages=(rejected_text,),
            ),
            "05-native-rejected.png",
        ),
        (
            active,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.TIMED_OUT,
                detail="No matching native Pause acknowledgment arrived before the deadline.",
                last_action=NativePauseResumeAction.PAUSE,
            ),
            "06-timeout.png",
        ),
        (
            replace(active, link_connected=False),
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.LINK_LOST,
                detail="SiK link was lost; onboard behavior remains native.",
            ),
            "07-link-loss.png",
        ),
        (
            complete,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.MISSION_COMPLETED,
                detail="Native mission reports Complete.",
            ),
            "08-mission-complete.png",
        ),
        (
            landing,
            NativePauseResumeSnapshot(
                state=NativePauseResumeState.LANDING,
                detail="Vehicle telemetry reports Landing; both controls are disabled.",
            ),
            "09-landing.png",
        ),
    )
    for state_telemetry, control, filename in states:
        capture(state_telemetry, control, now_s + 0.1, filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
