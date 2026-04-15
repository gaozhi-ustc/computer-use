"""Lightweight frame grouping by logical action boundaries.

Groups frames using four signals (priority order):
P1 — Application switch (window_title_raw)
P2 — Image similarity (perceptual hash)
P3 — Time gap (> 3x median interval)
P4 — Cursor/focus jump (> 30% screen diagonal)

Groups overlap by ±N frames at boundaries to preserve context.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FrameGroup:
    group_index: int
    frame_ids: list[int] = field(default_factory=list)
    primary_application: str = ""


PHASH_THRESHOLD = 12
TIME_GAP_MULTIPLIER = 3
CURSOR_JUMP_RATIO = 0.3
OVERLAP_FRAMES = 3
MIN_GROUP_SIZE = 2


def find_boundaries(
    frames: list[dict[str, Any]],
    *,
    use_phash: bool = True,
    phash_threshold: int = PHASH_THRESHOLD,
    time_gap_multiplier: float = TIME_GAP_MULTIPLIER,
    cursor_jump_ratio: float = CURSOR_JUMP_RATIO,
    screen_width: int = 0,
    screen_height: int = 0,
) -> set[int]:
    """Return set of boundary indices where a new group should start."""
    if len(frames) < 2:
        return set()

    boundaries: set[int] = set()

    # P1: Application switch
    for i in range(1, len(frames)):
        prev_app = (frames[i - 1].get("window_title_raw") or "").strip()
        curr_app = (frames[i].get("window_title_raw") or "").strip()
        if prev_app and curr_app and prev_app != curr_app:
            boundaries.add(i)

    # P2: Image similarity (perceptual hash)
    if use_phash:
        hashes = _compute_phashes(frames)
        if hashes:
            for i in range(1, len(frames)):
                if hashes[i] is not None and hashes[i - 1] is not None:
                    dist = hashes[i] - hashes[i - 1]
                    if dist > phash_threshold:
                        boundaries.add(i)

    # P3: Time gap
    timestamps = [f.get("timestamp", 0.0) for f in frames]
    intervals = [timestamps[i] - timestamps[i - 1]
                 for i in range(1, len(timestamps))
                 if timestamps[i] > timestamps[i - 1]]
    if intervals:
        median_interval = statistics.median(intervals)
        threshold = median_interval * time_gap_multiplier
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            if gap > threshold:
                boundaries.add(i)

    # P4: Cursor jump
    if screen_width <= 0 or screen_height <= 0:
        screen_width = screen_width or 1920
        screen_height = screen_height or 1080
    diagonal = math.sqrt(screen_width ** 2 + screen_height ** 2)
    jump_threshold = diagonal * cursor_jump_ratio

    for i in range(1, len(frames)):
        cx1 = frames[i - 1].get("cursor_x", -1)
        cy1 = frames[i - 1].get("cursor_y", -1)
        cx2 = frames[i].get("cursor_x", -1)
        cy2 = frames[i].get("cursor_y", -1)
        if cx1 >= 0 and cy1 >= 0 and cx2 >= 0 and cy2 >= 0:
            dist = math.sqrt((cx2 - cx1) ** 2 + (cy2 - cy1) ** 2)
            if dist > jump_threshold:
                boundaries.add(i)

    return boundaries


def split_with_overlap(
    frame_ids: list[int],
    boundaries: list[int],
    overlap: int = OVERLAP_FRAMES,
    min_group_size: int = MIN_GROUP_SIZE,
) -> list[list[int]]:
    """Split frame_ids at boundary indices with ±overlap frames."""
    if not boundaries:
        return [list(frame_ids)]

    boundaries = sorted(boundaries)
    n = len(frame_ids)

    # Build raw segments
    segments: list[tuple[int, int]] = []
    prev = 0
    for b in boundaries:
        if b > prev:
            segments.append((prev, b))
        prev = b
    if prev < n:
        segments.append((prev, n))

    # Merge small segments
    merged: list[tuple[int, int]] = []
    for seg in segments:
        seg_size = seg[1] - seg[0]
        if merged and seg_size < min_group_size:
            prev_start, _ = merged[-1]
            merged[-1] = (prev_start, seg[1])
        elif not merged and seg_size < min_group_size and len(segments) > 1:
            merged.append(seg)
        else:
            if merged and (merged[-1][1] - merged[-1][0]) < min_group_size:
                prev_start, _ = merged[-1]
                merged[-1] = (prev_start, seg[1])
            else:
                merged.append(seg)

    # Apply overlap
    groups: list[list[int]] = []
    for start, end in merged:
        overlap_start = max(0, start - overlap)
        overlap_end = min(n, end + overlap)
        groups.append(frame_ids[overlap_start:overlap_end])

    return groups


def _compute_phashes(frames: list[dict]) -> list[Any]:
    """Compute perceptual hashes for frames that have image_path."""
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        return []

    hashes = []
    for f in frames:
        path = f.get("image_path", "")
        if path and Path(path).is_file():
            try:
                img = Image.open(path)
                hashes.append(imagehash.phash(img))
            except Exception:
                hashes.append(None)
        else:
            hashes.append(None)
    return hashes


def _dominant_app(frames: list[dict], frame_ids: list[int]) -> str:
    """Find the most common window_title_raw among the given frame IDs."""
    id_to_frame = {f["id"]: f for f in frames}
    apps = [
        (id_to_frame[fid].get("window_title_raw") or "").strip()
        for fid in frame_ids
        if fid in id_to_frame
    ]
    apps = [a for a in apps if a]
    if not apps:
        return ""
    return Counter(apps).most_common(1)[0][0]


def group_frames(
    frames: list[dict[str, Any]],
    *,
    use_phash: bool = True,
    overlap: int = OVERLAP_FRAMES,
    min_group_size: int = MIN_GROUP_SIZE,
    screen_width: int = 0,
    screen_height: int = 0,
) -> list[FrameGroup]:
    """Main entry point: group a session's frames into logical action groups."""
    if not frames:
        return []

    frames = sorted(frames, key=lambda f: f.get("frame_index", 0))

    boundaries = find_boundaries(
        frames,
        use_phash=use_phash,
        screen_width=screen_width,
        screen_height=screen_height,
    )

    frame_ids = [f["id"] for f in frames]
    id_lists = split_with_overlap(
        frame_ids, sorted(boundaries),
        overlap=overlap, min_group_size=min_group_size,
    )

    groups = []
    for i, fids in enumerate(id_lists):
        groups.append(FrameGroup(
            group_index=i,
            frame_ids=fids,
            primary_application=_dominant_app(frames, fids),
        ))

    return groups
