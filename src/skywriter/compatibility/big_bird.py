"""Pure, offline validation for the reviewed Big Bird vehicle profile.

This module parses caller-supplied Mission Planner parameter exports.  It has no
vehicle connection, parameter-read, parameter-write, firmware, or command surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

_PARAMETER_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class BigBirdProfileStage(StrEnum):
    """Closed validation stages for retained parameter exports."""

    PRE_CHANGE = "pre-change"
    BENCH_READY = "bench-ready"


class FindingSeverity(StrEnum):
    """Whether a finding blocks the selected profile stage."""

    ERROR = "error"
    NOTICE = "notice"


@dataclass(frozen=True, slots=True)
class ParameterFinding:
    """One explicit parameter-export validation result."""

    severity: FindingSeverity
    code: str
    parameter: str | None
    expected: str | None
    actual: str | None
    message: str


@dataclass(frozen=True, slots=True)
class BigBirdParameterValidation:
    """Fail-closed decision for one parsed Big Bird parameter export."""

    stage: BigBirdProfileStage
    source_line_count: int
    parameter_count: int
    mission_total: int | None
    findings: tuple[ParameterFinding, ...]

    @property
    def passed(self) -> bool:
        return not any(finding.severity is FindingSeverity.ERROR for finding in self.findings)


_COMMON_EXACT: dict[str, Decimal] = {
    "SYSID_THISMAV": Decimal(20),
    "SYSID_MYGCS": Decimal(255),
    "FRAME_CLASS": Decimal(1),
    "FRAME_TYPE": Decimal(1),
    "SERIAL0_PROTOCOL": Decimal(2),
    "SERIAL0_BAUD": Decimal(115),
    "SERIAL1_PROTOCOL": Decimal(23),
    "SERIAL1_BAUD": Decimal(115),
    "SERIAL1_OPTIONS": Decimal(7),
    "SERIAL2_PROTOCOL": Decimal(2),
    "SERIAL2_BAUD": Decimal(57),
    "SERIAL2_OPTIONS": Decimal(0),
    "SERIAL3_PROTOCOL": Decimal(5),
    "SERIAL3_BAUD": Decimal(115),
    "SERIAL3_OPTIONS": Decimal(0),
    "GPS1_TYPE": Decimal(1),
    "GPS1_RATE_MS": Decimal(200),
    "BARO_PRIMARY": Decimal(0),
    "BARO1_DEVID": Decimal(816641),
    "COMPASS_DEV_ID": Decimal(855297),
    "COMPASS_DEV_ID2": Decimal(0),
    "COMPASS_DEV_ID3": Decimal(0),
    "COMPASS_DEV_ID4": Decimal(0),
    "COMPASS_DEV_ID5": Decimal(0),
    "COMPASS_DEV_ID6": Decimal(0),
    "COMPASS_DEV_ID7": Decimal(0),
    "COMPASS_DEV_ID8": Decimal(0),
    "COMPASS_EXTERNAL": Decimal(1),
    "COMPASS_USE": Decimal(1),
    "ARMING_CHECK": Decimal(4366),
    "BATT_ARM_VOLT": Decimal("19.7"),
}

_SR2_ZERO_GROUPS = (
    "SR2_ADSB",
    "SR2_EXTRA1",
    "SR2_EXTRA2",
    "SR2_PARAMS",
    "SR2_RAW_CTRL",
    "SR2_RAW_SENS",
    "SR2_RC_CHAN",
)

_PRE_CHANGE_STREAM_RATES: dict[str, Decimal] = {
    **{name: Decimal(0) for name in _SR2_ZERO_GROUPS},
    "SR2_EXT_STAT": Decimal(0),
    "SR2_EXTRA3": Decimal(0),
    "SR2_POSITION": Decimal(0),
}

_BENCH_READY_STREAM_RATES: dict[str, Decimal] = {
    **{name: Decimal(0) for name in _SR2_ZERO_GROUPS},
    "SR2_EXT_STAT": Decimal(2),
    "SR2_EXTRA3": Decimal(1),
    "SR2_POSITION": Decimal(2),
}


def validate_big_bird_parameter_export(
    text: str,
    *,
    stage: BigBirdProfileStage,
    expected_mission_count: int | None = None,
) -> BigBirdParameterValidation:
    """Validate one offline export against the reviewed Big Bird profile.

    ``expected_mission_count`` is optional because ``MIS_TOTAL`` cannot establish
    mission semantics.  The existing SKYWriter full download/comparison remains
    authoritative for mission verification.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(stage, BigBirdProfileStage):
        raise TypeError("stage must be a BigBirdProfileStage")
    if expected_mission_count is not None and (
        isinstance(expected_mission_count, bool)
        or not isinstance(expected_mission_count, int)
        or expected_mission_count < 0
    ):
        raise ValueError("expected_mission_count must be a non-negative integer or None")

    values, parse_findings, source_line_count = _parse_parameter_export(text)
    findings = list(parse_findings)
    if parse_findings:
        return BigBirdParameterValidation(
            stage, source_line_count, len(values), None, tuple(findings)
        )

    expected = dict(_COMMON_EXACT)
    expected.update(
        _PRE_CHANGE_STREAM_RATES
        if stage is BigBirdProfileStage.PRE_CHANGE
        else _BENCH_READY_STREAM_RATES
    )
    if stage is BigBirdProfileStage.PRE_CHANGE:
        expected["MIS_TOTAL"] = Decimal(14)
    elif expected_mission_count is not None:
        expected["MIS_TOTAL"] = Decimal(expected_mission_count)

    for name, expected_value in expected.items():
        _check_exact(values, findings, name, expected_value)

    _check_unapproved_stream_groups(values, findings)
    for name in ("COMPASS_OFS_X", "COMPASS_OFS_Y", "COMPASS_OFS_Z"):
        _check_nonzero(values, findings, name)

    mission_total = _integer_value(values.get("MIS_TOTAL"))
    if stage is BigBirdProfileStage.PRE_CHANGE and mission_total == 14:
        findings.append(
            ParameterFinding(
                FindingSeverity.NOTICE,
                "existing_mission_requires_replacement",
                "MIS_TOTAL",
                None,
                "14",
                "An onboard mission exists; the later bench workflow must explicitly replace it "
                "and verify the complete downloaded mission.",
            )
        )
    if stage is BigBirdProfileStage.BENCH_READY and expected_mission_count is None:
        findings.append(
            ParameterFinding(
                FindingSeverity.NOTICE,
                "mission_semantics_not_proven",
                "MIS_TOTAL",
                None,
                str(mission_total) if mission_total is not None else None,
                "The export records only a mission count; accepted MISSION_ACK plus a complete "
                "SKYWriter download and field comparison are still required.",
            )
        )

    return BigBirdParameterValidation(
        stage,
        source_line_count,
        len(values),
        mission_total,
        tuple(findings),
    )


def validation_to_document(result: BigBirdParameterValidation) -> dict[str, object]:
    """Return a stable JSON-compatible evidence representation."""

    if not isinstance(result, BigBirdParameterValidation):
        raise TypeError("result must be a BigBirdParameterValidation")
    return {
        "schema_version": 1,
        "profile": "big-bird-matekh7a3-arducopter-4.6.3",
        "stage": result.stage.value,
        "passed": result.passed,
        "source_line_count": result.source_line_count,
        "parameter_count": result.parameter_count,
        "mission_total": result.mission_total,
        "findings": [
            {
                "severity": finding.severity.value,
                "code": finding.code,
                "parameter": finding.parameter,
                "expected": finding.expected,
                "actual": finding.actual,
                "message": finding.message,
            }
            for finding in result.findings
        ],
    }


def _parse_parameter_export(
    text: str,
) -> tuple[dict[str, Decimal], tuple[ParameterFinding, ...], int]:
    values: dict[str, Decimal] = {}
    findings: list[ParameterFinding] = []
    lines = text.splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        separator = "," if "," in line else "\t" if "\t" in line else None
        if separator is None:
            findings.append(_parse_error(line_number, "missing comma or tab separator"))
            continue
        parts = [part.strip() for part in line.split(separator)]
        if len(parts) != 2:
            findings.append(_parse_error(line_number, "expected exactly name and value"))
            continue
        name, raw_value = parts
        if not _PARAMETER_NAME.fullmatch(name):
            findings.append(_parse_error(line_number, "invalid parameter name", name))
            continue
        if name in values:
            findings.append(_parse_error(line_number, "duplicate parameter", name))
            continue
        try:
            value = Decimal(raw_value)
        except InvalidOperation:
            findings.append(_parse_error(line_number, "value is not numeric", name))
            continue
        if not value.is_finite():
            findings.append(_parse_error(line_number, "value must be finite", name))
            continue
        values[name] = value
    return values, tuple(findings), len(lines)


def _parse_error(line_number: int, message: str, parameter: str | None = None) -> ParameterFinding:
    return ParameterFinding(
        FindingSeverity.ERROR,
        "malformed_export",
        parameter,
        None,
        None,
        f"Line {line_number}: {message}.",
    )


def _check_exact(
    values: dict[str, Decimal],
    findings: list[ParameterFinding],
    name: str,
    expected: Decimal,
) -> None:
    actual = values.get(name)
    if actual is None:
        findings.append(
            ParameterFinding(
                FindingSeverity.ERROR,
                "missing_parameter",
                name,
                str(expected),
                None,
                f"Required parameter {name} is missing.",
            )
        )
    elif actual != expected:
        findings.append(
            ParameterFinding(
                FindingSeverity.ERROR,
                "unexpected_value",
                name,
                str(expected),
                str(actual),
                f"{name} differs from the reviewed Big Bird profile.",
            )
        )


def _check_nonzero(values: dict[str, Decimal], findings: list[ParameterFinding], name: str) -> None:
    actual = values.get(name)
    if actual is None:
        findings.append(
            ParameterFinding(
                FindingSeverity.ERROR,
                "missing_parameter",
                name,
                "nonzero",
                None,
                f"Required parameter {name} is missing.",
            )
        )
    elif actual == 0:
        findings.append(
            ParameterFinding(
                FindingSeverity.ERROR,
                "unexpected_value",
                name,
                "nonzero",
                "0",
                f"{name} must retain observed sensor/calibration evidence.",
            )
        )


def _check_unapproved_stream_groups(
    values: dict[str, Decimal], findings: list[ParameterFinding]
) -> None:
    approved_names = set(_BENCH_READY_STREAM_RATES)
    for name, actual in sorted(values.items()):
        if name.startswith("SR2_") and name not in approved_names and actual != 0:
            findings.append(
                ParameterFinding(
                    FindingSeverity.ERROR,
                    "unapproved_stream_group",
                    name,
                    "0",
                    str(actual),
                    f"Unreviewed stream group {name} must remain zero.",
                )
            )


def _integer_value(value: Decimal | None) -> int | None:
    if value is None or value != value.to_integral_value():
        return None
    return int(value)
