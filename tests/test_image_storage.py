"""Tests for server/image_storage.py — save uploaded PNG bytes to filesystem."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_save_image_creates_expected_path(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path))
    from server.image_storage import save_image
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake but with PNG magic
    path = save_image(
        employee_id="E001",
        session_id="sess-abc",
        frame_index=42,
        image_bytes=png_bytes,
        received_at_iso="2026-04-14T10:30:00+00:00",
    )
    expected = tmp_path / "E001" / "2026-04-14" / "sess-abc" / "42.png"
    assert path == expected.resolve()
    assert expected.exists()
    assert expected.read_bytes() == png_bytes


def test_save_image_creates_nested_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path))
    from server.image_storage import save_image
    save_image(
        employee_id="E1",
        session_id="s1",
        frame_index=1,
        image_bytes=b"\x89PNG",
        received_at_iso="2026-01-01T00:00:00+00:00",
    )
    assert (tmp_path / "E1" / "2026-01-01" / "s1").is_dir()


def test_save_image_default_dir(tmp_path, monkeypatch):
    # Run from tmp_path as cwd so default './frame_images' lands there
    monkeypatch.delenv("WORKFLOW_IMAGE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    from server.image_storage import save_image
    path = save_image(
        employee_id="E1", session_id="s", frame_index=7,
        image_bytes=b"\x89PNG",
        received_at_iso="2026-01-01T00:00:00+00:00",
    )
    assert (tmp_path / "frame_images" / "E1" / "2026-01-01" / "s" / "7.png").exists()


def test_save_image_sanitizes_path_segments(tmp_path, monkeypatch):
    """Should reject path-traversal attempts in employee_id / session_id."""
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path))
    from server.image_storage import save_image
    with pytest.raises(ValueError, match="invalid"):
        save_image(
            employee_id="../../etc/passwd",
            session_id="s", frame_index=1,
            image_bytes=b"\x89PNG",
            received_at_iso="2026-01-01T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="invalid"):
        save_image(
            employee_id="E1",
            session_id="../evil", frame_index=1,
            image_bytes=b"\x89PNG",
            received_at_iso="2026-01-01T00:00:00+00:00",
        )


def test_image_base_dir_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "custom"))
    from server.image_storage import image_base_dir
    assert image_base_dir() == (tmp_path / "custom").resolve()
