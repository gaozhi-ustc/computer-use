"""Tests for sessions API."""

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

    # Create users
    db.insert_user(username="admin", password_hash=hash_password("admin123"),
                   display_name="Admin", role="admin", employee_id="E000")
    db.insert_user(username="emp1", password_hash=hash_password("emp123"),
                   display_name="Emp1", role="employee", employee_id="E001")
    db.insert_user(username="emp2", password_hash=hash_password("emp123"),
                   display_name="Emp2", role="employee", employee_id="E002")

    # Insert sample frames for two sessions
    for i in range(3):
        db.insert_frame({"employee_id": "E001", "session_id": "sess-a",
                         "frame_index": i, "timestamp": 1712856000.0 + i*15,
                         "application": "chrome.exe", "user_action": f"action {i}"})
    for i in range(2):
        db.insert_frame({"employee_id": "E002", "session_id": "sess-b",
                         "frame_index": i, "timestamp": 1712856100.0 + i*15,
                         "application": "vscode.exe", "user_action": f"coding {i}"})

    return TestClient(app)


def _token(client, username, password) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    return resp.json()["access_token"]


def test_admin_sees_all_sessions(client):
    token = _token(client, "admin", "admin123")
    resp = client.get("/api/sessions/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    session_ids = {s["session_id"] for s in data["sessions"]}
    assert session_ids == {"sess-a", "sess-b"}


def test_employee_sees_only_own_sessions(client):
    token = _token(client, "emp1", "emp123")
    resp = client.get("/api/sessions/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["sessions"][0]["session_id"] == "sess-a"
    assert data["sessions"][0]["frame_count"] == 3


def test_session_detail_returns_frames(client):
    token = _token(client, "admin", "admin123")
    resp = client.get("/api/sessions/sess-a", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-a"
    assert data["frame_count"] == 3
    assert len(data["frames"]) == 3


def test_employee_cannot_see_others_session(client):
    token = _token(client, "emp1", "emp123")
    resp = client.get("/api/sessions/sess-b", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_session_not_found(client):
    token = _token(client, "admin", "admin123")
    resp = client.get("/api/sessions/nonexistent", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404
