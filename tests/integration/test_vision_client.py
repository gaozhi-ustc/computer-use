"""Integration tests for VisionClient with real GPT API.

Requires: config.test.toml with valid API credentials.
Run with: pytest --run-integration

Note: These tests depend on an external API proxy which may have intermittent
availability. Each test retries up to 3 times to handle transient failures.
"""

from __future__ import annotations

import time

import pytest

from workflow_recorder.analysis.vision_client import VisionClient
from workflow_recorder.capture.window_info import WindowContext


def _retry_analyze(client, *, image_path, window_context, frame_index, max_retries=3):
    """Retry analyze_frame up to max_retries times for flaky API."""
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
class TestVisionClientIntegration:

    def test_analyze_frame_returns_analysis(self, integration_config, test_image):
        """Send a test screenshot to GPT and validate we get a response."""
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

        assert analysis is not None, "API should return a parseable analysis after retries"
        assert analysis.frame_index == 0

    def test_analyze_frame_without_window_context(self, integration_config, test_image):
        """Test analysis when no window context is available."""
        client = VisionClient(integration_config.analysis)

        analysis = _retry_analyze(
            client, image_path=test_image,
            window_context=None, frame_index=1,
        )

        assert analysis is not None, "API should return analysis even without window context"

    def test_analyze_frame_returns_frame_analysis_type(self, integration_config, test_image):
        """Verify the return type is FrameAnalysis with valid structure."""
        from workflow_recorder.analysis.frame_analysis import FrameAnalysis

        client = VisionClient(integration_config.analysis)

        analysis = _retry_analyze(
            client, image_path=test_image,
            window_context=WindowContext(
                process_name="testapp.exe",
                window_title="Test Application - Document.txt",
                window_rect=(0, 0, 800, 600),
                is_maximized=False,
                pid=1234,
            ),
            frame_index=5,
        )

        assert analysis is not None
        assert isinstance(analysis, FrameAnalysis)
        assert analysis.frame_index == 5
        assert isinstance(analysis.ui_elements_visible, list)
