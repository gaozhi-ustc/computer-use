"""Shared test fixtures and pytest plugins."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from workflow_recorder.config import AppConfig, load_config


# ---------------------------------------------------------------------------
# CLI flags for integration / e2e tests
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False,
                     help="Run integration tests that call real GPT API")
    parser.addoption("--run-e2e", action="store_true", default=False,
                     help="Run end-to-end pipeline tests")


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason="need --run-integration to run")
    skip_e2e = pytest.mark.skip(reason="need --run-e2e to run")
    for item in items:
        if "integration" in item.keywords and not config.getoption("--run-integration"):
            item.add_marker(skip_integration)
        if "e2e" in item.keywords and not config.getoption("--run-e2e"):
            item.add_marker(skip_e2e)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def default_config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def integration_config() -> AppConfig:
    """Load config from config.test.toml for integration tests."""
    toml_path = PROJECT_ROOT / "config.test.toml"
    if not toml_path.exists():
        pytest.skip("config.test.toml not found")
    return load_config(toml_path)


@pytest.fixture
def test_image(tmp_path) -> Path:
    """Generate a synthetic screenshot with colored rectangles and text."""
    img = Image.new("RGB", (800, 600), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)

    # Title bar
    draw.rectangle([0, 0, 800, 30], fill=(0, 120, 215))
    draw.text((10, 5), "Test Application - Document.txt", fill="white")

    # Menu bar
    draw.rectangle([0, 30, 800, 55], fill=(245, 245, 245))
    for i, label in enumerate(["File", "Edit", "View", "Help"]):
        draw.text((10 + i * 60, 35), label, fill="black")

    # Content area
    draw.rectangle([10, 60, 790, 560], fill="white", outline=(200, 200, 200))
    draw.text((20, 70), "Hello World - this is a test document.", fill="black")
    draw.text((20, 90), "Line 2: some more content here.", fill="black")

    # Button
    draw.rectangle([650, 520, 780, 550], fill=(0, 120, 215), outline=(0, 90, 180))
    draw.text((685, 527), "Save", fill="white")

    path = tmp_path / "test_screenshot.png"
    img.save(path, "PNG")
    return path


@pytest.fixture
def test_image_alt(tmp_path) -> Path:
    """Generate a different screenshot (for deduplication tests)."""
    img = Image.new("RGB", (800, 600), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 800, 30], fill=(50, 50, 50))
    draw.text((10, 5), "Another App - Settings", fill="white")
    draw.rectangle([100, 100, 700, 500], fill=(60, 60, 60))
    draw.text((120, 120), "Dark theme settings panel", fill=(200, 200, 200))

    path = tmp_path / "test_screenshot_alt.png"
    img.save(path, "PNG")
    return path


@pytest.fixture
def tmp_output_dir(tmp_path) -> Path:
    """Temporary output directory for writer tests."""
    d = tmp_path / "output"
    d.mkdir()
    return d
