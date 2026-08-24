"""Run the isolated Task 005A probe against an official stock Copter SITL.

This script is evidence infrastructure, not a production adapter. It starts a
caller-supplied SITL binary in a disposable directory, talks to the stock TCP
MAVLink endpoint, and records every relevant message. It never arms, changes a
mode, writes a parameter, or exposes controls to the application.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import subprocess
import tempfile
import time
import traceback
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, TextIO, cast

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import __version__ as pymavlink_version  # noqa: E402
from pymavlink import mavutil  # noqa: E402

JsonObject = dict[str, object]

MISSION_TYPE = 0
SITL_HOME = "51.5007292,-0.1246254,15,0"
FLOAT_FIELDS = ("param1", "param2", "param3", "param4", "altitude_m")
WIRE_TO_FIXTURE_FIELDS = {
    "seq": "sequence",
    "frame": "frame",
    "command": "command",
    "current": "current",
    "autocontinue": "autocontinue",
    "param1": "param1",
    "param2": "param2",
    "param3": "param3",
    "param4": "param4",
    "x": "latitude_e7",
    "y": "longitude_e7",
    "z": "altitude_m",
    "mission_type": "mission_type",
}


class EvidenceRecorder:
    """Append timestamped protocol evidence as durable JSON Lines."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._started = time.monotonic()

    def write(self, direction: str, message_type: str, fields: Mapping[str, object]) -> None:
        record = {
            "elapsed_s": round(time.monotonic() - self._started, 6),
            "direction": direction,
            "message_type": message_type,
            "fields": _json_safe(dict(fields)),
        }
        self._stream.write(json.dumps(record, sort_keys=True) + "\n")
        self._stream.flush()

    def receive(self, message: object) -> None:
        mavlink_message = cast(Any, message)
        fields = mavlink_message.to_dict()
        header = getattr(mavlink_message, "_header", None)
        packet = bytes(mavlink_message.get_msgbuf())
        fields["_wire"] = {
            "magic": packet[0] if packet else None,
            "packet_length": len(packet),
            "sequence": getattr(header, "seq", None),
            "source_system": mavlink_message.get_srcSystem(),
            "source_component": mavlink_message.get_srcComponent(),
        }
        self.write("vehicle_to_probe", mavlink_message.get_type(), fields)


def _json_safe(value: object) -> object:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return repr(value)


def _load_fixture(path: Path) -> list[JsonObject]:
    root = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(root, dict):
        raise TypeError("fixture root must be an object")
    items = root.get("expected_items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise TypeError("fixture expected_items must be an object array")
    return cast(list[JsonObject], items)


def _connect(connection_string: str, timeout_s: float) -> Any:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return mavutil.mavlink_connection(
                connection_string,
                source_system=255,
                source_component=190,
                dialect="ardupilotmega",
                autoreconnect=False,
            )
        except (ConnectionError, OSError) as error:
            last_error = error
            time.sleep(0.2)
    raise TimeoutError(f"SITL MAVLink endpoint did not open: {last_error}")


def _receive_until(
    connection: Any,
    recorder: EvidenceRecorder,
    predicate: Callable[[Any], bool],
    *,
    timeout_s: float = 30.0,
) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        message = connection.recv_match(
            blocking=True, timeout=min(1.0, deadline - time.monotonic())
        )
        if message is None:
            continue
        recorder.receive(message)
        if message.get_type() != "BAD_DATA" and predicate(message):
            return message
    raise TimeoutError("expected MAVLink response was not received")


def _send_gcs_heartbeat(connection: Any, recorder: EvidenceRecorder) -> None:
    fields = {
        "type": int(mavutil.mavlink.MAV_TYPE_GCS),
        "autopilot": int(mavutil.mavlink.MAV_AUTOPILOT_INVALID),
        "base_mode": 0,
        "custom_mode": 0,
        "system_status": int(mavutil.mavlink.MAV_STATE_ACTIVE),
        "mavlink_version": 3,
    }
    recorder.write("probe_to_vehicle", "HEARTBEAT", fields)
    connection.mav.heartbeat_send(*fields.values())


def _wait_vehicle_heartbeat(connection: Any, recorder: EvidenceRecorder) -> Any:
    return _receive_until(
        connection,
        recorder,
        lambda message: (
            message.get_type() == "HEARTBEAT"
            and int(message.autopilot) == int(mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA)
        ),
        timeout_s=45.0,
    )


def _request_message(
    connection: Any,
    recorder: EvidenceRecorder,
    target_system: int,
    target_component: int,
    message_id: int,
    message_name: str,
) -> JsonObject:
    fields = _command_fields(
        target_system,
        target_component,
        int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
        (float(message_id), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )
    recorder.write("probe_to_vehicle", "COMMAND_LONG", fields)
    connection.mav.command_long_send(*fields.values())
    response = _receive_until(
        connection,
        recorder,
        lambda message: message.get_type() == message_name,
    )
    return cast(JsonObject, _json_safe(response.to_dict()))


def _command_fields(
    target_system: int,
    target_component: int,
    command: int,
    params: tuple[float, float, float, float, float, float, float],
) -> JsonObject:
    return {
        "target_system": target_system,
        "target_component": target_component,
        "command": command,
        "confirmation": 0,
        **{f"param{index}": value for index, value in enumerate(params, start=1)},
    }


def _probe_command_ack(
    connection: Any,
    recorder: EvidenceRecorder,
    target_system: int,
    target_component: int,
    command: int,
    params: tuple[float, float, float, float, float, float, float],
) -> JsonObject:
    fields = _command_fields(target_system, target_component, command, params)
    recorder.write("probe_to_vehicle", "COMMAND_LONG", fields)
    connection.mav.command_long_send(*fields.values())
    ack = _receive_until(
        connection,
        recorder,
        lambda message: message.get_type() == "COMMAND_ACK" and int(message.command) == command,
    )
    document = cast(JsonObject, _json_safe(ack.to_dict()))
    result = int(ack.result)
    enum_entry = mavutil.mavlink.enums["MAV_RESULT"].get(result)
    document["result_name"] = enum_entry.name if enum_entry is not None else "UNKNOWN"
    return document


def _download_mission(
    connection: Any,
    recorder: EvidenceRecorder,
    target_system: int,
    target_component: int,
) -> JsonObject:
    request_fields = {
        "target_system": target_system,
        "target_component": target_component,
        "mission_type": MISSION_TYPE,
    }
    recorder.write("probe_to_vehicle", "MISSION_REQUEST_LIST", request_fields)
    connection.mav.mission_request_list_send(**request_fields)
    count_message = _receive_until(
        connection,
        recorder,
        lambda message: (
            message.get_type() == "MISSION_COUNT"
            and int(getattr(message, "mission_type", MISSION_TYPE)) == MISSION_TYPE
        ),
    )
    count = int(count_message.count)
    items: list[JsonObject] = []
    for sequence in range(count):
        item_request = {
            "target_system": target_system,
            "target_component": target_component,
            "seq": sequence,
            "mission_type": MISSION_TYPE,
        }
        recorder.write("probe_to_vehicle", "MISSION_REQUEST_INT", item_request)
        connection.mav.mission_request_int_send(**item_request)
        item_message = _receive_until(
            connection,
            recorder,
            lambda message, expected=sequence: (
                message.get_type() == "MISSION_ITEM_INT" and int(message.seq) == expected
            ),
        )
        items.append(_canonical_item(item_message.to_dict()))
    return {"count": count, "items": items}


def _upload_mission(
    connection: Any,
    recorder: EvidenceRecorder,
    target_system: int,
    target_component: int,
    fixture_items: list[JsonObject],
) -> JsonObject:
    count_fields = {
        "target_system": target_system,
        "target_component": target_component,
        "count": len(fixture_items),
        "mission_type": MISSION_TYPE,
    }
    recorder.write("probe_to_vehicle", "MISSION_COUNT", count_fields)
    connection.mav.mission_count_send(**count_fields)

    sent_sequences: list[int] = []
    request_messages: list[str] = []
    while True:
        response = _receive_until(
            connection,
            recorder,
            lambda message: (
                message.get_type() in {"MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"}
            ),
        )
        response_type = response.get_type()
        if response_type == "MISSION_ACK":
            result = int(response.type)
            enum_entry = mavutil.mavlink.enums["MAV_MISSION_RESULT"].get(result)
            return {
                "ack": cast(JsonObject, _json_safe(response.to_dict())),
                "result_name": enum_entry.name if enum_entry is not None else "UNKNOWN",
                "request_messages": request_messages,
                "sent_protocol": "MISSION_ITEM_INT",
                "sent_sequences": sent_sequences,
            }
        request_messages.append(response_type)
        sequence = int(response.seq)
        if not 0 <= sequence < len(fixture_items):
            raise RuntimeError(f"target requested out-of-range mission sequence {sequence}")
        fields = _mission_item_int_fields(
            target_system,
            target_component,
            fixture_items[sequence],
        )
        # ArduCopter 4.6.3 advertises MISSION_INT but may issue the legacy request.
        # Respond with the compiler boundary's integer item to test actual stock
        # acceptance while retaining the request mismatch as compatibility evidence.
        recorder.write("probe_to_vehicle", "MISSION_ITEM_INT", fields)
        connection.mav.mission_item_int_send(**fields)
        sent_sequences.append(sequence)


def _mission_item_int_fields(
    target_system: int,
    target_component: int,
    item: Mapping[str, object],
) -> JsonObject:
    return {
        "target_system": target_system,
        "target_component": target_component,
        "seq": int(cast(int, item["sequence"])),
        "frame": int(cast(int, item["frame"])),
        "command": int(cast(int, item["command"])),
        "current": int(cast(bool, item["current"])),
        "autocontinue": int(cast(bool, item["autocontinue"])),
        "param1": float(cast(float, item["param1"])),
        "param2": float(cast(float, item["param2"])),
        "param3": float(cast(float, item["param3"])),
        "param4": float(cast(float, item["param4"])),
        "x": int(cast(int, item["latitude_e7"])),
        "y": int(cast(int, item["longitude_e7"])),
        "z": float(cast(float, item["altitude_m"])),
        "mission_type": int(cast(int, item["mission_type"])),
    }


def _canonical_item(wire_item: Mapping[str, object]) -> JsonObject:
    canonical: JsonObject = {}
    for wire_name, fixture_name in WIRE_TO_FIXTURE_FIELDS.items():
        value = wire_item.get(wire_name, MISSION_TYPE if wire_name == "mission_type" else None)
        if value is None:
            raise KeyError(f"MISSION_ITEM_INT omitted required field {wire_name}")
        if fixture_name in {"current", "autocontinue"}:
            canonical[fixture_name] = bool(value)
        elif fixture_name in FLOAT_FIELDS:
            canonical[fixture_name] = float(cast(float, value))
        else:
            canonical[fixture_name] = int(cast(int, value))
    return canonical


def _float32(value: object) -> float:
    return struct.unpack("<f", struct.pack("<f", float(cast(float, value))))[0]


def _compare_items(expected_items: list[JsonObject], actual_items: list[JsonObject]) -> JsonObject:
    comparisons: list[JsonObject] = []
    all_match = len(expected_items) == len(actual_items)
    for index, expected in enumerate(expected_items):
        actual = actual_items[index] if index < len(actual_items) else {}
        fields: list[JsonObject] = []
        for name, expected_value in expected.items():
            wire_expected = _float32(expected_value) if name in FLOAT_FIELDS else expected_value
            actual_value = actual.get(name)
            matches = actual_value == wire_expected
            field: JsonObject = {
                "field": name,
                "compiler_value": expected_value,
                "wire_expected": wire_expected,
                "readback_value": actual_value,
                "matches": matches,
            }
            if name in FLOAT_FIELDS:
                field["wire_delta_from_compiler"] = float(wire_expected) - float(
                    cast(float, expected_value)
                )
            fields.append(field)
            all_match = all_match and matches
        comparisons.append({"sequence": index, "fields": fields})
    return {
        "expected_count": len(expected_items),
        "readback_count": len(actual_items),
        "all_fields_match_after_float32_normalization": all_match,
        "items": comparisons,
    }


def _drain_messages(
    connection: Any, recorder: EvidenceRecorder, duration_s: float
) -> list[JsonObject]:
    messages: list[JsonObject] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        message = connection.recv_match(
            blocking=True, timeout=min(0.2, deadline - time.monotonic())
        )
        if message is None:
            continue
        recorder.receive(message)
        if message.get_type() == "STATUSTEXT":
            messages.append(cast(JsonObject, _json_safe(message.to_dict())))
    return messages


def _run_probe(sitl_path: Path, fixture_path: Path, output_path: Path) -> JsonObject:
    output_path.mkdir(parents=True, exist_ok=True)
    fixture_items = _load_fixture(fixture_path)
    result: JsonObject = {
        "status": "failed",
        "safety": {
            "real_hardware": False,
            "arm_requested": False,
            "mode_changed": False,
            "parameter_writes": 0,
            "mission_target": "ephemeral stock SITL only",
        },
        "probe": {
            "pymavlink_version": pymavlink_version,
            "dialect": "ardupilotmega",
            "wire_protocol": mavutil.mavlink.WIRE_PROTOCOL_VERSION,
            "fixture": fixture_path.as_posix(),
        },
    }
    stdout_path = output_path / "sitl.stdout.log"
    stderr_path = output_path / "sitl.stderr.log"
    protocol_path = output_path / "mavlink-messages.jsonl"
    process: subprocess.Popen[bytes] | None = None
    connection: Any | None = None
    with (
        tempfile.TemporaryDirectory(prefix="skywriter-task005a-") as working_directory,
        stdout_path.open("wb") as stdout_stream,
        stderr_path.open("wb") as stderr_stream,
        protocol_path.open("w", encoding="utf-8", newline="\n") as protocol_stream,
    ):
        recorder = EvidenceRecorder(protocol_stream)
        command = [
            str(sitl_path.resolve()),
            "--model",
            "quad",
            "--home",
            SITL_HOME,
            "--speedup",
            "1",
        ]
        result["sitl"] = {"command": command, "home": SITL_HOME}
        try:
            process = subprocess.Popen(
                command,
                cwd=working_directory,
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
            )
            connection = _connect("tcp:127.0.0.1:5760", timeout_s=30.0)
            _send_gcs_heartbeat(connection, recorder)
            heartbeat = _wait_vehicle_heartbeat(connection, recorder)
            target_system = int(heartbeat.get_srcSystem())
            target_component = int(heartbeat.get_srcComponent())
            result["vehicle_heartbeat"] = cast(JsonObject, _json_safe(heartbeat.to_dict()))
            result["target"] = {
                "system": target_system,
                "component": target_component,
            }

            result["autopilot_version"] = _request_message(
                connection,
                recorder,
                target_system,
                target_component,
                int(mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION),
                "AUTOPILOT_VERSION",
            )
            result["home_position"] = _request_message(
                connection,
                recorder,
                target_system,
                target_component,
                int(mavutil.mavlink.MAVLINK_MSG_ID_HOME_POSITION),
                "HOME_POSITION",
            )
            result["mission_before_upload"] = _download_mission(
                connection, recorder, target_system, target_component
            )
            result["mission_upload"] = _upload_mission(
                connection,
                recorder,
                target_system,
                target_component,
                fixture_items,
            )
            mission_after = _download_mission(connection, recorder, target_system, target_component)
            result["mission_after_upload"] = mission_after
            result["compiler_comparison"] = _compare_items(
                fixture_items,
                cast(list[JsonObject], mission_after["items"]),
            )

            prearm_command = int(mavutil.mavlink.MAV_CMD_RUN_PREARM_CHECKS)
            result["prearm_request_ack"] = _probe_command_ack(
                connection,
                recorder,
                target_system,
                target_component,
                prearm_command,
                (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )
            result["prearm_statustext"] = _drain_messages(connection, recorder, 2.0)

            pause_command = int(mavutil.mavlink.MAV_CMD_DO_PAUSE_CONTINUE)
            result["pause_ack"] = _probe_command_ack(
                connection,
                recorder,
                target_system,
                target_component,
                pause_command,
                (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )
            result["continue_ack"] = _probe_command_ack(
                connection,
                recorder,
                target_system,
                target_component,
                pause_command,
                (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )
            result["final_statustext"] = _drain_messages(connection, recorder, 1.0)
            result["status"] = "passed"
        except Exception as error:
            result["error"] = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            }
        finally:
            if connection is not None:
                connection.close()
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10.0)
                result["sitl_exit_code_after_probe_termination"] = process.returncode
    return result


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_args(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sitl", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> int:
    args = _parse_args(arguments)
    output = cast(Path, args.output)
    result = _run_probe(cast(Path, args.sitl), cast(Path, args.fixture), output)
    _write_json(output / "probe-result.json", result)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
