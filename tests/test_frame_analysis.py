"""Tests for frame analysis data models."""

from __future__ import annotations

from workflow_recorder.analysis.frame_analysis import FrameAnalysis, UIElement


def test_ui_element_creation():
    elem = UIElement(name="Save", element_type="button", coordinates=[100, 200])
    assert elem.name == "Save"
    assert elem.element_type == "button"
    assert elem.coordinates == [100, 200]


def test_ui_element_defaults():
    elem = UIElement(name="OK")
    assert elem.element_type == ""
    assert elem.coordinates == []


def test_frame_analysis_creation():
    analysis = FrameAnalysis(
        frame_index=1,
        timestamp=1000.0,
        application="notepad.exe",
        window_title="Untitled - Notepad",
        user_action="typing hello world",
        confidence=0.85,
    )
    assert analysis.frame_index == 1
    assert analysis.application == "notepad.exe"
    assert analysis.confidence == 0.85


def test_frame_analysis_defaults():
    analysis = FrameAnalysis(
        frame_index=0,
        timestamp=0.0,
        application="",
        window_title="",
        user_action="",
    )
    assert analysis.ui_elements_visible == []
    assert analysis.text_content == ""
    assert analysis.mouse_position_estimate == []
    assert analysis.confidence == 0.0


def test_frame_analysis_with_ui_elements():
    analysis = FrameAnalysis(
        frame_index=1,
        timestamp=1000.0,
        application="chrome.exe",
        window_title="Google",
        user_action="clicking search",
        ui_elements_visible=[
            UIElement(name="Search", element_type="button", coordinates=[400, 300]),
            UIElement(name="Input", element_type="input", coordinates=[400, 250]),
        ],
    )
    assert len(analysis.ui_elements_visible) == 2
    assert analysis.ui_elements_visible[0].name == "Search"
