"""Strict versioned JSON repository tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    Mission,
    MissionSettings,
    ProceedAction,
)
from skywriter.infrastructure.json_repository import (
    JsonMissionRepository,
    MissionJsonError,
    MissionRepositoryError,
    deserialize_mission,
    serialize_mission,
)


def mixed_mission() -> Mission:
    return Mission(
        schema_version=1,
        id="mission-example",
        settings=MissionSettings(20.0, 5.5, True),
        actions=(
            ProceedAction(GeoPoint(38.8895, -77.0353), 30.0),
            HoldAction(GeoPoint(38.89, -77.04), 32.0, 8.0),
            CircleAction(GeoPoint(38.9, -77.05), 35.0, 15.0),
            LandAction(GeoPoint(38.91, -77.06), 10.0),
        ),
    )


def minimal_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "draft",
        "settings": {
            "takeoff_altitude_m": 20.0,
            "cruise_speed_m_s": 5.0,
            "obstacle_warning_acknowledged": True,
        },
        "actions": [],
    }


def test_schema_is_explicit_human_readable_and_discriminated() -> None:
    document = serialize_mission(mixed_mission())
    parsed = json.loads(document)

    assert document.endswith("\n")
    assert parsed["schema_version"] == 1
    assert parsed["id"] == "mission-example"
    assert [action["type"] for action in parsed["actions"]] == [
        "proceed",
        "hold",
        "circle",
        "land",
    ]
    assert parsed["actions"][2]["turns"] == 1
    assert parsed["actions"][2]["direction"] == "clockwise"
    assert "connection" not in parsed
    assert "compiled" not in parsed
    assert "verified" not in parsed


def test_complete_and_open_draft_round_trip_equivalently() -> None:
    complete = mixed_mission()
    draft = Mission(complete.settings, actions=complete.actions[:-1], id="open-draft")

    assert deserialize_mission(serialize_mission(complete)) == complete
    assert deserialize_mission(serialize_mission(draft)) == draft


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda document: document.update({"verified": True}), "unknown field"),
        (
            lambda document: document["settings"].update({"maximum_altitude_m": 100}),
            "unknown field",
        ),
        (lambda document: document.update({"schema_version": 2}), "unsupported schema"),
        (lambda document: document.update({"actions": [{"type": "rtl"}]}), "unknown action"),
    ],
)
def test_unknown_fields_actions_and_schema_are_rejected(
    mutate: Callable[[dict[str, object]], object], message: str
) -> None:
    document = minimal_document()
    mutate(document)

    with pytest.raises(MissionJsonError, match=message):
        deserialize_mission(json.dumps(document))


def test_unknown_nested_point_field_is_rejected() -> None:
    document = minimal_document()
    document["actions"] = [
        {
            "type": "proceed",
            "point": {"latitude_deg": 1.0, "longitude_deg": 2.0, "trusted": True},
            "altitude_m": 3.0,
        }
    ]

    with pytest.raises(MissionJsonError, match="unknown field"):
        deserialize_mission(json.dumps(document))


@pytest.mark.parametrize(
    "document",
    [
        "{",
        '{"schema_version": 1, "schema_version": 1}',
        '{"schema_version": NaN}',
    ],
)
def test_malformed_duplicate_and_nonfinite_json_are_rejected(document: str) -> None:
    with pytest.raises(MissionJsonError):
        deserialize_mission(document)


def test_load_time_structural_validation_rejects_invalid_draft() -> None:
    document = minimal_document()
    document["actions"] = [
        {
            "type": "hold",
            "point": {"latitude_deg": 1.0, "longitude_deg": 2.0},
            "altitude_m": 3.0,
            "hold_time_s": 0.0,
        }
    ]

    with pytest.raises(MissionJsonError, match="valid draft"):
        deserialize_mission(json.dumps(document))


def test_repository_writes_atomically_and_loads_equivalent_mission(tmp_path: Path) -> None:
    path = tmp_path / "mission.json"
    repository = JsonMissionRepository()

    repository.save(path, mixed_mission())

    assert repository.load(path) == mixed_mission()
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_atomic_replace_preserves_existing_file_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mission.json"
    original = "previous mission\n"
    path.write_text(original, encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr("skywriter.infrastructure.json_repository.os.replace", fail_replace)

    with pytest.raises(MissionRepositoryError, match="atomically write"):
        JsonMissionRepository().save(path, mixed_mission())

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_load_does_not_return_a_partially_decoded_mission(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({**minimal_document(), "actions": [42]}), encoding="utf-8")

    with pytest.raises(MissionJsonError):
        JsonMissionRepository().load(path)
