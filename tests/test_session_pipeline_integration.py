"""Integration test: upload → finalize → group → (mock) analyze → auto SOP."""

from __future__ import annotations

import io
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from server import db
from server.app import app


@pytest.fixture
def fresh_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_DISABLE_ANALYSIS_POOL", "1")
    monkeypatch.setenv("WORKFLOW_DISABLE_SESSION_FINALIZER", "1")
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "images"))
    db.init_db()
    from server.auth import hash_password
    db.insert_user(username="admin", password_hash=hash_password("test"),
                   display_name="Admin", role="admin")
    return tmp_path


@pytest.fixture
def client(fresh_env):
    return TestClient(app)


def _upload_frame(client, session_id: str, frame_index: int):
    """Upload a fake frame."""
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    return client.post("/frames/upload", data={
        "employee_id": "E001",
        "session_id": session_id,
        "frame_index": str(frame_index),
        "timestamp": str(1000.0 + frame_index),
        "window_title_raw": "chrome" if frame_index < 3 else "excel",
    }, files={"image": ("f.png", io.BytesIO(fake_png), "image/png")})


@pytest.mark.integration
def test_full_pipeline(fresh_env, client, monkeypatch):
    # 1. Upload 6 frames
    for i in range(6):
        resp = _upload_frame(client, "sess-int", i)
        assert resp.status_code == 200, f"frame {i} upload failed: {resp.text}"

    # Verify session created
    sess = db.get_session("sess-int")
    assert sess is not None
    assert sess["status"] == "active"
    assert sess["frame_count"] == 6

    # 2. Run SessionFinalizer manually
    from server import session_finalizer
    from server.session_finalizer import SessionFinalizer
    from server.frame_grouper import FrameGroup

    # Mock grouper to avoid phash on fake PNGs — patch on session_finalizer
    # since it does `from server.frame_grouper import group_frames`
    monkeypatch.setattr(
        session_finalizer, "group_frames",
        lambda frames, **kw: [
            FrameGroup(0, [f["id"] for f in frames[:3]], "chrome"),
            FrameGroup(1, [f["id"] for f in frames[3:]], "excel"),
        ],
    )

    stop = threading.Event()
    finalizer = SessionFinalizer(stop_event=stop, idle_timeout=0, poll_interval=0.1)
    # Directly finalize the session instead of relying on timing-sensitive polling
    sess_row = db.get_session("sess-int")
    finalizer._finalize_session(sess_row)

    # 3. Verify grouped
    sess = db.get_session("sess-int")
    assert sess["status"] == "grouped"
    groups = db.list_frame_groups("sess-int")
    assert len(groups) == 2

    # 4. Simulate analysis completion
    for g in groups:
        claimed = db.claim_next_pending_group()
        assert claimed is not None
        db.store_group_analysis_result(
            "sess-int", claimed["group_index"],
            [{"step_order": 1, "title": f"Action in {claimed['primary_application']}",
              "human_description": "Do something",
              "machine_actions": [{"type": "click", "x": 100, "y": 200, "target": "button"}],
              "application": claimed["primary_application"],
              "key_frame_indices": [0]}],
        )
        db.mark_group_done(claimed["id"])

    assert db.all_groups_done("sess-int")

    # 5. Trigger auto SOP creation
    from server.analysis_pool import _auto_create_sop
    _auto_create_sop("sess-int", "E001")

    sess = db.get_session("sess-int")
    assert sess["status"] == "analyzed"

    # Verify SOP exists
    sops = db.list_sops()
    assert len(sops) >= 1
    sop = sops[0]
    steps = db.list_sop_steps(sop["id"])
    assert len(steps) == 2
