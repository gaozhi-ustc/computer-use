"""Tests for SOP CRUD API."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WORKFLOW_DISABLE_ANALYSIS_POOL", "1")
    from server.app import app
    from server import db
    from server.auth import hash_password

    db.init_db()

    # Create users with different roles
    db.insert_user(
        username="admin", password_hash=hash_password("admin123"),
        display_name="Admin", role="admin", employee_id="E000",
    )
    db.insert_user(
        username="manager", password_hash=hash_password("mgr123"),
        display_name="Manager", role="manager", employee_id="E100",
    )
    db.insert_user(
        username="emp1", password_hash=hash_password("emp123"),
        display_name="Emp1", role="employee", employee_id="E001",
    )

    # Insert sample frames for a session (used by generate endpoint)
    for i in range(5):
        app_name = "chrome.exe" if i < 3 else "vscode.exe"
        db.insert_frame({
            "employee_id": "E001", "session_id": "sess-abc",
            "frame_index": i, "timestamp": 1712856000.0 + i * 15,
            "application": app_name,
            "user_action": f"action {i}",
            "confidence": 0.8 + i * 0.02,
        })

    return TestClient(app)


def _token(client, username, password) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _admin_headers(client) -> dict:
    return {"Authorization": f"Bearer {_token(client, 'admin', 'admin123')}"}


def _manager_headers(client) -> dict:
    return {"Authorization": f"Bearer {_token(client, 'manager', 'mgr123')}"}


def _emp_headers(client) -> dict:
    return {"Authorization": f"Bearer {_token(client, 'emp1', 'emp123')}"}


# --- test_create_sop ---

def test_create_sop(client):
    headers = _admin_headers(client)
    resp = client.post("/api/sops/", json={
        "title": "How to file expense",
        "description": "Step by step guide",
        "tags": ["finance", "onboarding"],
    }, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "How to file expense"
    assert data["status"] == "draft"
    assert data["tags"] == ["finance", "onboarding"]
    assert data["id"] > 0


# --- test_list_sops_by_status ---

def test_list_sops_by_status(client):
    headers = _admin_headers(client)
    # Create two SOPs: one draft, one published
    client.post("/api/sops/", json={"title": "Draft SOP"}, headers=headers)
    resp2 = client.post("/api/sops/", json={"title": "Published SOP"}, headers=headers)
    sop_id = resp2.json()["id"]
    # Transition to in_review then published
    client.put(f"/api/sops/{sop_id}/status", json={"status": "in_review"}, headers=headers)
    client.put(f"/api/sops/{sop_id}/status", json={"status": "published"}, headers=headers)

    # List only draft
    resp = client.get("/api/sops/?status=draft", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert all(s["status"] == "draft" for s in data["sops"])

    # List only published
    resp = client.get("/api/sops/?status=published", headers=headers)
    data = resp.json()
    assert all(s["status"] == "published" for s in data["sops"])
    assert data["total"] >= 1


# --- test_sop_detail_includes_steps ---

def test_sop_detail_includes_steps(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "With steps"}, headers=headers).json()
    sop_id = sop["id"]

    # Add steps
    client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Open browser", "step_order": 1,
        "application": "chrome.exe", "action_type": "click",
    }, headers=headers)
    client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Navigate to URL", "step_order": 2,
        "application": "chrome.exe", "action_type": "type",
    }, headers=headers)

    resp = client.get(f"/api/sops/{sop_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "With steps"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["title"] == "Open browser"
    assert data["steps"][1]["title"] == "Navigate to URL"


# --- test_add_and_reorder_steps ---

def test_add_and_reorder_steps(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "Reorder test"}, headers=headers).json()
    sop_id = sop["id"]

    s1 = client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Step A", "step_order": 1,
    }, headers=headers).json()
    s2 = client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Step B", "step_order": 2,
    }, headers=headers).json()
    s3 = client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Step C", "step_order": 3,
    }, headers=headers).json()

    # Reorder: C, A, B
    resp = client.put(f"/api/sops/{sop_id}/steps/reorder", json={
        "step_ids": [s3["id"], s1["id"], s2["id"]],
    }, headers=headers)
    assert resp.status_code == 200

    # Verify new order
    detail = client.get(f"/api/sops/{sop_id}", headers=headers).json()
    titles = [s["title"] for s in detail["steps"]]
    assert titles == ["Step C", "Step A", "Step B"]


# --- test_status_transition_draft_to_published ---

def test_status_transition_draft_to_published(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "Status test"}, headers=headers).json()
    sop_id = sop["id"]
    assert sop["status"] == "draft"

    # draft -> in_review
    resp = client.put(f"/api/sops/{sop_id}/status", json={"status": "in_review"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_review"

    # in_review -> published
    resp = client.put(f"/api/sops/{sop_id}/status", json={"status": "published"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"
    assert resp.json().get("published_at") is not None

    # Cannot go published -> draft (invalid transition)
    resp = client.put(f"/api/sops/{sop_id}/status", json={"status": "draft"}, headers=headers)
    assert resp.status_code == 400


# --- test_employee_sees_only_published ---

def test_employee_sees_only_published(client):
    admin_headers = _admin_headers(client)
    emp_headers = _emp_headers(client)

    # Create a draft and a published SOP as admin
    client.post("/api/sops/", json={"title": "Secret draft"}, headers=admin_headers)
    resp2 = client.post("/api/sops/", json={"title": "Public SOP"}, headers=admin_headers)
    sop_id = resp2.json()["id"]
    client.put(f"/api/sops/{sop_id}/status", json={"status": "in_review"}, headers=admin_headers)
    client.put(f"/api/sops/{sop_id}/status", json={"status": "published"}, headers=admin_headers)

    # Employee lists SOPs — should only see published
    resp = client.get("/api/sops/", headers=emp_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert all(s["status"] == "published" for s in data["sops"])
    assert any(s["title"] == "Public SOP" for s in data["sops"])
    # Should not see the draft
    assert not any(s["title"] == "Secret draft" for s in data["sops"])


# --- test_generate_steps_from_session ---

def test_generate_steps_from_session(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={
        "title": "Auto-generated SOP",
        "source_session_id": "sess-abc",
        "source_employee_id": "E001",
    }, headers=headers).json()
    sop_id = sop["id"]

    resp = client.post(f"/api/sops/{sop_id}/generate", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # The 5 frames should group into 2 steps (3 chrome + 2 vscode)
    assert data["steps_created"] == 2

    # Verify steps in detail
    detail = client.get(f"/api/sops/{sop_id}", headers=headers).json()
    assert len(detail["steps"]) == 2
    assert detail["steps"][0]["application"] == "chrome.exe"
    assert detail["steps"][1]["application"] == "vscode.exe"


# --- test_export_markdown ---

def test_export_markdown(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "MD Export Test"}, headers=headers).json()
    sop_id = sop["id"]

    # Add a step
    client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Open app", "step_order": 1, "description": "Launch the application",
        "application": "notepad.exe", "action_type": "click",
    }, headers=headers)

    resp = client.get(f"/api/sops/{sop_id}/export/md", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    body = resp.text
    assert "# MD Export Test" in body
    assert "Open app" in body
    assert "notepad.exe" in body


# --- test_export_json (computer-use format) ---

def test_export_json(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "JSON Export"}, headers=headers).json()
    sop_id = sop["id"]

    client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Click button", "step_order": 1,
        "application": "chrome.exe", "action_type": "click",
        "action_detail": {"target": "Submit", "coordinates": [100, 200]},
        "confidence": 0.9,
    }, headers=headers)

    resp = client.get(f"/api/sops/{sop_id}/export/json", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # Should match Workflow schema shape
    assert "$schema" in data or "schema_version" in data
    assert "metadata" in data
    assert "steps" in data
    assert len(data["steps"]) == 1
    step = data["steps"][0]
    assert step["application"]["process_name"] == "chrome.exe"


# --- test_delete_sop_cascades_steps ---

def test_delete_sop_cascades_steps(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "To delete"}, headers=headers).json()
    sop_id = sop["id"]

    # Add steps
    client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Step 1", "step_order": 1,
    }, headers=headers)
    client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Step 2", "step_order": 2,
    }, headers=headers)

    # Delete SOP
    resp = client.delete(f"/api/sops/{sop_id}", headers=headers)
    assert resp.status_code == 204

    # SOP should be gone
    resp = client.get(f"/api/sops/{sop_id}", headers=headers)
    assert resp.status_code == 404


# --- Additional edge case tests ---

def test_update_sop_metadata(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "Original"}, headers=headers).json()
    sop_id = sop["id"]

    resp = client.put(f"/api/sops/{sop_id}", json={
        "title": "Updated Title",
        "description": "New description",
        "tags": ["updated"],
    }, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated Title"
    assert data["description"] == "New description"
    assert data["tags"] == ["updated"]


def test_update_step(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "Step edit"}, headers=headers).json()
    sop_id = sop["id"]
    step = client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "Original step", "step_order": 1,
    }, headers=headers).json()

    resp = client.put(f"/api/sops/{sop_id}/steps/{step['id']}", json={
        "title": "Edited step", "description": "Now with description",
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Edited step"


def test_delete_step(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "Del step"}, headers=headers).json()
    sop_id = sop["id"]
    step = client.post(f"/api/sops/{sop_id}/steps/", json={
        "title": "To remove", "step_order": 1,
    }, headers=headers).json()

    resp = client.delete(f"/api/sops/{sop_id}/steps/{step['id']}", headers=headers)
    assert resp.status_code == 204

    detail = client.get(f"/api/sops/{sop_id}", headers=headers).json()
    assert len(detail["steps"]) == 0


def test_employee_cannot_create_sop_for_others(client):
    """Employee can create draft SOPs (their own), but cannot set arbitrary created_by."""
    emp_headers = _emp_headers(client)
    resp = client.post("/api/sops/", json={
        "title": "My workflow",
    }, headers=emp_headers)
    # Employee can create a draft SOP
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "draft"
    assert data["created_by"] == "emp1"


def test_employee_cannot_edit_sop(client):
    admin_headers = _admin_headers(client)
    emp_headers = _emp_headers(client)
    sop = client.post("/api/sops/", json={"title": "Admin SOP"}, headers=admin_headers).json()
    resp = client.put(f"/api/sops/{sop['id']}", json={"title": "Hacked"}, headers=emp_headers)
    assert resp.status_code == 403


def test_employee_cannot_delete_sop(client):
    admin_headers = _admin_headers(client)
    emp_headers = _emp_headers(client)
    sop = client.post("/api/sops/", json={"title": "Protected"}, headers=admin_headers).json()
    resp = client.delete(f"/api/sops/{sop['id']}", headers=emp_headers)
    assert resp.status_code == 403


def test_generate_without_source_session_fails(client):
    headers = _admin_headers(client)
    sop = client.post("/api/sops/", json={"title": "No source"}, headers=headers).json()
    resp = client.post(f"/api/sops/{sop['id']}/generate", headers=headers)
    assert resp.status_code == 400
