"""Tests for cursor / focus helpers (Windows + macOS + unsupported fallback)."""

from __future__ import annotations

import sys

import pytest


def test_get_cursor_position_returns_tuple_or_none():
    """On Windows/macOS returns (x, y) when the OS call works; otherwise None."""
    from workflow_recorder.capture.cursor_focus import get_cursor_position
    result = get_cursor_position()
    if sys.platform in ("win32", "darwin"):
        # On macOS the Quartz import may not be installed in CI envs; tolerate None.
        if result is None:
            return
        assert len(result) == 2
        x, y = result
        assert isinstance(x, int)
        assert isinstance(y, int)
    else:
        assert result is None


def test_get_focus_rect_returns_rect_or_none():
    """Returns [x1,y1,x2,y2] when a focused control exists, else None.

    On Windows the call always works but may return None if no control
    has focus. On macOS it additionally requires the hosting process to
    have Accessibility permission — otherwise we expect None.
    """
    from workflow_recorder.capture.cursor_focus import get_focus_rect
    result = get_focus_rect()
    if sys.platform in ("win32", "darwin"):
        assert result is None or (isinstance(result, list) and len(result) == 4)
    else:
        assert result is None


def test_screen_to_image_coords_identity_no_downscale():
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # monitor at (0,0), 1920x1080, no downscale
    x, y = screen_to_image_coords(
        screen_x=500, screen_y=300,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=1.0,
    )
    assert x == 500
    assert y == 300


def test_screen_to_image_coords_with_downscale():
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # 1920x1080 monitor at (0,0), downscale 0.5
    x, y = screen_to_image_coords(
        screen_x=1000, screen_y=600,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=0.5,
    )
    assert x == 500
    assert y == 300


def test_screen_to_image_coords_offset_monitor():
    """Secondary monitor at (1920, 0) — coords should be relative to its origin."""
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    x, y = screen_to_image_coords(
        screen_x=2500, screen_y=400,
        monitor_left=1920, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=1.0,
    )
    assert x == 580  # 2500 - 1920
    assert y == 400


def test_screen_to_image_coords_returns_none_if_outside_monitor():
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # Cursor on secondary monitor, asking for primary's coords
    result = screen_to_image_coords(
        screen_x=3000, screen_y=400,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=1.0,
    )
    assert result is None


def test_screen_to_image_coords_clamps_to_image_bounds():
    """If cursor is right at the edge, result stays within image dimensions."""
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # Cursor at (1919, 1079) on a 1920x1080 monitor with 0.5 downscale -> (959, 539)
    x, y = screen_to_image_coords(
        screen_x=1919, screen_y=1079,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=0.5,
    )
    assert 0 <= x < 960
    assert 0 <= y < 540
