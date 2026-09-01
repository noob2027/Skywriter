"""Application construction and process entry point."""

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from skywriter.config import DEFAULT_CONFIG
from skywriter.logging_config import configure_logging
from skywriter.ui import MainWindow
from skywriter.ui.map import MapReady

PACKAGED_SMOKE_TEST_ARGUMENT = "--packaged-smoke-test"
PACKAGED_SMOKE_TEST_ENVIRONMENT = "SKYWRITER_PACKAGED_SMOKE_TEST"
PACKAGED_SMOKE_EVIDENCE_ENVIRONMENT = "SKYWRITER_PACKAGED_SMOKE_EVIDENCE"
PACKAGED_SMOKE_TIMEOUT_MS = 15_000


def create_application(arguments: Sequence[str] | None = None) -> QApplication:
    """Return the process QApplication, creating it when necessary."""

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
) -> int:
    """Show the shell and run the Qt event loop.

    ``close_after_ms`` is an injectable test seam for a bounded smoke launch.
    """

    configure_logging()
    app = create_application(arguments)
    window = MainWindow()
    map_host = window.mission_workspace.builder.map_canvas

    if packaged_smoke:
        completed = False

        def finish_smoke(readiness: object) -> None:
            nonlocal completed
            if completed or not isinstance(readiness, MapReady):
                return
            completed = True
            _write_packaged_smoke_evidence(
                {
                    "ready": True,
                    "leaflet_version": readiness.leaflet_version,
                    "container_width_px": readiness.container_width_px,
                    "container_height_px": readiness.container_height_px,
                    "map_document": str(map_host.static_root / "map.html"),
                    "map_document_exists": (map_host.static_root / "map.html").is_file(),
                    "provider": map_host.tile_provider.value,
                    "working_directory": str(Path.cwd()),
                    "vehicle_io_blocked": os.environ.get(PACKAGED_SMOKE_TEST_ENVIRONMENT) == "1",
                }
            )
            window.close()
            app.exit(0)

        def fail_smoke() -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            _write_packaged_smoke_evidence(
                {
                    "ready": False,
                    "reason": "mounted map readiness timed out",
                    "map_document_exists": (map_host.static_root / "map.html").is_file(),
                    "working_directory": str(Path.cwd()),
                    "vehicle_io_blocked": os.environ.get(PACKAGED_SMOKE_TEST_ENVIRONMENT) == "1",
                }
            )
            window.close()
            app.exit(2)

        map_host.map_ready.connect(finish_smoke)
        QTimer.singleShot(PACKAGED_SMOKE_TIMEOUT_MS, fail_smoke)

    window.show()

    if packaged_smoke and map_host.readiness is not None:
        finish_smoke(map_host.readiness)

    if close_after_ms is not None:
        QTimer.singleShot(close_after_ms, window.close)
        QTimer.singleShot(close_after_ms, app.quit)

    return app.exec()


def main() -> int:
    """Run SKYWriter from its console-script or module entry point."""

    arguments = list(sys.argv)
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
