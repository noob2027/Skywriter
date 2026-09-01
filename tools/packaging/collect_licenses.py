"""Collect license files for distributions bundled into the Windows payload."""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import metadata
from pathlib import Path

RUNTIME_DISTRIBUTIONS = (
    "ast-serialize",
    "future",
    "librt",
    "lxml",
    "pymavlink",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "PySide6-Addons",
    "PySide6-Essentials",
    "shiboken6",
)


def collect_licenses(output: Path) -> None:
    """Copy declared runtime license files and write an inspectable inventory."""

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    inventory = [
        "SKYWriter Prototype third-party license inventory",
        "",
        "These files are copied from the exact distributions used for this build.",
        "SKYWriter itself does not yet declare a repository license.",
        "",
    ]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError(f"Python license is missing: {python_license}")
    python_destination = output / "Python" / "LICENSE.txt"
    python_destination.parent.mkdir()
    shutil.copy2(python_license, python_destination)
    inventory.append(f"- CPython {sys.version.split()[0]}: Python/LICENSE.txt")

    for distribution_name in RUNTIME_DISTRIBUTIONS:
        distribution = metadata.distribution(distribution_name)
        declared = distribution.metadata.get_all("License-File") or []
        if not declared:
            raise RuntimeError(f"{distribution_name} declares no License-File metadata")
        copied: list[str] = []
        for declared_path in declared:
            candidate = _declared_license_file(distribution, declared_path)
            relative = Path(declared_path.replace("\\", "/"))
            destination = output / distribution_name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, destination)
            copied.append(destination.relative_to(output).as_posix())
        inventory.append(
            f"- {distribution.metadata['Name']} {distribution.version}: {', '.join(copied)}"
        )

    (output / "THIRD-PARTY-NOTICES.txt").write_text(
        "\n".join(inventory) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _declared_license_file(
    distribution: metadata.Distribution,
    declared_path: str,
) -> Path:
    normalized = declared_path.replace("\\", "/")
    files = tuple(distribution.files or ())
    matching = [
        item for item in files if item.as_posix().endswith(f".dist-info/licenses/{normalized}")
    ]
    if not matching:
        matching = [item for item in files if item.as_posix().endswith(f".dist-info/{normalized}")]
    if len(matching) != 1:
        raise RuntimeError(
            f"{distribution.metadata['Name']} license {declared_path!r} "
            f"resolved to {len(matching)} files"
        )
    candidate = Path(distribution.locate_file(matching[0]))
    if not candidate.is_file():
        raise FileNotFoundError(f"declared license is missing: {candidate}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    collect_licenses(arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
