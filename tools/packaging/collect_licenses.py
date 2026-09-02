"""Collect license files for distributions bundled into the Windows payload."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from importlib import metadata
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PYSERIAL_FALLBACK_VERSION = "3.5"
PYSERIAL_FALLBACK_ROOT = REPOSITORY_ROOT / "packaging" / "licenses" / "pyserial-3.5"
PYSERIAL_LICENSE_SHA256 = "f91cb9813de6a5b142b8f7f2dede630b5134160aedaeaf55f4d6a7e2593ca3f3"

RUNTIME_DISTRIBUTIONS = (
    "ast-serialize",
    "future",
    "librt",
    "lxml",
    "pymavlink",
    "pyserial",
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
        fallback_detail = ""
        if declared:
            copied: list[str] = []
            for declared_path in declared:
                candidate = _declared_license_file(distribution, declared_path)
                relative = Path(declared_path.replace("\\", "/"))
                destination = output / distribution_name / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, destination)
                copied.append(destination.relative_to(output).as_posix())
        else:
            copied, fallback_detail = _copy_pinned_license_fallback(
                distribution_name,
                distribution,
                output,
            )
        inventory.append(
            f"- {distribution.metadata['Name']} {distribution.version}: "
            f"{', '.join(copied)}{fallback_detail}"
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
    candidate = Path(str(distribution.locate_file(matching[0])))
    if not candidate.is_file():
        raise FileNotFoundError(f"declared license is missing: {candidate}")
    return candidate


def _copy_pinned_license_fallback(
    distribution_name: str,
    distribution: metadata.Distribution,
    output: Path,
) -> tuple[list[str], str]:
    """Copy the one approved notice fallback for the pyserial 3.5 wheel."""

    if distribution_name != "pyserial":
        raise RuntimeError(f"{distribution_name} declares no License-File metadata")
    if distribution.version != PYSERIAL_FALLBACK_VERSION:
        raise RuntimeError(
            "pyserial declares no License-File metadata and its repository fallback "
            f"is pinned to {PYSERIAL_FALLBACK_VERSION}; found {distribution.version}"
        )

    license_source = PYSERIAL_FALLBACK_ROOT / "LICENSE.txt"
    provenance_source = PYSERIAL_FALLBACK_ROOT / "SOURCE.txt"
    for source in (license_source, provenance_source):
        if not source.is_file():
            raise FileNotFoundError(f"pinned pyserial notice source is missing: {source}")

    license_text = license_source.read_text(encoding="utf-8")
    license_hash = hashlib.sha256(license_text.encode("utf-8")).hexdigest()
    if license_hash != PYSERIAL_LICENSE_SHA256:
        raise RuntimeError(
            f"pinned pyserial license does not match the upstream v3.5 LICENSE.txt: {license_hash}"
        )

    destination_root = output / distribution_name
    destination_root.mkdir(parents=True, exist_ok=True)
    license_destination = destination_root / "LICENSE.txt"
    license_destination.write_text(
        license_text,
        encoding="utf-8",
        newline="\n",
    )
    provenance_destination = destination_root / "SOURCE.txt"
    provenance_destination.write_text(
        provenance_source.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    copied = [
        license_destination.relative_to(output).as_posix(),
        provenance_destination.relative_to(output).as_posix(),
    ]
    detail = " (repository-pinned upstream v3.5 fallback; wheel has no License-File metadata)"
    return copied, detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    collect_licenses(arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
