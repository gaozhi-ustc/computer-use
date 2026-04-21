"""OS-level cursor position + focused-control rect capture.

These are authoritative for "where is the user interacting on screen" —
captured at screenshot time, pixel-accurate, and independent of what qwen
later decides about the image.

Platform support:
  • Windows: Win32 user32/GetCursorPos/GetFocus/GetAsyncKeyState
  • macOS:   Quartz / ApplicationServices (pyobjc)
      - cursor: CGEventGetLocation
      - focus rect: Accessibility API (requires user-granted permission)
      - keystroke/click detection: CGEventSourceCounterForEventType

On unsupported platforms (or when the underlying API call fails — e.g.
Accessibility permission not granted on macOS), these functions return
None / False so the rest of the pipeline treats the frame as having no
interaction marker rather than crashing.
"""

from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# Cursor position
# ---------------------------------------------------------------------------


def get_cursor_position() -> tuple[int, int] | None:
    """Return the cursor's current screen coordinates, or None if unavailable."""
    if sys.platform == "win32":
        return _get_cursor_position_win32()
    if sys.platform == "darwin":
        return _get_cursor_position_macos()
    return None


def _get_cursor_position_win32() -> tuple[int, int] | None:
    try:
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return (int(point.x), int(point.y))
    except (OSError, AttributeError):
        return None


def _get_cursor_position_macos() -> tuple[int, int] | None:
    try:
        from Quartz import CGEventCreate, CGEventGetLocation
    except ImportError:
        return None
    try:
        evt = CGEventCreate(None)
        if evt is None:
            return None
        pt = CGEventGetLocation(evt)
        return (int(pt.x), int(pt.y))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Mouse motion sampling
# ---------------------------------------------------------------------------


def is_mouse_moving(sample_interval_ms: int = 150) -> bool:
    """Return True if the cursor moved during a short sample window.

    Samples cursor position twice with a brief gap. Returns False on
    unsupported platforms or if either sample fails.
    """
    p1 = get_cursor_position()
    if p1 is None:
        return False
    import time as _time
    _time.sleep(max(0, sample_interval_ms) / 1000.0)
    p2 = get_cursor_position()
    if p2 is None:
        return False
    return p1 != p2


# ---------------------------------------------------------------------------
# Wait for click/key input
# ---------------------------------------------------------------------------


def wait_for_click_or_key(
    max_wait_seconds: float = 3.0,
    poll_interval_ms: int = 100,
    stop_event=None,
) -> bool:
    """Block until a mouse click or keystroke is detected, then return True.

    Returns False if max_wait_seconds elapses without a click/keystroke,
    or if stop_event is set. On unsupported platforms returns False
    immediately (callers fall through to a time-based capture).
    """
    if sys.platform == "win32":
        return _wait_for_click_or_key_win32(
            max_wait_seconds, poll_interval_ms, stop_event
        )
    if sys.platform == "darwin":
        return _wait_for_click_or_key_macos(
            max_wait_seconds, poll_interval_ms, stop_event
        )
    return False


def _wait_for_click_or_key_win32(
    max_wait_seconds: float,
    poll_interval_ms: int,
    stop_event,
) -> bool:
    try:
        import ctypes
        user32 = ctypes.windll.user32
    except (ImportError, OSError, AttributeError):
        return False

    import time as _time

    watched = [
        0x01, 0x02, 0x04, 0x05, 0x06,            # mouse: L, R, M, X1, X2
        0x08, 0x09, 0x0D, 0x1B, 0x20, 0x2D, 0x2E,  # back, tab, enter, esc, space, insert, delete
        0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # pgup, pgdn, end, home, arrows
    ]
    watched.extend(range(0x30, 0x5A + 1))   # 0-9, A-Z
    watched.extend(range(0x60, 0x6F + 1))   # numpad 0-9 + ops
    watched.extend(range(0x70, 0x87 + 1))   # F1-F24

    for vk in watched:
        try:
            user32.GetAsyncKeyState(vk)
        except Exception:
            return False

    deadline = _time.monotonic() + max_wait_seconds
    poll_s = max(0.01, poll_interval_ms / 1000.0)
    while _time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        for vk in watched:
            state = user32.GetAsyncKeyState(vk)
            if state & 0x0001:
                return True
        _time.sleep(poll_s)
    return False


def _wait_for_click_or_key_macos(
    max_wait_seconds: float,
    poll_interval_ms: int,
    stop_event,
) -> bool:
    """Poll CGEventSourceCounterForEventType for mouse/keyboard events.

    The counter is a monotonically increasing count of events of a given
    type across all sources on the system. We sum counters for the event
    types we care about, snapshot the baseline, then poll until the sum
    increases or the deadline expires.

    No Accessibility permission required for this API.
    """
    try:
        from Quartz import (
            CGEventSourceCounterForEventType,
            kCGEventSourceStateCombinedSessionState,
            kCGEventLeftMouseDown,
            kCGEventRightMouseDown,
            kCGEventOtherMouseDown,
            kCGEventKeyDown,
            kCGEventFlagsChanged,
            kCGEventScrollWheel,
        )
    except ImportError:
        return False

    import time as _time

    source_state = kCGEventSourceStateCombinedSessionState
    watched_types = (
        kCGEventLeftMouseDown,
        kCGEventRightMouseDown,
        kCGEventOtherMouseDown,
        kCGEventKeyDown,
        kCGEventFlagsChanged,
        kCGEventScrollWheel,
    )

    def _total() -> int:
        try:
            return sum(
                int(CGEventSourceCounterForEventType(source_state, et))
                for et in watched_types
            )
        except Exception:
            return -1

    baseline = _total()
    if baseline < 0:
        return False

    deadline = _time.monotonic() + max_wait_seconds
    poll_s = max(0.01, poll_interval_ms / 1000.0)
    while _time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        current = _total()
        if current < 0:
            return False
        if current > baseline:
            return True
        _time.sleep(poll_s)
    return False


# ---------------------------------------------------------------------------
# Focused-control rect
# ---------------------------------------------------------------------------


def get_focus_rect() -> list[int] | None:
    """Return the focused control's bounding rect [x1,y1,x2,y2] in screen
    coords, or None if no focused control / platform not supported / the
    OS API refused (e.g. missing Accessibility permission on macOS)."""
    if sys.platform == "win32":
        return _get_focus_rect_win32()
    if sys.platform == "darwin":
        return _get_focus_rect_macos()
    return None


def _get_focus_rect_win32() -> list[int] | None:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetFocus()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    except (OSError, AttributeError):
        return None


def _get_focus_rect_macos() -> list[int] | None:
    """Use the Accessibility API to find the focused UI element's screen rect.

    Requires the operator to grant the hosting app "Accessibility" access
    (System Settings → Privacy & Security → Accessibility). Without that,
    every AX call returns an error and we fall back to None, which the
    pipeline already tolerates.

    macOS AX positions use the same top-left-origin screen coordinate
    space as CGEventGetLocation — no y-flip needed for our usage.
    """
    try:
        from AppKit import NSWorkspace
        from ApplicationServices import (
            AXUIElementCreateApplication,
            AXUIElementCopyAttributeValue,
            AXValueGetValue,
            kAXFocusedUIElementAttribute,
            kAXPositionAttribute,
            kAXSizeAttribute,
            kAXValueCGPointType,
            kAXValueCGSizeType,
        )
    except ImportError:
        return None

    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        if pid <= 0:
            return None

        app_ref = AXUIElementCreateApplication(pid)
        if app_ref is None:
            return None

        err, focused = AXUIElementCopyAttributeValue(
            app_ref, kAXFocusedUIElementAttribute, None
        )
        if err != 0 or focused is None:
            return None

        err_p, pos_val = AXUIElementCopyAttributeValue(
            focused, kAXPositionAttribute, None
        )
        err_s, size_val = AXUIElementCopyAttributeValue(
            focused, kAXSizeAttribute, None
        )
        if err_p != 0 or err_s != 0 or pos_val is None or size_val is None:
            return None

        # AXValueGetValue unpacks the opaque AXValue into a CGPoint / CGSize.
        # pyobjc returns a tuple: (success_bool, struct_value). The struct
        # exposes .x/.y or .width/.height attributes.
        ok_p, point = AXValueGetValue(pos_val, kAXValueCGPointType, None)
        ok_s, size = AXValueGetValue(size_val, kAXValueCGSizeType, None)
        if not ok_p or not ok_s:
            return None

        x1 = int(point.x)
        y1 = int(point.y)
        x2 = int(point.x + size.width)
        y2 = int(point.y + size.height)
        return [x1, y1, x2, y2]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Coordinate transforms
# ---------------------------------------------------------------------------


def screen_to_image_coords(
    screen_x: int,
    screen_y: int,
    monitor_left: int,
    monitor_top: int,
    monitor_width: int,
    monitor_height: int,
    downscale_factor: float,
) -> tuple[int, int] | None:
    """Convert screen-absolute coords to image-pixel coords of the captured frame.

    - Subtracts monitor origin (for multi-monitor setups where primary isn't at 0,0).
    - Returns None if the point lies outside the captured monitor.
    - Applies downscale_factor so coords match the uploaded image's pixel space.
    - Clamps the result into [0, image_width) × [0, image_height).
    """
    rel_x = screen_x - monitor_left
    rel_y = screen_y - monitor_top
    if rel_x < 0 or rel_y < 0:
        return None
    if rel_x >= monitor_width or rel_y >= monitor_height:
        return None

    img_x = int(rel_x * downscale_factor)
    img_y = int(rel_y * downscale_factor)

    img_w = int(monitor_width * downscale_factor)
    img_h = int(monitor_height * downscale_factor)
    img_x = max(0, min(img_x, img_w - 1))
    img_y = max(0, min(img_y, img_h - 1))
    return (img_x, img_y)


def rect_to_image_coords(
    rect: list[int],
    monitor_left: int,
    monitor_top: int,
    monitor_width: int,
    monitor_height: int,
    downscale_factor: float,
) -> list[int] | None:
    """Convert a screen-space rect [x1,y1,x2,y2] to image-pixel rect.

    Returns None if the rect is entirely outside the captured monitor.
    """
    if len(rect) != 4:
        return None
    x1, y1, x2, y2 = rect
    top_left = screen_to_image_coords(
        x1, y1, monitor_left, monitor_top, monitor_width, monitor_height,
        downscale_factor,
    )
    bot_right = screen_to_image_coords(
        x2, y2, monitor_left, monitor_top, monitor_width, monitor_height,
        downscale_factor,
    )
    if top_left is None and bot_right is None:
        return None
    img_w = int(monitor_width * downscale_factor)
    img_h = int(monitor_height * downscale_factor)
    if top_left is None:
        top_left = (0, 0)
    if bot_right is None:
        bot_right = (img_w - 1, img_h - 1)
    return [top_left[0], top_left[1], bot_right[0], bot_right[1]]
