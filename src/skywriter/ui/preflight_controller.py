"""Installed Task 111 composition for native Preflight and normal Arm."""

from __future__ import annotations

from PySide6.QtCore import QObject, Slot

from skywriter.application.arm import NormalArmService, NormalArmSnapshot, NormalArmState
from skywriter.application.connected import ConnectedMissionSnapshot
from skywriter.application.prearm import PrearmReadinessService, PrearmReadinessSnapshot
from skywriter.ui.connected_controller import ConnectedMissionController
from skywriter.ui.preflight import (
    NativePrearmChecksRequested,
    NormalArmRequested,
    PrearmReviewAcknowledgmentRequested,
    PreflightTelemetryWidget,
)


class PreflightController(QObject):
    """Bind typed UI intents while the Connected controller owns all blocking I/O."""

    def __init__(
        self,
        widget: PreflightTelemetryWidget,
        connected: ConnectedMissionController,
        *,
        readiness: PrearmReadinessService | None = None,
        arm: NormalArmService | None = None,
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._connected_controller = connected
        self._readiness = readiness or PrearmReadinessService()
        self._arm = arm or NormalArmService()
        self._connected = connected.service.snapshot
        self._widget.intent_emitted.connect(self._handle_intent)
        self._connected_controller.snapshot_ready.connect(self._handle_connected_snapshot)
        self._connected_controller.busy_changed.connect(self._handle_busy)
        self._connected_controller.prearm_snapshot_ready.connect(self._handle_prearm_snapshot)
        self._connected_controller.arm_snapshot_ready.connect(self._handle_arm_snapshot)
        self._synchronize()

    @property
    def readiness_service(self) -> PrearmReadinessService:
        return self._readiness

    @property
    def arm_service(self) -> NormalArmService:
        return self._arm

    @Slot(object)
    def _handle_intent(self, intent: object) -> None:
        if isinstance(intent, NativePrearmChecksRequested):
            if self._arm.snapshot.state is NormalArmState.ARMED:
                self._render()
                return
            self._connected_controller.request_native_prearm(self._readiness)
        elif isinstance(intent, PrearmReviewAcknowledgmentRequested):
            if self._connected_controller.busy:
                self._render()
                return
            self._readiness.acknowledge_review(
                intent.acknowledged,
                self._connected,
                now_s=self._connected_controller.clock.now(),
            )
            self._synchronize()
        elif isinstance(intent, NormalArmRequested):
            if self._arm.snapshot.state is NormalArmState.ARMED:
                self._render()
                return
            self._connected_controller.request_normal_arm(self._arm, self._readiness)

    @Slot(object)
    def _handle_connected_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, ConnectedMissionSnapshot):
            return
        self._connected = snapshot
        if self._connected_controller.busy:
            self._widget.render_snapshot(
                snapshot.telemetry,
                now_s=self._connected_controller.clock.now(),
            )
            return
        self._synchronize()

    @Slot(bool, str)
    def _handle_busy(self, busy: bool, detail: str) -> None:
        self._widget.set_busy(busy, detail)
        if not busy:
            self._synchronize()

    @Slot(object)
    def _handle_prearm_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, PrearmReadinessSnapshot):
            return
        self._render()

    @Slot(object)
    def _handle_arm_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, NormalArmSnapshot):
            return
        self._render()

    def _synchronize(self) -> None:
        now_s = self._connected_controller.clock.now()
        self._readiness.synchronize_context(self._connected, now_s=now_s)
        # Preserve the gateway's confirmed Armed terminal result while still
        # invalidating the old readiness review against canonical telemetry.
        if not self._connected_controller.armed_interlock:
            self._arm.synchronize_context(
                self._connected,
                self._readiness,
                now_s=now_s,
                command_channel_idle=not self._connected_controller.busy,
            )
        self._render()

    def _render(self) -> None:
        now_s = self._connected_controller.clock.now()
        self._widget.render_composed_readiness(
            self._readiness.snapshot,
            telemetry=self._connected.telemetry,
            now_s=now_s,
        )
        self._widget.render_arm(self._arm.snapshot)
