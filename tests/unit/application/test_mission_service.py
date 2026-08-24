"""Offline application use-case and lifecycle tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from skywriter.application import OfflineMissionError, OfflineMissionService
from skywriter.domain.compiled import MissionCommand
from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    MissionSettings,
    ProceedAction,
)
from skywriter.infrastructure.json_repository import JsonMissionRepository, MissionJsonError


def complete_service() -> OfflineMissionService:
    service = OfflineMissionService(JsonMissionRepository())
    service.update_settings(MissionSettings(22.5, 8.25, True))
    service.append_action(ProceedAction(GeoPoint(51.5007292, -0.1246254), 30.0))
    service.append_action(HoldAction(GeoPoint(51.501364, -0.14189), 35.0, 15.0))
    service.append_action(CircleAction(GeoPoint(51.503399, -0.119519), 40.0, 25.0))
    service.append_action(LandAction(GeoPoint(51.5000001, -0.1), 12.0))
    return service


def test_complete_create_save_load_and_compile_lifecycle(tmp_path: Path) -> None:
    service = complete_service()
    assert service.snapshot.can_save
    assert service.snapshot.can_compile
    assert service.snapshot.is_dirty

    service.compile_preview()
    preview = service.snapshot.compiled_preview
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
    assert service.snapshot.compiled_revision == service.snapshot.revision

    destination = tmp_path / "mission.json"
    service.save(destination)
    saved_mission = service.snapshot.mission
    assert not service.snapshot.is_dirty

    service.new_mission()
    assert service.snapshot.mission is None
    assert service.snapshot.compiled_preview is None
    service.load(destination)
    assert service.snapshot.mission == saved_mission
    assert not service.snapshot.is_dirty
    assert service.snapshot.compiled_preview is None
    assert not hasattr(service.snapshot, "verified")
    assert not hasattr(service.snapshot, "connected")


@pytest.mark.parametrize(
    "edit",
    [
        lambda service: service.update_settings(MissionSettings(25.0, 7.0, True)),
        lambda service: service.replace_action(0, ProceedAction(GeoPoint(51.51, -0.13), 31.0)),
        lambda service: service.delete_action(0),
        lambda service: service.move_action(1, GeoPoint(51.52, -0.14)),
        lambda service: service.clear_actions(),
        lambda service: service.remove_land(),
    ],
)
def test_every_possible_post_compile_edit_invalidates_preview_and_version(edit: object) -> None:
    service = complete_service()
    service.compile_preview()
    previous_revision = service.snapshot.revision

    assert callable(edit)
    edit(service)

    assert service.snapshot.revision == previous_revision + 1
    assert service.snapshot.compiled_preview is None
    assert service.snapshot.compiled_revision is None


def test_selection_does_not_invalidate_but_closed_route_edits_fail_closed() -> None:
    service = complete_service()
    service.compile_preview()
    preview = service.snapshot.compiled_preview
    revision = service.snapshot.revision

    service.select_action(1)
    assert service.snapshot.compiled_preview is preview
    assert service.snapshot.revision == revision

    with pytest.raises(OfflineMissionError, match="Remove Land|remove Land"):
        service.append_action(ProceedAction(GeoPoint(1.0, 2.0), 3.0))
    with pytest.raises(OfflineMissionError, match="Remove Land|remove Land"):
        service.undo()
    with pytest.raises(OfflineMissionError, match="Remove Land|remove Land"):
        service.delete_action(3)

    service.remove_land()
    assert service.snapshot.mission is not None
    assert not service.snapshot.mission.is_closed
    service.append_action(LandAction(GeoPoint(51.5000001, -0.1), 12.0))
    assert service.snapshot.mission is not None
    assert service.snapshot.mission.is_closed


def test_failed_load_preserves_current_mission_and_reports_strict_json(tmp_path: Path) -> None:
    service = complete_service()
    previous = service.snapshot
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 1, "verified": true}', encoding="utf-8")

    with pytest.raises(MissionJsonError):
        service.load(invalid)

    assert service.snapshot == previous


def test_draft_gates_save_but_requires_land_for_compile() -> None:
    service = OfflineMissionService(JsonMissionRepository())
    assert not service.snapshot.can_save
    assert not service.snapshot.can_compile
    service.update_settings(MissionSettings(20.0, 6.0, True))
    service.append_action(ProceedAction(GeoPoint(38.0, -77.0), 30.0))

    assert service.snapshot.can_save
    assert not service.snapshot.can_compile
    assert [finding.code.value for finding in service.snapshot.complete_findings] == [
        "land_required"
    ]
