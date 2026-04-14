"""Tests for users management API (admin only)."""

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
    db.insert_user(username="admin", password_hash=hash_password("admin123"),
                   display_name="Admin", role="admin", employee_id="E000")
    db.insert_user(username="emp", password_hash=hash_password("emp123"),
                   display_name="Employee", role="employee", employee_id="E001")
    return TestClient(app)


def _admin_token(client) -> str:
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return resp.json()["access_token"]


def _emp_token(client) -> str:
    resp = client.post("/api/auth/login", json={"username": "emp", "password": "emp123"})
    return resp.json()["access_token"]


def test_list_users_as_admin(client):
    token = _admin_token(client)
    resp = client.get("/api/users/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_list_users_as_employee_forbidden(client):
    token = _emp_token(client)
    resp = client.get("/api/users/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_create_user_as_admin(client):
    token = _admin_token(client)
    resp = client.post("/api/users/", json={
        "username": "newuser", "display_name": "New User",
        "role": "employee", "password": "pass123"
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    assert resp.json()["username"] == "newuser"


def test_update_user_role(client):
    token = _admin_token(client)
    users = client.get("/api/users/", headers={"Authorization": f"Bearer {token}"}).json()
    emp_id = [u for u in users["users"] if u["username"] == "emp"][0]["id"]
    resp = client.put(f"/api/users/{emp_id}", json={"role": "manager"},
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "manager"


def test_delete_user(client):
    token = _admin_token(client)
    create = client.post("/api/users/", json={
        "username": "todelete", "display_name": "Del", "role": "employee"
    }, headers={"Authorization": f"Bearer {token}"})
    uid = create.json()["id"]
    resp = client.delete(f"/api/users/{uid}",
                         headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
