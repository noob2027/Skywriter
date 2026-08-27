from __future__ import annotations

from pathlib import Path
from threading import get_ident
from typing import cast

from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

from skywriter.application.arm import NormalArmSnapshot, NormalArmState
from skywriter.application.prearm import (
    NativePrearmAssessment,
    PrearmReadinessSnapshot,
    PrearmRequestState,
)
from skywriter.application.telemetry import NativeStatusText
from skywriter.main import create_application
from skywriter.ui.preflight import (
    NativePrearmChecksRequested,
    NormalArmRequested,
    PrearmReviewAcknowledgmentRequested,
    PreflightIntent,
    PreflightTelemetryWidget,
)


def test_preflight_emits_only_typed_request_and_review_intents() -> None:
    create_application(["skywriter-task-100-preflight-intents"])
    widget = PreflightTelemetryWidget()
    received: list[PreflightIntent] = []
    widget.intent_emitted.connect(lambda value: received.append(cast(PreflightIntent, value)))
    request = widget.findChild(QPushButton, "requestNativePrearmButton")
    review = widget.findChild(QCheckBox, "acknowledgeNativePrearmReview")
    arm = widget.findChild(QPushButton, "normalArmButton")
    assert request is not None and review is not None and arm is not None

    request.click()
    request.click()
    assert not request.isEnabled()
    widget.render_readiness(
        PrearmReadinessSnapshot(
            request_state=PrearmRequestState.ACCEPTED,
            detail="accepted",
            native_assessment=NativePrearmAssessment.HEALTHY,
        ),
        now_s=100.0,
    )
    review.click()
    widget.render_arm(
        NormalArmSnapshot(
            state=NormalArmState.IDLE,
            detail="ready",
            request_available=True,
        )
    )
    arm.click()

    assert received == [
        NativePrearmChecksRequested(),
        PrearmReviewAcknowledgmentRequested(True),
        NormalArmRequested(),
    ]


def test_preflight_distinguishes_pending_repeated_failure_and_reviewed_states() -> None:
    create_application(["skywriter-task-100-preflight-states"])
    widget = PreflightTelemetryWidget()
    request = widget.findChild(QPushButton, "requestNativePrearmButton")
    review = widget.findChild(QCheckBox, "acknowledgeNativePrearmReview")
    status = widget.findChild(QLabel, "nativePrearmRequestStatus")
    detail = widget.findChild(QLabel, "nativePrearmRequestDetail")
    assessment = widget.findChild(QLabel, "nativePrearmAssessment")
    application_gate = widget.findChild(QLabel, "nativePrearmApplicationGate")
    assert all(
        value is not None
        for value in (request, review, status, detail, assessment, application_gate)
    )
    assert request is not None and review is not None
    assert status is not None and detail is not None and assessment is not None
    assert application_gate is not None

    widget.render_readiness(
        PrearmReadinessSnapshot(
            request_state=PrearmRequestState.PENDING,
            detail="waiting",
            repeated_request_ignored=True,
        ),
        now_s=100.0,
    )
    assert not request.isEnabled()
    assert "Repeated request ignored" in detail.text()
    assert not review.isEnabled()
    assert "Blocked" in application_gate.text()

    widget.render_readiness(
        PrearmReadinessSnapshot(
            request_state=PrearmRequestState.ACCEPTED,
            detail="handled, not arm approval",
            native_assessment=NativePrearmAssessment.CONFLICTING,
            native_messages=(NativeStatusText(2, "PreArm: GPS not healthy", 0, 0, 100.1),),
        ),
        now_s=100.2,
    )
    assert request.isEnabled()
    assert review.isEnabled()
    assert "not arm approval" in detail.text()
    assert "disagree" in assessment.text()

    widget.render_readiness(
        PrearmReadinessSnapshot(
            request_state=PrearmRequestState.ACCEPTED,
            detail="handled, not arm approval",
            native_assessment=NativePrearmAssessment.HEALTHY,
            review_acknowledged=True,
        ),
        now_s=100.2,
    )
    assert review.isChecked()
    assert "ArduCopter may still reject" in assessment.text()
    assert "Reviewed" in application_gate.text()
    assert "not proof ArduCopter will arm" in application_gate.text()


def test_preflight_contains_no_later_command_controls_or_blocking_gateway_import() -> None:
    create_application(["skywriter-task-100-preflight-confinement"])
    widget = PreflightTelemetryWidget()
    labels = " ".join(button.text().casefold() for button in widget.findChildren(QPushButton))
    assert "request native pre-arm checks" in labels
    assert "arm normally" in labels
    for prohibited in (
        "force",
        "disarm",
        "auto",
        "pause",
        "resume",
        "land",
        "rtl",
        "parameter",
        "mode",
    ):
        assert prohibited not in labels
    source = (Path(__file__).parents[3] / "src/skywriter/ui/preflight.py").read_text(
        encoding="utf-8"
    )
    assert "skywriter.infrastructure" not in source
    assert "request_prearm_checks(" not in source
    assert "request_normal_arm(" not in source


def test_arm_states_distinguish_pending_armed_rejected_and_uncertain_results() -> None:
    create_application(["skywriter-task-101-arm-states"])
    widget = PreflightTelemetryWidget()
    button = widget.findChild(QPushButton, "normalArmButton")
    status = widget.findChild(QLabel, "normalArmStatus")
    detail = widget.findChild(QLabel, "normalArmDetail")
    assert button is not None and status is not None and detail is not None

    states = (
        (NormalArmState.PENDING, "pending", "controls locked", None),
        (NormalArmState.ARMED, "confirmed", "Armed", 100.2),
        (NormalArmState.REJECTED, "PreArm: GPS", "rejected", None),
        (NormalArmState.TIMED_OUT, "No ACK", "uncertain", None),
        (NormalArmState.LINK_LOST, "radio lost", "uncertain", None),
        (NormalArmState.TELEMETRY_DISAGREEMENT, "still disarmed", "disagree", None),
    )
    for state, state_detail, expected_status, armed_observed_at_s in states:
        widget.render_arm(
            NormalArmSnapshot(
                state=state,
                detail=state_detail,
                request_available=False,
                armed_observed_at_s=armed_observed_at_s,
            )
        )
        assert expected_status.casefold() in status.text().casefold()
        assert state_detail in detail.text()
        assert not button.isEnabled()


def test_normal_arm_operation_runs_on_worker_and_double_click_submits_once() -> None:
    application = create_application(["skywriter-task-101-arm-worker"])
    widget = PreflightTelemetryWidget()
    button = widget.findChild(QPushButton, "normalArmButton")
    assert button is not None
    calls = 0
    ui_thread = get_ident()
    operation_thread: int | None = None

    def operation() -> NormalArmSnapshot:
        nonlocal calls, operation_thread
        calls += 1
        operation_thread = get_ident()
        return NormalArmSnapshot(
            state=NormalArmState.REJECTED,
            detail="native rejection",
        )

    worker = widget.bind_normal_arm_operation(operation)
    completed = QSignalSpy(worker.snapshot_ready)
    widget.render_arm(
        NormalArmSnapshot(
            state=NormalArmState.IDLE,
            detail="ready",
            request_available=True,
        )
    )
    button.click()
    button.click()
    assert not button.isEnabled()
    assert worker.busy
    assert QThreadPool.globalInstance().waitForDone(1000)
    application.processEvents()

    assert calls == 1
    assert completed.count() == 1
    assert operation_thread is not None and operation_thread != ui_thread
    assert not worker.busy
    assert widget.arm_snapshot.state is NormalArmState.REJECTED
