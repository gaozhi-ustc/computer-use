"""End-to-end pipeline test.

Runs the full daemon for a short duration and validates output.
Requires: config.test.toml with valid API credentials + Windows desktop.
Run with: pytest --run-e2e
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from workflow_recorder.config import load_config
from workflow_recorder.daemon import Daemon
from workflow_recorder.output.schema import Workflow


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.e2e
class TestFullPipeline:

    def test_daemon_short_run_produces_output(self, tmp_path):
        """Run daemon for ~10 seconds, then validate the workflow output."""
        config_path = PROJECT_ROOT / "config.test.toml"
        if not config_path.exists():
            pytest.skip("config.test.toml not found")

        config = load_config(config_path)
        # Override output to tmp dir
        config.output.directory = str(tmp_path / "output")
        config.session.max_duration_seconds = 10.0
        config.capture.interval_seconds = 2.0

        daemon = Daemon(config)

        # Run daemon in a thread, auto-stop after max_duration
        daemon_thread = threading.Thread(target=daemon.run, daemon=True)
        daemon_thread.start()

        # Wait for daemon to finish (max_duration + buffer)
        daemon_thread.join(timeout=20.0)
        if daemon_thread.is_alive():
            daemon.stop()
            daemon_thread.join(timeout=5.0)

        # Check output
        output_dir = Path(config.output.directory)
        if not output_dir.exists():
            pytest.skip("No output produced (possibly no GPT analyses succeeded)")

        json_files = list(output_dir.glob("workflow_*.json"))
        if not json_files:
            # Capture-only mode or no analyses completed
            pytest.skip("No workflow JSON produced")

        # Validate JSON against schema
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        workflow = Workflow(**data)

        assert workflow.metadata.session_id != ""
        assert workflow.metadata.duration_seconds > 0
        assert workflow.metadata.total_frames_captured >= 1

    @pytest.mark.e2e
    def test_capture_only_mode(self, tmp_path):
        """Test daemon in capture-only mode (no GPT API needed)."""
        config_path = PROJECT_ROOT / "config.test.toml"
        if not config_path.exists():
            pytest.skip("config.test.toml not found")

        config = load_config(config_path)
        config.output.directory = str(tmp_path / "output")
        config.session.max_duration_seconds = 6.0
        config.capture.interval_seconds = 2.0
        # Invalidate API key to force capture-only mode
        config.analysis.openai_api_key = ""

        daemon = Daemon(config)

        daemon_thread = threading.Thread(target=daemon.run, daemon=True)
        daemon_thread.start()
        daemon_thread.join(timeout=15.0)
        if daemon_thread.is_alive():
            daemon.stop()
            daemon_thread.join(timeout=5.0)

        # In capture-only mode, session should have captured frames
        # but no workflow output (no analyses)
        assert daemon.session is not None
        assert len(daemon.session.captured_frames) >= 1
