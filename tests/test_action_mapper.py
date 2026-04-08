"""Tests for action mapping."""

from __future__ import annotations

from workflow_recorder.aggregation.action_mapper import map_to_actions
from workflow_recorder.analysis.frame_analysis import FrameAnalysis, UIElement


def _make_analysis(user_action: str, **kwargs) -> FrameAnalysis:
    return FrameAnalysis(
        frame_index=1,
        timestamp=1000.0,
        application="test.exe",
        window_title="Test Window",
        user_action=user_action,
        **kwargs,
    )


def test_click_action():
    analysis = _make_analysis(
        "clicking the Save button",
        ui_elements_visible=[
            UIElement(name="Save", element_type="button", coordinates=[100, 200]),
        ],
    )
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "click"
    assert actions[0].coordinates == [100, 200]


def test_type_action():
    analysis = _make_analysis("typing 'hello world'")
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "type"
    assert "hello world" in actions[0].text


def test_key_press_action():
    analysis = _make_analysis("pressing Ctrl + S")
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "key"
    assert actions[0].keys == "ctrl+s"


def test_scroll_action():
    analysis = _make_analysis("scrolling down")
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "scroll"
    assert actions[0].direction == "down"


def test_wait_action():
    analysis = _make_analysis("reading the document")
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "wait"
