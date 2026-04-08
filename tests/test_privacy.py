"""Tests for privacy filtering."""

from __future__ import annotations

from workflow_recorder.capture.privacy import should_skip_frame
from workflow_recorder.capture.window_info import WindowContext
from workflow_recorder.config import PrivacyConfig


def _make_context(process_name: str, window_title: str) -> WindowContext:
    return WindowContext(
        process_name=process_name,
        window_title=window_title,
        window_rect=(0, 0, 1920, 1080),
        is_maximized=False,
        pid=1234,
    )


def test_excluded_app():
    config = PrivacyConfig(excluded_apps=["KeePass.exe"])
    ctx = _make_context("KeePass.exe", "KeePass")
    assert should_skip_frame(ctx, config) is True


def test_excluded_app_case_insensitive():
    config = PrivacyConfig(excluded_apps=["keepass.exe"])
    ctx = _make_context("KeePass.exe", "KeePass")
    assert should_skip_frame(ctx, config) is True


def test_excluded_title_pattern():
    config = PrivacyConfig(excluded_window_titles=[".*Incognito.*"])
    ctx = _make_context("chrome.exe", "New Tab - Incognito - Google Chrome")
    assert should_skip_frame(ctx, config) is True


def test_allowed_app():
    config = PrivacyConfig(excluded_apps=["KeePass.exe"])
    ctx = _make_context("notepad.exe", "Untitled - Notepad")
    assert should_skip_frame(ctx, config) is False


def test_none_context():
    config = PrivacyConfig(excluded_apps=["KeePass.exe"])
    assert should_skip_frame(None, config) is False
