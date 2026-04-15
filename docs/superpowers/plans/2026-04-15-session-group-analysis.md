# Session-Based Group Analysis & Interactive SOP Refinement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-frame real-time analysis with session-level grouped analysis and interactive SOP refinement with revision tracking.

**Architecture:** SessionFinalizer daemon detects idle sessions, FrameGrouper clusters frames by logical action (app switch / image hash / time gap / cursor jump), AnalysisPool workers analyze each group via multi-image vision API call producing SOP steps, users review and iteratively refine via feedback loop.

**Tech Stack:** Python 3.12, FastAPI, SQLite, OpenAI-compatible vision API (DashScope qwen3.6-plus), Vue 3 + Naive UI + TypeScript

**Spec:** `docs/superpowers/specs/2026-04-15-session-group-analysis-design.md`

---

## Task 1: Database Schema — New Tables & ALTER Existing

**Files:**
- Modify: `server/db.py`
- Test: `tests/test_server_db.py`

This task adds 4 new tables (sessions, frame_groups, sop_feedbacks, sop_revisions) and ALTERs 2 existing tables (sops, sop_steps). All changes are idempotent.

- [ ] **Step 1: Write tests for new sessions table CRUD**

Add to `tests/test_server_db.py`:

```python
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
    # Cutoff is 09:55 — sess-old is before it, sess-new is after
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
```

- [ ] **Step 2: Write tests for frame_groups table CRUD**

Add to `tests/test_server_db.py`:

```python
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
    # Second claim gets group_index=1
    claimed2 = db.claim_next_pending_group()
    assert claimed2["group_index"] == 1
    # No more pending
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
    # Mark first done
    g = db.claim_next_pending_group()
    db.mark_group_done(g["id"])
    assert db.all_groups_done("sess-1") is False
    # Mark second done
    g2 = db.claim_next_pending_group()
    db.mark_group_done(g2["id"])
    assert db.all_groups_done("sess-1") is True
```

- [ ] **Step 3: Write tests for sop_feedbacks and sop_revisions**

Add to `tests/test_server_db.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_server_db.py -v -k "session or group or feedback or revision" 2>&1 | tail -20`

Expected: All new tests FAIL (functions don't exist yet).

- [ ] **Step 5: Implement schema changes in `server/db.py`**

Add new table DDL after the existing `SCHEMA` constant (line ~97). In `init_db()` (line ~120), execute the new DDL and run idempotent ALTER TABLE statements.

```python
# After line ~97, add:
SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    first_frame_at TEXT NOT NULL,
    last_frame_at TEXT NOT NULL,
    frame_count INTEGER DEFAULT 0,
    finalized_at TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_employee ON sessions(employee_id);
"""

FRAME_GROUPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS frame_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    group_index INTEGER NOT NULL,
    frame_ids_json TEXT NOT NULL DEFAULT '[]',
    primary_application TEXT DEFAULT '',
    analysis_status TEXT DEFAULT 'pending',
    analysis_error TEXT DEFAULT '',
    analysis_attempts INTEGER DEFAULT 0,
    analyzed_at TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(session_id, group_index)
);
CREATE INDEX IF NOT EXISTS idx_frame_groups_status ON frame_groups(analysis_status);
"""

SOP_FEEDBACKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sop_feedbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sop_id INTEGER NOT NULL REFERENCES sops(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    feedback_text TEXT NOT NULL,
    feedback_scope TEXT DEFAULT 'full',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sop_feedbacks_sop ON sop_feedbacks(sop_id);
"""

SOP_REVISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sop_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sop_id INTEGER NOT NULL REFERENCES sops(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    steps_snapshot_json TEXT NOT NULL,
    feedback_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(sop_id, revision)
);
"""
```

In `init_db()`, after the existing `conn.executescript(SCHEMA)` call, add:

```python
    conn.executescript(SESSIONS_SCHEMA)
    conn.executescript(FRAME_GROUPS_SCHEMA)
    conn.executescript(SOP_FEEDBACKS_SCHEMA)
    conn.executescript(SOP_REVISIONS_SCHEMA)

    # Idempotent ALTER TABLE for sops
    for col, default in [
        ("revision", "1"),
        ("source_group_ids_json", "'[]'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE sops ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass  # column already exists

    # Idempotent ALTER TABLE for sop_steps
    for col, default in [
        ("human_description", "''"),
        ("machine_actions", "'[]'"),
        ("revision", "1"),
    ]:
        try:
            conn.execute(f"ALTER TABLE sop_steps ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass

    # Idempotent ALTER TABLE for frames — add window_title_raw
    try:
        conn.execute("ALTER TABLE frames ADD COLUMN window_title_raw TEXT DEFAULT ''")
    except Exception:
        pass
```

- [ ] **Step 6: Implement sessions CRUD functions in `server/db.py`**

Add after the existing session-related functions (after `count_sessions` at line ~280):

```python
def upsert_session(session_id: str, employee_id: str, frame_at: str) -> None:
    """Create or update a session record when a frame is uploaded."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, frame_count FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO sessions "
                "(session_id, employee_id, status, first_frame_at, last_frame_at, "
                " frame_count, created_at, updated_at) "
                "VALUES (?, ?, 'active', ?, ?, 1, ?, ?)",
                (session_id, employee_id, frame_at, frame_at, now, now),
            )
        else:
            conn.execute(
                "UPDATE sessions SET last_frame_at = ?, frame_count = frame_count + 1, "
                "updated_at = ? WHERE session_id = ?",
                (frame_at, now, session_id),
            )


def get_session(session_id: str) -> dict[str, Any] | None:
    """Fetch a session record by session_id."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
    return dict(row) if row else None


def list_idle_sessions(cutoff_iso: str) -> list[dict[str, Any]]:
    """Return active sessions whose last_frame_at is before cutoff_iso."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE status = 'active' AND last_frame_at < ?",
            (cutoff_iso,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_session_status(session_id: str, status: str,
                          finalized_at: str | None = None) -> None:
    """Transition a session to a new status."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        if finalized_at:
            conn.execute(
                "UPDATE sessions SET status = ?, finalized_at = ?, updated_at = ? "
                "WHERE session_id = ?",
                (status, finalized_at, now, session_id),
            )
        else:
            conn.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (status, now, session_id),
            )
```

- [ ] **Step 7: Implement frame_groups CRUD functions in `server/db.py`**

```python
def insert_frame_group(session_id: str, employee_id: str, group_index: int,
                       frame_ids: list[int], primary_application: str = "") -> int:
    """Insert a new frame group for analysis."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO frame_groups "
            "(session_id, employee_id, group_index, frame_ids_json, "
            " primary_application, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, employee_id, group_index,
             json.dumps(frame_ids), primary_application, now),
        )
        return cur.lastrowid


def get_frame_group(group_id: int) -> dict[str, Any] | None:
    """Fetch a single frame group by ID."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM frame_groups WHERE id = ?", (group_id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["frame_ids"] = json.loads(d.pop("frame_ids_json", "[]"))
    return d


def claim_next_pending_group() -> dict[str, Any] | None:
    """Atomically claim the oldest pending frame group for analysis."""
    with connect() as conn:
        row = conn.execute(
            "UPDATE frame_groups SET analysis_status = 'running', "
            "analysis_attempts = analysis_attempts + 1 "
            "WHERE id = (SELECT id FROM frame_groups WHERE analysis_status = 'pending' "
            "ORDER BY id ASC LIMIT 1) RETURNING *",
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["frame_ids"] = json.loads(d.pop("frame_ids_json", "[]"))
    return d


def mark_group_done(group_id: int) -> None:
    """Mark a frame group analysis as complete."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_status = 'done', analyzed_at = ? "
            "WHERE id = ?",
            (now, group_id),
        )


def mark_group_failed(group_id: int, reason: str) -> None:
    """Mark a frame group analysis as failed."""
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_status = 'failed', analysis_error = ? "
            "WHERE id = ?",
            (reason, group_id),
        )


def reset_group_to_pending(group_id: int) -> None:
    """Reset a failed group back to pending for retry."""
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_status = 'pending', analysis_error = '' "
            "WHERE id = ?",
            (group_id,),
        )


def all_groups_done(session_id: str) -> bool:
    """Return True if all frame groups for a session have status='done'."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN analysis_status = 'done' THEN 1 ELSE 0 END) as done_count "
            "FROM frame_groups WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row["total"] > 0 and row["total"] == row["done_count"]


def list_frame_groups(session_id: str) -> list[dict[str, Any]]:
    """Return all frame groups for a session, ordered by group_index."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM frame_groups WHERE session_id = ? ORDER BY group_index",
            (session_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["frame_ids"] = json.loads(d.pop("frame_ids_json", "[]"))
        result.append(d)
    return result
```

- [ ] **Step 8: Implement sop_feedbacks and sop_revisions CRUD in `server/db.py`**

```python
def insert_sop_feedback(sop_id: int, revision: int, user_id: str,
                        feedback_text: str, feedback_scope: str = "full") -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO sop_feedbacks (sop_id, revision, user_id, feedback_text, "
            "feedback_scope, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (sop_id, revision, user_id, feedback_text, feedback_scope, now),
        )
        return cur.lastrowid


def list_sop_feedbacks(sop_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sop_feedbacks WHERE sop_id = ? ORDER BY created_at",
            (sop_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_sop_revision(sop_id: int, revision: int,
                        steps_snapshot_json: str,
                        feedback_id: int | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO sop_revisions (sop_id, revision, steps_snapshot_json, "
            "feedback_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (sop_id, revision, steps_snapshot_json, feedback_id, now),
        )
        return cur.lastrowid


def list_sop_revisions(sop_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sop_revisions WHERE sop_id = ? ORDER BY revision",
            (sop_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_sop_revision(sop_id: int, revision: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sop_revisions WHERE sop_id = ? AND revision = ?",
            (sop_id, revision),
        ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 9: Run all new tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_server_db.py -v -k "session or group or feedback or revision"`

Expected: All PASS.

- [ ] **Step 10: Run full existing test suite to verify no regressions**

Run: `PYTHONPATH=src python3 -m pytest tests/test_server_db.py -v`

Expected: All existing + new tests PASS.

- [ ] **Step 11: Commit**

```bash
git add server/db.py tests/test_server_db.py
git commit -m "feat(db): add sessions, frame_groups, sop_feedbacks, sop_revisions tables + CRUD"
```

---

## Task 2: Upload Endpoint — Upsert Sessions + Status Change

**Files:**
- Modify: `server/frames_router.py`
- Test: `tests/test_frames_router.py`

Change upload to set `analysis_status='uploaded'` (not `'pending'`) and upsert the sessions table.

- [ ] **Step 1: Write test for upload creating session record**

Add to `tests/test_frames_router.py`:

```python
def test_upload_creates_session_record(client_and_db):
    """Upload should upsert a sessions row with status='active'."""
    client, db_path = client_and_db
    import io
    resp = client.post(
        "/frames/upload",
        data={
            "employee_id": "E001",
            "session_id": "sess-abc",
            "frame_index": "0",
            "timestamp": "1700000000.0",
        },
        files={"image": ("test.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100), "image/png")},
    )
    assert resp.status_code == 200
    from server import db
    sess = db.get_session("sess-abc")
    assert sess is not None
    assert sess["status"] == "active"
    assert sess["frame_count"] == 1
```

- [ ] **Step 2: Write test for frame status being 'uploaded' not 'pending'**

```python
def test_upload_sets_status_uploaded(client_and_db):
    """New frames should have analysis_status='uploaded', not 'pending'."""
    client, db_path = client_and_db
    import io
    resp = client.post(
        "/frames/upload",
        data={
            "employee_id": "E001",
            "session_id": "sess-abc",
            "frame_index": "0",
            "timestamp": "1700000000.0",
        },
        files={"image": ("test.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100), "image/png")},
    )
    assert resp.status_code == 200
    frame_id = resp.json()["id"]
    from server import db
    frame = db.get_frame(frame_id)
    assert frame["analysis_status"] == "uploaded"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_frames_router.py -v -k "session_record or status_uploaded" 2>&1 | tail -10`

Expected: FAIL.

- [ ] **Step 4: Modify `server/frames_router.py` upload endpoint**

In the `upload_frame` function (line 35), add `window_title` optional Form param, call `db.upsert_session()`, and add optional `window_title_raw` to form params. Also modify `db.insert_pending_frame` call — we need to change the function name or add a status param.

At line 35, add `window_title_raw` parameter:

```python
@router.post("/frames/upload")
def upload_frame(
    employee_id: str = Form(...),
    session_id: str = Form(...),
    frame_index: int = Form(...),
    timestamp: float = Form(...),
    cursor_x: int = Form(-1),
    cursor_y: int = Form(-1),
    focus_rect: str = Form(""),
    window_title_raw: str = Form(""),  # NEW: raw window title from client OS
    image: UploadFile = File(...),
    _auth=Depends(require_upload_key),
):
```

After `save_image()` call and before `db.insert_pending_frame()`, add session upsert:

```python
    received_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # ... save_image call ...

    # Upsert session tracking
    db.upsert_session(
        session_id=session_id,
        employee_id=employee_id,
        frame_at=received_at,
    )
```

Change `db.insert_pending_frame` to use `analysis_status='uploaded'`. Modify the `insert_pending_frame` function in `db.py` to accept an optional `analysis_status` parameter defaulting to `'uploaded'`:

In `server/db.py`, at `insert_pending_frame` (line ~856), change the default:

```python
def insert_pending_frame(employee_id: str, session_id: str, frame_index: int,
                         timestamp: float, image_path: str,
                         cursor_x: int = -1, cursor_y: int = -1,
                         focus_rect: list[int] | None = None,
                         window_title_raw: str = "",
                         analysis_status: str = "uploaded") -> int | None:
```

And in the INSERT SQL, replace the hardcoded `'pending'` with the parameter, and include `window_title_raw`.

In `frames_router.py`, pass `window_title_raw`:

```python
    row_id = db.insert_pending_frame(
        employee_id=employee_id,
        session_id=session_id,
        frame_index=frame_index,
        timestamp=timestamp,
        image_path=str(saved_path),
        cursor_x=int(cursor_x),
        cursor_y=int(cursor_y),
        focus_rect=focus_rect_list,
        window_title_raw=window_title_raw,
    )
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_frames_router.py -v -k "session_record or status_uploaded"`

Expected: PASS.

- [ ] **Step 6: Run full test suite to check regressions**

Run: `PYTHONPATH=src python3 -m pytest tests/ -v --timeout=30 2>&1 | tail -20`

Expected: All PASS (existing tests that check `analysis_status='pending'` may need updating to `'uploaded'`).

- [ ] **Step 7: Commit**

```bash
git add server/frames_router.py server/db.py tests/test_frames_router.py
git commit -m "feat(upload): upsert sessions table on frame upload, set status='uploaded'"
```

---

## Task 3: FrameGrouper Algorithm

**Files:**
- Create: `server/frame_grouper.py`
- Test: `tests/test_frame_grouper.py`

Pure-Python module. No LLM, no DB dependency — operates on a list of frame dicts.

- [ ] **Step 1: Write tests for FrameGrouper**

Create `tests/test_frame_grouper.py`:

```python
"""Tests for server/frame_grouper.py — lightweight frame clustering."""

from __future__ import annotations

import pytest


def _frame(idx: int, *, app: str = "chrome", ts: float = 0.0,
           cx: int = 100, cy: int = 100, image_path: str = "") -> dict:
    """Build a minimal frame dict for grouper input."""
    return {
        "id": idx,
        "frame_index": idx,
        "window_title_raw": app,
        "recorded_at": f"2026-04-15T10:00:{ts:05.2f}+00:00",
        "timestamp": 1000.0 + ts,
        "cursor_x": cx,
        "cursor_y": cy,
        "focus_rect": None,
        "image_path": image_path,
    }


class TestBoundaryDetection:
    def test_app_switch_creates_boundary(self):
        from server.frame_grouper import find_boundaries
        frames = [
            _frame(0, app="chrome", ts=0),
            _frame(1, app="chrome", ts=1),
            _frame(2, app="excel", ts=2),
            _frame(3, app="excel", ts=3),
        ]
        boundaries = find_boundaries(frames, use_phash=False)
        assert 2 in boundaries

    def test_time_gap_creates_boundary(self):
        from server.frame_grouper import find_boundaries
        frames = [
            _frame(0, ts=0),
            _frame(1, ts=1),
            _frame(2, ts=2),
            _frame(3, ts=30),  # huge gap
            _frame(4, ts=31),
        ]
        boundaries = find_boundaries(frames, use_phash=False)
        assert 3 in boundaries

    def test_cursor_jump_creates_boundary(self):
        from server.frame_grouper import find_boundaries
        # Screen diagonal ~ 2203 for 1920x1080
        # 30% threshold = 661 pixels
        frames = [
            _frame(0, cx=100, cy=100, ts=0),
            _frame(1, cx=110, cy=110, ts=1),
            _frame(2, cx=1500, cy=900, ts=2),  # big jump
            _frame(3, cx=1510, cy=910, ts=3),
        ]
        boundaries = find_boundaries(
            frames, use_phash=False,
            screen_width=1920, screen_height=1080,
        )
        assert 2 in boundaries

    def test_no_boundary_for_similar_frames(self):
        from server.frame_grouper import find_boundaries
        frames = [_frame(i, ts=float(i)) for i in range(5)]
        boundaries = find_boundaries(frames, use_phash=False)
        assert len(boundaries) == 0


class TestGroupSplitting:
    def test_split_with_overlap(self):
        from server.frame_grouper import split_with_overlap
        frame_ids = [0, 1, 2, 3, 4, 5, 6, 7]
        boundaries = [4]
        groups = split_with_overlap(frame_ids, boundaries, overlap=3)
        # Group 0: [0,1,2,3] + 3 overlap into next = [0,1,2,3,4,5,6]
        # Group 1: 3 overlap from prev + [4,5,6,7] = [1,2,3,4,5,6,7]
        assert len(groups) == 2
        assert groups[0] == [0, 1, 2, 3, 4, 5, 6]
        assert groups[1] == [1, 2, 3, 4, 5, 6, 7]

    def test_small_group_merged(self):
        from server.frame_grouper import split_with_overlap
        frame_ids = [0, 1, 2, 3, 4]
        boundaries = [4]  # second group has only 1 frame (< min_group_size=2)
        groups = split_with_overlap(frame_ids, boundaries, overlap=3,
                                     min_group_size=2)
        # Too small, merged into one group
        assert len(groups) == 1
        assert groups[0] == [0, 1, 2, 3, 4]

    def test_no_boundaries_single_group(self):
        from server.frame_grouper import split_with_overlap
        frame_ids = [0, 1, 2, 3]
        groups = split_with_overlap(frame_ids, [], overlap=3)
        assert len(groups) == 1
        assert groups[0] == [0, 1, 2, 3]


class TestGroupFrames:
    def test_end_to_end_grouping(self):
        from server.frame_grouper import group_frames
        frames = [
            _frame(0, app="chrome", ts=0),
            _frame(1, app="chrome", ts=1),
            _frame(2, app="chrome", ts=2),
            _frame(3, app="excel", ts=3),
            _frame(4, app="excel", ts=4),
            _frame(5, app="excel", ts=5),
        ]
        groups = group_frames(frames, use_phash=False)
        assert len(groups) == 2
        assert groups[0].primary_application == "chrome"
        assert groups[1].primary_application == "excel"
        # Overlap: group 0 should include some of group 1's frames
        assert any(fid in groups[0].frame_ids for fid in [3, 4, 5])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_frame_grouper.py -v 2>&1 | tail -10`

Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement `server/frame_grouper.py`**

```python
"""Lightweight frame grouping by logical action boundaries.

Groups frames using four signals (priority order):
P1 — Application switch (window_title_raw)
P2 — Image similarity (perceptual hash)
P3 — Time gap (> 3x median interval)
P4 — Cursor/focus jump (> 30% screen diagonal)

Groups overlap by ±N frames at boundaries to preserve context.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FrameGroup:
    group_index: int
    frame_ids: list[int] = field(default_factory=list)
    primary_application: str = ""


# Defaults — overridable via env vars in caller
PHASH_THRESHOLD = 12
TIME_GAP_MULTIPLIER = 3
CURSOR_JUMP_RATIO = 0.3
OVERLAP_FRAMES = 3
MIN_GROUP_SIZE = 2


def find_boundaries(
    frames: list[dict[str, Any]],
    *,
    use_phash: bool = True,
    phash_threshold: int = PHASH_THRESHOLD,
    time_gap_multiplier: float = TIME_GAP_MULTIPLIER,
    cursor_jump_ratio: float = CURSOR_JUMP_RATIO,
    screen_width: int = 0,
    screen_height: int = 0,
) -> set[int]:
    """Return set of boundary indices where a new group should start."""
    if len(frames) < 2:
        return set()

    boundaries: set[int] = set()

    # P1: Application switch
    for i in range(1, len(frames)):
        prev_app = (frames[i - 1].get("window_title_raw") or "").strip()
        curr_app = (frames[i].get("window_title_raw") or "").strip()
        if prev_app and curr_app and prev_app != curr_app:
            boundaries.add(i)

    # P2: Image similarity (perceptual hash)
    if use_phash:
        hashes = _compute_phashes(frames)
        if hashes:
            for i in range(1, len(frames)):
                if hashes[i] is not None and hashes[i - 1] is not None:
                    dist = hashes[i] - hashes[i - 1]
                    if dist > phash_threshold:
                        boundaries.add(i)

    # P3: Time gap
    timestamps = [f.get("timestamp", 0.0) for f in frames]
    intervals = [timestamps[i] - timestamps[i - 1]
                 for i in range(1, len(timestamps))
                 if timestamps[i] > timestamps[i - 1]]
    if intervals:
        median_interval = statistics.median(intervals)
        threshold = median_interval * time_gap_multiplier
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            if gap > threshold:
                boundaries.add(i)

    # P4: Cursor jump
    if screen_width <= 0 or screen_height <= 0:
        # Estimate from a reasonable default
        screen_width = screen_width or 1920
        screen_height = screen_height or 1080
    diagonal = math.sqrt(screen_width ** 2 + screen_height ** 2)
    jump_threshold = diagonal * cursor_jump_ratio

    for i in range(1, len(frames)):
        cx1 = frames[i - 1].get("cursor_x", -1)
        cy1 = frames[i - 1].get("cursor_y", -1)
        cx2 = frames[i].get("cursor_x", -1)
        cy2 = frames[i].get("cursor_y", -1)
        if cx1 >= 0 and cy1 >= 0 and cx2 >= 0 and cy2 >= 0:
            dist = math.sqrt((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2)
            if dist > jump_threshold:
                boundaries.add(i)

    return boundaries


def split_with_overlap(
    frame_ids: list[int],
    boundaries: list[int],
    overlap: int = OVERLAP_FRAMES,
    min_group_size: int = MIN_GROUP_SIZE,
) -> list[list[int]]:
    """Split frame_ids at boundary indices with ±overlap frames."""
    if not boundaries:
        return [list(frame_ids)]

    boundaries = sorted(boundaries)
    n = len(frame_ids)

    # Build raw segments
    segments: list[tuple[int, int]] = []  # (start, end) exclusive
    prev = 0
    for b in boundaries:
        if b > prev:
            segments.append((prev, b))
        prev = b
    if prev < n:
        segments.append((prev, n))

    # Merge small segments
    merged: list[tuple[int, int]] = []
    for seg in segments:
        seg_size = seg[1] - seg[0]
        if merged and seg_size < min_group_size:
            # Merge with previous
            prev_start, _ = merged[-1]
            merged[-1] = (prev_start, seg[1])
        elif not merged and seg_size < min_group_size and len(segments) > 1:
            # Will be merged with next in the next iteration
            merged.append(seg)
        else:
            if merged and (merged[-1][1] - merged[-1][0]) < min_group_size:
                # Previous was too small, merge it with current
                prev_start, _ = merged[-1]
                merged[-1] = (prev_start, seg[1])
            else:
                merged.append(seg)

    # Apply overlap
    groups: list[list[int]] = []
    for start, end in merged:
        overlap_start = max(0, start - overlap)
        overlap_end = min(n, end + overlap)
        groups.append(frame_ids[overlap_start:overlap_end])

    return groups


def _compute_phashes(frames: list[dict]) -> list[Any]:
    """Compute perceptual hashes for frames that have image_path."""
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        return []

    hashes = []
    for f in frames:
        path = f.get("image_path", "")
        if path and Path(path).is_file():
            try:
                img = Image.open(path)
                hashes.append(imagehash.phash(img))
            except Exception:
                hashes.append(None)
        else:
            hashes.append(None)
    return hashes


def _dominant_app(frames: list[dict], frame_ids: list[int]) -> str:
    """Find the most common window_title_raw among the given frame IDs."""
    id_to_frame = {f["id"]: f for f in frames}
    apps = [
        (id_to_frame[fid].get("window_title_raw") or "").strip()
        for fid in frame_ids
        if fid in id_to_frame
    ]
    apps = [a for a in apps if a]
    if not apps:
        return ""
    return Counter(apps).most_common(1)[0][0]


def group_frames(
    frames: list[dict[str, Any]],
    *,
    use_phash: bool = True,
    overlap: int = OVERLAP_FRAMES,
    min_group_size: int = MIN_GROUP_SIZE,
    screen_width: int = 0,
    screen_height: int = 0,
) -> list[FrameGroup]:
    """Main entry point: group a session's frames into logical action groups."""
    if not frames:
        return []

    # Ensure sorted by frame_index
    frames = sorted(frames, key=lambda f: f.get("frame_index", 0))

    boundaries = find_boundaries(
        frames,
        use_phash=use_phash,
        screen_width=screen_width,
        screen_height=screen_height,
    )

    frame_ids = [f["id"] for f in frames]
    id_lists = split_with_overlap(
        frame_ids, sorted(boundaries),
        overlap=overlap, min_group_size=min_group_size,
    )

    groups = []
    for i, fids in enumerate(id_lists):
        groups.append(FrameGroup(
            group_index=i,
            frame_ids=fids,
            primary_application=_dominant_app(frames, fids),
        ))

    return groups
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_frame_grouper.py -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add server/frame_grouper.py tests/test_frame_grouper.py
git commit -m "feat: FrameGrouper algorithm with P1-P4 boundary detection + overlap"
```

---

## Task 4: SessionFinalizer Daemon Thread

**Files:**
- Create: `server/session_finalizer.py`
- Test: `tests/test_session_finalizer.py`
- Modify: `server/app.py`

- [ ] **Step 1: Write tests for SessionFinalizer**

Create `tests/test_session_finalizer.py`:

```python
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
    # Update last_frame_at to match
    for i in range(1, count):
        db.upsert_session(session_id, employee_id, start_ts)


def test_finalize_idle_session(fresh_db, monkeypatch):
    """SessionFinalizer should group frames for idle sessions."""
    from server.session_finalizer import SessionFinalizer

    _insert_frames("sess-1", "E001", 6)

    # Patch FrameGrouper to avoid needing real images
    from server import frame_grouper
    from server.frame_grouper import FrameGroup
    monkeypatch.setattr(
        frame_grouper, "group_frames",
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
        idle_timeout=0,  # everything is "idle"
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

    # Should still be 'analyzed', not touched
    sess = db.get_session("sess-1")
    assert sess["status"] == "analyzed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_session_finalizer.py -v 2>&1 | tail -10`

Expected: FAIL.

- [ ] **Step 3: Implement `server/session_finalizer.py`**

```python
"""SessionFinalizer — daemon thread that detects idle sessions and triggers grouping.

Polls the sessions table for sessions in 'active' status whose last_frame_at
is older than SESSION_IDLE_TIMEOUT_SECONDS. For each such session:
1. Set status → 'finalizing'
2. Load all frames, run FrameGrouper
3. Insert frame_groups records
4. Set status → 'grouped'
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone, timedelta

import structlog

from server import db
from server.frame_grouper import group_frames

log = structlog.get_logger()

SESSION_IDLE_TIMEOUT_SECONDS = int(os.environ.get("SESSION_IDLE_TIMEOUT", "300"))
FINALIZER_POLL_INTERVAL_SECONDS = int(os.environ.get("FINALIZER_POLL_INTERVAL", "60"))


class SessionFinalizer:
    """Background thread that finalizes idle recording sessions."""

    def __init__(
        self,
        stop_event: threading.Event,
        idle_timeout: int = SESSION_IDLE_TIMEOUT_SECONDS,
        poll_interval: float = FINALIZER_POLL_INTERVAL_SECONDS,
    ):
        self._stop = stop_event
        self._idle_timeout = idle_timeout
        self._poll_interval = poll_interval

    def run(self) -> None:
        log.info("session_finalizer_started",
                 idle_timeout=self._idle_timeout,
                 poll_interval=self._poll_interval)
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                log.exception("session_finalizer_error")
            self._stop.wait(timeout=self._poll_interval)
        log.info("session_finalizer_stopped")

    def _poll_once(self) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self._idle_timeout)
        ).isoformat(timespec="seconds")
        idle_sessions = db.list_idle_sessions(cutoff_iso=cutoff)

        for sess in idle_sessions:
            session_id = sess["session_id"]
            try:
                self._finalize_session(sess)
            except Exception:
                log.exception("session_finalize_failed", session_id=session_id)
                db.update_session_status(session_id, "failed")

    def _finalize_session(self, sess: dict) -> None:
        session_id = sess["session_id"]
        employee_id = sess["employee_id"]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        log.info("session_finalizing", session_id=session_id)
        db.update_session_status(session_id, "finalizing", finalized_at=now)

        # Load all frames for this session
        frames = db.query_frames(session_id=session_id, limit=100_000)
        if not frames:
            db.update_session_status(session_id, "failed")
            return

        # Sort by frame_index ASC (query_frames returns DESC)
        frames.sort(key=lambda f: f.get("frame_index", 0))

        # Run grouper (disable phash if images might not exist in test)
        groups = group_frames(frames, use_phash=True)

        # Insert frame_groups
        for g in groups:
            db.insert_frame_group(
                session_id=session_id,
                employee_id=employee_id,
                group_index=g.group_index,
                frame_ids=g.frame_ids,
                primary_application=g.primary_application,
            )

        db.update_session_status(session_id, "grouped")
        log.info("session_grouped", session_id=session_id, group_count=len(groups))
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_session_finalizer.py -v`

Expected: All PASS.

- [ ] **Step 5: Wire SessionFinalizer into `server/app.py`**

In `server/app.py`, modify `_startup()` (line ~108) to also start the SessionFinalizer:

```python
_analysis_pool = None
_session_finalizer_thread = None  # NEW


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # Seed default admin if no users exist yet
    if not db.list_users(limit=1):
        from server.auth import hash_password
        db.insert_user(
            username="admin",
            password_hash=hash_password("admin"),
            display_name="System Admin",
            role="admin",
        )

    if os.environ.get("WORKFLOW_DISABLE_ANALYSIS_POOL"):
        return

    # Start analysis pool
    global _analysis_pool
    from server.analysis_pool import AnalysisPool
    from server.api_keys import load_api_keys
    keys = load_api_keys()
    _analysis_pool = AnalysisPool(keys=keys)
    _analysis_pool.start()

    # Start session finalizer
    if not os.environ.get("WORKFLOW_DISABLE_SESSION_FINALIZER"):
        global _session_finalizer_thread
        import threading
        from server.session_finalizer import SessionFinalizer
        stop_event = _analysis_pool._stop  # share the same stop event
        finalizer = SessionFinalizer(stop_event=stop_event)
        _session_finalizer_thread = threading.Thread(
            target=finalizer.run, daemon=True, name="session-finalizer",
        )
        _session_finalizer_thread.start()
```

In `_shutdown()`:

```python
@app.on_event("shutdown")
def _shutdown() -> None:
    global _analysis_pool, _session_finalizer_thread
    if _analysis_pool is not None:
        _analysis_pool.stop(timeout=30.0)
        _analysis_pool = None
    if _session_finalizer_thread is not None:
        _session_finalizer_thread.join(timeout=5.0)
        _session_finalizer_thread = None
```

- [ ] **Step 6: Commit**

```bash
git add server/session_finalizer.py tests/test_session_finalizer.py server/app.py
git commit -m "feat: SessionFinalizer daemon thread detects idle sessions and triggers grouping"
```

---

## Task 5: Group-Level Analysis — AnalysisPool Rework

**Files:**
- Create: `server/group_analysis.py`
- Modify: `server/analysis_pool.py`
- Test: `tests/test_analysis_pool.py` (update existing + add new)

- [ ] **Step 1: Create `server/group_analysis.py` with prompts and response parsing**

```python
"""Group-level vision analysis: multi-image prompt construction and response parsing."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GROUP_SYSTEM_PROMPT = """You are a workflow SOP extraction expert. You will receive a sequence of screenshots captured over time, along with mouse cursor coordinates and focus region data for each frame.

Your task: identify the discrete user actions and produce reproducible SOP steps.

For each step, output:
{
    "step_order": <int>,
    "title": "<short action title>",
    "human_description": "<detailed description a human can follow to reproduce this action, including specific UI elements, their locations, and what to look for>",
    "machine_actions": [
        {
            "type": "click|double_click|right_click|type|key|scroll|drag",
            "x": <pixel x>,
            "y": <pixel y>,
            "target": "<UI element name>",
            "text": "<for type actions>",
            "key": "<for key actions, e.g. Enter, Ctrl+S>"
        }
    ],
    "application": "<application name>",
    "key_frame_indices": [<indices within this group that best represent this step>]
}

Return a JSON object: {"steps": [...]}

Guidelines:
- One step = one logical user action (may span multiple frames)
- Include precise coordinates from the cursor data provided
- human_description should be detailed enough for someone unfamiliar with the workflow
- machine_actions should be precise enough for RPA replay
- key_frame_indices reference the 0-based index within the provided image sequence"""

REFINE_SYSTEM_PROMPT = """You are an SOP refinement assistant. You will receive:
1. The original screenshot sequence from a workflow recording
2. The current SOP steps (which you or a previous version generated)
3. User feedback requesting specific changes

Revise the SOP steps according to the feedback. Maintain the same output format as the original generation. Only change what the feedback requests — preserve steps that are not mentioned.

Return a JSON object: {"steps": [...]}"""


@dataclass
class GroupAnalysisInput:
    """Everything needed to analyze a frame group."""
    group_id: int
    session_id: str
    frames: list[dict[str, Any]]  # ordered by frame_index


def build_user_prompt(frames: list[dict[str, Any]]) -> str:
    """Build the text portion of the user message."""
    lines = [f"Here are {len(frames)} sequential screenshots from a recording session.",
             "For each frame I provide: timestamp, cursor position (x, y), and focus region [x1, y1, x2, y2] if available.",
             "",
             "Frame data:"]
    for i, f in enumerate(frames):
        cx = f.get("cursor_x", -1)
        cy = f.get("cursor_y", -1)
        fr = f.get("focus_rect") or "none"
        ts = f.get("recorded_at", "unknown")
        lines.append(f"- Frame {i}: timestamp={ts}, cursor=({cx}, {cy}), focus_rect={fr}")
    lines.append("")
    lines.append("Please extract the SOP steps from this image sequence.")
    return "\n".join(lines)


def build_refine_user_prompt(current_steps_json: str, feedback_text: str,
                              feedback_scope: str,
                              frames: list[dict[str, Any]]) -> str:
    """Build prompt for SOP refinement."""
    lines = [
        "Current SOP steps:",
        current_steps_json,
        "",
        f"User feedback (scope: {feedback_scope}):",
        f'"{feedback_text}"',
        "",
        f"Original recording has {len(frames)} frames (images attached).",
        "",
        "Please output the revised steps.",
    ]
    return "\n".join(lines)


def build_image_content_blocks(frames: list[dict[str, Any]]) -> list[dict]:
    """Build OpenAI-format image content blocks from frame image paths."""
    blocks: list[dict] = []
    for f in frames:
        path = Path(f.get("image_path", ""))
        if not path.is_file():
            continue
        data = path.read_bytes()
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return blocks


def parse_steps_response(raw_text: str) -> list[dict[str, Any]]:
    """Parse LLM response into a list of step dicts."""
    text = raw_text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting outermost JSON object
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
        else:
            return []

    if isinstance(parsed, dict) and "steps" in parsed:
        return parsed["steps"]
    if isinstance(parsed, list):
        return parsed
    return []
```

- [ ] **Step 2: Write tests for group_analysis**

Create `tests/test_group_analysis.py`:

```python
"""Tests for server/group_analysis.py."""

from server.group_analysis import (
    build_user_prompt,
    build_refine_user_prompt,
    parse_steps_response,
)


def test_build_user_prompt():
    frames = [
        {"cursor_x": 100, "cursor_y": 200, "focus_rect": [10, 20, 30, 40],
         "recorded_at": "2026-04-15T10:00:00"},
        {"cursor_x": 300, "cursor_y": 400, "focus_rect": None,
         "recorded_at": "2026-04-15T10:00:03"},
    ]
    prompt = build_user_prompt(frames)
    assert "2 sequential screenshots" in prompt
    assert "cursor=(100, 200)" in prompt
    assert "focus_rect=[10, 20, 30, 40]" in prompt
    assert "focus_rect=none" in prompt


def test_build_refine_user_prompt():
    prompt = build_refine_user_prompt(
        current_steps_json='[{"step_order": 1}]',
        feedback_text="Add more detail to step 1",
        feedback_scope="step:1",
        frames=[{}, {}],
    )
    assert "step:1" in prompt
    assert "Add more detail" in prompt
    assert "2 frames" in prompt


def test_parse_steps_response_json():
    raw = '{"steps": [{"step_order": 1, "title": "Open app"}]}'
    steps = parse_steps_response(raw)
    assert len(steps) == 1
    assert steps[0]["title"] == "Open app"


def test_parse_steps_response_markdown_fenced():
    raw = '```json\n{"steps": [{"step_order": 1, "title": "Click"}]}\n```'
    steps = parse_steps_response(raw)
    assert len(steps) == 1
    assert steps[0]["title"] == "Click"


def test_parse_steps_response_embedded_json():
    raw = 'Here is the result: {"steps": [{"step_order": 1}]} end.'
    steps = parse_steps_response(raw)
    assert len(steps) == 1
```

- [ ] **Step 3: Run tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_group_analysis.py -v`

Expected: All PASS.

- [ ] **Step 4: Modify `server/analysis_pool.py` to work with groups**

Replace the per-frame worker logic with per-group logic. The AnalysisWorker now:
1. Calls `db.claim_next_pending_group()` instead of `claim_next_pending_frame()`
2. Loads all frame images for the group
3. Makes a single multi-image API call
4. Writes SOP steps to DB
5. Checks if all groups are done → auto-creates SOP

Full replacement of `server/analysis_pool.py`:

```python
"""AnalysisPool — per-API-key worker threads for group-level frame analysis.

Each worker thread:
1. Claims the next pending frame_group (atomic UPDATE...RETURNING)
2. Loads all frame images + metadata for the group
3. Calls the vision API with multi-image prompt
4. Parses response into SOP steps
5. Writes steps to DB, marks group as done
6. When all groups for a session are done, auto-creates SOP
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from server import db

log = structlog.get_logger()

EMPTY_QUEUE_POLL_INTERVAL_SECONDS = 2.0
MAX_ANALYSIS_ATTEMPTS = 3


class AnalysisWorker:
    """One worker per API key — processes frame groups."""

    def __init__(self, key: str, key_index: int, stop_event: threading.Event,
                 vision_client: Any = None):
        self.key = key
        self.label = f"worker-{key_index}"
        self._stop = stop_event
        self._client = vision_client

    def _build_client(self):
        """Build OpenAI client for multi-image calls."""
        from openai import OpenAI
        return OpenAI(
            api_key=self.key,
            base_url="https://coding.dashscope.aliyuncs.com/v1",
        )

    def run(self) -> None:
        log.info("analysis_worker_started", label=self.label)
        client = self._client or self._build_client()

        while not self._stop.is_set():
            group = db.claim_next_pending_group()
            if group is None:
                self._stop.wait(timeout=EMPTY_QUEUE_POLL_INTERVAL_SECONDS)
                continue
            try:
                self._analyze_group(client, group)
            except Exception as exc:
                log.exception("group_analysis_error",
                              group_id=group["id"], label=self.label)
                self._handle_failure(group["id"], group["analysis_attempts"],
                                     str(exc))

        log.info("analysis_worker_stopped", label=self.label)

    def _analyze_group(self, client: Any, group: dict) -> None:
        from server.group_analysis import (
            GROUP_SYSTEM_PROMPT, build_user_prompt,
            build_image_content_blocks, parse_steps_response,
        )

        group_id = group["id"]
        session_id = group["session_id"]
        frame_ids = group["frame_ids"]

        # Load frames ordered by frame_index
        frames = []
        for fid in frame_ids:
            f = db.get_frame(fid)
            if f:
                frames.append(f)
        frames.sort(key=lambda f: f.get("frame_index", 0))

        if not frames:
            db.mark_group_failed(group_id, "no frames found")
            return

        # Build multi-image prompt
        user_text = build_user_prompt(frames)
        image_blocks = build_image_content_blocks(frames)

        if not image_blocks:
            db.mark_group_failed(group_id, "no valid images")
            return

        content: list[dict] = [{"type": "text", "text": user_text}] + image_blocks

        model = os.environ.get("ANALYSIS_MODEL", "qwen3.6-plus")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": GROUP_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            max_tokens=4000,
            temperature=0.1,
        )

        raw_text = response.choices[0].message.content or ""
        steps = parse_steps_response(raw_text)

        if not steps:
            db.mark_group_failed(group_id, "empty steps from LLM")
            return

        # Write steps linked to the session (SOP created after all groups done)
        # Store steps temporarily in group metadata for later SOP assembly
        db.mark_group_done(group_id)

        # Store parsed steps as group result for SOP assembly
        _store_group_steps(session_id, group["group_index"], steps)

        # Check if all groups are done
        if db.all_groups_done(session_id):
            _auto_create_sop(session_id, group["employee_id"])

    def _handle_failure(self, group_id: int, attempts: int, reason: str) -> None:
        if attempts >= MAX_ANALYSIS_ATTEMPTS:
            db.mark_group_failed(group_id, reason)
            log.warning("group_analysis_permanently_failed",
                        group_id=group_id, reason=reason)
        else:
            db.reset_group_to_pending(group_id)
            log.info("group_analysis_retry",
                     group_id=group_id, attempts=attempts)


def _store_group_steps(session_id: str, group_index: int,
                       steps: list[dict]) -> None:
    """Persist group analysis steps for later SOP assembly.

    We store them as a JSON blob on the frame_groups row (via analysis_error
    field repurposed, or better: a dedicated column). For simplicity, we add
    a helper that stores to a temp cache keyed by (session_id, group_index).
    """
    # Use a simple approach: store in frame_groups table via a new helper
    db.store_group_analysis_result(session_id, group_index, steps)


def _auto_create_sop(session_id: str, employee_id: str) -> None:
    """Create a draft SOP from all completed group analyses."""
    log.info("auto_creating_sop", session_id=session_id)
    db.update_session_status(session_id, "analyzed")

    groups = db.list_frame_groups(session_id)
    all_steps: list[dict] = []
    group_ids: list[int] = []

    for g in groups:
        group_ids.append(g["id"])
        result = db.get_group_analysis_result(session_id, g["group_index"])
        if result:
            all_steps.extend(result)

    # Renumber step_order sequentially
    for i, step in enumerate(all_steps):
        step["step_order"] = i + 1

    # Create SOP
    sop_id = db.insert_sop(
        title=f"SOP - {employee_id} / {session_id[:8]}",
        created_by="system",
        source_session_id=session_id,
        source_employee_id=employee_id,
    )
    # Update with source_group_ids and revision
    db.update_sop_group_ids(sop_id, group_ids)

    # Insert steps
    for step in all_steps:
        db.insert_sop_step(
            sop_id=sop_id,
            step_order=step.get("step_order", 0),
            title=step.get("title", ""),
            description=step.get("human_description", ""),
            application=step.get("application", ""),
            action_type=step.get("machine_actions", [{}])[0].get("type", "")
                if step.get("machine_actions") else "",
            action_detail=step.get("machine_actions", []),
            source_frame_ids=step.get("key_frame_indices", []),
            confidence=0.0,
            human_description=step.get("human_description", ""),
            machine_actions=step.get("machine_actions", []),
        )

    log.info("sop_auto_created", sop_id=sop_id, step_count=len(all_steps))


class AnalysisPool:
    """Manages a pool of analysis worker threads, one per API key."""

    def __init__(self, keys: list[str], worker_factory=None):
        self._keys = keys
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._worker_factory = worker_factory

    def start(self) -> None:
        for i, key in enumerate(self._keys):
            if self._worker_factory:
                worker = self._worker_factory(key, i, self._stop)
            else:
                worker = AnalysisWorker(key, i, self._stop)
            t = threading.Thread(target=worker.run, daemon=True,
                                 name=f"analysis-worker-{i}")
            t.start()
            self._threads.append(t)
        log.info("analysis_pool_started", worker_count=len(self._threads))

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout / max(len(self._threads), 1))
        self._threads.clear()
        log.info("analysis_pool_stopped", worker_count=len(self._keys))
```

- [ ] **Step 5: Add helper DB functions for group analysis results**

In `server/db.py`, add:

```python
def store_group_analysis_result(session_id: str, group_index: int,
                                 steps: list[dict]) -> None:
    """Store parsed SOP steps from group analysis as JSON on the group row."""
    import json as _json
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_error = ? "
            "WHERE session_id = ? AND group_index = ?",
            (_json.dumps(steps, ensure_ascii=False), session_id, group_index),
        )


def get_group_analysis_result(session_id: str, group_index: int) -> list[dict] | None:
    """Retrieve parsed SOP steps stored on a group row."""
    import json as _json
    with connect() as conn:
        row = conn.execute(
            "SELECT analysis_error FROM frame_groups "
            "WHERE session_id = ? AND group_index = ? AND analysis_status = 'done'",
            (session_id, group_index),
        ).fetchone()
    if row is None or not row["analysis_error"]:
        return None
    try:
        return _json.loads(row["analysis_error"])
    except (ValueError, TypeError):
        return None


def update_sop_group_ids(sop_id: int, group_ids: list[int]) -> None:
    """Set source_group_ids_json on a SOP."""
    import json as _json
    with connect() as conn:
        conn.execute(
            "UPDATE sops SET source_group_ids_json = ? WHERE id = ?",
            (_json.dumps(group_ids), sop_id),
        )
```

Also update `insert_sop_step` signature (line ~541) to accept the new fields:

```python
def insert_sop_step(sop_id: int, step_order: int, title: str,
                    description: str = "", application: str = "",
                    action_type: str = "", action_detail: dict | list | None = None,
                    screenshot_ref: str = "",
                    source_frame_ids: list[int] | None = None,
                    confidence: float = 0.0,
                    human_description: str = "",
                    machine_actions: list | None = None) -> int:
```

Add to the INSERT SQL: `human_description`, `machine_actions` (as JSON), `revision` (default 1).

- [ ] **Step 6: Run tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_analysis_pool.py tests/test_group_analysis.py -v`

Expected: All PASS (existing analysis_pool tests may need adaptation for the new claim_next_pending_group interface).

- [ ] **Step 7: Commit**

```bash
git add server/group_analysis.py server/analysis_pool.py server/db.py tests/test_group_analysis.py tests/test_analysis_pool.py
git commit -m "feat: group-level analysis with multi-image vision API + auto SOP creation"
```

---

## Task 6: SOP Feedback & Revision API

**Files:**
- Create: `server/sop_feedback_router.py`
- Modify: `server/app.py` (register router)
- Modify: `server/sops_router.py` (add status transitions for 'regenerating')
- Test: `tests/test_sop_feedback_api.py`

- [ ] **Step 1: Write tests for feedback API**

Create `tests/test_sop_feedback_api.py`:

```python
"""Tests for SOP feedback / revision / regeneration API."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from server import db
from server.app import app


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_DISABLE_ANALYSIS_POOL", "1")
    monkeypatch.setenv("WORKFLOW_DISABLE_SESSION_FINALIZER", "1")
    db.init_db()
    from server.auth import hash_password
    db.insert_user(username="admin", password_hash=hash_password("test"),
                   display_name="Admin", role="admin")
    return tmp_path


@pytest.fixture
def authed_client(fresh_db):
    client = TestClient(app)
    resp = client.post("/api/auth/login",
                       json={"username": "admin", "password": "test"})
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def _create_sop_with_steps(client) -> int:
    resp = client.post("/api/sops/", json={"title": "Test SOP"})
    sop_id = resp.json()["id"]
    client.post(f"/api/sops/{sop_id}/steps/",
                json={"title": "Step 1", "description": "Do thing 1"})
    client.post(f"/api/sops/{sop_id}/steps/",
                json={"title": "Step 2", "description": "Do thing 2"})
    return sop_id


def test_submit_feedback(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    resp = authed_client.post(
        f"/api/sops/{sop_id}/feedback",
        json={"feedback_text": "Step 1 needs more detail", "scope": "step:1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback_id"] > 0
    assert data["new_revision"] == 2


def test_list_revisions(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    authed_client.post(f"/api/sops/{sop_id}/feedback",
                       json={"feedback_text": "Fix it", "scope": "full"})
    resp = authed_client.get(f"/api/sops/{sop_id}/revisions")
    assert resp.status_code == 200
    revisions = resp.json()
    assert len(revisions) >= 1
    assert revisions[0]["revision"] == 1


def test_get_revision_snapshot(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    authed_client.post(f"/api/sops/{sop_id}/feedback",
                       json={"feedback_text": "Fix it", "scope": "full"})
    resp = authed_client.get(f"/api/sops/{sop_id}/revisions/1")
    assert resp.status_code == 200
    data = resp.json()
    steps = json.loads(data["steps_snapshot_json"])
    assert len(steps) == 2


def test_get_sop_status(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    resp = authed_client.get(f"/api/sops/{sop_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert resp.json()["revision"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_sop_feedback_api.py -v 2>&1 | tail -10`

Expected: FAIL.

- [ ] **Step 3: Implement `server/sop_feedback_router.py`**

```python
"""SOP feedback, revision history, and regeneration API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server import db
from server.auth_router import get_current_user

router = APIRouter(prefix="/api/sops", tags=["sop-feedback"])


class FeedbackRequest(BaseModel):
    feedback_text: str
    scope: str = "full"  # "full" or "step:N"


class FeedbackResponse(BaseModel):
    feedback_id: int
    new_revision: int
    status: str


@router.post("/{sop_id}/feedback", response_model=FeedbackResponse)
def submit_feedback(
    sop_id: int,
    body: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    """Submit modification feedback and trigger SOP regeneration."""
    sop = db.get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")

    current_revision = sop.get("revision", 1) or 1

    # 1. Snapshot current steps
    steps = db.list_sop_steps(sop_id)
    snapshot = json.dumps([dict(s) for s in steps], ensure_ascii=False, default=str)
    db.insert_sop_revision(sop_id, current_revision, snapshot)

    # 2. Insert feedback
    feedback_id = db.insert_sop_feedback(
        sop_id=sop_id,
        revision=current_revision,
        user_id=current_user["username"],
        feedback_text=body.feedback_text,
        feedback_scope=body.scope,
    )

    # 3. Bump revision, set status to regenerating
    new_revision = current_revision + 1
    db.update_sop_revision(sop_id, new_revision, status="regenerating")

    # 4. Queue regeneration (handled by analysis pool picking up sop tasks)
    db.queue_sop_regeneration(sop_id, feedback_id)

    return FeedbackResponse(
        feedback_id=feedback_id,
        new_revision=new_revision,
        status="regenerating",
    )


@router.get("/{sop_id}/status")
def get_sop_status(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    sop = db.get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    return {
        "status": sop["status"],
        "revision": sop.get("revision", 1) or 1,
    }


@router.get("/{sop_id}/revisions")
def list_revisions(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    sop = db.get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    return db.list_sop_revisions(sop_id)


@router.get("/{sop_id}/revisions/{rev}")
def get_revision(
    sop_id: int,
    rev: int,
    current_user: dict = Depends(get_current_user),
):
    revision = db.get_sop_revision(sop_id, rev)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")
    return revision


@router.post("/{sop_id}/revisions/{rev}/restore")
def restore_revision(
    sop_id: int,
    rev: int,
    current_user: dict = Depends(get_current_user),
):
    """Restore a historical revision as the current version."""
    revision = db.get_sop_revision(sop_id, rev)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    sop = db.get_sop(sop_id)
    current_revision = sop.get("revision", 1) or 1

    # Snapshot current before restoring
    steps = db.list_sop_steps(sop_id)
    snapshot = json.dumps([dict(s) for s in steps], ensure_ascii=False, default=str)
    db.insert_sop_revision(sop_id, current_revision, snapshot)

    # Restore old steps
    old_steps = json.loads(revision["steps_snapshot_json"])
    db.delete_sop_steps(sop_id)
    for step in old_steps:
        db.insert_sop_step(
            sop_id=sop_id,
            step_order=step.get("step_order", 0),
            title=step.get("title", ""),
            description=step.get("description", ""),
            application=step.get("application", ""),
            human_description=step.get("human_description", ""),
            machine_actions=step.get("machine_actions"),
        )

    new_revision = current_revision + 1
    db.update_sop_revision(sop_id, new_revision, status="draft")

    return {"ok": True, "revision": new_revision}
```

- [ ] **Step 4: Add missing DB helper functions**

In `server/db.py`, add:

```python
def update_sop_revision(sop_id: int, revision: int, status: str = "draft") -> None:
    """Update SOP revision number and status."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "UPDATE sops SET revision = ?, status = ?, updated_at = ? WHERE id = ?",
            (revision, status, now, sop_id),
        )


def queue_sop_regeneration(sop_id: int, feedback_id: int) -> None:
    """Queue a SOP for regeneration. For now, uses a simple DB flag.

    The analysis worker pool checks for SOPs with status='regenerating'.
    """
    # The regeneration is picked up by workers checking for this status.
    # No separate queue needed — workers poll for it.
    pass


def delete_sop_steps(sop_id: int) -> None:
    """Delete all steps for a SOP (before restoring a revision)."""
    with connect() as conn:
        conn.execute("DELETE FROM sop_steps WHERE sop_id = ?", (sop_id,))
```

- [ ] **Step 5: Register router in `server/app.py`**

Add after the existing router includes:

```python
from server.sop_feedback_router import router as sop_feedback_router
app.include_router(sop_feedback_router)
```

Also update `_VALID_TRANSITIONS` in `server/sops_router.py` to include `regenerating`:

```python
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"in_review"},
    "regenerating": {"draft"},  # NEW: after LLM finishes
    "in_review": {"published", "draft"},
    "published": set(),
}
```

- [ ] **Step 6: Run tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_sop_feedback_api.py -v`

Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add server/sop_feedback_router.py server/db.py server/app.py server/sops_router.py tests/test_sop_feedback_api.py
git commit -m "feat: SOP feedback/revision API with snapshot + restore + regeneration queue"
```

---

## Task 7: Frontend — SOP API Extensions

**Files:**
- Modify: `dashboard/src/api/sops.ts`

- [ ] **Step 1: Add new API methods to `dashboard/src/api/sops.ts`**

Add interfaces and methods after the existing `sopsApi` object:

```typescript
// Add to existing interfaces section:
export interface SopFeedbackResponse {
  feedback_id: number
  new_revision: number
  status: string
}

export interface SopStatusResponse {
  status: string
  revision: number
}

export interface SopRevision {
  id: number
  sop_id: number
  revision: number
  steps_snapshot_json: string
  feedback_id: number | null
  created_at: string
}
```

Add to the `sopsApi` object:

```typescript
  // Feedback & revision methods
  getStatus: (sopId: number) =>
    client.get<SopStatusResponse>(`/api/sops/${sopId}/status`),

  submitFeedback: (sopId: number, body: { feedback_text: string; scope: string }) =>
    client.post<SopFeedbackResponse>(`/api/sops/${sopId}/feedback`, body),

  listRevisions: (sopId: number) =>
    client.get<SopRevision[]>(`/api/sops/${sopId}/revisions`),

  getRevision: (sopId: number, rev: number) =>
    client.get<SopRevision>(`/api/sops/${sopId}/revisions/${rev}`),

  restoreRevision: (sopId: number, rev: number) =>
    client.post<{ ok: boolean; revision: number }>(`/api/sops/${sopId}/revisions/${rev}/restore`),
```

Also extend `StepInfo` interface with new fields:

```typescript
export interface StepInfo {
  // ... existing fields ...
  human_description?: string
  machine_actions?: Array<{
    type: string
    x?: number
    y?: number
    target?: string
    text?: string
    key?: string
  }>
  revision?: number
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/api/sops.ts
git commit -m "feat(dashboard): add feedback/revision API methods to sops.ts"
```

---

## Task 8: Frontend — FrameCarousel Component

**Files:**
- Create: `dashboard/src/components/FrameCarousel.vue`

- [ ] **Step 1: Create `dashboard/src/components/FrameCarousel.vue`**

```vue
<script setup lang="ts">
import { ref, computed } from 'vue'
import type { FrameInfo } from '@/api/sessions'
import FrameImage from './FrameImage.vue'
import { NButton, NSpace } from 'naive-ui'

const props = defineProps<{
  frames: FrameInfo[]
  maxWidth?: string
}>()

const currentIndex = ref(0)

const currentFrame = computed(() =>
  props.frames.length > 0 ? props.frames[currentIndex.value] : null
)

function prev() {
  if (currentIndex.value > 0) currentIndex.value--
}

function next() {
  if (currentIndex.value < props.frames.length - 1) currentIndex.value++
}
</script>

<template>
  <div class="frame-carousel" :style="{ maxWidth: maxWidth || '100%' }">
    <FrameImage
      v-if="currentFrame"
      :frame="currentFrame"
      :max-width="maxWidth || '100%'"
      :clickable="false"
    />
    <div v-else class="carousel-empty">No frames</div>
    <NSpace justify="center" align="center" style="margin-top: 8px" :size="12">
      <NButton size="tiny" :disabled="currentIndex <= 0" @click="prev">
        ◀
      </NButton>
      <span class="carousel-counter">
        {{ frames.length > 0 ? `${currentIndex + 1} / ${frames.length}` : '0 / 0' }}
      </span>
      <NButton size="tiny" :disabled="currentIndex >= frames.length - 1" @click="next">
        ▶
      </NButton>
    </NSpace>
  </div>
</template>

<style scoped>
.frame-carousel {
  display: inline-block;
}
.carousel-counter {
  font-size: 12px;
  color: #666;
  min-width: 60px;
  text-align: center;
}
.carousel-empty {
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  background: #f5f5f5;
  border-radius: 4px;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/components/FrameCarousel.vue
git commit -m "feat(dashboard): FrameCarousel component for multi-frame image navigation"
```

---

## Task 9: Frontend — SopStepCard, SopFeedbackInput, SopRevisionNav Components

**Files:**
- Create: `dashboard/src/components/SopStepCard.vue`
- Create: `dashboard/src/components/SopFeedbackInput.vue`
- Create: `dashboard/src/components/SopRevisionNav.vue`

- [ ] **Step 1: Create `dashboard/src/components/SopStepCard.vue`**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { StepInfo } from '@/api/sops'
import { NCard, NTag, NSpace, NButton, NCollapse, NCollapseItem } from 'naive-ui'

const props = defineProps<{
  step: StepInfo
  index: number
  active: boolean
  readonly: boolean
}>()

const emit = defineEmits<{
  (e: 'select'): void
  (e: 'feedback', stepOrder: number): void
}>()

const actions = computed(() => {
  if (!props.step.machine_actions) return []
  return Array.isArray(props.step.machine_actions)
    ? props.step.machine_actions
    : []
})

function actionLabel(a: { type: string; target?: string; x?: number; y?: number }) {
  const pos = a.x !== undefined ? ` [${a.x}, ${a.y}]` : ''
  return `${a.type} ${a.target || ''}${pos}`.trim()
}
</script>

<template>
  <NCard
    size="small"
    hoverable
    :class="{ 'step-card-active': active }"
    class="sop-step-card"
    @click="emit('select')"
  >
    <NSpace vertical :size="6">
      <NSpace align="center" :size="8">
        <span class="step-number">{{ index + 1 }}</span>
        <span class="step-title">{{ step.title }}</span>
        <NTag v-if="step.application" size="small">{{ step.application }}</NTag>
      </NSpace>

      <div v-if="step.human_description" class="step-description">
        {{ step.human_description }}
      </div>
      <div v-else-if="step.description" class="step-description">
        {{ step.description }}
      </div>

      <NCollapse v-if="actions.length > 0">
        <NCollapseItem title="Machine Actions" name="actions">
          <div v-for="(a, i) in actions" :key="i" class="action-item">
            <NTag size="tiny" type="info">{{ a.type }}</NTag>
            <span class="action-detail">{{ actionLabel(a) }}</span>
          </div>
        </NCollapseItem>
      </NCollapse>

      <NButton
        v-if="!readonly"
        size="tiny"
        quaternary
        @click.stop="emit('feedback', step.step_order)"
      >
        Feedback on this step
      </NButton>
    </NSpace>
  </NCard>
</template>

<style scoped>
.sop-step-card { cursor: pointer; transition: border-color 0.2s; }
.sop-step-card.step-card-active { border-color: var(--primary-color, #18a058); }
.step-number { font-weight: 700; color: #18a058; min-width: 24px; }
.step-title { font-weight: 600; font-size: 14px; }
.step-description { font-size: 13px; color: #555; line-height: 1.5; }
.action-item { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.action-detail { font-size: 12px; color: #666; font-family: monospace; }
</style>
```

- [ ] **Step 2: Create `dashboard/src/components/SopFeedbackInput.vue`**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { NInput, NButton, NSpace } from 'naive-ui'

const props = defineProps<{
  disabled: boolean
  loading: boolean
  defaultScope?: string
}>()

const emit = defineEmits<{
  (e: 'submit', payload: { feedback_text: string; scope: string }): void
}>()

const feedbackText = ref('')
const scope = ref(props.defaultScope || 'full')

function submit() {
  if (!feedbackText.value.trim()) return
  emit('submit', {
    feedback_text: feedbackText.value.trim(),
    scope: scope.value,
  })
  feedbackText.value = ''
}
</script>

<template>
  <div class="feedback-input">
    <NInput
      v-model:value="feedbackText"
      type="textarea"
      :rows="3"
      :disabled="disabled"
      placeholder="Enter modification feedback..."
      @keydown.ctrl.enter="submit"
    />
    <NSpace justify="space-between" style="margin-top: 8px">
      <span class="scope-label">
        Scope: <strong>{{ scope === 'full' ? 'Full SOP' : scope }}</strong>
      </span>
      <NButton
        type="primary"
        :disabled="disabled || !feedbackText.trim()"
        :loading="loading"
        @click="submit"
      >
        Regenerate SOP
      </NButton>
    </NSpace>
  </div>
</template>

<style scoped>
.feedback-input { padding: 12px 0; }
.scope-label { font-size: 12px; color: #666; line-height: 34px; }
</style>
```

- [ ] **Step 3: Create `dashboard/src/components/SopRevisionNav.vue`**

```vue
<script setup lang="ts">
import { NButton, NSpace } from 'naive-ui'

const props = defineProps<{
  currentRevision: number
  maxRevision: number
  isHistorical: boolean
}>()

const emit = defineEmits<{
  (e: 'navigate', revision: number): void
  (e: 'restore'): void
}>()

function prev() {
  if (props.currentRevision > 1) {
    emit('navigate', props.currentRevision - 1)
  }
}

function next() {
  if (props.currentRevision < props.maxRevision) {
    emit('navigate', props.currentRevision + 1)
  }
}
</script>

<template>
  <NSpace align="center" :size="8">
    <NButton size="tiny" :disabled="currentRevision <= 1" @click="prev">
      ◀
    </NButton>
    <span class="rev-label">rev {{ currentRevision }}/{{ maxRevision }}</span>
    <NButton size="tiny" :disabled="currentRevision >= maxRevision" @click="next">
      ▶
    </NButton>
    <NButton
      v-if="isHistorical"
      size="tiny"
      type="warning"
      @click="emit('restore')"
    >
      Restore
    </NButton>
  </NSpace>
</template>

<style scoped>
.rev-label { font-size: 12px; color: #666; min-width: 70px; text-align: center; }
</style>
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/components/SopStepCard.vue dashboard/src/components/SopFeedbackInput.vue dashboard/src/components/SopRevisionNav.vue
git commit -m "feat(dashboard): SopStepCard, SopFeedbackInput, SopRevisionNav components"
```

---

## Task 10: Frontend — SopEditor.vue Redesign

**Files:**
- Modify: `dashboard/src/views/SopEditor.vue`

This is the most significant frontend change. Redesign from two-panel (step list + editor) to three-panel (step cards + frame preview + feedback area) with revision navigation.

- [ ] **Step 1: Rewrite `dashboard/src/views/SopEditor.vue`**

Replace the entire file. The new layout:
- Top bar: title + revision nav + status actions
- Left panel: SopStepCard list (scrollable)
- Right panel: FrameCarousel showing selected step's source frames
- Bottom: SopFeedbackInput

Key changes from existing:
- Remove drag-to-reorder and manual step editing (LLM generates steps now)
- Add revision navigation
- Add feedback input
- Add polling when status is `regenerating`
- Show `FrameCarousel` for selected step's `source_frame_ids`

```vue
<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { sopsApi, type SopDetail, type StepInfo } from '@/api/sops'
import { useAuthStore } from '@/stores/auth'
import SopStepCard from '@/components/SopStepCard.vue'
import SopFeedbackInput from '@/components/SopFeedbackInput.vue'
import SopRevisionNav from '@/components/SopRevisionNav.vue'
import FrameCarousel from '@/components/FrameCarousel.vue'
import {
  NCard, NSpace, NInput, NTag, NButton, NSpin, NEmpty,
  NScrollbar, NDivider, NDropdown, useMessage, NSkeleton,
} from 'naive-ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const auth = useAuthStore()

const sopId = Number(route.params.id)
const loading = ref(false)
const sop = ref<SopDetail | null>(null)
const selectedStepId = ref<number | null>(null)
const editTitle = ref('')
const savingTitle = ref(false)
const feedbackLoading = ref(false)

// Revision state
const currentRevision = ref(1)
const maxRevision = ref(1)
const viewingHistorical = ref(false)
const historicalSteps = ref<StepInfo[]>([])

// Polling for regenerating status
let pollTimer: ReturnType<typeof setInterval> | null = null

const statusLabel: Record<string, string> = {
  draft: 'Draft', regenerating: 'Regenerating...', in_review: 'In Review', published: 'Published',
}
const statusType: Record<string, string> = {
  draft: 'default', regenerating: 'warning', in_review: 'info', published: 'success',
}

const displaySteps = computed(() => {
  if (viewingHistorical.value) return historicalSteps.value
  return sop.value?.steps || []
})

const selectedStep = computed(() =>
  displaySteps.value.find(s => s.id === selectedStepId.value) || null
)

const isReadonly = computed(() =>
  viewingHistorical.value ||
  sop.value?.status === 'published' ||
  sop.value?.status === 'regenerating'
)

// Source frames for selected step (use source_frame_ids to build FrameInfo objects)
const selectedStepFrames = computed(() => {
  if (!selectedStep.value) return []
  const ids = selectedStep.value.source_frame_ids || []
  // Build minimal FrameInfo objects for FrameCarousel
  return ids.map((id: number) => ({ id, frame_index: 0 } as any))
})

async function fetchSop() {
  loading.value = true
  try {
    const { data } = await sopsApi.detail(sopId)
    sop.value = data
    editTitle.value = data.title
    currentRevision.value = (data as any).revision || 1
    maxRevision.value = currentRevision.value
    viewingHistorical.value = false

    if (data.status === 'regenerating') startPolling()
    else stopPolling()
  } catch {
    message.error('Failed to load SOP')
  } finally {
    loading.value = false
  }
}

async function saveTitle() {
  if (!sop.value || editTitle.value === sop.value.title) return
  savingTitle.value = true
  try {
    await sopsApi.update(sopId, { title: editTitle.value })
    sop.value.title = editTitle.value
  } catch { message.error('Failed to save title') }
  finally { savingTitle.value = false }
}

async function changeStatus(newStatus: string) {
  try {
    await sopsApi.updateStatus(sopId, { status: newStatus })
    await fetchSop()
    message.success(`Status changed to ${newStatus}`)
  } catch { message.error('Failed to change status') }
}

async function submitFeedback(payload: { feedback_text: string; scope: string }) {
  feedbackLoading.value = true
  try {
    const { data } = await sopsApi.submitFeedback(sopId, payload)
    message.success(`Feedback submitted, generating revision ${data.new_revision}`)
    currentRevision.value = data.new_revision
    maxRevision.value = data.new_revision
    if (sop.value) sop.value.status = 'regenerating'
    startPolling()
  } catch { message.error('Failed to submit feedback') }
  finally { feedbackLoading.value = false }
}

function handleStepFeedback(stepOrder: number) {
  // Pre-fill scope for the feedback input — use a simple approach
  // The SopFeedbackInput will get the scope from this
  feedbackScope.value = `step:${stepOrder}`
}
const feedbackScope = ref('full')

async function navigateRevision(rev: number) {
  if (rev === maxRevision.value) {
    viewingHistorical.value = false
    await fetchSop()
    return
  }
  try {
    const { data } = await sopsApi.getRevision(sopId, rev)
    const steps = JSON.parse(data.steps_snapshot_json)
    historicalSteps.value = steps
    currentRevision.value = rev
    viewingHistorical.value = true
  } catch { message.error('Failed to load revision') }
}

async function restoreRevision() {
  try {
    await sopsApi.restoreRevision(sopId, currentRevision.value)
    message.success('Revision restored')
    await fetchSop()
  } catch { message.error('Failed to restore revision') }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const { data } = await sopsApi.getStatus(sopId)
      if (data.status !== 'regenerating') {
        stopPolling()
        await fetchSop()
        message.success('SOP regeneration complete')
      }
    } catch { /* ignore polling errors */ }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function goBack() { router.push('/sops') }

const exportOptions = [
  { label: 'Markdown', key: 'md' },
  { label: 'JSON', key: 'json' },
]

async function handleExport(key: string) {
  try {
    const resp = key === 'md'
      ? await sopsApi.exportMd(sopId)
      : await sopsApi.exportJson(sopId)
    const blob = new Blob([typeof resp.data === 'string' ? resp.data : JSON.stringify(resp.data, null, 2)])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `sop-${sopId}.${key}`
    a.click()
    URL.revokeObjectURL(url)
  } catch { message.error('Export failed') }
}

onMounted(fetchSop)
onUnmounted(stopPolling)
</script>

<template>
  <div class="sop-editor-page">
    <NSpin :show="loading">
      <template v-if="sop">
        <!-- Top bar -->
        <NCard size="small" style="margin-bottom: 16px">
          <NSpace align="center" justify="space-between" style="width: 100%">
            <NSpace align="center" :size="12">
              <NButton text @click="goBack">&larr; Back</NButton>
              <NDivider vertical />
              <NInput
                v-model:value="editTitle"
                style="width: 280px; font-weight: 600"
                :disabled="isReadonly"
                @blur="saveTitle"
                @keyup.enter="($event.target as HTMLInputElement)?.blur()"
              />
              <NTag :type="(statusType[sop.status] || 'default') as any" size="small">
                {{ statusLabel[sop.status] || sop.status }}
              </NTag>
              <SopRevisionNav
                :current-revision="currentRevision"
                :max-revision="maxRevision"
                :is-historical="viewingHistorical"
                @navigate="navigateRevision"
                @restore="restoreRevision"
              />
            </NSpace>

            <NSpace :size="8">
              <NButton
                v-if="sop.status === 'draft'"
                type="warning" size="small"
                @click="changeStatus('in_review')"
              >Submit Review</NButton>
              <NButton
                v-if="sop.status === 'in_review'"
                type="success" size="small"
                @click="changeStatus('published')"
              >Publish</NButton>
              <NButton
                v-if="sop.status === 'in_review'"
                size="small"
                @click="changeStatus('draft')"
              >Reject</NButton>
              <NDropdown :options="exportOptions" @select="handleExport">
                <NButton size="small">Export</NButton>
              </NDropdown>
            </NSpace>
          </NSpace>
        </NCard>

        <!-- Main content -->
        <div class="editor-layout">
          <!-- Left: step list -->
          <div class="step-list-panel">
            <NCard title="Steps" size="small">
              <NSkeleton v-if="sop.status === 'regenerating'" :repeat="4" text style="margin: 8px 0" />
              <NEmpty v-else-if="displaySteps.length === 0" description="No steps yet" />
              <NScrollbar v-else style="max-height: calc(100vh - 380px)">
                <NSpace vertical :size="8">
                  <SopStepCard
                    v-for="(step, idx) in displaySteps"
                    :key="step.id || idx"
                    :step="step"
                    :index="idx"
                    :active="selectedStepId === step.id"
                    :readonly="isReadonly"
                    @select="selectedStepId = step.id"
                    @feedback="handleStepFeedback"
                  />
                </NSpace>
              </NScrollbar>
            </NCard>
          </div>

          <!-- Right: frame preview -->
          <div class="frame-preview-panel">
            <NCard title="Screenshots" size="small">
              <FrameCarousel
                v-if="selectedStepFrames.length > 0"
                :frames="selectedStepFrames"
                max-width="100%"
              />
              <NEmpty v-else description="Select a step to view screenshots" style="margin-top: 40px" />
            </NCard>
          </div>
        </div>

        <!-- Bottom: feedback -->
        <NCard v-if="!isReadonly || sop.status === 'in_review'" size="small" style="margin-top: 16px">
          <SopFeedbackInput
            :disabled="sop.status === 'regenerating'"
            :loading="feedbackLoading"
            :default-scope="feedbackScope"
            @submit="submitFeedback"
          />
        </NCard>
      </template>

      <NEmpty v-if="!sop && !loading" description="SOP not found" />
    </NSpin>
  </div>
</template>

<style scoped>
.sop-editor-page { padding: 0; }
.editor-layout { display: flex; gap: 16px; align-items: flex-start; }
.step-list-panel { width: 50%; min-width: 0; }
.frame-preview-panel { width: 50%; min-width: 0; }

@media (max-width: 900px) {
  .editor-layout { flex-direction: column; }
  .step-list-panel, .frame-preview-panel { width: 100%; }
}
</style>
```

- [ ] **Step 2: Build and verify**

Run: `cd /home/gaozhi/git_projects/computer-use/dashboard && npm run build 2>&1 | tail -5`

Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/views/SopEditor.vue
git commit -m "feat(dashboard): redesign SopEditor with step cards, frame preview, feedback, revision nav"
```

---

## Task 11: Sessions Router — Use Sessions Table

**Files:**
- Modify: `server/sessions_router.py`

Update the sessions router to use the new `sessions` table and return status info.

- [ ] **Step 1: Update `SessionInfo` model to include status**

In `server/sessions_router.py`, add `status` field:

```python
class SessionInfo(BaseModel):
    session_id: str
    employee_id: str
    first_frame_at: str
    last_frame_at: str
    frame_count: int
    applications: list[str] = Field(default_factory=list)
    status: str = "active"  # NEW
```

- [ ] **Step 2: Update `list_sessions` to merge sessions table data**

Modify the `list_sessions` endpoint to include session status from the sessions table (if available). The existing `db.list_sessions()` still derives data from frames. Merge status from sessions table:

After fetching sessions at line ~56, add:

```python
    # Enrich with status from sessions table
    for s in sessions:
        sess_record = db.get_session(s["session_id"])
        if sess_record:
            s["status"] = sess_record["status"]
        else:
            s["status"] = "active"
```

- [ ] **Step 3: Run existing sessions tests**

Run: `PYTHONPATH=src python3 -m pytest tests/test_sessions_api.py -v`

Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add server/sessions_router.py
git commit -m "feat(sessions): include session status from sessions table in API responses"
```

---

## Task 12: Integration Test — Full Pipeline

**Files:**
- Create: `tests/test_session_pipeline_integration.py`

End-to-end test of: upload frames → session detected idle → grouped → analysis → SOP created.

- [ ] **Step 1: Write integration test**

```python
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
        assert resp.status_code == 200

    # Verify session created
    sess = db.get_session("sess-int")
    assert sess is not None
    assert sess["status"] == "active"
    assert sess["frame_count"] == 6

    # 2. Run SessionFinalizer manually
    from server.session_finalizer import SessionFinalizer
    from server.frame_grouper import FrameGroup
    from server import frame_grouper

    # Mock grouper to avoid phash on fake PNGs
    monkeypatch.setattr(
        frame_grouper, "group_frames",
        lambda frames, **kw: [
            FrameGroup(0, [f["id"] for f in frames[:3]], "chrome"),
            FrameGroup(1, [f["id"] for f in frames[3:]], "excel"),
        ],
    )

    stop = threading.Event()
    finalizer = SessionFinalizer(stop_event=stop, idle_timeout=0, poll_interval=0.1)
    t = threading.Thread(target=finalizer.run, daemon=True)
    t.start()
    time.sleep(0.5)
    stop.set()
    t.join(timeout=2)

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
```

- [ ] **Step 2: Run integration test**

Run: `PYTHONPATH=src python3 -m pytest tests/test_session_pipeline_integration.py -v --run-integration`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_session_pipeline_integration.py
git commit -m "test: integration test for full session → group → analysis → SOP pipeline"
```

---

## Task 13: Build Dashboard & Final Verification

**Files:**
- Modify: `dashboard/` (build)

- [ ] **Step 1: Build production dashboard**

Run: `cd /home/gaozhi/git_projects/computer-use/dashboard && npm run build 2>&1 | tail -5`

Expected: Build succeeds.

- [ ] **Step 2: Run full test suite**

Run: `PYTHONPATH=src python3 -m pytest tests/ -v --timeout=30 2>&1 | tail -30`

Expected: All tests PASS.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "release: v0.5.0 — session-based group analysis + interactive SOP refinement"
```
