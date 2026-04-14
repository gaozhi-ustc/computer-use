"""Aggregate frame analyses into a coherent workflow document."""

from __future__ import annotations

import platform
import socket
import time
from datetime import datetime, timezone
from itertools import groupby
from typing import TYPE_CHECKING

import structlog

from workflow_recorder.aggregation.action_mapper import map_to_actions
from workflow_recorder.aggregation.deduplication import deduplicate_frames
from workflow_recorder.config import AppConfig
from workflow_recorder.output.schema import (
    ApplicationInfo,
    EnvironmentInfo,
    Verification,
    Workflow,
    WorkflowMetadata,
    WorkflowStep,
)

if TYPE_CHECKING:
    from workflow_recorder.analysis.frame_analysis import FrameAnalysis

log = structlog.get_logger()


class WorkflowBuilder:
    """Builds a Workflow document from a sequence of FrameAnalysis results."""

    def __init__(self, config: AppConfig):
        self.config = config

    def build(
        self,
        session_id: str,
        start_time: float,
        frame_analyses: list[FrameAnalysis],
        captured_frames: list,  # list[CapturedFrame] — old shape, duck-typed
    ) -> Workflow:
        duration = time.time() - start_time

        # Step 1: Deduplicate frames by image similarity
        frame_paths = [f.capture.file_path for f in captured_frames]
        keep_indices = set(deduplicate_frames(
            frame_paths,
            threshold=self.config.session.similarity_threshold,
        ))

        # Filter analyses to only keep non-duplicate frames
        analyses = [
            a for a in frame_analyses
            if a.frame_index in keep_indices
            and a.confidence >= self.config.aggregation.min_confidence
        ]

        log.info("deduplication_done",
                 original=len(frame_analyses),
                 after_dedup=len(analyses))

        if not analyses:
            return self._empty_workflow(session_id, start_time, duration,
                                        len(captured_frames))

        # Step 2: Group by application context
        steps = self._build_steps(analyses)

        # Determine screen resolution from first capture
        screen_res = [captured_frames[0].capture.width,
                      captured_frames[0].capture.height] if captured_frames else []

        return Workflow(
            metadata=WorkflowMetadata(
                session_id=session_id,
                recorded_at=datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
                duration_seconds=round(duration, 1),
                total_frames_captured=len(captured_frames),
                total_steps=len(steps),
            ),
            environment=EnvironmentInfo(
                screen_resolution=screen_res,
                os=f"{platform.system()} {platform.release()}",
                hostname=socket.gethostname(),
            ),
            steps=steps,
        )

    def _build_steps(self, analyses: list[FrameAnalysis]) -> list[WorkflowStep]:
        """Convert sorted analyses into workflow steps."""
        steps: list[WorkflowStep] = []
        step_id = 0

        # Group consecutive frames by (application, window_title)
        def app_key(a: FrameAnalysis) -> tuple[str, str]:
            return (a.application, a.window_title)

        for (app, title), group_iter in groupby(analyses, key=app_key):
            group = list(group_iter)

            # Within each app segment, detect action changes
            current_action = None
            current_frames: list[FrameAnalysis] = []

            for analysis in group:
                if current_action is None or not self._same_action(current_action, analysis.user_action):
                    # Emit previous step if exists
                    if current_frames:
                        step_id += 1
                        steps.append(self._make_step(step_id, current_frames))
                    current_action = analysis.user_action
                    current_frames = [analysis]
                else:
                    current_frames.append(analysis)

            # Emit last step in segment
            if current_frames:
                step_id += 1
                steps.append(self._make_step(step_id, current_frames))

        return steps

    def _same_action(self, prev: str, curr: str) -> bool:
        """Determine if two action descriptions represent the same action."""
        # Simple heuristic: same if identical or very similar
        if prev.lower() == curr.lower():
            return True
        # Check if one contains the other (handles minor variations)
        p, c = prev.lower(), curr.lower()
        if p in c or c in p:
            return True
        return False

    def _make_step(self, step_id: int, frames: list[FrameAnalysis]) -> WorkflowStep:
        """Create a WorkflowStep from a group of related frames."""
        first = frames[0]
        last = frames[-1]

        # Map the primary frame's action to computer-use primitives
        actions = map_to_actions(first)

        # Calculate wait time (time between this step's last frame and next step)
        wait = 0.0
        if len(frames) > 1:
            wait = round(last.timestamp - first.timestamp, 1)

        return WorkflowStep(
            step_id=step_id,
            timestamp=datetime.fromtimestamp(first.timestamp, tz=timezone.utc).isoformat(),
            application=ApplicationInfo(
                process_name=first.application,
                window_title=first.window_title,
            ),
            description=first.user_action,
            actions=actions,
            wait_after_seconds=wait,
            verification=Verification(
                expected_window_title=last.window_title,
            ),
            confidence=round(sum(f.confidence for f in frames) / len(frames), 2),
            source_frames=[f.frame_index for f in frames],
        )

    def _empty_workflow(self, session_id: str, start_time: float,
                        duration: float, total_frames: int) -> Workflow:
        return Workflow(
            metadata=WorkflowMetadata(
                session_id=session_id,
                recorded_at=datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
                duration_seconds=round(duration, 1),
                total_frames_captured=total_frames,
                total_steps=0,
            ),
        )
