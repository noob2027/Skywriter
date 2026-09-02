"""Task 100 preflight telemetry and native readiness-review presentation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from skywriter.application.arm import NormalArmSnapshot, NormalArmState
from skywriter.application.prearm import (
    NativePrearmAssessment,
    PrearmReadinessSnapshot,
    PrearmRequestState,
)
from skywriter.application.telemetry import TelemetryFreshness, TelemetrySnapshot
from skywriter.ui.arm_worker import NormalArmWorkerHandoff
from skywriter.ui.telemetry import NativeMessagesList, TelemetryCard, render_signal


@dataclass(frozen=True, slots=True)
class NativePrearmChecksRequested:
    pass


@dataclass(frozen=True, slots=True)
class PrearmReviewAcknowledgmentRequested:
    acknowledged: bool


@dataclass(frozen=True, slots=True)
class NormalArmRequested:
    pass


PreflightIntent: TypeAlias = (
    NativePrearmChecksRequested | PrearmReviewAcknowledgmentRequested | NormalArmRequested
)


class PreflightTelemetryWidget(QWidget):
    """Emit typed intents and render application-owned native review state."""

    intent_emitted = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._readiness = PrearmReadinessSnapshot()
        self._arm = NormalArmSnapshot()
        self._arm_worker: NormalArmWorkerHandoff | None = None
        self._interaction_unavailable_reason: str | None = None
        self._busy = False
        self._busy_detail = ""
        self._rendered_telemetry: TelemetrySnapshot | None = None
        self._rendered_now_s = 0.0
        self.setObjectName("preflightTelemetryView")
        root = QVBoxLayout(self)
        heading = QLabel("Preflight telemetry")
        heading.setObjectName("viewHeading")
        heading.setStyleSheet("font-size: 24px; font-weight: 700; color: #173f3d;")
        disclaimer = QLabel(
            "NATIVE PRE-ARM REVIEW — telemetry remains READ-ONLY. This request only asks "
            "ArduCopter to run its checks. An accepted request is not proof that the aircraft "
            "will arm, and unavailable data or silence is never approval."
        )
        disclaimer.setObjectName("preflightTelemetryDisclaimer")
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(
            "padding: 9px; background: #fff2cc; color: #6a4b00; font-weight: 600;"
        )
        root.addWidget(heading)
        root.addWidget(disclaimer)
        self._interaction_gate = QLabel()
        self._interaction_gate.setObjectName("preflightInteractionGate")
        self._interaction_gate.setAccessibleName("Preflight controls unavailable explanation")
        self._interaction_gate.setWordWrap(True)
        self._interaction_gate.setStyleSheet(
            "padding: 9px; background: #e5f0f6; color: #17435a; font-weight: 700;"
        )
        self._interaction_gate.setVisible(False)
        root.addWidget(self._interaction_gate)

        command_panel = QFrame()
        command_panel.setObjectName("nativePrearmReviewPanel")
        command_panel.setFrameShape(QFrame.Shape.StyledPanel)
        command_layout = QVBoxLayout(command_panel)
        command_heading = QLabel("Native pre-arm request")
        command_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        self._request_status = QLabel()
        self._request_status.setObjectName("nativePrearmRequestStatus")
        self._request_status.setWordWrap(True)
        self._request_detail = QLabel()
        self._request_detail.setObjectName("nativePrearmRequestDetail")
        self._request_detail.setWordWrap(True)
        self._native_assessment = QLabel()
        self._native_assessment.setObjectName("nativePrearmAssessment")
        self._native_assessment.setWordWrap(True)
        self._hardware_safety = QLabel()
        self._hardware_safety.setObjectName("nativeHardwareSafety")
        self._hardware_safety.setWordWrap(True)
        self._application_gate = QLabel()
        self._application_gate.setObjectName("nativePrearmApplicationGate")
        self._application_gate.setWordWrap(True)
        command_actions = QHBoxLayout()
        self._request_button = QPushButton("Request native pre-arm checks")
        self._request_button.setObjectName("requestNativePrearmButton")
        self._review_ack = QCheckBox(
            "I reviewed the native result and every available observation shown below."
        )
        self._review_ack.setObjectName("acknowledgeNativePrearmReview")
        command_actions.addWidget(self._request_button)
        command_actions.addWidget(self._review_ack, 1)
        command_layout.addWidget(command_heading)
        command_layout.addWidget(self._request_status)
        command_layout.addWidget(self._request_detail)
        command_layout.addWidget(self._native_assessment)
        command_layout.addWidget(self._hardware_safety)
        command_layout.addWidget(self._application_gate)
        command_layout.addLayout(command_actions)
        root.addWidget(command_panel)

        arm_panel = QFrame()
        arm_panel.setObjectName("normalArmPanel")
        arm_panel.setFrameShape(QFrame.Shape.StyledPanel)
        arm_layout = QVBoxLayout(arm_panel)
        arm_heading = QLabel("Normal acknowledged Arm")
        arm_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        arm_warning = QLabel(
            "ARM CHANGES VEHICLE STATE. The action remains blocked until the current mission, "
            "SiK telemetry, and reviewed native readiness evidence all match. An accepted "
            "acknowledgment is not shown as Armed without later selected-target telemetry."
        )
        arm_warning.setObjectName("normalArmWarning")
        arm_warning.setWordWrap(True)
        arm_warning.setStyleSheet(
            "padding: 9px; background: #ffe2d5; color: #762500; font-weight: 700;"
        )
        self._arm_status = QLabel()
        self._arm_status.setObjectName("normalArmStatus")
        self._arm_status.setWordWrap(True)
        self._arm_detail = QLabel()
        self._arm_detail.setObjectName("normalArmDetail")
        self._arm_detail.setWordWrap(True)
        self._arm_button = QPushButton("Arm normally")
        self._arm_button.setObjectName("normalArmButton")
        arm_layout.addWidget(arm_heading)
        arm_layout.addWidget(arm_warning)
        arm_layout.addWidget(self._arm_status)
        arm_layout.addWidget(self._arm_detail)
        arm_layout.addWidget(self._arm_button)
        root.addWidget(arm_panel)

        identity_row = QHBoxLayout()
        self._identity = TelemetryCard("Vehicle identity", "telemetryIdentity")
        self._connection = TelemetryCard("Connection freshness", "telemetryConnection")
        self._vehicle = TelemetryCard("Native vehicle state", "telemetryVehicleState")
        identity_row.addWidget(self._identity)
        identity_row.addWidget(self._connection)
        identity_row.addWidget(self._vehicle)
        root.addLayout(identity_row)

        grid = QGridLayout()
        self._gps = TelemetryCard("GPS observation", "telemetryGps")
        self._ekf = TelemetryCard("EKF report", "telemetryEkf")
        self._sensors = TelemetryCard("Native sensor flags", "telemetrySensors")
        self._home = TelemetryCard("Home observation", "telemetryHome")
        self._extended = TelemetryCard("Extended state", "telemetryExtendedState")
        self._battery = TelemetryCard("Battery observation", "telemetryBattery")
        for index, card in enumerate(
            (self._gps, self._ekf, self._sensors, self._home, self._extended, self._battery)
        ):
            grid.addWidget(card, index // 3, index % 3)
        root.addLayout(grid)

        messages_label = QLabel("Native status messages")
        messages_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        root.addWidget(messages_label)
        self._messages = NativeMessagesList()
        root.addWidget(self._messages, 1)
        self._request_button.clicked.connect(self._emit_prearm_request)
        self._arm_button.clicked.connect(self._emit_normal_arm_request)
        self._review_ack.toggled.connect(
            lambda checked: self.intent_emitted.emit(PrearmReviewAcknowledgmentRequested(checked))
        )
        self.render_snapshot(None, now_s=0.0)
        self.render_readiness(self._readiness, now_s=0.0)
        self.render_arm(self._arm)

    def set_interaction_unavailable(self, reason: str) -> None:
        """Disable vehicle actions when the installed shell has no controller binding."""

        if not reason.strip():
            raise ValueError("interaction-unavailable reason must not be empty")
        self._interaction_unavailable_reason = reason
        self._interaction_gate.setText(reason)
        self._interaction_gate.setVisible(True)
        self._apply_interaction_gate()

    def set_busy(self, busy: bool, detail: str = "") -> None:
        """Lock all Preflight actions while the shared installed session is owned."""

        self._busy = busy
        self._busy_detail = detail.strip()
        if self._interaction_unavailable_reason is None:
            self._interaction_gate.setText(
                self._busy_detail or "Another installed vehicle operation owns the channel."
            )
            self._interaction_gate.setVisible(busy)
        # Reapply each snapshot's own enablement first, then the transient lock.
        self.render_composed_readiness(
            self._readiness,
            telemetry=self._rendered_telemetry,
            now_s=self._rendered_now_s,
        )
        self.render_arm(self._arm)

    def _emit_prearm_request(self) -> None:
        # Disable synchronously so a double-click cannot queue a second worker transaction.
        self._request_button.setEnabled(False)
        self.intent_emitted.emit(NativePrearmChecksRequested())

    def _emit_normal_arm_request(self) -> None:
        # Lock synchronously; the worker bridge also rejects an already-active operation.
        self._arm_button.setEnabled(False)
        self.intent_emitted.emit(NormalArmRequested())
        if self._arm_worker is not None:
            self._arm_worker.submit()

    def bind_normal_arm_operation(
        self,
        operation: Callable[[], NormalArmSnapshot],
    ) -> NormalArmWorkerHandoff:
        """Bind blocking application work to the explicit background handoff."""

        if self._arm_worker is not None:
            raise RuntimeError("a normal Arm operation is already bound")
        worker = NormalArmWorkerHandoff(operation)
        worker.snapshot_ready.connect(self._render_worker_arm_snapshot)
        worker.operation_failed.connect(self._render_worker_failure)
        self._arm_worker = worker
        return worker

    def _render_worker_arm_snapshot(self, snapshot: object) -> None:
        if isinstance(snapshot, NormalArmSnapshot):
            self.render_arm(snapshot)

    def _render_worker_failure(self, detail: str) -> None:
        self._arm_status.setText("Worker failed — vehicle state uncertain")
        self._arm_detail.setText(detail)
        self._arm_button.setEnabled(False)

    @property
    def readiness_snapshot(self) -> PrearmReadinessSnapshot:
        return self._readiness

    @property
    def arm_snapshot(self) -> NormalArmSnapshot:
        return self._arm

    def render_arm(self, arm: NormalArmSnapshot) -> None:
        self._arm = arm
        self._arm_status.setText(_arm_state_text(arm.state))
        repeated = (
            " Repeated activation was ignored while the original request remained active."
            if arm.repeated_request_ignored
            else ""
        )
        self._arm_detail.setText(f"{arm.detail}{repeated}")
        self._arm_button.setEnabled(
            arm.request_available
            and arm.state is not NormalArmState.PENDING
            and arm.state is not NormalArmState.ARMED
        )
        self._apply_interaction_gate()
        if arm.native_messages:
            current = (
                ()
                if self._readiness.telemetry is None
                else self._readiness.telemetry.native_messages
            )
            combined = (*current, *arm.native_messages)
            self._messages.render_messages((message.severity, message.text) for message in combined)
            self._messages.scrollToBottom()

    def render_readiness(self, readiness: PrearmReadinessSnapshot, *, now_s: float) -> None:
        self.render_composed_readiness(
            readiness,
            telemetry=readiness.telemetry,
            now_s=now_s,
        )

    def render_composed_readiness(
        self,
        readiness: PrearmReadinessSnapshot,
        *,
        telemetry: TelemetrySnapshot | None,
        now_s: float,
    ) -> None:
        """Render readiness against the installed session's current telemetry."""

        self._readiness = readiness
        self._rendered_telemetry = telemetry
        self._rendered_now_s = now_s
        self.render_snapshot(telemetry, now_s=now_s)
        self._request_status.setText(_request_state_text(readiness.request_state))
        repeated = (
            " Repeated request ignored while the original request remained active."
            if readiness.repeated_request_ignored
            else ""
        )
        self._request_detail.setText(f"{readiness.detail}{repeated}")
        self._native_assessment.setText(_assessment_text(readiness.native_assessment))
        self._hardware_safety.setText(f"Hardware safety: {readiness.hardware_safety_text}")
        if readiness.application_gate_ready:
            self._application_gate.setText(
                "Application readiness gate: Reviewed — not proof ArduCopter will arm."
            )
        else:
            self._application_gate.setText(
                "Application readiness gate: Blocked — accepted, healthy, current evidence and "
                "explicit review are required."
            )
        self._request_button.setEnabled(readiness.request_state is not PrearmRequestState.PENDING)
        self._review_ack.blockSignals(True)
        self._review_ack.setChecked(readiness.review_acknowledged)
        self._review_ack.setEnabled(readiness.review_available)
        self._review_ack.blockSignals(False)
        self._apply_interaction_gate()
        if readiness.native_messages:
            current = () if telemetry is None else telemetry.native_messages
            combined = (*current, *readiness.native_messages)
            self._messages.render_messages((message.severity, message.text) for message in combined)
            self._messages.scrollToBottom()

    def _apply_interaction_gate(self) -> None:
        reason = self._interaction_unavailable_reason
        if reason is None and self._busy:
            reason = self._busy_detail or "Another installed vehicle operation owns the channel."
        if reason is None and self._arm.state is NormalArmState.ARMED:
            reason = (
                "Armed telemetry was confirmed. Further Preflight and Arm requests remain "
                "blocked for this connected session."
            )
        if reason is None:
            for control in (self._request_button, self._review_ack, self._arm_button):
                control.setToolTip("")
            return
        for control in (self._request_button, self._review_ack, self._arm_button):
            control.setEnabled(False)
            control.setToolTip(reason)

    def render_snapshot(self, snapshot: TelemetrySnapshot | None, *, now_s: float) -> None:
        if snapshot is None:
            for card in (
                self._identity,
                self._connection,
                self._vehicle,
                self._gps,
                self._ekf,
                self._sensors,
                self._home,
                self._extended,
                self._battery,
            ):
                card.set_value("Unavailable", TelemetryFreshness.UNAVAILABLE)
            self._messages.render_messages(())
            return

        heartbeat_freshness = snapshot.heartbeat.freshness(now_s)
        self._identity.set_value(
            f"{snapshot.vehicle_identity}\nTarget {snapshot.target_system}:"
            f"{snapshot.target_component} · {snapshot.link_kind.value.upper()}",
            heartbeat_freshness,
        )
        state = snapshot.connection_state(now_s)
        self._connection.set_value(state.value.title(), heartbeat_freshness)
        render_signal(
            self._vehicle,
            snapshot.heartbeat,
            now_s=now_s,
            formatter=lambda value: (
                f"{value.mode_name} · {'Armed' if value.armed else 'Disarmed'} · "
                f"system state {value.system_status}"
            ),
        )
        render_signal(
            self._gps,
            snapshot.gps,
            now_s=now_s,
            formatter=lambda value: (
                f"fix {value.fix_type} · satellites "
                f"{_available(value.satellites_visible)} · HDOP {_available(value.hdop)}"
            ),
        )
        render_signal(
            self._ekf,
            snapshot.ekf,
            now_s=now_s,
            formatter=lambda value: (
                f"reported flags 0x{value.flags:x} · horizontal variance "
                f"{value.horizontal_position_variance:g}"
            ),
        )
        render_signal(
            self._sensors,
            snapshot.sensors,
            now_s=now_s,
            formatter=lambda value: (
                f"present 0x{value.present_flags:x} · enabled 0x{value.enabled_flags:x} · "
                f"health 0x{value.health_flags:x}"
            ),
        )
        render_signal(
            self._home,
            snapshot.home,
            now_s=now_s,
            formatter=lambda value: (
                f"{value.point.latitude_deg:.7f}, {value.point.longitude_deg:.7f} · "
                f"{value.altitude_msl_m:.1f} m MSL"
            ),
        )
        render_signal(
            self._extended,
            snapshot.extended_state,
            now_s=now_s,
            formatter=lambda value: (
                f"landed state {value.landed_state} · VTOL state {value.vtol_state}"
            ),
        )
        render_signal(
            self._battery,
            snapshot.battery,
            now_s=now_s,
            formatter=lambda value: (
                f"{_measurement(value.voltage_v, 'V')} · "
                f"{_measurement(value.current_a, 'A')} · "
                f"{_measurement(value.remaining_percent, '%')}"
            ),
        )
        self._messages.render_messages(
            (message.severity, message.text) for message in snapshot.native_messages
        )
        self._messages.scrollToBottom()


def _available(value: object | None) -> str:
    return "unavailable" if value is None else str(value)


def _measurement(value: int | float | None, unit: str) -> str:
    return "unavailable" if value is None else f"{value:g} {unit}"


def _request_state_text(state: PrearmRequestState) -> str:
    return {
        PrearmRequestState.IDLE: "Not requested",
        PrearmRequestState.PENDING: "Request pending — controls locked",
        PrearmRequestState.ACCEPTED: "Request accepted — not arm approval",
        PrearmRequestState.REJECTED: "Request rejected by ArduCopter",
        PrearmRequestState.UNSUPPORTED: "Native request unsupported",
        PrearmRequestState.TIMED_OUT: "Request timed out — readiness unknown",
        PrearmRequestState.WRONG_TARGET: "Wrong-target acknowledgment — blocked",
        PrearmRequestState.WRONG_ACK: "Unrelated or misaddressed acknowledgment — blocked",
        PrearmRequestState.STALE_LINK: "SiK heartbeat stale — blocked",
        PrearmRequestState.LINK_LOST: "SiK link lost — blocked",
        PrearmRequestState.CANCELLED: "Request cancelled — blocked",
        PrearmRequestState.BLOCKED_WRONG_LINK: "SiK link required",
        PrearmRequestState.BLOCKED_ARMED: "Vehicle is armed — request blocked",
        PrearmRequestState.BLOCKED_MISSION: "Exact current mission verification required",
        PrearmRequestState.BLOCKED_IDENTITY: "Same-target identity unresolved",
    }[state]


def _assessment_text(assessment: NativePrearmAssessment) -> str:
    return {
        NativePrearmAssessment.UNAVAILABLE: (
            "Native check observation: unavailable. Silence is not readiness."
        ),
        NativePrearmAssessment.HEALTHY: (
            "Native check observation: SYS_STATUS pre-arm bit is present, enabled, and healthy. "
            "ArduCopter may still reject a later arm request."
        ),
        NativePrearmAssessment.FAILED: (
            "Native check observation: failure or unhealthy pre-arm state reported — blocked."
        ),
        NativePrearmAssessment.CONFLICTING: (
            "Native check observation: STATUSTEXT and telemetry disagree — blocked."
        ),
    }[assessment]


def _arm_state_text(state: NormalArmState) -> str:
    return {
        NormalArmState.IDLE: "Normal Arm has not been requested — readiness gate is blocked",
        NormalArmState.PENDING: "Normal Arm pending — controls locked",
        NormalArmState.ARMED: "Armed — acknowledgment and telemetry confirmed",
        NormalArmState.REJECTED: "Normal Arm rejected by ArduCopter",
        NormalArmState.UNSUPPORTED: "Normal Arm unsupported",
        NormalArmState.TIMED_OUT: "Acknowledgment timed out — state uncertain",
        NormalArmState.CANCELLED: "Request cancelled — state uncertain",
        NormalArmState.WRONG_TARGET: "Wrong-target acknowledgment — blocked",
        NormalArmState.WRONG_ACK: "Unrelated or misaddressed acknowledgment — blocked",
        NormalArmState.STALE_LINK: "SiK telemetry stale — blocked",
        NormalArmState.LINK_LOST: "SiK link lost — state uncertain",
        NormalArmState.ACKNOWLEDGED_NO_ARMED_TELEMETRY: (
            "Acknowledged, but armed telemetry is absent — state uncertain"
        ),
        NormalArmState.TELEMETRY_DISAGREEMENT: (
            "Acknowledgment and telemetry disagree — state uncertain"
        ),
        NormalArmState.BLOCKED_WRONG_LINK: "SiK link required",
        NormalArmState.BLOCKED_ARMED: "Already armed before request — no command sent",
        NormalArmState.BLOCKED_MISSION: "Exact current mission verification required",
        NormalArmState.BLOCKED_READINESS: "Current reviewed native readiness is required",
        NormalArmState.BLOCKED_IDENTITY: "Same-target identity unresolved",
        NormalArmState.BLOCKED_BUSY: "Command channel busy",
    }[state]
