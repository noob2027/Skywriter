"""Integrity and safety checks for the sanitized Big Bird profile record."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

ROOT = Path(__file__).parents[2]
PROFILE_ROOT = ROOT / "compatibility" / "big-bird"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return cast(dict[str, object], value)


def test_manifest_pins_the_exact_official_board_artifact_without_certifying_hardware() -> None:
    manifest = _load(PROFILE_ROOT / "manifest.json")
    decision = cast(dict[str, object], manifest["decision"])
    firmware = cast(dict[str, object], manifest["official_firmware_artifact"])

    assert decision == {
        "repository_profile_prepared": True,
        "powered_bench_observed": False,
        "hardware_certified": False,
        "arming_or_flight_authorized": False,
    }
    assert firmware["board_id"] == 1149
    assert firmware["summary"] == "MatekH7A3"
    assert firmware["git_identity"] == "3fc7011a"
    assert firmware["sha256"] == (
        "6cbeb3e1e109072963929ee582d4b0624e23acb964c581f000881488f10e0956"
    )
    assert firmware["retained_in_repository"] is False


def test_pre_change_evidence_is_sanitized_and_traceable() -> None:
    evidence = _load(PROFILE_ROOT / "evidence" / "pre-change-validation.json")

    assert evidence["passed"] is True
    assert evidence["source_line_count"] == 1269
    assert evidence["parameter_count"] == 1269
    assert evidence["source_sha256"] == (
        "ef8f97a40a677085842717f43a9d932802aa91f819f7caa34d8df17485161020"
    )
    assert "parameters" not in evidence
    assert cast(list[dict[str, object]], evidence["findings"])[0]["code"] == (
        "existing_mission_requires_replacement"
    )


def test_manifest_preserves_serial2_mapping_and_only_accepted_stream_changes() -> None:
    manifest = _load(PROFILE_ROOT / "manifest.json")
    profile = cast(dict[str, object], manifest["vehicle_profile"])
    serial = cast(dict[str, object], profile["serial_assignments"])
    configured = cast(dict[str, object], manifest["accepted_operator_configuration"])

    assert "TX2/RX2" in cast(str, serial["SERIAL2"])
    assert "SiK MAVLink2" in cast(str, serial["SERIAL2"])
    assert "GPS" in cast(str, serial["SERIAL3"])
    assert configured["stream_rates"] == {
        "SR2_EXT_STAT": 2,
        "SR2_POSITION": 2,
        "SR2_EXTRA3": 1,
    }
    assert configured["expected_skywriter_inputs"] == {
        "SR2_EXT_STAT": ["SYS_STATUS", "MISSION_CURRENT", "GPS_RAW_INT"],
        "SR2_POSITION": ["GLOBAL_POSITION_INT"],
        "SR2_EXTRA3": ["BATTERY_STATUS", "EKF_STATUS_REPORT"],
    }
    assert configured["all_other_SR2_groups"] == 0


def test_record_contains_no_uid_or_personal_desktop_path_and_claims_no_live_result() -> None:
    retained_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(PROFILE_ROOT.rglob("*"))
        if path.is_file()
    )
    manifest = _load(PROFILE_ROOT / "manifest.json")
    safety = cast(dict[str, object], manifest["safety"])

    assert "Desktop" not in retained_text
    assert "DEVICE_ID" not in retained_text
    assert "20204232" not in retained_text
    assert safety["raw_board_uid_retained_or_published"] is False
    assert safety["real_hardware_operated_for_task_105"] is False
    assert safety["powered_bench_result_claimed"] is False
    assert safety["arm_requested"] is False
    assert safety["motors_commanded"] is False
    assert safety["flight_performed"] is False
    assert safety["skywriter_parameter_writes_added"] is False
    assert safety["skywriter_stream_requests_added"] is False
