"""Capture the five required Task 003 remediation states from the production widget."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QPoint, QRect, QSizeF
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineSettings

from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    MissionSettings,
    ProceedAction,
)
from skywriter.main import create_application
from skywriter.ui.mission_builder import (
    MissionBuilderSnapshot,
    MissionBuilderWidget,
)

OUTPUT_ROOT = Path("docs/screenshots/task-003-remediation")
SETTINGS = MissionSettings(
    takeoff_altitude_m=25.0,
    cruise_speed_m_s=6.0,
    obstacle_warning_acknowledged=True,
)
PROCEED = ProceedAction(GeoPoint(38.8890, -77.0360), 30.0)
HOLD = HoldAction(GeoPoint(38.8910, -77.0330), 32.0, 8.0)
CIRCLE = CircleAction(GeoPoint(38.8930, -77.0300), 35.0, 120.0)
LAND = LandAction(GeoPoint(38.8950, -77.0270), 10.0)


def wait_until(predicate: Callable[[], bool], timeout_ms: int = 10_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    app = create_application()
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(25)
    raise RuntimeError("timed out waiting for the production map")


def map_ready(widget: MissionBuilderWidget) -> bool:
    completed = False
    ready = False

    def receive(value: object) -> None:
        nonlocal completed, ready
        completed = True
        ready = value is True

    widget.map_canvas.page().runJavaScript(
        "Boolean(window.skywriterMapTest?.bridgeConnected())",
        receive,
    )
    wait_until(lambda: completed, 2_000)
    return ready


def rendered_state(widget: MissionBuilderWidget) -> dict[str, object]:
    completed = False
    result: object = None

    def receive(value: object) -> None:
        nonlocal completed, result
        completed = True
        result = value

    widget.map_canvas.page().runJavaScript(
        "JSON.stringify(window.skywriterMapTest.snapshot())",
        receive,
    )
    wait_until(lambda: completed, 2_000)
    if not isinstance(result, str):
        return {}
    return cast(dict[str, object], json.loads(result))


def render_map_page(widget: MissionBuilderWidget) -> QImage:
    view = widget.map_canvas
    view.settings().setAttribute(
        QWebEngineSettings.WebAttribute.PrintElementBackgrounds,
        True,
    )
    pdf_data: QByteArray | None = None

    def receive(value: QByteArray) -> None:
        nonlocal pdf_data
        pdf_data = value

    page_size = QPageSize(
        QSizeF(view.width(), view.height()),
        QPageSize.Unit.Point,
        "SKYWriter map viewport",
        QPageSize.SizeMatchPolicy.ExactMatch,
    )
    layout = QPageLayout(
        page_size,
        QPageLayout.Orientation.Landscape,
        QMarginsF(0, 0, 0, 0),
    )
    view.page().printToPdf(receive, layout)
    wait_until(lambda: pdf_data is not None, 30_000)
    if pdf_data is None or pdf_data.isEmpty():
        raise RuntimeError("WebEngine returned an empty map PDF")

    buffer = QBuffer()
    buffer.setData(pdf_data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("failed to open the in-memory map PDF")
    document = QPdfDocument()
    document.load(buffer)
    wait_until(lambda: document.status() is not QPdfDocument.Status.Loading)
    image = document.render(0, view.size())
    if image.isNull():
        raise RuntimeError("failed to render the map PDF")
    return image


def capture(
    filename: str,
    snapshot: MissionBuilderSnapshot,
    pending: GeoPoint | None = None,
) -> None:
    widget = MissionBuilderWidget()
    widget.resize(1440, 900)
    widget.move(40, 40)
    widget.show()
    widget.raise_()
    widget.activateWindow()
    wait_until(lambda: map_ready(widget))
    widget.render_snapshot(snapshot)
    if pending is not None:
        widget.begin_pending(pending)
    wait_until(
        lambda: (
            rendered_state(widget)
            == {
                "action_count": len(snapshot.actions),
                "pending": pending is not None,
                "provider": "offline",
                "rendered": True,
            }
        )
    )
    QTest.qWait(500)
    path = OUTPUT_ROOT / filename
    image = widget.grab().toImage()
    map_image = render_map_page(widget)
    map_origin = widget.map_canvas.mapTo(widget, QPoint(0, 0))
    map_rectangle = QRect(map_origin, widget.map_canvas.size())
    painter = QPainter(image)
    painter.drawImage(map_rectangle, map_image)
    painter.end()
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"failed to save {path}")
    widget.close()
    widget.deleteLater()
    QTest.qWait(100)


def warm_up_map() -> None:
    """Initialize the native WebEngine surface before capturing layout evidence."""

    widget = MissionBuilderWidget()
    widget.resize(1440, 900)
    widget.show()
    wait_until(lambda: map_ready(widget))
    widget.close()
    widget.deleteLater()
    QTest.qWait(100)


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    create_application(["skywriter-task-003-screenshots"])
    warm_up_map()
    capture("01-empty-takeoff.png", MissionBuilderSnapshot())
    capture(
        "02-pending-point.png",
        MissionBuilderSnapshot(settings=SETTINGS),
        GeoPoint(38.8890, -77.0360),
    )
    capture(
        "03-mixed-route.png",
        MissionBuilderSnapshot(settings=SETTINGS, actions=(PROCEED, HOLD, CIRCLE)),
    )
    capture(
        "04-selected-circle.png",
        MissionBuilderSnapshot(
            settings=SETTINGS,
            actions=(PROCEED, HOLD, CIRCLE),
            selected_index=2,
        ),
    )
    capture(
        "05-landed-closed.png",
        MissionBuilderSnapshot(
            settings=SETTINGS,
            actions=(PROCEED, HOLD, CIRCLE, LAND),
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
