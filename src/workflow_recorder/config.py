"""Configuration loading and validation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, model_validator


class ServiceConfig(BaseModel):
    name: str = "WorkflowRecorder"
    display_name: str = "Workflow Recorder Daemon"
    run_as_service: bool = False


class CaptureConfig(BaseModel):
    interval_seconds: float = 3.0
    monitor: int = 0  # 0=primary, -1=all
    image_format: str = "png"
    image_quality: int = 85
    downscale_factor: float = 1.0
    max_queue_size: int = 50


class PrivacyConfig(BaseModel):
    excluded_apps: list[str] = Field(default_factory=list)
    excluded_window_titles: list[str] = Field(default_factory=list)
    masked_regions: list[list[int]] = Field(default_factory=list)  # [x, y, w, h]


class AnalysisConfig(BaseModel):
    openai_api_key: str = ""
    base_url: str = ""  # Custom API endpoint (e.g. proxy); empty = default OpenAI
    model: str = "gpt-4o"
    detail: str = "low"
    max_tokens: int = 500
    temperature: float = 0.1
    retry_attempts: int = 3
    retry_backoff_base: float = 2.0
    rate_limit_rpm: int = 30


class SessionConfig(BaseModel):
    idle_timeout_seconds: float = 60.0
    max_duration_seconds: float = 0.0  # 0 = unlimited, run until Ctrl+C
    similarity_threshold: float = 0.95


class IdleDetectionConfig(BaseModel):
    """Backoff capture interval when user is idle (no mouse/keyboard input)."""
    enabled: bool = True
    idle_threshold_seconds: float = 60.0  # how long without input -> idle
    max_interval_seconds: float = 300.0   # cap on backed-off interval (5 min)
    backoff_factor: float = 2.0           # multiply interval by this each idle tick


class AggregationConfig(BaseModel):
    llm_cleanup_pass: bool = True
    min_confidence: float = 0.3


class OutputConfig(BaseModel):
    directory: str = "./workflows"
    format: str = "json"
    include_reference_screenshots: bool = True
    include_markdown_summary: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "./logs/recorder.log"
    max_size_mb: int = 50
    backup_count: int = 5


class ServerConfig(BaseModel):
    """Upstream server that collects per-frame analysis results."""
    enabled: bool = True
    url: str = "http://127.0.0.1:8000"
    api_key: str = ""
    timeout_seconds: float = 10.0
    max_retries: int = 3
    buffer_path: str = "./logs/push_buffer.jsonl"
    queue_size: int = 500


class AppConfig(BaseModel):
    employee_id: str = ""  # filled by init wizard on first run
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    idle_detection: IdleDetectionConfig = Field(default_factory=IdleDetectionConfig)

    @model_validator(mode="before")
    @classmethod
    def interpolate_env_vars(cls, data: dict) -> dict:
        """Replace ${ENV_VAR} patterns with environment variable values."""
        return _interpolate(data)


_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate(obj):
    if isinstance(obj, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), obj)
    if isinstance(obj, dict):
        return {k: _interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate(v) for v in obj]
    return obj


def _load_json(path: str | Path) -> dict:
    """Load a JSON file and return as dict."""
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _apply_model_preset(data: dict) -> dict:
    """Merge active model preset into analysis config.

    If data contains 'model_presets' and 'active_preset', merge the selected
    preset into 'analysis', then remove preset-related keys.
    """
    presets = data.get("model_presets")
    active = data.get("active_preset")
    if not presets or not active or active not in presets:
        return data

    preset = dict(presets[active])
    preset.pop("name", None)  # display name, not a config field

    analysis = data.get("analysis", {})
    # Preset values are defaults; explicit analysis values take precedence
    merged = {**preset, **{k: v for k, v in analysis.items() if v}}
    data["analysis"] = merged

    # Clean up non-config keys
    data.pop("model_presets", None)
    data.pop("active_preset", None)
    return data


def _load_toml(path: str | Path) -> dict:
    """Load a TOML file and return as dict."""
    import sys
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomli as tomllib
        except ImportError:
            raise ImportError("Install 'tomli' for Python < 3.11 TOML support")
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_config(path: Optional[str | Path] = None) -> AppConfig:
    """Load configuration from a YAML, TOML, or JSON file, falling back to defaults.

    JSON files support model_presets + active_preset for multi-model switching.
    """
    if path is None:
        return AppConfig()
    path = Path(path)
    if path.suffix == ".toml":
        data = _load_toml(path)
    elif path.suffix == ".json":
        data = _load_json(path)
    else:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    data = _apply_model_preset(data)
    return AppConfig(**data)
