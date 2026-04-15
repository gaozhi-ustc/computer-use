"""Tests for server/session_finalizer.py."""

from __future__ import annotations

import threading
import time

import pytest

from server import db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(db_file))
    db.init_db()
    return db_file


def _insert_frames(session_id: str, employee_id: str, count: int,
                    start_ts: str = "2026-04-15T09:00:00+00:00"):
    """Insert test frames and a session record."""
    db.upsert_session(session_id, employee_id, start_ts)
    for i in range(count):
        db.insert_pending_frame(
            employee_id=employee_id,
            session_id=session_id,
            frame_index=i,
            timestamp=1000.0 + i,
            image_path=f"/tmp/fake/{i}.png",
            analysis_status="uploaded",
        )
    for i in range(1, count):
        db.upsert_session(session_id, employee_id, start_ts)


def test_finalize_idle_session(fresh_db, monkeypatch):
    """SessionFinalizer should group frames for idle sessions."""
    from server.session_finalizer import SessionFinalizer

    _insert_frames("sess-1", "E001", 6)

    from server import session_finalizer
    from server.frame_grouper import FrameGroup
    monkeypatch.setattr(
        session_finalizer, "group_frames",
        lambda frames, **kw: [
            FrameGroup(group_index=0, frame_ids=[f["id"] for f in frames[:3]],
                       primary_application="chrome"),
            FrameGroup(group_index=1, frame_ids=[f["id"] for f in frames[3:]],
                       primary_application="excel"),
        ],
    )

    stop = threading.Event()
    finalizer = SessionFinalizer(
        stop_event=stop,
        idle_timeout=0,
        poll_interval=0.1,
    )

    t = threading.Thread(target=finalizer.run, daemon=True)
    t.start()
    time.sleep(0.5)
    stop.set()
    t.join(timeout=2)

    sess = db.get_session("sess-1")
    assert sess["status"] == "grouped"

    groups = db.list_frame_groups("sess-1")
    assert len(groups) == 2
    assert groups[0]["analysis_status"] == "pending"


def test_finalizer_skips_non_active(fresh_db, monkeypatch):
    """Sessions not in 'active' status should be ignored."""
    from server.session_finalizer import SessionFinalizer

    _insert_frames("sess-1", "E001", 3)
    db.update_session_status("sess-1", "analyzed")

    stop = threading.Event()
    finalizer = SessionFinalizer(stop_event=stop, idle_timeout=0, poll_interval=0.1)
    t = threading.Thread(target=finalizer.run, daemon=True)
    t.start()
    time.sleep(0.3)
    stop.set()
    t.join(timeout=2)

    sess = db.get_session("sess-1")
    assert sess["status"] == "analyzed"
