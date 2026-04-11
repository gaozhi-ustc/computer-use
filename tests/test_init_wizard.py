"""Tests for the first-run initialization wizard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflow_recorder.config import AppConfig, load_config
from workflow_recorder.init_wizard import (
    _persist_to_json,
    needs_wizard,
)


# ---------------------------------------------------------------------------
# needs_wizard()
# ---------------------------------------------------------------------------


def test_needs_wizard_empty_config_requires_wizard():
    cfg = AppConfig()
    assert needs_wizard(cfg) is True


def test_needs_wizard_missing_api_key_only():
    cfg = AppConfig(employee_id="E001")
    assert needs_wizard(cfg) is True


def test_needs_wizard_missing_employee_only():
    cfg = AppConfig()
    cfg.analysis.openai_api_key = "sk-real"
    assert needs_wizard(cfg) is True


def test_needs_wizard_both_set_skips_wizard():
    cfg = AppConfig(employee_id="E001")
    cfg.analysis.openai_api_key = "sk-real"
    assert needs_wizard(cfg) is False


def test_needs_wizard_whitespace_values_still_require_wizard():
    cfg = AppConfig(employee_id="   ")
    cfg.analysis.openai_api_key = "\t  "
    assert needs_wizard(cfg) is True


# ---------------------------------------------------------------------------
# _persist_to_json — model_presets + active_preset layout
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def test_persist_to_json_writes_into_active_preset(tmp_path):
    cfg_path = tmp_path / "model_config.json"
    _write_json(cfg_path, {
        "model_presets": {
            "qwen": {
                "name": "Qwen3.5-Plus",
                "model": "qwen3.5-plus",
                "base_url": "https://coding.dashscope.aliyuncs.com/v1",
                "openai_api_key": "",
                "detail": "low",
            }
        },
        "active_preset": "qwen",
        "analysis": {"retry_attempts": 3},
    })

    cfg = load_config(cfg_path)
    cfg.employee_id = "E042"
    cfg.analysis.openai_api_key = "sk-sp-NEWKEY"

    _persist_to_json(cfg_path, cfg)
    written = json.loads(cfg_path.read_text(encoding="utf-8"))

    assert written["employee_id"] == "E042"
    assert written["model_presets"]["qwen"]["openai_api_key"] == "sk-sp-NEWKEY"
    # Non-target preset fields stay intact
    assert written["model_presets"]["qwen"]["model"] == "qwen3.5-plus"
    assert written["active_preset"] == "qwen"


def test_persist_to_json_roundtrip_skips_wizard_second_time(tmp_path):
    cfg_path = tmp_path / "model_config.json"
    _write_json(cfg_path, {
        "model_presets": {
            "qwen": {"name": "Qwen", "model": "qwen3.5-plus", "base_url": "x",
                     "openai_api_key": "", "detail": "low"}
        },
        "active_preset": "qwen",
    })

    cfg = load_config(cfg_path)
    cfg.employee_id = "E100"
    cfg.analysis.openai_api_key = "sk-test"
    _persist_to_json(cfg_path, cfg)

    reloaded = load_config(cfg_path)
    assert reloaded.employee_id == "E100"
    assert reloaded.analysis.openai_api_key == "sk-test"
    assert needs_wizard(reloaded) is False


def test_persist_to_json_all_presets_layout_fills_empty_keys(tmp_path):
    cfg_path = tmp_path / "model_config.json"
    _write_json(cfg_path, {
        "model_presets": {
            "qwen": {"name": "Qwen", "model": "qwen3.5-plus", "openai_api_key": ""},
            "gpt": {"name": "GPT", "model": "gpt-4o", "openai_api_key": "sk-existing"},
        },
        "active_preset": "__all__",
    })

    cfg = AppConfig(employee_id="E200")
    cfg.analysis.openai_api_key = "sk-sp-SHARED"
    _persist_to_json(cfg_path, cfg)

    written = json.loads(cfg_path.read_text(encoding="utf-8"))
    # Empty preset gets the new key
    assert written["model_presets"]["qwen"]["openai_api_key"] == "sk-sp-SHARED"
    # Already-populated preset is left alone
    assert written["model_presets"]["gpt"]["openai_api_key"] == "sk-existing"
    assert written["employee_id"] == "E200"


def test_persist_to_json_flat_layout_falls_back_to_analysis(tmp_path):
    cfg_path = tmp_path / "model_config.json"
    _write_json(cfg_path, {
        "analysis": {
            "openai_api_key": "",
            "model": "qwen3.5-plus",
        },
    })

    cfg = AppConfig(employee_id="E300")
    cfg.analysis.openai_api_key = "sk-flat"
    _persist_to_json(cfg_path, cfg)

    written = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert written["employee_id"] == "E300"
    assert written["analysis"]["openai_api_key"] == "sk-flat"
    # Untouched analysis fields survive
    assert written["analysis"]["model"] == "qwen3.5-plus"


def test_persist_to_json_creates_new_file_when_missing(tmp_path):
    cfg_path = tmp_path / "does_not_exist_yet.json"
    assert not cfg_path.exists()

    cfg = AppConfig(employee_id="E500")
    cfg.analysis.openai_api_key = "sk-fresh"
    cfg.analysis.model = "qwen3.5-plus"
    cfg.analysis.base_url = "https://coding.dashscope.aliyuncs.com/v1"

    _persist_to_json(cfg_path, cfg)

    assert cfg_path.exists()
    written = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert written["employee_id"] == "E500"
    assert written["analysis"]["openai_api_key"] == "sk-fresh"
    assert written["analysis"]["model"] == "qwen3.5-plus"


def test_persist_to_json_rejects_non_json_extension(tmp_path):
    cfg_path = tmp_path / "model_config.yaml"
    cfg_path.write_text("analysis:\n  openai_api_key: ''\n", encoding="utf-8")

    cfg = AppConfig(employee_id="E900")
    cfg.analysis.openai_api_key = "sk-no"
    with pytest.raises(ValueError, match="can only persist to .json"):
        _persist_to_json(cfg_path, cfg)
