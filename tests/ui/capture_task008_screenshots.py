"""Capture Task 008 read-only telemetry states from the production widgets."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from skywriter.application.telemetry import TelemetryPoint, TelemetryRoute, TelemetryRoutePoint
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
from skywriter.ui.preflight import PreflightTelemetryWidget

OUTPUT_ROOT = Path("docs/screenshots/task-008")
FIXTURE = Path("tests/fixtures/telemetry/arducopter-4.6.3.jsonl")
TARGET = MavlinkAddress(1, 1)


def fixture_snapshot() -> tuple[TelemetryAdapter, float]:
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
    return adapter, last_observed


def sample_route() -> TelemetryRoute:
    base = TelemetryPoint(51.5007291, -0.1246254)
    return TelemetryRoute(
        tuple(
            TelemetryRoutePoint(
                sequence,
                TelemetryPoint(
                    base.latitude_deg + sequence * 0.00011,
                    base.longitude_deg + sequence * 0.00013,
                ),
                f"Native item {sequence}",
            )
            for sequence in range(8)
        )
    )


def capture(widget: QWidget, filename: str) -> None:
    widget.resize(1440, 860)
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
    application = create_application(["skywriter-task-008-screenshots"])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\arial.ttf")
    font_families = QFontDatabase.applicationFontFamilies(font_id)
    if not font_families:
        raise RuntimeError("Windows Arial font was unavailable for screenshot rendering")
    application.setFont(QFont(font_families[0], 10))
    adapter, last_observed = fixture_snapshot()
    snapshot = adapter.snapshot(link_connected=True)

    preflight = PreflightTelemetryWidget()
    preflight.render_snapshot(snapshot, now_s=last_observed + 0.1)
    capture(preflight, "01-preflight-fresh.png")

    flight = FlightTelemetryWidget()
    flight.render_snapshot(
        snapshot,
        now_s=last_observed + 0.1,
        route=sample_route(),
    )
    capture(flight, "02-flight-fresh.png")

    stale = FlightTelemetryWidget()
    stale.render_snapshot(snapshot, now_s=last_observed + 12.0, route=sample_route())
    capture(stale, "03-flight-stale.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
