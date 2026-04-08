"""Tests for retry decorator."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from workflow_recorder.utils.retry import retry


def test_success_on_first_try():
    call_count = 0

    @retry(max_attempts=3, backoff_base=0.01, retryable_exceptions=(ValueError,))
    def succeed():
        nonlocal call_count
        call_count += 1
        return "ok"

    assert succeed() == "ok"
    assert call_count == 1


def test_success_after_retry():
    call_count = 0

    @retry(max_attempts=3, backoff_base=0.01, retryable_exceptions=(ValueError,))
    def fail_then_succeed():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("not yet")
        return "ok"

    with patch("workflow_recorder.utils.retry.time.sleep"):
        result = fail_then_succeed()

    assert result == "ok"
    assert call_count == 3


def test_exhaust_retries():
    call_count = 0

    @retry(max_attempts=2, backoff_base=0.01, retryable_exceptions=(ValueError,))
    def always_fail():
        nonlocal call_count
        call_count += 1
        raise ValueError("always fails")

    with patch("workflow_recorder.utils.retry.time.sleep"):
        with pytest.raises(ValueError, match="always fails"):
            always_fail()

    assert call_count == 2


def test_non_retryable_exception_raises_immediately():
    call_count = 0

    @retry(max_attempts=3, backoff_base=0.01, retryable_exceptions=(ValueError,))
    def raise_type_error():
        nonlocal call_count
        call_count += 1
        raise TypeError("not retryable")

    with pytest.raises(TypeError, match="not retryable"):
        raise_type_error()

    assert call_count == 1


def test_retry_respects_retry_after():
    """If exception has retry_after attribute, use it as wait time."""
    call_count = 0

    class RateLimitError(Exception):
        retry_after = 5.0

    @retry(max_attempts=2, backoff_base=0.01, retryable_exceptions=(RateLimitError,))
    def rate_limited():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise RateLimitError("rate limited")
        return "ok"

    with patch("workflow_recorder.utils.retry.time.sleep") as mock_sleep:
        result = rate_limited()

    assert result == "ok"
    # Should have slept with retry_after value (5.0), not backoff
    mock_sleep.assert_called_once_with(5.0)
