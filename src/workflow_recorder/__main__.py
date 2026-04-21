"""Entry point: python -m workflow_recorder"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from workflow_recorder.config import load_config
from workflow_recorder.daemon import Daemon
from workflow_recorder.init_wizard import needs_wizard, run_wizard
from workflow_recorder.utils.logging import setup_logging


def _run_daemon(config) -> None:
    """Run the recorder daemon with live console progress."""
    _print_banner(config)

    daemon = Daemon(config)

    def handle_signal(signum, frame):
        print("\n\n  Stopping recording...")
        daemon.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    import threading

    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    try:
        while daemon_thread.is_alive():
            _print_progress(daemon)
            daemon_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        print("\n\n  Stopping recording...")
        daemon.stop()
        daemon_thread.join(timeout=10.0)

    _print_summary(daemon)


def _print_banner(config) -> None:
    """Print startup information to console."""
    output_dir = Path(config.output.directory).resolve()
    duration = config.session.max_duration_seconds
    if duration <= 0:
        duration_str = "unlimited (Ctrl+C to stop)"
    else:
        mins = int(duration // 60)
        secs = int(duration % 60)
        duration_str = f"{mins}m {secs}s"

    print()
    print("=" * 58)
    print("  Workflow Recorder v0.4.0")
    print("=" * 58)
    print()

    server_target = config.server.url if config.server.enabled else "disabled"
    if config.idle_detection.enabled:
        idle_str = (f"idle backoff: >{int(config.idle_detection.idle_threshold_seconds)}s "
                    f"-> up to {int(config.idle_detection.max_interval_seconds)}s")
    else:
        idle_str = "idle backoff: disabled"

    print(f"  Employee ID:          {config.employee_id or '(not set)'}")
    print(f"  Screenshot interval:  {config.capture.interval_seconds}s ({idle_str})")
    print(f"  Max recording time:   {duration_str}")
    print(f"  Upload target:        {server_target}")
    print(f"  Output directory:     {output_dir}")
    print()
    print("  Analysis now runs on the server — no API key needed on this machine.")
    print("  Press Ctrl+C to stop early.")
    print()
    print("-" * 58)


def _print_progress(daemon) -> None:
    """Print periodic progress updates to console."""
    if not daemon.session:
        return
    elapsed = int(daemon.session.elapsed)
    captured = daemon.session.frames_captured
    skipped = daemon.session.frames_skipped
    mins, secs = divmod(elapsed, 60)
    sys.stdout.write(
        f"\r  [{mins:02d}:{secs:02d}] "
        f"Frames captured: {captured}  |  Skipped: {skipped}"
    )
    sys.stdout.flush()


def _print_summary(daemon) -> None:
    """Print session summary after recording ends."""
    print()
    print()
    print("-" * 58)

    if not daemon.session:
        print("  No session data.")
        return

    elapsed = int(daemon.session.elapsed)
    captured = daemon.session.frames_captured
    skipped = daemon.session.frames_skipped
    mins, secs = divmod(elapsed, 60)

    print(f"  Recording complete!")
    print()
    print(f"  Duration:         {mins}m {secs}s")
    print(f"  Frames captured:  {captured}")
    print(f"  Frames skipped:   {skipped}")
    if daemon.config.server.enabled:
        print(f"  Upload target:    {daemon.config.server.url}")
    else:
        print(f"  Upload target:    disabled")

    print()
    print("  Analysis runs on the server — check the dashboard for results.")
    print()
    print("=" * 58)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="workflow-recorder",
        description="Record desktop captures (Windows/macOS) and upload for server-side analysis",
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to config YAML/TOML file",
        default=None,
    )
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="Only capture screenshots without uploading",
    )
    args = parser.parse_args()

    # Auto-discover config: explicit arg > model_config.json > config.yaml > defaults
    config_path = args.config
    if config_path is None:
        for candidate in ("model_config.json", "config.yaml", "config.json"):
            p = Path(candidate)
            if p.exists():
                config_path = str(p)
                break

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"\n  Error loading config: {e}")
        print(f"  Please check your config file: {config_path}")
        _wait_before_exit()
        sys.exit(1)

    # Interactive first-run setup for employee_id / API key if missing.
    if needs_wizard(config):
        try:
            config = run_wizard(config, config_path)
        except SystemExit:
            _wait_before_exit()
            raise

    # Resolve output directory to absolute path
    config.output.directory = str(Path(config.output.directory).resolve())

    setup_logging(
        level=config.logging.level,
        log_file=config.logging.file,
        max_size_mb=config.logging.max_size_mb,
        backup_count=config.logging.backup_count,
    )

    _run_daemon(config)

    _wait_before_exit()


def _wait_before_exit() -> None:
    """Wait for user keypress before closing the console window."""
    # Only pause if running in a standalone console (not piped/scripted)
    if sys.stdout.isatty():
        print("  Press Enter to close this window...")
        try:
            input()
        except EOFError:
            pass


if __name__ == "__main__":
    main()
