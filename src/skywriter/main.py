"""Application construction and process entry point."""

import sys
from collections.abc import Sequence
from typing import cast

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from skywriter.config import DEFAULT_CONFIG
from skywriter.logging_config import configure_logging
from skywriter.ui import MainWindow


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


def run(arguments: Sequence[str] | None = None, *, close_after_ms: int | None = None) -> int:
    """Show the shell and run the Qt event loop.

    ``close_after_ms`` is an injectable test seam for a bounded smoke launch.
    """

    configure_logging()
    app = create_application(arguments)
    window = MainWindow()
    window.show()

    if close_after_ms is not None:
        QTimer.singleShot(close_after_ms, window.close)
        QTimer.singleShot(close_after_ms, app.quit)

    return app.exec()


def main() -> int:
    """Run SKYWriter from its console-script or module entry point."""

    return run()
