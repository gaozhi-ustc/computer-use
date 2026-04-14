"""End-to-end pipeline test.

Runs the full daemon for a short duration and validates capture counts.
With the offline analysis architecture, the client only captures+uploads;
the workflow document is built server-side.
Requires: config.test.toml + Windows desktop.
Run with: pytest --run-e2e
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from workflow_recorder.config import load_config
from workflow_recorder.daemon import Daemon


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.e2e
class TestFullPipeline:

    def test_daemon_short_run_captures_frames(self, tmp_path):
        """Run daemon for ~10 seconds and validate frames were captured."""
        config_path = PROJECT_ROOT / "config.test.toml"
        if not config_path.exists():
            pytest.skip("config.test.toml not found")

        config = load_config(config_path)
        # Disable server upload — we only test local capture here
        config.server.enabled = False
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

        assert daemon.session is not None
        # Expect several frames captured over the 10s window at 2s interval
        assert daemon.session.frames_captured >= 1

    @pytest.mark.e2e
    def test_capture_only_mode(self, tmp_path):
        """Test daemon with server upload disabled."""
        config_path = PROJECT_ROOT / "config.test.toml"
        if not config_path.exists():
            pytest.skip("config.test.toml not found")

        config = load_config(config_path)
        config.output.directory = str(tmp_path / "output")
        config.session.max_duration_seconds = 6.0
        config.capture.interval_seconds = 2.0
        # Force capture-only (no upload) mode
        config.server.enabled = False

        daemon = Daemon(config)

        daemon_thread = threading.Thread(target=daemon.run, daemon=True)
        daemon_thread.start()
        daemon_thread.join(timeout=15.0)
        if daemon_thread.is_alive():
            daemon.stop()
            daemon_thread.join(timeout=5.0)

        assert daemon.session is not None
        assert daemon.session.frames_captured >= 1
