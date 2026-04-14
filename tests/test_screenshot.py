"""Tests for screenshot capture (mocked mss)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from workflow_recorder.capture.screenshot import CaptureResult, capture_screenshot


class FakeSctGrab:
    """Simulates mss screenshot raw data."""
    def __init__(self, width=800, height=600):
        self.width = width
        self.height = height
        # 3 bytes per pixel (RGB)
        self.rgb = bytes([200, 200, 200] * width * height)


@pytest.fixture
def mock_mss():
    """Mock the mss.mss() context manager."""
    mock_sct = MagicMock()
    mock_sct.monitors = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},  # all
        {"left": 0, "top": 0, "width": 1920, "height": 1080},  # primary
    ]
    mock_sct.grab.return_value = FakeSctGrab(800, 600)

    with patch("workflow_recorder.capture.screenshot.mss.mss") as mock_mss_cls:
        mock_mss_cls.return_value.__enter__ = MagicMock(return_value=mock_sct)
        mock_mss_cls.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_sct


def test_capture_returns_result(mock_mss, tmp_path):
    result = capture_screenshot(output_dir=tmp_path, monitor=0)
    assert isinstance(result, CaptureResult)
    assert result.file_path.exists()
    assert result.width == 800
    assert result.height == 600
    assert result.monitor_index == 0


def test_capture_png_format(mock_mss, tmp_path):
    result = capture_screenshot(output_dir=tmp_path, image_format="png")
    assert result.file_path.suffix == ".png"
    img = Image.open(result.file_path)
    assert img.format == "PNG"


def test_capture_jpg_format(mock_mss, tmp_path):
    result = capture_screenshot(output_dir=tmp_path, image_format="jpg")
    assert result.file_path.suffix == ".jpg"
    img = Image.open(result.file_path)
    assert img.format == "JPEG"


def test_capture_downscale(mock_mss, tmp_path):
    result = capture_screenshot(output_dir=tmp_path, downscale_factor=0.5)
    assert result.width == 400
    assert result.height == 300


def test_capture_creates_output_dir(mock_mss, tmp_path):
    new_dir = tmp_path / "nested" / "dir"
    result = capture_screenshot(output_dir=new_dir)
    assert new_dir.is_dir()
    assert result.file_path.exists()


def test_capture_all_monitors(mock_mss, tmp_path):
    result = capture_screenshot(output_dir=tmp_path, monitor=-1)
    assert result.monitor_index == -1


def test_capture_includes_cursor_and_focus_fields(tmp_path):
    from workflow_recorder.capture.screenshot import capture_screenshot
    result = capture_screenshot(output_dir=tmp_path, monitor=0,
                                 image_format="png", downscale_factor=1.0)
    # On Windows there's always a cursor; on other OS fields may be defaults (-1 / None)
    assert hasattr(result, "cursor_x")
    assert hasattr(result, "cursor_y")
    assert hasattr(result, "focus_rect")
    assert isinstance(result.cursor_x, int)
    assert isinstance(result.cursor_y, int)
    assert result.focus_rect is None or (isinstance(result.focus_rect, list) and len(result.focus_rect) == 4)
