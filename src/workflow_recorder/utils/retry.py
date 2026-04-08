"""Retry decorator with exponential backoff."""

from __future__ import annotations

import functools
import time
from typing import Type

import structlog

log = structlog.get_logger()


def retry(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,),
):
    """Decorator that retries a function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts.
        backoff_base: Base for exponential backoff in seconds (2 -> 2s, 4s, 8s).
        retryable_exceptions: Tuple of exception types to retry on.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        log.error("retry_exhausted",
                                  func=func.__name__,
                                  attempts=max_attempts,
                                  error=str(e))
                        raise
                    wait = backoff_base ** attempt
                    # Check for Retry-After header (OpenAI rate limit)
                    retry_after = getattr(e, "retry_after", None)
                    if retry_after:
                        wait = float(retry_after)
                    log.warning("retrying",
                                func=func.__name__,
                                attempt=attempt,
                                wait=wait,
                                error=str(e))
                    time.sleep(wait)
            raise last_exc  # unreachable but satisfies type checker
        return wrapper
    return decorator
