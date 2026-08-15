"""Immutable application state tests."""

from skywriter.application import (
    ApplicationSnapshot,
    ApplicationStarted,
    ViewName,
    ViewSelected,
    reduce_snapshot,
)
from skywriter.result import is_ok


def test_reducer_returns_a_new_snapshot() -> None:
    original = ApplicationSnapshot()

    started = reduce_snapshot(original, ApplicationStarted())
    assert is_ok(started)
    selected = reduce_snapshot(started.value, ViewSelected(ViewName.PREFLIGHT))

    assert is_ok(selected)
    assert original == ApplicationSnapshot()
    assert selected.value.active_view is ViewName.PREFLIGHT
    assert selected.value.revision == 2
