from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

from skywriter.application.prearm import (
    NativePrearmAssessment,
    PrearmReadinessSnapshot,
    PrearmRequestState,
)
from skywriter.application.telemetry import NativeStatusText
from skywriter.main import create_application
from skywriter.ui.preflight import (
    NativePrearmChecksRequested,
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
    assert request is not None and review is not None

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

    assert received == [
        NativePrearmChecksRequested(),
        PrearmReviewAcknowledgmentRequested(True),
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
