"""Win32 cursor position + focused-control rect capture.

These are authoritative for "where is the user interacting on screen" —
captured at screenshot time, pixel-accurate, and independent of what qwen
later decides about the image. On non-Windows platforms the functions
return None so the rest of the pipeline treats those frames as having no
interaction marker.
"""

from __future__ import annotations

import sys


def get_cursor_position() -> tuple[int, int] | None:
    """Return the cursor's current screen coordinates, or None if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return (int(point.x), int(point.y))
    except (OSError, AttributeError):
        return None


def get_focus_rect() -> list[int] | None:
    """Return the focused control's bounding rect [x1,y1,x2,y2] in screen coords,
    or None if no focused control / platform not supported."""
    if sys.platform != "win32":
        return None
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

    # Clamp to image pixel space (right edge can round up to width)
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
    # If only one corner is in-monitor, clamp the other to the image bounds
    img_w = int(monitor_width * downscale_factor)
    img_h = int(monitor_height * downscale_factor)
    if top_left is None:
        top_left = (0, 0)
    if bot_right is None:
        bot_right = (img_w - 1, img_h - 1)
    return [top_left[0], top_left[1], bot_right[0], bot_right[1]]
