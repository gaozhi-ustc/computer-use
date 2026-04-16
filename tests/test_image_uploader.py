"""Tests for ImageUploader — replaces FramePusher for offline analysis."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from workflow_recorder.image_uploader import ImageUploader


# ---------------------------------------------------------------------------
# FakeHttpxClient — captures calls and returns configurable responses
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None):
        self.status_code = status_code
        self._json = json_body or {"ok": True, "id": 1}
        self.text = json.dumps(self._json)

    def json(self):
        return self._json


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self._response = FakeResponse(200)
        self._raise = None

    def set_response(self, resp: FakeResponse):
        self._response = resp

    def set_raise(self, exc: Exception):
        self._raise = exc

    def post(self, url, data=None, files=None, headers=None, timeout=None):
        self.calls.append({
            "url": url, "data": data,
            "filenames": list((files or {}).keys()),
            "headers": headers,
        })
        if self._raise is not None:
            raise self._raise
        return self._response

    def close(self):
        pass


@pytest.fixture
def sample_png(tmp_path):
    p = tmp_path / "frame-001.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return p


def _make_uploader(tmp_path, **kwargs):
    defaults = dict(
        server_url="http://127.0.0.1:8000",
        api_key="sk-test",
        employee_id="E001",
        session_id="sess-1",
        buffer_path=str(tmp_path / "buffer.jsonl"),
        timeout=2.0,
        max_retries=2,
    )
    defaults.update(kwargs)
    return ImageUploader(**defaults)


def test_enqueue_uploads_multipart(tmp_path, sample_png, monkeypatch):
    """Uploader posts multipart (image + form fields including cursor/focus)."""
    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)

    up = _make_uploader(tmp_path)
    up.start()
    try:
        up.enqueue(
            image_path=sample_png, frame_index=1, timestamp=100.0,
            cursor_x=50, cursor_y=60, focus_rect=[10, 20, 100, 200],
        )
        # Wait for worker to process
        deadline = time.time() + 2.0
        while time.time() < deadline and not fake.calls:
            time.sleep(0.01)
    finally:
        up.stop(timeout=2.0)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "http://127.0.0.1:8000/frames/upload"
    assert call["data"]["employee_id"] == "E001"
    assert call["data"]["session_id"] == "sess-1"
    assert call["data"]["frame_index"] == "1"
    assert call["data"]["cursor_x"] == "50"
    assert call["data"]["cursor_y"] == "60"
    assert call["data"]["focus_rect"] == "[10, 20, 100, 200]"
    assert call["headers"]["X-API-Key"] == "sk-test"
    assert call["filenames"] == ["image"]


def test_enqueue_had_input_field(tmp_path, sample_png, monkeypatch):
    """had_input=True must be serialized as form field 'had_input' = '1'.
    had_input=False (or omitted) must serialize as '0'."""
    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    try:
        up.enqueue(image_path=sample_png, frame_index=1, timestamp=1.0,
                   had_input=True)
        up.enqueue(image_path=sample_png, frame_index=2, timestamp=2.0,
                   had_input=False)
        up.enqueue(image_path=sample_png, frame_index=3, timestamp=3.0)  # default
        deadline = time.time() + 2.0
        while time.time() < deadline and len(fake.calls) < 3:
            time.sleep(0.01)
    finally:
        up.stop(timeout=2.0)

    assert len(fake.calls) == 3
    assert fake.calls[0]["data"]["had_input"] == "1"
    assert fake.calls[1]["data"]["had_input"] == "0"
    assert fake.calls[2]["data"]["had_input"] == "0"


def test_enqueue_empty_focus_rect_sends_empty_string(tmp_path, sample_png, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    try:
        up.enqueue(image_path=sample_png, frame_index=1, timestamp=1.0,
                   cursor_x=-1, cursor_y=-1, focus_rect=None)
        deadline = time.time() + 2.0
        while time.time() < deadline and not fake.calls:
            time.sleep(0.01)
    finally:
        up.stop(timeout=2.0)
    assert fake.calls[0]["data"]["focus_rect"] == ""


def test_upload_failure_writes_to_buffer(tmp_path, sample_png, monkeypatch):
    """After max_retries the payload (minus image) is written to buffer_path."""
    fake = FakeClient()
    fake.set_response(FakeResponse(status_code=500))
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    monkeypatch.setattr("time.sleep", lambda *_: None)  # don't wait on backoff

    up = _make_uploader(tmp_path, max_retries=2)
    up.start()
    try:
        up.enqueue(image_path=sample_png, frame_index=7, timestamp=77.0,
                   cursor_x=0, cursor_y=0, focus_rect=None)
        # Wait for worker to exhaust retries and buffer the item
        deadline = time.time() + 5.0
        buf = Path(tmp_path / "buffer.jsonl")
        while time.time() < deadline and not buf.exists():
            time.sleep(0.05)
    finally:
        up.stop(timeout=2.0)

    buf = Path(tmp_path / "buffer.jsonl")
    assert buf.exists()
    lines = buf.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["frame_index"] == 7
    assert entry["image_path"] == str(sample_png)
    assert entry["cursor_x"] == 0


def test_buffer_replay_on_start(tmp_path, sample_png, monkeypatch):
    """On startup the uploader re-posts any buffer entries whose images still exist."""
    # Pre-populate buffer as if a previous session crashed
    buf = tmp_path / "buffer.jsonl"
    buf.write_text(json.dumps({
        "image_path": str(sample_png),
        "frame_index": 99, "timestamp": 50.0,
        "cursor_x": 1, "cursor_y": 2, "focus_rect": None,
    }) + "\n", encoding="utf-8")

    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and not fake.calls:
            time.sleep(0.01)
    finally:
        up.stop(timeout=2.0)

    assert len(fake.calls) == 1
    assert fake.calls[0]["data"]["frame_index"] == "99"
    # Buffer cleared after successful replay
    assert not buf.exists()


def test_buffer_replay_drops_missing_images(tmp_path, monkeypatch):
    """If the image file in a buffer entry is gone, that entry is dropped."""
    buf = tmp_path / "buffer.jsonl"
    buf.write_text(json.dumps({
        "image_path": "/nonexistent/gone.png",
        "frame_index": 88, "timestamp": 40.0,
        "cursor_x": 1, "cursor_y": 2, "focus_rect": None,
    }) + "\n", encoding="utf-8")

    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    try:
        time.sleep(0.5)  # give worker time to skip the entry
    finally:
        up.stop(timeout=2.0)

    assert len(fake.calls) == 0
    assert not buf.exists() or buf.read_text(encoding="utf-8").strip() == ""


def test_stop_drains_queue_before_returning(tmp_path, sample_png, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    for i in range(3):
        up.enqueue(image_path=sample_png, frame_index=i, timestamp=float(i),
                   cursor_x=0, cursor_y=0, focus_rect=None)
    up.stop(timeout=5.0)

    # All 3 should have been posted
    assert len(fake.calls) == 3


def test_enqueue_before_start_raises_or_ignored(tmp_path, sample_png):
    """Pre-start enqueue should not crash (and not block)."""
    up = _make_uploader(tmp_path)
    # Don't start — enqueue should be a no-op or buffer-write, not raise
    up.enqueue(image_path=sample_png, frame_index=1, timestamp=1.0,
               cursor_x=0, cursor_y=0, focus_rect=None)
    up.stop()  # safe even without start


# ---------------------------------------------------------------------------
# v0.4.5: shutdown flush — items still in queue get persisted to buffer
# ---------------------------------------------------------------------------


def test_stop_flushes_queue_remainder_to_buffer(tmp_path, sample_png, monkeypatch):
    """If stop() is called while queue has un-uploaded items, those items
    must be written to the JSONL buffer for next-session replay.
    Without this, a slow server + large backlog + daemon shutdown = data loss."""
    # FakeClient that hangs on upload so queue can't drain
    class BlockingClient:
        def __init__(self, *a, **kw):
            self.block = threading.Event()

        def post(self, *a, **kw):
            # Block until the test releases us. Simulates a slow server.
            self.block.wait(timeout=10.0)
            return FakeResponse(200)

        def close(self):
            pass

    blocking_client = BlockingClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: blocking_client)

    up = _make_uploader(tmp_path, max_retries=1, timeout=10.0)
    up.start()

    # Enqueue 5 items. Only the first will be in-flight against the
    # blocking client; items 2-5 wait in the queue.
    for i in range(1, 6):
        up.enqueue(image_path=sample_png, frame_index=i, timestamp=float(i),
                   cursor_x=i, cursor_y=i, focus_rect=None)

    # Give the worker a moment to grab the first item and block on post()
    time.sleep(0.3)

    # Stop with a short timeout — simulates daemon shutdown while queue
    # still has items.
    up.stop(timeout=1.0)

    # The unblocked client is not used anymore; release so it can exit
    blocking_client.block.set()

    # Buffer file should contain the items that never got to upload
    buf = Path(tmp_path / "buffer.jsonl")
    assert buf.exists(), "buffer file must exist after shutdown flush"
    lines = [ln for ln in buf.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # At least 2 of the 5 items should have been flushed (the first was
    # either in-flight or just starting; items 2+ definitely waiting)
    assert len(lines) >= 2, f"expected >=2 items flushed, got {len(lines)}"
    flushed_indices = {json.loads(ln)["frame_index"] for ln in lines}
    # Items enqueued last are most likely to still be in queue
    assert any(i in flushed_indices for i in (3, 4, 5))
