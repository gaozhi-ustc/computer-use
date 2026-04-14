"""Tests for stats/dashboard/audit API endpoints."""

import pytest
from datetime import datetime, timezone, timedelta
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
    db.insert_user(
        username="admin",
        password_hash=hash_password("admin123"),
        display_name="Admin",
        role="admin",
        employee_id="E000",
    )
    db.insert_user(
        username="emp",
        password_hash=hash_password("emp123"),
        display_name="Employee",
        role="employee",
        employee_id="E001",
    )

    # Seed some frame data
    now = datetime.now(timezone.utc)
    for i in range(5):
        db.insert_frame(
            {
                "employee_id": "E001",
                "session_id": "sess-001",
                "frame_index": i,
                "timestamp": (now - timedelta(minutes=5 * i)).isoformat(),
                "application": "Chrome" if i % 2 == 0 else "VSCode",
                "window_title": "Test Window",
                "user_action": f"click button {i}",
                "text_content": f"some text content {i}",
                "confidence": 0.8 + i * 0.02,
            }
        )
    for i in range(3):
        db.insert_frame(
            {
                "employee_id": "E000",
                "session_id": "sess-002",
                "frame_index": i,
                "timestamp": (now - timedelta(minutes=10 * i)).isoformat(),
                "application": "Excel",
                "window_title": "Spreadsheet",
                "user_action": f"type data {i}",
                "text_content": f"spreadsheet data {i}",
                "confidence": 0.9,
            }
        )

    return TestClient(app)


def _admin_token(client) -> str:
    resp = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    return resp.json()["access_token"]


def _emp_token(client) -> str:
    resp = client.post(
        "/api/auth/login", json={"username": "emp", "password": "emp123"}
    )
    return resp.json()["access_token"]


def test_dashboard_summary(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "today_frames" in data
    assert "today_sessions" in data
    assert "draft_sops" in data
    assert "published_sops" in data
    assert "total_employees" in data
    assert data["total_employees"] == 2


def test_dashboard_recent_sessions(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/dashboard/recent-sessions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_frame_stats_app_usage(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "app_usage" in data
    assert "heatmap" in data
    assert "daily" in data
    apps = {item["application"]: item["frame_count"] for item in data["app_usage"]}
    assert "Chrome" in apps
    assert "VSCode" in apps
    assert "Excel" in apps


def test_frame_stats_heatmap(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    heatmap = data["heatmap"]
    assert isinstance(heatmap, list)
    assert len(heatmap) > 0
    for item in heatmap:
        assert "hour" in item
        assert "weekday" in item
        assert "count" in item


def test_frame_stats_daily(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = resp.json()
    daily = data["daily"]
    assert isinstance(daily, list)
    assert len(daily) >= 1
    for item in daily:
        assert "date" in item
        assert "frame_count" in item
        assert "app_count" in item
        assert "first_at" in item
        assert "last_at" in item


def test_frame_stats_with_employee_filter(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/stats?employee_id=E001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    apps = {item["application"]: item["frame_count"] for item in data["app_usage"]}
    assert "Excel" not in apps  # Excel only belongs to E000


def test_search_frames_keyword(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/search?keyword=click",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["count"] == 5
    for frame in data["frames"]:
        assert "click" in frame["user_action"]


def test_search_frames_application(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/search?application=Excel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    for frame in data["frames"]:
        assert frame["application"] == "Excel"


def test_search_frames_min_confidence(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/search?min_confidence=0.85",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    for frame in data["frames"]:
        assert frame["confidence"] >= 0.85


def test_search_frames_pagination(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/search?limit=2&offset=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["total"] == 8  # 5 + 3 total frames


def test_export_csv(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/export",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "frames_export.csv" in resp.headers["content-disposition"]
    lines = resp.text.strip().split("\n")
    assert len(lines) >= 2  # header + at least 1 data row
    header = lines[0]
    assert "employee_id" in header
    assert "application" in header


def test_export_csv_with_filter(client):
    token = _admin_token(client)
    resp = client.get(
        "/api/frames/export?employee_id=E001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    lines = resp.text.strip().split("\n")
    # header + 5 E001 frames
    assert len(lines) == 6


def test_employee_sees_only_own_data(client):
    token = _emp_token(client)
    resp = client.get(
        "/api/frames/search",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # employee E001 should only see their own 5 frames
    assert data["total"] == 5
    for frame in data["frames"]:
        assert frame["employee_id"] == "E001"


def test_employee_dashboard_summary(client):
    token = _emp_token(client)
    resp = client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_employees"] == 1  # only sees self


def test_unauthenticated_returns_401(client):
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 401
    resp2 = client.get("/api/frames/search")
    assert resp2.status_code == 401
