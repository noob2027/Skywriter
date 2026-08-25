"""Immutable placeholder state and reducer for the application shell."""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeAlias, assert_never

from skywriter.result import Ok, Result


class ViewName(StrEnum):
    """Top-level foundation views available in the desktop shell."""

    BUILDER = "builder"
    PREFLIGHT = "preflight"
    FLIGHT = "flight"
    CONNECTED = "connected"


@dataclass(frozen=True, slots=True)
class ApplicationSnapshot:
    """Current immutable shell state."""

    active_view: ViewName = ViewName.BUILDER
    revision: int = 0


@dataclass(frozen=True, slots=True)
class ApplicationStarted:
    """Record that the desktop shell has started."""


@dataclass(frozen=True, slots=True)
class ViewSelected:
    """Request selection of a top-level shell view."""

    view: ViewName


ApplicationEvent: TypeAlias = ApplicationStarted | ViewSelected


def reduce_snapshot(
    snapshot: ApplicationSnapshot,
    event: ApplicationEvent,
) -> Result[ApplicationSnapshot]:
    """Apply one typed shell event without mutating the input snapshot."""

    match event:
        case ApplicationStarted():
            return Ok(replace(snapshot, revision=snapshot.revision + 1))
        case ViewSelected(view=view):
            return Ok(replace(snapshot, active_view=view, revision=snapshot.revision + 1))
        case unreachable:
            assert_never(unreachable)
