"""Tests for image deduplication."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from workflow_recorder.aggregation.deduplication import (
    compute_image_hash,
    deduplicate_frames,
    hamming_distance,
    is_similar,
)


def test_compute_image_hash(test_image):
    h = compute_image_hash(test_image)
    assert isinstance(h, str)
    assert len(h) == 16  # phash produces 64-bit hash = 16 hex chars


def test_identical_images_have_zero_distance(test_image, tmp_path):
    copy_path = tmp_path / "copy.png"
    shutil.copy2(test_image, copy_path)
    h1 = compute_image_hash(test_image)
    h2 = compute_image_hash(copy_path)
    assert hamming_distance(h1, h2) == 0


def test_different_images_have_nonzero_distance(test_image, test_image_alt):
    h1 = compute_image_hash(test_image)
    h2 = compute_image_hash(test_image_alt)
    assert hamming_distance(h1, h2) > 0


def test_is_similar_identical(test_image, tmp_path):
    copy_path = tmp_path / "copy.png"
    shutil.copy2(test_image, copy_path)
    h1 = compute_image_hash(test_image)
    h2 = compute_image_hash(copy_path)
    assert is_similar(h1, h2, threshold=0.95) == True


def test_is_similar_different(test_image, test_image_alt):
    h1 = compute_image_hash(test_image)
    h2 = compute_image_hash(test_image_alt)
    assert is_similar(h1, h2, threshold=0.95) == False


def test_deduplicate_empty():
    assert deduplicate_frames([], threshold=0.95) == []


def test_deduplicate_single(test_image):
    result = deduplicate_frames([test_image], threshold=0.95)
    assert result == [0]


def test_deduplicate_identical_frames(test_image, tmp_path):
    """Consecutive identical frames should be deduplicated to just the first."""
    paths = []
    for i in range(5):
        p = tmp_path / f"frame_{i}.png"
        shutil.copy2(test_image, p)
        paths.append(p)
    result = deduplicate_frames(paths, threshold=0.95)
    assert result == [0]


def test_deduplicate_different_frames(test_image, test_image_alt, tmp_path):
    """Different frames should all be kept."""
    result = deduplicate_frames([test_image, test_image_alt], threshold=0.95)
    assert result == [0, 1]


def test_deduplicate_mixed(test_image, test_image_alt, tmp_path):
    """Mix of identical and different frames."""
    copy1 = tmp_path / "copy1.png"
    copy2 = tmp_path / "copy2.png"
    shutil.copy2(test_image, copy1)
    shutil.copy2(test_image, copy2)

    paths = [test_image, copy1, test_image_alt, copy2]
    result = deduplicate_frames(paths, threshold=0.95)
    # Should keep: 0 (first), 2 (different from 1), 3 (different from 2 = alt)
    assert 0 in result
    assert 2 in result
