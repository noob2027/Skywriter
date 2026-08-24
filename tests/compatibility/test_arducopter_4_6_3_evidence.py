"""Integrity and acceptance checks for the retained Task 005A evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

COMPATIBILITY_ROOT = Path(__file__).parents[2] / "compatibility" / "arducopter-4.6.3"
EVIDENCE_ROOT = COMPATIBILITY_ROOT / "evidence"


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(dict[str, object], value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_retained_evidence_matches_hash_ledger() -> None:
    entries = (EVIDENCE_ROOT / "SHA256SUMS").read_text(encoding="utf-8").splitlines()

    assert len(entries) == 7
    for entry in entries:
        expected, relative_path = entry.split("  ./", maxsplit=1)
        evidence_path = EVIDENCE_ROOT / relative_path
        assert evidence_path.is_file(), relative_path
        assert _sha256(evidence_path) == expected


def test_manifest_keeps_tag_and_published_sitl_identities_distinct() -> None:
    manifest = _load_json(COMPATIBILITY_ROOT / "manifest.json")
    candidate = cast(dict[str, object], manifest["candidate"])
    recommendation = cast(dict[str, object], manifest["recommendation"])

    assert candidate["tag_commit"] == "92b0cd788ec29406f26c6f9c31d5ceedbd1cc538"
    assert candidate["published_sitl_commit"] == "3fc7011a7d3dc047cbb17d8bd98ee94577d144c6"
    assert recommendation == {
        "decision": "reject",
        "tasks_006_through_008_blocked": True,
        "reason": (
            "Stock ArduCopter 4.6.3 does not preserve the accepted compiler fixture "
            "field-for-field; sequence zero is consumed as home and the takeoff item "
            "is lost on readback."
        ),
    }


def test_probe_is_isolated_and_fails_the_compiler_compatibility_decision() -> None:
    result = _load_json(EVIDENCE_ROOT / "probe-result.json")
    safety = cast(dict[str, object], result["safety"])
    comparison = cast(dict[str, object], result["compiler_comparison"])

    assert result["status"] == "passed"
    assert safety == {
        "arm_requested": False,
        "mission_target": "ephemeral stock SITL only",
        "mode_changed": False,
        "parameter_writes": 0,
        "real_hardware": False,
    }
    assert comparison["expected_count"] == 7
    assert comparison["readback_count"] == 7
    assert comparison["all_fields_match_after_float32_normalization"] is False


def test_all_whitelisted_items_crossed_stock_sitl_and_mismatches_are_retained() -> None:
    result = _load_json(EVIDENCE_ROOT / "probe-result.json")
    upload = cast(dict[str, object], result["mission_upload"])
    mission_after = cast(dict[str, object], result["mission_after_upload"])
    readback_items = cast(list[dict[str, object]], mission_after["items"])

    records = [
        json.loads(line)
        for line in (EVIDENCE_ROOT / "mavlink-messages.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    sent_items = [
        cast(dict[str, object], record["fields"])
        for record in records
        if record["direction"] == "probe_to_vehicle"
        and record["message_type"] == "MISSION_ITEM_INT"
    ]

    assert [item["command"] for item in sent_items] == [22, 178, 16, 19, 18, 16, 21]
    assert all(item["frame"] == 6 for item in sent_items)
    assert upload["result_name"] == "MAV_MISSION_ACCEPTED"
    assert upload["request_messages"] == ["MISSION_REQUEST"] * 7
    assert upload["sent_protocol"] == "MISSION_ITEM_INT"

    assert readback_items[0] == {
        "altitude_m": 15.09999942779541,
        "autocontinue": True,
        "command": 16,
        "current": False,
        "frame": 0,
        "latitude_e7": 515007291,
        "longitude_e7": -1246254,
        "mission_type": 0,
        "param1": 0.0,
        "param2": 0.0,
        "param3": 0.0,
        "param4": 0.0,
        "sequence": 0,
    }
    assert readback_items[3]["param3"] == 1.0
    assert readback_items[6]["param4"] == 1.0
    assert all(item["current"] is False for item in readback_items)

    relevant_received = [
        cast(dict[str, object], record["fields"])
        for record in records
        if record["direction"] == "vehicle_to_probe"
        and record["message_type"]
        in {"HEARTBEAT", "MISSION_REQUEST", "MISSION_ACK", "MISSION_ITEM_INT"}
    ]
    assert relevant_received
    assert all(
        cast(dict[str, object], fields["_wire"])["magic"] == 253 for fields in relevant_received
    )


def test_native_command_acknowledgements_are_evidence_not_controls() -> None:
    result = _load_json(EVIDENCE_ROOT / "probe-result.json")
    prearm = cast(dict[str, object], result["prearm_request_ack"])
    pause = cast(dict[str, object], result["pause_ack"])
    resume = cast(dict[str, object], result["continue_ack"])

    assert (prearm["command"], prearm["result_name"]) == (401, "MAV_RESULT_ACCEPTED")
    assert (pause["command"], pause["result_name"]) == (193, "MAV_RESULT_FAILED")
    assert (resume["command"], resume["result_name"]) == (193, "MAV_RESULT_FAILED")
