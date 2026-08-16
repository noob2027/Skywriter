"""Production Qt WebEngine host and mounted Leaflet interaction tests."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, cast

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtTest import QTest
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
from skywriter.ui.map import MissionMapHost

T = TypeVar("T")


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


def make_host() -> MissionMapHost:
    create_application(["skywriter-map-host-test"])
    host = MissionMapHost()
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
    def rendered() -> bool:
        snapshot = cast(
            dict[str, object],
            evaluate_json(host, "window.skywriterMapTest.snapshot()"),
        )
        return (
            snapshot.get("rendered") is True
            and snapshot.get("action_count") == action_count
            and snapshot.get("pending") is pending
        )

    wait_until(rendered)


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
    assert not host.page().acceptNavigationRequest(
        QUrl("https://example.com/escape"), navigation_type, True
    )
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
    clicked: list[GeoPoint] = []
    viewport: list[object] = []
    host.map_clicked.connect(clicked.append)
    host.viewport_changed.connect(viewport.append)

    center = point_from_js(evaluate_json(host, "window.skywriterMapTest.mapCenter()"))
    target, target_center = event_target(host, center)
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target_center)
    wait_until(lambda: len(clicked) == 1)

    evaluate(host, "window.skywriterMapTest.panBy(60, 20)")
    wait_until(lambda: len(viewport) >= 1)

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
        json.dumps({"schema_version": 1, "type": "point_selected", "index": 9})
    )
    wait_until(lambda: len(rejected) == 1)
    assert "outside render snapshot" in rejected[0]
    host.close()
