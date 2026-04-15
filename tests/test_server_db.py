"""Tests for server/db.py — SQLite schema + insert/query helpers.

Each test uses a fresh on-disk SQLite file inside tmp_path, selected via
the WORKFLOW_SERVER_DB env var that server/db.py honors.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from server import db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point db_path() at a temp file and run init_db()."""
    db_file = tmp_path / "frames_test.db"
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(db_file))
    db.init_db()
    return db_file


def _sample_frame(**overrides) -> dict:
    base = {
        "employee_id": "E001",
        "session_id": "sess-a",
        "frame_index": 1,
        "timestamp": 1_712_856_000.0,
        "application": "chrome.exe",
        "window_title": "Google Chrome",
        "user_action": "clicked Submit",
        "text_content": "hello world",
        "confidence": 0.91,
        "mouse_position_estimate": [120, 240],
        "ui_elements_visible": [
            {"name": "Submit", "element_type": "button", "coordinates": [100, 200]},
            {"name": "Cancel", "element_type": "button", "coordinates": [180, 200]},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------


def test_init_db_is_idempotent(fresh_db):
    db.init_db()
    db.init_db()  # no-op, no error


def test_db_path_respects_env(tmp_path, monkeypatch):
    target = tmp_path / "custom.db"
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(target))
    assert db.db_path() == target.resolve()


# ---------------------------------------------------------------------------
# insert_frame
# ---------------------------------------------------------------------------


def test_insert_frame_happy_path(fresh_db):
    row_id = db.insert_frame(_sample_frame())
    assert row_id == 1

    rows = db.query_frames(employee_id="E001")
    assert len(rows) == 1
    row = rows[0]
    assert row["employee_id"] == "E001"
    assert row["frame_index"] == 1
    assert row["application"] == "chrome.exe"
    assert row["confidence"] == pytest.approx(0.91)
    # ui_elements roundtrips as a list of dicts
    assert len(row["ui_elements"]) == 2
    assert row["ui_elements"][0]["name"] == "Submit"
    assert row["mouse_position"] == [120, 240]


def test_insert_duplicate_returns_none(fresh_db):
    first = db.insert_frame(_sample_frame())
    assert first == 1
    # Identical (employee_id, session_id, frame_index) triple
    second = db.insert_frame(_sample_frame())
    assert second is None
    # Even with different payload data
    third = db.insert_frame(_sample_frame(user_action="different action"))
    assert third is None
    # DB still holds exactly one row
    assert db.count_frames() == 1


def test_different_frame_index_inserts_separately(fresh_db):
    assert db.insert_frame(_sample_frame(frame_index=1)) == 1
    assert db.insert_frame(_sample_frame(frame_index=2)) == 2
    assert db.insert_frame(_sample_frame(frame_index=3)) == 3
    assert db.count_frames() == 3


def test_different_employees_do_not_collide(fresh_db):
    db.insert_frame(_sample_frame(employee_id="E001", frame_index=1))
    db.insert_frame(_sample_frame(employee_id="E002", frame_index=1))
    assert db.count_frames() == 2
    assert db.count_frames(employee_id="E001") == 1
    assert db.count_frames(employee_id="E002") == 1


def test_insert_accepts_empty_optional_fields(fresh_db):
    db.insert_frame({
        "employee_id": "E9",
        "session_id": "s9",
        "frame_index": 1,
        "timestamp": 1_712_856_000.0,
    })
    rows = db.query_frames(employee_id="E9")
    assert len(rows) == 1
    assert rows[0]["application"] is None or rows[0]["application"] == ""
    assert rows[0]["ui_elements"] == []
    assert rows[0]["mouse_position"] == []


# ---------------------------------------------------------------------------
# query_frames
# ---------------------------------------------------------------------------


def test_query_filters_by_employee_and_session(fresh_db):
    db.insert_frame(_sample_frame(employee_id="E1", session_id="sA", frame_index=1))
    db.insert_frame(_sample_frame(employee_id="E1", session_id="sA", frame_index=2))
    db.insert_frame(_sample_frame(employee_id="E1", session_id="sB", frame_index=1))
    db.insert_frame(_sample_frame(employee_id="E2", session_id="sA", frame_index=1))

    assert len(db.query_frames(employee_id="E1")) == 3
    assert len(db.query_frames(employee_id="E1", session_id="sA")) == 2
    assert len(db.query_frames(session_id="sA")) == 3
    assert len(db.query_frames()) == 4


def test_query_pagination(fresh_db):
    for i in range(1, 11):
        db.insert_frame(_sample_frame(frame_index=i))

    page1 = db.query_frames(limit=3, offset=0)
    page2 = db.query_frames(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    # No overlap
    p1_idx = {r["frame_index"] for r in page1}
    p2_idx = {r["frame_index"] for r in page2}
    assert p1_idx.isdisjoint(p2_idx)


def test_count_frames_matches_query(fresh_db):
    for i in range(1, 6):
        db.insert_frame(_sample_frame(frame_index=i))

    assert db.count_frames() == 5
    assert db.count_frames(employee_id="E001") == 5
    assert db.count_frames(employee_id="nobody") == 0


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def test_ts_to_iso_converts_float():
    iso = db._ts_to_iso(1_712_856_000.0)
    # A plain unix epoch float should produce an ISO string ending in +00:00
    assert iso.endswith("+00:00")
    # Parse back and sanity-check
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None


def test_ts_to_iso_passes_through_string():
    iso = db._ts_to_iso("2026-04-11T10:00:00+00:00")
    assert iso == "2026-04-11T10:00:00+00:00"


def test_ts_to_iso_handles_none_falls_back_to_now():
    iso = db._ts_to_iso(None)
    assert isinstance(iso, str)
    # The fallback should be parseable as ISO
    datetime.fromisoformat(iso)


# ---------------------------------------------------------------------------
# Unicode roundtrip
# ---------------------------------------------------------------------------


def test_unicode_in_user_action_and_ui_elements(fresh_db):
    db.insert_frame(_sample_frame(
        user_action="在聊天窗口输入消息",
        ui_elements_visible=[
            {"name": "发送", "element_type": "button", "coordinates": [50, 100]},
        ],
    ))
    rows = db.query_frames()
    assert rows[0]["user_action"] == "在聊天窗口输入消息"
    assert rows[0]["ui_elements"][0]["name"] == "发送"


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


def test_insert_user_and_query(fresh_db):
    from server.db import insert_user, get_user_by_username
    user_id = insert_user(
        username="testadmin", password_hash="$2b$12$fakehash",
        display_name="Test Admin", role="admin", employee_id="E001",
    )
    assert user_id == 1
    user = get_user_by_username("testadmin")
    assert user is not None
    assert user["username"] == "testadmin"
    assert user["role"] == "admin"
    assert user["employee_id"] == "E001"
    assert user["is_active"] == 1


def test_insert_duplicate_username_raises(fresh_db):
    from server.db import insert_user
    import sqlite3
    insert_user(username="dup", password_hash="x", display_name="A", role="employee")
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(username="dup", password_hash="y", display_name="B", role="employee")


def test_get_user_by_id(fresh_db):
    from server.db import insert_user, get_user_by_id
    uid = insert_user(username="byid", password_hash="x", display_name="ByID", role="employee")
    user = get_user_by_id(uid)
    assert user["username"] == "byid"


def test_list_users(fresh_db):
    from server.db import insert_user, list_users
    insert_user(username="a", password_hash="x", display_name="A", role="admin")
    insert_user(username="b", password_hash="x", display_name="B", role="employee")
    insert_user(username="c", password_hash="x", display_name="C", role="manager")
    users = list_users()
    assert len(users) == 3


def test_update_user(fresh_db):
    from server.db import insert_user, update_user, get_user_by_id
    uid = insert_user(username="upd", password_hash="x", display_name="Old", role="employee")
    update_user(uid, display_name="New", role="manager")
    user = get_user_by_id(uid)
    assert user["display_name"] == "New"
    assert user["role"] == "manager"


def test_delete_user(fresh_db):
    from server.db import insert_user, delete_user, get_user_by_id
    uid = insert_user(username="del", password_hash="x", display_name="Del", role="employee")
    delete_user(uid)
    assert get_user_by_id(uid) is None


# ---------------------------------------------------------------------------
# Offline analysis — migration (Task 1)
# ---------------------------------------------------------------------------


def test_migration_adds_offline_columns(fresh_db):
    """DB must have image_path, analysis_status, cursor_x/y, focus_rect_json columns."""
    import server.db as db_mod
    with db_mod.connect() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(frames)").fetchall()}
    for expected in ("image_path", "analysis_status", "analysis_error",
                     "analysis_attempts", "analyzed_at",
                     "cursor_x", "cursor_y", "focus_rect_json"):
        assert expected in cols, f"Column {expected} not added by migration"


def test_migration_adds_status_index(fresh_db):
    """An index on (analysis_status, id) must exist for worker queue queries."""
    import server.db as db_mod
    with db_mod.connect() as conn:
        idx_names = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
    assert "idx_frames_status" in idx_names


# ---------------------------------------------------------------------------
# Offline analysis — queue helpers (Task 2)
# ---------------------------------------------------------------------------


def test_insert_pending_frame(fresh_db):
    from server.db import insert_pending_frame, get_frame
    frame_id = insert_pending_frame(
        employee_id="E001",
        session_id="sess-a",
        frame_index=1,
        timestamp=1712856000.0,
        image_path="/tmp/test/1.png",
        cursor_x=500,
        cursor_y=300,
        focus_rect=[100, 100, 300, 200],
    )
    assert frame_id == 1
    frame = get_frame(frame_id)
    assert frame["analysis_status"] == "uploaded"
    assert frame["image_path"] == "/tmp/test/1.png"
    assert frame["cursor_x"] == 500
    assert frame["cursor_y"] == 300
    assert frame["focus_rect"] == [100, 100, 300, 200]


def test_insert_pending_frame_without_optional_fields(fresh_db):
    from server.db import insert_pending_frame, get_frame
    frame_id = insert_pending_frame(
        employee_id="E001", session_id="sess-a", frame_index=1,
        timestamp=1712856000.0, image_path="/tmp/1.png",
    )
    frame = get_frame(frame_id)
    assert frame["cursor_x"] == -1
    assert frame["cursor_y"] == -1
    assert frame["focus_rect"] is None


def test_claim_next_pending_frame_returns_oldest(fresh_db):
    from server.db import insert_pending_frame, claim_next_pending_frame
    insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                         timestamp=100.0, image_path="/tmp/1.png",
                         analysis_status="pending")
    insert_pending_frame(employee_id="E1", session_id="s", frame_index=2,
                         timestamp=200.0, image_path="/tmp/2.png",
                         analysis_status="pending")
    frame = claim_next_pending_frame()
    assert frame is not None
    assert frame["frame_index"] == 1
    assert frame["analysis_attempts"] == 1  # incremented on claim


def test_claim_next_pending_frame_returns_none_when_empty(fresh_db):
    from server.db import claim_next_pending_frame
    assert claim_next_pending_frame() is None


def test_claim_next_pending_frame_skips_running_and_done_and_failed(fresh_db):
    from server.db import insert_pending_frame, claim_next_pending_frame, mark_frame_done, mark_frame_failed
    # frame 1 - will be marked done
    id1 = insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                               timestamp=1.0, image_path="/tmp/1.png",
                               analysis_status="pending")
    # frame 2 - will be marked failed
    id2 = insert_pending_frame(employee_id="E1", session_id="s", frame_index=2,
                               timestamp=2.0, image_path="/tmp/2.png",
                               analysis_status="pending")
    # frame 3 - stays pending
    id3 = insert_pending_frame(employee_id="E1", session_id="s", frame_index=3,
                               timestamp=3.0, image_path="/tmp/3.png",
                               analysis_status="pending")

    # Claim id1 and mark done
    claim_next_pending_frame()
    mark_frame_done(id1, {"application": "Chrome", "user_action": "test",
                          "window_title": "", "text_content": "",
                          "confidence": 0.9, "ui_elements_visible": [],
                          "mouse_position_estimate": [], "context_data": {},
                          "timestamp": 1.0, "frame_index": 1})
    # Claim id2 and mark failed
    claim_next_pending_frame()
    mark_frame_failed(id2, "test failure")

    # Next claim should return id3
    next_frame = claim_next_pending_frame()
    assert next_frame is not None
    assert next_frame["id"] == id3


def test_mark_frame_done_updates_analysis_fields(fresh_db):
    from server.db import insert_pending_frame, mark_frame_done, get_frame
    fid = insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                               timestamp=100.0, image_path="/tmp/1.png",
                               analysis_status="pending")
    result = {
        "application": "Chrome",
        "window_title": "GitHub",
        "user_action": "scrolling",
        "text_content": "readme",
        "confidence": 0.9,
        "ui_elements_visible": [{"name": "button", "element_type": "button", "coordinates": [1, 2]}],
        "mouse_position_estimate": [100, 200],
        "context_data": {"page_title": "README"},
        "timestamp": 100.0,
        "frame_index": 1,
    }
    mark_frame_done(fid, result)
    frame = get_frame(fid)
    assert frame["analysis_status"] == "done"
    assert frame["application"] == "Chrome"
    assert frame["user_action"] == "scrolling"
    assert frame["confidence"] == 0.9
    assert frame["context_data"] == {"page_title": "README"}
    assert frame["ui_elements"][0]["name"] == "button"
    assert frame["analyzed_at"] != ""


def test_mark_frame_failed_records_error(fresh_db):
    from server.db import insert_pending_frame, mark_frame_failed, get_frame
    fid = insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                               timestamp=100.0, image_path="/tmp/1.png",
                               analysis_status="pending")
    mark_frame_failed(fid, "401 unauthorized")
    frame = get_frame(fid)
    assert frame["analysis_status"] == "failed"
    assert frame["analysis_error"] == "401 unauthorized"


def test_reset_frame_to_pending_from_running(fresh_db):
    from server.db import insert_pending_frame, claim_next_pending_frame, reset_frame_to_pending, get_frame
    fid = insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                               timestamp=100.0, image_path="/tmp/1.png",
                               analysis_status="pending")
    claim_next_pending_frame()  # now running, attempts=1
    reset_frame_to_pending(fid)
    frame = get_frame(fid)
    assert frame["analysis_status"] == "pending"
    assert frame["analysis_attempts"] == 1  # not cleared by default


def test_reset_frame_to_pending_with_clear_attempts(fresh_db):
    from server.db import insert_pending_frame, claim_next_pending_frame, mark_frame_failed, reset_frame_to_pending, get_frame
    fid = insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                               timestamp=100.0, image_path="/tmp/1.png",
                               analysis_status="pending")
    claim_next_pending_frame()
    mark_frame_failed(fid, "oops")
    reset_frame_to_pending(fid, clear_attempts=True)
    frame = get_frame(fid)
    assert frame["analysis_status"] == "pending"
    assert frame["analysis_attempts"] == 0


def test_queue_stats(fresh_db):
    from server.db import insert_pending_frame, claim_next_pending_frame, mark_frame_done, mark_frame_failed, get_analysis_queue_stats
    # Seed 4 frames in different states
    for i in range(1, 5):
        insert_pending_frame(employee_id="E1", session_id="s", frame_index=i,
                             timestamp=float(i), image_path=f"/tmp/{i}.png",
                             analysis_status="pending")
    # Claim first, mark done
    f1 = claim_next_pending_frame()
    mark_frame_done(f1["id"], {
        "application": "", "window_title": "", "user_action": "", "text_content": "",
        "confidence": 0.0, "ui_elements_visible": [], "mouse_position_estimate": [],
        "context_data": {}, "timestamp": 1.0, "frame_index": 1,
    })
    # Claim second, mark failed
    f2 = claim_next_pending_frame()
    mark_frame_failed(f2["id"], "x")
    # Claim third, leave running
    claim_next_pending_frame()
    # Fourth stays pending
    stats = get_analysis_queue_stats()
    assert stats == {"uploaded": 0, "pending": 1, "running": 1, "failed": 1, "done": 1}


# ---------------------------------------------------------------------------
# Sessions table
# ---------------------------------------------------------------------------


def test_upsert_session_insert(fresh_db):
    """First frame for a session creates a new sessions row."""
    from server import db
    db.upsert_session(
        session_id="sess-1",
        employee_id="E001",
        frame_at="2026-04-15T10:00:00+00:00",
    )
    sess = db.get_session("sess-1")
    assert sess is not None
    assert sess["employee_id"] == "E001"
    assert sess["status"] == "active"
    assert sess["frame_count"] == 1
    assert sess["first_frame_at"] == "2026-04-15T10:00:00+00:00"
    assert sess["last_frame_at"] == "2026-04-15T10:00:00+00:00"


def test_upsert_session_update(fresh_db):
    """Subsequent frames update last_frame_at and increment frame_count."""
    from server import db
    db.upsert_session("sess-1", "E001", "2026-04-15T10:00:00+00:00")
    db.upsert_session("sess-1", "E001", "2026-04-15T10:00:03+00:00")
    db.upsert_session("sess-1", "E001", "2026-04-15T10:00:06+00:00")
    sess = db.get_session("sess-1")
    assert sess["frame_count"] == 3
    assert sess["first_frame_at"] == "2026-04-15T10:00:00+00:00"
    assert sess["last_frame_at"] == "2026-04-15T10:00:06+00:00"


def test_list_idle_sessions(fresh_db):
    """list_idle_sessions returns only active sessions older than timeout."""
    from server import db
    db.upsert_session("sess-old", "E001", "2026-04-15T09:00:00+00:00")
    db.upsert_session("sess-new", "E001", "2026-04-15T10:00:00+00:00")
    idle = db.list_idle_sessions(cutoff_iso="2026-04-15T09:55:00+00:00")
    assert len(idle) == 1
    assert idle[0]["session_id"] == "sess-old"


def test_update_session_status(fresh_db):
    """update_session_status transitions session state."""
    from server import db
    db.upsert_session("sess-1", "E001", "2026-04-15T10:00:00+00:00")
    db.update_session_status("sess-1", "finalizing")
    sess = db.get_session("sess-1")
    assert sess["status"] == "finalizing"


# ---------------------------------------------------------------------------
# Frame groups table
# ---------------------------------------------------------------------------


def test_insert_frame_group(fresh_db):
    """insert_frame_group creates a group with pending status."""
    from server import db
    gid = db.insert_frame_group(
        session_id="sess-1",
        employee_id="E001",
        group_index=0,
        frame_ids=[10, 11, 12],
        primary_application="chrome.exe",
    )
    assert gid is not None
    group = db.get_frame_group(gid)
    assert group["session_id"] == "sess-1"
    assert group["frame_ids"] == [10, 11, 12]
    assert group["analysis_status"] == "pending"
    assert group["analysis_attempts"] == 0


def test_claim_next_pending_group(fresh_db):
    """claim_next_pending_group atomically claims oldest pending group."""
    from server import db
    db.insert_frame_group("sess-1", "E001", 0, [10, 11], "chrome")
    db.insert_frame_group("sess-1", "E001", 1, [12, 13], "excel")
    claimed = db.claim_next_pending_group()
    assert claimed is not None
    assert claimed["group_index"] == 0
    assert claimed["analysis_status"] == "running"
    assert claimed["analysis_attempts"] == 1
    claimed2 = db.claim_next_pending_group()
    assert claimed2["group_index"] == 1
    assert db.claim_next_pending_group() is None


def test_mark_group_done(fresh_db):
    """mark_group_done sets status to done."""
    from server import db
    gid = db.insert_frame_group("sess-1", "E001", 0, [10], "chrome")
    db.claim_next_pending_group()
    db.mark_group_done(gid)
    group = db.get_frame_group(gid)
    assert group["analysis_status"] == "done"
    assert group["analyzed_at"] != ""


def test_mark_group_failed(fresh_db):
    """mark_group_failed sets status to failed with error."""
    from server import db
    gid = db.insert_frame_group("sess-1", "E001", 0, [10], "chrome")
    db.claim_next_pending_group()
    db.mark_group_failed(gid, "API timeout")
    group = db.get_frame_group(gid)
    assert group["analysis_status"] == "failed"
    assert group["analysis_error"] == "API timeout"


def test_all_groups_done(fresh_db):
    """all_groups_done returns True only when every group is done."""
    from server import db
    db.insert_frame_group("sess-1", "E001", 0, [10], "chrome")
    db.insert_frame_group("sess-1", "E001", 1, [11], "chrome")
    assert db.all_groups_done("sess-1") is False
    g = db.claim_next_pending_group()
    db.mark_group_done(g["id"])
    assert db.all_groups_done("sess-1") is False
    g2 = db.claim_next_pending_group()
    db.mark_group_done(g2["id"])
    assert db.all_groups_done("sess-1") is True


# ---------------------------------------------------------------------------
# SOP feedbacks & revisions
# ---------------------------------------------------------------------------


def _create_test_sop(db_module) -> int:
    """Helper: create a minimal SOP and return its id."""
    return db_module.insert_sop(title="Test SOP", created_by="admin")


def test_insert_sop_feedback(fresh_db):
    from server import db
    sop_id = _create_test_sop(db)
    fid = db.insert_sop_feedback(
        sop_id=sop_id, revision=1, user_id="admin",
        feedback_text="Step 3 needs more detail", feedback_scope="step:3",
    )
    assert fid is not None
    feedbacks = db.list_sop_feedbacks(sop_id)
    assert len(feedbacks) == 1
    assert feedbacks[0]["feedback_scope"] == "step:3"


def test_insert_sop_revision(fresh_db):
    from server import db
    sop_id = _create_test_sop(db)
    import json
    snapshot = json.dumps([{"step_order": 1, "title": "Open app"}])
    rid = db.insert_sop_revision(
        sop_id=sop_id, revision=1, steps_snapshot_json=snapshot,
    )
    assert rid is not None
    revs = db.list_sop_revisions(sop_id)
    assert len(revs) == 1
    assert revs[0]["revision"] == 1


def test_get_sop_revision(fresh_db):
    from server import db
    import json
    sop_id = _create_test_sop(db)
    snapshot = json.dumps([{"step_order": 1, "title": "Open app"}])
    db.insert_sop_revision(sop_id, 1, snapshot)
    rev = db.get_sop_revision(sop_id, 1)
    assert rev is not None
    assert json.loads(rev["steps_snapshot_json"]) == [{"step_order": 1, "title": "Open app"}]
