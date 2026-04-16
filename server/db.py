"""SQLite storage for received frame analyses.

One file database (path configurable via env WORKFLOW_SERVER_DB, defaults
to ./frames.db). Schema is idempotent — safe to call init_db() on every
server start. Connections are opened per-request to keep sqlite3's
per-thread rule happy without juggling a pool.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dingtalk_userid TEXT UNIQUE,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    display_name TEXT NOT NULL,
    avatar_url TEXT DEFAULT '',
    role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'employee')),
    employee_id TEXT,
    department TEXT DEFAULT '',
    department_id TEXT DEFAULT '',
    is_dept_manager BOOLEAN DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS sops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('draft', 'in_review', 'published', 'regenerating')),
    created_by TEXT NOT NULL,
    assigned_reviewer TEXT,
    source_session_id TEXT,
    source_employee_id TEXT,
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS sop_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sop_id INTEGER NOT NULL REFERENCES sops(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    application TEXT DEFAULT '',
    action_type TEXT DEFAULT '',
    action_detail TEXT DEFAULT '{}',
    screenshot_ref TEXT DEFAULT '',
    source_frame_ids TEXT DEFAULT '[]',
    confidence REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sops_status ON sops(status);
CREATE INDEX IF NOT EXISTS idx_sop_steps_sop ON sop_steps(sop_id, step_order);
"""

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    application TEXT,
    window_title TEXT,
    user_action TEXT,
    text_content TEXT,
    confidence REAL,
    mouse_position_json TEXT,
    ui_elements_json TEXT,
    context_data_json TEXT DEFAULT '{}',
    UNIQUE(employee_id, session_id, frame_index) ON CONFLICT IGNORE
);

CREATE INDEX IF NOT EXISTS idx_frames_employee_session
    ON frames(employee_id, session_id);

CREATE INDEX IF NOT EXISTS idx_frames_received
    ON frames(received_at);
"""


def db_path() -> Path:
    """Resolve the database file path from env or default."""
    raw = os.environ.get("WORKFLOW_SERVER_DB", "./frames.db")
    return Path(raw).expanduser().resolve()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a sqlite3 connection for the duration of a request."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and indexes if absent. Safe to re-run."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(USERS_SCHEMA)
        conn.executescript(SESSIONS_SCHEMA)
        conn.executescript(FRAME_GROUPS_SCHEMA)
        conn.executescript(SOP_FEEDBACKS_SCHEMA)
        conn.executescript(SOP_REVISIONS_SCHEMA)
        _migrate_add_columns(conn)

        # Idempotent ALTER TABLE for sops
        for col, default in [
            ("revision", "1"),
            ("source_group_ids_json", "'[]'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE sops ADD COLUMN {col} TEXT DEFAULT {default}")
            except Exception:
                pass

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
    # v0.5.2: server-side pre-analysis filter. Empty string = kept.
    # Values: 'near_duplicate' (phash near-same within heartbeat window),
    # 'low_signal' (empty canvas / loading splash). Images are retained
    # on disk regardless so thresholds can be retuned by re-running the
    # filter without re-uploading.
    if "skip_reason" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN skip_reason TEXT DEFAULT ''")
    # v0.4.9: client-reported "any mouse/keyboard input between prev capture
    # and this one". 0 = no input (passive), 1 = input happened (interactive).
    if "had_input" not in cols:
        conn.execute("ALTER TABLE frames ADD COLUMN had_input INTEGER DEFAULT 0")
    # Index for AnalysisPool worker queue lookup
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_frames_status ON frames(analysis_status, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_frames_session_skip "
        "ON frames(session_id, skip_reason)"
    )


def insert_frame(frame: dict[str, Any]) -> Optional[int]:
    """Insert a single frame. Returns the row id, or None if duplicate.

    Required keys: employee_id, session_id, frame_index, timestamp.
    Optional: application, window_title, user_action, text_content,
    confidence, mouse_position_estimate, ui_elements_visible.
    """
    received_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    recorded_at = _ts_to_iso(frame.get("timestamp"))

    row = (
        str(frame.get("employee_id", "")),
        str(frame.get("session_id", "")),
        int(frame.get("frame_index", 0)),
        recorded_at,
        received_at,
        frame.get("application"),
        frame.get("window_title"),
        frame.get("user_action"),
        frame.get("text_content"),
        float(frame.get("confidence") or 0.0),
        json.dumps(frame.get("mouse_position_estimate") or [], ensure_ascii=False),
        json.dumps(frame.get("ui_elements_visible") or [], ensure_ascii=False),
        json.dumps(frame.get("context_data") or {}, ensure_ascii=False),
    )

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO frames (
                employee_id, session_id, frame_index,
                recorded_at, received_at,
                application, window_title, user_action, text_content,
                confidence, mouse_position_json, ui_elements_json, context_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        if cur.rowcount == 0:
            # UNIQUE constraint kicked in (idempotent retry).
            return None
        return cur.lastrowid


def list_sessions(
    employee_id: str | None = None,
    employee_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List distinct sessions with aggregated metadata.

    Returns dicts with: session_id, employee_id, first_frame_at, last_frame_at,
    frame_count, applications (JSON array of distinct apps).
    """
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id:
        clauses.append("employee_id = ?")
        params.append(employee_id)
    if employee_ids:
        placeholders = ",".join("?" * len(employee_ids))
        clauses.append(f"employee_id IN ({placeholders})")
        params.extend(employee_ids)
    if date_from:
        clauses.append("recorded_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("recorded_at <= ?")
        params.append(date_to)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.extend([limit, offset])

    sql = f"""
        SELECT session_id, employee_id,
               MIN(recorded_at) as first_frame_at,
               MAX(recorded_at) as last_frame_at,
               COUNT(*) as frame_count,
               GROUP_CONCAT(DISTINCT application) as applications
        FROM frames {where}
        GROUP BY session_id, employee_id
        ORDER BY MAX(recorded_at) DESC
        LIMIT ? OFFSET ?
    """
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        apps_str = d.pop("applications", "") or ""
        d["applications"] = [a for a in apps_str.split(",") if a]
        result.append(d)
    return result


def count_sessions(
    employee_id: str | None = None,
    employee_ids: list[str] | None = None,
) -> int:
    """Return the number of distinct sessions matching the filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id:
        clauses.append("employee_id = ?")
        params.append(employee_id)
    if employee_ids:
        placeholders = ",".join("?" * len(employee_ids))
        clauses.append(f"employee_id IN ({placeholders})")
        params.extend(employee_ids)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        (count,) = conn.execute(
            f"SELECT COUNT(DISTINCT session_id) FROM frames {where}", params
        ).fetchone()
    return int(count)


def query_frames(
    employee_id: Optional[str] = None,
    employee_ids: list[str] | None = None,
    session_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query stored frames with optional filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id:
        clauses.append("employee_id = ?")
        params.append(employee_id)
    if employee_ids:
        placeholders = ",".join("?" * len(employee_ids))
        clauses.append(f"employee_id IN ({placeholders})")
        params.extend(employee_ids)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "SELECT id, employee_id, session_id, frame_index, recorded_at, "
        "received_at, application, window_title, user_action, text_content, "
        "confidence, mouse_position_json, ui_elements_json, context_data_json, "
        "image_path, analysis_status, cursor_x, cursor_y, focus_rect_json, "
        "skip_reason, had_input, window_title_raw "
        f"FROM frames {where} "
        "ORDER BY recorded_at DESC, frame_index DESC "
        "LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [_row_to_dict(r) for r in rows]


def count_frames(
    employee_id: Optional[str] = None,
    employee_ids: list[str] | None = None,
    session_id: Optional[str] = None,
) -> int:
    """Return the number of frames matching the filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id:
        clauses.append("employee_id = ?")
        params.append(employee_id)
    if employee_ids:
        placeholders = ",".join("?" * len(employee_ids))
        clauses.append(f"employee_id IN ({placeholders})")
        params.extend(employee_ids)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        (count,) = conn.execute(
            f"SELECT COUNT(*) FROM frames {where}", params
        ).fetchone()
    return int(count)


def insert_user(
    username: str,
    password_hash: str | None = None,
    display_name: str = "",
    role: str = "employee",
    employee_id: str | None = None,
    dingtalk_userid: str | None = None,
    department: str = "",
    department_id: str = "",
    is_dept_manager: bool = False,
) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO users (dingtalk_userid, username, password_hash,
               display_name, role, employee_id, department, department_id,
               is_dept_manager, is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (dingtalk_userid, username, password_hash, display_name, role,
             employee_id, department, department_id, is_dept_manager, now),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_dingtalk(dingtalk_userid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE dingtalk_userid = ?", (dingtalk_userid,)).fetchone()
    return dict(row) if row else None


def list_users(
    role: str | None = None,
    department_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if role:
        clauses.append("role = ?")
        params.append(role)
    if department_id:
        clauses.append("department_id = ?")
        params.append(department_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.extend([limit, offset])
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM users {where} ORDER BY id LIMIT ? OFFSET ?", params
        ).fetchall()
    return [dict(r) for r in rows]


def update_user(user_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [user_id]
    with connect() as conn:
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)


def delete_user(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def get_department_employee_ids(department_id: str) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT employee_id FROM users WHERE department_id = ? AND employee_id IS NOT NULL",
            (department_id,),
        ).fetchall()
    return [r["employee_id"] for r in rows]


# ---------------------------------------------------------------------------
# SOP CRUD
# ---------------------------------------------------------------------------


def insert_sop(
    title: str,
    created_by: str,
    description: str = "",
    status: str = "draft",
    assigned_reviewer: str | None = None,
    source_session_id: str | None = None,
    source_employee_id: str | None = None,
    tags: list[str] | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO sops (title, description, status, created_by,
               assigned_reviewer, source_session_id, source_employee_id,
               tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, status, created_by, assigned_reviewer,
             source_session_id, source_employee_id,
             json.dumps(tags or []), now, now),
        )
        return cur.lastrowid


def get_sop(sop_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sops WHERE id = ?", (sop_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    return d


def list_sops(
    status: str | None = None,
    created_by: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if created_by:
        clauses.append("created_by = ?")
        params.append(created_by)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.extend([limit, offset])
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM sops {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?", params
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        result.append(d)
    return result


def count_sops(status: str | None = None, created_by: str | None = None) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if created_by:
        clauses.append("created_by = ?")
        params.append(created_by)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        (count,) = conn.execute(f"SELECT COUNT(*) FROM sops {where}", params).fetchone()
    return int(count)


def update_sop(sop_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "tags" in fields and isinstance(fields["tags"], list):
        fields["tags"] = json.dumps(fields["tags"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [sop_id]
    with connect() as conn:
        conn.execute(f"UPDATE sops SET {sets} WHERE id = ?", vals)


def delete_sop(sop_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sop_steps WHERE sop_id = ?", (sop_id,))
        conn.execute("DELETE FROM sops WHERE id = ?", (sop_id,))


# ---------------------------------------------------------------------------
# SOP Steps CRUD
# ---------------------------------------------------------------------------


def insert_sop_step(
    sop_id: int,
    step_order: int,
    title: str,
    description: str = "",
    application: str = "",
    action_type: str = "",
    action_detail: dict | list | None = None,
    screenshot_ref: str = "",
    source_frame_ids: list[int] | None = None,
    confidence: float = 0.0,
    human_description: str = "",
    machine_actions: list | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO sop_steps (sop_id, step_order, title, description,
               application, action_type, action_detail, screenshot_ref,
               source_frame_ids, confidence, human_description,
               machine_actions, revision, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sop_id, step_order, title, description, application, action_type,
             json.dumps(action_detail or {}), screenshot_ref,
             json.dumps(source_frame_ids or []), confidence,
             human_description, json.dumps(machine_actions or []),
             1, now, now),
        )
        return cur.lastrowid


def list_sop_steps(sop_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sop_steps WHERE sop_id = ? ORDER BY step_order", (sop_id,)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["action_detail"] = json.loads(d.get("action_detail") or "{}")
        d["source_frame_ids"] = json.loads(d.get("source_frame_ids") or "[]")
        result.append(d)
    return result


def update_sop_step(step_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "action_detail" in fields and isinstance(fields["action_detail"], dict):
        fields["action_detail"] = json.dumps(fields["action_detail"])
    if "source_frame_ids" in fields and isinstance(fields["source_frame_ids"], list):
        fields["source_frame_ids"] = json.dumps(fields["source_frame_ids"])
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [step_id]
    with connect() as conn:
        conn.execute(f"UPDATE sop_steps SET {sets} WHERE id = ?", vals)


def delete_sop_step(step_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sop_steps WHERE id = ?", (step_id,))


def reorder_sop_steps(sop_id: int, step_ids: list[int]) -> None:
    """Update step_order for all steps in the given order."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        for order, step_id in enumerate(step_ids, 1):
            conn.execute(
                "UPDATE sop_steps SET step_order = ?, updated_at = ? WHERE id = ? AND sop_id = ?",
                (order, now, step_id, sop_id),
            )


# ---------------------------------------------------------------------------
# Stats / Analytics / Search
# ---------------------------------------------------------------------------


def get_app_usage_stats(
    employee_id: str | None = None,
    employee_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Return app usage distribution: [{application, frame_count, ...}]"""
    clauses, params = _build_employee_clauses(employee_id, employee_ids)
    if date_from:
        clauses.append("recorded_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("recorded_at <= ?")
        params.append(date_to)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT application, COUNT(*) as frame_count
            FROM frames {where}
            GROUP BY application ORDER BY frame_count DESC
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_activity_heatmap(
    employee_id: str | None = None,
    employee_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Return hourly activity data: [{hour: 0-23, weekday: 0-6, count: N}]"""
    clauses, params = _build_employee_clauses(employee_id, employee_ids)
    if date_from:
        clauses.append("recorded_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("recorded_at <= ?")
        params.append(date_to)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT CAST(strftime('%H', recorded_at) AS INTEGER) as hour,
                   CAST(strftime('%w', recorded_at) AS INTEGER) as weekday,
                   COUNT(*) as count
            FROM frames {where}
            GROUP BY hour, weekday
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_active_stats(
    employee_id: str | None = None,
    employee_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Return daily activity: [{date, frame_count, app_count, first_at, last_at}]"""
    clauses, params = _build_employee_clauses(employee_id, employee_ids)
    if date_from:
        clauses.append("recorded_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("recorded_at <= ?")
        params.append(date_to)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DATE(recorded_at) as date,
                   COUNT(*) as frame_count,
                   COUNT(DISTINCT application) as app_count,
                   MIN(recorded_at) as first_at,
                   MAX(recorded_at) as last_at
            FROM frames {where}
            GROUP BY date ORDER BY date DESC LIMIT 30
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def search_frames(
    keyword: str | None = None,
    employee_id: str | None = None,
    employee_ids: list[str] | None = None,
    application: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_confidence: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Full-text search across frames. Returns (rows, total_count)."""
    clauses, params = _build_employee_clauses(employee_id, employee_ids)
    if keyword:
        clauses.append("(user_action LIKE ? OR text_content LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if application:
        clauses.append("application = ?")
        params.append(application)
    if date_from:
        clauses.append("recorded_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("recorded_at <= ?")
        params.append(date_to)
    if min_confidence is not None:
        clauses.append("confidence >= ?")
        params.append(min_confidence)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    with connect() as conn:
        (total,) = conn.execute(
            f"SELECT COUNT(*) FROM frames {where}", params
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT id, employee_id, session_id, frame_index, recorded_at,
                   application, window_title, user_action, text_content,
                   confidence
            FROM frames {where}
            ORDER BY recorded_at DESC LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    return [dict(r) for r in rows], int(total)


def get_dashboard_summary(employee_ids: list[str] | None = None) -> dict[str, Any]:
    """Summary stats for the dashboard overview."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _where(extra_clauses: list[str] | None = None) -> tuple[str, list[Any]]:
        clauses = list(extra_clauses or [])
        params: list[Any] = []
        if employee_ids is not None:
            ph = ",".join("?" * len(employee_ids))
            clauses.append(f"employee_id IN ({ph})")
            params.extend(employee_ids)
        w = "WHERE " + " AND ".join(clauses) if clauses else ""
        return w, params

    with connect() as conn:
        # Today's frames
        w, p = _where(["DATE(recorded_at) = ?"])
        (today_frames,) = conn.execute(
            f"SELECT COUNT(*) FROM frames {w}", p + [today]
        ).fetchone()

        # Active sessions (today)
        (today_sessions,) = conn.execute(
            f"SELECT COUNT(DISTINCT session_id) FROM frames {w}", p + [today]
        ).fetchone()

        # SOP counts
        (draft_sops,) = conn.execute(
            "SELECT COUNT(*) FROM sops WHERE status = 'draft'"
        ).fetchone()
        (published_sops,) = conn.execute(
            "SELECT COUNT(*) FROM sops WHERE status = 'published'"
        ).fetchone()

        # Total employees (distinct in frames)
        w2, p2 = _where()
        (total_employees,) = conn.execute(
            f"SELECT COUNT(DISTINCT employee_id) FROM frames {w2}", p2
        ).fetchone()

    return {
        "today_frames": int(today_frames),
        "today_sessions": int(today_sessions),
        "draft_sops": int(draft_sops),
        "published_sops": int(published_sops),
        "total_employees": int(total_employees),
    }


def _build_employee_clauses(
    employee_id: str | None, employee_ids: list[str] | None
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id:
        clauses.append("employee_id = ?")
        params.append(employee_id)
    if employee_ids:
        ph = ",".join("?" * len(employee_ids))
        clauses.append(f"employee_id IN ({ph})")
        params.extend(employee_ids)
    return clauses, params


def _ts_to_iso(ts: Any) -> str:
    """Convert a unix timestamp (float) to ISO-8601, or echo a pre-formatted string."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    if isinstance(ts, str) and ts:
        return ts
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    # context_data is a dict (not list) — separate handling
    raw_ctx = d.pop("context_data_json", None)
    try:
        d["context_data"] = json.loads(raw_ctx) if raw_ctx else {}
    except json.JSONDecodeError:
        d["context_data"] = {}
    # focus_rect (list or None) — only present when query selects focus_rect_json
    if "focus_rect_json" in d:
        raw_focus = d.pop("focus_rect_json", None)
        try:
            d["focus_rect"] = json.loads(raw_focus) if raw_focus else None
        except json.JSONDecodeError:
            d["focus_rect"] = None
    return d


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
    window_title_raw: str = "",
    analysis_status: str = "uploaded",
    had_input: bool = False,
) -> int | None:
    """Insert a frame awaiting analysis.

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
                cursor_x, cursor_y, focus_rect_json, window_title_raw,
                had_input
            ) VALUES (?, ?, ?, ?, ?, '', '', '', '', 0.0, '[]', '[]', '{}',
                      ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (employee_id, session_id, frame_index, recorded_at, received_at,
             image_path, analysis_status, cursor_x, cursor_y, focus_json,
             window_title_raw, 1 if had_input else 0),
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
    if not row:
        return None
    d = dict(row)
    raw_focus = d.pop("focus_rect_json", None)
    try:
        d["focus_rect"] = json.loads(raw_focus) if raw_focus else None
    except (json.JSONDecodeError, TypeError):
        d["focus_rect"] = None
    return d


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
    stats = {"uploaded": 0, "pending": 0, "running": 0, "failed": 0, "done": 0}
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


# ---------------------------------------------------------------------------
# Sessions CRUD
# ---------------------------------------------------------------------------


def upsert_session(session_id: str, employee_id: str, frame_at: str) -> None:
    """Insert or update a session row when a new frame arrives."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO sessions
                   (session_id, employee_id, status, first_frame_at, last_frame_at,
                    frame_count, finalized_at, created_at, updated_at)
                   VALUES (?, ?, 'active', ?, ?, 1, '', ?, ?)""",
                (session_id, employee_id, frame_at, frame_at, now, now),
            )
        else:
            conn.execute(
                """UPDATE sessions SET
                   last_frame_at = ?, frame_count = frame_count + 1, updated_at = ?
                   WHERE session_id = ?""",
                (frame_at, now, session_id),
            )


def get_session(session_id: str) -> dict[str, Any] | None:
    """Return a session row by session_id."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
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


def update_session_status(
    session_id: str, status: str, finalized_at: str | None = None
) -> None:
    """Transition the session to a new status."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fin = finalized_at or ""
    with connect() as conn:
        conn.execute(
            "UPDATE sessions SET status = ?, finalized_at = ?, updated_at = ? WHERE session_id = ?",
            (status, fin, now, session_id),
        )


# ---------------------------------------------------------------------------
# Frame groups CRUD
# ---------------------------------------------------------------------------


def insert_frame_group(
    session_id: str,
    employee_id: str,
    group_index: int,
    frame_ids: list[int],
    primary_application: str = "",
) -> int:
    """Insert a new frame group. Returns the row id."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO frame_groups
               (session_id, employee_id, group_index, frame_ids_json,
                primary_application, analysis_status, analysis_error,
                analysis_attempts, analyzed_at, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', '', 0, '', ?)""",
            (session_id, employee_id, group_index,
             json.dumps(frame_ids), primary_application, now),
        )
        return cur.lastrowid


def get_frame_group(group_id: int) -> dict[str, Any] | None:
    """Return a frame group by id, with frame_ids deserialized."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM frame_groups WHERE id = ?", (group_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["frame_ids"] = json.loads(d.pop("frame_ids_json", "[]"))
    return d


def claim_next_pending_group() -> dict[str, Any] | None:
    """Atomically claim the oldest pending frame group for analysis."""
    with connect() as conn:
        row = conn.execute(
            """
            UPDATE frame_groups
            SET analysis_status = 'running',
                analysis_attempts = analysis_attempts + 1
            WHERE id = (
                SELECT id FROM frame_groups
                WHERE analysis_status = 'pending'
                ORDER BY id ASC
                LIMIT 1
            )
            RETURNING *
            """,
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["frame_ids"] = json.loads(d.pop("frame_ids_json", "[]"))
    return d


def mark_group_done(group_id: int) -> None:
    """Set group status to done."""
    analyzed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_status = 'done', analyzed_at = ? WHERE id = ?",
            (analyzed_at, group_id),
        )


def mark_group_failed(group_id: int, reason: str) -> None:
    """Set group status to failed with error reason."""
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_status = 'failed', analysis_error = ? WHERE id = ?",
            (reason, group_id),
        )


def reset_group_to_pending(group_id: int) -> None:
    """Reset a failed group back to pending for retry."""
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_status = 'pending', analysis_error = '' WHERE id = ?",
            (group_id,),
        )


def all_groups_done(session_id: str) -> bool:
    """Check if all frame groups for a session are done."""
    with connect() as conn:
        (total,) = conn.execute(
            "SELECT COUNT(*) FROM frame_groups WHERE session_id = ?", (session_id,)
        ).fetchone()
        (done,) = conn.execute(
            "SELECT COUNT(*) FROM frame_groups WHERE session_id = ? AND analysis_status = 'done'",
            (session_id,),
        ).fetchone()
    return total > 0 and total == done


def list_frame_groups(session_id: str) -> list[dict[str, Any]]:
    """List all frame groups for a session, ordered by group_index."""
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


def store_group_analysis_result(session_id: str, group_index: int,
                                 steps: list[dict]) -> None:
    """Store parsed SOP steps from group analysis as JSON on the group row.

    We repurpose analysis_error field for done groups to store the result JSON.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE frame_groups SET analysis_error = ? "
            "WHERE session_id = ? AND group_index = ?",
            (json.dumps(steps, ensure_ascii=False), session_id, group_index),
        )


def get_group_analysis_result(session_id: str, group_index: int) -> list[dict] | None:
    """Retrieve parsed SOP steps stored on a group row."""
    with connect() as conn:
        row = conn.execute(
            "SELECT analysis_error FROM frame_groups "
            "WHERE session_id = ? AND group_index = ? AND analysis_status = 'done'",
            (session_id, group_index),
        ).fetchone()
    if row is None or not row["analysis_error"]:
        return None
    try:
        return json.loads(row["analysis_error"])
    except (ValueError, TypeError):
        return None


def update_sop_group_ids(sop_id: int, group_ids: list[int]) -> None:
    """Set source_group_ids_json on a SOP."""
    with connect() as conn:
        conn.execute(
            "UPDATE sops SET source_group_ids_json = ? WHERE id = ?",
            (json.dumps(group_ids), sop_id),
        )


# ---------------------------------------------------------------------------
# SOP feedbacks & revisions
# ---------------------------------------------------------------------------


def insert_sop_feedback(
    sop_id: int,
    revision: int,
    user_id: str,
    feedback_text: str,
    feedback_scope: str = "full",
) -> int:
    """Insert a feedback entry for a SOP revision. Returns the row id."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO sop_feedbacks
               (sop_id, revision, user_id, feedback_text, feedback_scope, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sop_id, revision, user_id, feedback_text, feedback_scope, now),
        )
        return cur.lastrowid


def list_sop_feedbacks(sop_id: int) -> list[dict[str, Any]]:
    """List all feedbacks for a SOP, ordered by created_at."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sop_feedbacks WHERE sop_id = ? ORDER BY created_at",
            (sop_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_sop_revision(
    sop_id: int,
    revision: int,
    steps_snapshot_json: str,
    feedback_id: int | None = None,
) -> int:
    """Insert a SOP revision snapshot. Returns the row id."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO sop_revisions
               (sop_id, revision, steps_snapshot_json, feedback_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (sop_id, revision, steps_snapshot_json, feedback_id, now),
        )
        return cur.lastrowid


def list_sop_revisions(sop_id: int) -> list[dict[str, Any]]:
    """List all revisions for a SOP, ordered by revision number."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sop_revisions WHERE sop_id = ? ORDER BY revision",
            (sop_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_sop_revision(sop_id: int, revision: int) -> dict[str, Any] | None:
    """Get a specific revision of a SOP."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sop_revisions WHERE sop_id = ? AND revision = ?",
            (sop_id, revision),
        ).fetchone()
    return dict(row) if row else None


def update_sop_revision(sop_id: int, revision: int, status: str = "draft") -> None:
    """Update SOP revision number and status."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "UPDATE sops SET revision = ?, status = ?, updated_at = ? WHERE id = ?",
            (str(revision), status, now, sop_id),
        )


def queue_sop_regeneration(sop_id: int, feedback_id: int) -> None:
    """Queue a SOP for regeneration. Workers poll for status='regenerating'."""
    pass


def delete_sop_steps(sop_id: int) -> None:
    """Delete all steps for a SOP (before restoring a revision)."""
    with connect() as conn:
        conn.execute("DELETE FROM sop_steps WHERE sop_id = ?", (sop_id,))
