"""Sessions API router."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server import db
from server.auth_router import get_current_user
from server.permissions import filter_employee_ids


class SessionInfo(BaseModel):
    session_id: str
    employee_id: str
    first_frame_at: str
    last_frame_at: str
    frame_count: int
    applications: list[str] = Field(default_factory=list)


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
