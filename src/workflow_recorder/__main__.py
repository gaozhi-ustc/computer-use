"""Entry point: python -m workflow_recorder"""

from __future__ import annotations

import argparse
import signal
import sys

from workflow_recorder.config import load_config
from workflow_recorder.daemon import Daemon
from workflow_recorder.utils.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="workflow-recorder",
        description="Record and analyze Windows desktop workflows via GPT vision",
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to config YAML file",
        default=None,
    )
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="Only capture screenshots without GPT analysis",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(
        level=config.logging.level,
        log_file=config.logging.file,
        max_size_mb=config.logging.max_size_mb,
        backup_count=config.logging.backup_count,
    )

    daemon = Daemon(config)

    # Graceful shutdown on SIGINT/SIGTERM
    def handle_signal(signum, frame):
        daemon.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    daemon.run()


if __name__ == "__main__":
    main()
