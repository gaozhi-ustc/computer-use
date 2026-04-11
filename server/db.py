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
