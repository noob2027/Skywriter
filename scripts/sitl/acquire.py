"""Acquire and verify the exact approved stock SITL artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

from scripts.sitl.pinned import (
    PINNED_ARTIFACT,
    PINNED_STARTUP_DEFAULTS,
    VerifiedArtifact,
    VerifiedStartupDefaults,
    verify_artifact,
    verify_startup_defaults,
)

VerifiedInput = TypeVar("VerifiedInput", VerifiedArtifact, VerifiedStartupDefaults)


def _write_record(path: Path | None, document: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    hash_path = path.with_suffix(f"{path.suffix}.sha256")
    hash_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def _acquire_input(
    destination: Path,
    url: str,
    verify: Callable[[Path], VerifiedInput],
    *,
    executable: bool,
) -> tuple[str, VerifiedInput]:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            verified = verify(destination)
        except RuntimeError:
            destination.unlink()
        else:
            if executable and os.name == "posix":
                destination.chmod(0o700)
            return "verified-cache", verified

    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.download")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with temporary.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
        verify(temporary)
        os.replace(temporary, destination)
        if executable and os.name == "posix":
            destination.chmod(0o700)
        return "downloaded", verify(destination)
    finally:
        temporary.unlink(missing_ok=True)


def acquire(
    destination: Path,
    startup_defaults_destination: Path,
    record_path: Path | None = None,
) -> None:
    """Acquire and verify the stock binary plus its official Copter defaults."""

    try:
        artifact_status, artifact = _acquire_input(
            destination,
            PINNED_ARTIFACT.url,
            verify_artifact,
            executable=True,
        )
        defaults_status, startup_defaults = _acquire_input(
            startup_defaults_destination,
            PINNED_STARTUP_DEFAULTS.url,
            verify_startup_defaults,
            executable=False,
        )
        status = (
            "verified-cache"
            if artifact_status == defaults_status == "verified-cache"
            else "downloaded"
        )
        _write_record(
            record_path,
            {
                "status": status,
                "artifact": asdict(artifact),
                "startup_defaults": asdict(startup_defaults),
            },
        )
    except BaseException as error:
        _write_record(
            record_path,
            {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "expected_artifact": asdict(PINNED_ARTIFACT),
                "expected_startup_defaults": asdict(PINNED_STARTUP_DEFAULTS),
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--startup-defaults-destination", type=Path, required=True)
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    acquire(args.destination, args.startup_defaults_destination, args.record)
    print(f"verified {args.destination.resolve()} as {PINNED_ARTIFACT.sha256}")
    print(
        "verified "
        f"{args.startup_defaults_destination.resolve()} as {PINNED_STARTUP_DEFAULTS.sha256}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
