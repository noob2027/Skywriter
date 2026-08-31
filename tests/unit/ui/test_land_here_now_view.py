from __future__ import annotations

from threading import get_ident
from typing import Any

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from skywriter.application.land_here_now import (
    MAV_LANDED_STATE_IN_AIR,
    NativeLandHereNowAuthorization,
    NativeLandHereNowSnapshot,
    NativeLandHereNowState,
)
from skywriter.application.pause_resume import MAV_LANDED_STATE_LANDING
from skywriter.application.telemetry import NativeStatusText
from skywriter.main import create_application
from skywriter.ui.flight import (
    FlightTelemetryWidget,
    LandHereNowCancelled,
    LandHereNowConfirmationRequested,
    LandHereNowConfirmed,
)
from skywriter.ui.telemetry import NativeMessagesList


def child(widget: FlightTelemetryWidget, kind: type, name: str) -> Any:
    result = widget.findChild(kind, name)
    assert result is not None
    return result


def authorization() -> NativeLandHereNowAuthorization:
    return NativeLandHereNowAuthorization(
        vehicle_identity="mavlink-system-1-component-1",
        system_id=1,
        component_id=1,
        mission_revision=8,
        expected_mission_digest="d" * 64,
        auto_start_revision=5,
        first_executable_sequence=1,
        last_sequence=7,
        progress_sequence=3,
        mission_state=3,
    )


def available() -> NativeLandHereNowSnapshot:
    return NativeLandHereNowSnapshot(
        state=NativeLandHereNowState.AVAILABLE,
        detail="airborne mission verified",
        authorization=authorization(),
        request_available=True,
    )


def confirming() -> NativeLandHereNowSnapshot:
    return NativeLandHereNowSnapshot(
        state=NativeLandHereNowState.CONFIRMATION_REQUIRED,
        detail="abandon all remaining mission progress and land at the current location",
        authorization=authorization(),
        confirm_available=True,
        cancel_available=True,
        confirmation_requested_at_s=100.0,
    )


def landing() -> NativeLandHereNowSnapshot:
    return NativeLandHereNowSnapshot(
        state=NativeLandHereNowState.LANDING,
        detail="ACK and later native landing telemetry confirmed",
        authorization=authorization(),
        ack_result=0,
        requested_at_s=100.1,
        completed_at_s=100.5,
        land_mode_observed_at_s=100.3,
        landed_state_observed_at_s=100.4,
        landed_state=MAV_LANDED_STATE_LANDING,
    )


def test_panel_is_visibly_separate_from_planned_clicked_land() -> None:
    create_application(["skywriter-task-104-warning"])
    widget = FlightTelemetryWidget()
    panel = child(widget, QWidget, "nativeLandHereNowPanel")
    heading = child(widget, QLabel, "nativeLandHereNowHeading")
    warning = child(widget, QLabel, "nativeLandHereNowWarning")
    confirmation_warning = child(widget, QLabel, "landHereNowConfirmationWarning")
    assert panel is not None
    assert "current aircraft location" in heading.text()
    assert "NOT THE PLANNED CLICKED LAND POINT" in warning.text()
    assert "abandons all remaining mission progress" in warning.text()
    assert "abandon the remaining mission" in confirmation_warning.text()


def test_confirmation_and_cancel_are_application_owned_and_send_no_worker_request() -> None:
    create_application(["skywriter-task-104-confirm-cancel"])
    widget = FlightTelemetryWidget()
    emitted = QSignalSpy(widget.intent_emitted)
    begin_calls = 0
    cancel_calls = 0
    confirm_calls = 0

    def begin() -> NativeLandHereNowSnapshot:
        nonlocal begin_calls
        begin_calls += 1
        return confirming()

    def cancel() -> NativeLandHereNowSnapshot:
        nonlocal cancel_calls
        cancel_calls += 1
        return NativeLandHereNowSnapshot(
            state=NativeLandHereNowState.CONFIRMATION_CANCELLED,
            detail="cancelled; no vehicle command was sent",
        )

    def confirm() -> NativeLandHereNowSnapshot:
        nonlocal confirm_calls
        confirm_calls += 1
        return landing()

    worker = widget.bind_land_here_now_operations(begin, cancel, confirm)
    widget.render_land_here_now(available())
    child(widget, QPushButton, "landHereNowButton").click()
    assert begin_calls == 1 and cancel_calls == 0 and confirm_calls == 0
    assert isinstance(emitted.at(0)[0], LandHereNowConfirmationRequested)
    assert widget.land_here_now_snapshot.state is NativeLandHereNowState.CONFIRMATION_REQUIRED
    assert child(widget, QWidget, "landHereNowConfirmation").isVisibleTo(widget)
    child(widget, QPushButton, "landHereNowCancelButton").click()
    assert begin_calls == 1 and cancel_calls == 1 and confirm_calls == 0
    assert not worker.busy
    assert isinstance(emitted.at(1)[0], LandHereNowCancelled)
    assert widget.land_here_now_snapshot.state is NativeLandHereNowState.CONFIRMATION_CANCELLED


def test_confirm_runs_off_ui_thread_and_rejects_overlap() -> None:
    application = create_application(["skywriter-task-104-worker"])
    widget = FlightTelemetryWidget()
    emitted = QSignalSpy(widget.intent_emitted)
    confirm_calls = 0
    operation_thread: int | None = None
    ui_thread = get_ident()

    def confirm() -> NativeLandHereNowSnapshot:
        nonlocal confirm_calls, operation_thread
        confirm_calls += 1
        operation_thread = get_ident()
        return landing()

    worker = widget.bind_land_here_now_operations(confirming, available, confirm)
    widget.render_land_here_now(confirming())
    child(widget, QPushButton, "landHereNowConfirmButton").click()
    assert worker.busy
    assert not worker.submit()
    assert not child(widget, QPushButton, "landHereNowButton").isEnabled()
    assert not child(widget, QPushButton, "landHereNowConfirmButton").isEnabled()
    assert not child(widget, QPushButton, "landHereNowCancelButton").isEnabled()
    assert QThreadPool.globalInstance().waitForDone(1000)
    application.processEvents()
    assert confirm_calls == 1
    assert operation_thread is not None and operation_thread != ui_thread
    assert isinstance(emitted.at(0)[0], LandHereNowConfirmed)
    assert widget.land_here_now_snapshot.state is NativeLandHereNowState.LANDING


@pytest.mark.parametrize(
    ("state", "detail", "status_text"),
    [
        (NativeLandHereNowState.PENDING, "waiting", "pending"),
        (NativeLandHereNowState.REJECTED, "native denied", "rejected"),
        (NativeLandHereNowState.TIMED_OUT, "no ACK", "uncertain"),
        (NativeLandHereNowState.LINK_LOST, "radio lost", "native"),
        (
            NativeLandHereNowState.ACKNOWLEDGED_NO_LANDING_TELEMETRY,
            "no proof",
            "absent",
        ),
        (NativeLandHereNowState.TELEMETRY_DISAGREEMENT, "Auto", "disagree"),
        (NativeLandHereNowState.ALREADY_LANDING, "Landing", "already Landing"),
        (NativeLandHereNowState.ALREADY_LANDED, "On Ground", "already Landed"),
        (NativeLandHereNowState.CONFIRMATION_CANCELLED, "no command", "cancelled"),
        (NativeLandHereNowState.DISARMED, "disarmed", "Disarmed"),
        (NativeLandHereNowState.MISSION_COMPLETED, "complete", "Complete"),
    ],
)
def test_visible_terminal_states_are_distinct_and_controls_are_disabled(
    state: NativeLandHereNowState,
    detail: str,
    status_text: str,
) -> None:
    create_application(["skywriter-task-104-states"])
    widget = FlightTelemetryWidget()
    widget.render_land_here_now(NativeLandHereNowSnapshot(state=state, detail=detail))
    assert (
        status_text.casefold() in child(widget, QLabel, "nativeLandHereNowStatus").text().casefold()
    )
    assert detail in child(widget, QLabel, "nativeLandHereNowDetail").text()
    assert not child(widget, QPushButton, "landHereNowButton").isEnabled()
    assert not child(widget, QPushButton, "landHereNowConfirmButton").isEnabled()
    assert not child(widget, QPushButton, "landHereNowCancelButton").isEnabled()


def test_native_land_rejection_text_is_visible_beside_telemetry() -> None:
    create_application(["skywriter-task-104-native-text"])
    widget = FlightTelemetryWidget()
    widget.render_land_here_now(
        NativeLandHereNowSnapshot(
            state=NativeLandHereNowState.REJECTED,
            detail="native rejection",
            ack_result=4,
            landed_state=MAV_LANDED_STATE_IN_AIR,
            native_messages=(NativeStatusText(6, "Landing denied", 0, 0, 100.0),),
        )
    )
    messages = widget.findChild(NativeMessagesList)
    assert messages is not None
    assert messages.count() == 1
    assert "Landing denied" in messages.item(0).text()
