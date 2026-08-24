"""Acquire and verify the exact approved stock SITL artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

from scripts.sitl.pinned import PINNED_ARTIFACT, verify_artifact


def _write_record(path: Path | None, document: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    hash_path = path.with_suffix(f"{path.suffix}.sha256")
    hash_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def acquire(destination: Path, record_path: Path | None = None) -> None:
    """Reuse a valid cached artifact or replace it from the official endpoint."""

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if destination.exists():
            try:
                verified = verify_artifact(destination)
            except RuntimeError:
                destination.unlink()
            else:
                if os.name == "posix":
                    destination.chmod(0o700)
                _write_record(
                    record_path,
                    {"status": "verified-cache", "artifact": verified.__dict__},
                )
                return

        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.download")
        try:
            with urllib.request.urlopen(PINNED_ARTIFACT.url, timeout=60) as response:
                with temporary.open("wb") as stream:
                    shutil.copyfileobj(response, stream, length=1024 * 1024)
            verify_artifact(temporary)
            os.replace(temporary, destination)
            if os.name == "posix":
                destination.chmod(0o700)
            verified = verify_artifact(destination)
        finally:
            temporary.unlink(missing_ok=True)
        _write_record(record_path, {"status": "downloaded", "artifact": verified.__dict__})
    except BaseException as error:
        _write_record(
            record_path,
            {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "expected": PINNED_ARTIFACT.__dict__,
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    acquire(args.destination, args.record)
    print(f"verified {args.destination.resolve()} as {PINNED_ARTIFACT.sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
