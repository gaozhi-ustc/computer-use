"""SOP feedback, revision history, and regeneration API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from server import db
from server.auth_router import get_current_user

router = APIRouter(prefix="/api/sops", tags=["sop-feedback"])


class FeedbackRequest(BaseModel):
    feedback_text: str
    scope: str = "full"


class FeedbackResponse(BaseModel):
    feedback_id: int
    new_revision: int
    status: str


@router.post("/{sop_id}/feedback", response_model=FeedbackResponse)
def submit_feedback(
    sop_id: int,
    body: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    """Submit modification feedback and trigger SOP regeneration."""
    sop = db.get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")

    current_revision = sop.get("revision", 1) or 1
    # Handle the case where revision is stored as string
    if isinstance(current_revision, str):
        current_revision = int(current_revision) if current_revision else 1

    # 1. Snapshot current steps
    steps = db.list_sop_steps(sop_id)
    snapshot = json.dumps([dict(s) for s in steps], ensure_ascii=False, default=str)
    db.insert_sop_revision(sop_id, current_revision, snapshot)

    # 2. Insert feedback
    feedback_id = db.insert_sop_feedback(
        sop_id=sop_id,
        revision=current_revision,
        user_id=current_user["username"],
        feedback_text=body.feedback_text,
        feedback_scope=body.scope,
    )

    # 3. Bump revision, set status to regenerating
    new_revision = current_revision + 1
    db.update_sop_revision(sop_id, new_revision, status="regenerating")

    # 4. Queue regeneration
    db.queue_sop_regeneration(sop_id, feedback_id)

    return FeedbackResponse(
        feedback_id=feedback_id,
        new_revision=new_revision,
        status="regenerating",
    )


@router.get("/{sop_id}/status")
def get_sop_status(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    sop = db.get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    rev = sop.get("revision", 1) or 1
    if isinstance(rev, str):
        rev = int(rev) if rev else 1
    return {
        "status": sop["status"],
        "revision": rev,
    }


@router.get("/{sop_id}/revisions")
def list_revisions(
    sop_id: int,
    current_user: dict = Depends(get_current_user),
):
    sop = db.get_sop(sop_id)
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    return db.list_sop_revisions(sop_id)


@router.get("/{sop_id}/revisions/{rev}")
def get_revision(
    sop_id: int,
    rev: int,
    current_user: dict = Depends(get_current_user),
):
    revision = db.get_sop_revision(sop_id, rev)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")
    return revision


@router.post("/{sop_id}/revisions/{rev}/restore")
def restore_revision(
    sop_id: int,
    rev: int,
    current_user: dict = Depends(get_current_user),
):
    """Restore a historical revision as the current version."""
    revision = db.get_sop_revision(sop_id, rev)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    sop = db.get_sop(sop_id)
    current_revision = sop.get("revision", 1) or 1
    if isinstance(current_revision, str):
        current_revision = int(current_revision) if current_revision else 1

    steps = db.list_sop_steps(sop_id)
    snapshot = json.dumps([dict(s) for s in steps], ensure_ascii=False, default=str)
    db.insert_sop_revision(sop_id, current_revision, snapshot)

    old_steps = json.loads(revision["steps_snapshot_json"])
    db.delete_sop_steps(sop_id)
    for step in old_steps:
        db.insert_sop_step(
            sop_id=sop_id,
            step_order=step.get("step_order", 0),
            title=step.get("title", ""),
            description=step.get("description", ""),
            application=step.get("application", ""),
            human_description=step.get("human_description", ""),
            machine_actions=step.get("machine_actions"),
        )

    new_revision = current_revision + 1
    db.update_sop_revision(sop_id, new_revision, status="draft")

    return {"ok": True, "revision": new_revision}
