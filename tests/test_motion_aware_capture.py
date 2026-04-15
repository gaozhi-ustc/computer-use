"""Tests for motion-aware capture helpers."""

from __future__ import annotations

import sys
import threading
import time

import pytest


def test_is_mouse_moving_non_windows_returns_false(monkeypatch):
    """On non-Windows the function should degrade gracefully."""
    from workflow_recorder.capture import cursor_focus
    if sys.platform == "win32":
        pytest.skip("only meaningful on non-Windows")
    assert cursor_focus.is_mouse_moving(sample_interval_ms=10) is False


def test_is_mouse_moving_same_position_returns_false(monkeypatch):
    """If GetCursorPos returns the same point twice, is_mouse_moving is False."""
    from workflow_recorder.capture import cursor_focus
    monkeypatch.setattr(cursor_focus, "get_cursor_position", lambda: (100, 200))
    assert cursor_focus.is_mouse_moving(sample_interval_ms=10) is False


def test_is_mouse_moving_different_position_returns_true(monkeypatch):
    """If the two samples differ, is_mouse_moving is True."""
    from workflow_recorder.capture import cursor_focus
    positions = iter([(100, 200), (150, 210)])
    monkeypatch.setattr(cursor_focus, "get_cursor_position", lambda: next(positions))
    assert cursor_focus.is_mouse_moving(sample_interval_ms=10) is True


def test_is_mouse_moving_failed_sample_returns_false(monkeypatch):
    """If get_cursor_position returns None, is_mouse_moving is False."""
    from workflow_recorder.capture import cursor_focus
    monkeypatch.setattr(cursor_focus, "get_cursor_position", lambda: None)
    assert cursor_focus.is_mouse_moving(sample_interval_ms=10) is False


def test_wait_for_click_or_key_non_windows_returns_false_immediately():
    """On non-Windows the function returns False without blocking."""
    from workflow_recorder.capture.cursor_focus import wait_for_click_or_key
    if sys.platform == "win32":
        pytest.skip("only meaningful on non-Windows")
    start = time.monotonic()
    assert wait_for_click_or_key(max_wait_seconds=1.0, poll_interval_ms=50) is False
    assert time.monotonic() - start < 0.2


def test_wait_for_click_or_key_respects_stop_event():
    """If stop_event is set, wait_for_click_or_key should return quickly."""
    from workflow_recorder.capture.cursor_focus import wait_for_click_or_key
    stop = threading.Event()
    stop.set()
    start = time.monotonic()
    result = wait_for_click_or_key(max_wait_seconds=5.0, poll_interval_ms=50, stop_event=stop)
    # Non-Windows returns False before checking stop; Windows sees stop set.
    assert time.monotonic() - start < 0.3
    assert result is False


def test_wait_for_click_or_key_times_out(monkeypatch):
    """With no input events, wait_for_click_or_key returns False at timeout."""
    # Force the path to be "Windows"-ish: if actually on Windows this will
    # run against the real API (no input in test environment -> timeout).
    # On non-Windows, the function returns False immediately regardless.
    from workflow_recorder.capture.cursor_focus import wait_for_click_or_key
    start = time.monotonic()
    result = wait_for_click_or_key(max_wait_seconds=0.3, poll_interval_ms=50)
    elapsed = time.monotonic() - start
    assert result is False
    if sys.platform == "win32":
        # Must have spent close to max_wait, give generous slack
        assert elapsed < 1.0


def test_capture_config_defaults_wait_for_click():
    from workflow_recorder.config import CaptureConfig
    cfg = CaptureConfig()
    assert cfg.wait_for_click_when_moving is True
    assert cfg.max_wait_for_click_seconds == 3.0
    assert cfg.interval_seconds == 3.0


def test_daemon_waits_for_click_when_mouse_moving(tmp_path, monkeypatch):
    """Daemon should call wait_for_click_or_key when is_mouse_moving() is True."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False  # no uploader
    cfg.idle_detection.enabled = False
    cfg.capture.wait_for_click_when_moving = True
    cfg.capture.max_wait_for_click_seconds = 0.5

    moving_calls = []
    wait_calls = []

    monkeypatch.setattr(cursor_focus, "is_mouse_moving",
                        lambda *a, **kw: (moving_calls.append(1), True)[1])
    monkeypatch.setattr(cursor_focus, "wait_for_click_or_key",
                        lambda *a, **kw: (wait_calls.append(kw), True)[1])

    # Mock capture_screenshot to avoid touching mss
    import workflow_recorder.daemon as daemon_mod
    class FakeCR:
        file_path = tmp_path / "x.png"
        timestamp = 0.0
        cursor_x = -1
        cursor_y = -1
        focus_rect = None
    monkeypatch.setattr(daemon_mod, "capture_screenshot",
                        lambda **kw: (FakeCR.file_path.write_bytes(b"PNG"), FakeCR)[1])
    monkeypatch.setattr(daemon_mod, "get_active_window", lambda: None)
    monkeypatch.setattr(daemon_mod, "should_skip_frame", lambda *a, **kw: False)
    monkeypatch.setattr(daemon_mod, "apply_masks", lambda *a, **kw: None)

    d = Daemon(cfg)
    d.session = daemon_mod.RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    d._capture_and_enqueue()

    assert len(moving_calls) == 1, "is_mouse_moving should be called once per capture"
    assert len(wait_calls) == 1, "wait_for_click_or_key should be called when moving"
    assert wait_calls[0].get("max_wait_seconds") == 0.5


def test_daemon_skips_wait_when_not_moving(tmp_path, monkeypatch):
    """If mouse is not moving, daemon captures immediately without waiting."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False
    cfg.capture.wait_for_click_when_moving = True

    wait_calls = []
    monkeypatch.setattr(cursor_focus, "is_mouse_moving", lambda *a, **kw: False)
    monkeypatch.setattr(cursor_focus, "wait_for_click_or_key",
                        lambda *a, **kw: (wait_calls.append(1), True)[1])

    import workflow_recorder.daemon as daemon_mod
    class FakeCR:
        file_path = tmp_path / "x.png"
        timestamp = 0.0
        cursor_x = -1
        cursor_y = -1
        focus_rect = None
    monkeypatch.setattr(daemon_mod, "capture_screenshot",
                        lambda **kw: (FakeCR.file_path.write_bytes(b"PNG"), FakeCR)[1])
    monkeypatch.setattr(daemon_mod, "get_active_window", lambda: None)
    monkeypatch.setattr(daemon_mod, "should_skip_frame", lambda *a, **kw: False)
    monkeypatch.setattr(daemon_mod, "apply_masks", lambda *a, **kw: None)

    d = Daemon(cfg)
    d.session = daemon_mod.RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    d._capture_and_enqueue()

    assert len(wait_calls) == 0


def test_daemon_skips_wait_when_feature_disabled(tmp_path, monkeypatch):
    """If config.capture.wait_for_click_when_moving is False, no wait happens."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False
    cfg.capture.wait_for_click_when_moving = False  # disabled

    called = []
    monkeypatch.setattr(cursor_focus, "is_mouse_moving",
                        lambda *a, **kw: called.append("check") or True)
    monkeypatch.setattr(cursor_focus, "wait_for_click_or_key",
                        lambda *a, **kw: called.append("wait") or True)

    import workflow_recorder.daemon as daemon_mod
    class FakeCR:
        file_path = tmp_path / "x.png"
        timestamp = 0.0
        cursor_x = -1
        cursor_y = -1
        focus_rect = None
    monkeypatch.setattr(daemon_mod, "capture_screenshot",
                        lambda **kw: (FakeCR.file_path.write_bytes(b"PNG"), FakeCR)[1])
    monkeypatch.setattr(daemon_mod, "get_active_window", lambda: None)
    monkeypatch.setattr(daemon_mod, "should_skip_frame", lambda *a, **kw: False)
    monkeypatch.setattr(daemon_mod, "apply_masks", lambda *a, **kw: None)

    d = Daemon(cfg)
    d.session = daemon_mod.RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    d._capture_and_enqueue()

    assert called == [], "Disabled config means neither helper should be called"
