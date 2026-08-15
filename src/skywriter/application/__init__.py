"""Presentation-neutral application state and use-case contracts."""

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
    "ViewName",
    "ViewSelected",
    "reduce_snapshot",
]
