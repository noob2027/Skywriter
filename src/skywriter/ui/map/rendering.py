"""Process-level rendering policy for the lightweight Windows mission map."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths, qVersion
from PySide6.QtWidgets import QApplication

LOGGER = logging.getLogger("skywriter.ui.map.rendering")
_CHROMIUM_FLAGS_ENVIRONMENT = "QTWEBENGINE_CHROMIUM_FLAGS"
_WEBENGINE_SANDBOX_ENVIRONMENT = "QTWEBENGINE_DISABLE_SANDBOX"
_SOFTWARE_RENDERING_FLAG = "--disable-gpu"
_NO_SANDBOX_FLAG = "--no-sandbox"
_DIAGNOSTIC_FILENAME = "map-renderer.json"


@dataclass(frozen=True, slots=True)
class MapRendererConfiguration:
    """Safe, non-identifying renderer facts for logs and smoke evidence."""

    mode: str
    windows_software_default: bool
    chromium_gpu_disabled: bool
    sandbox_disabled_by_environment: bool


def configure_map_renderer(
    environment: MutableMapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> MapRendererConfiguration:
    """Select the proven Windows software compositor before QApplication exists."""

    target = os.environ if environment is None else environment
    platform_value = sys.platform if platform_name is None else platform_name
    flags = target.get(_CHROMIUM_FLAGS_ENVIRONMENT, "").split()
    windows_default = platform_value == "win32"
    if windows_default and _SOFTWARE_RENDERING_FLAG not in flags:
        flags.append(_SOFTWARE_RENDERING_FLAG)
        target[_CHROMIUM_FLAGS_ENVIRONMENT] = " ".join(flags)

    sandbox_disabled = (
        target.get(_WEBENGINE_SANDBOX_ENVIRONMENT, "").strip().lower() in {"1", "true", "yes"}
        or _NO_SANDBOX_FLAG in flags
    )
    return MapRendererConfiguration(
        mode="chromium-software" if windows_default else "platform-default",
        windows_software_default=windows_default,
        chromium_gpu_disabled=_SOFTWARE_RENDERING_FLAG in flags,
        sandbox_disabled_by_environment=sandbox_disabled,
    )


def renderer_diagnostics(
    app: QApplication,
    configuration: MapRendererConfiguration,
) -> dict[str, object]:
    """Return bounded diagnostics without command lines, paths, or device identifiers."""

    return {
        **asdict(configuration),
        "qt_platform": app.platformName(),
        "qt_version": qVersion(),
        "sandbox_disabled_by_skywriter": False,
    }


def persist_renderer_diagnostics(
    app: QApplication,
    configuration: MapRendererConfiguration,
    *,
    directory: Path | None = None,
) -> Path:
    """Persist only the safe renderer facts needed for a later field report."""

    root = directory or Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / _DIAGNOSTIC_FILENAME
    path.write_text(
        json.dumps(renderer_diagnostics(app, configuration), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    LOGGER.info(
        "Mission map renderer configured",
        extra={
            "renderer_mode": configuration.mode,
            "chromium_gpu_disabled": configuration.chromium_gpu_disabled,
            "sandbox_disabled_by_environment": configuration.sandbox_disabled_by_environment,
        },
    )
    return path
