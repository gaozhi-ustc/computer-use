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
    status TEXT NOT NULL CHECK(status IN ('draft', 'in_review', 'published')),
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
    )

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO frames (
                employee_id, session_id, frame_index,
                recorded_at, received_at,
                application, window_title, user_action, text_content,
                confidence, mouse_position_json, ui_elements_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        if cur.rowcount == 0:
            # UNIQUE constraint kicked in (idempotent retry).
            return None
        return cur.lastrowid


def query_frames(
    employee_id: Optional[str] = None,
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
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "SELECT id, employee_id, session_id, frame_index, recorded_at, "
        "received_at, application, window_title, user_action, text_content, "
        "confidence, mouse_position_json, ui_elements_json "
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
    session_id: Optional[str] = None,
) -> int:
    """Return the number of frames matching the filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id:
        clauses.append("employee_id = ?")
        params.append(employee_id)
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
    for key in ("mouse_position_json", "ui_elements_json"):
        raw = d.pop(key, None)
        out_key = key.replace("_json", "")
        try:
            d[out_key] = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            d[out_key] = []
    return d
