"""End-to-end offline workflow through the production Qt composition."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, cast

from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
)

from skywriter.domain.compiled import CompiledMissionItem, MissionCommand
from skywriter.domain.mission import GeoPoint
from skywriter.main import create_application
from skywriter.ui import MainWindow, OfflineMissionWorkspace
from skywriter.ui.map import MissionMapHost

T = TypeVar("T", bound=QWidget)


def compiled_values(item: CompiledMissionItem) -> tuple[object, ...]:
    return (
        item.sequence,
        int(item.frame),
        int(item.command),
        item.current,
        item.autocontinue,
        item.param1,
        item.param2,
        item.param3,
        item.param4,
        item.latitude_e7,
        item.longitude_e7,
        item.altitude_m,
        int(item.mission_type),
    )


def child(parent: QWidget, widget_type: type[T], name: str) -> T:
    widget = parent.findChild(widget_type, name)
    assert widget is not None
    return widget


def wait_until(predicate: Callable[[], bool], *, timeout_ms: int = 8_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        create_application().processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("timed out waiting for the offline workflow")


def evaluate(host: MissionMapHost, expression: str) -> object:
    complete = False
    result: object = None

    def receive(value: object) -> None:
        nonlocal complete, result
        result = value
        complete = True

    host.page().runJavaScript(f"JSON.stringify({expression})", receive)
    wait_until(lambda: complete)
    assert isinstance(result, str)
    return json.loads(result)


def confirm_takeoff(workspace: OfflineMissionWorkspace) -> None:
    child(workspace, QLineEdit, "takeoffAltitudeInput").setText("22.5")
    child(workspace, QLineEdit, "cruiseSpeedInput").setText("8.25")
    child(workspace, QCheckBox, "obstacleWarningCheck").setChecked(True)
    child(workspace, QPushButton, "confirmTakeoffButton").click()


def confirm_point(
    workspace: OfflineMissionWorkspace,
    point: GeoPoint,
    kind: str,
    altitude: str,
    *,
    detail: str | None = None,
) -> None:
    workspace.builder.begin_pending(point)
    action_kind = child(workspace, QComboBox, "actionKindInput")
    action_kind.setCurrentIndex(action_kind.findData(kind))
    child(workspace, QLineEdit, "actionAltitudeInput").setText(altitude)
    if kind == "hold":
        assert detail is not None
        child(workspace, QLineEdit, "holdTimeInput").setText(detail)
    if kind == "circle":
        assert detail is not None
        child(workspace, QLineEdit, "circleRadiusInput").setText(detail)
    child(workspace, QPushButton, "confirmActionButton").click()


def test_complete_offline_create_edit_save_load_compile_and_map_visuals(
    tmp_path: Path,
) -> None:
    create_application(["skywriter-task-005-integration"])
    window = MainWindow()
    window.resize(1600, 980)
    window.show()
    workspace = window.mission_workspace
    host = workspace.builder.map_canvas
    wait_until(
        lambda: bool(
            cast(
                dict[str, object],
                evaluate(host, "({connected: window.skywriterMapTest?.bridgeConnected()})"),
            ).get("connected")
        )
    )

    assert not child(workspace, QPushButton, "saveMissionButton").isEnabled()
    assert not child(workspace, QPushButton, "compileMissionButton").isEnabled()
    confirm_takeoff(workspace)
    assert child(workspace, QPushButton, "saveMissionButton").isEnabled()
    assert not child(workspace, QPushButton, "compileMissionButton").isEnabled()

    workspace.builder.begin_pending(GeoPoint(51.49, -0.11))
    assert workspace.builder.pending_point is not None
    child(workspace, QPushButton, "cancelPendingButton").click()
    assert workspace.builder.pending_point is None
    assert workspace.service.snapshot.mission is not None
    assert workspace.service.snapshot.mission.actions == ()

    confirm_point(workspace, GeoPoint(51.5007292, -0.1246254), "proceed", "30")
    confirm_point(workspace, GeoPoint(51.501364, -0.14189), "hold", "35", detail="15")
    confirm_point(workspace, GeoPoint(51.503399, -0.119519), "circle", "40", detail="25")
    confirm_point(workspace, GeoPoint(51.5000001, -0.1), "land", "12")
    assert workspace.service.snapshot.can_compile
    assert not child(workspace, QPushButton, "primaryActionButton").isEnabled()

    host.bridge.receive_message(
        json.dumps(
            {
                "schema_version": 2,
                "type": "point_dragged",
                "index": 0,
                "point": {"latitude_deg": 51.5008, "longitude_deg": -0.1247},
            }
        )
    )
    wait_until(
        lambda: (
            workspace.service.snapshot.mission is not None
            and workspace.service.snapshot.mission.actions[0].point == GeoPoint(51.5008, -0.1247)
        )
    )

    action_list = child(workspace, QListWidget, "missionActionList")
    action_list.setCurrentRow(1)
    child(workspace, QLineEdit, "holdTimeInput").setText("18")
    child(workspace, QPushButton, "confirmActionButton").click()
    assert workspace.service.snapshot.mission is not None
    assert "18" in action_list.item(1).text()

    action_list.setCurrentRow(2)
    child(workspace, QLineEdit, "circleRadiusInput").setText("30")
    child(workspace, QPushButton, "confirmActionButton").click()
    assert "r 30" in action_list.item(2).text()

    action_list.setCurrentRow(3)
    child(workspace, QLineEdit, "actionAltitudeInput").setText("14")
    child(workspace, QPushButton, "confirmActionButton").click()
    assert "approach 14" in action_list.item(3).text()

    wait_until(
        lambda: (
            cast(dict[str, object], evaluate(host, "window.skywriterMapTest.snapshot()"))[
                "action_count"
            ]
            == 4
        )
    )
    visuals = cast(
        dict[str, object],
        evaluate(
            host,
            "({route: Boolean(document.querySelector('.mission-route')), "
            "circle: Boolean(document.querySelector('.circle-perimeter')), "
            "direction: Boolean(document.querySelector('.circle-direction-label')), "
            "land: Boolean(document.querySelector('.mission-marker.is-land')), "
            "pending: Boolean(document.querySelector('.is-pending'))})",
        ),
    )
    assert visuals == {
        "route": True,
        "circle": True,
        "direction": True,
        "land": True,
        "pending": False,
    }

    saved = tmp_path / "mixed-mission.json"
    workspace.save_mission(saved)
    persisted = workspace.service.snapshot.mission
    assert not workspace.service.snapshot.is_dirty
    workspace.compile_preview()
    preview = workspace.service.snapshot.compiled_preview
    assert preview is not None
    assert [item.command for item in preview.items] == [
        MissionCommand.NAV_TAKEOFF,
        MissionCommand.DO_CHANGE_SPEED,
        MissionCommand.NAV_WAYPOINT,
        MissionCommand.NAV_LOITER_TIME,
        MissionCommand.NAV_LOITER_TURNS,
        MissionCommand.NAV_WAYPOINT,
        MissionCommand.NAV_LAND,
    ]
    assert [compiled_values(item) for item in preview.items] == [
        (0, 6, 22, True, True, 0.0, 0.0, 0.0, 0.0, 0, 0, 22.5, 0),
        (1, 6, 178, False, True, 1.0, 8.25, -1.0, 0.0, 0, 0, 0.0, 0),
        (2, 6, 16, False, True, 0.0, 0.0, 0.0, 0.0, 515008000, -1247000, 30.0, 0),
        (
            3,
            6,
            19,
            False,
            True,
            18.0,
            0.0,
            0.0,
            0.0,
            515013640,
            -1418900,
            35.0,
            0,
        ),
        (
            4,
            6,
            18,
            False,
            True,
            1.0,
            0.0,
            30.0,
            0.0,
            515033990,
            -1195190,
            40.0,
            0,
        ),
        (
            5,
            6,
            16,
            False,
            True,
            0.0,
            0.0,
            0.0,
            0.0,
            515000001,
            -1000000,
            14.0,
            0,
        ),
        (
            6,
            6,
            21,
            False,
            True,
            0.0,
            0.0,
            0.0,
            0.0,
            515000001,
            -1000000,
            0.0,
            0,
        ),
    ]
    assert child(workspace, QListWidget, "compiledMissionItems").count() == 7
    assert "flight safety" in child(workspace, QLabel, "compiledPreviewDisclaimer").text()

    workspace.new_mission()
    assert workspace.service.snapshot.mission is None
    assert not child(workspace, QWidget, "compiledPreviewPanel").isVisible()
    workspace.load_mission(saved)
    assert workspace.service.snapshot.mission == persisted
    assert workspace.service.snapshot.compiled_preview is None
    assert not hasattr(workspace.service.snapshot, "verified")
    assert not hasattr(workspace.service.snapshot, "connected")

    child(workspace, QPushButton, "removeLandButton").click()
    assert workspace.service.snapshot.mission is not None
    assert not workspace.service.snapshot.mission.is_closed
    assert child(workspace, QPushButton, "primaryActionButton").isEnabled()
    assert not child(workspace, QPushButton, "compileMissionButton").isEnabled()
    confirm_point(workspace, GeoPoint(51.5000001, -0.1), "land", "12")
    assert workspace.service.snapshot.can_compile

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 1, "verified": true}', encoding="utf-8")
    before_invalid_load = workspace.service.snapshot
    workspace.load_mission(invalid)
    assert workspace.service.snapshot == before_invalid_load
    assert "failed" in child(workspace, QLabel, "offlineWorkflowStatus").text().lower()
    assert child(workspace, QLabel, "structuralValidationStatus").text()
    window.close()
