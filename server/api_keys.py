"""Load DashScope API keys from ./api_keys.txt (one per line).

File format:
- One key per line, whitespace stripped
- Lines starting with # are treated as comments and skipped
- Blank lines are skipped
- Inline # comments are NOT stripped (keep keys opaque)

Missing file returns [] — caller is responsible for logging/warning.
"""

from __future__ import annotations

from pathlib import Path


DEFAULT_PATH = "./api_keys.txt"


def load_api_keys(path: str | Path | None = None) -> list[str]:
    """Parse api_keys.txt and return the list of keys."""
    p = Path(path) if path is not None else Path(DEFAULT_PATH)
    if not p.is_file():
        return []
    keys: list[str] = []
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        keys.append(line)
    return keys
