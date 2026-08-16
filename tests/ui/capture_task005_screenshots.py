"""Capture the seven required Task 005 states from the production workspace."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QPoint, QRect, QSizeF
from PySide6.QtGui import QFont, QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton

from skywriter.domain.mission import GeoPoint, MissionSettings
from skywriter.main import create_application
from skywriter.ui import OfflineMissionWorkspace

OUTPUT_ROOT = Path("docs/screenshots/task-005")
SAMPLE = Path("examples/missions/mixed-offline-mission.json")


def wait_until(predicate: Callable[[], bool], timeout_ms: int = 10_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    app = create_application()
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(25)
    raise RuntimeError("timed out waiting for the production workspace")


def map_ready(workspace: OfflineMissionWorkspace) -> bool:
    completed = False
    ready = False

    def receive(value: object) -> None:
        nonlocal completed, ready
        completed = True
        ready = value is True

    workspace.builder.map_canvas.page().runJavaScript(
        "Boolean(window.skywriterMapTest?.bridgeConnected())", receive
    )
    wait_until(lambda: completed, 2_000)
    return ready


def rendered_state(workspace: OfflineMissionWorkspace) -> dict[str, object]:
    completed = False
    result: object = None

    def receive(value: object) -> None:
        nonlocal completed, result
        completed = True
        result = value

    workspace.builder.map_canvas.page().runJavaScript(
        "JSON.stringify(window.skywriterMapTest.snapshot())", receive
    )
    wait_until(lambda: completed, 2_000)
    if not isinstance(result, str):
        return {}
    return cast(dict[str, object], json.loads(result))


def render_map_page(workspace: OfflineMissionWorkspace) -> QImage:
    view = workspace.builder.map_canvas
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.PrintElementBackgrounds, True)
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
    layout = QPageLayout(page_size, QPageLayout.Orientation.Landscape, QMarginsF(0, 0, 0, 0))
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


def capture(workspace: OfflineMissionWorkspace, filename: str, *, pending: bool = False) -> None:
    mission = workspace.service.snapshot.mission
    action_count = 0 if mission is None else len(mission.actions)
    wait_until(
        lambda: (
            rendered_state(workspace)
            == {
                "action_count": action_count,
                "pending": pending,
                "provider": "offline",
                "rendered": True,
            }
        )
    )
    QTest.qWait(350)
    image = workspace.grab().toImage()
    map_image = render_map_page(workspace)
    map_view = workspace.builder.map_canvas
    map_origin = map_view.mapTo(workspace, QPoint(0, 0))
    painter = QPainter(image)
    painter.drawImage(QRect(map_origin, map_view.size()), map_image)
    painter.end()
    path = OUTPUT_ROOT / filename
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"failed to save {path}")


def add_land(workspace: OfflineMissionWorkspace) -> None:
    workspace.builder.begin_pending(GeoPoint(51.5000001, -0.1))
    kind = workspace.findChild(QComboBox, "actionKindInput")
    altitude = workspace.findChild(QLineEdit, "actionAltitudeInput")
    confirm = workspace.findChild(QPushButton, "confirmActionButton")
    assert kind is not None and altitude is not None and confirm is not None
    kind.setCurrentIndex(kind.findData("land"))
    altitude.setText("12")
    confirm.click()


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    application = create_application(["skywriter-task-005-screenshots"])
    application.setFont(QFont("Arial", 10))
    workspace = OfflineMissionWorkspace()
    workspace.resize(1660, 980)
    workspace.show()
    wait_until(lambda: map_ready(workspace))

    capture(workspace, "01-empty-mission.png")
    workspace.update_settings(MissionSettings(22.5, 8.25, True))
    workspace.builder.begin_pending(GeoPoint(51.5007292, -0.1246254))
    capture(workspace, "02-pending-point.png", pending=True)
    cancel = workspace.findChild(QPushButton, "cancelPendingButton")
    assert cancel is not None
    cancel.click()

    workspace.load_mission(SAMPLE)
    remove_land = workspace.findChild(QPushButton, "removeLandButton")
    assert remove_land is not None
    remove_land.click()
    capture(workspace, "03-mixed-route.png")
    add_land(workspace)
    workspace.compile_preview()
    capture(workspace, "04-compiled-preview.png")

    with tempfile.TemporaryDirectory(prefix="skywriter-task-005-") as temporary:
        roundtrip = Path(temporary) / "mixed-roundtrip.json"
        workspace.save_mission(roundtrip)
        expected = workspace.service.snapshot.mission
        workspace.new_mission()
        workspace.load_mission(roundtrip)
        if workspace.service.snapshot.mission != expected:
            raise RuntimeError("screenshot round-trip mission changed")
        capture(workspace, "05-saved-reloaded-equivalent.png")

        remove_land.click()
        add_land(workspace)
        capture(workspace, "06-landed-closed.png")

        invalid = Path(temporary) / "invalid-mission.json"
        invalid.write_text('{"schema_version": 1, "verified": true}', encoding="utf-8")
        workspace.load_mission(invalid)
        capture(workspace, "07-invalid-file.png")

    workspace.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
