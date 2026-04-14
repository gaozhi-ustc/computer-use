"""Background worker pool that analyzes pending frames using qwen.

One AnalysisWorker per DashScope API key. Each worker:
- Polls the DB's pending queue (atomic claim via UPDATE ... RETURNING)
- Calls VisionClient.analyze_frame for each claimed frame
- On success: mark_frame_done with the analysis result
- On failure: reset to pending (for retry) or mark_frame_failed after 3 attempts
- Sleeps briefly when queue is empty
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import structlog

from server import db

log = structlog.get_logger()


# How long a worker waits between polls when the queue is empty
EMPTY_QUEUE_POLL_INTERVAL_SECONDS = 2.0

# Max consecutive failures before marking a frame 'failed' permanently
MAX_ANALYSIS_ATTEMPTS = 3


class AnalysisWorker:
    """One worker bound to one API key."""

    def __init__(
        self,
        key: str,
        key_index: int,
        stop_event: threading.Event,
        vision_client: Any = None,  # injected for tests; None = build real one
    ):
        self.key = key
        self.key_index = key_index
        self.label = f"worker-{key_index}"
        self._stop = stop_event
        self._vision = vision_client if vision_client is not None else self._build_vision()

    def _build_vision(self):
        """Build a real VisionClient using this worker's API key."""
        from workflow_recorder.analysis.vision_client import VisionClient
        from workflow_recorder.config import AnalysisConfig
        cfg = AnalysisConfig(
            openai_api_key=self.key,
            base_url="https://coding.dashscope.aliyuncs.com/v1",
            model="qwen3.5-plus",
            detail="low",
            max_tokens=1000,
            temperature=0.1,
        )
        return VisionClient(cfg)

    def run(self) -> None:
        """Main loop: claim → analyze → repeat. Stops when stop_event is set."""
        log.info("analysis_worker_started", label=self.label)
        while not self._stop.is_set():
            frame = db.claim_next_pending_frame()
            if frame is None:
                # Queue empty — wait (interruptible by stop_event)
                self._stop.wait(timeout=EMPTY_QUEUE_POLL_INTERVAL_SECONDS)
                continue
            self._analyze_one(frame)
        log.info("analysis_worker_stopped", label=self.label)

    def _analyze_one(self, frame: dict) -> None:
        """Analyze a single claimed frame and update the DB accordingly."""
        frame_id = frame["id"]
        attempts = frame.get("analysis_attempts", 0)
        try:
            result = self._vision.analyze_frame(
                image_path=Path(frame["image_path"]),
                window_context=None,
                frame_index=frame["frame_index"],
                timestamp=None,
            )
            if result is None:
                self._handle_failure(frame_id, attempts, "empty response")
                return
            db.mark_frame_done(frame_id, result.model_dump())
            log.debug("frame_analyzed", frame_id=frame_id,
                      worker=self.label, frame_index=frame["frame_index"])
        except Exception as exc:
            self._handle_failure(frame_id, attempts, f"{type(exc).__name__}: {exc}")

    def _handle_failure(self, frame_id: int, attempts: int, reason: str) -> None:
        """Retry by resetting to pending, or mark failed after MAX_ANALYSIS_ATTEMPTS."""
        if attempts >= MAX_ANALYSIS_ATTEMPTS:
            db.mark_frame_failed(frame_id, reason)
            log.warning("frame_failed", frame_id=frame_id,
                        worker=self.label, attempts=attempts, reason=reason)
        else:
            db.reset_frame_to_pending(frame_id)
            log.info("frame_retry_scheduled", frame_id=frame_id,
                     worker=self.label, attempts=attempts, reason=reason)


class AnalysisPool:
    """Manages the set of AnalysisWorker threads, one per API key."""

    def __init__(
        self,
        keys: list[str],
        worker_factory=None,  # for tests to inject FakeVisionClient
    ):
        self._keys = list(keys)
        self._worker_factory = worker_factory
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Spawn one daemon thread per key."""
        if not self._keys:
            log.warning("analysis_pool_no_keys",
                        msg="api_keys.txt empty/missing — uploaded frames "
                            "will sit in 'pending' forever.")
            return

        for i, key in enumerate(self._keys):
            if self._worker_factory is not None:
                worker = self._worker_factory(key, i, self._stop_event)
            else:
                worker = AnalysisWorker(key, i, self._stop_event)
            t = threading.Thread(
                target=worker.run,
                name=f"analysis-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

        log.info("analysis_pool_started", worker_count=len(self._keys))

    def stop(self, timeout: float = 30.0) -> None:
        """Signal all workers to exit and wait."""
        if not self._threads:
            return
        self._stop_event.set()
        per_thread = max(1.0, timeout / len(self._threads))
        for t in self._threads:
            t.join(timeout=per_thread)
        log.info("analysis_pool_stopped", worker_count=len(self._threads))
