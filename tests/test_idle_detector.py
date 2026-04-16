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


# ---------------------------------------------------------------------------
# Three-tier idle backoff (v0.4.9)
# ---------------------------------------------------------------------------


def test_light_idle_bumps_fast_base_to_light_interval():
    """base=1s, light_threshold=3s, light_interval=3s: after 3s idle → 3s."""
    b = IdleBackoff(
        base_interval=1.0, max_interval=300,
        idle_threshold_seconds=60,
        light_idle_threshold_seconds=3.0,
        light_idle_interval_seconds=3.0,
    )
    # Active
    assert b.update(0.5) == 1.0
    assert b.update(2.9) == 1.0
    # Light-idle tier kicks in at 3s
    assert b.update(3.0) == 3.0
    assert b.update(10.0) == 3.0
    assert b.update(59.9) == 3.0


def test_light_idle_then_deep_idle_exponential():
    """After 60s idle we leave light tier and start deep exponential backoff."""
    b = IdleBackoff(
        base_interval=1.0, max_interval=300,
        idle_threshold_seconds=60, backoff_factor=2.0,
        light_idle_threshold_seconds=3.0,
        light_idle_interval_seconds=3.0,
    )
    b.update(10.0)  # light → 3.0
    assert b.current_interval == 3.0
    # Enter deep idle: 3 × 2 = 6, 6 × 2 = 12, ...
    assert b.update(60.0) == 6.0
    assert b.update(70.0) == 12.0
    assert b.update(80.0) == 24.0


def test_light_idle_no_op_when_base_already_at_light_interval():
    """If base >= light_interval, light tier has no effect (nothing to bump to)."""
    b = IdleBackoff(
        base_interval=3.0, max_interval=300,
        idle_threshold_seconds=60,
        light_idle_threshold_seconds=3.0,
        light_idle_interval_seconds=3.0,
    )
    assert b.update(0.5) == 3.0
    assert b.update(10.0) == 3.0  # light-tier, still 3s
    assert b.update(59.9) == 3.0


def test_light_idle_resets_on_activity():
    """User comes back active → interval drops from light back to base."""
    b = IdleBackoff(
        base_interval=1.0, max_interval=300,
        idle_threshold_seconds=60,
        light_idle_threshold_seconds=3.0,
        light_idle_interval_seconds=3.0,
    )
    b.update(10.0)
    assert b.current_interval == 3.0
    b.update(0.1)  # input just happened
    assert b.current_interval == 1.0


def test_light_idle_disabled_when_threshold_none():
    """Backward compat: no light_idle params → 2-tier behavior preserved."""
    b = IdleBackoff(
        base_interval=1.0, max_interval=300,
        idle_threshold_seconds=60,
    )
    # Before deep threshold, interval stays at base
    assert b.update(10.0) == 1.0
    assert b.update(30.0) == 1.0
    # Deep idle kicks in as before
    assert b.update(60.0) == 2.0


def test_light_idle_capped_by_max_interval():
    """Light interval should never exceed max_interval."""
    b = IdleBackoff(
        base_interval=1.0, max_interval=2.0,
        idle_threshold_seconds=60,
        light_idle_threshold_seconds=3.0,
        light_idle_interval_seconds=10.0,  # way above max
    )
    assert b.update(10.0) == 2.0  # clamped to max
