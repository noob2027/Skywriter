"""Capture Task 107 mission-map provider and coordinate states on Windows."""

from __future__ import annotations

import os
import sys
import threading
import time
from base64 import b64decode
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

if "--live-success-only" in sys.argv:
    os.environ.setdefault(
        "SKYWRITER_MAP_CACHE_ROOT",
        str(Path.cwd().parent / "task107-live-screenshot-cache"),
    )
elif "--online-only" not in sys.argv:
    os.environ.setdefault(
        "SKYWRITER_MAP_CACHE_ROOT",
        str(Path.cwd().parent / "task107-screenshot-cache"),
    )

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QPoint, QRect, QSizeF, QUrl
from PySide6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPen
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWidgets import QComboBox

from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    MissionSettings,
    ProceedAction,
)
from skywriter.main import create_application
from skywriter.ui.map import MissionMapHost, ProviderState, TileProvider
from skywriter.ui.mission_builder import MissionBuilderSnapshot, MissionBuilderWidget

OUTPUT_ROOT = Path("docs/screenshots/task-107")
_PNG_TILE = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_SNAPSHOT = MissionBuilderSnapshot(
    settings=MissionSettings(25.0, 6.0, True),
    actions=(
        ProceedAction(GeoPoint(51.5007292, -0.1246254), 30.0),
        HoldAction(GeoPoint(51.501364, -0.14189), 35.0, 15.0),
        CircleAction(GeoPoint(51.503399, -0.119519), 40.0, 120.0),
        LandAction(GeoPoint(51.5000001, -0.1), 12.0),
    ),
)


class ScreenshotTileServer(ThreadingHTTPServer):
    status_code = 200
    delay_s = 0.0
    tile_bytes = _PNG_TILE

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), ScreenshotTileHandler)

    @property
    def origin(self) -> QUrl:
        host, port = cast(tuple[str, int], self.server_address)
        return QUrl(f"http://{host}:{port}")


class ScreenshotTileHandler(BaseHTTPRequestHandler):
    server: ScreenshotTileServer

    def do_GET(self) -> None:  # noqa: N802
        if self.server.delay_s:
            time.sleep(self.server.delay_s)
        self.send_response(self.server.status_code)
        if self.server.status_code == 200:
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "public, max-age=604800")
            self.send_header("Content-Length", str(len(self.server.tile_bytes)))
            self.end_headers()
            try:
                self.wfile.write(self.server.tile_bytes)
            except OSError:
                pass
        else:
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def wait_until(predicate: Callable[[], bool], timeout_ms: int = 15_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    app = create_application()
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(25)
    raise RuntimeError("timed out waiting for the Task 107 screenshot state")


def controlled_success_tile() -> bytes:
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(QColor("#dcece8"))
    painter = QPainter(image)
    painter.setPen(QPen(QColor("#86b7ae"), 2))
    for offset in range(0, 257, 32):
        painter.drawLine(offset, 0, offset, 256)
        painter.drawLine(0, offset, 256, offset)
    painter.setPen(QColor("#176b68"))
    painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
    painter.drawText(30, 132, "CONTROLLED TILE SUCCESS")
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, b"PNG"):
        raise RuntimeError("failed to create controlled screenshot tile")
    raw = data.data()
    if not isinstance(raw, bytes):
        raise RuntimeError("controlled screenshot tile did not encode as bytes")
    return raw


def render_webengine(view: MissionMapHost) -> QImage:
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.PrintElementBackgrounds, True)
    pdf_data: QByteArray | None = None

    def receive(value: QByteArray) -> None:
        nonlocal pdf_data
        pdf_data = value

    page_size = QPageSize(
        QSizeF(view.width(), view.height()),
        QPageSize.Unit.Point,
        "SKYWriter Task 107 map viewport",
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


def render_map_page(widget: MissionBuilderWidget) -> QImage:
    return render_webengine(widget.map_canvas)


def capture(
    widget: MissionBuilderWidget,
    filename: str,
    *,
    crop_height: int | None = None,
) -> None:
    QTest.qWait(250)
    image = widget.grab().toImage()
    map_image = render_map_page(widget)
    map_view = widget.map_canvas
    map_origin = map_view.mapTo(widget, QPoint(0, 0))
    painter = QPainter(image)
    painter.drawImage(QRect(map_origin, map_view.size()), map_image)
    painter.end()
    if crop_height is not None:
        image = image.copy(QRect(0, 0, image.width(), min(crop_height, image.height())))
    path = OUTPUT_ROOT / filename
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"failed to save {path}")


def capture_host(host: MissionMapHost, filename: str) -> None:
    QTest.qWait(250)
    image = render_webengine(host)
    path = OUTPUT_ROOT / filename
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"failed to save {path}")


def make_builder(host: MissionMapHost | None = None) -> MissionBuilderWidget:
    widget = MissionBuilderWidget(map_host=host)
    widget.resize(1500, 900)
    widget.show()
    widget.render_snapshot(_SNAPSHOT)
    wait_until(lambda: widget.map_canvas.readiness is not None)
    return widget


def choose_osm(widget: MissionBuilderWidget) -> None:
    selector = widget.findChild(QComboBox, "mapProviderInput")
    if selector is None:
        raise RuntimeError("basemap selector is missing")
    selector.setCurrentIndex(selector.findData("openstreetmap"))


def close_builder(widget: MissionBuilderWidget) -> None:
    widget.close()
    widget.deleteLater()
    QTest.qWait(500)


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    application = create_application(["skywriter-task-107-screenshots"])
    application.setFont(QFont("Segoe UI", 10))

    if "--online-only" in sys.argv:
        os.environ["SKYWRITER_MAP_CACHE_ROOT"] = str(Path.cwd().parent / "task107-screenshot-cache")
        success_server = ScreenshotTileServer()
        success_server.tile_bytes = controlled_success_tile()
        success_thread = threading.Thread(target=success_server.serve_forever, daemon=True)
        success_thread.start()
        online_host = MissionMapHost(test_tile_origin=success_server.origin)
        online_host.resize(1200, 760)
        online_host.show()
        wait_until(lambda: online_host.readiness is not None)
        online_host.set_tile_provider(TileProvider.OPENSTREETMAP)
        wait_until(
            lambda: (
                online_host.provider_status.state is ProviderState.ONLINE
                and online_host.provider_status.loaded_tiles > 0
            ),
            30_000,
        )
        capture_host(online_host, "03-online-openstreetmap.png")
        online_host.close()
        success_server.shutdown()
        success_server.server_close()
        success_thread.join(timeout=2)
        return 0

    if "--failure-only" in sys.argv:
        failure_server = ScreenshotTileServer()
        failure_server.status_code = 503
        failure_thread = threading.Thread(target=failure_server.serve_forever, daemon=True)
        failure_thread.start()
        failure = make_builder(MissionMapHost(test_tile_origin=failure_server.origin))
        choose_osm(failure)
        wait_until(lambda: failure.map_canvas.provider_status.state is ProviderState.UNAVAILABLE)
        capture(failure, "04-actionable-provider-failure.png")
        close_builder(failure)
        failure_server.shutdown()
        failure_server.server_close()
        failure_thread.join(timeout=2)
        return 0

    if "--live-success-only" in sys.argv:
        online = make_builder()
        online.render_snapshot(MissionBuilderSnapshot())
        online.map_canvas.recenter(GeoPoint(0.0, 0.0), zoom=2)
        choose_osm(online)
        wait_until(
            lambda: (
                online.map_canvas.provider_status.state is ProviderState.ONLINE
                and online.map_canvas.provider_status.loaded_tiles > 0
            ),
            30_000,
        )
        capture(online, "03-online-live-openstreetmap.png")
        close_builder(online)
        return 0

    if "--controlled-success-only" in sys.argv:
        success_server = ScreenshotTileServer()
        success_server.tile_bytes = controlled_success_tile()
        success_thread = threading.Thread(target=success_server.serve_forever, daemon=True)
        success_thread.start()
        success = make_builder(MissionMapHost(test_tile_origin=success_server.origin))
        choose_osm(success)
        wait_until(lambda: success.map_canvas.provider_status.loaded_tiles > 0)
        capture(
            success,
            "03-online-controlled-tile-success.png",
            crop_height=560,
        )
        close_builder(success)
        success_server.shutdown()
        success_server.server_close()
        success_thread.join(timeout=2)
        return 0

    offline = make_builder()
    capture(offline, "01-offline-neutral-and-coordinates.png")
    close_builder(offline)

    loading_server = ScreenshotTileServer()
    loading_server.delay_s = 12.0
    loading_thread = threading.Thread(target=loading_server.serve_forever, daemon=True)
    loading_thread.start()
    loading = make_builder(
        MissionMapHost(test_tile_origin=loading_server.origin, test_tile_timeout_ms=30_000)
    )
    choose_osm(loading)
    wait_until(
        lambda: (
            loading.map_canvas.provider_status.state is ProviderState.LOADING
            and loading.map_canvas.provider_status.requested_tiles > 0
        )
    )
    capture(loading, "02-loading-openstreetmap.png")
    close_builder(loading)
    loading_server.shutdown()
    loading_server.server_close()
    loading_thread.join(timeout=2)

    online = make_builder()
    online.render_snapshot(MissionBuilderSnapshot())
    online.map_canvas.recenter(GeoPoint(0.0, 0.0), zoom=2)
    choose_osm(online)
    wait_until(
        lambda: (
            online.map_canvas.provider_status.state is ProviderState.ONLINE
            and online.map_canvas.provider_status.pending_tiles == 0
        ),
        30_000,
    )
    capture(online, "03-online-openstreetmap.png")
    close_builder(online)

    failure_server = ScreenshotTileServer()
    failure_server.status_code = 503
    failure_thread = threading.Thread(target=failure_server.serve_forever, daemon=True)
    failure_thread.start()
    failure = make_builder(MissionMapHost(test_tile_origin=failure_server.origin))
    choose_osm(failure)
    wait_until(lambda: failure.map_canvas.provider_status.state is ProviderState.UNAVAILABLE)
    capture(failure, "04-actionable-provider-failure.png")
    close_builder(failure)
    failure_server.shutdown()
    failure_server.server_close()
    failure_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
