"""FastAPI collection server for workflow recorder frame analyses.

Endpoints:
    POST /frames         — ingest one frame
    POST /frames/batch   — ingest many frames at once
    GET  /frames         — query by employee_id / session_id
    GET  /health         — liveness probe

Authentication:
    If env WORKFLOW_SERVER_KEY is set, every POST/GET (except /health)
    requires the same value in an `X-API-Key` header. If the env var is
    unset, auth is disabled — handy for local dev.

Run:
    uvicorn server.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from server import db
from server.auth_router import router as auth_router
from server.users_router import router as users_router


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class UIElementIn(BaseModel):
    name: str = ""
    element_type: str = ""
    coordinates: list[int] = Field(default_factory=list)


class FrameIn(BaseModel):
    employee_id: str
    session_id: str
    frame_index: int
    timestamp: float
    application: str = ""
    window_title: str = ""
    user_action: str = ""
    text_content: str = ""
    confidence: float = 0.0
    mouse_position_estimate: list[int] = Field(default_factory=list)
    ui_elements_visible: list[UIElementIn] = Field(default_factory=list)


class IngestResult(BaseModel):
    ok: bool
    id: Optional[int] = None
    duplicate: bool = False


class BatchIngestResult(BaseModel):
    ok: bool
    inserted: int
    duplicates: int
    total: int


class FrameOut(BaseModel):
    id: int
    employee_id: str
    session_id: str
    frame_index: int
    recorded_at: str
    received_at: str
    application: Optional[str] = None
    window_title: Optional[str] = None
    user_action: Optional[str] = None
    text_content: Optional[str] = None
    confidence: float = 0.0
    mouse_position: list = Field(default_factory=list)
    ui_elements: list = Field(default_factory=list)


class FrameListResult(BaseModel):
    total: int
    count: int
    frames: list[FrameOut]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _expected_api_key() -> Optional[str]:
    key = os.environ.get("WORKFLOW_SERVER_KEY", "").strip()
    return key or None


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Dependency that enforces X-API-Key if WORKFLOW_SERVER_KEY is set."""
    expected = _expected_api_key()
    if expected is None:
        return  # auth disabled
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
        )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


app = FastAPI(
    title="Workflow Recorder Collector",
    description="Receives per-frame vision analyses from recorder clients.",
    version="0.1.0",
)
app.include_router(auth_router)
app.include_router(users_router)


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


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "db_path": str(db.db_path()),
        "auth_enabled": _expected_api_key() is not None,
    }


@app.post(
    "/frames",
    response_model=IngestResult,
    dependencies=[Depends(require_api_key)],
)
def ingest_frame(frame: FrameIn) -> IngestResult:
    row_id = db.insert_frame(frame.model_dump())
    if row_id is None:
        return IngestResult(ok=True, id=None, duplicate=True)
    return IngestResult(ok=True, id=row_id, duplicate=False)


@app.post(
    "/frames/batch",
    response_model=BatchIngestResult,
    dependencies=[Depends(require_api_key)],
)
def ingest_batch(frames: list[FrameIn]) -> BatchIngestResult:
    inserted = 0
    duplicates = 0
    for f in frames:
        row_id = db.insert_frame(f.model_dump())
        if row_id is None:
            duplicates += 1
        else:
            inserted += 1
    return BatchIngestResult(
        ok=True, inserted=inserted, duplicates=duplicates, total=len(frames),
    )


@app.get(
    "/frames",
    response_model=FrameListResult,
    dependencies=[Depends(require_api_key)],
)
def list_frames(
    employee_id: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> FrameListResult:
    rows = db.query_frames(
        employee_id=employee_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    total = db.count_frames(employee_id=employee_id, session_id=session_id)
    frames = [FrameOut(**r) for r in rows]
    return FrameListResult(total=total, count=len(frames), frames=frames)
