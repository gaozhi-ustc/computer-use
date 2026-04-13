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
