"""Tests for AnalysisPool + AnalysisWorker."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# AnalysisWorker tests with a mocked VisionClient
# ---------------------------------------------------------------------------


class FakeVisionClient:
    """Stand-in for workflow_recorder.analysis.vision_client.VisionClient.

    Return values are controlled via attributes set by the test.
    """

    def __init__(self, *args, **kwargs):
        self.analyze_frame_calls: list[dict] = []
        self._result = None
        self._raise: Exception | None = None

    def set_result(self, result):
        self._result = result
        self._raise = None

    def set_raise(self, exc: Exception):
        self._raise = exc
        self._result = None

    def analyze_frame(self, image_path: Path, window_context=None,
                      frame_index: int = 0, timestamp=None):
        self.analyze_frame_calls.append({
            "image_path": image_path,
            "frame_index": frame_index,
            "timestamp": timestamp,
        })
        if self._raise is not None:
            raise self._raise
        return self._result


def _fake_analysis(frame_index: int):
    """Build a FrameAnalysis-shaped object (only the fields mark_frame_done uses)."""
    class FA:
        def model_dump(self) -> dict:
            return {
                "frame_index": frame_index,
                "timestamp": 100.0,
                "application": "chrome.exe",
                "window_title": "Test",
                "user_action": "clicking Save",
                "ui_elements_visible": [],
                "text_content": "hello",
                "mouse_position_estimate": [10, 20],
                "confidence": 0.88,
                "context_data": {"page_title": "Test"},
            }
    return FA()


@pytest.fixture
def fresh_db_with_pending(tmp_path, monkeypatch):
    """DB with 3 pending frames ready to be analyzed."""
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    from server import db
    db.init_db()
    # Create dummy image files so the worker can "analyze" them
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    for i in range(1, 4):
        img = img_dir / f"{i}.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)
        db.insert_pending_frame(
            employee_id="E001", session_id="s1", frame_index=i,
            timestamp=float(i), image_path=str(img),
        )
    return db


def test_worker_processes_pending_frame(fresh_db_with_pending):
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_result(_fake_analysis(frame_index=1))

    stop_event = threading.Event()
    worker = AnalysisWorker(
        key="sk-test", key_index=0, stop_event=stop_event,
        vision_client=fake_client,  # injected for test
    )

    # Manually claim + analyze one (simulating one iteration)
    frame = db.claim_next_pending_frame()
    worker._analyze_one(frame)

    updated = db.get_frame(frame["id"])
    assert updated["analysis_status"] == "done"
    assert updated["application"] == "chrome.exe"
    assert updated["user_action"] == "clicking Save"
    assert updated["confidence"] == pytest.approx(0.88)
    assert updated["context_data"] == {"page_title": "Test"}

    # Worker must have been called with the right image
    assert len(fake_client.analyze_frame_calls) == 1
    assert fake_client.analyze_frame_calls[0]["frame_index"] == 1


def test_worker_retries_on_exception_resets_to_pending(fresh_db_with_pending):
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_raise(RuntimeError("api down"))

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    frame = db.claim_next_pending_frame()
    worker._analyze_one(frame)
    refreshed = db.get_frame(frame["id"])
    # First failure: status goes back to 'pending', attempts stays at 1
    assert refreshed["analysis_status"] == "pending"
    assert refreshed["analysis_attempts"] == 1


def test_worker_fails_after_3_attempts(fresh_db_with_pending):
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_raise(RuntimeError("persistent error"))

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    # Claim three times, fail three times
    frame_id = None
    for attempt in range(3):
        frame = db.claim_next_pending_frame()
        assert frame is not None, f"attempt {attempt}: expected a claimable frame"
        if frame_id is None:
            frame_id = frame["id"]
        worker._analyze_one(frame)

    final = db.get_frame(frame_id)
    assert final["analysis_status"] == "failed"
    assert final["analysis_attempts"] == 3
    assert "persistent error" in final["analysis_error"]


def test_worker_handles_empty_result_as_failure(fresh_db_with_pending):
    """VisionClient returning None (empty response) should count as a failure."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_result(None)  # qwen returned nothing parseable

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    frame = db.claim_next_pending_frame()
    worker._analyze_one(frame)
    refreshed = db.get_frame(frame["id"])
    assert refreshed["analysis_status"] == "pending"  # will retry
    assert refreshed["analysis_attempts"] == 1


def test_worker_run_loop_drains_queue_then_waits(fresh_db_with_pending):
    """Worker's main run loop: process all pending, then wait on stop_event."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_result(_fake_analysis(frame_index=0))

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    t = threading.Thread(target=worker.run, daemon=True)
    t.start()

    # Poll until all three pending are done, max 3 seconds
    deadline = time.time() + 3.0
    while time.time() < deadline:
        stats = db.get_analysis_queue_stats()
        if stats["done"] == 3:
            break
        time.sleep(0.05)

    stop_event.set()
    t.join(timeout=5.0)

    final = db.get_analysis_queue_stats()
    assert final["done"] == 3
    assert final["pending"] == 0


# ---------------------------------------------------------------------------
# AnalysisPool lifecycle
# ---------------------------------------------------------------------------


def test_pool_starts_one_thread_per_key(fresh_db_with_pending):
    """N keys -> N threads, all alive until stop()."""
    from server.analysis_pool import AnalysisPool, AnalysisWorker
    # Use a factory that returns FakeVisionClient so no real API calls happen
    fake_clients = []

    def worker_factory(key, key_index, stop_event):
        fc = FakeVisionClient()
        fc.set_result(_fake_analysis(frame_index=0))
        fake_clients.append(fc)
        return AnalysisWorker(key, key_index, stop_event, vision_client=fc)

    pool = AnalysisPool(keys=["sk-a", "sk-b", "sk-c"], worker_factory=worker_factory)
    pool.start()
    try:
        assert len(pool._threads) == 3
        for t in pool._threads:
            assert t.is_alive()
    finally:
        pool.stop(timeout=5.0)

    # After stop, all threads should be joined
    for t in pool._threads:
        assert not t.is_alive()


def test_pool_drains_queue_with_multiple_workers(fresh_db_with_pending):
    """3 workers + 3 pending frames -> all become 'done'."""
    from server.analysis_pool import AnalysisPool, AnalysisWorker
    db = fresh_db_with_pending

    def worker_factory(key, key_index, stop_event):
        fc = FakeVisionClient()
        fc.set_result(_fake_analysis(frame_index=0))
        return AnalysisWorker(key, key_index, stop_event, vision_client=fc)

    pool = AnalysisPool(keys=["sk-a", "sk-b", "sk-c"], worker_factory=worker_factory)
    pool.start()

    # Wait up to 3s for drain
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if db.get_analysis_queue_stats()["done"] == 3:
            break
        time.sleep(0.05)

    pool.stop(timeout=5.0)
    assert db.get_analysis_queue_stats()["done"] == 3


def test_pool_with_empty_keys_is_noop():
    """No keys -> pool does nothing, stop is safe."""
    from server.analysis_pool import AnalysisPool
    pool = AnalysisPool(keys=[])
    pool.start()  # no-op
    assert pool._threads == []
    pool.stop()  # no-op
