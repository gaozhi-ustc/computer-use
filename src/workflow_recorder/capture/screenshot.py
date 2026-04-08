"""Screenshot capture using mss."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import mss
from PIL import Image


@dataclass
class CaptureResult:
    file_path: Path
    timestamp: float
    width: int
    height: int
    monitor_index: int


def capture_screenshot(
    output_dir: Path,
    monitor: int = 0,
    image_format: str = "png",
    image_quality: int = 85,
    downscale_factor: float = 1.0,
) -> CaptureResult:
    """Capture a screenshot and save to output_dir.

    Args:
        output_dir: Directory to save the screenshot.
        monitor: Monitor index (0=primary, -1=all monitors combined).
        image_format: 'png' or 'jpg'.
        image_quality: JPEG quality (ignored for PNG).
        downscale_factor: Scale factor (0.5 = half resolution).

    Returns:
        CaptureResult with file path and metadata.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.time()

    with mss.mss() as sct:
        # mss monitors: index 0 = all combined, 1+ = individual monitors
        # Our convention: 0=primary (mss index 1), -1=all (mss index 0)
        if monitor == -1:
            mon = sct.monitors[0]
            mon_idx = -1
        else:
            mon_idx = monitor
            mss_idx = monitor + 1
            if mss_idx >= len(sct.monitors):
                mss_idx = 1  # fallback to primary
            mon = sct.monitors[mss_idx]

        raw = sct.grab(mon)

    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    if downscale_factor < 1.0:
        new_w = int(img.width * downscale_factor)
        new_h = int(img.height * downscale_factor)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    filename = f"capture_{int(ts * 1000)}.{image_format}"
    filepath = output_dir / filename

    if image_format == "jpg":
        img.save(filepath, "JPEG", quality=image_quality)
    else:
        img.save(filepath, "PNG")

    return CaptureResult(
        file_path=filepath,
        timestamp=ts,
        width=img.width,
        height=img.height,
        monitor_index=mon_idx,
    )
