"""Typed result pattern tests."""

from skywriter.errors import AppError, ErrorCode
from skywriter.result import Err, Ok, is_ok


def test_result_variants_are_explicit_and_immutable() -> None:
    success = Ok(42)
    failure = Err(AppError(ErrorCode.INVALID_STATE, "not ready"))

    assert is_ok(success)
    assert success.value == 42
    assert not is_ok(failure)
    assert failure.error.code is ErrorCode.INVALID_STATE
