"""Main daemon loop and session management.

Runs two threads:
- Capture thread: periodic screenshots + window context → queue
- Analysis thread: dequeue frames → GPT analysis → buffer

On stop, aggregation runs and outputs the workflow document.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from workflow_recorder.capture.privacy import apply_masks, should_skip_frame
from workflow_recorder.capture.screenshot import CaptureResult, capture_screenshot
from workflow_recorder.capture.window_info import WindowContext, get_active_window
from workflow_recorder.config import AppConfig
from workflow_recorder.frame_pusher import FramePusher
from workflow_recorder.utils.storage import get_temp_capture_dir

if TYPE_CHECKING:
    from workflow_recorder.analysis.frame_analysis import FrameAnalysis

log = structlog.get_logger()


@dataclass
class CapturedFrame:
    """A screenshot paired with its window context."""
    capture: CaptureResult
    window_context: WindowContext | None
    frame_index: int


class RecordingSession:
    """Manages a single recording session from start to finalization."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.session_id = str(uuid.uuid4())
        self.start_time = time.time()
        self.frame_queue: queue.Queue[CapturedFrame | None] = queue.Queue(
            maxsize=config.capture.max_queue_size
        )
        self.frame_analyses: list[FrameAnalysis] = []
        self.captured_frames: list[CapturedFrame] = []
        self._frame_counter = 0
        self._stop_event = threading.Event()
        self._capture_dir = get_temp_capture_dir()
        self._lock = threading.Lock()

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def capture_once(self) -> CapturedFrame | None:
        """Take a single screenshot and enqueue it."""
        try:
            # Check privacy before capturing
            window_ctx = get_active_window()
            if should_skip_frame(window_ctx, self.config.privacy):
                return None

            result = capture_screenshot(
                output_dir=self._capture_dir,
                monitor=self.config.capture.monitor,
                image_format=self.config.capture.image_format,
                image_quality=self.config.capture.image_quality,
                downscale_factor=self.config.capture.downscale_factor,
            )

            # Apply region masks
            apply_masks(result.file_path, self.config.privacy)

            with self._lock:
                self._frame_counter += 1
                idx = self._frame_counter

            frame = CapturedFrame(
                capture=result,
                window_context=window_ctx,
                frame_index=idx,
            )

            try:
                self.frame_queue.put_nowait(frame)
                self.captured_frames.append(frame)
                log.debug("frame_captured", frame_index=idx,
                          app=window_ctx.process_name if window_ctx else "none")
                return frame
            except queue.Full:
                log.warning("frame_dropped", frame_index=idx,
                            reason="queue_full")
                result.file_path.unlink(missing_ok=True)
                return None

        except Exception:
            log.exception("capture_failed")
            return None

    def stop(self) -> None:
        """Signal the session to stop."""
        self._stop_event.set()
        # Sentinel to unblock analysis thread
        try:
            self.frame_queue.put_nowait(None)
        except queue.Full:
            pass

    @property
    def is_stopped(self) -> bool:
        return self._stop_event.is_set()


class Daemon:
    """Orchestrates capture and analysis threads."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.session: RecordingSession | None = None
        self._stop_event = threading.Event()
        self.pusher: FramePusher | None = None

    def run(self) -> None:
        """Run the daemon in foreground mode (blocking)."""
        log.info("daemon_starting", mode="foreground")
        self.session = RecordingSession(self.config)

        # Start the background frame pusher (no-op if server.enabled is False).
        self.pusher = FramePusher(
            server_config=self.config.server,
            employee_id=self.config.employee_id,
            session_id=self.session.session_id,
        )
        self.pusher.start()

        capture_thread = threading.Thread(
            target=self._capture_loop,
            name="capture",
            daemon=True,
        )
        analysis_thread = threading.Thread(
            target=self._analysis_loop,
            name="analysis",
            daemon=True,
        )

        capture_thread.start()
        analysis_thread.start()

        log.info("daemon_running",
                 session_id=self.session.session_id,
                 interval=self.config.capture.interval_seconds)

        try:
            # Block until stop signal or max duration
            while not self._stop_event.is_set():
                if self.session.elapsed >= self.config.session.max_duration_seconds:
                    log.info("max_duration_reached")
                    break
                self._stop_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            log.info("keyboard_interrupt")

        self._finalize()

    def stop(self) -> None:
        """Signal the daemon to stop."""
        self._stop_event.set()
        if self.session:
            self.session.stop()

    def _capture_loop(self) -> None:
        """Periodically capture screenshots."""
        interval = self.config.capture.interval_seconds
        while not self._stop_event.is_set():
            if self.session:
                self.session.capture_once()
            self._stop_event.wait(timeout=interval)

        log.info("capture_loop_stopped")

    def _analysis_loop(self) -> None:
        """Process captured frames through GPT vision analysis."""
        if not self.session:
            return

        # Lazy import to avoid circular deps and allow running without openai
        try:
            from workflow_recorder.analysis.vision_client import VisionClient
            client = VisionClient(self.config.analysis)
        except Exception:
            log.warning("vision_client_unavailable",
                        msg="Analysis disabled — running in capture-only mode")
            self._drain_queue()
            return

        while True:
            try:
                frame = self.session.frame_queue.get(timeout=2.0)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            if frame is None:  # sentinel
                break

            try:
                analysis = client.analyze_frame(
                    image_path=frame.capture.file_path,
                    window_context=frame.window_context,
                    frame_index=frame.frame_index,
                    timestamp=frame.capture.timestamp,
                )
                if analysis:
                    with self.session._lock:
                        self.session.frame_analyses.append(analysis)
                    log.debug("frame_analyzed", frame_index=frame.frame_index,
                              action=analysis.user_action)
                    if self.pusher is not None:
                        self.pusher.enqueue(analysis)
            except Exception:
                log.exception("analysis_failed", frame_index=frame.frame_index)

        log.info("analysis_loop_stopped",
                 total_analyzed=len(self.session.frame_analyses))

    def _drain_queue(self) -> None:
        """Drain remaining items from the queue (capture-only mode)."""
        if not self.session:
            return
        while True:
            try:
                item = self.session.frame_queue.get(timeout=2.0)
                if item is None:
                    break
            except queue.Empty:
                if self._stop_event.is_set():
                    break

    def _finalize(self) -> None:
        """Stop session and produce the workflow document."""
        if not self.session:
            return

        self.stop()

        # Flush remaining frames up to the server before aggregation.
        if self.pusher is not None:
            self.pusher.stop()

        log.info("finalizing_session",
                 session_id=self.session.session_id,
                 frames_captured=len(self.session.captured_frames),
                 frames_analyzed=len(self.session.frame_analyses))

        if not self.session.frame_analyses:
            log.warning("no_analyses_to_aggregate")
            return

        try:
            from workflow_recorder.aggregation.workflow_builder import WorkflowBuilder
            from workflow_recorder.output.reference_store import store_reference_screenshots
            from workflow_recorder.output.writer import WorkflowWriter

            builder = WorkflowBuilder(self.config)
            workflow = builder.build(
                session_id=self.session.session_id,
                start_time=self.session.start_time,
                frame_analyses=self.session.frame_analyses,
                captured_frames=self.session.captured_frames,
            )

            output_dir = Path(self.config.output.directory)
            if self.config.output.include_reference_screenshots:
                workflow = store_reference_screenshots(
                    workflow, self.session.captured_frames, output_dir)

            writer = WorkflowWriter(self.config.output)
            output_path = writer.write(workflow)
            log.info("workflow_written", path=str(output_path),
                     steps=len(workflow.steps))
        except Exception:
            log.exception("finalization_failed")
