"""Tests for AnalysisPool + AnalysisWorker (group-level analysis)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_openai_response(steps_json: list[dict]) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps({"steps": steps_json})
    return resp


class FakeOpenAIClient:
    """Stand-in for openai.OpenAI client used in group analysis."""

    def __init__(self):
        self.calls: list[dict] = []
        self._response = _fake_openai_response([
            {"step_order": 1, "title": "Click button",
             "human_description": "Click the Save button",
             "machine_actions": [{"type": "click", "x": 100, "y": 200, "target": "Save"}],
             "application": "chrome.exe",
             "key_frame_indices": [0]}
        ])
        self._raise: Exception | None = None
        self.chat = MagicMock()
        self.chat.completions.create = self._create

    def set_response(self, steps: list[dict]):
        self._response = _fake_openai_response(steps)
        self._raise = None

    def set_raise(self, exc: Exception):
        self._raise = exc

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise:
            raise self._raise
        return self._response


@pytest.fixture
def fresh_db_with_groups(tmp_path, monkeypatch):
    """DB with a session containing 3 frames and 1 pending group."""
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    from server import db
    db.init_db()

    # Create dummy image files
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    frame_ids = []
    for i in range(1, 4):
        img = img_dir / f"{i}.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)
        fid = db.insert_pending_frame(
            employee_id="E001", session_id="s1", frame_index=i,
            timestamp=float(i), image_path=str(img),
            analysis_status="pending",
        )
        frame_ids.append(fid)

    # Create a session record
    db.upsert_session(
        session_id="s1", employee_id="E001",
        frame_at="2026-04-15T10:00:00",
    )

    # Create a frame group
    db.insert_frame_group(
        session_id="s1", employee_id="E001",
        group_index=0, frame_ids=frame_ids,
        primary_application="chrome.exe",
    )

    return db


def test_worker_processes_pending_group(fresh_db_with_groups):
    """Worker claims a group, calls the LLM, stores results, marks done."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_groups

    fake_client = FakeOpenAIClient()
    stop_event = threading.Event()
    worker = AnalysisWorker(
        key="sk-test", key_index=0, stop_event=stop_event,
        vision_client=fake_client,
    )

    # Run one iteration manually
    group = db.claim_next_pending_group()
    assert group is not None
    worker._analyze_group(fake_client, group)

    # Group should be done
    groups = db.list_frame_groups("s1")
    assert groups[0]["analysis_status"] == "done"

    # Steps should be stored
    result = db.get_group_analysis_result("s1", 0)
    assert result is not None
    assert len(result) == 1
    assert result[0]["title"] == "Click button"

    # SOP should be auto-created (all groups done)
    sops = db.list_sops()
    assert len(sops) >= 1
    assert "system" in sops[0]["created_by"]


def test_worker_retries_on_exception(fresh_db_with_groups):
    """API failure resets group to pending for retry."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_groups

    fake_client = FakeOpenAIClient()
    fake_client.set_raise(RuntimeError("api down"))

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    group = db.claim_next_pending_group()
    assert group is not None
    # The run method catches exceptions, but we call _analyze_group directly
    # which will raise; the run() method handles it via _handle_failure
    try:
        worker._analyze_group(fake_client, group)
    except RuntimeError:
        worker._handle_failure(group["id"], group["analysis_attempts"], "api down")

    # Should be reset to pending
    groups = db.list_frame_groups("s1")
    assert groups[0]["analysis_status"] == "pending"


def test_worker_fails_after_max_attempts(fresh_db_with_groups):
    """After MAX_ANALYSIS_ATTEMPTS, group is marked failed."""
    from server.analysis_pool import AnalysisWorker, MAX_ANALYSIS_ATTEMPTS
    db = fresh_db_with_groups

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=FakeOpenAIClient())

    # Simulate max attempts by failing repeatedly
    for attempt in range(MAX_ANALYSIS_ATTEMPTS):
        group = db.claim_next_pending_group()
        if group is None:
            break
        worker._handle_failure(group["id"], group["analysis_attempts"], "persistent error")

    groups = db.list_frame_groups("s1")
    assert groups[0]["analysis_status"] == "failed"
    assert "persistent error" in groups[0].get("analysis_error", "")


def test_worker_handles_empty_steps(fresh_db_with_groups):
    """LLM returning no parseable steps marks group as failed."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_groups

    fake_client = FakeOpenAIClient()
    # Return empty steps
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "I don't understand the images"
    fake_client._response = resp

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    group = db.claim_next_pending_group()
    worker._analyze_group(fake_client, group)

    groups = db.list_frame_groups("s1")
    assert groups[0]["analysis_status"] == "failed"
    assert "empty steps" in groups[0].get("analysis_error", "")


def test_worker_run_loop_drains_queue(fresh_db_with_groups):
    """Worker's main loop processes all pending groups then waits."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_groups

    fake_client = FakeOpenAIClient()
    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    t = threading.Thread(target=worker.run, daemon=True)
    t.start()

    # Wait until the group is done
    deadline = time.time() + 5.0
    while time.time() < deadline:
        groups = db.list_frame_groups("s1")
        if groups and groups[0]["analysis_status"] == "done":
            break
        time.sleep(0.05)

    stop_event.set()
    t.join(timeout=5.0)

    groups = db.list_frame_groups("s1")
    assert groups[0]["analysis_status"] == "done"


# ---------------------------------------------------------------------------
# AnalysisPool lifecycle
# ---------------------------------------------------------------------------


def test_pool_starts_one_thread_per_key(fresh_db_with_groups):
    """N keys -> N threads, all alive until stop()."""
    from server.analysis_pool import AnalysisPool, AnalysisWorker

    def worker_factory(key, key_index, stop_event):
        fc = FakeOpenAIClient()
        return AnalysisWorker(key, key_index, stop_event, vision_client=fc)

    pool = AnalysisPool(keys=["sk-a", "sk-b", "sk-c"], worker_factory=worker_factory)
    pool.start()
    try:
        assert len(pool._threads) == 3
        for t in pool._threads:
            assert t.is_alive()
    finally:
        pool.stop(timeout=5.0)

    # After stop, threads should be joined (may already be dead as daemon)
    for t in pool._threads:
        assert not t.is_alive()


def test_pool_drains_queue_with_multiple_workers(fresh_db_with_groups):
    """Workers + pending group -> group becomes 'done'."""
    from server.analysis_pool import AnalysisPool, AnalysisWorker
    db = fresh_db_with_groups

    def worker_factory(key, key_index, stop_event):
        fc = FakeOpenAIClient()
        return AnalysisWorker(key, key_index, stop_event, vision_client=fc)

    pool = AnalysisPool(keys=["sk-a", "sk-b"], worker_factory=worker_factory)
    pool.start()

    deadline = time.time() + 5.0
    while time.time() < deadline:
        groups = db.list_frame_groups("s1")
        if groups and groups[0]["analysis_status"] == "done":
            break
        time.sleep(0.05)

    pool.stop(timeout=5.0)
    groups = db.list_frame_groups("s1")
    assert groups[0]["analysis_status"] == "done"


def test_pool_with_empty_keys_is_noop():
    """No keys -> pool does nothing, stop is safe."""
    from server.analysis_pool import AnalysisPool
    pool = AnalysisPool(keys=[])
    pool.start()
    assert pool._threads == []
    pool.stop()
