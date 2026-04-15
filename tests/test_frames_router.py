"""Tests for /frames/upload and dashboard frame-related admin endpoints."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "frame_images"))
    monkeypatch.setenv("WORKFLOW_SERVER_KEY", "test-upload-key")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WORKFLOW_DISABLE_ANALYSIS_POOL", "1")
    from server.app import app
    from server import db
    from server.auth import hash_password
    db.init_db()
    db.insert_user(username="admin", password_hash=hash_password("admin123"),
                   display_name="Admin", role="admin", employee_id="E000")
    db.insert_user(username="emp", password_hash=hash_password("emp123"),
                   display_name="E", role="employee", employee_id="E001")
    return TestClient(app)


def _admin_token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return r.json()["access_token"]


def _emp_token(client):
    r = client.post("/api/auth/login", json={"username": "emp", "password": "emp123"})
    return r.json()["access_token"]


def _make_png_bytes() -> bytes:
    # Minimal 1x1 PNG (generated, not random bytes)
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDAT\x08\x99c\xfa\x0f\x00\x00\x01\x01\x00\x01"
        b"\xae\xf0\x18\x95\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_upload_frame_requires_api_key(client):
    r = client.post(
        "/frames/upload",
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "100", "cursor_y": "200", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    assert r.status_code == 401


def test_upload_frame_happy_path(client, tmp_path):
    r = client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "100", "cursor_y": "200",
              "focus_rect": "[50, 60, 250, 260]"},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] == 1
    # Verify image file written
    assert (tmp_path / "frame_images").exists()
    pngs = list((tmp_path / "frame_images").rglob("1.png"))
    assert len(pngs) == 1


def test_upload_frame_empty_focus_rect(client):
    r = client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "-1", "cursor_y": "-1", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    assert r.status_code == 200


def test_get_frame_image_requires_jwt(client):
    # First upload one
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    # Fetch without token
    r = client.get("/api/frames/1/image")
    assert r.status_code == 401


def test_get_frame_image_admin_can_read(client):
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    token = _admin_token(client)
    r = client.get("/api/frames/1/image",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")


def test_get_frame_image_employee_denied_other_employee(client):
    # Upload for E001
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    # emp user has employee_id=E001 so they can read their own
    emp_token = _emp_token(client)
    r = client.get("/api/frames/1/image",
                   headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 200

    # Upload for a different employee E999
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E999", "session_id": "s2",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    r = client.get("/api/frames/2/image",
                   headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 403


def test_get_frame_image_404_if_missing(client):
    token = _admin_token(client)
    r = client.get("/api/frames/999/image",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_retry_frame_requires_admin(client):
    # Upload one
    client.post(
        "/frames/upload",
        headers={"X-API-Key": "test-upload-key"},
        data={"employee_id": "E001", "session_id": "s1",
              "frame_index": "1", "timestamp": "100.0",
              "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
        files={"image": ("1.png", _make_png_bytes(), "image/png")},
    )
    # Mark failed
    from server import db
    db.mark_frame_failed(1, "test")

    # Employee can't retry
    emp_token = _emp_token(client)
    r = client.post("/api/frames/1/retry",
                    headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 403

    # Admin can
    admin_token = _admin_token(client)
    r = client.post("/api/frames/1/retry",
                    headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    assert db.get_frame(1)["analysis_status"] == "pending"
    assert db.get_frame(1)["analysis_attempts"] == 0


def test_queue_stats_endpoint(client):
    # Upload two pending frames
    for i in (1, 2):
        client.post(
            "/frames/upload",
            headers={"X-API-Key": "test-upload-key"},
            data={"employee_id": "E001", "session_id": "s1",
                  "frame_index": str(i), "timestamp": f"{i}.0",
                  "cursor_x": "0", "cursor_y": "0", "focus_rect": ""},
            files={"image": ("x.png", _make_png_bytes(), "image/png")},
        )

    admin_token = _admin_token(client)
    r = client.get("/api/frames/queue",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["uploaded"] == 2
    assert body["pending"] == 0
    assert body["running"] == 0
    assert body["done"] == 0
    assert body["failed"] == 0


def test_queue_stats_employee_denied(client):
    emp_token = _emp_token(client)
    r = client.get("/api/frames/queue",
                   headers={"Authorization": f"Bearer {emp_token}"})
    assert r.status_code == 403


def test_old_post_frames_returns_404(client):
    """Old JSON-push endpoint is deleted; returns 404 or 405 (not 200/422)."""
    r = client.post(
        "/frames",
        headers={"X-API-Key": "test-upload-key"},
        json={"employee_id": "E001", "session_id": "s", "frame_index": 1,
              "timestamp": 1.0},
    )
    # 404 = path gone entirely; 405 = path exists (GET /frames kept) but POST not registered
    assert r.status_code in (404, 405), f"Expected 404/405, got {r.status_code}"


def test_old_post_frames_batch_returns_404(client):
    r = client.post(
        "/frames/batch",
        headers={"X-API-Key": "test-upload-key"},
        json=[],
    )
    assert r.status_code in (404, 405), f"Expected 404/405, got {r.status_code}"


@pytest.fixture
def client_and_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "frame_images"))
    monkeypatch.setenv("WORKFLOW_SERVER_KEY", "")  # no key required
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WORKFLOW_DISABLE_ANALYSIS_POOL", "1")
    from server.app import app
    from server import db
    db.init_db()
    return TestClient(app), str(tmp_path / "test.db")


def test_upload_creates_session_record(client_and_db):
    """Upload should upsert a sessions row with status='active'."""
    client, db_path = client_and_db
    import io
    resp = client.post(
        "/frames/upload",
        data={
            "employee_id": "E001",
            "session_id": "sess-abc",
            "frame_index": "0",
            "timestamp": "1700000000.0",
        },
        files={"image": ("test.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100), "image/png")},
    )
    assert resp.status_code == 200
    from server import db
    sess = db.get_session("sess-abc")
    assert sess is not None
    assert sess["status"] == "active"
    assert sess["frame_count"] == 1


def test_upload_sets_status_uploaded(client_and_db):
    """New frames should have analysis_status='uploaded', not 'pending'."""
    client, db_path = client_and_db
    import io
    resp = client.post(
        "/frames/upload",
        data={
            "employee_id": "E001",
            "session_id": "sess-abc",
            "frame_index": "0",
            "timestamp": "1700000000.0",
        },
        files={"image": ("test.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100), "image/png")},
    )
    assert resp.status_code == 200
    frame_id = resp.json()["id"]
    from server import db
    frame = db.get_frame(frame_id)
    assert frame["analysis_status"] == "uploaded"
