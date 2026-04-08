"""Detect idle/duplicate frames and merge redundant actions."""

from __future__ import annotations

from pathlib import Path

import structlog
from PIL import Image

log = structlog.get_logger()


def compute_image_hash(image_path: Path) -> str:
    """Compute a perceptual hash of an image for similarity comparison."""
    import imagehash
    with Image.open(image_path) as img:
        return str(imagehash.phash(img))


def hamming_distance(hash1: str, hash2: str) -> int:
    """Compute hamming distance between two hex hash strings."""
    import imagehash
    h1 = imagehash.hex_to_hash(hash1)
    h2 = imagehash.hex_to_hash(hash2)
    return h1 - h2


def is_similar(hash1: str, hash2: str, threshold: float = 0.95) -> bool:
    """Check if two images are similar based on perceptual hash.

    threshold is 0-1 where 1.0 means identical. We convert to hamming distance:
    phash produces 64-bit hashes, so max distance is 64.
    threshold of 0.95 means max distance of ~3.
    """
    max_distance = 64
    max_allowed = int(max_distance * (1.0 - threshold))
    return hamming_distance(hash1, hash2) <= max_allowed


def deduplicate_frames(
    frame_paths: list[Path],
    threshold: float = 0.95,
) -> list[int]:
    """Return indices of frames to keep (non-duplicate).

    Consecutive frames that are similar to the previous are dropped.
    """
    if not frame_paths:
        return []

    keep = [0]
    prev_hash = compute_image_hash(frame_paths[0])

    for i in range(1, len(frame_paths)):
        curr_hash = compute_image_hash(frame_paths[i])
        if not is_similar(prev_hash, curr_hash, threshold):
            keep.append(i)
            prev_hash = curr_hash
        else:
            log.debug("frame_deduplicated", index=i, reason="similar_to_previous")

    return keep
