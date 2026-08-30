from __future__ import annotations

from threading import get_ident
from typing import Any

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLabel, QPushButton

from skywriter.application.pause_resume import (
    NativePauseResumeSnapshot,
    NativePauseResumeState,
)
from skywriter.application.telemetry import NativeStatusText
from skywriter.main import create_application
from skywriter.ui.flight import (
    FlightTelemetryWidget,
    NativePauseRequested,
    NativeResumeRequested,
)
from skywriter.ui.telemetry import NativeMessagesList


def child(widget: FlightTelemetryWidget, kind: type, name: str) -> Any:
    result = widget.findChild(kind, name)
    assert result is not None
    return result


def running_snapshot() -> NativePauseResumeSnapshot:
    return NativePauseResumeSnapshot(
        state=NativePauseResumeState.RUNNING,
        detail="current Active telemetry",
        pause_available=True,
        state_observed_at_s=100.0,
        progress_sequence=3,
    )


def paused_snapshot() -> NativePauseResumeSnapshot:
    return NativePauseResumeSnapshot(
        state=NativePauseResumeState.PAUSED,
        detail="current Paused telemetry",
        resume_available=True,
        state_observed_at_s=100.2,
        progress_sequence=3,
    )


def test_flight_emits_only_typed_pause_and_resume_intents() -> None:
    create_application(["skywriter-task-103-intents"])
    widget = FlightTelemetryWidget()
    emitted = QSignalSpy(widget.intent_emitted)
    pause = child(widget, QPushButton, "nativePauseButton")
    resume = child(widget, QPushButton, "nativeResumeButton")

    widget.render_pause_resume(running_snapshot())
    assert pause.isEnabled()
    assert not resume.isEnabled()
    pause.click()
    assert isinstance(emitted.at(0)[0], NativePauseRequested)
    assert not pause.isEnabled() and not resume.isEnabled()

    widget.render_pause_resume(paused_snapshot())
    assert not pause.isEnabled()
    assert resume.isEnabled()
    resume.click()
    assert isinstance(emitted.at(1)[0], NativeResumeRequested)
    assert not pause.isEnabled() and not resume.isEnabled()


@pytest.mark.parametrize(
    ("state", "detail", "status_text"),
    [
        (NativePauseResumeState.PAUSE_PENDING, "waiting pause", "Pause pending"),
        (NativePauseResumeState.RESUME_PENDING, "waiting resume", "Resume pending"),
        (NativePauseResumeState.REJECTED, "native denied", "rejected"),
        (NativePauseResumeState.TIMED_OUT, "No ACK", "uncertain"),
        (NativePauseResumeState.LINK_LOST, "radio lost", "native"),
        (NativePauseResumeState.UNEXPECTED_MODE, "Loiter", "AUTO"),
        (NativePauseResumeState.MISSION_COMPLETED, "complete", "Complete"),
        (NativePauseResumeState.LANDING, "landing", "Landing"),
        (NativePauseResumeState.DISARMED, "disarmed", "Disarmed"),
        (NativePauseResumeState.MISSION_MISMATCH, "sequence 9", "mismatched"),
    ],
)
def test_visible_pause_resume_states_are_distinct(
    state: NativePauseResumeState,
    detail: str,
    status_text: str,
) -> None:
    create_application(["skywriter-task-103-states"])
    widget = FlightTelemetryWidget()
    widget.render_pause_resume(NativePauseResumeSnapshot(state=state, detail=detail))
    status = child(widget, QLabel, "nativePauseResumeStatus")
    shown_detail = child(widget, QLabel, "nativePauseResumeDetail")
    pause = child(widget, QPushButton, "nativePauseButton")
    resume = child(widget, QPushButton, "nativeResumeButton")
    assert status_text.casefold() in status.text().casefold()
    assert detail in shown_detail.text()
    assert not pause.isEnabled() and not resume.isEnabled()


def test_pause_resume_operations_share_one_worker_and_reject_overlap() -> None:
    application = create_application(["skywriter-task-103-worker"])
    widget = FlightTelemetryWidget()
    pause = child(widget, QPushButton, "nativePauseButton")
    resume = child(widget, QPushButton, "nativeResumeButton")
    pause_calls = 0
    resume_calls = 0
    ui_thread = get_ident()
    operation_thread: int | None = None

    def pause_operation() -> NativePauseResumeSnapshot:
        nonlocal pause_calls, operation_thread
        pause_calls += 1
        operation_thread = get_ident()
        return paused_snapshot()

    def resume_operation() -> NativePauseResumeSnapshot:
        nonlocal resume_calls
        resume_calls += 1
        return running_snapshot()

    worker = widget.bind_native_pause_resume_operations(pause_operation, resume_operation)
    completed = QSignalSpy(worker.snapshot_ready)
    widget.render_pause_resume(running_snapshot())
    pause.click()
    assert worker.busy
    assert not worker.submit_resume()
    resume.click()
    assert QThreadPool.globalInstance().waitForDone(1000)
    application.processEvents()
    assert pause_calls == 1
    assert resume_calls == 0
    assert completed.count() == 1
    assert operation_thread is not None and operation_thread != ui_thread
    assert widget.pause_resume_snapshot.state is NativePauseResumeState.PAUSED


def test_native_pause_rejection_text_is_visible_beside_telemetry() -> None:
    create_application(["skywriter-task-103-native-text"])
    widget = FlightTelemetryWidget()
    widget.render_pause_resume(
        NativePauseResumeSnapshot(
            state=NativePauseResumeState.REJECTED,
            detail="native rejection",
            ack_result=4,
            native_messages=(NativeStatusText(6, "Failed to pause", 0, 0, 100.0),),
        )
    )
    messages = widget.findChild(NativeMessagesList)
    assert messages is not None
    assert messages.count() == 1
    assert "Failed to pause" in messages.item(0).text()
