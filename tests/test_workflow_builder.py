"""Tests for workflow builder."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from workflow_recorder.aggregation.workflow_builder import WorkflowBuilder
from workflow_recorder.analysis.frame_analysis import FrameAnalysis, UIElement
from workflow_recorder.config import AppConfig


@dataclass
class FakeCaptureResult:
    file_path: Path
    timestamp: float
    width: int = 1920
    height: int = 1080
    monitor_index: int = 0


@dataclass
class FakeCapturedFrame:
    capture: FakeCaptureResult
    window_context: object = None
    frame_index: int = 0


def _make_analysis(
    frame_index: int,
    app: str = "notepad.exe",
    title: str = "Test",
    action: str = "typing hello",
    confidence: float = 0.8,
    ts_offset: float = 0.0,
) -> FrameAnalysis:
    return FrameAnalysis(
        frame_index=frame_index,
        timestamp=1000.0 + ts_offset,
        application=app,
        window_title=title,
        user_action=action,
        confidence=confidence,
    )


def _make_captured_frame(frame_index: int, file_path: Path) -> FakeCapturedFrame:
    return FakeCapturedFrame(
        capture=FakeCaptureResult(file_path=file_path, timestamp=time.time()),
        frame_index=frame_index,
    )


class TestWorkflowBuilder:
    def setup_method(self):
        self.config = AppConfig()
        self.builder = WorkflowBuilder(self.config)

    @patch("workflow_recorder.aggregation.workflow_builder.deduplicate_frames")
    def test_build_basic(self, mock_dedup, test_image):
        mock_dedup.return_value = [0, 1]  # keep all frames

        analyses = [
            _make_analysis(0, action="clicking Save", ts_offset=0),
            _make_analysis(1, action="typing hello", ts_offset=3),
        ]
        frames = [
            _make_captured_frame(0, test_image),
            _make_captured_frame(1, test_image),
        ]

        workflow = self.builder.build("session-123", time.time() - 10, analyses, frames)

        assert workflow.metadata.session_id == "session-123"
        assert len(workflow.steps) == 2

    @patch("workflow_recorder.aggregation.workflow_builder.deduplicate_frames")
    def test_build_groups_same_action(self, mock_dedup, test_image):
        """Consecutive frames with same action should be grouped into one step."""
        mock_dedup.return_value = [0, 1, 2]

        analyses = [
            _make_analysis(0, action="typing hello", ts_offset=0),
            _make_analysis(1, action="typing hello", ts_offset=3),
            _make_analysis(2, action="typing hello", ts_offset=6),
        ]
        frames = [_make_captured_frame(i, test_image) for i in range(3)]

        workflow = self.builder.build("s1", time.time() - 10, analyses, frames)

        assert len(workflow.steps) == 1
        assert workflow.steps[0].source_frames == [0, 1, 2]

    @patch("workflow_recorder.aggregation.workflow_builder.deduplicate_frames")
    def test_build_groups_by_app(self, mock_dedup, test_image):
        """Switching app should create a new step even with same action."""
        mock_dedup.return_value = [0, 1]

        analyses = [
            _make_analysis(0, app="notepad.exe", action="typing", ts_offset=0),
            _make_analysis(1, app="chrome.exe", action="typing", ts_offset=3),
        ]
        frames = [_make_captured_frame(i, test_image) for i in range(2)]

        workflow = self.builder.build("s1", time.time() - 10, analyses, frames)

        assert len(workflow.steps) == 2

    @patch("workflow_recorder.aggregation.workflow_builder.deduplicate_frames")
    def test_confidence_filtering(self, mock_dedup, test_image):
        """Low confidence frames should be filtered out."""
        mock_dedup.return_value = [0, 1]

        analyses = [
            _make_analysis(0, action="typing", confidence=0.8, ts_offset=0),
            _make_analysis(1, action="unknown", confidence=0.1, ts_offset=3),  # below 0.3
        ]
        frames = [_make_captured_frame(i, test_image) for i in range(2)]

        workflow = self.builder.build("s1", time.time() - 10, analyses, frames)

        assert len(workflow.steps) == 1

    @patch("workflow_recorder.aggregation.workflow_builder.deduplicate_frames")
    def test_empty_analyses(self, mock_dedup, test_image):
        mock_dedup.return_value = []

        workflow = self.builder.build("s1", time.time(), [], [])

        assert workflow.metadata.total_steps == 0
        assert workflow.steps == []

    def test_same_action_exact(self):
        assert self.builder._same_action("typing hello", "typing hello") is True

    def test_same_action_case_insensitive(self):
        assert self.builder._same_action("Typing Hello", "typing hello") is True

    def test_same_action_substring(self):
        assert self.builder._same_action("clicking", "clicking Save button") is True

    def test_different_action(self):
        assert self.builder._same_action("clicking Save", "typing hello") is False

    @patch("workflow_recorder.aggregation.workflow_builder.deduplicate_frames")
    def test_step_confidence_is_average(self, mock_dedup, test_image):
        mock_dedup.return_value = [0, 1]

        analyses = [
            _make_analysis(0, action="typing", confidence=0.8, ts_offset=0),
            _make_analysis(1, action="typing", confidence=0.6, ts_offset=3),
        ]
        frames = [_make_captured_frame(i, test_image) for i in range(2)]

        workflow = self.builder.build("s1", time.time() - 10, analyses, frames)

        assert workflow.steps[0].confidence == 0.7  # (0.8 + 0.6) / 2

    @patch("workflow_recorder.aggregation.workflow_builder.deduplicate_frames")
    def test_environment_info(self, mock_dedup, test_image):
        mock_dedup.return_value = [0]

        analyses = [_make_analysis(0)]
        frames = [_make_captured_frame(0, test_image)]

        workflow = self.builder.build("s1", time.time() - 5, analyses, frames)

        assert workflow.environment.os != ""
        assert workflow.environment.hostname != ""
