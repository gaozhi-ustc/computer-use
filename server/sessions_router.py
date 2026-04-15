"""Sessions API router."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server import db
from server.auth_router import get_current_user
from server.permissions import filter_employee_ids

log = structlog.get_logger()


class SessionInfo(BaseModel):
    session_id: str
    employee_id: str
    first_frame_at: str
    last_frame_at: str
    frame_count: int
    applications: list[str] = Field(default_factory=list)
    status: str = "active"


class SessionListResponse(BaseModel):
    total: int
    count: int
    sessions: list[SessionInfo]


router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/", response_model=SessionListResponse)
def list_sessions(
    employee_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    allowed = filter_employee_ids(current_user)
    # If allowed is None (admin), no restriction
    # If allowed is a list, restrict to those IDs
    kwargs: dict = {}
    if allowed is not None:
        if employee_id and employee_id in allowed:
            kwargs["employee_id"] = employee_id
        elif employee_id and employee_id not in allowed:
            return SessionListResponse(total=0, count=0, sessions=[])
        else:
            kwargs["employee_ids"] = allowed
    elif employee_id:
        kwargs["employee_id"] = employee_id

    sessions = db.list_sessions(
        **kwargs, date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )
    total = db.count_sessions(
        **{k: v for k, v in kwargs.items() if k not in ("date_from", "date_to")},
    )

    # Enrich with status from sessions table
    for s in sessions:
        sess_record = db.get_session(s["session_id"])
        if sess_record:
            s["status"] = sess_record["status"]
        else:
            s["status"] = "active"

    return SessionListResponse(
        total=total,
        count=len(sessions),
        sessions=[SessionInfo(**s) for s in sessions],
    )


@router.get("/{session_id}")
def get_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """Get session detail with all frames."""
    frames = db.query_frames(session_id=session_id, limit=10000)
    if not frames:
        raise HTTPException(status_code=404, detail="Session not found")

    # Permission check
    allowed = filter_employee_ids(current_user)
    if allowed is not None and frames[0]["employee_id"] not in allowed:
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "session_id": session_id,
        "employee_id": frames[0]["employee_id"],
        "frame_count": len(frames),
        "frames": frames,
    }


@router.post("/{session_id}/analyze")
def trigger_session_analysis(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Manually trigger group analysis for a session (admin/manager only)."""
    if current_user["role"] not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Admin or manager required")

    # Load frames to verify session exists and get employee_id
    frames = db.query_frames(session_id=session_id, limit=100_000)
    if not frames:
        raise HTTPException(status_code=404, detail="Session not found or has no frames")

    employee_id = frames[0]["employee_id"]

    # Permission check
    allowed = filter_employee_ids(current_user)
    if allowed is not None and employee_id not in allowed:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if already being processed
    sess_record = db.get_session(session_id)
    if sess_record and sess_record["status"] in ("finalizing", "grouped", "analyzed"):
        raise HTTPException(
            status_code=409,
            detail=f"Session already in status '{sess_record['status']}'"
        )

    # Ensure sessions table record exists (for legacy sessions uploaded before v0.5.0)
    frames.sort(key=lambda f: f.get("frame_index", 0))
    first_at = frames[0].get("recorded_at", "")
    last_at = frames[-1].get("recorded_at", "")
    if not sess_record:
        # Bootstrap: insert a sessions record from frames data
        db.upsert_session(session_id, employee_id, first_at)
        for _ in range(len(frames) - 1):
            db.upsert_session(session_id, employee_id, last_at)

    # Clear any previous groups for re-analysis
    _clear_session_groups(session_id)

    # Reset to active so finalizer logic can run
    db.update_session_status(session_id, "active")

    # Run finalization in a background thread
    def _run():
        try:
            from server.session_finalizer import SessionFinalizer
            sess = db.get_session(session_id)
            finalizer = SessionFinalizer(
                stop_event=threading.Event(), idle_timeout=0, poll_interval=999,
            )
            finalizer._finalize_session(sess)
            log.info("manual_session_analysis_triggered", session_id=session_id)
        except Exception:
            log.exception("manual_session_analysis_failed", session_id=session_id)
            db.update_session_status(session_id, "failed")

    threading.Thread(target=_run, daemon=True, name=f"analyze-{session_id[:8]}").start()

    return {"ok": True, "session_id": session_id, "status": "finalizing"}


def _clear_session_groups(session_id: str) -> None:
    """Remove existing frame_groups for a session to allow re-analysis."""
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM frame_groups WHERE session_id = ?", (session_id,),
        )
