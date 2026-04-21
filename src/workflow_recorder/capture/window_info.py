"""Active window detection.

Uses platform-specific APIs:
- Windows: pywin32 (win32gui/win32process) + psutil
- macOS:   AppKit (frontmost app) + Quartz (window bounds/title)
- Linux:   not supported
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

        import win32con
        placement = win32gui.GetWindowPlacement(hwnd)
        is_maximized = placement[1] == win32con.SW_SHOWMAXIMIZED

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
    """Frontmost application + its frontmost on-screen window.

    Uses NSWorkspace for the app identity and Quartz
    CGWindowListCopyWindowInfo to find the topmost on-screen window
    belonging to that PID. Window titles are only populated if the
    hosting process has been granted Screen Recording permission; when
    unavailable we fall back to the app's localized name.
    """
    try:
        from AppKit import NSWorkspace
    except ImportError:
        return None

    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None

        app_name = str(app.localizedName() or "unknown")
        pid = int(app.processIdentifier() or 0)

        title = app_name
        bounds: tuple[int, int, int, int] = (0, 0, 0, 0)

        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGWindowListOptionOnScreenOnly,
                kCGWindowListExcludeDesktopElements,
                kCGNullWindowID,
            )

            opts = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
            windows = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) or []
            for w in windows:
                if int(w.get("kCGWindowOwnerPID", -1)) != pid:
                    continue
                # Skip invisible/layered system windows
                if int(w.get("kCGWindowLayer", 0)) != 0:
                    continue
                b = w.get("kCGWindowBounds") or {}
                x = int(b.get("X", 0))
                y = int(b.get("Y", 0))
                width = int(b.get("Width", 0))
                height = int(b.get("Height", 0))
                if width <= 0 or height <= 0:
                    continue
                bounds = (x, y, x + width, y + height)
                name = w.get("kCGWindowName")
                if name:
                    title = str(name)
                break
        except ImportError:
            pass

        return WindowContext(
            process_name=app_name,
            window_title=title,
            window_rect=bounds,
            is_maximized=False,
            pid=pid,
        )
    except Exception:
        return None
