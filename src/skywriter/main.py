"""Application construction and process entry point."""

import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from skywriter.config import DEFAULT_CONFIG
from skywriter.infrastructure.serial_ports import (
    SerialPortInfo,
    StaticSerialPortEnumerator,
)
from skywriter.logging_config import configure_logging
from skywriter.packaged_runtime_smoke import (
    PACKAGED_SERIAL_IMPORT_SMOKE_ARGUMENT,
    run_packaged_serial_import_smoke,
)
from skywriter.ui import MainWindow
from skywriter.ui.map import MapReady, ProviderState, ProviderStatusChanged, TileProvider
from skywriter.ui.map.rendering import (
    MapRendererConfiguration,
    configure_map_renderer,
    persist_renderer_diagnostics,
    renderer_diagnostics,
)
from skywriter.ui.map.visual import inspect_map_surface
from skywriter.ui.offline_workspace import OfflineMissionWorkspace

PACKAGED_SMOKE_TEST_ARGUMENT = "--packaged-smoke-test"
PACKAGED_VISUAL_SMOKE_TEST_ARGUMENT = "--packaged-map-visual-smoke"
PACKAGED_UI_ACCEPTANCE_ARGUMENT = "--packaged-ui-acceptance"
PACKAGED_SMOKE_TEST_ENVIRONMENT = "SKYWRITER_PACKAGED_SMOKE_TEST"
PACKAGED_SMOKE_EVIDENCE_ENVIRONMENT = "SKYWRITER_PACKAGED_SMOKE_EVIDENCE"
PACKAGED_SMOKE_SCREENSHOT_ENVIRONMENT = "SKYWRITER_PACKAGED_SMOKE_SCREENSHOT"
PACKAGED_UI_ACCEPTANCE_MODE_ENVIRONMENT = "SKYWRITER_PACKAGED_UI_ACCEPTANCE"
PACKAGED_UI_ACCEPTANCE_EVIDENCE_ENVIRONMENT = "SKYWRITER_INSTALLED_UI_EVIDENCE"
PACKAGED_SMOKE_TIMEOUT_MS = 15_000
LOGGER = logging.getLogger("skywriter.main")
_renderer_configuration: MapRendererConfiguration | None = None


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    """Return the process QApplication, creating it when necessary."""

    global _renderer_configuration
    _renderer_configuration = configure_map_renderer()
    existing = QCoreApplication.instance()
    if existing is not None:
        return cast(QApplication, existing)

    app = QApplication(list(arguments) if arguments is not None else sys.argv)
    app.setApplicationName(DEFAULT_CONFIG.name)
    app.setApplicationVersion(DEFAULT_CONFIG.version)
    app.setOrganizationName(DEFAULT_CONFIG.organization)
    return app


def run(
    arguments: Sequence[str] | None = None,
    *,
    close_after_ms: int | None = None,
    packaged_smoke: bool = False,
    packaged_visual_smoke: bool = False,
    packaged_ui_acceptance: bool = False,
) -> int:
    """Show the shell and run the Qt event loop.

    ``close_after_ms`` is an injectable test seam for a bounded smoke launch.
    """

    configure_logging()
    app = create_application(arguments)
    configuration = _renderer_configuration or configure_map_renderer()
    try:
        diagnostic_path = persist_renderer_diagnostics(app, configuration)
    except OSError as error:
        LOGGER.warning("Could not persist map renderer diagnostics: %s", error)
        diagnostic_path = None
    if packaged_ui_acceptance:
        acceptance_root_value = os.environ.get(PACKAGED_UI_ACCEPTANCE_EVIDENCE_ENVIRONMENT)
        if not acceptance_root_value:
            raise RuntimeError("installed UI acceptance requires an evidence directory")
        acceptance_root = Path(acceptance_root_value).resolve()
        safe_temp = acceptance_root / "safe-temp"
        safe_temp.mkdir(parents=True, exist_ok=True)
        mission_path = safe_temp / "accepted-mission.json"
        workspace = OfflineMissionWorkspace(
            save_path_picker=lambda _initial: mission_path,
            load_path_picker=lambda: mission_path,
        )
        window = MainWindow(
            mission_workspace=workspace,
            serial_port_enumerator=StaticSerialPortEnumerator(
                (
                    SerialPortInfo(
                        "COM42",
                        "SKYWriter installed-acceptance serial fixture",
                        "Hardware-blocked test inventory",
                    ),
                )
            ),
        )
    else:
        acceptance_root = None
        window = MainWindow()
    map_host = window.mission_workspace.builder.map_canvas

    if packaged_smoke:
        completed = False
        visual_capture_attempts = 0

        def common_evidence(readiness: MapReady | None) -> dict[str, object]:
            provider = map_host.provider_status
            evidence: dict[str, object] = {
                "map_document": str(map_host.static_root / "map.html"),
                "map_document_exists": (map_host.static_root / "map.html").is_file(),
                "provider": map_host.tile_provider.value,
                "provider_state": provider.state.value,
                "requested_tiles": provider.requested_tiles,
                "loaded_tiles": provider.loaded_tiles,
                "error_tiles": provider.error_tiles,
                "pending_tiles": provider.pending_tiles,
                "working_directory": str(Path.cwd()),
                "vehicle_io_blocked": os.environ.get(PACKAGED_SMOKE_TEST_ENVIRONMENT) == "1",
                "renderer": renderer_diagnostics(app, configuration),
                "renderer_diagnostic_file": (
                    str(diagnostic_path) if diagnostic_path is not None else None
                ),
            }
            if readiness is not None:
                evidence.update(
                    {
                        "leaflet_version": readiness.leaflet_version,
                        "container_width_px": readiness.container_width_px,
                        "container_height_px": readiness.container_height_px,
                    }
                )
            return evidence

        def finish_readiness_smoke(readiness: object) -> None:
            nonlocal completed
            if completed or not isinstance(readiness, MapReady):
                return
            completed = True
            _write_packaged_smoke_evidence(
                {
                    **common_evidence(readiness),
                    "ready": True,
                    "visual_ready": None,
                }
            )
            window.close()
            app.exit(0)

        def capture_visual_surface() -> None:
            nonlocal visual_capture_attempts
            visual_capture_attempts += 1
            script = """
                JSON.stringify((() => {
                  const control = document.querySelector('.leaflet-control-zoom');
                  const rect = control?.getBoundingClientRect();
                  return {
                    leaflet_controls_dom: Boolean(control),
                    zoom_control_rect: rect ? {
                      left: rect.left, top: rect.top,
                      right: rect.right, bottom: rect.bottom
                    } : null,
                    loaded_tile_elements:
                      document.querySelectorAll('.leaflet-tile-loaded').length
                  };
                })())
            """

            def finish_capture(value: object) -> None:
                nonlocal completed
                if completed:
                    return
                try:
                    parsed_dom = json.loads(value) if isinstance(value, str) else {}
                except json.JSONDecodeError:
                    parsed_dom = {}
                dom = cast(dict[str, object], parsed_dom) if isinstance(parsed_dom, dict) else {}
                pixmap = map_host.grab()
                screenshot_path_value = os.environ.get(PACKAGED_SMOKE_SCREENSHOT_ENVIRONMENT)
                screenshot_saved = False
                if screenshot_path_value:
                    screenshot_path = Path(screenshot_path_value)
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    screenshot_saved = pixmap.save(str(screenshot_path), "PNG")
                surface = inspect_map_surface(pixmap.toImage(), dom)
                ready = surface.visual_ready and screenshot_saved
                if not ready:
                    QTimer.singleShot(250, capture_visual_surface)
                    return
                completed = True
                _write_packaged_smoke_evidence(
                    {
                        **common_evidence(map_host.readiness),
                        **surface.as_dict(),
                        "ready": ready,
                        "capture_method": "QWebEngineView.grab",
                        "capture_attempts": visual_capture_attempts,
                        "loaded_tile_elements": dom.get("loaded_tile_elements", 0),
                        "screenshot_saved": screenshot_saved,
                    }
                )
                window.close()
                app.exit(0 if ready else 3)

            map_host.page().runJavaScript(script, finish_capture)

        def begin_visual_smoke(readiness: object) -> None:
            if completed or not isinstance(readiness, MapReady):
                return
            map_host.set_tile_provider(TileProvider.OPENSTREETMAP)

        def observe_visual_provider(value: object) -> None:
            if (
                completed
                or not isinstance(value, ProviderStatusChanged)
                or value.state is not ProviderState.ONLINE
                or value.loaded_tiles <= 0
                or value.pending_tiles != 0
            ):
                return
            QTimer.singleShot(500, capture_visual_surface)

        def fail_smoke() -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            _write_packaged_smoke_evidence(
                {
                    **common_evidence(map_host.readiness),
                    "ready": False,
                    "visual_ready": False if packaged_visual_smoke else None,
                    "reason": (
                        "rendered map surface or controlled tiles timed out"
                        if packaged_visual_smoke
                        else "mounted map readiness timed out"
                    ),
                }
            )
            window.close()
            app.exit(2)

        if packaged_visual_smoke:
            map_host.map_ready.connect(begin_visual_smoke)
            map_host.provider_status_changed.connect(observe_visual_provider)
        else:
            map_host.map_ready.connect(finish_readiness_smoke)
        QTimer.singleShot(PACKAGED_SMOKE_TIMEOUT_MS, fail_smoke)

    window.show()

    if packaged_ui_acceptance:
        assert acceptance_root is not None

        def finish_installed_acceptance() -> None:
            from skywriter.ui.installed_acceptance import execute_installed_ui_acceptance

            passed = execute_installed_ui_acceptance(window, acceptance_root)
            window.close()
            app.exit(0 if passed else 4)

        QTimer.singleShot(0, finish_installed_acceptance)

    if packaged_smoke and map_host.readiness is not None:
        if packaged_visual_smoke:
            begin_visual_smoke(map_host.readiness)
        else:
            finish_readiness_smoke(map_host.readiness)

    if close_after_ms is not None:
        QTimer.singleShot(close_after_ms, window.close)
        QTimer.singleShot(close_after_ms, app.quit)

    return app.exec()


def main() -> int:
    """Run SKYWriter from its console-script or module entry point."""

    arguments = list(sys.argv)
    packaged_ui_acceptance = (
        PACKAGED_UI_ACCEPTANCE_ARGUMENT in arguments
        or os.environ.get(PACKAGED_UI_ACCEPTANCE_MODE_ENVIRONMENT) == "1"
    )
    if PACKAGED_UI_ACCEPTANCE_ARGUMENT in arguments:
        arguments.remove(PACKAGED_UI_ACCEPTANCE_ARGUMENT)
    if packaged_ui_acceptance:
        os.environ[PACKAGED_SMOKE_TEST_ENVIRONMENT] = "1"
        return run(arguments, packaged_ui_acceptance=True)
    if PACKAGED_SERIAL_IMPORT_SMOKE_ARGUMENT in arguments:
        os.environ[PACKAGED_SMOKE_TEST_ENVIRONMENT] = "1"
        return run_packaged_serial_import_smoke(arguments)
    if PACKAGED_VISUAL_SMOKE_TEST_ARGUMENT in arguments:
        arguments.remove(PACKAGED_VISUAL_SMOKE_TEST_ARGUMENT)
        os.environ[PACKAGED_SMOKE_TEST_ENVIRONMENT] = "1"
        return run(arguments, packaged_smoke=True, packaged_visual_smoke=True)
    if PACKAGED_SMOKE_TEST_ARGUMENT in arguments:
        arguments.remove(PACKAGED_SMOKE_TEST_ARGUMENT)
        # The explicit packaging smoke mode is both bounded and fail-closed at the
        # MAVLink open boundary. It is never selected by a normal shortcut launch.
        os.environ[PACKAGED_SMOKE_TEST_ENVIRONMENT] = "1"
        return run(arguments, packaged_smoke=True)
    return run(arguments)


def _write_packaged_smoke_evidence(evidence: dict[str, object]) -> None:
    path_value = os.environ.get(PACKAGED_SMOKE_EVIDENCE_ENVIRONMENT)
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
