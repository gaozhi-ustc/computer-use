"""Tests for storage utilities."""

from __future__ import annotations

from pathlib import Path

from workflow_recorder.utils.storage import cleanup_dir, ensure_dir, get_temp_capture_dir


def test_ensure_dir_creates(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    result = ensure_dir(target)
    assert result == target
    assert target.is_dir()


def test_ensure_dir_existing(tmp_path):
    result = ensure_dir(tmp_path)
    assert result == tmp_path
    assert tmp_path.is_dir()


def test_cleanup_dir(tmp_path):
    d = tmp_path / "to_remove"
    d.mkdir()
    (d / "file.txt").write_text("hello")
    cleanup_dir(d)
    assert not d.exists()


def test_cleanup_dir_nonexistent(tmp_path):
    d = tmp_path / "does_not_exist"
    # Should not raise
    cleanup_dir(d)


def test_get_temp_capture_dir():
    result = get_temp_capture_dir()
    assert "captures" in str(result)


def test_get_temp_capture_dir_custom_base(tmp_path):
    result = get_temp_capture_dir(base_dir=str(tmp_path))
    assert str(tmp_path) in str(result)
