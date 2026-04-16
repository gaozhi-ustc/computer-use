"""Tests for the 'drop idle duplicate frame' filter (v0.4.8).

Logic under test (in Daemon._capture_and_enqueue):
    if drop_idle_duplicate_frames:
        if hash(new_frame) ~~ hash(last_frame) and no input since prev capture:
            unlink the file, bump frames_skipped, do NOT enqueue
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(path: Path, color=(128, 128, 128)) -> Path:
    """Create a PNG with structure derived from `color`. Solid colors
    don't differentiate under phash (all reduce to flat low-frequency
    content), so we draw a checkerboard whose cell size depends on the
    color tuple — that way different `color` values produce different
    phashes, while identical `color` values produce identical phashes."""
    from PIL import ImageDraw
    img = Image.new("RGB", (128, 128), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Cell size derived deterministically from color so equal tuples
    # produce equal images, but different tuples produce different
    # patterns the phash can distinguish.
    cell = max(4, (sum(color) % 24) + 4)
    for y in range(0, 128, cell):
        for x in range(0, 128, cell):
            if ((x // cell) + (y // cell)) % 2 == 0:
                draw.rectangle([x, y, x + cell, y + cell], fill=color)
    img.save(path, format="PNG")
    return path


def _install_capture_stubs(monkeypatch, tmp_path, *, color_sequence):
    """Patch capture_screenshot to write a PNG with a configurable color
    each call. Returns a list that records each call's resulting file path."""
    import workflow_recorder.daemon as daemon_mod

    captures: list[Path] = []
    colors = iter(color_sequence)

    class FakeCR:
        timestamp = 0.0
        cursor_x = -1
        cursor_y = -1
        focus_rect = None
        file_path: Path | None = None  # set per-call

    def fake_capture(**kw):
        # Each call writes a unique PNG with the next color
        idx = len(captures)
        path = tmp_path / f"frame-{idx}.png"
        _make_png(path, color=next(colors))
        cr = type("CR", (), {})()
        cr.file_path = path
        cr.timestamp = float(idx)
        cr.cursor_x = -1
        cr.cursor_y = -1
        cr.focus_rect = None
        captures.append(path)
        return cr

    monkeypatch.setattr(daemon_mod, "capture_screenshot", fake_capture)
    monkeypatch.setattr(daemon_mod, "get_active_window", lambda: None)
    monkeypatch.setattr(daemon_mod, "should_skip_frame", lambda *a, **kw: False)
    monkeypatch.setattr(daemon_mod, "apply_masks", lambda *a, **kw: None)
    return captures


def _make_daemon(tmp_path, monkeypatch, *, hash_threshold=2,
                 drop_enabled=True, idle_secs=0.0):
    from workflow_recorder.config import AppConfig
    from workflow_recorder.daemon import Daemon, RecordingSession
    from workflow_recorder.capture import cursor_focus

    cfg = AppConfig(employee_id="E001")
    cfg.server.enabled = False
    cfg.idle_detection.enabled = False  # we feed idle directly
    cfg.capture.wait_for_click_when_moving = False
    cfg.capture.interval_seconds = 0.0  # no min-gap delay in tests
    cfg.capture.drop_idle_duplicate_frames = drop_enabled
    cfg.capture.duplicate_hash_threshold = hash_threshold

    # No mouse motion → take immediate path
    monkeypatch.setattr(cursor_focus, "is_mouse_moving", lambda *a, **kw: False)

    d = Daemon(cfg)
    d.session = RecordingSession(employee_id="E001")
    d._capture_dir = tmp_path

    # Inject a stub IdleDetector that returns a controllable "seconds since
    # last input" value. The daemon will read this on each capture.
    class StubIdle:
        def __init__(self, secs):
            self._secs = secs

        def set(self, secs):
            self._secs = secs

        def seconds_since_last_input(self) -> float:
            return self._secs

    d._idle_detector = StubIdle(idle_secs)
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_frame_always_kept(tmp_path, monkeypatch):
    """No prior hash to compare against → first capture is never dropped."""
    captures = _install_capture_stubs(monkeypatch, tmp_path,
                                      color_sequence=[(10, 20, 30)])
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=999.0)  # idle forever

    enqueued = []
    monkeypatch.setattr(d, "uploader", None)
    # Daemon enqueues by calling self.uploader.enqueue when uploader set.
    # With server.enabled=False, that path is skipped, so we check
    # frames_captured / frames_skipped counters instead.

    d._capture_and_enqueue()
    assert d.session.frames_captured == 1
    assert d.session.frames_skipped == 0
    assert captures[0].exists(), "first frame's file must NOT be deleted"


def test_identical_frame_with_no_input_is_dropped(tmp_path, monkeypatch):
    """Same image + zero input since last capture → dropped."""
    color = (50, 100, 150)
    _install_capture_stubs(monkeypatch, tmp_path,
                           color_sequence=[color, color])
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=999.0)

    d._capture_and_enqueue()  # frame 1: kept (first)
    assert d.session.frames_captured == 1
    assert d.session.frames_skipped == 0

    # Idle stays high (no input) — second identical frame should be dropped
    d._capture_and_enqueue()
    assert d.session.frames_captured == 1, "second identical frame must be dropped"
    assert d.session.frames_skipped == 1


def test_identical_frame_with_input_is_kept(tmp_path, monkeypatch):
    """Same image but user pressed/clicked since last capture → kept."""
    color = (50, 100, 150)
    _install_capture_stubs(monkeypatch, tmp_path,
                           color_sequence=[color, color])
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=0.0)  # input just happened

    d._capture_and_enqueue()
    d._capture_and_enqueue()
    assert d.session.frames_captured == 2
    assert d.session.frames_skipped == 0


def test_different_frame_is_always_kept(tmp_path, monkeypatch):
    """Different image → kept regardless of input state."""
    _install_capture_stubs(monkeypatch, tmp_path,
                           color_sequence=[(0, 0, 0), (255, 255, 255)])
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=999.0)

    d._capture_and_enqueue()
    d._capture_and_enqueue()
    assert d.session.frames_captured == 2
    assert d.session.frames_skipped == 0


def test_disabled_via_config_never_drops(tmp_path, monkeypatch):
    """drop_idle_duplicate_frames=False keeps every frame."""
    color = (200, 50, 50)
    _install_capture_stubs(monkeypatch, tmp_path,
                           color_sequence=[color, color, color])
    d = _make_daemon(tmp_path, monkeypatch, drop_enabled=False, idle_secs=999.0)

    d._capture_and_enqueue()
    d._capture_and_enqueue()
    d._capture_and_enqueue()
    assert d.session.frames_captured == 3
    assert d.session.frames_skipped == 0


def test_dropped_frame_deletes_local_png(tmp_path, monkeypatch):
    """When a frame is dropped, its PNG file is removed from disk to avoid
    leaking storage on the client machine."""
    color = (88, 88, 88)
    captures = _install_capture_stubs(monkeypatch, tmp_path,
                                      color_sequence=[color, color])
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=999.0)

    d._capture_and_enqueue()
    d._capture_and_enqueue()
    assert captures[0].exists(), "first kept frame must NOT be deleted"
    assert not captures[1].exists(), "dropped frame's PNG must be unlinked"


def test_dropped_frame_updates_last_capture_time(tmp_path, monkeypatch):
    """A dropped frame still updates _last_capture_time so the min-gap
    timer works on the next iteration (otherwise we'd re-capture in a
    tight loop)."""
    color = (88, 88, 88)
    _install_capture_stubs(monkeypatch, tmp_path,
                           color_sequence=[color, color])
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=999.0)

    d._capture_and_enqueue()
    t_after_first = d._last_capture_time
    assert t_after_first > 0

    # Sleep tiny bit so monotonic moves
    time.sleep(0.01)
    d._capture_and_enqueue()  # this one is dropped
    assert d._last_capture_time > t_after_first


def test_drop_then_change_then_drop_sequence(tmp_path, monkeypatch):
    """After a real change happens, the new frame becomes the baseline
    and the next identical-no-input frame is dropped against the new
    baseline (not the stale pre-change one)."""
    captures = _install_capture_stubs(monkeypatch, tmp_path,
                                      color_sequence=[
                                          (10, 10, 10),  # baseline
                                          (10, 10, 10),  # dup → drop
                                          (200, 200, 200),  # change → keep
                                          (200, 200, 200),  # dup of new → drop
                                      ])
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=999.0)

    d._capture_and_enqueue()  # kept (first)
    d._capture_and_enqueue()  # dropped (dup of frame 1)
    d._capture_and_enqueue()  # kept (changed)
    d._capture_and_enqueue()  # dropped (dup of frame 3)

    assert d.session.frames_captured == 2
    assert d.session.frames_skipped == 2
    assert captures[0].exists()  # frame 1 kept
    assert not captures[1].exists()  # frame 2 dropped
    assert captures[2].exists()  # frame 3 kept
    assert not captures[3].exists()  # frame 4 dropped


# ---------------------------------------------------------------------------
# had_input detection (v0.4.9)
# ---------------------------------------------------------------------------


def test_detect_input_since_first_capture_reports_false(tmp_path, monkeypatch):
    """First capture (prev_capture_time=0) should report False — no prior
    window to compare against."""
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=0.0)
    assert d._detect_input_since(0.0) is False


def test_detect_input_since_input_inside_window(tmp_path, monkeypatch):
    """idle_secs < elapsed → input happened in the window → True."""
    import time as _t
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=0.2)
    prev = _t.monotonic() - 1.0  # 1s ago
    assert d._detect_input_since(prev) is True


def test_detect_input_since_input_older_than_window(tmp_path, monkeypatch):
    """idle_secs > elapsed → input is older than the window → False."""
    import time as _t
    d = _make_daemon(tmp_path, monkeypatch, idle_secs=10.0)
    prev = _t.monotonic() - 1.0  # 1s ago
    assert d._detect_input_since(prev) is False
