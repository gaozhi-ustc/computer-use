"""Tests for the background FramePusher."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from workflow_recorder.analysis.frame_analysis import FrameAnalysis, UIElement
from workflow_recorder.config import ServerConfig
from workflow_recorder.frame_pusher import FramePusher


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


def _make_analysis(frame_index: int = 1, **overrides) -> FrameAnalysis:
    """Build a FrameAnalysis with sensible test defaults."""
    defaults = dict(
        frame_index=frame_index,
        timestamp=1_712_856_000.0 + frame_index,
        application="chrome.exe",
        window_title=f"Window {frame_index}",
        user_action=f"clicked button {frame_index}",
        ui_elements_visible=[UIElement(name="Submit", element_type="button", coordinates=[10, 20])],
        text_content="hello",
        mouse_position_estimate=[100, 200],
        confidence=0.9,
    )
    defaults.update(overrides)
    return FrameAnalysis(**defaults)


def _server_cfg(tmp_path: Path, **overrides) -> ServerConfig:
    cfg = dict(
        enabled=True,
        url="http://unit-test.invalid",
        api_key="unit-key",
        timeout_seconds=1.0,
        max_retries=2,
        buffer_path=str(tmp_path / "push_buffer.jsonl"),
        queue_size=10,
    )
    cfg.update(overrides)
    return ServerConfig(**cfg)


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeClient:
    """Captures calls so we can assert on them; doesn't touch the network."""

    def __init__(self, *, responses=None, **kwargs):
        # responses is a list of status codes to return in order. If exhausted,
        # returns 200 forever. If a tuple (status_code, exception), raises.
        self.responses = list(responses or [200])
        self.calls: list[dict] = []
        self.headers = kwargs.get("headers", {})
        self.closed = False

    def post(self, url: str, json=None):
        self.calls.append({"url": url, "json": json})
        if not self.responses:
            return FakeResponse(200)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Disabled pusher is a strict no-op
# ---------------------------------------------------------------------------


def test_disabled_pusher_does_not_spawn_thread(tmp_path):
    cfg = _server_cfg(tmp_path, enabled=False)
    pusher = FramePusher(cfg, employee_id="E001", session_id="sess-1")
    pusher.start()
    assert pusher._thread is None

    pusher.enqueue(_make_analysis())
    # Nothing got queued and no buffer file was created
    assert pusher._queue.qsize() == 0
    assert not Path(cfg.buffer_path).exists()

    pusher.stop()  # should not raise


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------


def test_payload_includes_employee_and_session_ids(tmp_path):
    cfg = _server_cfg(tmp_path)
    pusher = FramePusher(cfg, employee_id="E007", session_id="sess-xyz")

    analysis = _make_analysis(frame_index=5, user_action="typed password")
    payload = pusher._build_payload(analysis)

    assert payload["employee_id"] == "E007"
    assert payload["session_id"] == "sess-xyz"
    assert payload["frame_index"] == 5
    assert payload["user_action"] == "typed password"
    # Nested ui_elements serialize via pydantic model_dump
    assert isinstance(payload["ui_elements_visible"], list)
    assert payload["ui_elements_visible"][0]["name"] == "Submit"


# ---------------------------------------------------------------------------
# Queue-full spills directly to buffer
# ---------------------------------------------------------------------------


def test_enqueue_on_full_queue_spills_to_buffer(tmp_path):
    cfg = _server_cfg(tmp_path, queue_size=1)
    pusher = FramePusher(cfg, employee_id="E001", session_id="sess-1")
    # Manually fill the queue without starting a consumer thread
    pusher._queue.put({"__full__": True})
    assert pusher._queue.full()

    pusher.enqueue(_make_analysis(frame_index=42))

    # The new frame spilled to disk
    buffer_file = Path(cfg.buffer_path)
    assert buffer_file.exists()
    lines = buffer_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    dropped = json.loads(lines[0])
    assert dropped["frame_index"] == 42
    assert dropped["employee_id"] == "E001"
    assert pusher.buffered == 1


# ---------------------------------------------------------------------------
# _send_with_retry — happy path, retries, give-up semantics
# ---------------------------------------------------------------------------


def test_send_with_retry_success_first_try(tmp_path):
    cfg = _server_cfg(tmp_path)
    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")
    client = FakeClient(responses=[200])

    ok = pusher._send_with_retry(client, "http://x/frames", {"frame_index": 1})
    assert ok is True
    assert len(client.calls) == 1


def test_send_with_retry_retries_on_5xx_then_gives_up(tmp_path, monkeypatch):
    cfg = _server_cfg(tmp_path, max_retries=3)
    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")
    # No actual sleeping during tests
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeClient(responses=[503, 500, 502])

    ok = pusher._send_with_retry(client, "http://x/frames", {"i": 1})
    assert ok is False
    assert len(client.calls) == 3  # exactly max_retries attempts


def test_send_with_retry_does_not_retry_400(tmp_path):
    cfg = _server_cfg(tmp_path, max_retries=3)
    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")
    client = FakeClient(responses=[400])

    ok = pusher._send_with_retry(client, "http://x/frames", {"i": 1})
    assert ok is False
    assert len(client.calls) == 1  # no retries on client error


def test_send_with_retry_does_retry_429(tmp_path, monkeypatch):
    """429 (rate limit) should be treated as transient and retried."""
    cfg = _server_cfg(tmp_path, max_retries=2)
    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")
    monkeypatch.setattr("time.sleep", lambda _: None)
    client = FakeClient(responses=[429, 200])

    ok = pusher._send_with_retry(client, "http://x/frames", {"i": 1})
    assert ok is True
    assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# Buffer replay at startup
# ---------------------------------------------------------------------------


def test_replay_buffer_sends_all_lines_and_deletes_file(tmp_path):
    cfg = _server_cfg(tmp_path)
    buffer = Path(cfg.buffer_path)
    buffer.parent.mkdir(parents=True, exist_ok=True)
    buffer.write_text(
        json.dumps({"frame_index": 1, "employee_id": "E1"}) + "\n"
        + json.dumps({"frame_index": 2, "employee_id": "E1"}) + "\n",
        encoding="utf-8",
    )

    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")
    client = FakeClient(responses=[200, 200])

    pusher._replay_buffer(client, "http://x/frames")

    assert len(client.calls) == 2
    assert client.calls[0]["json"]["frame_index"] == 1
    assert client.calls[1]["json"]["frame_index"] == 2
    assert pusher.pushed_ok == 2
    assert not buffer.exists()  # cleared after full drain


def test_replay_buffer_keeps_unsent_lines(tmp_path, monkeypatch):
    cfg = _server_cfg(tmp_path, max_retries=1)
    buffer = Path(cfg.buffer_path)
    buffer.parent.mkdir(parents=True, exist_ok=True)
    buffer.write_text(
        json.dumps({"frame_index": 1}) + "\n"
        + json.dumps({"frame_index": 2}) + "\n"
        + json.dumps({"frame_index": 3}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("time.sleep", lambda _: None)

    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")
    # First line succeeds, second fails (500), third succeeds
    client = FakeClient(responses=[200, 500, 200])

    pusher._replay_buffer(client, "http://x/frames")

    assert pusher.pushed_ok == 2
    # Buffer still exists and contains only the failed frame
    assert buffer.exists()
    remaining = [json.loads(ln) for ln in buffer.read_text(encoding="utf-8").splitlines()]
    assert len(remaining) == 1
    assert remaining[0]["frame_index"] == 2


def test_replay_buffer_absent_is_noop(tmp_path):
    cfg = _server_cfg(tmp_path)
    buffer = Path(cfg.buffer_path)
    assert not buffer.exists()

    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")
    # Should not raise
    pusher._replay_buffer(FakeClient(), "http://x/frames")
    assert pusher.pushed_ok == 0


# ---------------------------------------------------------------------------
# Append to buffer
# ---------------------------------------------------------------------------


def test_append_to_buffer_creates_and_appends(tmp_path):
    cfg = _server_cfg(tmp_path)
    pusher = FramePusher(cfg, employee_id="E1", session_id="S1")

    pusher._append_to_buffer({"frame_index": 1})
    pusher._append_to_buffer({"frame_index": 2})

    lines = Path(cfg.buffer_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["frame_index"] == 1
    assert json.loads(lines[1])["frame_index"] == 2


# ---------------------------------------------------------------------------
# End-to-end thread cycle (exercises _run, _replay_buffer, _serve_queue)
# ---------------------------------------------------------------------------


def test_full_thread_cycle_pushes_live_frames(tmp_path, monkeypatch):
    """Start a pusher with a fake httpx and verify frames round-trip."""
    import httpx as real_httpx

    cfg = _server_cfg(tmp_path)

    # Record every post the pusher makes
    received: list[dict] = []

    class RecordingClient:
        def __init__(self, **_kwargs):
            pass

        def post(self, url, json=None):
            received.append({"url": url, "json": json})
            return FakeResponse(200)

        def close(self):
            pass

    monkeypatch.setattr(real_httpx, "Client", RecordingClient)

    pusher = FramePusher(cfg, employee_id="E-thread", session_id="sess-thr")
    pusher.start()

    pusher.enqueue(_make_analysis(frame_index=1))
    pusher.enqueue(_make_analysis(frame_index=2))
    pusher.enqueue(_make_analysis(frame_index=3))

    # Give the background thread a chance to drain the queue
    deadline = time.time() + 5
    while time.time() < deadline and pusher.pushed_ok < 3:
        time.sleep(0.05)

    pusher.stop(flush_timeout=2.0)

    assert pusher.pushed_ok == 3
    assert pusher.buffered == 0
    assert len(received) == 3
    assert [r["json"]["frame_index"] for r in received] == [1, 2, 3]
    assert all(r["json"]["employee_id"] == "E-thread" for r in received)
    assert all(r["url"].endswith("/frames") for r in received)
