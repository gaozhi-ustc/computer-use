# Offline Analysis — Phase 1: Server Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing FastAPI server so it can receive uploaded PNG images, save them to disk, insert `pending` rows into the frames table, and serve those images back to the dashboard — everything except the actual AI analysis loop (that's Phase 2).

**Architecture:** Additive DB migration (`ALTER TABLE ADD COLUMN` for image_path, analysis_status, cursor/focus coords). New upload endpoint accepts multipart form + image file, persists under `./frame_images/<employee_id>/<YYYY-MM-DD>/<session_id>/<frame_index>.png`, inserts `analysis_status='pending'` rows. Delete the old JSON-push `/frames` and `/frames/batch` endpoints. New auth-protected endpoints for image serving (`GET /api/frames/:id/image`), admin retry (`POST /api/frames/:id/retry`), and queue stats (`GET /api/frames/queue`).

**Tech Stack:** FastAPI, Starlette `UploadFile` + `FileResponse`, stdlib `sqlite3`, existing `auth_router.get_current_user` + `users_router.require_admin` dependencies.

**Phases roadmap (this plan covers Phase 1 only):**

| Phase | Deliverable | Depends on |
|-------|-------------|-----------|
| **1. Server Foundation** (this plan) | Upload + image serving + DB migration + admin endpoints | — |
| 2. Server AnalysisPool | api_keys.txt loader + worker threads + vision integration | Phase 1 |
| 3. Client Rewrite | Win32 cursor/focus capture + ImageUploader + daemon simplification | Phase 1 |
| 4. Dashboard | FrameImage component + Recording.vue update + Settings queue widget | Phase 1 (image endpoint) |
| 5. Release | Version bump, installer, CLAUDE.md, GitHub release | Phases 1-4 |

---

## File Structure

### New files

```
server/
├── image_storage.py       # save image bytes to ./frame_images/<emp>/<date>/<session>/<idx>.png
└── frames_router.py       # POST /frames/upload, GET /api/frames/:id/image,
                           # POST /api/frames/:id/retry, GET /api/frames/queue
tests/
├── test_image_storage.py
└── test_frames_router.py  # upload + image serving + retry + queue
```

### Modified files

```
server/db.py               # +migration columns, +insert_pending_frame, +claim_next_pending_frame,
                           # +mark_frame_done, +mark_frame_failed, +reset_frame_to_pending,
                           # +get_analysis_queue_stats, +get_frame
server/app.py              # mount frames_router, delete POST /frames + POST /frames/batch
.gitignore                 # +frame_images/, +api_keys.txt
```

---

### Task 1: DB schema migration — new columns and index

**Files:**
- Modify: `server/db.py` — extend `_migrate_add_columns()`
- Modify: `tests/test_server_db.py` — add test for new columns

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server_db.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_server_db.py::test_migration_adds_offline_columns tests/test_server_db.py::test_migration_adds_status_index -v`
Expected: FAIL — columns don't exist

- [ ] **Step 3: Extend `_migrate_add_columns` in server/db.py**

Locate the existing function in `server/db.py`:

```python
def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE for new columns added in later versions."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(frames)").fetchall()}
    if "context_data_json" not in cols:
        conn.execute(
            "ALTER TABLE frames ADD COLUMN context_data_json TEXT DEFAULT '{}'"
        )
```

Replace with the extended version:

```python
def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE for new columns added in later versions."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(frames)").fetchall()}
    if "context_data_json" not in cols:
        conn.execute(
            "ALTER TABLE frames ADD COLUMN context_data_json TEXT DEFAULT '{}'"
        )
    # v0.4.0: offline analysis fields
    if "image_path" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN image_path TEXT DEFAULT ''")
    if "analysis_status" not in cols:
        conn.execute(
            "ALTER TABLE frames ADD COLUMN analysis_status TEXT DEFAULT 'done'"
        )
    if "analysis_error" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN analysis_error TEXT DEFAULT ''")
    if "analysis_attempts" not in cols:
        conn.execute(
            "ALTER TABLE frames ADD COLUMN analysis_attempts INTEGER DEFAULT 0"
        )
    if "analyzed_at" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN analyzed_at TEXT DEFAULT ''")
    if "cursor_x" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN cursor_x INTEGER DEFAULT -1")
    if "cursor_y" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN cursor_y INTEGER DEFAULT -1")
    if "focus_rect_json" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN focus_rect_json TEXT DEFAULT ''")
    # Index for AnalysisPool worker queue lookup
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_frames_status ON frames(analysis_status, id)"
    )
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_server_db.py -q`
Expected: all pass (two new tests + existing ones still green)

- [ ] **Step 5: Commit**

```bash
git add server/db.py tests/test_server_db.py
git commit -m "feat: DB migration for offline analysis (image_path, analysis_status, cursor/focus)"
```

---

### Task 2: DB helpers for pending queue operations

**Files:**
- Modify: `server/db.py` — add 7 new functions
- Modify: `tests/test_server_db.py` — add tests

- [ ] **Step 1: Write failing tests**

Add to `tests/test_server_db.py`:

```python
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
    assert frame["analysis_status"] == "pending"
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
                         timestamp=100.0, image_path="/tmp/1.png")
    insert_pending_frame(employee_id="E1", session_id="s", frame_index=2,
                         timestamp=200.0, image_path="/tmp/2.png")
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
                               timestamp=1.0, image_path="/tmp/1.png")
    # frame 2 - will be marked failed
    id2 = insert_pending_frame(employee_id="E1", session_id="s", frame_index=2,
                               timestamp=2.0, image_path="/tmp/2.png")
    # frame 3 - stays pending
    id3 = insert_pending_frame(employee_id="E1", session_id="s", frame_index=3,
                               timestamp=3.0, image_path="/tmp/3.png")

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
                               timestamp=100.0, image_path="/tmp/1.png")
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
                               timestamp=100.0, image_path="/tmp/1.png")
    mark_frame_failed(fid, "401 unauthorized")
    frame = get_frame(fid)
    assert frame["analysis_status"] == "failed"
    assert frame["analysis_error"] == "401 unauthorized"


def test_reset_frame_to_pending_from_running(fresh_db):
    from server.db import insert_pending_frame, claim_next_pending_frame, reset_frame_to_pending, get_frame
    fid = insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                               timestamp=100.0, image_path="/tmp/1.png")
    claim_next_pending_frame()  # now running, attempts=1
    reset_frame_to_pending(fid)
    frame = get_frame(fid)
    assert frame["analysis_status"] == "pending"
    assert frame["analysis_attempts"] == 1  # not cleared by default


def test_reset_frame_to_pending_with_clear_attempts(fresh_db):
    from server.db import insert_pending_frame, claim_next_pending_frame, mark_frame_failed, reset_frame_to_pending, get_frame
    fid = insert_pending_frame(employee_id="E1", session_id="s", frame_index=1,
                               timestamp=100.0, image_path="/tmp/1.png")
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
                             timestamp=float(i), image_path=f"/tmp/{i}.png")
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
    assert stats == {"pending": 1, "running": 1, "failed": 1, "done": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_server_db.py -k "pending or claim or mark_frame or reset or queue_stats" -v`
Expected: FAIL — functions not defined

- [ ] **Step 3: Implement the helpers in `server/db.py`**

Append at the end of `server/db.py` (before the existing `_row_to_dict` helper if present, otherwise at end):

```python
# ---------------------------------------------------------------------------
# Offline analysis queue (Phase 1 of offline-analysis architecture)
# ---------------------------------------------------------------------------


def insert_pending_frame(
    employee_id: str,
    session_id: str,
    frame_index: int,
    timestamp: float,
    image_path: str,
    cursor_x: int = -1,
    cursor_y: int = -1,
    focus_rect: list[int] | None = None,
) -> int | None:
    """Insert a frame in 'pending' state, awaiting analysis.

    Returns the row id, or None on UNIQUE collision (retry of the same frame).
    """
    received_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    recorded_at = _ts_to_iso(timestamp)
    focus_json = json.dumps(focus_rect) if focus_rect else ""

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO frames (
                employee_id, session_id, frame_index,
                recorded_at, received_at,
                application, window_title, user_action, text_content,
                confidence, mouse_position_json, ui_elements_json, context_data_json,
                image_path, analysis_status, analysis_attempts,
                cursor_x, cursor_y, focus_rect_json
            ) VALUES (?, ?, ?, ?, ?, '', '', '', '', 0.0, '[]', '[]', '{}',
                      ?, 'pending', 0, ?, ?, ?)
            """,
            (employee_id, session_id, frame_index, recorded_at, received_at,
             image_path, cursor_x, cursor_y, focus_json),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid


def claim_next_pending_frame() -> dict[str, Any] | None:
    """Atomically claim the oldest pending frame for analysis.

    Sets status='running', increments analysis_attempts, returns the row.
    Returns None if no pending frames available.
    """
    with connect() as conn:
        row = conn.execute(
            """
            UPDATE frames
            SET analysis_status = 'running',
                analysis_attempts = analysis_attempts + 1
            WHERE id = (
                SELECT id FROM frames
                WHERE analysis_status = 'pending'
                ORDER BY id ASC
                LIMIT 1
            )
            RETURNING id, employee_id, session_id, frame_index, image_path,
                      cursor_x, cursor_y, focus_rect_json, analysis_attempts,
                      recorded_at
            """,
        ).fetchone()
    return dict(row) if row else None


def mark_frame_done(frame_id: int, analysis: dict[str, Any]) -> None:
    """Write analysis results and transition to 'done'."""
    analyzed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            UPDATE frames SET
                analysis_status = 'done',
                analysis_error = '',
                analyzed_at = ?,
                application = ?,
                window_title = ?,
                user_action = ?,
                text_content = ?,
                confidence = ?,
                mouse_position_json = ?,
                ui_elements_json = ?,
                context_data_json = ?
            WHERE id = ?
            """,
            (
                analyzed_at,
                analysis.get("application", ""),
                analysis.get("window_title", ""),
                analysis.get("user_action", ""),
                analysis.get("text_content", ""),
                float(analysis.get("confidence", 0.0)),
                json.dumps(analysis.get("mouse_position_estimate") or [], ensure_ascii=False),
                json.dumps(analysis.get("ui_elements_visible") or [], ensure_ascii=False),
                json.dumps(analysis.get("context_data") or {}, ensure_ascii=False),
                frame_id,
            ),
        )


def mark_frame_failed(frame_id: int, reason: str) -> None:
    """Transition to 'failed' with error reason."""
    with connect() as conn:
        conn.execute(
            "UPDATE frames SET analysis_status = 'failed', analysis_error = ? WHERE id = ?",
            (reason, frame_id),
        )


def reset_frame_to_pending(frame_id: int, clear_attempts: bool = False) -> None:
    """Put a frame back into 'pending' (either for retry or admin-triggered reset)."""
    with connect() as conn:
        if clear_attempts:
            conn.execute(
                "UPDATE frames SET analysis_status = 'pending', "
                "analysis_attempts = 0, analysis_error = '' WHERE id = ?",
                (frame_id,),
            )
        else:
            conn.execute(
                "UPDATE frames SET analysis_status = 'pending' WHERE id = ?",
                (frame_id,),
            )


def get_analysis_queue_stats() -> dict[str, int]:
    """Return counts per analysis_status value."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT analysis_status, COUNT(*) as n FROM frames GROUP BY analysis_status"
        ).fetchall()
    stats = {"pending": 0, "running": 0, "failed": 0, "done": 0}
    for r in rows:
        status = r["analysis_status"]
        if status in stats:
            stats[status] = int(r["n"])
    return stats


def get_frame(frame_id: int) -> dict[str, Any] | None:
    """Return a single frame by id with deserialized JSON fields."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM frames WHERE id = ?", (frame_id,)
        ).fetchone()
    if not row:
        return None
    return _frame_row_to_dict(row)


def _frame_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Deserialize a frames row including all JSON columns + focus_rect."""
    d = dict(row)
    for key in ("mouse_position_json", "ui_elements_json"):
        raw = d.pop(key, None)
        out_key = key.replace("_json", "")
        try:
            d[out_key] = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            d[out_key] = []
    raw_ctx = d.pop("context_data_json", None)
    try:
        d["context_data"] = json.loads(raw_ctx) if raw_ctx else {}
    except (json.JSONDecodeError, TypeError):
        d["context_data"] = {}
    raw_focus = d.pop("focus_rect_json", None)
    try:
        d["focus_rect"] = json.loads(raw_focus) if raw_focus else None
    except (json.JSONDecodeError, TypeError):
        d["focus_rect"] = None
    return d
```

Also update `query_frames` and `_row_to_dict` (existing) to deserialize the new `focus_rect_json` column. Locate existing `_row_to_dict`:

```python
def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    list_keys = ("mouse_position_json", "ui_elements_json")
    for key in list_keys:
        raw = d.pop(key, None)
        out_key = key.replace("_json", "")
        try:
            d[out_key] = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            d[out_key] = []
    raw_ctx = d.pop("context_data_json", None)
    try:
        d["context_data"] = json.loads(raw_ctx) if raw_ctx else {}
    except json.JSONDecodeError:
        d["context_data"] = {}
    return d
```

Replace with:

```python
def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    list_keys = ("mouse_position_json", "ui_elements_json")
    for key in list_keys:
        raw = d.pop(key, None)
        out_key = key.replace("_json", "")
        try:
            d[out_key] = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            d[out_key] = []
    raw_ctx = d.pop("context_data_json", None)
    try:
        d["context_data"] = json.loads(raw_ctx) if raw_ctx else {}
    except json.JSONDecodeError:
        d["context_data"] = {}
    raw_focus = d.pop("focus_rect_json", None)
    try:
        d["focus_rect"] = json.loads(raw_focus) if raw_focus else None
    except json.JSONDecodeError:
        d["focus_rect"] = None
    return d
```

Also update the SELECT in `query_frames` to include `context_data_json, focus_rect_json, image_path, analysis_status, cursor_x, cursor_y`. Find the SELECT statement in `query_frames`:

```python
sql = (
    "SELECT id, employee_id, session_id, frame_index, recorded_at, "
    "received_at, application, window_title, user_action, text_content, "
    "confidence, mouse_position_json, ui_elements_json, context_data_json "
    f"FROM frames {where} "
    "ORDER BY recorded_at DESC, frame_index DESC "
    "LIMIT ? OFFSET ?"
)
```

Replace with:

```python
sql = (
    "SELECT id, employee_id, session_id, frame_index, recorded_at, "
    "received_at, application, window_title, user_action, text_content, "
    "confidence, mouse_position_json, ui_elements_json, context_data_json, "
    "image_path, analysis_status, cursor_x, cursor_y, focus_rect_json "
    f"FROM frames {where} "
    "ORDER BY recorded_at DESC, frame_index DESC "
    "LIMIT ? OFFSET ?"
)
```

- [ ] **Step 4: Run all tests**

Run: `PYTHONPATH=src python -m pytest tests/test_server_db.py -q`
Expected: all green (new + existing)

- [ ] **Step 5: Commit**

```bash
git add server/db.py tests/test_server_db.py
git commit -m "feat: pending queue DB helpers (insert/claim/done/failed/reset/stats)"
```

---

### Task 3: Image storage helper

**Files:**
- Create: `server/image_storage.py`
- Create: `tests/test_image_storage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_image_storage.py`:

```python
"""Tests for server/image_storage.py — save uploaded PNG bytes to filesystem."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_save_image_creates_expected_path(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path))
    from server.image_storage import save_image
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake but with PNG magic
    path = save_image(
        employee_id="E001",
        session_id="sess-abc",
        frame_index=42,
        image_bytes=png_bytes,
        received_at_iso="2026-04-14T10:30:00+00:00",
    )
    expected = tmp_path / "E001" / "2026-04-14" / "sess-abc" / "42.png"
    assert path == expected.resolve()
    assert expected.exists()
    assert expected.read_bytes() == png_bytes


def test_save_image_creates_nested_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path))
    from server.image_storage import save_image
    save_image(
        employee_id="E1",
        session_id="s1",
        frame_index=1,
        image_bytes=b"\x89PNG",
        received_at_iso="2026-01-01T00:00:00+00:00",
    )
    assert (tmp_path / "E1" / "2026-01-01" / "s1").is_dir()


def test_save_image_default_dir(tmp_path, monkeypatch):
    # Run from tmp_path as cwd so default './frame_images' lands there
    monkeypatch.delenv("WORKFLOW_IMAGE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    from server.image_storage import save_image
    path = save_image(
        employee_id="E1", session_id="s", frame_index=7,
        image_bytes=b"\x89PNG",
        received_at_iso="2026-01-01T00:00:00+00:00",
    )
    assert (tmp_path / "frame_images" / "E1" / "2026-01-01" / "s" / "7.png").exists()


def test_save_image_sanitizes_path_segments(tmp_path, monkeypatch):
    """Should reject path-traversal attempts in employee_id / session_id."""
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path))
    from server.image_storage import save_image
    with pytest.raises(ValueError, match="invalid"):
        save_image(
            employee_id="../../etc/passwd",
            session_id="s", frame_index=1,
            image_bytes=b"\x89PNG",
            received_at_iso="2026-01-01T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="invalid"):
        save_image(
            employee_id="E1",
            session_id="../evil", frame_index=1,
            image_bytes=b"\x89PNG",
            received_at_iso="2026-01-01T00:00:00+00:00",
        )


def test_image_base_dir_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "custom"))
    from server.image_storage import image_base_dir
    assert image_base_dir() == (tmp_path / "custom").resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_image_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.image_storage'`

- [ ] **Step 3: Implement `server/image_storage.py`**

```python
"""Filesystem storage for uploaded frame PNGs.

Layout: <base>/<employee_id>/<YYYY-MM-DD>/<session_id>/<frame_index>.png
where <base> = $WORKFLOW_IMAGE_DIR or ./frame_images

The date subdirectory uses the server's received_at date, not the client's
recorded_at — this makes "all files received today" an easy ls.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


# Allow alphanumerics, dash, underscore, dot. Anything else -> invalid.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def image_base_dir() -> Path:
    """Resolve the image storage root from env or default."""
    raw = os.environ.get("WORKFLOW_IMAGE_DIR", "./frame_images")
    return Path(raw).expanduser().resolve()


def _safe_segment(name: str, field_name: str) -> str:
    """Validate a single path segment, rejecting traversal and weird chars."""
    if not name or not _SAFE_SEGMENT.match(name):
        raise ValueError(f"invalid {field_name}: {name!r}")
    return name


def save_image(
    employee_id: str,
    session_id: str,
    frame_index: int,
    image_bytes: bytes,
    received_at_iso: str,
) -> Path:
    """Save image bytes under base/<employee>/<date>/<session>/<index>.png.

    Returns the absolute path written to.
    Raises ValueError if any path segment contains invalid characters.
    """
    emp = _safe_segment(employee_id, "employee_id")
    sess = _safe_segment(session_id, "session_id")
    # received_at_iso looks like "2026-04-14T10:30:00+00:00"; take the date part
    date_part = received_at_iso.split("T", 1)[0]
    _safe_segment(date_part, "received_at date")  # sanity check

    base = image_base_dir()
    target_dir = base / emp / date_part / sess
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{int(frame_index)}.png"
    target.write_bytes(image_bytes)
    return target.resolve()
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_image_storage.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add server/image_storage.py tests/test_image_storage.py
git commit -m "feat: filesystem storage for uploaded frame PNGs with path sanitization"
```

---

### Task 4: Frames router — upload endpoint

**Files:**
- Create: `server/frames_router.py`
- Create: `tests/test_frames_router.py`
- Modify: `server/app.py` — mount router

- [ ] **Step 1: Write failing tests**

Create `tests/test_frames_router.py`:

```python
"""Tests for /frames/upload and dashboard frame-related admin endpoints."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "frame_images"))
    monkeypatch.setenv("WORKFLOW_SERVER_KEY", "test-upload-key")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret")
    # Avoid AnalysisPool warnings during tests (no api_keys.txt)
    from server.app import app
    from server import db
    from server.auth import hash_password
    db.init_db()
    db.insert_user(username="admin", password_hash=hash_password("admin123"),
                   display_name="Admin", role="admin", employee_id="E000")
    db.insert_user(username="emp", password_hash=hash_password("emp123"),
                   display_name="E", role="employee", employee_id="E001")
    return TestClient(app)


def _admin_token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return r.json()["access_token"]


def _emp_token(client):
    r = client.post("/api/auth/login", json={"username": "emp", "password": "emp123"})
    return r.json()["access_token"]


def _make_png_bytes() -> bytes:
    # Minimal 1x1 PNG (generated, not random bytes)
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDAT\x08\x99c\xfa\x0f\x00\x00\x01\x01\x00\x01"
        b"\xae\xf0\x18\x95\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_upload_frame_requires_api_key(client):
    r = client.post(
        "/frames/upload",
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "100", "cursor_y": "200", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    assert r.status_code == 401


def test_upload_frame_happy_path(client, tmp_path):
    r = client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "100", "cursor_y": "200",
              "focus_rect": "[50, 60, 250, 260]"},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] == 1
    # Verify image file written
    assert (tmp_path / "frame_images").exists()
    pngs = list((tmp_path / "frame_images").rglob("1.png"))
    assert len(pngs) == 1


def test_upload_frame_empty_focus_rect(client):
    r = client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "-1", "cursor_y": "-1", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    assert r.status_code == 200


def test_get_frame_image_requires_jwt(client):
    # First upload one
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    # Fetch without token
    r = client.get("/api/frames/1/image")
    assert r.status_code == 401


def test_get_frame_image_admin_can_read(client):
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    token = _admin_token(client)
    r = client.get("/api/frames/1/image",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_get_frame_image_employee_denied_other_employee(client):
    # Upload for E001
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    # emp user has employee_id=E001 so they can read their own
    emp_token = _emp_token(client)
    r = client.get("/api/frames/1/image",
                   headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 200

    # Upload for a different employee E999
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E999", "session_id": "s2",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    r = client.get("/api/frames/2/image",
                   headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 403


def test_get_frame_image_404_if_missing(client):
    token = _admin_token(client)
    r = client.get("/api/frames/999/image",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_retry_frame_requires_admin(client):
    # Upload one
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    # Mark failed
    from server import db
    db.mark_frame_failed(1, "test")

    # Employee can't retry
    emp_token = _emp_token(client)
    r = client.post("/api/frames/1/retry",
                    headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 403

    # Admin can
    admin_token = _admin_token(client)
    r = client.post("/api/frames/1/retry",
                    headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert db.get_frame(1)["analysis_status"] == "pending"
    assert db.get_frame(1)["analysis_attempts"] == 0


def test_queue_stats_endpoint(client):
    # Upload two pending frames
    for i in (1, 2):
        client.post(
            "/frames/upload",
            headers={"X-API-Key": "test-upload-key"},
            data={"employee_id": "E001", "session_id": "s1",
                  "frame_index": str(i), "timestamp": f"{i}.0",
                  "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
            files={"image": ("x.png", _make_png_bytes(), "image/png")},
        )

    admin_token = _admin_token(client)
    r = client.get("/api/frames/queue",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["pending"] == 2
    assert body["running"] == 0
    assert body["done"] == 0
    assert body["failed"] == 0


def test_queue_stats_employee_denied(client):
    emp_token = _emp_token(client)
    r = client.get("/api/frames/queue",
                   headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_frames_router.py -v`
Expected: FAIL — router doesn't exist yet

- [ ] **Step 3: Implement `server/frames_router.py`**

```python
"""Frames router: upload, image serving, admin retry, queue stats."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status,
)
from fastapi.responses import FileResponse

from server import db
from server.auth_router import get_current_user
from server.image_storage import save_image
from server.permissions import filter_employee_ids
from server.users_router import require_admin


router = APIRouter(tags=["frames"])


def require_upload_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Shared upload API key (same convention as the old POST /frames)."""
    import os
    expected = os.environ.get("WORKFLOW_SERVER_KEY", "").strip() or None
    if expected is None:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@router.post("/frames/upload")
def upload_frame(
    employee_id: str = Form(...),
    session_id: str = Form(...),
    frame_index: int = Form(...),
    timestamp: float = Form(...),
    cursor_x: int = Form(-1),
    cursor_y: int = Form(-1),
    focus_rect: str = Form(""),  # JSON array or empty
    image: UploadFile = File(...),
    _auth=Depends(require_upload_key),
):
    """Client uploads a captured screenshot awaiting server-side analysis."""
    image_bytes = image.file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="empty image")

    received_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        saved_path = save_image(
            employee_id=employee_id,
            session_id=session_id,
            frame_index=frame_index,
            image_bytes=image_bytes,
            received_at_iso=received_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    focus_rect_list = None
    if focus_rect.strip():
        try:
            import json as _json
            parsed = _json.loads(focus_rect)
            if isinstance(parsed, list) and len(parsed) == 4:
                focus_rect_list = [int(v) for v in parsed]
        except (ValueError, TypeError):
            focus_rect_list = None

    row_id = db.insert_pending_frame(
        employee_id=employee_id,
        session_id=session_id,
        frame_index=frame_index,
        timestamp=timestamp,
        image_path=str(saved_path),
        cursor_x=int(cursor_x),
        cursor_y=int(cursor_y),
        focus_rect=focus_rect_list,
    )
    if row_id is None:
        # Duplicate (same emp+session+frame_index). The file is now redundant
        # — clean it up to avoid accumulating orphan images on retry.
        try:
            saved_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": True, "id": None, "duplicate": True}

    return {"ok": True, "id": row_id, "duplicate": False}


@router.get("/api/frames/{frame_id}/image")
def get_frame_image(
    frame_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Serve the raw PNG for a frame, subject to role-based access filter."""
    frame = db.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="frame not found")

    # Permission check
    allowed = filter_employee_ids(current_user)
    if allowed is not None and frame["employee_id"] not in allowed:
        raise HTTPException(status_code=403, detail="access denied")

    path = Path(frame.get("image_path", "") or "")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image file missing")

    return FileResponse(path, media_type="image/png")


@router.post("/api/frames/{frame_id}/retry")
def retry_frame(
    frame_id: int,
    _admin: dict = Depends(require_admin),
):
    """Admin-only: reset a failed frame back to pending for re-analysis."""
    frame = db.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="frame not found")
    db.reset_frame_to_pending(frame_id, clear_attempts=True)
    return {"ok": True}


@router.get("/api/frames/queue")
def queue_stats(_admin: dict = Depends(require_admin)) -> dict:
    """Admin-only: counts of frames per analysis_status."""
    return db.get_analysis_queue_stats()
```

- [ ] **Step 4: Mount router in `server/app.py`**

Locate the existing router mounts in `server/app.py` (near `app.include_router(...)` calls):

```python
from server.auth_router import router as auth_router
from server.users_router import router as users_router
from server.sessions_router import router as sessions_router
from server.sops_router import router as sops_router
from server.stats_router import router as stats_router
```

Add the new import (grouped with others):

```python
from server.frames_router import router as frames_router
```

And the include call (with others):

```python
app.include_router(frames_router)
```

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_frames_router.py -v`
Expected: 10 passed

- [ ] **Step 6: Run full suite for regression**

Run: `PYTHONPATH=src python -m pytest tests/ -q --no-header`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add server/frames_router.py server/app.py tests/test_frames_router.py
git commit -m "feat: frames router — upload, image serving, admin retry, queue stats"
```

---

### Task 5: Delete old POST /frames and POST /frames/batch

**Files:**
- Modify: `server/app.py` — remove old endpoint functions + FrameIn/IngestResult/BatchIngestResult models
- Modify: `tests/test_server_db.py` — any tests for the old endpoints

- [ ] **Step 1: Identify what to delete**

In `server/app.py`, find and remove:
- `class FrameIn(BaseModel)` and its fields
- `class IngestResult(BaseModel)`
- `class BatchIngestResult(BaseModel)`
- `class UIElementIn(BaseModel)` (only used by FrameIn)
- `def ingest_frame(...)` endpoint function for POST /frames
- `def ingest_batch(...)` endpoint function for POST /frames/batch
- `def require_api_key(...)` dependency if only used by those endpoints (KEEP it if other tests still reference it; we already have `require_upload_key` in frames_router)

**Keep**:
- `FrameOut`, `FrameListResult` — still used by other endpoints (existing `/frames` GET was removed in Phase 5 planning, but if still there it's fine for now)
- `health` endpoint
- All router mounts
- Startup hooks

- [ ] **Step 2: Write/update test ensuring old endpoints are gone**

Add to `tests/test_frames_router.py`:

```python
def test_old_post_frames_returns_404(client):
    """Old JSON-push endpoint is deleted; ensure it 404s (no accidental re-add)."""
    r = client.post(
        "/frames",
        headers={"X-API-Key": "test-upload-key"},
        json={"employee_id": "E001", "session_id": "s", "frame_index": 1,
              "timestamp": 1.0},
    )
    assert r.status_code == 404


def test_old_post_frames_batch_returns_404(client):
    r = client.post(
        "/frames/batch",
        headers={"X-API-Key": "test-upload-key"},
        json=[],
    )
    assert r.status_code == 404
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src python -m pytest tests/test_frames_router.py::test_old_post_frames_returns_404 -v`
Expected: FAIL — endpoint still exists, returns 200 or validation error

- [ ] **Step 4: Delete the old code in `server/app.py`**

Remove the identified blocks. The file should now focus on app setup + health + static file serving + router mounts.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/ -q --no-header`
Expected: all pass (including the two new 404 tests)

If any existing test in `tests/` still POSTs to `/frames` or `/frames/batch`, update it to use `/frames/upload` with the new multipart form (or delete the test if it was specifically testing the old JSON protocol that no longer exists).

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_frames_router.py
git commit -m "refactor: remove legacy POST /frames and /frames/batch (hard cut for v0.4.0)"
```

---

### Task 6: Update .gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add patterns**

Append to `.gitignore`:

```
# Offline analysis (v0.4.0)
frame_images/
api_keys.txt
smoke_frames.db
dashboard_dev.db
```

Check `api_keys.txt` isn't already ignored by a wildcard.

- [ ] **Step 2: Verify**

Run: `git check-ignore -v api_keys.txt frame_images/test.png`
Expected: both show the gitignore rule

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore frame_images/ and api_keys.txt"
```

---

### Task 7: End-to-end smoke test for Phase 1

**Files:**
- Create: `tests/integration/test_upload_e2e.py`

- [ ] **Step 1: Write the smoke test**

```python
"""E2E smoke test: upload → row pending → retrieve image → admin retry.

Does not require Phase 2 (AnalysisPool). Exercises the full Phase 1
surface area through the public API.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_upload_and_query_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "imgs"))
    monkeypatch.setenv("WORKFLOW_SERVER_KEY", "sk-test")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "secret")

    from server.app import app
    from server import db
    from server.auth import hash_password
    db.init_db()
    db.insert_user(username="admin", password_hash=hash_password("p"),
                   display_name="A", role="admin", employee_id="E000")

    client = TestClient(app)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDAT\x08\x99c\xfa\x0f\x00\x00\x01\x01\x00\x01"
        b"\xae\xf0\x18\x95\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    # 1. Upload
    up = client.post(
        "/frames/upload",
        headers={"X-API-Key": "sk-test"},
        data={"employee_id": "E001", "session_id": "sess-1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "123", "cursor_y": "456",
              "focus_rect": "[10, 20, 100, 200]"},
        files={"image": ("1.png", png, "image/png")},
    )
    assert up.status_code == 200
    frame_id = up.json()["id"]

    # 2. DB has the pending row with the OS coords
    frame = db.get_frame(frame_id)
    assert frame["analysis_status"] == "pending"
    assert frame["cursor_x"] == 123
    assert frame["cursor_y"] == 456
    assert frame["focus_rect"] == [10, 20, 100, 200]

    # 3. Queue stats
    token = client.post("/api/auth/login",
                       json={"username": "admin", "password": "p"}).json()["access_token"]
    stats = client.get("/api/frames/queue",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert stats["pending"] == 1

    # 4. Image retrieval
    img_resp = client.get(f"/api/frames/{frame_id}/image",
                          headers={"Authorization": f"Bearer {token}"})
    assert img_resp.status_code == 200
    assert img_resp.content.startswith(b"\x89PNG")

    # 5. Simulate analysis failure + admin retry
    db.mark_frame_failed(frame_id, "simulated")
    retry_resp = client.post(f"/api/frames/{frame_id}/retry",
                             headers={"Authorization": f"Bearer {token}"})
    assert retry_resp.status_code == 200
    assert db.get_frame(frame_id)["analysis_status"] == "pending"
```

- [ ] **Step 2: Run it**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_upload_e2e.py --run-integration -v`
Expected: 1 passed

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_upload_e2e.py
git commit -m "test: E2E smoke test for Phase 1 upload lifecycle"
```

---

## Phase 1 Completion Criteria

After all 7 tasks:

1. `POST /frames/upload` accepts multipart (employee_id, session_id, frame_index, timestamp, cursor_x/y, focus_rect JSON, PNG file) with `X-API-Key` auth
2. Uploaded image saved to `<WORKFLOW_IMAGE_DIR>/<employee>/<YYYY-MM-DD>/<session>/<index>.png`
3. DB row inserted with `analysis_status = 'pending'`, OS cursor/focus coords, empty analysis fields
4. `GET /api/frames/:id/image` serves the raw PNG with JWT + role-based access filter
5. `POST /api/frames/:id/retry` (admin only) resets failed rows to pending with `analysis_attempts = 0`
6. `GET /api/frames/queue` (admin only) returns pending/running/failed/done counts
7. Old `POST /frames` and `POST /frames/batch` return 404
8. DB migration adds all new columns idempotently; old data becomes `analysis_status='done'`
9. Full test suite passes (existing + ~20 new tests)

**Verification command:**

```bash
PYTHONPATH=src python -m pytest tests/ --no-header -q
# Expected: ~228 passed (208 existing + ~20 new), 7 skipped
```
