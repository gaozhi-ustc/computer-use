"""Tests for reference screenshot storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workflow_recorder.output.reference_store import store_reference_screenshots
from workflow_recorder.output.schema import (
    Action,
    ApplicationInfo,
    Workflow,
    WorkflowMetadata,
    WorkflowStep,
)


@dataclass
class FakeCaptureResult:
    file_path: Path
    timestamp: float = 0.0
    width: int = 800
    height: int = 600
    monitor_index: int = 0


@dataclass
class FakeCapturedFrame:
    capture: FakeCaptureResult
    window_context: object = None
    frame_index: int = 0


def _make_workflow_with_steps(source_frames_list: list[list[int]]) -> Workflow:
    steps = []
    for i, frames in enumerate(source_frames_list, 1):
        steps.append(WorkflowStep(
            step_id=i,
            timestamp="2025-01-01T00:00:00+00:00",
            application=ApplicationInfo(process_name="test.exe", window_title="Test"),
            description=f"step {i}",
            actions=[Action(type="wait")],
            source_frames=frames,
        ))
    return Workflow(
        metadata=WorkflowMetadata(
            session_id="test-session",
            recorded_at="2025-01-01T00:00:00+00:00",
            duration_seconds=10.0,
            total_frames_captured=5,
            total_steps=len(steps),
        ),
        steps=steps,
    )


def test_store_reference_screenshots(test_image, tmp_output_dir):
    workflow = _make_workflow_with_steps([[1], [2]])
    frames = [
        FakeCapturedFrame(capture=FakeCaptureResult(file_path=test_image), frame_index=1),
        FakeCapturedFrame(capture=FakeCaptureResult(file_path=test_image), frame_index=2),
    ]

    result = store_reference_screenshots(workflow, frames, tmp_output_dir)

    screenshots_dir = tmp_output_dir / "screenshots"
    assert screenshots_dir.is_dir()
    assert (screenshots_dir / "step_001.png").exists()
    assert (screenshots_dir / "step_002.png").exists()
    assert result.steps[0].reference_screenshot == "screenshots/step_001.png"
    assert result.steps[1].reference_screenshot == "screenshots/step_002.png"


def test_store_missing_frame(test_image, tmp_output_dir):
    """Steps referencing non-existent frames should be skipped gracefully."""
    workflow = _make_workflow_with_steps([[1], [99]])
    frames = [
        FakeCapturedFrame(capture=FakeCaptureResult(file_path=test_image), frame_index=1),
    ]

    result = store_reference_screenshots(workflow, frames, tmp_output_dir)

    assert result.steps[0].reference_screenshot == "screenshots/step_001.png"
    assert result.steps[1].reference_screenshot == ""  # no frame 99


def test_store_empty_source_frames(test_image, tmp_output_dir):
    workflow = _make_workflow_with_steps([[]])
    frames = [
        FakeCapturedFrame(capture=FakeCaptureResult(file_path=test_image), frame_index=1),
    ]

    result = store_reference_screenshots(workflow, frames, tmp_output_dir)
    assert result.steps[0].reference_screenshot == ""
