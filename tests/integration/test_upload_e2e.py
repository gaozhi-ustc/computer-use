"""E2E smoke test: upload → row pending → retrieve image → admin retry.

Does not require Phase 2 (AnalysisPool). Exercises the full Phase 1
surface area through the public API.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_upload_and_query_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "imgs"))
    monkeypatch.setenv("WORKFLOW_SERVER_KEY", "sk-test")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "secret")

    from server.app import app
    from server import db
    from server.auth import hash_password
    db.init_db()
    db.insert_user(username="admin", password_hash=hash_password("p"),
                   display_name="A", role="admin", employee_id="E000")

    client = TestClient(app)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDAT\x08\x99c\xfa\x0f\x00\x00\x01\x01\x00\x01"
        b"\xae\xf0\x18\x95\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    # 1. Upload
    up = client.post(
        "/frames/upload",
        headers={"X-API-Key": "sk-test"},
        data={"employee_id": "E001", "session_id": "sess-1",
              "frame_index": "1", "timestamp": "1712856000.0",
              "cursor_x": "123", "cursor_y": "456",
              "focus_rect": "[10, 20, 100, 200]"},
        files={"image": ("1.png", png, "image/png")},
    )
    assert up.status_code == 200
    frame_id = up.json()["id"]

    # 2. DB has the uploaded row with the OS coords
    frame = db.get_frame(frame_id)
    assert frame["analysis_status"] == "uploaded"
    assert frame["cursor_x"] == 123
    assert frame["cursor_y"] == 456
    assert frame["focus_rect"] == [10, 20, 100, 200]

    # 3. Queue stats
    token = client.post("/api/auth/login",
                       json={"username": "admin", "password": "p"}).json()["access_token"]
    stats = client.get("/api/frames/queue",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert stats["uploaded"] == 1

    # 4. Image retrieval
    img_resp = client.get(f"/api/frames/{frame_id}/image",
                          headers={"Authorization": f"Bearer {token}"})
    assert img_resp.status_code == 200
    assert img_resp.content.startswith(b"\x89PNG")

    # 5. Simulate analysis failure + admin retry
    db.mark_frame_failed(frame_id, "simulated")
    retry_resp = client.post(f"/api/frames/{frame_id}/retry",
                             headers={"Authorization": f"Bearer {token}"})
    assert retry_resp.status_code == 200
    assert db.get_frame(frame_id)["analysis_status"] == "pending"
