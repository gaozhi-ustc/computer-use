"""Tests for motion-aware capture helpers."""

from __future__ import annotations

import sys
import threading
import time

import pytest


def test_is_mouse_moving_unsupported_returns_false(monkeypatch):
    """On unsupported platforms (not win32 / not darwin) the function
    degrades gracefully. On darwin with Quartz installed the real cursor
    may or may not be moving, so we skip there."""
    from workflow_recorder.capture import cursor_focus
    if sys.platform in ("win32", "darwin"):
        pytest.skip("only meaningful on unsupported platforms")
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


def test_wait_for_click_or_key_unsupported_returns_false_immediately():
    """On platforms without a supported backend the function returns
    False without blocking. On darwin/win32 it polls until timeout, so
    we skip there."""
    from workflow_recorder.capture.cursor_focus import wait_for_click_or_key
    if sys.platform in ("win32", "darwin"):
        pytest.skip("only meaningful on unsupported platforms")
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
    # Unsupported platforms return False immediately; win32/darwin see stop set
    # on the first poll iteration (well under the 50ms poll_interval).
    assert time.monotonic() - start < 0.3
    assert result is False


def test_wait_for_click_or_key_times_out(monkeypatch):
    """With no input events, wait_for_click_or_key returns False at timeout.

    On macOS in an interactive desktop session this test is unreliable —
    any real mouse/keyboard activity during the 0.3s window bumps the
    CGEvent counter and the function returns True. Skip there.
    """
    if sys.platform == "darwin":
        pytest.skip("cannot guarantee a sterile input window on interactive macOS")
    from workflow_recorder.capture.cursor_focus import wait_for_click_or_key
    start = time.monotonic()
    result = wait_for_click_or_key(max_wait_seconds=0.3, poll_interval_ms=50)
    elapsed = time.monotonic() - start
    assert result is False
    if sys.platform == "win32":
        assert elapsed < 1.0


def test_capture_config_defaults_wait_for_click():
    from workflow_recorder.config import CaptureConfig
    cfg = CaptureConfig()
    assert cfg.wait_for_click_when_moving is True
    assert cfg.max_wait_for_click_seconds == 3.0
    assert cfg.interval_seconds == 1.0


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


# ---------------------------------------------------------------------------
# v0.4.4: minimum-gap enforcement (rerun the decision after waiting)
# ---------------------------------------------------------------------------


def _install_capture_stubs(monkeypatch, tmp_path):
    """Install a set of no-op / fake patches so _capture_and_enqueue runs
    to completion. Returns a list that collects each capture_screenshot call."""
    import workflow_recorder.daemon as daemon_mod

    captures = []

    class FakeCR:
        file_path = tmp_path / "x.png"
        timestamp = 0.0
        cursor_x = -1
        cursor_y = -1
        focus_rect = None

    def fake_capture(**kw):
        FakeCR.file_path.write_bytes(b"PNG")
        captures.append(1)
        return FakeCR

    monkeypatch.setattr(daemon_mod, "capture_screenshot", fake_capture)
    monkeypatch.setattr(daemon_mod, "get_active_window", lambda: None)
    monkeypatch.setattr(daemon_mod, "should_skip_frame", lambda *a, **kw: False)
    monkeypatch.setattr(daemon_mod, "apply_masks", lambda *a, **kw: None)
    return captures


def test_waits_remainder_when_stationary_and_too_recent(tmp_path, monkeypatch):
    """Mouse stationary + elapsed < interval_seconds -> wait the remainder."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon, RecordingSession
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False
    cfg.capture.wait_for_click_when_moving = True
    cfg.capture.interval_seconds = 0.3  # fast test

    wait_calls = []
    monkeypatch.setattr(cursor_focus, "is_mouse_moving", lambda *a, **kw: False)
    monkeypatch.setattr(cursor_focus, "wait_for_click_or_key",
                        lambda *a, **kw: wait_calls.append(kw) or True)
    captures = _install_capture_stubs(monkeypatch, tmp_path)

    d = Daemon(cfg)
    d.session = RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    d._last_capture_time = time.monotonic()  # pretend we just captured

    t0 = time.monotonic()
    d._capture_and_enqueue()
    elapsed = time.monotonic() - t0

    assert 0.25 <= elapsed <= 0.8, f"expected ~0.3s wait, got {elapsed:.2f}s"
    assert wait_calls == []  # motion path NOT invoked (mouse stationary)
    assert len(captures) == 1


def test_captures_immediately_when_stationary_and_gap_satisfied(tmp_path, monkeypatch):
    """Mouse stationary + elapsed >= interval_seconds -> capture right away."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon, RecordingSession
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False
    cfg.capture.interval_seconds = 0.3

    monkeypatch.setattr(cursor_focus, "is_mouse_moving", lambda *a, **kw: False)
    captures = _install_capture_stubs(monkeypatch, tmp_path)

    d = Daemon(cfg)
    d.session = RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    d._last_capture_time = time.monotonic() - 5.0  # long ago

    t0 = time.monotonic()
    d._capture_and_enqueue()
    assert time.monotonic() - t0 < 0.05
    assert len(captures) == 1


def test_first_capture_is_immediate(tmp_path, monkeypatch):
    """First capture (no prior timestamp) should not wait."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon, RecordingSession
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False
    cfg.capture.interval_seconds = 5.0

    monkeypatch.setattr(cursor_focus, "is_mouse_moving", lambda *a, **kw: False)
    captures = _install_capture_stubs(monkeypatch, tmp_path)

    d = Daemon(cfg)
    d.session = RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    assert d._last_capture_time == 0.0

    t0 = time.monotonic()
    d._capture_and_enqueue()
    assert time.monotonic() - t0 < 0.05  # no waiting on first capture
    assert len(captures) == 1
    assert d._last_capture_time > 0.0  # updated after capture


def test_stop_event_aborts_during_min_gap_wait(tmp_path, monkeypatch):
    """Setting stop_event mid-wait aborts without capturing."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon, RecordingSession
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False
    cfg.capture.interval_seconds = 5.0  # long wait

    monkeypatch.setattr(cursor_focus, "is_mouse_moving", lambda *a, **kw: False)
    monkeypatch.setattr(cursor_focus, "wait_for_click_or_key", lambda *a, **kw: True)
    captures = _install_capture_stubs(monkeypatch, tmp_path)

    d = Daemon(cfg)
    d.session = RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    d._last_capture_time = time.monotonic()  # just now, needs 5s wait

    threading.Timer(0.1, d._stop_event.set).start()
    t0 = time.monotonic()
    d._capture_and_enqueue()
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"expected quick abort, took {elapsed:.2f}s"
    assert captures == []


def test_mouse_becomes_moving_during_wait_triggers_motion_path(tmp_path, monkeypatch):
    """If mouse starts moving during the min-gap sleep, next iteration
    routes to the motion path (wait_for_click_or_key)."""
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon, RecordingSession
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False
    cfg.capture.wait_for_click_when_moving = True
    cfg.capture.interval_seconds = 0.2  # very short wait
    cfg.capture.max_wait_for_click_seconds = 0.05

    # First is_mouse_moving call -> False (so we enter stationary branch and wait)
    # After the wait, second call -> True (so motion branch fires)
    moving_sequence = iter([False, True])
    monkeypatch.setattr(cursor_focus, "is_mouse_moving",
                        lambda *a, **kw: next(moving_sequence, True))
    wait_calls = []
    monkeypatch.setattr(cursor_focus, "wait_for_click_or_key",
                        lambda *a, **kw: wait_calls.append(kw) or False)
    captures = _install_capture_stubs(monkeypatch, tmp_path)

    d = Daemon(cfg)
    d.session = RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path
    d._last_capture_time = time.monotonic()  # force stationary-branch wait

    d._capture_and_enqueue()
    # Exactly one wait_for_click call on the second iteration
    assert len(wait_calls) == 1
    assert len(captures) == 1
