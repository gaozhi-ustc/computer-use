"""SOP management API router.

Provides full CRUD for SOPs and their steps, status transitions,
auto-extraction from recorded sessions, and export in Markdown /
computer-use JSON formats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import groupby
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from server import db
from server.auth_router import get_current_user
from server.models import (
    SopCreate, SopDetail, SopInfo, SopListResponse, SopStatusUpdate, SopUpdate,
    StepCreate, StepInfo, StepReorder, StepUpdate,
)

router = APIRouter(prefix="/api/sops", tags=["sops"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"in_review"},
    "in_review": {"published", "draft"},  # draft = reject
    "published": set(),                    # terminal state
}


def _require_manager_or_admin(user: dict) -> None:
    if user.get("role") not in ("manager", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _get_sop_or_404(sop_id: int) -> dict:
    sop = db.get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    return sop


# ---------------------------------------------------------------------------
# SOP CRUD
# ---------------------------------------------------------------------------


@router.get("/", response_model=SopListResponse)
def list_sops(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    created_by: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    role = current_user.get("role", "employee")
    # Employees can only see published SOPs
    if role == "employee":
        status_filter = "published"

    sops = db.list_sops(status=status_filter, created_by=created_by, limit=limit, offset=offset)
    total = db.count_sops(status=status_filter, created_by=created_by)
    return SopListResponse(
        total=total,
        count=len(sops),
        sops=[SopInfo(**s) for s in sops],
    )


@router.post("/", response_model=SopInfo, status_code=201)
def create_sop(
    body: SopCreate,
    current_user: dict = Depends(get_current_user),
):
    # Employees can create draft SOPs (submit own workflows)
    # Manager/admin can create any SOP
    sop_id = db.insert_sop(
        title=body.title,
        description=body.description,
        created_by=current_user["username"],
        status="draft",
        assigned_reviewer=body.assigned_reviewer,
        source_session_id=body.source_session_id,
        source_employee_id=body.source_employee_id,
        tags=body.tags,
    )
    return SopInfo(**db.get_sop(sop_id))


@router.get("/{sop_id}", response_model=SopDetail)
def get_sop(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    sop = _get_sop_or_404(sop_id)
    role = current_user.get("role", "employee")
    # Employees can only see published SOPs
    if role == "employee" and sop["status"] != "published":
        raise HTTPException(status_code=404, detail="SOP not found")
    steps = db.list_sop_steps(sop_id)
    return SopDetail(**sop, steps=[StepInfo(**s) for s in steps])


@router.put("/{sop_id}", response_model=SopInfo)
def update_sop(
    sop_id: int,
    body: SopUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    _get_sop_or_404(sop_id)
    updates = body.model_dump(exclude_none=True)
    if updates:
        db.update_sop(sop_id, **updates)
    return SopInfo(**db.get_sop(sop_id))


@router.delete("/{sop_id}", status_code=204)
def delete_sop(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    _get_sop_or_404(sop_id)
    db.delete_sop(sop_id)
    return Response(status_code=204)


@router.put("/{sop_id}/status", response_model=SopInfo)
def update_sop_status(
    sop_id: int,
    body: SopStatusUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    sop = _get_sop_or_404(sop_id)
    current_status = sop["status"]
    new_status = body.status

    if new_status not in _VALID_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status transition: {current_status} -> {new_status}",
        )

    updates: dict = {"status": new_status}
    if new_status == "published":
        updates["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    db.update_sop(sop_id, **updates)
    return SopInfo(**db.get_sop(sop_id))


# ---------------------------------------------------------------------------
# Auto-generate steps from session frames
# ---------------------------------------------------------------------------


@router.post("/{sop_id}/generate")
def generate_steps(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    sop = _get_sop_or_404(sop_id)

    source_session = sop.get("source_session_id")
    if not source_session:
        raise HTTPException(status_code=400, detail="SOP has no source_session_id")

    # Fetch frames ordered by frame_index (recorded_at ascending)
    frames = db.query_frames(session_id=source_session, limit=10000)
    if not frames:
        raise HTTPException(status_code=404, detail="No frames found for source session")

    # Frames come back in descending order from query_frames; reverse for chronological
    frames.sort(key=lambda f: f.get("frame_index", 0))

    # Group consecutive frames by application
    steps_created = 0
    step_order = 0
    for app_name, group_iter in groupby(frames, key=lambda f: f.get("application", "")):
        group = list(group_iter)
        step_order += 1
        # Build step from grouped frames
        first = group[0]
        confidences = [f.get("confidence", 0.0) for f in group]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        actions = [f.get("user_action", "") for f in group if f.get("user_action")]
        title = actions[0] if actions else f"Use {app_name}"
        frame_ids = [f["id"] for f in group]

        db.insert_sop_step(
            sop_id=sop_id,
            step_order=step_order,
            title=title,
            description="; ".join(actions),
            application=app_name or "",
            action_type="",
            source_frame_ids=frame_ids,
            confidence=round(avg_confidence, 3),
        )
        steps_created += 1

    return {"ok": True, "steps_created": steps_created}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.get("/{sop_id}/export/md")
def export_markdown(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    sop = _get_sop_or_404(sop_id)
    steps = db.list_sop_steps(sop_id)

    lines: list[str] = []
    lines.append(f"# {sop['title']}")
    lines.append("")
    if sop.get("description"):
        lines.append(sop["description"])
        lines.append("")
    lines.append(f"**Status:** {sop['status']}  ")
    lines.append(f"**Created by:** {sop['created_by']}  ")
    lines.append(f"**Updated at:** {sop['updated_at']}  ")
    if sop.get("tags"):
        tags_str = ", ".join(sop["tags"]) if isinstance(sop["tags"], list) else sop["tags"]
        lines.append(f"**Tags:** {tags_str}  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    for step in steps:
        lines.append(f"## Step {step['step_order']}: {step['title']}")
        lines.append("")
        if step.get("application"):
            lines.append(f"**Application:** {step['application']}  ")
        if step.get("action_type"):
            lines.append(f"**Action:** {step['action_type']}  ")
        if step.get("description"):
            lines.append(f"{step['description']}")
        if step.get("confidence"):
            lines.append(f"**Confidence:** {step['confidence']:.1%}  ")
        lines.append("")

    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="sop_{sop_id}.md"'},
    )


@router.get("/{sop_id}/export/json")
def export_json(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Export SOP in computer-use Workflow JSON format.

    Builds a dict matching the Workflow schema from
    src/workflow_recorder/output/schema.py without importing it
    to avoid coupling server to the recorder client code.
    """
    sop = _get_sop_or_404(sop_id)
    steps = db.list_sop_steps(sop_id)

    workflow_steps = []
    for step in steps:
        action_detail = step.get("action_detail", {})
        actions = []
        if step.get("action_type"):
            action = {
                "type": step["action_type"],
                "target": action_detail.get("target", ""),
                "coordinates": action_detail.get("coordinates", []),
                "button": action_detail.get("button", "left"),
                "text": action_detail.get("text", ""),
                "keys": action_detail.get("keys", ""),
                "direction": action_detail.get("direction", ""),
                "amount": action_detail.get("amount", 3),
                "is_variable": action_detail.get("is_variable", False),
            }
            actions.append(action)

        workflow_steps.append({
            "step_id": step["step_order"],
            "timestamp": step.get("created_at", ""),
            "application": {
                "process_name": step.get("application", ""),
                "window_title": "",
                "window_rect": [],
            },
            "description": step.get("description", ""),
            "actions": actions,
            "wait_after_seconds": 0.0,
            "reference_screenshot": step.get("screenshot_ref", ""),
            "verification": {
                "expected_window_title": "",
                "expected_elements": [],
            },
            "confidence": step.get("confidence", 0.0),
            "source_frames": step.get("source_frame_ids", []),
        })

    workflow = {
        "$schema": "workflow-recorder/v1",
        "metadata": {
            "session_id": sop.get("source_session_id", ""),
            "recorded_at": sop.get("created_at", ""),
            "duration_seconds": 0.0,
            "total_frames_captured": 0,
            "total_steps": len(workflow_steps),
            "recorder_version": "0.1.0",
        },
        "environment": {
            "screen_resolution": [],
            "os": "",
            "hostname": "",
        },
        "steps": workflow_steps,
        "variables": {},
    }
    return workflow


# ---------------------------------------------------------------------------
# SOP Steps CRUD
# ---------------------------------------------------------------------------

# NOTE: reorder must be registered BEFORE the {step_id} routes so that
# "reorder" is not captured as a path parameter.


@router.put("/{sop_id}/steps/reorder")
def reorder_steps(
    sop_id: int,
    body: StepReorder,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    _get_sop_or_404(sop_id)
    db.reorder_sop_steps(sop_id, body.step_ids)
    return {"ok": True}


@router.post("/{sop_id}/steps/", response_model=StepInfo, status_code=201)
def add_step(
    sop_id: int,
    body: StepCreate,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    _get_sop_or_404(sop_id)
    step_id = db.insert_sop_step(
        sop_id=sop_id,
        step_order=body.step_order,
        title=body.title,
        description=body.description,
        application=body.application,
        action_type=body.action_type,
        action_detail=body.action_detail,
        screenshot_ref=body.screenshot_ref,
        source_frame_ids=body.source_frame_ids,
        confidence=body.confidence,
    )
    steps = db.list_sop_steps(sop_id)
    step = next(s for s in steps if s["id"] == step_id)
    return StepInfo(**step)


@router.put("/{sop_id}/steps/{step_id}", response_model=StepInfo)
def edit_step(
    sop_id: int,
    step_id: int,
    body: StepUpdate,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    _get_sop_or_404(sop_id)
    updates = body.model_dump(exclude_none=True)
    if updates:
        db.update_sop_step(step_id, **updates)
    steps = db.list_sop_steps(sop_id)
    step = next((s for s in steps if s["id"] == step_id), None)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    return StepInfo(**step)


@router.delete("/{sop_id}/steps/{step_id}", status_code=204)
def delete_step(
    sop_id: int,
    step_id: int,
    current_user: dict = Depends(get_current_user),
):
    _require_manager_or_admin(current_user)
    _get_sop_or_404(sop_id)
    db.delete_sop_step(step_id)
    return Response(status_code=204)
