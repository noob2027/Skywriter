"""Versioned map bridge and isolated static-asset tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from skywriter.domain.mission import GeoPoint
from skywriter.ui.map.bridge import (
    BRIDGE_SCHEMA_VERSION,
    MapBridge,
    MapBridgeError,
    MapClicked,
    PointDragged,
    PointSelected,
    RenderAction,
    RenderActionKind,
    RenderModel,
    ViewportChanged,
    encode_render_message,
    parse_map_intent,
)

SOURCE_ROOT = Path(__file__).parents[3] / "src" / "skywriter" / "ui"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "schema_version": 1,
                "type": "map_clicked",
                "point": {"latitude_deg": 38.0, "longitude_deg": -77.0},
            },
            MapClicked(GeoPoint(38.0, -77.0)),
        ),
        (
            {
                "schema_version": 1,
                "type": "point_dragged",
                "index": 2,
                "point": {"latitude_deg": 39.0, "longitude_deg": -76.0},
            },
            PointDragged(2, GeoPoint(39.0, -76.0)),
        ),
        (
            {"schema_version": 1, "type": "point_selected", "index": 0},
            PointSelected(0),
        ),
        (
            {
                "schema_version": 1,
                "type": "viewport_changed",
                "south_west": {"latitude_deg": 37.0, "longitude_deg": -78.0},
                "north_east": {"latitude_deg": 39.0, "longitude_deg": -76.0},
            },
            ViewportChanged(GeoPoint(37.0, -78.0), GeoPoint(39.0, -76.0)),
        ),
    ],
)
def test_bridge_parses_only_typed_map_intents(payload: dict[str, object], expected: object) -> None:
    assert parse_map_intent(json.dumps(payload)) == expected


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        '{"schema_version":1,"schema_version":1}',
        '{"schema_version":1,"type":"map_clicked","point":{"latitude_deg":NaN,"longitude_deg":0}}',
        json.dumps({"schema_version": 2, "type": "point_selected", "index": 0}),
        json.dumps({"schema_version": 1, "type": "point_selected", "index": 0, "extra": 1}),
        json.dumps({"schema_version": 1, "type": "unknown", "index": 0}),
        json.dumps(
            {
                "schema_version": 1,
                "type": "map_clicked",
                "point": {"latitude_deg": 91, "longitude_deg": 0},
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "type": "viewport_changed",
                "south_west": {"latitude_deg": 40, "longitude_deg": -78},
                "north_east": {"latitude_deg": 39, "longitude_deg": -76},
            }
        ),
    ],
)
def test_bridge_rejects_malformed_unknown_or_out_of_range_messages(payload: str) -> None:
    with pytest.raises(MapBridgeError):
        parse_map_intent(payload)


def test_qobject_bridge_reports_rejection_without_emitting_an_intent() -> None:
    bridge = MapBridge()
    intents: list[object] = []
    errors: list[str] = []
    bridge.intent_received.connect(intents.append)
    bridge.message_rejected.connect(errors.append)

    bridge.receive_message('{"schema_version":1,"type":"point_selected","index":0}')
    bridge.receive_message('{"schema_version":1,"type":"point_selected","index":false}')

    assert intents == [PointSelected(0)]
    assert len(errors) == 1


def test_render_message_contains_only_sanitized_action_geometry() -> None:
    message = encode_render_message(
        RenderModel(
            actions=(
                RenderAction(
                    sequence=1,
                    kind=RenderActionKind.HOLD,
                    point=GeoPoint(38.0, -77.0),
                    altitude_m=30.0,
                    hold_time_s=8.0,
                ),
                RenderAction(
                    sequence=2,
                    kind=RenderActionKind.CIRCLE,
                    point=GeoPoint(38.1, -77.1),
                    altitude_m=35.0,
                    radius_m=15.0,
                    selected=True,
                ),
            ),
            pending_point=GeoPoint(38.2, -77.2),
        )
    )
    parsed = json.loads(message)

    assert parsed["schema_version"] == BRIDGE_SCHEMA_VERSION
    assert parsed["type"] == "render_mission"
    assert parsed["actions"][1]["turns"] == 1
    assert parsed["actions"][1]["direction"] == "clockwise"
    assert set(parsed) == {"schema_version", "type", "actions", "pending_point"}


def test_render_message_rejects_action_specific_field_mismatches() -> None:
    with pytest.raises(MapBridgeError, match="positive radius"):
        encode_render_message(
            RenderModel(
                actions=(
                    RenderAction(
                        1,
                        RenderActionKind.CIRCLE,
                        GeoPoint(1.0, 2.0),
                        3.0,
                        radius_m=0.0,
                    ),
                )
            )
        )
    with pytest.raises(MapBridgeError, match="only valid"):
        encode_render_message(
            RenderModel(
                actions=(
                    RenderAction(
                        1,
                        RenderActionKind.PROCEED,
                        GeoPoint(1.0, 2.0),
                        3.0,
                        hold_time_s=4.0,
                    ),
                )
            )
        )


def test_map_assets_are_local_versioned_and_have_no_vehicle_service_imports() -> None:
    static_root = SOURCE_ROOT / "map" / "static"
    html = (static_root / "map.html").read_text(encoding="utf-8")
    script = (static_root / "map.js").read_text(encoding="utf-8")
    bridge_source = (SOURCE_ROOT / "map" / "bridge.py").read_text(encoding="utf-8")
    imports = {
        node.module
        for node in ast.walk(ast.parse(bridge_source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert 'src="map.js"' in html
    assert "SCHEMA_VERSION = 1" in script
    assert "http://" not in html + script
    assert "https://" not in html + script
    assert "skywriter.application" not in imports
    assert "skywriter.infrastructure" not in imports


def test_ui_surface_has_no_raw_or_connected_control_identifiers() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SOURCE_ROOT.rglob("*"))
        if path.suffix in {".py", ".html", ".css", ".js"}
    )
    forbidden = (
        "MAV" + "_CMD",
        "PARAM" + "_SET",
        "send" + "_command",
        "pymav" + "link",
        "R" + "TL",
    )

    assert not any(fragment in source for fragment in forbidden)
