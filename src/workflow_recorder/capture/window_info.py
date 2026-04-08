"""Active window detection.

Uses platform-specific APIs:
- Windows: pywin32 (win32gui/win32process) + psutil
- macOS: AppKit (for development/testing)
- Linux: not supported
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class WindowContext:
    process_name: str
    window_title: str
    window_rect: tuple[int, int, int, int]  # (left, top, right, bottom)
    is_maximized: bool
    pid: int


def get_active_window() -> WindowContext | None:
    """Get information about the currently active window.

    Returns None if no window is focused or detection fails.
    """
    if sys.platform == "win32":
        return _get_active_window_win32()
    elif sys.platform == "darwin":
        return _get_active_window_macos()
    return None


def _get_active_window_win32() -> WindowContext | None:
    try:
        import win32gui
        import win32process
        import psutil
    except ImportError:
        return None

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None

        title = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd)

        # Check if maximized
        import win32con
        placement = win32gui.GetWindowPlacement(hwnd)
        is_maximized = placement[1] == win32con.SW_SHOWMAXIMIZED

        # Get process info
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid)
            process_name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_name = "unknown"

        return WindowContext(
            process_name=process_name,
            window_title=title,
            window_rect=rect,
            is_maximized=is_maximized,
            pid=pid,
        )
    except Exception:
        return None


def _get_active_window_macos() -> WindowContext | None:
    """macOS fallback for development — uses AppKit."""
    try:
        from AppKit import NSWorkspace
        import subprocess
    except ImportError:
        return None

    try:
        workspace = NSWorkspace.sharedWorkspace()
        active_app = workspace.activeApplication()
        if not active_app:
            return None

        app_name = active_app.get("NSApplicationName", "unknown")
        pid = active_app.get("NSApplicationProcessIdentifier", 0)

        # Get frontmost window title via AppleScript
        script = 'tell application "System Events" to get name of first window of (first process whose frontmost is true)'
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            title = result.stdout.strip() if result.returncode == 0 else app_name
        except Exception:
            title = app_name

        return WindowContext(
            process_name=app_name,
            window_title=title,
            window_rect=(0, 0, 0, 0),
            is_maximized=False,
            pid=pid,
        )
    except Exception:
        return None
