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


def test_load_config_from_json_with_gpt_preset(tmp_path):
    import json
    data = {
        "model_presets": {
            "gpt": {
                "name": "GPT-4o",
                "model": "gpt-4o",
                "base_url": "https://api.aicodemirror.com/api/codex/backend-api/codex/v1",
                "max_tokens": 1000,
            },
            "qwen": {
                "name": "Qwen-VL-Plus",
                "model": "qwen-vl-plus",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "max_tokens": 1000,
            },
        },
        "active_preset": "gpt",
        "analysis": {
            "openai_api_key": "sk-test-gpt",
        },
    }
    json_file = tmp_path / "model_config.json"
    json_file.write_text(json.dumps(data))
    config = load_config(json_file)

    assert config.analysis.model == "gpt-4o"
    assert config.analysis.base_url == "https://api.aicodemirror.com/api/codex/backend-api/codex/v1"
    assert config.analysis.openai_api_key == "sk-test-gpt"
    assert config.analysis.max_tokens == 1000


def test_load_config_from_json_with_qwen_preset(tmp_path):
    import json
    data = {
        "model_presets": {
            "gpt": {
                "name": "GPT-4o",
                "model": "gpt-4o",
                "base_url": "https://api.aicodemirror.com/v1",
            },
            "qwen": {
                "name": "Qwen3.5-Plus",
                "model": "qwen3.5-plus",
                "base_url": "https://coding.dashscope.aliyuncs.com/v1",
            },
        },
        "active_preset": "qwen",
        "analysis": {
            "openai_api_key": "sk-test-qwen",
        },
    }
    json_file = tmp_path / "model_config.json"
    json_file.write_text(json.dumps(data))
    config = load_config(json_file)

    assert config.analysis.model == "qwen3.5-plus"
    assert "dashscope" in config.analysis.base_url
    assert config.analysis.openai_api_key == "sk-test-qwen"


def test_load_config_json_without_presets(tmp_path):
    """JSON without model_presets should work as plain config."""
    import json
    data = {
        "analysis": {
            "model": "gpt-4o-mini",
            "openai_api_key": "sk-plain",
        },
    }
    json_file = tmp_path / "config.json"
    json_file.write_text(json.dumps(data))
    config = load_config(json_file)

    assert config.analysis.model == "gpt-4o-mini"
    assert config.analysis.openai_api_key == "sk-plain"
