"""Tests for auth API endpoints."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret")
    from server.app import app
    from server import db
    db.init_db()
    from server.auth import hash_password
    db.insert_user(
        username="admin",
        password_hash=hash_password("admin123"),
        display_name="Admin",
        role="admin",
        employee_id="E000",
    )
    return TestClient(app)


def test_login_success(client):
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "wrong"
    })
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/api/auth/login", json={
        "username": "nobody", "password": "x"
    })
    assert resp.status_code == 401


def test_me_with_valid_token(client):
    login = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    token = login.json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"


def test_me_without_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_refresh_token(client):
    login = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    refresh = login.json()["refresh_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    assert "access_token" in resp.json()
