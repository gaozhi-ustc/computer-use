"""Integration tests for the analysis -> action mapping pipeline.

Tests the full path: screenshot -> GPT analysis -> action mapping.
Requires: config.test.toml with valid API credentials.
Run with: pytest --run-integration
"""

from __future__ import annotations

import time

import pytest

from workflow_recorder.aggregation.action_mapper import map_to_actions
from workflow_recorder.analysis.vision_client import VisionClient
from workflow_recorder.capture.window_info import WindowContext


def _retry_analyze(client, *, image_path, window_context, frame_index, max_retries=3):
    """Retry analyze_frame for flaky API."""
    for attempt in range(max_retries):
        analysis = client.analyze_frame(
            image_path=image_path,
            window_context=window_context,
            frame_index=frame_index,
        )
        if analysis is not None:
            return analysis
        time.sleep(2)
    return None


@pytest.mark.integration
class TestAnalysisPipeline:

    def test_analyze_then_map_actions(self, integration_config, test_image):
        """Full pipeline: GPT analyzes screenshot, then actions are mapped."""
        client = VisionClient(integration_config.analysis)

        window_ctx = WindowContext(
            process_name="notepad.exe",
            window_title="Document.txt - Notepad",
            window_rect=(0, 0, 800, 600),
            is_maximized=False,
            pid=1234,
        )

        analysis = _retry_analyze(
            client, image_path=test_image,
            window_context=window_ctx, frame_index=0,
        )

        assert analysis is not None, "Need a valid analysis to test action mapping"

        # Map to actions
        actions = map_to_actions(analysis)
        assert len(actions) >= 1
        assert actions[0].type in ("click", "type", "key", "scroll", "wait")

    def test_multiple_frames_analysis(self, integration_config, test_image, test_image_alt):
        """Analyze two different screenshots and verify both return results."""
        client = VisionClient(integration_config.analysis)

        analysis1 = _retry_analyze(
            client, image_path=test_image,
            window_context=WindowContext(
                process_name="notepad.exe",
                window_title="Document.txt",
                window_rect=(0, 0, 800, 600),
                is_maximized=False,
                pid=1234,
            ),
            frame_index=0,
        )

        analysis2 = _retry_analyze(
            client, image_path=test_image_alt,
            window_context=WindowContext(
                process_name="settings.exe",
                window_title="Settings",
                window_rect=(0, 0, 800, 600),
                is_maximized=False,
                pid=5678,
            ),
            frame_index=1,
        )

        assert analysis1 is not None, "First analysis should succeed"
        assert analysis2 is not None, "Second analysis should succeed"
        assert analysis1.frame_index != analysis2.frame_index
