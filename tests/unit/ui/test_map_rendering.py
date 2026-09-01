"""Windows WebEngine renderer policy tests."""

from __future__ import annotations

import json
from pathlib import Path

from skywriter.main import create_application
from skywriter.ui.map.rendering import (
    configure_map_renderer,
    persist_renderer_diagnostics,
    renderer_diagnostics,
)


def test_windows_renderer_adds_only_the_documented_software_flag() -> None:
    environment = {"QTWEBENGINE_CHROMIUM_FLAGS": "--enable-logging"}

    configured = configure_map_renderer(environment, platform_name="win32")

    assert environment["QTWEBENGINE_CHROMIUM_FLAGS"] == "--enable-logging --disable-gpu"
    assert configured.mode == "chromium-software"
    assert configured.windows_software_default is True
    assert configured.chromium_gpu_disabled is True
    assert configured.sandbox_disabled_by_environment is False
    assert "--no-sandbox" not in environment["QTWEBENGINE_CHROMIUM_FLAGS"]


def test_non_windows_renderer_leaves_the_platform_default_untouched() -> None:
    environment: dict[str, str] = {}

    configured = configure_map_renderer(environment, platform_name="linux")

    assert environment == {}
    assert configured.mode == "platform-default"
    assert configured.windows_software_default is False
    assert configured.chromium_gpu_disabled is False


def test_diagnostics_report_but_never_claim_skywriter_disabled_the_sandbox(
    tmp_path: Path,
) -> None:
    configured = configure_map_renderer(
        {
            "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox",
            "QTWEBENGINE_DISABLE_SANDBOX": "1",
        },
        platform_name="win32",
    )
    app = create_application(["skywriter-renderer-policy-test"])

    diagnostics = renderer_diagnostics(app, configured)
    path = persist_renderer_diagnostics(app, configured, directory=tmp_path)

    assert diagnostics["sandbox_disabled_by_environment"] is True
    assert diagnostics["sandbox_disabled_by_skywriter"] is False
    assert json.loads(path.read_text(encoding="utf-8")) == diagnostics
    assert "command" not in path.read_text(encoding="utf-8")
