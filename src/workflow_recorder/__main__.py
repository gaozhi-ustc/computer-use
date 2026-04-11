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
    mins = int(duration // 60)
    secs = int(duration % 60)

    print()
    print("=" * 58)
    print("  Workflow Recorder v0.1.0")
    print("=" * 58)
    print()
    base = config.analysis.base_url or "https://api.openai.com/v1"
    has_key = "***" + config.analysis.openai_api_key[-4:] if len(config.analysis.openai_api_key) > 4 else "(not set)"
    server_status = config.server.url if config.server.enabled else "disabled"

    print(f"  Employee ID:          {config.employee_id or '(not set)'}")
    print(f"  Screenshot interval:  {config.capture.interval_seconds}s")
    print(f"  Max recording time:   {mins}m {secs}s")
    print(f"  Model:                {config.analysis.model}")
    print(f"  API endpoint:         {base}")
    print(f"  API key:              {has_key}")
    print(f"  Push target:          {server_status}")
    print(f"  Output directory:     {output_dir}")
    print()
    print("  Recording is now in progress.")
    print("  Press Ctrl+C to stop early.")
    print()
    print("-" * 58)


def _print_progress(daemon) -> None:
    """Print periodic progress updates to console."""
    if not daemon.session:
        return
    elapsed = int(daemon.session.elapsed)
    captured = len(daemon.session.captured_frames)
    analyzed = len(daemon.session.frame_analyses)
    mins, secs = divmod(elapsed, 60)
    sys.stdout.write(
        f"\r  [{mins:02d}:{secs:02d}] "
        f"Frames captured: {captured}  |  Analyzed: {analyzed}"
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
    captured = len(daemon.session.captured_frames)
    analyzed = len(daemon.session.frame_analyses)
    mins, secs = divmod(elapsed, 60)

    print(f"  Recording complete!")
    print()
    print(f"  Duration:         {mins}m {secs}s")
    print(f"  Frames captured:  {captured}")
    print(f"  Frames analyzed:  {analyzed}")
    if daemon.pusher is not None and daemon.config.server.enabled:
        print(f"  Pushed to server: {daemon.pusher.pushed_ok}"
              f"  (buffered on failure: {daemon.pusher.buffered})")

    output_dir = Path(daemon.config.output.directory).resolve()
    if output_dir.exists():
        json_files = list(output_dir.glob("workflow_*.json"))
        md_files = list(output_dir.glob("workflow_*.md"))
        if json_files:
            print()
            print(f"  Workflow JSON:     {json_files[-1]}")
        if md_files:
            print(f"  Workflow Summary:  {md_files[-1]}")
    else:
        if analyzed == 0:
            print()
            print("  No analyses completed. Check your API key in config.yaml.")

    print()
    print(f"  All outputs saved to: {output_dir}")
    print()
    print("=" * 58)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="workflow-recorder",
        description="Record and analyze Windows desktop workflows via GPT vision",
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to config YAML/TOML file",
        default=None,
    )
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="Only capture screenshots without GPT analysis",
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
