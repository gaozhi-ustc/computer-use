"""Manage reference screenshots for workflow steps."""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

from workflow_recorder.output.schema import Workflow

log = structlog.get_logger()


def store_reference_screenshots(
    workflow: Workflow,
    captured_frames: list,  # list[CapturedFrame]
    output_dir: Path,
) -> Workflow:
    """Copy one reference screenshot per step into the output directory.

    Updates the workflow's reference_screenshot paths in-place and returns it.
    """
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # Build a lookup from frame_index to file path
    frame_map: dict[int, Path] = {}
    for frame in captured_frames:
        frame_map[frame.frame_index] = frame.capture.file_path

    for step in workflow.steps:
        if not step.source_frames:
            continue

        # Use the first source frame as reference
        ref_index = step.source_frames[0]
        src_path = frame_map.get(ref_index)
        if not src_path or not src_path.exists():
            continue

        dest_name = f"step_{step.step_id:03d}{src_path.suffix}"
        dest_path = screenshots_dir / dest_name

        shutil.copy2(src_path, dest_path)
        step.reference_screenshot = f"screenshots/{dest_name}"
        log.debug("reference_screenshot_stored", step=step.step_id, path=dest_name)

    return workflow
