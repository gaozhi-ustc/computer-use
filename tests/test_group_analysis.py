"""Tests for server/group_analysis.py."""

from server.group_analysis import (
    build_user_prompt,
    build_refine_user_prompt,
    parse_steps_response,
)


def test_build_user_prompt():
    frames = [
        {"cursor_x": 100, "cursor_y": 200, "focus_rect": [10, 20, 30, 40],
         "recorded_at": "2026-04-15T10:00:00"},
        {"cursor_x": 300, "cursor_y": 400, "focus_rect": None,
         "recorded_at": "2026-04-15T10:00:03"},
    ]
    prompt = build_user_prompt(frames)
    assert "2 sequential screenshots" in prompt
    assert "cursor=(100, 200)" in prompt
    assert "focus_rect=[10, 20, 30, 40]" in prompt
    assert "focus_rect=none" in prompt


def test_build_refine_user_prompt():
    prompt = build_refine_user_prompt(
        current_steps_json='[{"step_order": 1}]',
        feedback_text="Add more detail to step 1",
        feedback_scope="step:1",
        frames=[{}, {}],
    )
    assert "step:1" in prompt
    assert "Add more detail" in prompt
    assert "2 frames" in prompt


def test_parse_steps_response_json():
    raw = '{"steps": [{"step_order": 1, "title": "Open app"}]}'
    steps = parse_steps_response(raw)
    assert len(steps) == 1
    assert steps[0]["title"] == "Open app"


def test_parse_steps_response_markdown_fenced():
    raw = '```json\n{"steps": [{"step_order": 1, "title": "Click"}]}\n```'
    steps = parse_steps_response(raw)
    assert len(steps) == 1
    assert steps[0]["title"] == "Click"


def test_parse_steps_response_embedded_json():
    raw = 'Here is the result: {"steps": [{"step_order": 1}]} end.'
    steps = parse_steps_response(raw)
    assert len(steps) == 1
