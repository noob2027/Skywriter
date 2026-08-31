from __future__ import annotations

from pathlib import Path
from threading import get_ident
from typing import Any

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLabel, QPushButton

from skywriter.application.auto_start import NativeAutoStartSnapshot, NativeAutoStartState
from skywriter.application.telemetry import NativeStatusText
from skywriter.main import create_application
from skywriter.ui.flight import FlightTelemetryWidget, NativeAutoStartRequested
from skywriter.ui.telemetry import NativeMessagesList


def child(widget: FlightTelemetryWidget, kind: type, name: str) -> Any:
    result = widget.findChild(kind, name)
    assert result is not None
    return result


def test_flight_emits_only_typed_native_auto_start_intent() -> None:
    create_application(["skywriter-task-102-intent"])
    widget = FlightTelemetryWidget()
    emitted = QSignalSpy(widget.intent_emitted)
    button = child(widget, QPushButton, "nativeAutoStartButton")
    widget.render_auto_start(
        NativeAutoStartSnapshot(
            state=NativeAutoStartState.IDLE,
            detail="ready",
            request_available=True,
        )
    )
    button.click()
    assert emitted.count() == 1
    assert isinstance(emitted.at(0)[0], NativeAutoStartRequested)
    assert not button.isEnabled()


@pytest.mark.parametrize(
    ("state", "detail", "status_text", "auto_time", "progress_time", "sequence"),
    [
        (NativeAutoStartState.PENDING, "waiting", "pending", None, None, None),
        (NativeAutoStartState.RUNNING, "confirmed", "Running", 100.3, 100.4, 2),
        (NativeAutoStartState.REJECTED, "native denied", "rejected", None, None, None),
        (NativeAutoStartState.TIMED_OUT, "No ACK", "uncertain", None, None, None),
        (NativeAutoStartState.LINK_LOST, "radio lost", "native", None, None, None),
        (NativeAutoStartState.UNEXPECTED_MODE, "Stabilize", "expected AUTO", None, None, None),
        (NativeAutoStartState.MISSION_MISMATCH, "sequence 9", "mismatched", None, None, None),
        (NativeAutoStartState.DISARMED, "disarmed", "not claimed", None, None, None),
    ],
)
def test_visible_start_states_are_distinct(
    state: NativeAutoStartState,
    detail: str,
    status_text: str,
    auto_time: float | None,
    progress_time: float | None,
    sequence: int | None,
) -> None:
    create_application(["skywriter-task-102-states"])
    widget = FlightTelemetryWidget()
    widget.render_auto_start(
        NativeAutoStartSnapshot(
            state=state,
            detail=detail,
            ack_result=0 if state is NativeAutoStartState.RUNNING else None,
            auto_observed_at_s=auto_time,
            progress_observed_at_s=progress_time,
            progress_sequence=sequence,
        )
    )
    status = child(widget, QLabel, "nativeAutoStartStatus")
    shown_detail = child(widget, QLabel, "nativeAutoStartDetail")
    button = child(widget, QPushButton, "nativeAutoStartButton")
    assert status_text.casefold() in status.text().casefold()
    assert detail in shown_detail.text()
    assert not button.isEnabled()


def test_start_operation_runs_on_worker_and_double_click_submits_once() -> None:
    application = create_application(["skywriter-task-102-worker"])
    widget = FlightTelemetryWidget()
    button = child(widget, QPushButton, "nativeAutoStartButton")
    calls = 0
    ui_thread = get_ident()
    operation_thread: int | None = None

    def operation() -> NativeAutoStartSnapshot:
        nonlocal calls, operation_thread
        calls += 1
        operation_thread = get_ident()
        return NativeAutoStartSnapshot(
            state=NativeAutoStartState.REJECTED,
            detail="native rejection",
        )

    worker = widget.bind_native_auto_start_operation(operation)
    completed = QSignalSpy(worker.snapshot_ready)
    widget.render_auto_start(
        NativeAutoStartSnapshot(
            state=NativeAutoStartState.IDLE,
            detail="ready",
            request_available=True,
        )
    )
    button.click()
    button.click()
    assert worker.busy
    assert QThreadPool.globalInstance().waitForDone(1000)
    application.processEvents()
    assert calls == 1
    assert completed.count() == 1
    assert operation_thread is not None and operation_thread != ui_thread
    assert widget.auto_start_snapshot.state is NativeAutoStartState.REJECTED


def test_native_rejection_text_is_visible_beside_receive_only_telemetry() -> None:
    create_application(["skywriter-task-102-native-text"])
    widget = FlightTelemetryWidget()
    widget.render_auto_start(
        NativeAutoStartSnapshot(
            state=NativeAutoStartState.REJECTED,
            detail="native rejection",
            ack_result=2,
            native_messages=(NativeStatusText(2, "Flight mode change failed", 0, 0, 100.0),),
        )
    )
    messages = widget.findChild(NativeMessagesList)
    assert messages is not None
    assert messages.count() == 1
    assert "Flight mode change failed" in messages.item(0).text()


def test_flight_ui_contains_only_approved_through_task104_controls() -> None:
    create_application(["skywriter-task-103-confinement"])
    widget = FlightTelemetryWidget()
    buttons = {button.objectName() for button in widget.findChildren(QPushButton)}
    assert buttons == {
        "nativeAutoStartButton",
        "nativePauseButton",
        "nativeResumeButton",
        "landHereNowButton",
        "landHereNowConfirmButton",
        "landHereNowCancelButton",
    }
    source = Path(__file__).parents[3] / "src/skywriter/ui/flight.py"
    text = source.read_text(encoding="utf-8").casefold()
    for prohibited in (
        "return to launch",
        "parameter write",
        "generic command",
    ):
        assert prohibited not in text
