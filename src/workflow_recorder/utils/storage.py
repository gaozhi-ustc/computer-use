"""Temporary file management and cleanup."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    """Create directory if it doesn't exist, return Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def cleanup_dir(path: str | Path) -> None:
    """Remove a directory and all its contents."""
    p = Path(path)
    if p.exists():
        shutil.rmtree(p)


def get_temp_capture_dir(base_dir: str | None = None) -> Path:
    """Get (and create) the temporary capture directory.

    Defaults to %TEMP%/workflow_recorder/captures (always writable,
    even when the exe is installed under C:\\Program Files).
    """
    if base_dir is None:
        base_dir = str(Path(tempfile.gettempdir()) / "workflow_recorder")
    return ensure_dir(Path(base_dir) / "captures")
