"""Tests for prompt templates."""

from __future__ import annotations

from workflow_recorder.analysis.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


def test_system_prompt_contains_required_fields():
    for field in ("application", "window_title", "user_action",
                  "ui_elements_visible", "confidence"):
        assert field in SYSTEM_PROMPT


def test_user_prompt_template_formatting():
    result = USER_PROMPT_TEMPLATE.format(
        process_name="notepad.exe",
        window_title="Untitled - Notepad",
        window_rect=(0, 0, 1920, 1080),
        is_maximized=True,
        width=1920,
        height=1080,
    )
    assert "notepad.exe" in result
    assert "Untitled - Notepad" in result
    assert "1920x1080" in result
    assert "True" in result


def test_system_prompt_requests_json_only():
    assert "Return ONLY the JSON object" in SYSTEM_PROMPT
