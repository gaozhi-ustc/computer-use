"""Tests for action mapping."""

from __future__ import annotations

from workflow_recorder.aggregation.action_mapper import map_to_actions, _best_coordinates
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


def test_double_click():
    analysis = _make_analysis(
        "double-clicking the file icon",
        ui_elements_visible=[
            UIElement(name="file icon", element_type="icon", coordinates=[300, 400]),
        ],
    )
    actions = map_to_actions(analysis)
    assert len(actions) == 2
    assert all(a.type == "click" for a in actions)


def test_right_click():
    analysis = _make_analysis(
        "right-clicking the desktop",
        mouse_position_estimate=[500, 400],
    )
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].button == "right"


def test_scroll_up():
    analysis = _make_analysis("scrolling up through the list")
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "scroll"
    assert actions[0].direction == "up"


def test_fallback_with_mouse_position():
    analysis = _make_analysis(
        "hovering over the toolbar",
        mouse_position_estimate=[200, 50],
    )
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "click"
    assert actions[0].coordinates == [200, 50]


def test_fallback_without_mouse_position():
    analysis = _make_analysis("doing something unknown")
    actions = map_to_actions(analysis)
    assert len(actions) == 1
    assert actions[0].type == "wait"


def test_best_coordinates_exact_match():
    analysis = _make_analysis(
        "clicking Save",
        ui_elements_visible=[
            UIElement(name="Cancel", element_type="button", coordinates=[50, 200]),
            UIElement(name="Save", element_type="button", coordinates=[150, 200]),
        ],
    )
    coords = _best_coordinates(analysis, "Save")
    assert coords == [150, 200]


def test_best_coordinates_substring_match():
    analysis = _make_analysis(
        "clicking save",
        ui_elements_visible=[
            UIElement(name="Save As...", element_type="button", coordinates=[150, 200]),
        ],
    )
    coords = _best_coordinates(analysis, "save")
    assert coords == [150, 200]


def test_best_coordinates_fallback_mouse():
    analysis = _make_analysis(
        "clicking something",
        mouse_position_estimate=[300, 300],
    )
    coords = _best_coordinates(analysis, "something")
    assert coords == [300, 300]
