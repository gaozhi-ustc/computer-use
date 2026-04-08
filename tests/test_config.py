"""Tests for configuration loading."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from workflow_recorder.config import AppConfig, AnalysisConfig, load_config


def test_default_config():
    config = AppConfig()
    assert config.capture.interval_seconds == 3.0
    assert config.analysis.model == "gpt-4o"
    assert config.session.max_duration_seconds == 3600.0


def test_base_url_default_empty():
    config = AppConfig()
    assert config.analysis.base_url == ""


def test_base_url_set():
    config = AnalysisConfig(base_url="https://proxy.example.com/v1")
    assert config.base_url == "https://proxy.example.com/v1"


def test_load_config_from_yaml():
    data = {
        "capture": {"interval_seconds": 5},
        "analysis": {"model": "gpt-4o-mini"},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        config = load_config(f.name)

    os.unlink(f.name)
    assert config.capture.interval_seconds == 5.0
    assert config.analysis.model == "gpt-4o-mini"
    # Defaults preserved
    assert config.session.max_duration_seconds == 3600.0


def test_load_config_from_toml(tmp_path):
    toml_content = """
[capture]
interval_seconds = 7.0

[analysis]
model = "gpt-4o-mini"
base_url = "https://proxy.example.com/v1"
"""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text(toml_content)
    config = load_config(toml_file)

    assert config.capture.interval_seconds == 7.0
    assert config.analysis.model == "gpt-4o-mini"
    assert config.analysis.base_url == "https://proxy.example.com/v1"
    # Defaults preserved
    assert config.session.max_duration_seconds == 3600.0


def test_env_var_interpolation(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test-123")
    data = {"analysis": {"openai_api_key": "${TEST_API_KEY}"}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        f.flush()
        config = load_config(f.name)

    os.unlink(f.name)
    assert config.analysis.openai_api_key == "sk-test-123"


def test_load_config_none_returns_defaults():
    config = load_config(None)
    assert config.capture.interval_seconds == 3.0
    assert config.analysis.openai_api_key == ""
