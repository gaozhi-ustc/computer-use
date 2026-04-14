"""Filesystem storage for uploaded frame PNGs.

Layout: <base>/<employee_id>/<YYYY-MM-DD>/<session_id>/<frame_index>.png
where <base> = $WORKFLOW_IMAGE_DIR or ./frame_images

The date subdirectory uses the server's received_at date, not the client's
recorded_at — this makes "all files received today" an easy ls.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


# Allow alphanumerics, dash, underscore, dot. Anything else -> invalid.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def image_base_dir() -> Path:
    """Resolve the image storage root from env or default."""
    raw = os.environ.get("WORKFLOW_IMAGE_DIR", "./frame_images")
    return Path(raw).expanduser().resolve()


def _safe_segment(name: str, field_name: str) -> str:
    """Validate a single path segment, rejecting traversal and weird chars."""
    if not name or not _SAFE_SEGMENT.match(name):
        raise ValueError(f"invalid {field_name}: {name!r}")
    return name


def save_image(
    employee_id: str,
    session_id: str,
    frame_index: int,
    image_bytes: bytes,
    received_at_iso: str,
) -> Path:
    """Save image bytes under base/<employee>/<date>/<session>/<index>.png.

    Returns the absolute path written to.
    Raises ValueError if any path segment contains invalid characters.
    """
    emp = _safe_segment(employee_id, "employee_id")
    sess = _safe_segment(session_id, "session_id")
    # received_at_iso looks like "2026-04-14T10:30:00+00:00"; take the date part
    date_part = received_at_iso.split("T", 1)[0]
    _safe_segment(date_part, "received_at date")  # sanity check

    base = image_base_dir()
    target_dir = base / emp / date_part / sess
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{int(frame_index)}.png"
    target.write_bytes(image_bytes)
    return target.resolve()
