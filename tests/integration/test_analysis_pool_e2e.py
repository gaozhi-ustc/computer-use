"""E2E smoke: upload frame → pool analyzes it → GET returns 'done'.

Uses FakeVisionClient (no real API calls) via the AnalysisPool's worker_factory
hook, so this runs without keys. Tagged integration because it exercises the
full startup/shutdown lifecycle through FastAPI's TestClient.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_upload_then_analyzed_by_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "imgs"))
    monkeypatch.setenv("WORKFLOW_SERVER_KEY", "sk-test")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "secret")
    # Don't disable the pool — we want it running
    monkeypatch.delenv("WORKFLOW_DISABLE_ANALYSIS_POOL", raising=False)

    # Stub AnalysisPool's keys + worker_factory before startup runs.
    # Strategy: write api_keys.txt with one fake key, then monkeypatch
    # AnalysisWorker to use a FakeVisionClient.
    (tmp_path / "api_keys.txt").write_text("sk-fake-1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Monkeypatch _build_vision to return a fake that always succeeds
    class FA:
        def model_dump(self):
            return {
                "frame_index": 1, "timestamp": 100.0,
                "application": "Chrome", "window_title": "Title",
                "user_action": "typing", "ui_elements_visible": [],
                "text_content": "", "mouse_position_estimate": [],
                "confidence": 0.9, "context_data": {},
            }

    class FakeVision:
        def analyze_frame(self, image_path, window_context=None,
                          frame_index=0, timestamp=None):
            return FA()

    from server import analysis_pool as ap_mod
    monkeypatch.setattr(
        ap_mod.AnalysisWorker, "_build_vision", lambda self: FakeVision()
    )

    from server.app import app
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDAT\x08\x99c\xfa\x0f\x00\x00\x01\x01\x00\x01"
        b"\xae\xf0\x18\x95\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(app) as client:
        # Upload one frame
        r = client.post(
            "/frames/upload",
            headers={"X-API-Key": "sk-test"},
            data={"employee_id": "E1", "session_id": "s1",
                  "frame_index": "1", "timestamp": "100.0",
                  "cursor_x": "50", "cursor_y": "60", "focus_rect": ""},
            files={"image": ("1.png", png, "image/png")},
        )
        assert r.status_code == 200
        frame_id = r.json()["id"]

        # Wait up to 5s for the worker to pick it up and mark done
        from server import db as db_mod
        deadline = time.time() + 5.0
        while time.time() < deadline:
            frame = db_mod.get_frame(frame_id)
            if frame["analysis_status"] == "done":
                break
            time.sleep(0.05)
        else:
            pytest.fail(
                f"Frame never reached 'done'; final state: "
                f"{db_mod.get_frame(frame_id)}"
            )

        final = db_mod.get_frame(frame_id)
        assert final["analysis_status"] == "done"
        assert final["application"] == "Chrome"
        assert final["user_action"] == "typing"
        assert final["cursor_x"] == 50  # OS coord preserved
