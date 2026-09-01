"""Production Qt WebEngine host and mounted Leaflet interaction tests."""

from __future__ import annotations

import json
import threading
import time
from base64 import b64decode
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypeVar, cast

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication, QWidget

from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    ProceedAction,
)
from skywriter.main import create_application
from skywriter.ui.map import MissionMapHost, ProviderState, TileProvider
from skywriter.ui.map.bridge import (
    BRIDGE_SCHEMA_VERSION,
    ProviderStatusChanged,
    ViewportChanged,
)

T = TypeVar("T")
_PNG_TILE = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ControlledTileServer(ThreadingHTTPServer):
    status_code = 200
    request_paths: list[str]
    user_agents: list[str]

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), ControlledTileHandler)
        self.request_paths = []
        self.user_agents = []

    @property
    def origin(self) -> QUrl:
        host, port = cast(tuple[str, int], self.server_address)
        return QUrl(f"http://{host}:{port}")


class ControlledTileHandler(BaseHTTPRequestHandler):
    server: ControlledTileServer

    def do_GET(self) -> None:  # noqa: N802
        self.server.request_paths.append(self.path)
        self.server.user_agents.append(self.headers.get("User-Agent", ""))
        self.send_response(self.server.status_code)
        if self.server.status_code == 200:
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "public, max-age=604800")
            self.send_header("Content-Length", str(len(_PNG_TILE)))
            self.end_headers()
            self.wfile.write(_PNG_TILE)
        else:
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def wait_until(predicate: Callable[[], bool], *, timeout_ms: int = 8_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        create_application().processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("timed out waiting for the WebEngine map")


def evaluate(host: MissionMapHost, script: str, *, timeout_ms: int = 5_000) -> object:
    completed = False
    result: object = None

    def receive(value: object) -> None:
        nonlocal completed, result
        completed = True
        result = value

    host.page().runJavaScript(script, receive)
    wait_until(lambda: completed, timeout_ms=timeout_ms)
    return result


def evaluate_json(host: MissionMapHost, expression: str) -> object:
    value = evaluate(host, f"JSON.stringify({expression})")
    assert isinstance(value, str)
    return json.loads(value)


def make_host(test_tile_origin: QUrl | None = None) -> MissionMapHost:
    create_application(["skywriter-map-host-test"])
    host = MissionMapHost(test_tile_origin=test_tile_origin)
    host.resize(900, 620)
    host.show()
    wait_until(
        lambda: bool(
            evaluate(
                host,
                "Boolean(window.skywriterMapTest?.bridgeConnected())",
            )
        )
    )
    return host


def wait_for_render(host: MissionMapHost, action_count: int, *, pending: bool) -> None:
    latest: dict[str, object] = {}

    def rendered() -> bool:
        nonlocal latest
        latest = cast(dict[str, object], evaluate_json(host, "window.skywriterMapTest.snapshot()"))
        render_sequence = latest.get("render_sequence")
        return (
            latest.get("rendered") is True
            and latest.get("bridge_connected") is True
            and latest.get("action_count") == action_count
            and latest.get("pending") is pending
            and isinstance(render_sequence, int)
            and not isinstance(render_sequence, bool)
            and render_sequence >= 1
            and latest.get("settled_render_sequence") == render_sequence
            and latest.get("viewport_intent_suppressed") is False
            and latest.get("pending_viewport_pan") is False
            and cast(float, latest.get("container_width", 0)) > 0
            and cast(float, latest.get("container_height", 0)) > 0
        )

    try:
        wait_until(rendered)
    except AssertionError as error:
        raise AssertionError(
            f"Leaflet render did not reach a settled mounted state: {latest}"
        ) from error


def wait_for_viewport_pan_completion(
    host: MissionMapHost,
    request_id: int,
) -> dict[str, object]:
    completion: object = None

    def bridge_round_trip_completed() -> bool:
        nonlocal completion
        completion = evaluate_json(
            host,
            f"window.skywriterMapTest.viewportPanCompletion({request_id})",
        )
        return isinstance(completion, dict)

    try:
        wait_until(bridge_round_trip_completed)
    except AssertionError as error:
        status = evaluate_json(
            host,
            f"window.skywriterMapTest.viewportPanStatus({request_id})",
        )
        raise AssertionError(
            f"viewport pan {request_id} did not complete its mounted round trip; "
            f"last JavaScript layer status: {status}"
        ) from error
    return cast(dict[str, object], completion)


def point_from_js(value: object) -> QPoint:
    point = cast(dict[str, float], value)
    return QPoint(round(point["x"]), round(point["y"]))


def event_target(host: MissionMapHost, point: QPoint) -> tuple[QWidget, QPoint]:
    target = host.focusProxy() or host
    return target, target.mapFrom(host, point)


def test_production_host_loads_packaged_leaflet_and_blocks_navigation() -> None:
    host = make_host()

    details = cast(
        dict[str, object],
        evaluate_json(
            host,
            "({"
            "leaflet: document.getElementById('mission-map').dataset.leafletVersion,"
            "ready: document.getElementById('mission-map').dataset.ready,"
            "scripts: Array.from(document.scripts).map((script) => script.src)"
            "})",
        ),
    )
    scripts = cast(list[str], details["scripts"])
    navigation_type = QWebEnginePage.NavigationType.NavigationTypeLinkClicked

    assert details["leaflet"] == "1.9.4"
    assert details["ready"] == "true"
    assert any("vendor/leaflet-1.9.4/leaflet.js" in source for source in scripts)
    assert not any(source.startswith("http") for source in scripts)
    assert host.url().isLocalFile()
    assert Path(host.url().toLocalFile()).parent == host.static_root
    viewport = cast(
        dict[str, object], evaluate_json(host, "window.skywriterMapTest.geographicViewport()")
    )
    assert viewport == {
        "center": {"latitude_deg": 0, "longitude_deg": 0},
        "zoom": 2,
    }
    assert not host.page().acceptNavigationRequest(
        QUrl("https://example.com/escape"), navigation_type, True
    )
    host.close()


def test_leaflet_tracks_a_resized_webengine_viewport() -> None:
    host = make_host()
    host.resize(1200, 760)

    latest: dict[str, object] = {}

    def leaflet_matches_container() -> bool:
        nonlocal latest
        latest = cast(
            dict[str, object],
            evaluate_json(host, "window.skywriterMapTest.snapshot()"),
        )
        return (
            abs(cast(float, latest["leaflet_width"]) - cast(float, latest["container_width"])) <= 2
            and abs(cast(float, latest["leaflet_height"]) - cast(float, latest["container_height"]))
            <= 2
            and cast(float, latest["container_width"]) > 900
            and cast(float, latest["container_height"]) > 620
        )

    try:
        wait_until(leaflet_matches_container)
    except AssertionError as error:
        raise AssertionError(f"Leaflet retained a stale viewport after resize: {latest}") from error
    finally:
        host.close()


def test_controlled_tiles_prove_loading_online_offline_and_retry_without_public_osm() -> None:
    server = ControlledTileServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = make_host(server.origin)
    try:
        QTest.qWait(150)
        assert server.request_paths == []
        assert host.provider_status.state is ProviderState.OFFLINE
        host.set_tile_provider(TileProvider.OPENSTREETMAP)
        wait_until(
            lambda: (
                host.provider_status.state is ProviderState.ONLINE
                and host.provider_status.pending_tiles == 0
            )
        )
        first = host.provider_status

        assert first.attempt_id == 1
        assert first.requested_tiles > 0
        assert first.loaded_tiles == first.requested_tiles
        assert first.error_tiles == 0
        assert server.request_paths
        assert all(path.count("/") == 3 and path.endswith(".png") for path in server.request_paths)
        assert set(server.user_agents) == {
            "SKYWriter/0.1.1 (+https://github.com/noob2027/Skywriter)"
        }
        assert "OpenStreetMap contributors" in cast(
            str,
            evaluate(host, "document.querySelector('.leaflet-control-attribution').textContent"),
        )

        host.resize(1200, 760)
        wait_until(
            lambda: (
                host.provider_status.state is ProviderState.ONLINE
                and host.provider_status.pending_tiles == 0
                and host.provider_status.loaded_tiles + host.provider_status.error_tiles
                == host.provider_status.requested_tiles
            )
        )

        host.retry_tiles()
        wait_until(
            lambda: (
                host.provider_status.attempt_id == 2
                and host.provider_status.state is ProviderState.ONLINE
            )
        )
        assert host.provider_status.loaded_tiles > 0

        host.set_tile_provider(TileProvider.OFFLINE)
        wait_until(lambda: host.provider_status.state is ProviderState.OFFLINE)
        assert host.provider_status == ProviderStatusChanged(
            TileProvider.OFFLINE, 0, ProviderState.OFFLINE, 0, 0, 0, 0
        )
        assert evaluate(host, "document.querySelectorAll('.leaflet-tile').length") == 0
    finally:
        host.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_controlled_tile_failure_is_actionable_and_retry_can_recover() -> None:
    server = ControlledTileServer()
    server.status_code = 503
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = make_host(server.origin)
    try:
        host.set_tile_provider(TileProvider.OPENSTREETMAP)
        wait_until(lambda: host.provider_status.state is ProviderState.UNAVAILABLE)
        failed = host.provider_status
        assert failed.attempt_id == 1
        assert failed.loaded_tiles == 0
        assert failed.error_tiles > 0

        server.status_code = 200
        host.retry_tiles()
        wait_until(
            lambda: (
                host.provider_status.attempt_id == 2
                and host.provider_status.state is ProviderState.ONLINE
            )
        )
        assert host.provider_status.loaded_tiles > 0
    finally:
        host.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_operator_recenter_moves_neutral_viewport_without_creating_a_mission_point() -> None:
    host = make_host()
    clicked: list[GeoPoint] = []
    host.map_clicked.connect(clicked.append)

    host.recenter(GeoPoint(-33.8688, 151.2093))
    wait_until(
        lambda: (
            cast(
                dict[str, object],
                evaluate_json(host, "window.skywriterMapTest.geographicViewport()"),
            )["zoom"]
            == 15
        )
    )
    viewport = cast(
        dict[str, object], evaluate_json(host, "window.skywriterMapTest.geographicViewport()")
    )
    center = cast(dict[str, float], viewport["center"])
    assert center["latitude_deg"] == pytest.approx(-33.8688)
    assert center["longitude_deg"] == pytest.approx(151.2093)
    assert clicked == []
    host.close()


def test_python_render_reaches_leaflet_with_geographic_visual_states() -> None:
    host = make_host()
    actions = (
        ProceedAction(GeoPoint(38.8890, -77.0360), 30.0),
        HoldAction(GeoPoint(38.8910, -77.0330), 32.0, 8.0),
        CircleAction(GeoPoint(38.8930, -77.0300), 35.0, 120.0),
    )
    host.render_mission(actions, GeoPoint(38.8950, -77.0270), 2)
    wait_for_render(host, 3, pending=True)

    details = cast(
        dict[str, object],
        evaluate_json(
            host,
            "({"
            "circleRadius: document.querySelector('.circle-perimeter')?.dataset.radiusM,"
            "direction: Boolean(document.querySelector('.circle-direction-label')),"
            "labels: Array.from(document.querySelectorAll('.mission-label'))"
            ".map((node) => node.textContent),"
            "pending: Boolean(document.querySelector('.is-pending')),"
            "radiusLine: Boolean(document.querySelector('.circle-radius-line')),"
            "route: Boolean(document.querySelector('.mission-route')),"
            "selected: Boolean(document.querySelector('.mission-marker.is-selected'))"
            "})",
        ),
    )

    assert details["circleRadius"] == "120"
    assert details["direction"] is True
    assert details["pending"] is True
    assert details["radiusLine"] is True
    assert details["route"] is True
    assert details["selected"] is True
    assert any("Above Home" in label for label in cast(list[str], details["labels"]))
    assert any("Hold 8 s" in label for label in cast(list[str], details["labels"]))

    before = cast(dict[str, object], evaluate_json(host, "window.skywriterMapTest.snapshot()"))
    evaluate(
        host,
        "window.skywriterMapTest.acceptRender(JSON.stringify({"
        "schema_version: 2, type: 'render_mission', extra: true"
        "}))",
    )
    after = cast(dict[str, object], evaluate_json(host, "window.skywriterMapTest.snapshot()"))
    assert after == before
    assert "schema mismatch" in cast(
        str, evaluate(host, "document.getElementById('status').textContent")
    )

    host.render_mission((*actions, LandAction(GeoPoint(38.8950, -77.0270), 10.0)), None, 3)
    wait_for_render(host, 4, pending=False)
    assert evaluate(host, "Boolean(document.querySelector('.mission-marker.is-land'))") is True
    host.close()


def test_javascript_map_click_and_viewport_intents_cross_mounted_channel() -> None:
    host = make_host()
    wait_for_render(host, 0, pending=False)
    clicked: list[GeoPoint] = []
    viewport = QSignalSpy(host.viewport_changed)
    host.map_clicked.connect(clicked.append)

    center = point_from_js(evaluate_json(host, "window.skywriterMapTest.mapCenter()"))
    target, target_center = event_target(host, center)
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target_center)
    wait_until(lambda: len(clicked) == 1)

    assert viewport.count() == 0
    request_value = evaluate(host, "window.skywriterMapTest.requestViewportPan(60, 20)")
    assert isinstance(request_value, float) and request_value.is_integer()
    request_id = int(request_value)
    # This explicit acknowledgement protects the asynchronous Leaflet/QWebChannel
    # boundary. JavaScript records it only in the real channel's return callback,
    # after Python validates the payload and synchronously emits the host signal.
    completion = wait_for_viewport_pan_completion(host, request_id)
    status = cast(
        dict[str, object],
        evaluate_json(host, f"window.skywriterMapTest.viewportPanStatus({request_id})"),
    )
    assert completion["bridge_result"] == "accepted", (
        "Leaflet moveend reached Python, but strict bridge validation rejected the payload"
    )
    assert status["phase"] == "bridge_accepted"
    assert viewport.count() == 1, (
        "Python returned accepted without synchronously emitting host.viewport_changed"
    )

    before = cast(dict[str, float], completion["before"])
    after = cast(dict[str, float], completion["after"])
    assert before != after
    assert cast(int, completion["moveend_sequence"]) == (
        cast(int, completion["moveend_sequence_before"]) + 1
    ), "the correlated Leaflet moveend did not run exactly once"
    assert completion["render_sequence"] == status["render_sequence"]
    assert completion["moveend_sequence"] == status["moveend_sequence"]

    viewport_event = cast(ViewportChanged, viewport.at(0)[0])
    assert (
        viewport_event.south_west.latitude_deg
        <= after["latitude_deg"]
        <= viewport_event.north_east.latitude_deg
    )
    assert (
        viewport_event.south_west.longitude_deg
        <= after["longitude_deg"]
        <= viewport_event.north_east.longitude_deg
    )

    assert -90 <= clicked[0].latitude_deg <= 90
    assert -180 <= clicked[0].longitude_deg <= 180
    host.close()


def test_real_marker_click_and_drag_obey_platform_threshold() -> None:
    host = make_host()
    action = ProceedAction(GeoPoint(38.8895, -77.0353), 30.0)
    host.render_mission((action,), None, None)
    wait_for_render(host, 1, pending=False)
    selected: list[int] = []
    dragged: list[tuple[int, GeoPoint]] = []
    map_clicked: list[GeoPoint] = []
    host.point_selected.connect(selected.append)
    host.point_dragged.connect(lambda index, point: dragged.append((index, point)))
    host.map_clicked.connect(map_clicked.append)
    marker = point_from_js(evaluate_json(host, "window.skywriterMapTest.markerCenter(0)"))
    target, target_marker = event_target(host, marker)

    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=target_marker)
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=target_marker)
    wait_until(lambda: selected == [0])
    assert dragged == []
    assert map_clicked == []

    threshold = max(QApplication.startDragDistance(), 1)
    selected.clear()
    almost = target_marker + QPoint(max(threshold - 1, 1), 0)
    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=target_marker)
    QTest.mouseMove(target, almost, delay=20)
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=almost)
    wait_until(lambda: selected == [0])
    assert dragged == []

    selected.clear()
    destination = target_marker + QPoint(threshold + 36, threshold + 24)
    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=target_marker)
    QTest.mouseMove(target, destination, delay=30)
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=destination)
    wait_until(lambda: len(dragged) == 1)
    QTest.qWait(100)
    assert len(dragged) == 1
    assert dragged[0][0] == 0
    assert dragged[0][1] != action.point
    assert selected == []

    host.close()


def test_drag_released_outside_map_and_invalid_indices_fail_closed() -> None:
    host = make_host()
    action = ProceedAction(GeoPoint(38.8895, -77.0353), 30.0)
    host.render_mission((action,), None, None)
    wait_for_render(host, 1, pending=False)
    dragged: list[tuple[int, GeoPoint]] = []
    rejected: list[str] = []
    host.point_dragged.connect(lambda index, point: dragged.append((index, point)))
    host.bridge_message_rejected.connect(rejected.append)
    marker = point_from_js(evaluate_json(host, "window.skywriterMapTest.markerCenter(0)"))
    target, target_marker = event_target(host, marker)

    outside = target.mapFrom(host, QPoint(2, 2))
    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=target_marker)
    QTest.mouseMove(target, outside, delay=30)
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=outside)
    QTest.qWait(150)
    assert dragged == []

    QTest.mouseMove(target, target_marker, delay=20)
    destination = target_marker + QPoint(QApplication.startDragDistance() + 30, 20)
    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=target_marker)
    QTest.mouseMove(target, destination, delay=30)
    wait_until(
        lambda: (
            point_from_js(evaluate_json(host, "window.skywriterMapTest.markerCenter(0)")) != marker
        )
    )
    evaluate(host, "window.dispatchEvent(new Event('blur'))")
    wait_until(
        lambda: (
            point_from_js(evaluate_json(host, "window.skywriterMapTest.markerCenter(0)")) == marker
        )
    )
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=destination)
    QTest.qWait(150)
    assert dragged == []

    host.bridge.receive_message(
        json.dumps(
            {
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "type": "point_selected",
                "index": 9,
            }
        )
    )
    wait_until(lambda: len(rejected) == 1)
    assert "outside render snapshot" in rejected[0]
    host.close()
