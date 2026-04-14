"""Tests for idle detection and capture-interval backoff."""

from __future__ import annotations

import pytest

from workflow_recorder.capture.idle_detector import IdleBackoff, IdleDetector


# ---------------------------------------------------------------------------
# IdleDetector
# ---------------------------------------------------------------------------


def test_idle_detector_returns_zero_when_unavailable():
    """On non-Windows or when Win32 init fails, treat user as always active."""
    d = IdleDetector()
    if not d.available:
        # macOS / Linux fallback
        assert d.seconds_since_last_input() == 0.0


def test_idle_detector_returns_nonneg_float_on_windows():
    """On Windows with working Win32, returned value should be a sane non-negative float."""
    d = IdleDetector()
    if d.available:
        v = d.seconds_since_last_input()
        assert isinstance(v, float)
        assert v >= 0.0
        # Reasonable upper bound: tick count wraps every ~49 days
        assert v < 86400 * 60


# ---------------------------------------------------------------------------
# IdleBackoff
# ---------------------------------------------------------------------------


def test_backoff_starts_at_base_interval():
    b = IdleBackoff(base_interval=15, max_interval=300, idle_threshold_seconds=60)
    assert b.current_interval == 15


def test_backoff_stays_at_base_when_user_active():
    b = IdleBackoff(base_interval=15, max_interval=300, idle_threshold_seconds=60)
    # Simulate active user (input within last 5s)
    for _ in range(10):
        new = b.update(seconds_since_last_input=5.0)
        assert new == 15


def test_backoff_doubles_when_idle():
    b = IdleBackoff(
        base_interval=15, max_interval=300,
        idle_threshold_seconds=60, backoff_factor=2.0,
    )
    # Each call simulates one capture cycle while user is idle
    assert b.update(120.0) == 30.0
    assert b.update(120.0) == 60.0
    assert b.update(120.0) == 120.0
    assert b.update(120.0) == 240.0
    assert b.update(120.0) == 300.0  # capped at max_interval
    assert b.update(120.0) == 300.0  # stays capped


def test_backoff_resets_on_user_activity():
    b = IdleBackoff(base_interval=15, max_interval=300, idle_threshold_seconds=60)
    # Walk up to a high interval
    for _ in range(5):
        b.update(120.0)
    assert b.current_interval > 15

    # User comes back
    b.update(seconds_since_last_input=2.0)
    assert b.current_interval == 15


def test_backoff_custom_factor():
    """Backoff factor of 1.5 should grow more gradually."""
    b = IdleBackoff(
        base_interval=10, max_interval=100,
        idle_threshold_seconds=30, backoff_factor=1.5,
    )
    assert b.update(60.0) == 15.0
    assert b.update(60.0) == 22.5
    assert b.update(60.0) == 33.75


def test_backoff_explicit_reset():
    b = IdleBackoff(base_interval=10, max_interval=100, idle_threshold_seconds=30)
    b.update(60.0)
    b.update(60.0)
    assert b.current_interval > 10
    b.reset()
    assert b.current_interval == 10


def test_backoff_threshold_is_inclusive():
    """seconds_since_input == idle_threshold should already trigger backoff."""
    b = IdleBackoff(base_interval=15, max_interval=300, idle_threshold_seconds=60)
    new = b.update(seconds_since_last_input=60.0)
    assert new == 30.0  # already backed off


def test_backoff_rejects_invalid_base():
    with pytest.raises(ValueError):
        IdleBackoff(base_interval=0, max_interval=100, idle_threshold_seconds=30)
    with pytest.raises(ValueError):
        IdleBackoff(base_interval=-1, max_interval=100, idle_threshold_seconds=30)


def test_backoff_rejects_max_below_base():
    with pytest.raises(ValueError):
        IdleBackoff(base_interval=60, max_interval=30, idle_threshold_seconds=30)


def test_backoff_max_equals_base_is_noop():
    """If max == base, backoff should never grow."""
    b = IdleBackoff(base_interval=15, max_interval=15, idle_threshold_seconds=60)
    for _ in range(10):
        assert b.update(seconds_since_last_input=120.0) == 15.0
