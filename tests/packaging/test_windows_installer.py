"""Static acceptance tests for the Windows packaging contract."""

from __future__ import annotations

import os
import struct
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

import skywriter.main as main_module
from skywriter import __version__

REPOSITORY_ROOT = Path(__file__).parents[2]
INSTALLER = REPOSITORY_ROOT / "packaging" / "windows" / "installer.iss"
SPEC = REPOSITORY_ROOT / "packaging" / "windows" / "skywriter.spec"
VERSION_INFO = REPOSITORY_ROOT / "packaging" / "windows" / "version_info.txt"
BUILD_SCRIPT = REPOSITORY_ROOT / "packaging" / "windows" / "build-installer.ps1"
LICENSE_COLLECTOR = REPOSITORY_ROOT / "tools" / "packaging" / "collect_licenses.py"
ICON = REPOSITORY_ROOT / "packaging" / "assets" / "skywriter-provisional.ico"


def test_installer_is_per_user_x64_with_standard_shortcuts_and_uninstall() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in script
    assert "DefaultDirName={localappdata}\\Programs\\SKYWriter Prototype" in script
    assert "ArchitecturesAllowed=x64compatible" in script
    assert 'Name: "{group}\\SKYWriter Prototype"' in script
    assert 'Name: "{autodesktop}\\SKYWriter Prototype"' in script
    assert "Flags: checkedonce" in script
    assert "UninstallDisplayIcon={app}\\{#AppExeName}" in script
    assert "OutputBaseFilename=SKYWriter-Prototype-Setup-{#AppVersion}" in script
    assert "PrivilegesRequired=admin" not in script


def test_packaging_metadata_matches_the_application_version() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    version_info = VERSION_INFO.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")

    version_tuple = tuple(int(part) for part in __version__.split(".")) + (0,)
    version_tuple_text = ",".join(str(part) for part in version_tuple)
    assert f"filevers=({version_tuple_text})" in "".join(version_info.split())
    assert f'StringStruct("ProductVersion", "{__version__}")' in version_info
    assert "AppName={#AppName}" in installer
    assert '#define AppName "SKYWriter Prototype"' in installer
    assert "AppPublisher=305 Skylab" in installer
    assert "Copyright" not in installer
    assert '$requiredPython = "3.12.10"' in build
    assert '$innoVersion = "6.7.3"' in build


def test_payload_includes_map_assets_notices_and_dynamic_mavlink_dialect() -> None:
    spec = SPEC.read_text(encoding="utf-8")

    assert 'includes=["ui/map/static/**/*"]' in spec
    assert '(str(NOTICES_ROOT), "notices")' in spec
    assert '"pymavlink.dialects.v20.ardupilotmega"' in spec
    assert 'contents_directory="_internal"' in spec
    assert "console=False" in spec


def test_notices_cover_the_bundled_pyinstaller_bootloader() -> None:
    collector = LICENSE_COLLECTOR.read_text(encoding="utf-8")

    assert '"pyinstaller"' in collector
    assert '"pyinstaller-hooks-contrib"' in collector


def test_provisional_icon_is_a_multisize_windows_icon() -> None:
    data = ICON.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", data)

    assert (reserved, image_type, count) == (0, 1, 4)
    assert len(data) > 1_000


def test_installer_behavior_contains_no_aircraft_specific_constants() -> None:
    behavior = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (INSTALLER, SPEC, BUILD_SCRIPT, Path(main_module.__file__))
    )
    forbidden = ("COM19", "COM8", "board UID", "SiK Net ID")

    assert not any(value in behavior for value in forbidden)


def test_packaged_smoke_argument_is_bounded_and_sets_the_io_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(
        arguments: Sequence[str] | None = None,
        *,
        close_after_ms: int | None = None,
        packaged_smoke: bool = False,
    ) -> int:
        observed["arguments"] = arguments
        observed["close_after_ms"] = close_after_ms
        observed["packaged_smoke"] = packaged_smoke
        return 0

    monkeypatch.setattr(main_module, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["SKYWriter.exe", main_module.PACKAGED_SMOKE_TEST_ARGUMENT],
    )
    monkeypatch.delenv(main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT, raising=False)

    assert main_module.main() == 0
    assert observed == {
        "arguments": ["SKYWriter.exe"],
        "close_after_ms": None,
        "packaged_smoke": True,
    }
    assert os.environ[main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT] == "1"
