"""Main daemon loop for the workflow recorder.

Single-thread capture loop. Each capture:
1. Take screenshot + OS cursor/focus (via capture/screenshot.py)
2. Enqueue for upload via ImageUploader
3. Optionally back off if user is idle (idle_detector)
4. Sleep until next interval
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
        idle_backoff = IdleBackoff(
            base_interval=self.config.capture.interval_seconds,
            max_interval=idle_cfg.max_interval_seconds,
            idle_threshold_seconds=idle_cfg.idle_threshold_seconds,
            backoff_factor=idle_cfg.backoff_factor,
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

    def _capture_and_enqueue(self) -> None:
        """Take one screenshot, run privacy filters, enqueue for upload."""
        try:
            window_ctx = get_active_window()
            if should_skip_frame(window_ctx, self.config.privacy):
                self.session.frames_skipped += 1
                return

            result = capture_screenshot(
                output_dir=self._capture_dir,
                monitor=self.config.capture.monitor,
                image_format=self.config.capture.image_format,
                image_quality=self.config.capture.image_quality,
                downscale_factor=self.config.capture.downscale_factor,
            )
            apply_masks(result.file_path, self.config.privacy)

            self.session.frames_captured += 1
            if self.uploader is not None and self.config.server.enabled:
                self.uploader.enqueue(
                    image_path=result.file_path,
                    frame_index=self.session.frames_captured,
                    timestamp=result.timestamp,
                    cursor_x=result.cursor_x,
                    cursor_y=result.cursor_y,
                    focus_rect=result.focus_rect,
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
