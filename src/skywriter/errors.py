"""Typed, presentation-neutral application errors."""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable categories for failures crossing application boundaries."""

    INVALID_STATE = "invalid_state"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class AppError:
    """An immutable failure value safe to pass between layers."""

    code: ErrorCode
    message: str
    context: tuple[tuple[str, str], ...] = ()
