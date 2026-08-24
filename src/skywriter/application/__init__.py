"""Presentation-neutral application state and use-case contracts."""

from skywriter.application.mission_service import (
    MissionRepository,
    OfflineMissionError,
    OfflineMissionService,
    OfflineMissionSnapshot,
)
from skywriter.application.state import (
    ApplicationEvent,
    ApplicationSnapshot,
    ApplicationStarted,
    ViewName,
    ViewSelected,
    reduce_snapshot,
)

__all__ = [
    "ApplicationEvent",
    "ApplicationSnapshot",
    "ApplicationStarted",
    "MissionRepository",
    "OfflineMissionError",
    "OfflineMissionService",
    "OfflineMissionSnapshot",
    "ViewName",
    "ViewSelected",
    "reduce_snapshot",
]
