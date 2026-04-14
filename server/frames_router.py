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
