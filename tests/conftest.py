"""Shared test fixtures."""

from __future__ import annotations

import pytest

from workflow_recorder.config import AppConfig


@pytest.fixture
def default_config() -> AppConfig:
    return AppConfig()
