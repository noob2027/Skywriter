"""Offline Big Bird profile validation tests."""

from __future__ import annotations

from skywriter.compatibility.big_bird import (
    BigBirdProfileStage,
    validate_big_bird_parameter_export,
    validation_to_document,
)


def _export(*, bench_ready: bool = False, mission_total: int = 14) -> str:
    values: dict[str, str] = {
        "SYSID_THISMAV": "20",
        "SYSID_MYGCS": "255",
        "FRAME_CLASS": "1",
        "FRAME_TYPE": "1",
        "SERIAL0_PROTOCOL": "2",
        "SERIAL0_BAUD": "115",
        "SERIAL1_PROTOCOL": "23",
        "SERIAL1_BAUD": "115",
        "SERIAL1_OPTIONS": "7",
        "SERIAL2_PROTOCOL": "2",
        "SERIAL2_BAUD": "57",
        "SERIAL2_OPTIONS": "0",
        "SERIAL3_PROTOCOL": "5",
        "SERIAL3_BAUD": "115",
        "SERIAL3_OPTIONS": "0",
        "GPS1_TYPE": "1",
        "GPS1_RATE_MS": "200",
        "BARO_PRIMARY": "0",
        "BARO1_DEVID": "816641",
        "COMPASS_DEV_ID": "855297",
        "COMPASS_DEV_ID2": "0",
        "COMPASS_DEV_ID3": "0",
        "COMPASS_DEV_ID4": "0",
        "COMPASS_DEV_ID5": "0",
        "COMPASS_DEV_ID6": "0",
        "COMPASS_DEV_ID7": "0",
        "COMPASS_DEV_ID8": "0",
        "COMPASS_EXTERNAL": "1",
        "COMPASS_USE": "1",
        "COMPASS_OFS_X": "-39.14638",
        "COMPASS_OFS_Y": "56.92298",
        "COMPASS_OFS_Z": "10.15593",
        "ARMING_CHECK": "4366",
        "BATT_ARM_VOLT": "19.7",
        "MIS_TOTAL": str(mission_total),
        "SR2_ADSB": "0",
        "SR2_EXT_STAT": "2" if bench_ready else "0",
        "SR2_EXTRA1": "0",
        "SR2_EXTRA2": "0",
        "SR2_EXTRA3": "1" if bench_ready else "0",
        "SR2_PARAMS": "0",
        "SR2_POSITION": "2" if bench_ready else "0",
        "SR2_RAW_CTRL": "0",
        "SR2_RAW_SENS": "0",
        "SR2_RC_CHAN": "0",
    }
    return "\n".join(f"{name},{value}" for name, value in values.items())


def test_reviewed_pre_change_export_passes_but_preserves_mission_warning() -> None:
    result = validate_big_bird_parameter_export(_export(), stage=BigBirdProfileStage.PRE_CHANGE)

    assert result.passed
    assert result.mission_total == 14
    assert [finding.code for finding in result.findings] == [
        "existing_mission_requires_replacement"
    ]


def test_bench_ready_export_requires_only_the_three_accepted_stream_rates() -> None:
    result = validate_big_bird_parameter_export(
        _export(bench_ready=True, mission_total=8),
        stage=BigBirdProfileStage.BENCH_READY,
        expected_mission_count=8,
    )

    assert result.passed
    assert result.findings == ()


def test_bench_ready_export_does_not_treat_mission_count_as_semantic_verification() -> None:
    result = validate_big_bird_parameter_export(
        _export(bench_ready=True, mission_total=8),
        stage=BigBirdProfileStage.BENCH_READY,
    )

    assert result.passed
    assert result.findings[0].code == "mission_semantics_not_proven"


def test_wrong_serial_mapping_fails_closed() -> None:
    text = _export().replace("SERIAL2_PROTOCOL,2", "SERIAL2_PROTOCOL,5")

    result = validate_big_bird_parameter_export(text, stage=BigBirdProfileStage.PRE_CHANGE)

    assert not result.passed
    assert any(
        finding.parameter == "SERIAL2_PROTOCOL"
        and finding.expected == "2"
        and finding.actual == "5"
        for finding in result.findings
    )


def test_unaccepted_stream_group_or_relaxed_arming_check_fails_closed() -> None:
    text = (
        _export(bench_ready=True)
        .replace("SR2_EXTRA1,0", "SR2_EXTRA1,2")
        .replace("ARMING_CHECK,4366", "ARMING_CHECK,0")
    )

    result = validate_big_bird_parameter_export(text, stage=BigBirdProfileStage.BENCH_READY)

    assert not result.passed
    assert {finding.parameter for finding in result.findings if finding.severity == "error"} == {
        "ARMING_CHECK",
        "SR2_EXTRA1",
    }


def test_unknown_nonzero_stream_group_fails_closed() -> None:
    text = _export(bench_ready=True) + "\nSR2_FUTURE_GROUP,1"

    result = validate_big_bird_parameter_export(text, stage=BigBirdProfileStage.BENCH_READY)

    assert not result.passed
    assert any(finding.code == "unapproved_stream_group" for finding in result.findings)


def test_missing_sensor_or_calibration_evidence_fails_closed() -> None:
    text = (
        _export()
        .replace("BARO1_DEVID,816641\n", "")
        .replace("COMPASS_OFS_Z,10.15593", "COMPASS_OFS_Z,0")
    )

    result = validate_big_bird_parameter_export(text, stage=BigBirdProfileStage.PRE_CHANGE)

    assert not result.passed
    assert {finding.parameter for finding in result.findings if finding.severity == "error"} == {
        "BARO1_DEVID",
        "COMPASS_OFS_Z",
    }


def test_missing_duplicate_and_malformed_rows_fail_before_profile_comparison() -> None:
    text = _export() + "\nSYSID_THISMAV,20\nnot a parameter row"

    result = validate_big_bird_parameter_export(text, stage=BigBirdProfileStage.PRE_CHANGE)

    assert not result.passed
    assert result.mission_total is None
    assert [finding.code for finding in result.findings] == [
        "malformed_export",
        "malformed_export",
    ]


def test_tab_delimited_exports_and_comments_are_supported() -> None:
    text = "# Mission Planner export\n" + _export().replace(",", "\t")

    result = validate_big_bird_parameter_export(text, stage=BigBirdProfileStage.PRE_CHANGE)

    assert result.passed
    assert result.source_line_count == 46


def test_evidence_document_is_stable_and_contains_no_parameter_dump() -> None:
    result = validate_big_bird_parameter_export(
        _export(bench_ready=True), stage=BigBirdProfileStage.BENCH_READY
    )

    document = validation_to_document(result)

    assert document["schema_version"] == 1
    assert document["profile"] == "big-bird-matekh7a3-arducopter-4.6.3"
    assert document["passed"] is True
    assert "parameters" not in document
