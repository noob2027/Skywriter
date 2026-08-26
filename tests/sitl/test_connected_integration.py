"""Genuine Task 009 production-boundary evidence against pinned stock SITL.

The two vehicle-control stimuli in this file are acceptance-test scaffolding only:
normal arm (force value zero) and AUTO mode are required to make stock ArduCopter
execute an uploaded mission.  They are intentionally absent from ``src/`` and do
not create a reusable command boundary or UI control.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from scripts.sitl.pinned import (
    MAVLINK_DIALECT,
    PrearmHealth,
    SitlEndpoint,
    SitlTargetIdentity,
    prearm_health_from_bitmaps,
)
from skywriter.application.connected import (
    ConnectedMissionService,
    ConnectedVerificationState,
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
from skywriter.infrastructure.mavlink.connected import ConnectedMavlinkPort
from skywriter.infrastructure.mavlink.connection import (
    MavlinkAddress,
    MonotonicClock,
    NeverCancelled,
    PymavlinkMissionLink,
    TransportDescriptor,
    TransportKind,
)

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


def _wait_prearm_ready(
    connection: Any,
    trace: list[dict[str, object]],
    *,
    timeout_s: float = 30.0,
) -> PrearmHealth:
    deadline_s = time.monotonic() + timeout_s
    last = PrearmHealth(present=False, enabled=False, healthy=False)
    while time.monotonic() < deadline_s:
        message = connection.recv_match(
            blocking=True,
            timeout=min(1.0, max(0.0, deadline_s - time.monotonic())),
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
    raise AssertionError(
        "stock SITL did not report a healthy read-only SYS_STATUS pre-arm bit; "
        f"last={last}; native trace=" + json.dumps(trace[-30:], sort_keys=True)
    )


def _normal_arm_then_auto(
    connection: Any,
    trace: list[dict[str, object]],
    prearm_health: PrearmHealth,
) -> None:
    from pymavlink import mavutil

    # Do not emit even a normal arm request until stock ArduPilot reports that
    # its enabled pre-arm checks are healthy on the same live connection.
    assert prearm_health.ready
    arm_command = int(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)
    deadline_s = time.monotonic() + 60.0
    armed = False
    while time.monotonic() < deadline_s and not armed:
        # param2=0 is the normal path.  The force-arm magic value is forbidden.
        connection.mav.command_long_send(
            TARGET.system_id,
            TARGET.component_id,
            arm_command,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        retry_deadline_s = min(deadline_s, time.monotonic() + 5.0)
        while time.monotonic() < retry_deadline_s:
            message = connection.recv_match(
                blocking=True,
                timeout=min(1.0, max(0.0, retry_deadline_s - time.monotonic())),
            )
            if message is None:
                continue
            _record_execution_message(trace, message)
            if message.get_type() != "HEARTBEAT" or message.get_srcSystem() != TARGET.system_id:
                continue
            armed = bool(int(message.base_mode) & int(mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED))
            if armed:
                break
    if not armed:
        raise AssertionError(
            "stock SITL did not accept a normal, non-forced arm request; native trace="
            + json.dumps(trace[-30:], sort_keys=True)
        )

    connection.mav.set_mode_send(
        TARGET.system_id,
        int(mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED),
        3,
    )
    _wait_message(
        connection,
        lambda message: (
            message.get_type() == "HEARTBEAT"
            and message.get_srcSystem() == TARGET.system_id
            and int(message.custom_mode) == 3
        ),
        timeout_s=15.0,
        trace=trace,
    )


def _record_execution_message(trace: list[dict[str, object]], message: Any) -> None:
    if message.get_type() not in {
        "COMMAND_ACK",
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
        settings=MissionSettings(3.0, 3.0, True),
        id="task-009-stock-sitl",
        actions=(
            ProceedAction(GeoPoint(51.5007472, -0.1246254), 3.0),
            HoldAction(GeoPoint(51.5007562, -0.1246130), 3.0, 1.0),
            CircleAction(GeoPoint(51.5007472, -0.1246005), 3.0, 2.0),
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

    execution_trace: list[dict[str, object]] = []
    execution_trace_path = evidence_root / "connected-execution-trace.json"
    try:
        prearm_health = _wait_prearm_ready(sik_connection, execution_trace)
        _normal_arm_then_auto(sik_connection, execution_trace, prearm_health)
    finally:
        # Preserve native pre-arm/arm evidence even when the test fails closed.
        _write_execution_trace(execution_trace_path, execution_trace)
    final_sequence = len(expected.items) - 1
    reached: set[int] = set()
    execution_deadline_s = time.monotonic() + 120.0
    landed_disarmed = False
    while time.monotonic() < execution_deadline_s:
        service.refresh_telemetry(sik, duration_s=3.0, cancellation=cancellation)
        telemetry = service.snapshot.telemetry
        assert telemetry is not None
        progress = telemetry.mission.value
        heartbeat = telemetry.heartbeat.value
        extended = telemetry.extended_state.value
        if progress is not None:
            if progress.current_sequence is not None:
                reached.add(progress.current_sequence)
            if progress.last_reached_sequence is not None:
                reached.add(progress.last_reached_sequence)
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
                    "landed_state": (None if extended is None else extended.landed_state),
                },
            }
        )
        _write_execution_trace(execution_trace_path, execution_trace)
        landed_disarmed = bool(
            heartbeat is not None
            and not heartbeat.armed
            and extended is not None
            and extended.landed_state == 1
            and final_sequence in reached
        )
        if landed_disarmed:
            break
    assert landed_disarmed, "mixed mission did not reach Land and return to disarmed landed state"

    reference_items = _reference_download(sik_connection)
    reference = verify_native_readback(expected, reference_items)
    assert reference.verified
    evidence = {
        "schema": "skywriter-task-009-connected-sitl-v1",
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
            "prearm_health": asdict(prearm_health),
            "auto_stimulus_location": "tests/sitl/test_connected_integration.py only",
            "final_sequence": final_sequence,
            "observed_sequences": sorted(reached),
            "landed_disarmed": landed_disarmed,
            "trace": execution_trace,
        },
        "safety": {
            "stock_sitl_only": True,
            "production_command_surface_added": False,
            "parameter_writes": 0,
            "force_arm": False,
            "real_hardware": False,
        },
    }
    (evidence_root / "connected-integration.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    sik_link.close()
