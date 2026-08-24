"""Pure ArduCopter 4.6.3 mission compatibility envelope.

The logical compiler remains the deterministic source of mission meaning.  This
module adapts that output to the stock vehicle's native mission representation;
it performs no I/O and knows nothing about SITL, serial links, USB, or SiK.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeAlias

from skywriter.domain.compiled import CompiledMission, MissionCommand, MissionType

_GLOBAL = 0
_GLOBAL_RELATIVE_ALT = 3
_GLOBAL_RELATIVE_ALT_INT = 6
_ITEM_FIELDS = (
    "sequence",
    "frame",
    "command",
    "current",
    "autocontinue",
    "param1",
    "param2",
    "param3",
    "param4",
    "latitude_e7",
    "longitude_e7",
    "altitude_m",
    "mission_type",
)
_FLOAT_FIELDS = ("param1", "param2", "param3", "param4", "altitude_m")


class HomeUnresolvedReason(StrEnum):
    """Closed reasons that prevent an uploadable native package."""

    UNCONNECTED = "unconnected"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    INVALID = "invalid"
    WRONG_VEHICLE = "wrong_vehicle"


@dataclass(frozen=True, slots=True)
class VehicleIdentity:
    """Opaque identity established by a future caller-owned connection boundary."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("vehicle identity must be a non-empty string")


@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    """Caller-supplied native home observation with explicit authority and lifetime."""

    vehicle: VehicleIdentity
    latitude_e7: int
    longitude_e7: int
    altitude_m: float
    captured_at_s: float
    valid_for_s: float
    authoritative: bool

    def __post_init__(self) -> None:
        if not isinstance(self.vehicle, VehicleIdentity):
            raise TypeError("vehicle must be a VehicleIdentity")
        _require_integer(self.latitude_e7, "latitude_e7")
        _require_integer(self.longitude_e7, "longitude_e7")
        for name in ("altitude_m", "captured_at_s", "valid_for_s"):
            _require_finite_number(getattr(self, name), name)
        object.__setattr__(self, "altitude_m", float(self.altitude_m))
        object.__setattr__(self, "captured_at_s", float(self.captured_at_s))
        object.__setattr__(self, "valid_for_s", float(self.valid_for_s))
        if not isinstance(self.authoritative, bool):
            raise TypeError("authoritative must be a boolean")


@dataclass(frozen=True, slots=True)
class HomeUnresolved:
    """Typed fail-closed state; this value can never be uploaded as coordinates."""

    reason: HomeUnresolvedReason
    detail: str
    vehicle: VehicleIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, HomeUnresolvedReason):
            raise TypeError("reason must be a HomeUnresolvedReason")
        if not isinstance(self.detail, str) or not self.detail:
            raise ValueError("detail must be a non-empty string")
        if self.vehicle is not None and not isinstance(self.vehicle, VehicleIdentity):
            raise TypeError("vehicle must be a VehicleIdentity or None")


HomeState: TypeAlias = HomeSnapshot | HomeUnresolved


@dataclass(frozen=True, slots=True)
class NativeMissionItem:
    """Transport-independent values matching one MISSION_ITEM_INT payload."""

    sequence: int
    frame: int
    command: int
    current: bool
    autocontinue: bool
    param1: float
    param2: float
    param3: float
    param4: float
    latitude_e7: int
    longitude_e7: int
    altitude_m: float
    mission_type: int

    def __post_init__(self) -> None:
        for name in ("sequence", "frame", "command", "latitude_e7", "longitude_e7"):
            _require_integer(getattr(self, name), name)
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if not isinstance(self.current, bool) or not isinstance(self.autocontinue, bool):
            raise TypeError("current and autocontinue must be booleans")
        for name in _FLOAT_FIELDS:
            _require_finite_number(getattr(self, name), name)
            object.__setattr__(self, name, float(getattr(self, name)))
        _require_integer(self.mission_type, "mission_type")


@dataclass(frozen=True, slots=True)
class NativeMissionPackage:
    """Uploadable native items bound to one validated vehicle home snapshot."""

    vehicle: VehicleIdentity
    home: HomeSnapshot
    items: tuple[NativeMissionItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.vehicle, VehicleIdentity):
            raise TypeError("vehicle must be a VehicleIdentity")
        if not isinstance(self.home, HomeSnapshot):
            raise TypeError("home must be a HomeSnapshot")
        items = tuple(self.items)
        if not items or not all(isinstance(item, NativeMissionItem) for item in items):
            raise TypeError("items must contain NativeMissionItem values")
        if tuple(item.sequence for item in items) != tuple(range(len(items))):
            raise ValueError("native mission sequences must start at zero with no gaps")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class FieldMismatch:
    """One exact fail-closed comparison difference."""

    location: str
    field: str
    expected: object
    actual: object


@dataclass(frozen=True, slots=True)
class HomeVerification:
    """Independent verification of native sequence-zero home."""

    mismatches: tuple[FieldMismatch, ...]

    @property
    def verified(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True, slots=True)
class MissionVerification:
    """Verification of shifted logical items, excluding native home."""

    mismatches: tuple[FieldMismatch, ...]

    @property
    def verified(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True, slots=True)
class NativeReadbackVerification:
    """Combined result whose two boundaries remain separately visible."""

    home: HomeVerification
    mission: MissionVerification

    @property
    def verified(self) -> bool:
        return self.home.verified and self.mission.verified


NORMALIZATION_WHITELIST: tuple[str, ...] = (
    "MISSION_ITEM_INT float fields are compared after exact IEEE-754 binary32 packing.",
    "The sequence-zero home waypoint remains MAV_FRAME_GLOBAL (0).",
    "Home altitude is read back from integer centimeters multiplied by binary32 0.01f.",
    "DO_CHANGE_SPEED frame 6 is read back as MAV_FRAME_GLOBAL (0).",
    "Navigation command frame 6 is read back as MAV_FRAME_GLOBAL_RELATIVE_ALT (3).",
    "NAV_LOITER_TIME param3 zero is read back as one.",
    "NAV_LAND param4 zero is read back as one.",
)


def prepare_native_mission(
    compiled: CompiledMission,
    *,
    target_vehicle: VehicleIdentity,
    home: HomeState,
    now_s: float,
) -> NativeMissionPackage | HomeUnresolved:
    """Translate logical items only when home is authoritative, fresh, and same-vehicle."""

    if not isinstance(compiled, CompiledMission):
        raise TypeError("compiled must be a CompiledMission")
    if not isinstance(target_vehicle, VehicleIdentity):
        raise TypeError("target_vehicle must be a VehicleIdentity")
    _require_finite_number(now_s, "now_s")
    if isinstance(home, HomeUnresolved):
        return home
    if not isinstance(home, HomeSnapshot):
        raise TypeError("home must be a HomeSnapshot or HomeUnresolved")
    invalid = _validate_home(home, target_vehicle=target_vehicle, now_s=float(now_s))
    if invalid is not None:
        return invalid

    home_item = NativeMissionItem(
        sequence=0,
        frame=_GLOBAL,
        command=int(MissionCommand.NAV_WAYPOINT),
        current=False,
        autocontinue=True,
        param1=0.0,
        param2=0.0,
        param3=0.0,
        param4=0.0,
        latitude_e7=home.latitude_e7,
        longitude_e7=home.longitude_e7,
        altitude_m=home.altitude_m,
        mission_type=int(MissionType.MISSION),
    )
    shifted = tuple(
        NativeMissionItem(
            sequence=item.sequence + 1,
            frame=int(item.frame),
            command=int(item.command),
            current=False,
            autocontinue=item.autocontinue,
            param1=item.param1,
            param2=item.param2,
            param3=item.param3,
            param4=item.param4,
            latitude_e7=item.latitude_e7,
            longitude_e7=item.longitude_e7,
            altitude_m=item.altitude_m,
            mission_type=int(item.mission_type),
        )
        for item in compiled.items
    )
    return NativeMissionPackage(target_vehicle, home, (home_item, *shifted))


def canonicalize_expected(package: NativeMissionPackage) -> tuple[NativeMissionItem, ...]:
    """Apply only the closed ArduCopter 4.6.3 readback normalization whitelist."""

    if not isinstance(package, NativeMissionPackage):
        raise TypeError("package must be a NativeMissionPackage")
    canonical: list[NativeMissionItem] = []
    for item in package.items:
        _require_approved_upload_shape(item)
        frame = item.frame
        param3 = item.param3
        param4 = item.param4
        altitude_m = item.altitude_m
        if item.sequence == 0:
            altitude_m = _home_altitude_readback(item.altitude_m)
        if item.sequence > 0:
            frame = (
                _GLOBAL
                if item.command == int(MissionCommand.DO_CHANGE_SPEED)
                else _GLOBAL_RELATIVE_ALT
            )
            if item.command == int(MissionCommand.NAV_LOITER_TIME):
                if item.param3 != 0.0:
                    raise ValueError("unapproved NAV_LOITER_TIME param3 normalization input")
                param3 = 1.0
            if item.command == int(MissionCommand.NAV_LAND):
                if item.param4 != 0.0:
                    raise ValueError("unapproved NAV_LAND param4 normalization input")
                param4 = 1.0
        canonical.append(
            replace(
                item,
                frame=frame,
                param1=_float32(item.param1),
                param2=_float32(item.param2),
                param3=_float32(param3),
                param4=_float32(param4),
                altitude_m=_float32(altitude_m),
            )
        )
    return tuple(canonical)


def verify_native_home(
    package: NativeMissionPackage, downloaded_home: NativeMissionItem | None
) -> HomeVerification:
    """Verify sequence-zero home independently from the logical mission items."""

    expected = canonicalize_expected(package)[0]
    if downloaded_home is None:
        return HomeVerification((FieldMismatch("home", "count", 1, 0),))
    actual = _float32_item(downloaded_home)
    return HomeVerification(_compare_item("home", expected, actual))


def verify_native_readback(
    package: NativeMissionPackage,
    downloaded_items: tuple[NativeMissionItem, ...],
) -> NativeReadbackVerification:
    """Fail closed on all fields; no tolerance or undocumented reversal is applied."""

    if not isinstance(package, NativeMissionPackage):
        raise TypeError("package must be a NativeMissionPackage")
    actual_items = tuple(downloaded_items)
    if not all(isinstance(item, NativeMissionItem) for item in actual_items):
        raise TypeError("downloaded_items must contain NativeMissionItem values")

    home = verify_native_home(package, actual_items[0] if actual_items else None)
    expected_mission = canonicalize_expected(package)[1:]
    actual_mission = tuple(_float32_item(item) for item in actual_items[1:])
    mismatches: list[FieldMismatch] = []
    if len(actual_mission) != len(expected_mission):
        mismatches.append(
            FieldMismatch("mission", "count", len(expected_mission), len(actual_mission))
        )
    for index, expected in enumerate(expected_mission):
        if index >= len(actual_mission):
            break
        mismatches.extend(_compare_item(f"mission[{index}]", expected, actual_mission[index]))
    return NativeReadbackVerification(home, MissionVerification(tuple(mismatches)))


def item_to_document(item: NativeMissionItem) -> dict[str, object]:
    """Return the stable evidence representation for a native item."""

    if not isinstance(item, NativeMissionItem):
        raise TypeError("item must be a NativeMissionItem")
    return {name: getattr(item, name) for name in _ITEM_FIELDS}


def item_from_document(document: dict[str, object]) -> NativeMissionItem:
    """Parse an evidence/readback item without normalizing untrusted fields."""

    if not isinstance(document, dict):
        raise TypeError("document must be an object")
    unknown = set(document) - set(_ITEM_FIELDS)
    missing = set(_ITEM_FIELDS) - set(document)
    if unknown or missing:
        raise ValueError(
            f"native item fields differ: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    return NativeMissionItem(**{name: document[name] for name in _ITEM_FIELDS})  # type: ignore[arg-type]


def verification_to_document(result: NativeReadbackVerification) -> dict[str, object]:
    """Serialize the exact compatibility decision for retained evidence."""

    def mismatches(values: tuple[FieldMismatch, ...]) -> list[dict[str, object]]:
        return [
            {
                "location": value.location,
                "field": value.field,
                "expected": value.expected,
                "actual": value.actual,
            }
            for value in values
        ]

    return {
        "verified": result.verified,
        "home": {
            "verified": result.home.verified,
            "mismatches": mismatches(result.home.mismatches),
        },
        "mission": {
            "verified": result.mission.verified,
            "mismatches": mismatches(result.mission.mismatches),
        },
    }


def _validate_home(
    home: HomeSnapshot, *, target_vehicle: VehicleIdentity, now_s: float
) -> HomeUnresolved | None:
    if home.vehicle != target_vehicle:
        return HomeUnresolved(
            HomeUnresolvedReason.WRONG_VEHICLE,
            "home snapshot belongs to a different vehicle",
            home.vehicle,
        )
    if not home.authoritative:
        return HomeUnresolved(
            HomeUnresolvedReason.INVALID,
            "home snapshot is not authoritative",
            home.vehicle,
        )
    if not (-900_000_000 <= home.latitude_e7 <= 900_000_000):
        return HomeUnresolved(
            HomeUnresolvedReason.INVALID, "home latitude is invalid", home.vehicle
        )
    if not (-1_800_000_000 <= home.longitude_e7 <= 1_800_000_000):
        return HomeUnresolved(
            HomeUnresolvedReason.INVALID, "home longitude is invalid", home.vehicle
        )
    if (home.latitude_e7, home.longitude_e7, home.altitude_m) == (0, 0, 0.0):
        return HomeUnresolved(
            HomeUnresolvedReason.INVALID,
            "numeric 0,0,0 cannot represent an unresolved home",
            home.vehicle,
        )
    altitude_cm = round(home.altitude_m * 100.0)
    if not math.isclose(home.altitude_m, altitude_cm / 100.0, abs_tol=1e-9):
        return HomeUnresolved(
            HomeUnresolvedReason.INVALID,
            "home altitude must preserve the native centimeter value",
            home.vehicle,
        )
    if home.valid_for_s <= 0.0:
        return HomeUnresolved(
            HomeUnresolvedReason.INVALID, "home validity must be positive", home.vehicle
        )
    age_s = now_s - home.captured_at_s
    if age_s < 0.0:
        return HomeUnresolved(
            HomeUnresolvedReason.INVALID, "home timestamp is in the future", home.vehicle
        )
    if age_s > home.valid_for_s:
        return HomeUnresolved(HomeUnresolvedReason.STALE, "home snapshot is stale", home.vehicle)
    return None


def _require_approved_upload_shape(item: NativeMissionItem) -> None:
    approved_commands = {int(command) for command in MissionCommand}
    if item.command not in approved_commands:
        raise ValueError(f"unapproved mission command {item.command}")
    if item.mission_type != int(MissionType.MISSION):
        raise ValueError(f"unapproved mission type {item.mission_type}")
    if not item.autocontinue or item.current:
        raise ValueError("native upload flags differ from the approved contract")
    if item.sequence == 0:
        if item.command != int(MissionCommand.NAV_WAYPOINT) or item.frame != _GLOBAL:
            raise ValueError("native sequence zero must be the global home waypoint")
    elif item.frame != _GLOBAL_RELATIVE_ALT_INT:
        raise ValueError("logical native upload items must retain integer relative-alt frame 6")


def _float32_item(item: NativeMissionItem) -> NativeMissionItem:
    return replace(
        item,
        param1=_float32(item.param1),
        param2=_float32(item.param2),
        param3=_float32(item.param3),
        param4=_float32(item.param4),
        altitude_m=_float32(item.altitude_m),
    )


def _compare_item(
    location: str, expected: NativeMissionItem, actual: NativeMissionItem
) -> tuple[FieldMismatch, ...]:
    return tuple(
        FieldMismatch(location, field, getattr(expected, field), getattr(actual, field))
        for field in _ITEM_FIELDS
        if getattr(expected, field) != getattr(actual, field)
    )


def _float32(value: float) -> float:
    return float(struct.unpack("<f", struct.pack("<f", value))[0])


def _home_altitude_readback(altitude_m: float) -> float:
    altitude_cm = float(round(altitude_m * 100.0))
    return _float32(_float32(altitude_cm) * _float32(0.01))


def _require_integer(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


def _require_finite_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise TypeError(f"{name} must be a finite number")
