"""Headless Qt shell tests."""

from PySide6.QtWidgets import QTabWidget

from skywriter.application import ViewName
from skywriter.main import create_application, run
from skywriter.ui import MainWindow


def test_main_window_labels_all_foundation_views() -> None:
    create_application(["skywriter-test"])
    window = MainWindow()
    tabs = window.findChild(QTabWidget, "primaryViews")

    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Builder",
        "Preflight",
        "Flight",
    ]

    tabs.setCurrentIndex(1)
    assert window.snapshot.active_view is ViewName.PREFLIGHT
    window.close()


def test_shell_launches_and_closes() -> None:
    assert run(["skywriter-test"], close_after_ms=0) == 0
