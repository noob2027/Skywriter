"""Capture meaningful Task 104 Land Here Now states from the production widget."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from skywriter.application.auto_start import NativeAutoStartSnapshot, NativeAutoStartState
from skywriter.application.land_here_now import (
    MAV_LANDED_STATE_IN_AIR,
    NativeLandHereNowAuthorization,
    NativeLandHereNowSnapshot,
    NativeLandHereNowState,
)
from skywriter.application.pause_resume import (
    MAV_LANDED_STATE_LANDING,
    MAV_LANDED_STATE_ON_GROUND,
    MAV_MISSION_STATE_ACTIVE,
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

OUTPUT_ROOT = Path("docs/screenshots/task-104")
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
    armed: bool = True,
    mode_number: int = 3,
    mode_name: str = "Auto",
    landed_state: int = MAV_LANDED_STATE_IN_AIR,
    link_connected: bool = True,
) -> TelemetrySnapshot:
    assert telemetry.heartbeat.value is not None
    assert telemetry.heartbeat.observed_at_s is not None
    assert telemetry.mission.value is not None
    assert telemetry.mission.observed_at_s is not None
    observed_at_s = telemetry.mission.observed_at_s
    return replace(
        telemetry,
        link_connected=link_connected,
        heartbeat=TimedSignal(
            replace(
                telemetry.heartbeat.value,
                armed=armed,
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
                total_items=7,
                mission_state=MAV_MISSION_STATE_ACTIVE,
                last_reached_sequence=3,
            ),
            observed_at_s,
            telemetry.mission.valid_for_s,
        ),
        extended_state=TimedSignal(
            ExtendedStateTelemetry(landed_state, 0),
            observed_at_s,
            5.0,
        ),
    )


def authorization() -> NativeLandHereNowAuthorization:
    return NativeLandHereNowAuthorization(
        vehicle_identity="mavlink-system-1-component-1",
        system_id=1,
        component_id=1,
        mission_revision=8,
        expected_mission_digest="d" * 64,
        auto_start_revision=5,
        first_executable_sequence=1,
        last_sequence=7,
        progress_sequence=4,
        mission_state=MAV_MISSION_STATE_ACTIVE,
    )


def capture(
    telemetry: TelemetrySnapshot,
    control: NativeLandHereNowSnapshot,
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
    widget.render_land_here_now(control)
    widget.resize(1500, 1550)
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
    application = create_application(["skywriter-task-104-screenshots"])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\arial.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise RuntimeError("Windows Arial font was unavailable for screenshot rendering")
    application.setFont(QFont(families[0], 10))
    telemetry, now_s = fixture_snapshot()
    active = flight_state(telemetry)
    landing = flight_state(
        telemetry,
        mode_number=9,
        mode_name="Land",
        landed_state=MAV_LANDED_STATE_LANDING,
    )
    landed = flight_state(
        telemetry,
        armed=False,
        mode_number=9,
        mode_name="Land",
        landed_state=MAV_LANDED_STATE_ON_GROUND,
    )
    rejected_text = NativeStatusText(3, "Mode change to Land denied", 0, 0, now_s)
    states = (
        (
            active,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.CONFIRMATION_REQUIRED,
                detail=(
                    "This abandons all remaining mission progress and lands at the aircraft's "
                    "current location. Confirm only if that is deliberate."
                ),
                authorization=authorization(),
                confirm_available=True,
                cancel_available=True,
                confirmation_requested_at_s=now_s,
            ),
            "01-confirmation-required.png",
        ),
        (
            active,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.PENDING,
                detail="Waiting for the exact native Land ACK and later landing telemetry.",
                authorization=authorization(),
                confirmation_requested_at_s=now_s - 0.2,
                requested_at_s=now_s,
            ),
            "02-pending.png",
        ),
        (
            landing,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.LANDING,
                detail="Native Land acknowledged; Land mode and Landing telemetry confirmed.",
                authorization=authorization(),
                ack_result=0,
                requested_at_s=now_s - 0.5,
                completed_at_s=now_s,
                land_mode_observed_at_s=now_s - 0.2,
                landed_state_observed_at_s=now_s - 0.1,
                landed_state=MAV_LANDED_STATE_LANDING,
            ),
            "03-accepted-landing.png",
        ),
        (
            active,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.REJECTED,
                detail="ArduCopter rejected native Land with MAV_RESULT 4.",
                ack_result=4,
                native_messages=(rejected_text,),
            ),
            "04-native-rejected.png",
        ),
        (
            active,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.TIMED_OUT,
                detail="No matching native Land acknowledgment arrived before the deadline.",
            ),
            "05-timeout.png",
        ),
        (
            flight_state(telemetry, link_connected=False),
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.LINK_LOST,
                detail="SiK link was lost; onboard behavior remains native.",
            ),
            "06-link-loss.png",
        ),
        (
            landing,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.ALREADY_LANDING,
                detail="Vehicle already reports native Land mode and Landing.",
            ),
            "07-already-landing.png",
        ),
        (
            landed,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.ALREADY_LANDED,
                detail="Vehicle already reports On Ground; no command was sent.",
            ),
            "08-already-landed.png",
        ),
        (
            active,
            NativeLandHereNowSnapshot(
                state=NativeLandHereNowState.TELEMETRY_DISAGREEMENT,
                detail=(
                    "Native Land was acknowledged, but later AUTO/In Air telemetry did not "
                    "confirm Landing."
                ),
                ack_result=0,
                landed_state=MAV_LANDED_STATE_IN_AIR,
            ),
            "09-telemetry-disagreement.png",
        ),
    )
    for state_telemetry, control, filename in states:
        capture(state_telemetry, control, now_s + 0.1, filename)
    return 0


if __name__ == "__main__":
    sys.exit(main())
