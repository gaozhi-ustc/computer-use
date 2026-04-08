"""Temporary file management and cleanup."""

from __future__ import annotations

import shutil
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


def get_temp_capture_dir(base_dir: str = "./tmp") -> Path:
    """Get (and create) the temporary capture directory."""
    return ensure_dir(Path(base_dir) / "captures")
