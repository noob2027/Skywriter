"""Static acceptance tests for the Windows packaging contract."""

from __future__ import annotations

import hashlib
import importlib
import os
import struct
import sys
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

import skywriter.main as main_module
import skywriter.packaged_runtime_smoke as runtime_smoke
import tools.packaging.collect_licenses as license_collector
from skywriter import __version__

REPOSITORY_ROOT = Path(__file__).parents[2]
INSTALLER = REPOSITORY_ROOT / "packaging" / "windows" / "installer.iss"
SPEC = REPOSITORY_ROOT / "packaging" / "windows" / "skywriter.spec"
VERSION_INFO = REPOSITORY_ROOT / "packaging" / "windows" / "version_info.txt"
BUILD_SCRIPT = REPOSITORY_ROOT / "packaging" / "windows" / "build-installer.ps1"
WINDOWS_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "windows-installer.yml"
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
    workflow = WINDOWS_WORKFLOW.read_text(encoding="utf-8")

    version_tuple = tuple(int(part) for part in __version__.split(".")) + (0,)
    version_tuple_text = ",".join(str(part) for part in version_tuple)
    assert f"filevers=({version_tuple_text})" in "".join(version_info.split())
    assert f'StringStruct("ProductVersion", "{__version__}")' in version_info
    assert "AppName={#AppName}" in installer
    assert '#define AppName "SKYWriter Prototype"' in installer
    assert "AppPublisher=305 Skylab" in installer
    assert "Copyright" not in installer
    assert '$requiredPython = "3.12.13"' in build
    assert '$innoVersion = "6.7.3"' in build
    assert (
        f"skywriter-prototype-windows-${{{{ steps.metadata.outputs.signing }}}}-{__version__}"
        in workflow
    )
    assert f"SKYWriter-Prototype-Setup-{__version__}.exe" in workflow


def test_payload_includes_map_assets_notices_mavlink_and_windows_serial_enumerator() -> None:
    spec = SPEC.read_text(encoding="utf-8")

    assert 'includes=["ui/map/static/**/*"]' in spec
    assert '(str(NOTICES_ROOT), "notices")' in spec
    assert '"pymavlink.dialects.v20.ardupilotmega"' in spec
    assert '"serial.tools.list_ports_windows"' in spec
    assert 'contents_directory="_internal"' in spec
    assert "console=False" in spec


def test_spec_rejects_bundled_workspace_poppler_icu_shadow() -> None:
    spec = SPEC.read_text(encoding="utf-8")

    assert 'POPPLER_ICU_SHADOWS = {"icudt78.dll", "icuuc.dll"}' in spec
    assert 'and "poppler" in' in spec


def test_notices_cover_the_bundled_pyinstaller_bootloader() -> None:
    collector = LICENSE_COLLECTOR.read_text(encoding="utf-8")

    assert '"pyinstaller"' in collector
    assert '"pyinstaller-hooks-contrib"' in collector


def test_pyserial_notice_uses_the_exact_version_pinned_upstream_fallback(
    tmp_path: Path,
) -> None:
    distribution = metadata.distribution("pyserial")

    assert distribution.version == license_collector.PYSERIAL_FALLBACK_VERSION
    assert not distribution.metadata.get_all("License-File")

    copied, detail = license_collector._copy_pinned_license_fallback(
        "pyserial",
        distribution,
        tmp_path,
    )

    assert copied == ["pyserial/LICENSE.txt", "pyserial/SOURCE.txt"]
    assert "repository-pinned upstream v3.5 fallback" in detail
    license_bytes = (tmp_path / "pyserial" / "LICENSE.txt").read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == license_collector.PYSERIAL_LICENSE_SHA256
    provenance = (tmp_path / "pyserial" / "SOURCE.txt").read_text(encoding="utf-8")
    assert "https://raw.githubusercontent.com/pyserial/pyserial/v3.5/LICENSE.txt" in provenance


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
        packaged_visual_smoke: bool = False,
    ) -> int:
        observed["arguments"] = arguments
        observed["close_after_ms"] = close_after_ms
        observed["packaged_smoke"] = packaged_smoke
        observed["packaged_visual_smoke"] = packaged_visual_smoke
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
        "packaged_visual_smoke": False,
    }
    assert os.environ[main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT] == "1"


def test_packaged_serial_import_smoke_checks_exact_windows_runtime_without_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["SKYWriter.exe", runtime_smoke.PACKAGED_SERIAL_IMPORT_SMOKE_ARGUMENT],
    )

    def import_module(name: str) -> object:
        imported.append(name)
        return SimpleNamespace(VERSION="3.5")

    monkeypatch.setattr(importlib, "import_module", import_module)
    monkeypatch.delenv(main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT, raising=False)

    assert main_module.main() == 0
    assert imported == ["serial", "serial.tools.list_ports_windows"]
    assert os.environ[main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT] == "1"


def test_packaged_visual_smoke_is_hardware_blocked_and_renderer_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(
        arguments: Sequence[str] | None = None,
        *,
        close_after_ms: int | None = None,
        packaged_smoke: bool = False,
        packaged_visual_smoke: bool = False,
    ) -> int:
        observed.update(
            arguments=arguments,
            close_after_ms=close_after_ms,
            packaged_smoke=packaged_smoke,
            packaged_visual_smoke=packaged_visual_smoke,
        )
        return 0

    monkeypatch.setattr(main_module, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["SKYWriter.exe", main_module.PACKAGED_VISUAL_SMOKE_TEST_ARGUMENT],
    )
    monkeypatch.delenv(main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT, raising=False)

    assert main_module.main() == 0
    assert observed == {
        "arguments": ["SKYWriter.exe"],
        "close_after_ms": None,
        "packaged_smoke": True,
        "packaged_visual_smoke": True,
    }
    assert os.environ[main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT] == "1"

    installer_smoke = (REPOSITORY_ROOT / "packaging/windows/test-installer.ps1").read_text(
        encoding="utf-8"
    )
    assert "--packaged-map-visual-smoke" in installer_smoke
    assert "--packaged-serial-import-smoke" in installer_smoke
    assert "SKYWRITER_PACKAGED_SMOKE_TILE_ORIGIN" in installer_smoke
    assert "QTWEBENGINE_CHROMIUM_FLAGS" not in installer_smoke
    assert "--no-sandbox" not in installer_smoke


def test_installed_ui_acceptance_uses_shortcut_safe_paths_and_hardware_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(
        arguments: Sequence[str] | None = None,
        *,
        close_after_ms: int | None = None,
        packaged_smoke: bool = False,
        packaged_visual_smoke: bool = False,
        packaged_ui_acceptance: bool = False,
    ) -> int:
        observed.update(
            arguments=arguments,
            close_after_ms=close_after_ms,
            packaged_smoke=packaged_smoke,
            packaged_visual_smoke=packaged_visual_smoke,
            packaged_ui_acceptance=packaged_ui_acceptance,
        )
        return 0

    monkeypatch.setattr(main_module, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["SKYWriter.exe", main_module.PACKAGED_UI_ACCEPTANCE_ARGUMENT],
    )
    monkeypatch.setenv(main_module.PACKAGED_UI_ACCEPTANCE_EVIDENCE_ENVIRONMENT, str(tmp_path))
    monkeypatch.delenv(main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT, raising=False)

    assert main_module.main() == 0
    assert observed == {
        "arguments": ["SKYWriter.exe"],
        "close_after_ms": None,
        "packaged_smoke": False,
        "packaged_visual_smoke": False,
        "packaged_ui_acceptance": True,
    }
    assert os.environ[main_module.PACKAGED_SMOKE_TEST_ENVIRONMENT] == "1"

    installer_smoke = (REPOSITORY_ROOT / "packaging/windows/test-installer.ps1").read_text(
        encoding="utf-8"
    )
    assert "Start-Process -FilePath $shortcut" in installer_smoke
    assert "SKYWRITER_INSTALLED_UI_EVIDENCE" in installer_smoke
    assert "vehicle_io.attempts -ne 0" in installer_smoke
    assert "serial_selection.enumerated_count -ne 1" in installer_smoke
    assert "serial_selection.vehicle_open_clicked" in installer_smoke
    assert "preflight_composition.controller_bound" in installer_smoke
    assert "preflight_composition.prearm_or_arm_clicked" in installer_smoke
    assert "flight_boundary.global_unavailable_gate_visible" in installer_smoke
    assert "flight_boundary.flight_command_clicked" in installer_smoke
    assert "installed-ui-acceptance.json" in installer_smoke
    install_start = installer_smoke.index("Start-Process -FilePath $installer")
    outer_try = installer_smoke.rfind("try {", 0, install_start)
    outer_finally = installer_smoke.rfind("finally {")
    assert outer_try >= 0
    assert outer_finally > install_start
    assert "Test-Path -LiteralPath $uninstaller -PathType Leaf" in installer_smoke
