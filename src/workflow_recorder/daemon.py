"""Main daemon loop for the workflow recorder.

Single-thread capture loop. Each capture:
1. Take screenshot + OS cursor/focus (via capture/screenshot.py)
2. Enqueue for upload via ImageUploader
3. Optionally back off if user is idle (idle_detector)
4. Sleep until next interval

Runs on Windows and macOS. On macOS the pipeline needs these OS
permissions to be granted to the hosting process (Terminal, iTerm,
or the compiled .app):
  • Screen Recording  — required for mss to capture the display
  • Accessibility     — required for get_focus_rect() via AX API
                         (focus rect is optional; without it frames
                          simply have no yellow focus overlay)
"""

from __future__ import annotations

import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import structlog

from workflow_recorder.capture.privacy import apply_masks, should_skip_frame
from workflow_recorder.capture.screenshot import capture_screenshot
from workflow_recorder.capture.window_info import get_active_window
from workflow_recorder.capture.idle_detector import IdleBackoff, IdleDetector
from workflow_recorder.config import AppConfig
from workflow_recorder.image_uploader import ImageUploader
from workflow_recorder.utils.storage import get_temp_capture_dir


log = structlog.get_logger()


class RecordingSession:
    """One recording session — just a session_id and start time."""

    def __init__(self, employee_id: str):
        self.session_id = str(uuid.uuid4())
        self.employee_id = employee_id
        self.start_time = time.time()
        self.frames_captured = 0
        self.frames_skipped = 0

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time


class Daemon:
    """Recorder daemon: capture screenshots, push to server for analysis."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._stop_event = threading.Event()
        self.session: Optional[RecordingSession] = None
        self.uploader: Optional[ImageUploader] = None
        self._capture_dir = get_temp_capture_dir()
        # Last capture timestamp (monotonic). 0.0 = never captured yet,
        # so the first capture fires immediately without waiting.
        self._last_capture_time: float = 0.0
        # Used by the "drop idle duplicate" filter: perceptual hash of the
        # most recently kept frame, and the IdleDetector for input checks.
        # _idle_detector is set in run() but tests can inject it directly.
        self._last_frame_hash = None
        self._idle_detector = None

    def run(self) -> None:
        """Start capture loop. Blocks until stop() or max_duration."""
        self.session = RecordingSession(employee_id=self.config.employee_id)
        log.info("daemon_starting", session_id=self.session.session_id,
                 employee_id=self.session.employee_id)

        self.uploader = ImageUploader(
            server_url=self.config.server.url,
            api_key=self.config.server.api_key,
            employee_id=self.session.employee_id,
            session_id=self.session.session_id,
            buffer_path=self.config.server.buffer_path,
            timeout=self.config.server.timeout_seconds,
            max_retries=self.config.server.max_retries,
        )
        if self.config.server.enabled:
            self.uploader.start()
        else:
            log.warning("uploader_disabled",
                        msg="server.enabled is False — captures won't be uploaded")

        # Idle backoff
        idle_cfg = self.config.idle_detection
        idle_detector = IdleDetector() if idle_cfg.enabled else None
        # Always have an IdleDetector available for the drop-duplicate
        # filter, even if idle_detection backoff is disabled.
        if self._idle_detector is None:
            self._idle_detector = idle_detector or IdleDetector()
        idle_backoff = IdleBackoff(
            base_interval=self.config.capture.interval_seconds,
            max_interval=idle_cfg.max_interval_seconds,
            idle_threshold_seconds=idle_cfg.idle_threshold_seconds,
            backoff_factor=idle_cfg.backoff_factor,
            light_idle_threshold_seconds=idle_cfg.light_idle_threshold_seconds,
            light_idle_interval_seconds=idle_cfg.light_idle_interval_seconds,
        ) if idle_cfg.enabled else None

        max_duration = self.config.session.max_duration_seconds
        start = self.session.start_time

        try:
            while not self._stop_event.is_set():
                self._capture_and_enqueue()

                # Backoff or fixed interval
                if idle_detector is not None and idle_backoff is not None:
                    idle_secs = idle_detector.seconds_since_last_input()
                    interval = idle_backoff.update(idle_secs)
                else:
                    interval = self.config.capture.interval_seconds

                # Max-duration check (0 = unlimited)
                if max_duration > 0 and (time.time() - start) >= max_duration:
                    log.info("max_duration_reached",
                             duration=max_duration)
                    break
                self._stop_event.wait(timeout=interval)
        finally:
            if self.uploader is not None:
                self.uploader.stop(timeout=15.0)
            log.info("daemon_stopped",
                     frames_captured=self.session.frames_captured,
                     frames_skipped=self.session.frames_skipped,
                     duration=time.time() - start)

    def stop(self) -> None:
        """Signal the capture loop to stop."""
        self._stop_event.set()

    def _wait_for_good_capture_moment(self) -> bool:
        """Block until it's a good time to take the next screenshot.

        Decision tree (re-evaluated each loop iteration so state changes
        during the wait are picked up):

        1. If the cursor is currently moving and wait_for_click_when_moving
           is enabled → wait for an intentional click/keystroke (bounded by
           max_wait_for_click_seconds). After the wait returns (either
           detected or timeout), take the shot.
        2. Otherwise (mouse stationary) → enforce a minimum gap of
           interval_seconds since the LAST capture. If we're still inside
           that gap, sleep the remainder and re-enter the decision tree
           (the user may have started moving the mouse during the sleep).
        3. Mouse stationary AND gap elapsed → capture now.

        Returns True when the caller should proceed to capture, False if
        stop_event was set and the caller should abort.
        """
        from workflow_recorder.capture.cursor_focus import (
            is_mouse_moving, wait_for_click_or_key,
        )

        cap = self.config.capture
        min_gap = cap.interval_seconds

        while not self._stop_event.is_set():
            if cap.wait_for_click_when_moving and is_mouse_moving():
                # Motion path: wait for an intentional interaction (or timeout).
                wait_for_click_or_key(
                    max_wait_seconds=cap.max_wait_for_click_seconds,
                    stop_event=self._stop_event,
                )
                return not self._stop_event.is_set()

            # Stationary path: ensure >= min_gap since the previous capture.
            # First capture ever (no prior timestamp) fires immediately —
            # `time.monotonic()` starts at process-launch on macOS/Linux,
            # so we can't rely on a large absolute value to short-circuit.
            if self._last_capture_time == 0.0:
                return True
            now = time.monotonic()
            elapsed = now - self._last_capture_time
            if elapsed >= min_gap:
                return True

            # Sleep out the remainder (interruptible), then re-check.
            remaining = min_gap - elapsed
            if self._stop_event.wait(timeout=remaining):
                return False
            # loop continues — the mouse may now be moving

        return False

    def _detect_input_since(self, prev_capture_time: float) -> bool:
        """Return True iff any mouse/keyboard input happened between
        prev_capture_time and now. Uses GetLastInputInfo via IdleDetector.

        Rule: if seconds_since_last_input < time elapsed since prev capture,
        the most recent input falls *inside* the window → input happened.

        Special case: prev_capture_time == 0 (first capture ever) — we
        have no prior window to compare against, so report False. This
        also keeps non-Windows (where IdleDetector returns 0) safe: the
        first frame is treated as "no input yet" instead of lying with
        True.
        """
        if prev_capture_time <= 0.0 or self._idle_detector is None:
            return False
        try:
            idle_secs = self._idle_detector.seconds_since_last_input()
        except Exception:
            return False
        elapsed = time.monotonic() - prev_capture_time
        # Strict <: idle exactly at the boundary doesn't count (same
        # convention as the drop-duplicate check's strict >).
        return idle_secs < elapsed

    def _should_drop_as_idle_duplicate(
        self, image_path: "Path", prev_capture_time: float
    ) -> bool:
        """Return True iff the freshly-captured image at `image_path` is
        perceptually identical to the previously kept frame AND no
        mouse/keyboard input event happened between the two captures.

        Side effect: when keeping the frame, updates self._last_frame_hash
        to the hash of this frame so the next call compares against it.
        """
        cap = self.config.capture
        if not cap.drop_idle_duplicate_frames:
            return False

        # Compute perceptual hash. Lazy-import so non-feature paths don't
        # pay the imagehash import cost.
        try:
            import imagehash
            from PIL import Image
            with Image.open(image_path) as im:
                new_hash = imagehash.phash(im)
        except Exception as exc:
            # Hash failure shouldn't break the capture pipeline.
            log.warning("idle_dup_hash_failed", error=str(exc))
            return False

        prev_hash = self._last_frame_hash
        # Always remember the new hash as the next baseline (whether we
        # drop or keep — the *image* is what was captured, which becomes
        # the comparison point either way).
        self._last_frame_hash = new_hash

        if prev_hash is None:
            # First frame ever — nothing to compare against.
            return False

        # Visual comparison: hamming distance between phashes.
        try:
            distance = new_hash - prev_hash  # imagehash overrides __sub__
        except Exception:
            return False
        if distance > cap.duplicate_hash_threshold:
            return False  # real visual change → keep

        # Visual match. Now check whether any input happened since the
        # previous capture. If yes → keep (user did something even if
        # the screen didn't react). If no → drop.
        if self._idle_detector is None:
            return False  # safety: can't tell, keep
        idle_secs = self._idle_detector.seconds_since_last_input()
        elapsed = time.monotonic() - prev_capture_time
        # idle_secs > elapsed means the most recent input is older than
        # the previous capture → no input during the gap → safe to drop.
        # We use strict > (not >=) so that an input at the boundary
        # counts as "input happened" — false negatives just keep extra
        # frames, false positives lose data.
        if idle_secs > elapsed:
            return True
        return False

    def _capture_and_enqueue(self) -> None:
        """Take one screenshot, run privacy filters, enqueue for upload."""
        try:
            window_ctx = get_active_window()
            if should_skip_frame(window_ctx, self.config.privacy):
                self.session.frames_skipped += 1
                return

            # Block until the capture moment is right (motion-aware +
            # minimum-interval gap).
            if not self._wait_for_good_capture_moment():
                return

            prev_capture_time = self._last_capture_time
            result = capture_screenshot(
                output_dir=self._capture_dir,
                monitor=self.config.capture.monitor,
                image_format=self.config.capture.image_format,
                image_quality=self.config.capture.image_quality,
                downscale_factor=self.config.capture.downscale_factor,
            )
            apply_masks(result.file_path, self.config.privacy)
            self._last_capture_time = time.monotonic()

            # Drop frame if it's perceptually identical to the previous
            # kept frame AND no input happened since that capture.
            if self._should_drop_as_idle_duplicate(
                result.file_path, prev_capture_time
            ):
                try:
                    result.file_path.unlink()
                except OSError:
                    pass
                self.session.frames_skipped += 1
                log.debug("frame_dropped_idle_duplicate",
                          frame_index=self.session.frames_captured + 1)
                return

            # Determine whether any mouse/keyboard input occurred between
            # prev_capture_time and now. Same semantics as the drop-
            # duplicate check: idle_secs < elapsed → input happened.
            had_input = self._detect_input_since(prev_capture_time)

            # Prefer process_name as the application identifier — it's
            # stable across documents (chrome.exe / EXCEL.EXE) which is
            # what the grouper's app-switch detector expects. Fall back
            # to window_title only if the foreground window has no
            # resolvable process (shouldn't normally happen).
            window_title_raw = ""
            if window_ctx is not None:
                window_title_raw = (
                    window_ctx.process_name or window_ctx.window_title or ""
                )

            self.session.frames_captured += 1
            if self.uploader is not None and self.config.server.enabled:
                self.uploader.enqueue(
                    image_path=result.file_path,
                    frame_index=self.session.frames_captured,
                    timestamp=result.timestamp,
                    cursor_x=result.cursor_x,
                    cursor_y=result.cursor_y,
                    focus_rect=result.focus_rect,
                    had_input=had_input,
                    window_title_raw=window_title_raw,
                )
        except Exception as exc:
            log.exception("capture_failed", error=str(exc))


def install_signal_handlers(daemon: Daemon) -> None:
    """SIGINT / SIGTERM cleanly stops the daemon."""
    def handler(signum, _frame):
        log.info("signal_received", signum=signum)
        daemon.stop()

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)
