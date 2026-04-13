"""Stats and dashboard API router."""

from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response

from server import db
from server.auth_router import get_current_user
from server.permissions import filter_employee_ids

router = APIRouter(tags=["stats"])


@router.get("/api/dashboard/summary")
def dashboard_summary(current_user: dict = Depends(get_current_user)):
    allowed = filter_employee_ids(current_user)
    return db.get_dashboard_summary(employee_ids=allowed)


@router.get("/api/dashboard/recent-sessions")
def recent_sessions(current_user: dict = Depends(get_current_user)):
    allowed = filter_employee_ids(current_user)
    kwargs: dict = {"employee_ids": allowed} if allowed is not None else {}
    return db.list_sessions(**kwargs, limit=10)


@router.get("/api/frames/stats")
def frame_stats(
    employee_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    allowed = filter_employee_ids(current_user)
    kwargs = _apply_filter(allowed, employee_id)
    kwargs.update({"date_from": date_from, "date_to": date_to})
    return {
        "app_usage": db.get_app_usage_stats(**kwargs),
        "heatmap": db.get_activity_heatmap(**kwargs),
        "daily": db.get_daily_active_stats(**kwargs),
    }


@router.get("/api/frames/search")
def search_frames(
    keyword: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    application: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    min_confidence: Optional[float] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    allowed = filter_employee_ids(current_user)
    kwargs = _apply_filter(allowed, employee_id)
    rows, total = db.search_frames(
        keyword=keyword,
        application=application,
        date_from=date_from,
        date_to=date_to,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
        **kwargs,
    )
    return {"total": total, "count": len(rows), "frames": rows}


@router.get("/api/frames/export")
def export_frames_csv(
    employee_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    allowed = filter_employee_ids(current_user)
    kwargs = _apply_filter(allowed, employee_id)
    rows, _ = db.search_frames(
        date_from=date_from, date_to=date_to, limit=10000, **kwargs
    )
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=frames_export.csv"
        },
    )


def _apply_filter(
    allowed: list[str] | None, employee_id: str | None
) -> dict:
    if allowed is None:
        return {"employee_id": employee_id} if employee_id else {}
    if employee_id and employee_id in allowed:
        return {"employee_id": employee_id}
    return {"employee_ids": allowed}
