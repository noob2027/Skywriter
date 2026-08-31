"""Genuine Task 009–104 production-boundary evidence against pinned stock SITL.

Normal Arm, native AUTO start, and native Pause/Resume use dedicated production
compartments. Only the deliberately invalid start selector used to capture stock-native
denial remains test-only; it creates no reusable application, UI, or production surface.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from scripts.sitl.pinned import (
    MAVLINK_DIALECT,
    EkfPositionHealth,
    PrearmHealth,
    SitlEndpoint,
    SitlTargetIdentity,
    ekf_position_health_from_flags,
    pinned_sitl_session,
    prearm_health_from_bitmaps,
)
from skywriter.application.arm import (
    NormalArmCommandResult,
    NormalArmService,
    NormalArmSnapshot,
    NormalArmState,
)
from skywriter.application.auto_start import (
    NativeAutoStartService,
    NativeAutoStartSnapshot,
    NativeAutoStartState,
)
from skywriter.application.connected import (
    ConnectedMissionService,
    ConnectedMissionSnapshot,
    ConnectedTarget,
    ConnectedVerificationState,
)
from skywriter.application.land_here_now import (
    MAV_LANDED_STATE_IN_AIR,
    NativeLandHereNowService,
    NativeLandHereNowSnapshot,
    NativeLandHereNowState,
)
from skywriter.application.pause_resume import (
    MAV_MISSION_STATE_ACTIVE,
    MAV_MISSION_STATE_PAUSED,
    NativePauseResumeService,
    NativePauseResumeState,
)
from skywriter.application.prearm import (
    NativePrearmAssessment,
    PrearmCommandResult,
    PrearmReadinessService,
    PrearmReadinessSnapshot,
    PrearmRequestState,
)
from skywriter.compatibility.arducopter_4_6_3 import (
    NativeMissionItem,
    NativeMissionPackage,
    item_to_document,
    verification_to_document,
    verify_native_readback,
)
from skywriter.domain.compiler import MissionCompiler
from skywriter.domain.mission import (
    CircleAction,
    GeoPoint,
    HoldAction,
    LandAction,
    Mission,
    MissionSettings,
    ProceedAction,
)
from skywriter.infrastructure.mavlink.arm import NativeNormalArmGateway, NormalArmLink
from skywriter.infrastructure.mavlink.auto_start import (
    NativeAutoStartGateway,
    NativeAutoStartLink,
)
from skywriter.infrastructure.mavlink.connected import ConnectedMavlinkPort
from skywriter.infrastructure.mavlink.connection import (
    IncomingMessage,
    MavlinkAddress,
    MonotonicClock,
    NeverCancelled,
    PymavlinkMissionLink,
    PymavlinkNativeAutoStartLink,
    PymavlinkNativeLandHereNowLink,
    PymavlinkNativePauseResumeLink,
    PymavlinkNormalArmLink,
    PymavlinkPrearmLink,
    TransportDescriptor,
    TransportKind,
)
from skywriter.infrastructure.mavlink.land_here_now import (
    NativeLandHereNowGateway,
    NativeLandHereNowLink,
)
from skywriter.infrastructure.mavlink.pause_resume import (
    NativePauseResumeGateway,
    NativePauseResumeLink,
)
from skywriter.infrastructure.mavlink.prearm import NativePrearmGateway, PrearmCommandLink

pytestmark = pytest.mark.sitl

TARGET = MavlinkAddress(1, 1)
LOCAL = MavlinkAddress(255, 190)
HOME = GeoPoint(51.5007292, -0.1246254)


def _connect(endpoint: SitlEndpoint) -> Any:
    from pymavlink import mavutil

    mavutil.set_dialect(MAVLINK_DIALECT)
    return mavutil.mavlink_connection(
        endpoint.connection_string,
        source_system=LOCAL.system_id,
        source_component=LOCAL.component_id,
        dialect=MAVLINK_DIALECT,
        autoreconnect=False,
    )


def _wait_message(
    connection: Any,
    predicate: Callable[[Any], bool],
    *,
    timeout_s: float,
    trace: list[dict[str, object]] | None = None,
) -> Any:
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        message = connection.recv_match(
            blocking=True,
            timeout=min(1.0, max(0.0, deadline_s - time.monotonic())),
        )
        if message is None:
            continue
        if trace is not None:
            _record_execution_message(trace, message)
        if message.get_type() != "BAD_DATA" and predicate(message):
            return message
    raise AssertionError("expected stock-SITL message was not received before the deadline")


def _request_home_position(connection: Any) -> None:
    from pymavlink import mavutil

    connection.mav.heartbeat_send(
        int(mavutil.mavlink.MAV_TYPE_GCS),
        int(mavutil.mavlink.MAV_AUTOPILOT_INVALID),
        0,
        0,
        int(mavutil.mavlink.MAV_STATE_ACTIVE),
        3,
    )
    connection.mav.command_long_send(
        TARGET.system_id,
        TARGET.component_id,
        int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
        0,
        float(mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION),
        0,
        0,
        0,
        0,
        0,
        0,
    )


def _request_execution_telemetry(connection: Any, trace: list[dict[str, object]]) -> None:
    from pymavlink import mavutil

    for trace_name, message_id in (
        (
            "COMMAND_LONG_REQUEST_EXTENDED_SYS_STATE",
            int(mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE),
        ),
        (
            "COMMAND_LONG_REQUEST_GLOBAL_POSITION_INT",
            int(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT),
        ),
        (
            "COMMAND_LONG_REQUEST_MISSION_CURRENT",
            int(mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT),
        ),
    ):
        trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": trace_name,
                "fields": {
                    "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
                    "requested_message_id": message_id,
                },
            }
        )
        connection.mav.command_long_send(
            TARGET.system_id,
            TARGET.component_id,
            int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
            0,
            message_id,
            0,
            0,
            0,
            0,
            0,
            0,
        )


def _request_prearm_review_observations(connection: Any, trace: list[dict[str, object]]) -> None:
    """Ask for read-only observations; this remains SITL evidence scaffolding."""

    from pymavlink import mavutil

    for name, message_id in (
        ("SYS_STATUS", int(mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS)),
        ("GPS_RAW_INT", int(mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT)),
        ("HOME_POSITION", int(mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION)),
        ("EKF_STATUS_REPORT", int(mavutil.mavlink.MAVLINK_MSG_ID_EKF_STATUS_REPORT)),
        ("BATTERY_STATUS", int(mavutil.mavlink.MAVLINK_MSG_ID_BATTERY_STATUS)),
        ("EXTENDED_SYS_STATE", int(mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE)),
    ):
        trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": f"COMMAND_LONG_REQUEST_{name}",
                "fields": {
                    "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
                    "requested_message_id": message_id,
                    "test_only_read_request": True,
                },
            }
        )
        connection.mav.command_long_send(
            TARGET.system_id,
            TARGET.component_id,
            int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
            0,
            float(message_id),
            0,
            0,
            0,
            0,
            0,
            0,
        )


class _EvidencePrearmLink(PrearmCommandLink):
    """Trace wrapper around the exact production pre-arm link."""

    def __init__(self, delegate: PymavlinkPrearmLink, trace: list[dict[str, object]]) -> None:
        self._delegate = delegate
        self._trace = trace
        self.descriptor = delegate.descriptor
        self.local_address = delegate.local_address

    def is_connected(self) -> bool:
        return self._delegate.is_connected()

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        message = self._delegate.receive(timeout_s)
        if message is not None and message.name in {
            "COMMAND_ACK",
            "HEARTBEAT",
            "STATUSTEXT",
        }:
            self._trace.append(
                {
                    "elapsed_monotonic_s": time.monotonic(),
                    "message_type": message.name,
                    "source_system": message.source.system_id,
                    "source_component": message.source.component_id,
                    "fields": _json_safe(message.fields),
                }
            )
        return message

    def send_prearm_checks(self, target: MavlinkAddress) -> None:
        self._trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_NATIVE_PREARM_REQUEST",
                "fields": {
                    "target_system": target.system_id,
                    "target_component": target.component_id,
                    "command": 401,
                    "confirmation": 0,
                    "params": [0, 0, 0, 0, 0, 0, 0],
                },
            }
        )
        self._delegate.send_prearm_checks(target)


class _EvidenceNormalArmLink(NormalArmLink):
    """Trace wrapper around the exact production normal-arm link."""

    def __init__(self, delegate: PymavlinkNormalArmLink, trace: list[dict[str, object]]) -> None:
        self._delegate = delegate
        self._trace = trace
        self.descriptor = delegate.descriptor
        self.local_address = delegate.local_address

    def is_connected(self) -> bool:
        return self._delegate.is_connected()

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        message = self._delegate.receive(timeout_s)
        if message is not None and message.name in {
            "COMMAND_ACK",
            "HEARTBEAT",
            "STATUSTEXT",
        }:
            self._trace.append(
                {
                    "elapsed_monotonic_s": time.monotonic(),
                    "message_type": message.name,
                    "source_system": message.source.system_id,
                    "source_component": message.source.component_id,
                    "fields": _json_safe(message.fields),
                }
            )
        return message

    def send_normal_arm(self, target: MavlinkAddress) -> None:
        self._trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_NORMAL_ARM",
                "fields": {
                    "target_system": target.system_id,
                    "target_component": target.component_id,
                    "command": 400,
                    "confirmation": 0,
                    "params": [1, 0, 0, 0, 0, 0, 0],
                },
            }
        )
        self._delegate.send_normal_arm(target)


class _EvidenceAutoStartLink(NativeAutoStartLink):
    """Trace wrapper around the fixed production native mission-start link."""

    def __init__(
        self,
        delegate: PymavlinkNativeAutoStartLink,
        trace: list[dict[str, object]],
    ) -> None:
        self._delegate = delegate
        self._trace = trace
        self.descriptor = delegate.descriptor
        self.local_address = delegate.local_address

    def is_connected(self) -> bool:
        return self._delegate.is_connected()

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        message = self._delegate.receive(timeout_s)
        if message is not None and message.name in {
            "COMMAND_ACK",
            "HEARTBEAT",
            "MISSION_CURRENT",
            "MISSION_ITEM_REACHED",
            "STATUSTEXT",
        }:
            self._trace.append(
                {
                    "elapsed_monotonic_s": time.monotonic(),
                    "message_type": message.name,
                    "source_system": message.source.system_id,
                    "source_component": message.source.component_id,
                    "fields": _json_safe(message.fields),
                }
            )
        return message

    def send_native_auto_start(self, target: MavlinkAddress) -> None:
        self._trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_NATIVE_AUTO_START",
                "fields": {
                    "target_system": target.system_id,
                    "target_component": target.component_id,
                    "command": 300,
                    "confirmation": 0,
                    "params": [0, 0, 0, 0, 0, 0, 0],
                },
            }
        )
        self._delegate.send_native_auto_start(target)


class _EvidencePauseResumeLink(NativePauseResumeLink):
    """Trace wrapper around the two fixed production command-193 actions."""

    def __init__(
        self,
        delegate: PymavlinkNativePauseResumeLink,
        trace: list[dict[str, object]],
    ) -> None:
        self._delegate = delegate
        self._trace = trace
        self.descriptor = delegate.descriptor
        self.local_address = delegate.local_address

    def is_connected(self) -> bool:
        return self._delegate.is_connected()

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        message = self._delegate.receive(timeout_s)
        if message is not None and message.name in {
            "COMMAND_ACK",
            "HEARTBEAT",
            "MISSION_CURRENT",
            "MISSION_ITEM_REACHED",
            "EXTENDED_SYS_STATE",
            "STATUSTEXT",
        }:
            self._trace.append(
                {
                    "elapsed_monotonic_s": time.monotonic(),
                    "message_type": message.name,
                    "source_system": message.source.system_id,
                    "source_component": message.source.component_id,
                    "fields": _json_safe(message.fields),
                }
            )
        return message

    def send_native_pause(self, target: MavlinkAddress) -> None:
        self._record_command(target, continue_mission=False)
        self._delegate.send_native_pause(target)

    def send_native_resume(self, target: MavlinkAddress) -> None:
        self._record_command(target, continue_mission=True)
        self._delegate.send_native_resume(target)

    def request_native_mission_state(self, target: MavlinkAddress) -> None:
        from pymavlink import mavutil

        self._trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_REQUEST_MISSION_CURRENT",
                "fields": {
                    "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
                    "requested_message_id": int(mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT),
                    "target_component": target.component_id,
                    "target_system": target.system_id,
                },
            }
        )
        self._delegate.request_native_mission_state(target)

    def _record_command(
        self,
        target: MavlinkAddress,
        *,
        continue_mission: bool,
    ) -> None:
        self._trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": (
                    "COMMAND_LONG_NATIVE_RESUME"
                    if continue_mission
                    else "COMMAND_LONG_NATIVE_PAUSE"
                ),
                "fields": {
                    "target_system": target.system_id,
                    "target_component": target.component_id,
                    "command": 193,
                    "confirmation": 0,
                    "params": [int(continue_mission), 0, 0, 0, 0, 0, 0],
                },
            }
        )


class _EvidenceLandHereNowLink(NativeLandHereNowLink):
    """Trace wrapper around fixed command 21 and its read-only state request."""

    def __init__(
        self,
        delegate: PymavlinkNativeLandHereNowLink,
        trace: list[dict[str, object]],
    ) -> None:
        self._delegate = delegate
        self._trace = trace
        self.descriptor = delegate.descriptor
        self.local_address = delegate.local_address

    def is_connected(self) -> bool:
        return self._delegate.is_connected()

    def receive(self, timeout_s: float) -> IncomingMessage | None:
        message = self._delegate.receive(timeout_s)
        if message is not None and message.name in {
            "COMMAND_ACK",
            "HEARTBEAT",
            "EXTENDED_SYS_STATE",
            "STATUSTEXT",
        }:
            self._trace.append(
                {
                    "elapsed_monotonic_s": time.monotonic(),
                    "message_type": message.name,
                    "source_system": message.source.system_id,
                    "source_component": message.source.component_id,
                    "fields": _json_safe(message.fields),
                }
            )
        return message

    def send_native_land_here_now(self, target: MavlinkAddress) -> None:
        self._trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_NATIVE_LAND_HERE_NOW",
                "fields": {
                    "target_system": target.system_id,
                    "target_component": target.component_id,
                    "command": 21,
                    "confirmation": 0,
                    "params": [0, 0, 0, 0, 0, 0, 0],
                },
            }
        )
        self._delegate.send_native_land_here_now(target)

    def request_native_landing_state(self, target: MavlinkAddress) -> None:
        from pymavlink import mavutil

        self._trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_LAND_HERE_NOW_REQUEST_EXTENDED_SYS_STATE",
                "fields": {
                    "target_system": target.system_id,
                    "target_component": target.component_id,
                    "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
                    "requested_message_id": int(mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE),
                },
            }
        )
        self._delegate.request_native_landing_state(target)


def _wait_prearm_ready(
    connection: Any,
    trace: list[dict[str, object]],
    *,
    timeout_s: float = 30.0,
) -> PrearmHealth:
    from pymavlink import mavutil

    deadline_s = time.monotonic() + timeout_s
    last = PrearmHealth(present=False, enabled=False, healthy=False)
    while time.monotonic() < deadline_s:
        # Direct stock-binary TCP sessions do not necessarily stream SYS_STATUS.
        # Request one read-only snapshot; this is not MAV_CMD_RUN_PREARM_CHECKS.
        trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_REQUEST_SYS_STATUS",
                "fields": {
                    "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
                    "requested_message_id": int(mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS),
                },
            }
        )
        connection.mav.command_long_send(
            TARGET.system_id,
            TARGET.component_id,
            int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
            0,
            float(mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        response_deadline_s = min(deadline_s, time.monotonic() + 2.0)
        while time.monotonic() < response_deadline_s:
            message = connection.recv_match(
                blocking=True,
                timeout=min(0.5, max(0.0, response_deadline_s - time.monotonic())),
            )
            if message is None:
                continue
            _record_execution_message(trace, message)
            if message.get_type() != "SYS_STATUS" or message.get_srcSystem() != TARGET.system_id:
                continue
            last = prearm_health_from_bitmaps(
                int(message.onboard_control_sensors_present),
                int(message.onboard_control_sensors_enabled),
                int(message.onboard_control_sensors_health),
            )
            if last.ready:
                return last
            break
        time.sleep(min(0.25, max(0.0, deadline_s - time.monotonic())))
    raise AssertionError(
        "stock SITL did not report a healthy read-only SYS_STATUS pre-arm bit; "
        f"last={last}; native trace=" + json.dumps(trace[-30:], sort_keys=True)
    )


def _wait_ekf_position_ready(
    connection: Any,
    trace: list[dict[str, object]],
    *,
    timeout_s: float,
) -> EkfPositionHealth:
    from pymavlink import mavutil

    deadline_s = time.monotonic() + timeout_s
    last = EkfPositionHealth(horizontal_absolute=False, constant_position_mode=False)
    while time.monotonic() < deadline_s:
        trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "COMMAND_LONG_REQUEST_EKF_STATUS_REPORT",
                "fields": {
                    "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
                    "requested_message_id": int(mavutil.mavlink.MAVLINK_MSG_ID_EKF_STATUS_REPORT),
                },
            }
        )
        connection.mav.command_long_send(
            TARGET.system_id,
            TARGET.component_id,
            int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
            0,
            float(mavutil.mavlink.MAVLINK_MSG_ID_EKF_STATUS_REPORT),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        response_deadline_s = min(deadline_s, time.monotonic() + 2.0)
        while time.monotonic() < response_deadline_s:
            message = connection.recv_match(
                blocking=True,
                timeout=min(0.5, max(0.0, response_deadline_s - time.monotonic())),
            )
            if message is None:
                continue
            _record_execution_message(trace, message)
            if (
                message.get_type() != "EKF_STATUS_REPORT"
                or message.get_srcSystem() != TARGET.system_id
            ):
                continue
            last = ekf_position_health_from_flags(int(message.flags))
            if last.ready:
                return last
            break
        time.sleep(min(0.25, max(0.0, deadline_s - time.monotonic())))
    raise AssertionError(
        "stock SITL did not report an absolute non-constant EKF position before the "
        f"bounded deadline; last={last}; native trace=" + json.dumps(trace[-30:], sort_keys=True)
    )


def _test_only_rejected_mission_start(
    connection: Any,
    trace: list[dict[str, object]],
) -> int:
    from pymavlink import mavutil

    # The pinned handler rejects any nonzero first/last selector. Production cannot
    # supply either selector; this direct invalid packet is negative evidence only.
    mission_start_command = int(mavutil.mavlink.MAV_CMD_MISSION_START)
    trace.append(
        {
            "elapsed_monotonic_s": time.monotonic(),
            "message_type": "COMMAND_LONG_TEST_ONLY_INVALID_MISSION_START",
            "fields": {
                "command": mission_start_command,
                "first_item": 1,
                "last_item": 0,
            },
        }
    )
    connection.mav.command_long_send(
        TARGET.system_id,
        TARGET.component_id,
        mission_start_command,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    acknowledgment = _wait_message(
        connection,
        lambda message: (
            message.get_type() == "COMMAND_ACK"
            and message.get_srcSystem() == TARGET.system_id
            and int(message.command) == mission_start_command
            and int(message.result) == int(mavutil.mavlink.MAV_RESULT_DENIED)
        ),
        timeout_s=15.0,
        trace=trace,
    )
    return int(acknowledgment.result)


def _record_execution_message(trace: list[dict[str, object]], message: Any) -> None:
    if message.get_type() not in {
        "COMMAND_ACK",
        "EKF_STATUS_REPORT",
        "HEARTBEAT",
        "MISSION_CURRENT",
        "MISSION_ITEM_REACHED",
        "STATUSTEXT",
        "SYS_STATUS",
    }:
        return
    trace.append(
        {
            "elapsed_monotonic_s": time.monotonic(),
            "message_type": str(message.get_type()),
            "fields": _json_safe(message.to_dict()),
        }
    )


def _wait_for_mission_state(
    service: ConnectedMissionService,
    port: ConnectedMavlinkPort,
    connection: Any,
    trace: list[dict[str, object]],
    cancellation: NeverCancelled,
    *,
    expected_state: int,
    sequence: int,
    timeout_s: float = 30.0,
) -> ConnectedMissionSnapshot:
    deadline_s = time.monotonic() + timeout_s
    last: dict[str, object] = {}
    while time.monotonic() < deadline_s:
        _request_execution_telemetry(connection, trace)
        service.refresh_telemetry(
            port,
            duration_s=1.25,
            cancellation=cancellation,
        )
        snapshot = service.snapshot
        telemetry = snapshot.telemetry
        heartbeat = None if telemetry is None else telemetry.heartbeat.value
        progress = None if telemetry is None else telemetry.mission.value
        last = {
            "armed": None if heartbeat is None else heartbeat.armed,
            "mode": None if heartbeat is None else heartbeat.mode_number,
            "sequence": None if progress is None else progress.current_sequence,
            "mission_state": None if progress is None else progress.mission_state,
        }
        trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "PAUSE_RESUME_GATE_TELEMETRY",
                "fields": last,
            }
        )
        if (
            heartbeat is not None
            and heartbeat.armed
            and heartbeat.mode_number == 3
            and progress is not None
            and progress.current_sequence == sequence
            and progress.mission_state == expected_state
        ):
            return snapshot
        if (
            progress is not None
            and progress.current_sequence is not None
            and progress.current_sequence > sequence
        ):
            raise AssertionError(
                f"mission advanced past requested Task 103 action {sequence}; last={last}"
            )
    raise AssertionError(
        f"mission did not report state {expected_state} at sequence {sequence}; last={last}"
    )


def _wait_for_land_here_now_context(
    service: ConnectedMissionService,
    port: ConnectedMavlinkPort,
    connection: Any,
    trace: list[dict[str, object]],
    cancellation: NeverCancelled,
    *,
    last_sequence: int,
    timeout_s: float = 60.0,
) -> ConnectedMissionSnapshot:
    """Wait for production Land Here Now's armed/AUTO/In Air/progress gate."""

    deadline_s = time.monotonic() + timeout_s
    last: dict[str, object] = {}
    while time.monotonic() < deadline_s:
        _request_execution_telemetry(connection, trace)
        service.refresh_telemetry(
            port,
            duration_s=1.25,
            cancellation=cancellation,
        )
        snapshot = service.snapshot
        telemetry = snapshot.telemetry
        heartbeat = None if telemetry is None else telemetry.heartbeat.value
        progress = None if telemetry is None else telemetry.mission.value
        extended = None if telemetry is None else telemetry.extended_state.value
        position = None if telemetry is None else telemetry.position.value
        last = {
            "armed": None if heartbeat is None else heartbeat.armed,
            "mode": None if heartbeat is None else heartbeat.mode_number,
            "sequence": None if progress is None else progress.current_sequence,
            "mission_state": None if progress is None else progress.mission_state,
            "landed_state": None if extended is None else extended.landed_state,
            "relative_altitude_m": (None if position is None else position.relative_altitude_m),
        }
        trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "LAND_HERE_NOW_GATE_TELEMETRY",
                "fields": last,
            }
        )
        sequence = None if progress is None else progress.current_sequence
        if (
            heartbeat is not None
            and heartbeat.armed
            and heartbeat.mode_number == 3
            and progress is not None
            and progress.mission_state in (MAV_MISSION_STATE_ACTIVE, MAV_MISSION_STATE_PAUSED)
            and sequence is not None
            and sequence < last_sequence
            and extended is not None
            and extended.landed_state == MAV_LANDED_STATE_IN_AIR
            and position is not None
            and position.relative_altitude_m >= 2.0
        ):
            return snapshot
    raise AssertionError(f"Land Here Now gate did not become eligible; last={last}")


def _reference_download(connection: Any) -> tuple[NativeMissionItem, ...]:
    from pymavlink import mavutil

    connection.mav.mission_request_list_send(
        TARGET.system_id, TARGET.component_id, mavutil.mavlink.MAV_MISSION_TYPE_MISSION
    )
    count_message = _wait_message(
        connection,
        lambda message: (
            message.get_type() == "MISSION_COUNT" and message.get_srcSystem() == TARGET.system_id
        ),
        timeout_s=10.0,
    )
    count = int(count_message.count)
    items: list[NativeMissionItem] = []
    for sequence in range(count):

        def is_expected_item(candidate: Any, expected_sequence: int = sequence) -> bool:
            return bool(
                candidate.get_type() == "MISSION_ITEM_INT"
                and candidate.get_srcSystem() == TARGET.system_id
                and int(candidate.seq) == expected_sequence
            )

        connection.mav.mission_request_int_send(
            TARGET.system_id,
            TARGET.component_id,
            sequence,
            mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
        )
        message = _wait_message(
            connection,
            is_expected_item,
            timeout_s=5.0,
        )
        items.append(
            NativeMissionItem(
                sequence=int(message.seq),
                frame=int(message.frame),
                command=int(message.command),
                current=bool(message.current),
                autocontinue=bool(message.autocontinue),
                param1=float(message.param1),
                param2=float(message.param2),
                param3=float(message.param3),
                param4=float(message.param4),
                latitude_e7=int(message.x),
                longitude_e7=int(message.y),
                altitude_m=float(message.z),
                mission_type=int(getattr(message, "mission_type", 0)),
            )
        )
    connection.mav.mission_ack_send(
        TARGET.system_id,
        TARGET.component_id,
        mavutil.mavlink.MAV_MISSION_ACCEPTED,
        mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
    )
    return tuple(items)


def _mission() -> Mission:
    return Mission(
        settings=MissionSettings(3.0, 8.0, True),
        id="task-103-stock-sitl",
        actions=(
            # The longer WP legs provide deterministic windows for positive Pause
            # proof while Proceed and the approach phase of Hold own execution.
            ProceedAction(GeoPoint(51.5011792, -0.1246254), 3.0),
            HoldAction(GeoPoint(51.5016292, -0.1245000), 3.0, 3.0),
            CircleAction(GeoPoint(51.5011792, -0.1243500), 3.0, 5.0),
            LandAction(HOME, 3.0),
        ),
    )


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(value)


def _write_execution_trace(path: Path, trace: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {"schema": "skywriter-task-009-execution-trace-v1", "trace": trace},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_connected_usb_upload_sik_reconnect_execution_and_reference_readback(
    sitl_endpoint: SitlEndpoint,
    sitl_target_identity: SitlTargetIdentity,
    request: pytest.FixtureRequest,
) -> None:
    from pymavlink import mavutil

    evidence_root = Path(os.environ["SKYWRITER_SITL_EVIDENCE"])
    clock = MonotonicClock()
    cancellation = NeverCancelled()
    service = ConnectedMissionService()
    compiled = MissionCompiler().compile(_mission())
    service.set_compiled(compiled, mission_revision=1)
    states = [service.snapshot.verification_state.value]

    usb_connection = _connect(sitl_endpoint)
    usb_link = PymavlinkMissionLink(
        usb_connection,
        TransportDescriptor(sitl_endpoint.connection_string, TransportKind.USB),
    )
    request.addfinalizer(usb_link.close)
    usb = ConnectedMavlinkPort(usb_link, clock=clock)
    service.discover(usb, duration_s=2.5, cancellation=cancellation)
    assert len(service.snapshot.candidates) == 1
    selected = service.snapshot.candidates[0]
    assert (selected.system_id, selected.component_id) == (
        sitl_target_identity.system_id,
        sitl_target_identity.component_id,
    )
    service.select_target(selected.system_id, selected.component_id, now_s=clock.now())
    service.inspect_onboard(usb, cancellation=cancellation)
    assert service.snapshot.onboard is not None
    assert service.snapshot.onboard.item_count == 0
    service.confirm_replacement(True)
    _request_home_position(usb_connection)
    service.refresh_telemetry(
        usb,
        duration_s=30.0,
        cancellation=cancellation,
        require_home=True,
    )
    service.upload_and_verify(usb, now_s=clock.now(), cancellation=cancellation)
    assert service.snapshot.failure is None
    assert service.snapshot.verification_state is ConnectedVerificationState.USB_VERIFIED
    expected = service.snapshot.expected_package
    assert isinstance(expected, NativeMissionPackage)
    states.append(service.snapshot.verification_state.value)

    service.disconnect()
    usb_link.close()
    states.append(service.snapshot.verification_state.value)

    sik_connection = _connect(sitl_endpoint)
    sik_link = PymavlinkMissionLink(
        sik_connection,
        TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
    )
    request.addfinalizer(sik_link.close)
    sik = ConnectedMavlinkPort(sik_link, clock=clock)
    service.discover(sik, duration_s=2.5, cancellation=cancellation)
    assert len(service.snapshot.candidates) == 1
    selected = service.snapshot.candidates[0]
    service.select_target(selected.system_id, selected.component_id, now_s=clock.now())
    _request_home_position(sik_connection)
    service.refresh_telemetry(
        sik,
        duration_s=30.0,
        cancellation=cancellation,
        require_home=True,
    )
    service.reverify_over_sik(sik, now_s=clock.now(), cancellation=cancellation)
    assert service.snapshot.failure is None
    sik_snapshot = service.snapshot
    assert sik_snapshot.verification_state is ConnectedVerificationState.SIK_VERIFIED
    assert sik_snapshot.connected_ready(clock.now())
    states.append(sik_snapshot.verification_state.value)

    # Copter builds sequence 0 from its live AHRS Home on every read. Verify the
    # independent native readback while that authoritative preflight Home is stable;
    # flight can legitimately refine its altitude and must not be normalized away.
    reference_items = _reference_download(sik_connection)
    reference = verify_native_readback(expected, reference_items)
    assert reference.verified

    execution_trace: list[dict[str, object]] = []
    execution_trace_path = evidence_root / "connected-execution-trace.json"
    prearm_service = PrearmReadinessService()
    positive_prearm: PrearmReadinessSnapshot | None = None
    positive_arm: NormalArmSnapshot | None = None
    positive_auto_start: NativeAutoStartSnapshot | None = None
    pause_resume_cycles: list[dict[str, object]] = []
    pause_resume_service = NativePauseResumeService()
    landed_auto_arm_rejection: NormalArmCommandResult | None = None
    armed_rejection: PrearmCommandResult | None = None
    native_auto_start_rejection: int | None = None
    link_interrupted_at_s: float | None = None
    link_reconnected_at_s: float | None = None
    observation_target: ConnectedTarget | None = None
    auto_start_state_after_link_loss: NativeAutoStartState | None = None
    pause_resume_state_after_link_loss: NativePauseResumeState | None = None
    second_prearm: PrearmReadinessSnapshot | None = None
    second_arm: NormalArmSnapshot | None = None
    second_auto_start: NativeAutoStartSnapshot | None = None
    land_confirmation: NativeLandHereNowSnapshot | None = None
    land_result: NativeLandHereNowSnapshot | None = None
    second_landed_disarmed = False
    second_max_relative_altitude_m = 0.0
    try:
        readiness_deadline_s = time.monotonic() + 30.0
        position_health = _wait_ekf_position_ready(
            sik_connection,
            execution_trace,
            timeout_s=max(0.0, readiness_deadline_s - time.monotonic()),
        )
        prearm_health = _wait_prearm_ready(
            sik_connection,
            execution_trace,
            timeout_s=max(0.0, readiness_deadline_s - time.monotonic()),
        )
        _request_prearm_review_observations(sik_connection, execution_trace)
        service.refresh_telemetry(sik, duration_s=3.0, cancellation=cancellation)
        assert service.snapshot.telemetry is not None
        assert service.snapshot.telemetry.sensors.value is not None
        selected_target = service.snapshot.selected_target
        assert selected_target is not None
        prearm_link = _EvidencePrearmLink(
            PymavlinkPrearmLink(
                sik_connection,
                TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
            ),
            execution_trace,
        )
        prearm_gateway = NativePrearmGateway(prearm_link, clock=clock)
        prearm_service.request_prearm_checks(
            prearm_gateway,
            service.snapshot,
            now_s=clock.now(),
            cancellation=cancellation,
        )
        positive_prearm = prearm_service.snapshot
        assert positive_prearm.request_state is PrearmRequestState.ACCEPTED
        assert positive_prearm.native_assessment is NativePrearmAssessment.HEALTHY
        prearm_service.acknowledge_review(True, service.snapshot, now_s=clock.now())
        assert prearm_service.application_gate_ready_at(service.snapshot, now_s=clock.now())

        # The production service owns the Task 100 fingerprint gate; the production
        # gateway owns exact ACK correlation and the later armed-heartbeat proof.
        assert prearm_health.ready
        assert position_health.ready
        arm_link = _EvidenceNormalArmLink(
            PymavlinkNormalArmLink(
                sik_connection,
                TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
            ),
            execution_trace,
        )
        arm_gateway = NativeNormalArmGateway(arm_link, clock=clock)
        arm_service = NormalArmService()
        positive_arm = arm_service.request_normal_arm(
            arm_gateway,
            service.snapshot,
            prearm_service,
            now_s=clock.now(),
            command_channel_idle=True,
            cancellation=cancellation,
        )
        assert positive_arm.state is NormalArmState.ARMED
        assert positive_arm.ack_result == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
        assert positive_arm.armed_observed_at_s is not None
        # Pinned source returns TEMPORARILY_REJECTED only while armed.  The production
        # application gate above never sends in that state; this direct gateway call
        # is isolated evidence using Task 009's existing normal-arm scaffold.
        armed_target = replace(selected_target, base_mode=128, observed_at_s=clock.now())
        armed_rejection = prearm_gateway.request_prearm_checks(
            armed_target,
            target_valid_for_s=3.0,
            cancellation=cancellation,
        )
        assert armed_rejection.state is PrearmRequestState.REJECTED
        assert armed_rejection.ack_result == int(mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED)

        # Refresh the accepted connected snapshot after the arm gateway's later
        # heartbeat proof, then run Task 102 through its production gate/gateway.
        service.refresh_telemetry(sik, duration_s=2.0, cancellation=cancellation)
        assert service.snapshot.selected_target is not None
        assert service.snapshot.selected_target.armed
        auto_start_link = _EvidenceAutoStartLink(
            PymavlinkNativeAutoStartLink(
                sik_connection,
                TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
            ),
            execution_trace,
        )
        auto_start_gateway = NativeAutoStartGateway(auto_start_link, clock=clock)
        auto_start_service = NativeAutoStartService()
        positive_auto_start = auto_start_service.request_native_auto_start(
            auto_start_gateway,
            service.snapshot,
            positive_arm,
            now_s=clock.now(),
            command_channel_idle=True,
            cancellation=cancellation,
        )
        assert positive_auto_start.state is NativeAutoStartState.RUNNING
        assert positive_auto_start.ack_result == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
        assert positive_auto_start.auto_observed_at_s is not None
        assert positive_auto_start.progress_observed_at_s is not None
        assert positive_auto_start.progress_sequence is not None

        pause_resume_link = _EvidencePauseResumeLink(
            PymavlinkNativePauseResumeLink(
                sik_connection,
                TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
            ),
            execution_trace,
        )
        pause_resume_gateway = NativePauseResumeGateway(pause_resume_link, clock=clock)
        for action_sequence, action_name in ((3, "Proceed"), (4, "Hold approach")):
            active_context = _wait_for_mission_state(
                service,
                sik,
                sik_connection,
                execution_trace,
                cancellation,
                expected_state=MAV_MISSION_STATE_ACTIVE,
                sequence=action_sequence,
            )
            pause_resume_service.synchronize_context(
                active_context,
                positive_auto_start,
                now_s=clock.now(),
                command_channel_idle=True,
            )
            assert pause_resume_service.snapshot.pause_available
            paused = pause_resume_service.request_native_pause(
                pause_resume_gateway,
                active_context,
                positive_auto_start,
                now_s=clock.now(),
                command_channel_idle=True,
                cancellation=cancellation,
            )
            assert paused.state is NativePauseResumeState.PAUSED
            assert paused.ack_result == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
            assert paused.progress_sequence == action_sequence
            paused_context = _wait_for_mission_state(
                service,
                sik,
                sik_connection,
                execution_trace,
                cancellation,
                expected_state=MAV_MISSION_STATE_PAUSED,
                sequence=action_sequence,
            )
            pause_resume_service.synchronize_context(
                paused_context,
                positive_auto_start,
                now_s=clock.now(),
                command_channel_idle=True,
            )
            assert pause_resume_service.snapshot.resume_available
            resumed = pause_resume_service.request_native_resume(
                pause_resume_gateway,
                paused_context,
                positive_auto_start,
                now_s=clock.now(),
                command_channel_idle=True,
                cancellation=cancellation,
            )
            assert resumed.state is NativePauseResumeState.RUNNING
            assert resumed.ack_result == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
            assert resumed.progress_sequence == action_sequence
            pause_resume_cycles.append(
                {
                    "mission_action": action_name,
                    "sequence": action_sequence,
                    "pause": asdict(paused),
                    "resume": asdict(resumed),
                }
            )

        # Explicitly sever the desktop command/telemetry session after Running.
        # The app gate invalidates immediately; stock Copter remains the onboard
        # flight authority and the test reconnects only to observe later progress.
        service.disconnect()
        auto_start_service.synchronize_context(
            service.snapshot,
            positive_arm,
            now_s=clock.now(),
            command_channel_idle=True,
        )
        auto_start_state_after_link_loss = auto_start_service.snapshot.state
        assert auto_start_state_after_link_loss is NativeAutoStartState.LINK_LOST
        pause_resume_service.synchronize_context(
            service.snapshot,
            positive_auto_start,
            now_s=clock.now(),
            command_channel_idle=True,
        )
        pause_resume_state_after_link_loss = pause_resume_service.snapshot.state
        assert pause_resume_state_after_link_loss is NativePauseResumeState.LINK_LOST
        link_interrupted_at_s = time.monotonic()
        sik_link.close()
        sik_connection = _connect(sitl_endpoint)
        sik_link = PymavlinkMissionLink(
            sik_connection,
            TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
        )
        request.addfinalizer(sik_link.close)
        sik = ConnectedMavlinkPort(sik_link, clock=clock)
        reconnected_targets = sik.discover(duration_s=2.5, cancellation=cancellation)
        observation_target = next(
            (
                candidate
                for candidate in reconnected_targets
                if candidate.vehicle == selected_target.vehicle
                and candidate.system_id == selected_target.system_id
                and candidate.component_id == selected_target.component_id
            ),
            None,
        )
        assert observation_target is not None, (
            "the selected target was not rediscovered after desktop link interruption"
        )
        link_reconnected_at_s = time.monotonic()
        execution_trace.append(
            {
                "elapsed_monotonic_s": link_reconnected_at_s,
                "message_type": "OBSERVATION_LINK_TARGET_REDISCOVERED",
                "fields": {
                    "system_id": observation_target.system_id,
                    "component_id": observation_target.component_id,
                    "vehicle_identity": observation_target.vehicle.value,
                },
            }
        )
    finally:
        # Preserve native pre-arm/arm/AUTO/Pause/Resume evidence on fail-closed exits.
        _write_execution_trace(execution_trace_path, execution_trace)
    final_sequence = len(expected.items) - 1
    assert expected.items[final_sequence].command == int(mavutil.mavlink.MAV_CMD_NAV_LAND)
    sequence_before_land = final_sequence - 1
    assert positive_auto_start is not None
    assert positive_auto_start.progress_sequence is not None
    assert observation_target is not None
    start_progress_sequence = positive_auto_start.progress_sequence
    reached: set[int] = {start_progress_sequence}
    execution_deadline_s = time.monotonic() + 120.0
    landed_disarmed = False
    progress_after_link_interruption = False
    max_relative_altitude_m = 0.0
    while time.monotonic() < execution_deadline_s:
        # Direct stock-binary sessions do not stream these states by default.
        # Read-only requests let the accepted telemetry adapter prove flight and landing.
        _request_execution_telemetry(sik_connection, execution_trace)
        telemetry = sik.collect_telemetry(
            observation_target,
            duration_s=3.0,
            cancellation=cancellation,
            require_home=False,
        )
        progress = telemetry.mission.value
        heartbeat = telemetry.heartbeat.value
        position = telemetry.position.value
        extended = telemetry.extended_state.value
        if position is not None:
            max_relative_altitude_m = max(max_relative_altitude_m, position.relative_altitude_m)
        if progress is not None:
            if progress.current_sequence is not None:
                reached.add(progress.current_sequence)
            if progress.last_reached_sequence is not None:
                reached.add(progress.last_reached_sequence)
            progress_after_link_interruption = progress_after_link_interruption or any(
                sequence > start_progress_sequence for sequence in reached
            )
        execution_trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "TYPED_TELEMETRY_SNAPSHOT",
                "fields": {
                    "armed": None if heartbeat is None else heartbeat.armed,
                    "mode": None if heartbeat is None else heartbeat.mode_name,
                    "current_sequence": (None if progress is None else progress.current_sequence),
                    "last_reached_sequence": (
                        None if progress is None else progress.last_reached_sequence
                    ),
                    "relative_altitude_m": (
                        None if position is None else position.relative_altitude_m
                    ),
                    "landed_state": (None if extended is None else extended.landed_state),
                },
            }
        )
        _write_execution_trace(execution_trace_path, execution_trace)
        landed_disarmed = bool(
            heartbeat is not None
            and not heartbeat.armed
            and extended is not None
            and extended.landed_state == int(mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND)
            # Pinned Copter disarms inside NAV_LAND and deliberately returns
            # incomplete, so it does not emit ITEM_REACHED for the Land item.
            and sequence_before_land in reached
            and max_relative_altitude_m >= 2.0
        )
        if landed_disarmed:
            break
    assert landed_disarmed, "mixed mission did not reach Land and return to disarmed landed state"
    assert progress_after_link_interruption, (
        "stock Copter did not show later native mission progress after the "
        "desktop link interruption"
    )

    # Stock Copter remains in AUTO after its native Land completion and auto-disarm.
    # A direct normal-only gateway request is isolated negative evidence: the application
    # review gate is no longer current here and therefore would never transmit it.
    assert selected_target is not None
    arm_gateway = NativeNormalArmGateway(
        _EvidenceNormalArmLink(
            PymavlinkNormalArmLink(
                sik_connection,
                TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
            ),
            execution_trace,
        ),
        clock=clock,
    )
    post_land_target = replace(selected_target, base_mode=0, observed_at_s=clock.now())
    landed_auto_arm_rejection = arm_gateway.request_normal_arm(
        post_land_target,
        target_valid_for_s=3.0,
        cancellation=cancellation,
    )
    assert landed_auto_arm_rejection.state is NormalArmState.REJECTED
    assert landed_auto_arm_rejection.ack_result == int(mavutil.mavlink.MAV_RESULT_FAILED)
    native_auto_start_rejection = _test_only_rejected_mission_start(
        sik_connection,
        execution_trace,
    )
    assert native_auto_start_rejection == int(mavutil.mavlink.MAV_RESULT_DENIED)
    _write_execution_trace(execution_trace_path, execution_trace)

    # Task 104 adds a separate safe sortie in a second verified stock process. A fresh
    # process is required because pinned Copter intentionally remains in unarmable AUTO
    # after the first native Land completion. The second sortie still crosses production
    # USB/SiK, pre-arm, normal Arm, and AUTO boundaries; no mode command or parameter write
    # is introduced to reset flight state.
    service.disconnect()
    sik_link.close()
    second_base_port = int(os.environ["SKYWRITER_SITL_BASE_PORT"]) + 20
    second_session = pinned_sitl_session(
        Path(os.environ["SKYWRITER_SITL_BINARY"]),
        Path(os.environ["SKYWRITER_SITL_STARTUP_DEFAULTS"]),
        evidence_root / "land-here-now-sortie",
        preferred_base_port=second_base_port,
    )
    second_readiness = second_session.__enter__()
    request.addfinalizer(lambda: second_session.__exit__(None, None, None))
    assert second_readiness.target_identity == sitl_target_identity
    assert second_readiness.clean_mission_state.count == 0
    sitl_endpoint = second_readiness.endpoint
    service = ConnectedMissionService()
    service.set_compiled(compiled, mission_revision=2)
    second_usb_connection = _connect(sitl_endpoint)
    second_usb_link = PymavlinkMissionLink(
        second_usb_connection,
        TransportDescriptor(sitl_endpoint.connection_string, TransportKind.USB),
    )
    request.addfinalizer(second_usb_link.close)
    second_usb = ConnectedMavlinkPort(second_usb_link, clock=clock)
    service.discover(second_usb, duration_s=2.5, cancellation=cancellation)
    assert len(service.snapshot.candidates) == 1
    second_usb_target = service.snapshot.candidates[0]
    service.select_target(
        second_usb_target.system_id,
        second_usb_target.component_id,
        now_s=clock.now(),
    )
    service.inspect_onboard(second_usb, cancellation=cancellation)
    assert service.snapshot.onboard is not None
    service.confirm_replacement(True)
    _request_home_position(second_usb_connection)
    service.refresh_telemetry(
        second_usb,
        duration_s=30.0,
        cancellation=cancellation,
        require_home=True,
    )
    service.upload_and_verify(second_usb, now_s=clock.now(), cancellation=cancellation)
    assert service.snapshot.failure is None
    assert service.snapshot.verification_state is ConnectedVerificationState.USB_VERIFIED
    second_expected = service.snapshot.expected_package
    assert isinstance(second_expected, NativeMissionPackage)
    # Native Home is freshly authoritative on each USB upload and may refine after
    # the first flight; the shifted logical mission remains field-for-field exact.
    assert second_expected.items[1:] == expected.items[1:]
    service.disconnect()
    second_usb_link.close()

    sik_connection = _connect(sitl_endpoint)
    sik_link = PymavlinkMissionLink(
        sik_connection,
        TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
    )
    request.addfinalizer(sik_link.close)
    sik = ConnectedMavlinkPort(sik_link, clock=clock)
    service.discover(sik, duration_s=2.5, cancellation=cancellation)
    assert len(service.snapshot.candidates) == 1
    second_sik_target = service.snapshot.candidates[0]
    service.select_target(
        second_sik_target.system_id,
        second_sik_target.component_id,
        now_s=clock.now(),
    )
    _request_home_position(sik_connection)
    service.refresh_telemetry(
        sik,
        duration_s=30.0,
        cancellation=cancellation,
        require_home=True,
    )
    service.reverify_over_sik(sik, now_s=clock.now(), cancellation=cancellation)
    assert service.snapshot.failure is None
    second_sik_snapshot = service.snapshot
    assert second_sik_snapshot.verification_state is ConnectedVerificationState.SIK_VERIFIED

    try:
        second_readiness_deadline_s = time.monotonic() + 30.0
        second_position_health = _wait_ekf_position_ready(
            sik_connection,
            execution_trace,
            timeout_s=max(0.0, second_readiness_deadline_s - time.monotonic()),
        )
        second_prearm_health = _wait_prearm_ready(
            sik_connection,
            execution_trace,
            timeout_s=max(0.0, second_readiness_deadline_s - time.monotonic()),
        )
        assert second_position_health.ready
        assert second_prearm_health.ready
        _request_prearm_review_observations(sik_connection, execution_trace)
        service.refresh_telemetry(sik, duration_s=3.0, cancellation=cancellation)
        assert service.snapshot.selected_target is not None

        second_prearm_service = PrearmReadinessService()
        second_prearm_gateway = NativePrearmGateway(
            _EvidencePrearmLink(
                PymavlinkPrearmLink(
                    sik_connection,
                    TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
                ),
                execution_trace,
            ),
            clock=clock,
        )
        second_prearm_service.request_prearm_checks(
            second_prearm_gateway,
            service.snapshot,
            now_s=clock.now(),
            cancellation=cancellation,
        )
        second_prearm = second_prearm_service.snapshot
        assert second_prearm.request_state is PrearmRequestState.ACCEPTED
        assert second_prearm.native_assessment is NativePrearmAssessment.HEALTHY
        second_prearm_service.acknowledge_review(True, service.snapshot, now_s=clock.now())
        assert second_prearm_service.application_gate_ready_at(service.snapshot, now_s=clock.now())

        second_arm_gateway = NativeNormalArmGateway(
            _EvidenceNormalArmLink(
                PymavlinkNormalArmLink(
                    sik_connection,
                    TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
                ),
                execution_trace,
            ),
            clock=clock,
        )
        second_arm_service = NormalArmService()
        second_arm = second_arm_service.request_normal_arm(
            second_arm_gateway,
            service.snapshot,
            second_prearm_service,
            now_s=clock.now(),
            command_channel_idle=True,
            cancellation=cancellation,
        )
        assert second_arm.state is NormalArmState.ARMED
        assert second_arm.ack_result == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
        service.refresh_telemetry(sik, duration_s=2.0, cancellation=cancellation)
        assert service.snapshot.selected_target is not None
        assert service.snapshot.selected_target.armed

        second_auto_start_gateway = NativeAutoStartGateway(
            _EvidenceAutoStartLink(
                PymavlinkNativeAutoStartLink(
                    sik_connection,
                    TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
                ),
                execution_trace,
            ),
            clock=clock,
        )
        second_auto_start_service = NativeAutoStartService()
        second_auto_start = second_auto_start_service.request_native_auto_start(
            second_auto_start_gateway,
            service.snapshot,
            second_arm,
            now_s=clock.now(),
            command_channel_idle=True,
            cancellation=cancellation,
        )
        assert second_auto_start.state is NativeAutoStartState.RUNNING
        assert second_auto_start.ack_result == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
        assert second_auto_start.authorization is not None

        land_context = _wait_for_land_here_now_context(
            service,
            sik,
            sik_connection,
            execution_trace,
            cancellation,
            last_sequence=second_auto_start.authorization.last_sequence,
        )
        land_service = NativeLandHereNowService()
        land_ready = land_service.synchronize_context(
            land_context,
            second_auto_start,
            now_s=clock.now(),
            command_channel_idle=True,
        )
        assert land_ready.state is NativeLandHereNowState.AVAILABLE
        land_commands_before_confirmation = sum(
            item["message_type"] == "COMMAND_LONG_NATIVE_LAND_HERE_NOW" for item in execution_trace
        )
        land_confirmation = land_service.begin_confirmation(
            land_context,
            second_auto_start,
            now_s=clock.now(),
            command_channel_idle=True,
        )
        assert land_confirmation.state is NativeLandHereNowState.CONFIRMATION_REQUIRED
        assert (
            sum(
                item["message_type"] == "COMMAND_LONG_NATIVE_LAND_HERE_NOW"
                for item in execution_trace
            )
            == land_commands_before_confirmation
        )
        execution_trace.append(
            {
                "elapsed_monotonic_s": time.monotonic(),
                "message_type": "APPLICATION_LAND_HERE_NOW_CONFIRMED",
                "fields": {
                    "abandon_remaining_mission": True,
                    "current_aircraft_location": True,
                    "initial_activation_sent_command": False,
                },
            }
        )
        land_gateway = NativeLandHereNowGateway(
            _EvidenceLandHereNowLink(
                PymavlinkNativeLandHereNowLink(
                    sik_connection,
                    TransportDescriptor(sitl_endpoint.connection_string, TransportKind.SIK),
                ),
                execution_trace,
            ),
            clock=clock,
        )
        land_result = land_service.confirm_native_land_here_now(
            land_gateway,
            land_context,
            second_auto_start,
            now_s=clock.now(),
            command_channel_idle=True,
            cancellation=cancellation,
        )
        assert land_result.state is NativeLandHereNowState.LANDING
        assert land_result.ack_result == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
        assert land_result.land_mode_observed_at_s is not None
        assert land_result.landed_state_observed_at_s is not None
        assert land_result.landed_state == int(mavutil.mavlink.MAV_LANDED_STATE_LANDING)

        final_target = service.snapshot.selected_target
        assert final_target is not None
        second_landing_deadline_s = time.monotonic() + 60.0
        while time.monotonic() < second_landing_deadline_s:
            _request_execution_telemetry(sik_connection, execution_trace)
            telemetry = sik.collect_telemetry(
                final_target,
                duration_s=2.0,
                cancellation=cancellation,
                require_home=False,
            )
            heartbeat = telemetry.heartbeat.value
            position = telemetry.position.value
            extended = telemetry.extended_state.value
            if position is not None:
                second_max_relative_altitude_m = max(
                    second_max_relative_altitude_m,
                    position.relative_altitude_m,
                )
            execution_trace.append(
                {
                    "elapsed_monotonic_s": time.monotonic(),
                    "message_type": "LAND_HERE_NOW_FINAL_TELEMETRY",
                    "fields": {
                        "armed": None if heartbeat is None else heartbeat.armed,
                        "mode": None if heartbeat is None else heartbeat.mode_name,
                        "relative_altitude_m": (
                            None if position is None else position.relative_altitude_m
                        ),
                        "landed_state": (None if extended is None else extended.landed_state),
                    },
                }
            )
            _write_execution_trace(execution_trace_path, execution_trace)
            second_landed_disarmed = bool(
                heartbeat is not None
                and not heartbeat.armed
                and extended is not None
                and extended.landed_state == int(mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND)
            )
            if second_landed_disarmed:
                break
        assert second_landed_disarmed, (
            "Land Here Now sortie did not reach final disarmed On Ground telemetry"
        )
        assert second_max_relative_altitude_m >= 2.0
    finally:
        _write_execution_trace(execution_trace_path, execution_trace)

    land_command_events = tuple(
        item
        for item in execution_trace
        if item["message_type"] == "COMMAND_LONG_NATIVE_LAND_HERE_NOW"
    )
    assert len(land_command_events) == 1
    assert land_command_events[0]["fields"] == {
        "target_system": TARGET.system_id,
        "target_component": TARGET.component_id,
        "command": 21,
        "confirmation": 0,
        "params": [0, 0, 0, 0, 0, 0, 0],
    }
    land_state_requests = tuple(
        item
        for item in execution_trace
        if item["message_type"] == "COMMAND_LONG_LAND_HERE_NOW_REQUEST_EXTENDED_SYS_STATE"
    )
    assert land_state_requests
    assert all(
        item["fields"]
        == {
            "target_system": TARGET.system_id,
            "target_component": TARGET.component_id,
            "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
            "requested_message_id": int(mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE),
        }
        for item in land_state_requests
    )
    assert any(
        item["message_type"] == "COMMAND_ACK"
        and isinstance(fields := item.get("fields"), dict)
        and fields.get("command") == int(mavutil.mavlink.MAV_CMD_NAV_LAND)
        and fields.get("result") == int(mavutil.mavlink.MAV_RESULT_ACCEPTED)
        for item in execution_trace
    )

    evidence = {
        "schema": "skywriter-task-104-connected-sitl-v1",
        "status": "passed",
        "target": {
            "system_id": sitl_target_identity.system_id,
            "component_id": sitl_target_identity.component_id,
            "flight_sw_version": sitl_target_identity.flight_sw_version,
            "flight_custom_version": sitl_target_identity.flight_custom_version,
        },
        "state_transitions": states,
        "transfer_evidence": (
            None
            if service.snapshot.transfer_evidence is None
            else asdict(service.snapshot.transfer_evidence)
        ),
        "expected_items": [item_to_document(item) for item in expected.items],
        "reference_readback": [item_to_document(item) for item in reference_items],
        "reference_verification": verification_to_document(reference),
        "execution": {
            "normal_arm_force_value": 0,
            "stimulus_order": [
                "normal non-forced arm",
                "production native AUTO mission start",
                "production native Pause/Resume during Proceed",
                "production native Pause/Resume during Hold approach",
                "desktop link interruption",
                "observation-only reconnect",
            ],
            "prearm_health": asdict(prearm_health),
            "ekf_position_health": asdict(position_health),
            "auto_start_boundary": "production Task 102 service/gateway/link",
            "pause_resume_boundary": "production Task 103 service/gateway/link",
            "final_land_sequence": final_sequence,
            "last_required_reached_sequence": sequence_before_land,
            "observed_sequences": sorted(reached),
            "max_relative_altitude_m": max_relative_altitude_m,
            "landed_disarmed": landed_disarmed,
            "link_interrupted_at_s": link_interrupted_at_s,
            "link_reconnected_at_s": link_reconnected_at_s,
            "application_state_after_link_loss": (
                None
                if auto_start_state_after_link_loss is None
                else auto_start_state_after_link_loss.value
            ),
            "pause_resume_state_after_link_loss": (
                None
                if pause_resume_state_after_link_loss is None
                else pause_resume_state_after_link_loss.value
            ),
            "progress_sequence_confirming_running": start_progress_sequence,
            "later_progress_after_link_interruption": progress_after_link_interruption,
            "second_sortie": {
                "fresh_process_base_port": second_base_port,
                "mission_setup_boundary": "production USB upload/readback verification",
                "same_logical_mission_verified": second_expected.items[1:] == expected.items[1:],
                "normal_non_forced_arm": True,
                "production_native_auto_start": True,
                "deliberate_land_here_now_confirmation": True,
                "initial_activation_sent_command": False,
                "max_relative_altitude_m": second_max_relative_altitude_m,
                "final_disarmed_on_ground": second_landed_disarmed,
            },
            "trace": execution_trace,
        },
        "native_land_here_now": {
            "confirmation_snapshot": (
                None if land_confirmation is None else asdict(land_confirmation)
            ),
            "positive_application_request": (None if land_result is None else asdict(land_result)),
            "second_prearm_review": (None if second_prearm is None else asdict(second_prearm)),
            "second_normal_arm": None if second_arm is None else asdict(second_arm),
            "second_auto_start": (None if second_auto_start is None else asdict(second_auto_start)),
            "exact_command": 21,
            "parameters": [0, 0, 0, 0, 0, 0, 0],
            "read_only_request_command": 512,
            "read_only_requested_message": 245,
            "ack_is_landing_proof": False,
            "later_land_mode_required": True,
            "later_native_landing_state_required": True,
            "final_disarmed_on_ground": second_landed_disarmed,
            "generic_command_mode_or_coordinate_exposed": False,
        },
        "native_auto_start": {
            "positive_application_request": (
                None if positive_auto_start is None else asdict(positive_auto_start)
            ),
            "test_only_invalid_selector_ack": native_auto_start_rejection,
            "exact_command": 300,
            "production_parameters": [0, 0, 0, 0, 0, 0, 0],
            "ack_is_running_proof": False,
            "selected_target_armed_auto_telemetry_required": True,
            "native_mission_progress_required": True,
            "first_last_selector_exposed_to_production": False,
            "test_only_rejection_parameters": [1, 0, 0, 0, 0, 0, 0],
            "test_only_rejection_location": "tests/sitl/test_connected_integration.py only",
            "desktop_link_interruption_proved_native_execution": (
                progress_after_link_interruption and landed_disarmed
            ),
        },
        "native_pause_resume": {
            "positive_cycles": pause_resume_cycles,
            "exact_command": 193,
            "pause_parameters": [0, 0, 0, 0, 0, 0, 0],
            "resume_parameters": [1, 0, 0, 0, 0, 0, 0],
            "ack_is_state_proof": False,
            "pinned_mission_state_required": True,
            "multiple_mission_action_types": ["Proceed", "Hold approach"],
            "generic_command_or_mode_exposed": False,
        },
        "normal_arm": {
            "positive_application_request": (
                None if positive_arm is None else asdict(positive_arm)
            ),
            "landed_auto_native_rejection": (
                None if landed_auto_arm_rejection is None else asdict(landed_auto_arm_rejection)
            ),
            "exact_command": 400,
            "normal_parameters": [1, 0, 0, 0, 0, 0, 0],
            "ack_is_armed_proof": False,
            "selected_target_armed_telemetry_required": True,
            "production_review_gate_bypassed": False,
            "test_only_rejection_condition": "landed, auto-disarmed stock SITL remains in AUTO",
        },
        "native_prearm": {
            "positive_application_review": (
                None if positive_prearm is None else asdict(prearm_service.snapshot)
            ),
            "armed_native_rejection": (
                None if armed_rejection is None else asdict(armed_rejection)
            ),
            "exact_command": 401,
            "reserved_params": [0, 0, 0, 0, 0, 0, 0],
            "accepted_is_arm_approval": False,
            "production_armed_send_blocked": True,
            "test_only_rejection_stimulus": "existing Task 009 normal non-forced arm scaffold",
        },
        "safety": {
            "stock_sitl_only": True,
            "production_command_surface_added": True,
            "parameter_writes": 0,
            "force_arm": False,
            "real_hardware": False,
            "production_prearm_command_only": True,
            "production_normal_arm_only": True,
            "production_native_auto_start_only": True,
            "production_native_pause_resume_only": True,
            "production_native_land_here_now_only": True,
            "production_generic_mode_or_command_api": False,
            "guided_setpoint_streaming": False,
            "prearm_bypass": False,
            "mid_air_disarm": False,
        },
    }
    (evidence_root / "connected-integration.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    sik_link.close()
