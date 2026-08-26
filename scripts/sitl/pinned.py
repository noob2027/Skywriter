"""Evidence-producing harness for the approved stock ArduCopter 4.6.3 SITL binary.

This module is test infrastructure. Production SKYWriter code must not import it.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO, cast

ARTIFACT_URL = "https://firmware.ardupilot.org/Copter/stable-4.6.3/SITL_x86_64_linux_gnu/arducopter"
ARTIFACT_SHA256 = "7862662092edc2861fc03da3d6fb2f0136d1670e563ca324eb52c1a324d1e14b"
ARTIFACT_SIZE = 7_023_152
RELEASE_TAG_COMMIT = "92b0cd788ec29406f26c6f9c31d5ceedbd1cc538"
PUBLISHED_SITL_COMMIT = "3fc7011a7d3dc047cbb17d8bd98ee94577d144c6"
STARTUP_DEFAULTS_URL = (
    "https://raw.githubusercontent.com/ArduPilot/ardupilot/"
    f"{PUBLISHED_SITL_COMMIT}/Tools/autotest/default_params/copter.parm"
)
STARTUP_DEFAULTS_SHA256 = "5e01345b45d1c6190b28bece5638bbdd4cf1cce35e05bbbf480ab24d2b51aa0e"
STARTUP_DEFAULTS_SIZE = 1_957
STARTUP_DEFAULTS_GIT_BLOB = "17e5c25b26d972a2155b69d2262db08d5f749583"
EXPECTED_FRAME_CLASS = 1
EXPECTED_FRAME_TYPE = 0
EXPECTED_FLIGHT_SW_VERSION = 0x04060380
EXPECTED_CUSTOM_VERSION = "3fc7011a"
MAVLINK_DIALECT = "ardupilotmega"
MAVLINK_VERSION = 2
PYMAVLINK_VERSION = "2.4.41"
DEFAULT_HOME = "51.5007292,-0.1246254,15,0"
# ArduPilot's pinned sim_vehicle mapping translates its user-facing ``quad``
# frame to the ``+`` physics model and the separately verified copter defaults.
DEFAULT_MODEL = "+"
DEFAULT_START_TIME = 1_700_000_000
DEFAULT_STARTUP_TIMEOUT_S = 45.0
DEFAULT_RESPONSE_TIMEOUT_S = 15.0
DEFAULT_SHUTDOWN_TIMEOUT_S = 10.0
PORT_BLOCK_WIDTH = 20
AUTO_PORT_START = 24_000
AUTO_PORT_STOP = 48_000
PREARM_CHECK_BIT = 1 << 28


class HarnessError(RuntimeError):
    """Raised when the pinned SITL harness fails closed."""


@dataclass(frozen=True)
class ArtifactIdentity:
    """Immutable identity of the only executable accepted by this harness."""

    url: str
    sha256: str
    size_bytes: int
    release_tag_commit: str
    published_sitl_commit: str


PINNED_ARTIFACT = ArtifactIdentity(
    url=ARTIFACT_URL,
    sha256=ARTIFACT_SHA256,
    size_bytes=ARTIFACT_SIZE,
    release_tag_commit=RELEASE_TAG_COMMIT,
    published_sitl_commit=PUBLISHED_SITL_COMMIT,
)


@dataclass(frozen=True)
class StartupDefaultsIdentity:
    """Immutable identity of ArduPilot's stock Copter SITL defaults input."""

    url: str
    sha256: str
    size_bytes: int
    published_sitl_commit: str
    git_blob_sha: str
    frame_class: int
    frame_type: int


PINNED_STARTUP_DEFAULTS = StartupDefaultsIdentity(
    url=STARTUP_DEFAULTS_URL,
    sha256=STARTUP_DEFAULTS_SHA256,
    size_bytes=STARTUP_DEFAULTS_SIZE,
    published_sitl_commit=PUBLISHED_SITL_COMMIT,
    git_blob_sha=STARTUP_DEFAULTS_GIT_BLOB,
    frame_class=EXPECTED_FRAME_CLASS,
    frame_type=EXPECTED_FRAME_TYPE,
)


@dataclass(frozen=True)
class VerifiedArtifact:
    """A locally verified copy of the pinned executable."""

    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class VerifiedStartupDefaults:
    """A verified stock startup-defaults file and its effective frame values."""

    path: str
    sha256: str
    size_bytes: int
    frame_class: int
    frame_type: int


@dataclass(frozen=True)
class SitlEndpoint:
    """Isolated endpoint assigned to one harness process."""

    host: str
    tcp_port: int

    @property
    def connection_string(self) -> str:
        return f"tcp:{self.host}:{self.tcp_port}"


@dataclass(frozen=True)
class SitlTargetIdentity:
    """Runtime identity reported by the connected stock target."""

    system_id: int
    component_id: int
    flight_sw_version: int
    flight_custom_version: str
    mavlink_dialect: str
    mavlink_version: int
    pymavlink_version: str


@dataclass(frozen=True)
class CleanMissionState:
    """Read-only confirmation that the wiped target has no mission items."""

    count: int
    mission_type: int


@dataclass(frozen=True)
class PrearmHealth:
    """Read-only interpretation of ArduPilot's SYS_STATUS pre-arm bit."""

    present: bool
    enabled: bool
    healthy: bool

    @property
    def ready(self) -> bool:
        return self.present and self.enabled and self.healthy


@dataclass(frozen=True)
class SitlReadiness:
    """All state proven before a real SITL fixture is yielded."""

    endpoint: SitlEndpoint
    target_identity: SitlTargetIdentity
    clean_mission_state: CleanMissionState
    heartbeat_base_mode: int
    disarmed: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: Path, identity: ArtifactIdentity = PINNED_ARTIFACT) -> VerifiedArtifact:
    """Verify the exact official binary before it can reach subprocess execution."""

    resolved = path.resolve()
    if not resolved.is_file():
        raise HarnessError(f"pinned SITL artifact is missing: {resolved}")
    size = resolved.stat().st_size
    if size != identity.size_bytes:
        raise HarnessError(
            f"pinned SITL artifact size mismatch: expected {identity.size_bytes}, got {size}"
        )
    sha256 = _sha256(resolved)
    if sha256 != identity.sha256:
        raise HarnessError(
            f"pinned SITL artifact SHA-256 mismatch: expected {identity.sha256}, got {sha256}"
        )
    return VerifiedArtifact(path=str(resolved), sha256=sha256, size_bytes=size)


def _required_frame_values(path: Path) -> tuple[int, int]:
    values: dict[str, int] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if fields[0] not in {"FRAME_CLASS", "FRAME_TYPE"}:
            continue
        if len(fields) != 2 or fields[0] in values:
            raise HarnessError(f"invalid or duplicate startup default: {raw_line!r}")
        try:
            values[fields[0]] = int(fields[1])
        except ValueError as error:
            raise HarnessError(f"non-integer startup default: {raw_line!r}") from error
    missing = {"FRAME_CLASS", "FRAME_TYPE"} - values.keys()
    if missing:
        raise HarnessError(f"startup defaults omit required frame values: {sorted(missing)}")
    return values["FRAME_CLASS"], values["FRAME_TYPE"]


def verify_startup_defaults(
    path: Path,
    identity: StartupDefaultsIdentity = PINNED_STARTUP_DEFAULTS,
) -> VerifiedStartupDefaults:
    """Verify the exact official defaults before passing them to stock SITL."""

    resolved = path.resolve()
    if not resolved.is_file():
        raise HarnessError(f"pinned SITL startup defaults are missing: {resolved}")
    size = resolved.stat().st_size
    if size != identity.size_bytes:
        raise HarnessError(
            "pinned SITL startup defaults size mismatch: "
            f"expected {identity.size_bytes}, got {size}"
        )
    sha256 = _sha256(resolved)
    if sha256 != identity.sha256:
        raise HarnessError(
            "pinned SITL startup defaults SHA-256 mismatch: "
            f"expected {identity.sha256}, got {sha256}"
        )
    frame_class, frame_type = _required_frame_values(resolved)
    if (frame_class, frame_type) != (identity.frame_class, identity.frame_type):
        raise HarnessError(
            "pinned SITL startup frame mismatch: expected "
            f"FRAME_CLASS={identity.frame_class}, FRAME_TYPE={identity.frame_type}; got "
            f"FRAME_CLASS={frame_class}, FRAME_TYPE={frame_type}"
        )
    return VerifiedStartupDefaults(
        path=str(resolved),
        sha256=sha256,
        size_bytes=size,
        frame_class=frame_class,
        frame_type=frame_type,
    )


def prearm_health_from_bitmaps(present: int, enabled: int, health: int) -> PrearmHealth:
    """Interpret the standard pre-arm bit without changing vehicle state."""

    return PrearmHealth(
        present=bool(present & PREARM_CHECK_BIT),
        enabled=bool(enabled & PREARM_CHECK_BIT),
        healthy=bool(health & PREARM_CHECK_BIT),
    )


def _ports_for_base(base_port: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    tcp_ports = tuple(range(base_port, base_port + 10))
    udp_ports = tuple(range(base_port + 10, base_port + 14))
    return tcp_ports, udp_ports


def _port_available(port: int, socket_type: int) -> bool:
    with socket.socket(socket.AF_INET, socket_type) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _port_block_available(base_port: int) -> bool:
    tcp_ports, udp_ports = _ports_for_base(base_port)
    return all(_port_available(port, socket.SOCK_STREAM) for port in tcp_ports) and all(
        _port_available(port, socket.SOCK_DGRAM) for port in udp_ports
    )


def _lock_file(stream: TextIO) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_file(stream: TextIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class PortLease:
    """Cross-process lease preventing two harnesses from sharing a port block."""

    def __init__(self, base_port: int, stream: TextIO) -> None:
        self.base_port = base_port
        self._stream = stream
        self._released = False

    @classmethod
    def acquire(cls, preferred_base_port: int | None = None) -> PortLease:
        candidates: Iterator[int]
        if preferred_base_port is not None:
            candidates = iter((preferred_base_port,))
        else:
            candidates = iter(range(AUTO_PORT_START, AUTO_PORT_STOP, PORT_BLOCK_WIDTH))

        lock_root = Path(tempfile.gettempdir()) / "skywriter-sitl-port-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        for base_port in candidates:
            if base_port < 1024 or base_port + PORT_BLOCK_WIDTH > 65_535:
                raise HarnessError(f"invalid SITL base port: {base_port}")
            lock_path = lock_root / f"{base_port}.lock"
            stream = lock_path.open("a+", encoding="utf-8")
            stream.seek(0)
            if not _lock_file(stream):
                stream.close()
                continue
            if not _port_block_available(base_port):
                _unlock_file(stream)
                stream.close()
                continue
            stream.seek(0)
            stream.truncate()
            stream.write(f"pid={os.getpid()}\n")
            stream.flush()
            return cls(base_port, stream)
        requested = (
            f"requested block {preferred_base_port}"
            if preferred_base_port is not None
            else "any managed block"
        )
        raise HarnessError(f"no isolated SITL port block is available ({requested})")

    def release(self) -> None:
        if self._released:
            return
        _unlock_file(self._stream)
        self._stream.close()
        self._released = True

    def __enter__(self) -> PortLease:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def build_command(
    artifact: VerifiedArtifact,
    startup_defaults: VerifiedStartupDefaults,
    base_port: int,
    *,
    home: str = DEFAULT_HOME,
    model: str = DEFAULT_MODEL,
) -> tuple[str, ...]:
    """Build the closed stock-binary invocation used by every harness run."""

    return (
        artifact.path,
        "--base-port",
        str(base_port),
        "--rc-in-port",
        str(base_port + 10),
        "--sim-port-out",
        str(base_port + 11),
        "--sim-port-in",
        str(base_port + 12),
        "--irlock-port",
        str(base_port + 13),
        "--model",
        model,
        "--defaults",
        startup_defaults.path,
        "--home",
        home,
        "--speedup",
        "1",
        "--sysid",
        "1",
        "--start-time",
        str(DEFAULT_START_TIME),
        "--wipe",
    )


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


class ProtocolRecorder:
    def __init__(self, path: Path) -> None:
        self._stream = path.open("w", encoding="utf-8", newline="\n")

    def write(self, direction: str, message_type: str, fields: Mapping[str, object]) -> None:
        entry = {
            "elapsed_monotonic_s": time.monotonic(),
            "direction": direction,
            "message_type": message_type,
            "fields": _json_safe(fields),
        }
        self._stream.write(json.dumps(entry, sort_keys=True) + "\n")
        self._stream.flush()

    def receive(self, message: Any) -> None:
        self.write("vehicle_to_harness", str(message.get_type()), message.to_dict())

    def close(self) -> None:
        self._stream.close()


def _mavutil() -> Any:
    return cast(Any, importlib.import_module("pymavlink.mavutil"))


def _pymavlink_version() -> str:
    package = importlib.import_module("pymavlink")
    return str(getattr(package, "__version__", PYMAVLINK_VERSION))


def _connect(connection_string: str, timeout_s: float, mavutil: ModuleType) -> Any:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return cast(Any, mavutil).mavlink_connection(
                connection_string,
                source_system=255,
                source_component=190,
                dialect=MAVLINK_DIALECT,
                autoreconnect=False,
            )
        except (ConnectionError, OSError) as error:
            last_error = error
            time.sleep(0.2)
    raise HarnessError(f"SITL MAVLink endpoint did not open: {last_error}")


def _receive_until(
    connection: Any,
    recorder: ProtocolRecorder,
    predicate: Callable[[Any], bool],
    *,
    timeout_s: float,
) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        message = connection.recv_match(
            blocking=True, timeout=min(1.0, max(0.0, deadline - time.monotonic()))
        )
        if message is None:
            continue
        recorder.receive(message)
        if message.get_type() != "BAD_DATA" and predicate(message):
            return message
    raise HarnessError("expected MAVLink response was not received before the bounded deadline")


def _send_gcs_heartbeat(connection: Any, recorder: ProtocolRecorder, mavutil: Any) -> None:
    fields = {
        "type": int(mavutil.mavlink.MAV_TYPE_GCS),
        "autopilot": int(mavutil.mavlink.MAV_AUTOPILOT_INVALID),
        "base_mode": 0,
        "custom_mode": 0,
        "system_status": int(mavutil.mavlink.MAV_STATE_ACTIVE),
        "mavlink_version": 3,
    }
    recorder.write("harness_to_vehicle", "HEARTBEAT", fields)
    connection.mav.heartbeat_send(*fields.values())


def _request_autopilot_version(
    connection: Any,
    recorder: ProtocolRecorder,
    mavutil: Any,
    target_system: int,
    target_component: int,
    timeout_s: float,
) -> Any:
    fields = {
        "target_system": target_system,
        "target_component": target_component,
        "command": int(mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE),
        "confirmation": 0,
        "param1": float(mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION),
        "param2": 0.0,
        "param3": 0.0,
        "param4": 0.0,
        "param5": 0.0,
        "param6": 0.0,
        "param7": 0.0,
    }
    recorder.write("harness_to_vehicle", "COMMAND_LONG", fields)
    connection.mav.command_long_send(*fields.values())
    return _receive_until(
        connection,
        recorder,
        lambda message: message.get_type() == "AUTOPILOT_VERSION",
        timeout_s=timeout_s,
    )


def _request_clean_mission_state(
    connection: Any,
    recorder: ProtocolRecorder,
    target_system: int,
    target_component: int,
    timeout_s: float,
) -> CleanMissionState:
    fields = {
        "target_system": target_system,
        "target_component": target_component,
        "mission_type": 0,
    }
    recorder.write("harness_to_vehicle", "MISSION_REQUEST_LIST", fields)
    connection.mav.mission_request_list_send(**fields)
    message = _receive_until(
        connection,
        recorder,
        lambda candidate: (
            candidate.get_type() == "MISSION_COUNT"
            and int(getattr(candidate, "mission_type", 0)) == 0
        ),
        timeout_s=timeout_s,
    )
    state = CleanMissionState(
        count=int(message.count), mission_type=int(getattr(message, "mission_type", 0))
    )
    if state != CleanMissionState(count=0, mission_type=0):
        raise HarnessError(f"wiped SITL did not report a clean mission state: {state}")
    return state


def _custom_version(value: object) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, (list, tuple)):
        raw = bytes(int(item) for item in value)
    else:
        raw = str(value).encode("ascii", errors="replace")
    return raw.rstrip(b"\x00").decode("ascii", errors="replace")


def _verify_readiness(
    connection: Any,
    recorder: ProtocolRecorder,
    endpoint: SitlEndpoint,
    response_timeout_s: float,
) -> SitlReadiness:
    mavutil = _mavutil()
    _send_gcs_heartbeat(connection, recorder, mavutil)
    heartbeat = _receive_until(
        connection,
        recorder,
        lambda message: (
            message.get_type() == "HEARTBEAT"
            and int(message.autopilot) == int(mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA)
        ),
        timeout_s=DEFAULT_STARTUP_TIMEOUT_S,
    )
    target_system = int(heartbeat.get_srcSystem())
    target_component = int(heartbeat.get_srcComponent())
    base_mode = int(heartbeat.base_mode)
    armed_flag = int(mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
    disarmed = base_mode & armed_flag == 0
    if not disarmed:
        raise HarnessError("pinned SITL heartbeat unexpectedly reports an armed vehicle")

    version = _request_autopilot_version(
        connection,
        recorder,
        mavutil,
        target_system,
        target_component,
        response_timeout_s,
    )
    flight_sw_version = int(version.flight_sw_version)
    custom_version = _custom_version(version.flight_custom_version)
    if flight_sw_version != EXPECTED_FLIGHT_SW_VERSION:
        raise HarnessError(
            "pinned SITL flight_sw_version mismatch: "
            f"expected {EXPECTED_FLIGHT_SW_VERSION:#010x}, got {flight_sw_version:#010x}"
        )
    if custom_version != EXPECTED_CUSTOM_VERSION:
        raise HarnessError(
            "pinned SITL custom version mismatch: "
            f"expected {EXPECTED_CUSTOM_VERSION}, got {custom_version}"
        )

    wire_protocol = str(connection.WIRE_PROTOCOL_VERSION)
    if wire_protocol != "2.0":
        raise HarnessError(
            f"pinned SITL MAVLink wire version mismatch: expected 2.0, got {wire_protocol}"
        )

    clean_mission_state = _request_clean_mission_state(
        connection,
        recorder,
        target_system,
        target_component,
        response_timeout_s,
    )
    return SitlReadiness(
        endpoint=endpoint,
        target_identity=SitlTargetIdentity(
            system_id=target_system,
            component_id=target_component,
            flight_sw_version=flight_sw_version,
            flight_custom_version=custom_version,
            mavlink_dialect=MAVLINK_DIALECT,
            mavlink_version=int(wire_protocol.split(".", maxsplit=1)[0]),
            pymavlink_version=_pymavlink_version(),
        ),
        clean_mission_state=clean_mission_state,
        heartbeat_base_mode=base_mode,
        disarmed=disarmed,
    )


def _stop_process(process: subprocess.Popen[str], timeout_s: float) -> int:
    if process.poll() is not None:
        return int(process.returncode)
    if os.name == "posix":
        _signal_process_group(process.pid, "SIGTERM")
    else:
        process.terminate()
    try:
        return int(process.wait(timeout=timeout_s))
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            _signal_process_group(process.pid, "SIGKILL")
        else:
            process.kill()
        try:
            return int(process.wait(timeout=timeout_s))
        except subprocess.TimeoutExpired as error:
            raise HarnessError(
                "SITL process survived bounded terminate and kill deadlines"
            ) from error


def _signal_process_group(pid: int, signal_name: str) -> None:
    posix_os = cast(Any, os)
    posix_signal = cast(Any, signal)
    posix_os.killpg(posix_os.getpgid(pid), int(getattr(posix_signal, signal_name)))


def _wait_ports_released(base_port: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _port_block_available(base_port):
            return True
        time.sleep(0.1)
    return False


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_hashes(output_dir: Path) -> None:
    lines = [
        f"{_sha256(path)}  {path.relative_to(output_dir).as_posix()}"
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (output_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


@contextmanager
def pinned_sitl_session(
    artifact_path: Path,
    startup_defaults_path: Path,
    output_dir: Path,
    *,
    preferred_base_port: int | None = None,
    startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S,
    response_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
    shutdown_timeout_s: float = DEFAULT_SHUTDOWN_TIMEOUT_S,
) -> Iterator[SitlReadiness]:
    """Run one verified stock target and preserve evidence on success or failure."""

    started = time.monotonic()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    error_text: str | None = None
    process: subprocess.Popen[str] | None = None
    connection: Any | None = None
    recorder: ProtocolRecorder | None = None
    lease: PortLease | None = None
    stdout_stream: TextIO | None = None
    stderr_stream: TextIO | None = None
    verified: VerifiedArtifact | None = None
    verified_startup_defaults: VerifiedStartupDefaults | None = None
    command: tuple[str, ...] | None = None
    readiness: SitlReadiness | None = None
    exit_code: int | None = None
    ports_released = False

    try:
        verified = verify_artifact(artifact_path)
        verified_startup_defaults = verify_startup_defaults(startup_defaults_path)
        lease = PortLease.acquire(preferred_base_port)
        endpoint = SitlEndpoint("127.0.0.1", lease.base_port)
        command = build_command(verified, verified_startup_defaults, lease.base_port)
        work_dir = output_dir / "work"
        work_dir.mkdir(exist_ok=False)
        stdout_stream = (output_dir / "sitl-stdout.log").open("w", encoding="utf-8", newline="\n")
        stderr_stream = (output_dir / "sitl-stderr.log").open("w", encoding="utf-8", newline="\n")
        recorder = ProtocolRecorder(output_dir / "protocol.jsonl")
        process = subprocess.Popen(
            command,
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
            stdout=stdout_stream,
            stderr=stderr_stream,
            text=True,
            start_new_session=os.name == "posix",
        )
        mavutil = cast(ModuleType, _mavutil())
        connection = _connect(endpoint.connection_string, startup_timeout_s, mavutil)
        readiness = _verify_readiness(connection, recorder, endpoint, response_timeout_s)
        # Readiness owns no live endpoint after its bounded probe.  Releasing the
        # probe client lets connected-integration tests exercise a clean restart.
        connection.close()
        connection = None
        yield readiness
    except BaseException as error:
        error_text = f"{type(error).__name__}: {error}"
        raise
    finally:
        cleanup_error: BaseException | None = None
        if connection is not None:
            connection.close()
        if process is not None:
            try:
                exit_code = _stop_process(process, shutdown_timeout_s)
            except BaseException as error:
                cleanup_error = error
        if stdout_stream is not None:
            stdout_stream.close()
        if stderr_stream is not None:
            stderr_stream.close()
        if recorder is not None:
            recorder.close()
        if lease is not None:
            ports_released = _wait_ports_released(lease.base_port, shutdown_timeout_s)
            lease.release()
            if not ports_released and cleanup_error is None:
                cleanup_error = HarnessError("SITL ports were not released after bounded shutdown")

        if cleanup_error is not None and error_text is None:
            error_text = f"{type(cleanup_error).__name__}: {cleanup_error}"
        result: dict[str, object] = {
            "status": "passed" if error_text is None else "failed",
            "error": error_text,
            "duration_s": round(time.monotonic() - started, 3),
            "artifact": asdict(verified) if verified is not None else None,
            "pin": asdict(PINNED_ARTIFACT),
            "startup_defaults": (
                asdict(verified_startup_defaults) if verified_startup_defaults is not None else None
            ),
            "startup_defaults_pin": asdict(PINNED_STARTUP_DEFAULTS),
            "command": list(command) if command is not None else None,
            "process_pid": process.pid if process is not None else None,
            "process_exit_code": exit_code,
            "ports_released": ports_released,
            "readiness": asdict(readiness) if readiness is not None else None,
            "safety": {
                "stock_binary_unmodified": True,
                "read_only_protocol_probe": True,
                "parameter_writes": False,
                "stock_startup_defaults_only": True,
                "harness_arm_or_mode_changes": False,
                "harness_mission_uploads": False,
            },
        }
        _write_json(result_path, result)
        _write_hashes(output_dir)
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise cleanup_error
