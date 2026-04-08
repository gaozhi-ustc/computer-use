"""Privacy filtering — applied before screenshots are sent to API or stored."""

from __future__ import annotations

import re
from pathlib import Path

import structlog
from PIL import Image, ImageDraw

from workflow_recorder.capture.window_info import WindowContext
from workflow_recorder.config import PrivacyConfig

log = structlog.get_logger()


def should_skip_frame(context: WindowContext | None, config: PrivacyConfig) -> bool:
    """Check if this frame should be skipped entirely based on privacy rules."""
    if not context:
        return False

    # Check excluded apps (case-insensitive process name match)
    for app in config.excluded_apps:
        if context.process_name.lower() == app.lower():
            log.debug("frame_skipped_privacy", reason="excluded_app",
                      app=context.process_name)
            return True

    # Check excluded window title patterns
    for pattern in config.excluded_window_titles:
        if re.search(pattern, context.window_title, re.IGNORECASE):
            log.debug("frame_skipped_privacy", reason="excluded_title",
                      title=context.window_title)
            return True

    return False


def apply_masks(image_path: Path, config: PrivacyConfig) -> None:
    """Apply black masks over configured screen regions in-place."""
    if not config.masked_regions:
        return

    img = Image.open(image_path)
    draw = ImageDraw.Draw(img)

    for region in config.masked_regions:
        if len(region) == 4:
            x, y, w, h = region
            draw.rectangle([x, y, x + w, y + h], fill="black")

    img.save(image_path)
    log.debug("masks_applied", count=len(config.masked_regions))
