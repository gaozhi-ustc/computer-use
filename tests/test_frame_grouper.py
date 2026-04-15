"""Tests for server/frame_grouper.py — lightweight frame clustering."""

from __future__ import annotations

import pytest


def _frame(idx: int, *, app: str = "chrome", ts: float = 0.0,
           cx: int = 100, cy: int = 100, image_path: str = "") -> dict:
    """Build a minimal frame dict for grouper input."""
    return {
        "id": idx,
        "frame_index": idx,
        "window_title_raw": app,
        "recorded_at": f"2026-04-15T10:00:{ts:05.2f}+00:00",
        "timestamp": 1000.0 + ts,
        "cursor_x": cx,
        "cursor_y": cy,
        "focus_rect": None,
        "image_path": image_path,
    }


class TestBoundaryDetection:
    def test_app_switch_creates_boundary(self):
        from server.frame_grouper import find_boundaries
        frames = [
            _frame(0, app="chrome", ts=0),
            _frame(1, app="chrome", ts=1),
            _frame(2, app="excel", ts=2),
            _frame(3, app="excel", ts=3),
        ]
        boundaries = find_boundaries(frames, use_phash=False)
        assert 2 in boundaries

    def test_time_gap_creates_boundary(self):
        from server.frame_grouper import find_boundaries
        frames = [
            _frame(0, ts=0),
            _frame(1, ts=1),
            _frame(2, ts=2),
            _frame(3, ts=30),  # huge gap
            _frame(4, ts=31),
        ]
        boundaries = find_boundaries(frames, use_phash=False)
        assert 3 in boundaries

    def test_cursor_jump_creates_boundary(self):
        from server.frame_grouper import find_boundaries
        frames = [
            _frame(0, cx=100, cy=100, ts=0),
            _frame(1, cx=110, cy=110, ts=1),
            _frame(2, cx=1500, cy=900, ts=2),  # big jump
            _frame(3, cx=1510, cy=910, ts=3),
        ]
        boundaries = find_boundaries(
            frames, use_phash=False,
            screen_width=1920, screen_height=1080,
        )
        assert 2 in boundaries

    def test_no_boundary_for_similar_frames(self):
        from server.frame_grouper import find_boundaries
        frames = [_frame(i, ts=float(i)) for i in range(5)]
        boundaries = find_boundaries(frames, use_phash=False)
        assert len(boundaries) == 0


class TestGroupSplitting:
    def test_split_with_overlap(self):
        from server.frame_grouper import split_with_overlap
        frame_ids = [0, 1, 2, 3, 4, 5, 6, 7]
        boundaries = [4]
        groups = split_with_overlap(frame_ids, boundaries, overlap=3)
        assert len(groups) == 2
        assert groups[0] == [0, 1, 2, 3, 4, 5, 6]
        assert groups[1] == [1, 2, 3, 4, 5, 6, 7]

    def test_small_group_merged(self):
        from server.frame_grouper import split_with_overlap
        frame_ids = [0, 1, 2, 3, 4]
        boundaries = [4]  # second group has only 1 frame
        groups = split_with_overlap(frame_ids, boundaries, overlap=3,
                                     min_group_size=2)
        assert len(groups) == 1
        assert groups[0] == [0, 1, 2, 3, 4]

    def test_no_boundaries_single_group(self):
        from server.frame_grouper import split_with_overlap
        frame_ids = [0, 1, 2, 3]
        groups = split_with_overlap(frame_ids, [], overlap=3)
        assert len(groups) == 1
        assert groups[0] == [0, 1, 2, 3]


class TestGroupFrames:
    def test_end_to_end_grouping(self):
        from server.frame_grouper import group_frames
        frames = [
            _frame(0, app="chrome", ts=0),
            _frame(1, app="chrome", ts=1),
            _frame(2, app="chrome", ts=2),
            _frame(3, app="chrome", ts=3),
            _frame(4, app="chrome", ts=4),
            _frame(5, app="excel", ts=5),
            _frame(6, app="excel", ts=6),
            _frame(7, app="excel", ts=7),
            _frame(8, app="excel", ts=8),
            _frame(9, app="excel", ts=9),
        ]
        groups = group_frames(frames, use_phash=False)
        assert len(groups) == 2
        assert groups[0].primary_application == "chrome"
        assert groups[1].primary_application == "excel"
        # Overlap: some excel frame_ids appear in the first group
        assert any(fid in groups[0].frame_ids for fid in [5, 6, 7])
