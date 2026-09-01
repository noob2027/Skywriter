"""Validate an offline Mission Planner export against the Big Bird profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from skywriter.compatibility.big_bird import (
    BigBirdProfileStage,
    validate_big_bird_parameter_export,
    validation_to_document,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline, read-only validation for a Big Bird Mission Planner .param export."
    )
    parser.add_argument("parameter_export", type=Path)
    parser.add_argument(
        "--stage",
        type=BigBirdProfileStage,
        choices=tuple(BigBirdProfileStage),
        required=True,
    )
    parser.add_argument("--expected-mission-count", type=int)
    args = parser.parse_args(argv)

    raw = args.parameter_export.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        parser.error(f"parameter export must be UTF-8 text: {error}")
    result = validate_big_bird_parameter_export(
        text,
        stage=args.stage,
        expected_mission_count=args.expected_mission_count,
    )
    document = validation_to_document(result)
    document["source_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
