"""Generic success and failure values for explicit application outcomes."""

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeGuard, TypeVar

from skywriter.errors import AppError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """A successful result containing a typed value."""

    value: T


@dataclass(frozen=True, slots=True)
class Err:
    """A failed result containing a typed application error."""

    error: AppError


Result: TypeAlias = Ok[T] | Err


def is_ok(result: Result[T]) -> TypeGuard[Ok[T]]:
    """Narrow a result to its success variant."""

    return isinstance(result, Ok)
