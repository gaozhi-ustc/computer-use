"""Tests for SOP feedback / revision / regeneration API."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from server import db
from server.app import app


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_DISABLE_ANALYSIS_POOL", "1")
    monkeypatch.setenv("WORKFLOW_DISABLE_SESSION_FINALIZER", "1")
    db.init_db()
    from server.auth import hash_password
    db.insert_user(username="admin", password_hash=hash_password("test"),
                   display_name="Admin", role="admin")
    return tmp_path


@pytest.fixture
def authed_client(fresh_db):
    client = TestClient(app)
    resp = client.post("/api/auth/login",
                       json={"username": "admin", "password": "test"})
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def _create_sop_with_steps(client) -> int:
    resp = client.post("/api/sops/", json={"title": "Test SOP"})
    sop_id = resp.json()["id"]
    client.post(f"/api/sops/{sop_id}/steps/",
                json={"title": "Step 1", "description": "Do thing 1",
                      "step_order": 1})
    client.post(f"/api/sops/{sop_id}/steps/",
                json={"title": "Step 2", "description": "Do thing 2",
                      "step_order": 2})
    return sop_id


def test_submit_feedback(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    resp = authed_client.post(
        f"/api/sops/{sop_id}/feedback",
        json={"feedback_text": "Step 1 needs more detail", "scope": "step:1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback_id"] > 0
    assert data["new_revision"] == 2


def test_list_revisions(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    authed_client.post(f"/api/sops/{sop_id}/feedback",
                       json={"feedback_text": "Fix it", "scope": "full"})
    resp = authed_client.get(f"/api/sops/{sop_id}/revisions")
    assert resp.status_code == 200
    revisions = resp.json()
    assert len(revisions) >= 1
    assert revisions[0]["revision"] == 1


def test_get_revision_snapshot(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    authed_client.post(f"/api/sops/{sop_id}/feedback",
                       json={"feedback_text": "Fix it", "scope": "full"})
    resp = authed_client.get(f"/api/sops/{sop_id}/revisions/1")
    assert resp.status_code == 200
    data = resp.json()
    steps = json.loads(data["steps_snapshot_json"])
    assert len(steps) == 2


def test_get_sop_status(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    resp = authed_client.get(f"/api/sops/{sop_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "draft"
    assert resp.json()["revision"] == 1


def test_get_sop_status_after_feedback(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    authed_client.post(f"/api/sops/{sop_id}/feedback",
                       json={"feedback_text": "Regen please", "scope": "full"})
    resp = authed_client.get(f"/api/sops/{sop_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "regenerating"
    assert resp.json()["revision"] == 2


def test_restore_revision(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    # Create feedback to generate revision 1 snapshot
    authed_client.post(f"/api/sops/{sop_id}/feedback",
                       json={"feedback_text": "Fix it", "scope": "full"})
    # Restore revision 1
    resp = authed_client.post(f"/api/sops/{sop_id}/revisions/1/restore")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["revision"] == 3  # was 2 after feedback, now 3 after restore

    # Check that steps were restored
    resp = authed_client.get(f"/api/sops/{sop_id}")
    steps = resp.json()["steps"]
    assert len(steps) == 2
    assert steps[0]["title"] == "Step 1"


def test_feedback_not_found(authed_client):
    resp = authed_client.post(
        "/api/sops/99999/feedback",
        json={"feedback_text": "Fix it", "scope": "full"},
    )
    assert resp.status_code == 404


def test_revision_not_found(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    resp = authed_client.get(f"/api/sops/{sop_id}/revisions/999")
    assert resp.status_code == 404


def test_restore_revision_not_found(authed_client):
    sop_id = _create_sop_with_steps(authed_client)
    resp = authed_client.post(f"/api/sops/{sop_id}/revisions/999/restore")
    assert resp.status_code == 404
