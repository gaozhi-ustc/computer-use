"""Detect how long the user has been idle (no mouse/keyboard input).

Uses Win32 GetLastInputInfo() — system-wide tick count of the most recent
input event, no per-application hooks needed.

Falls back to "always active" on non-Windows platforms (returns 0 seconds
since last input), which effectively disables backoff so the recorder
keeps running at the base interval. This matches the behavior employees
running the recorder on Windows expect, while keeping macOS/Linux dev
environments usable for testing.
"""

from __future__ import annotations

import sys
import time


class IdleDetector:
    """Reports how many seconds since the last system-wide input event."""

    def __init__(self) -> None:
        self._win32_available = False
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.UINT),
                        ("dwTime", wintypes.DWORD),
                    ]

                self._lii_cls = LASTINPUTINFO
                self._user32 = ctypes.windll.user32
                self._kernel32 = ctypes.windll.kernel32
                self._win32_available = True
            except (ImportError, OSError, AttributeError):
                pass

    def seconds_since_last_input(self) -> float:
        """Return seconds since the last mouse/keyboard event.

        Returns 0.0 on non-Windows or if the Win32 call fails — this means
        the recorder will treat the user as always active and never enter
        backoff. That's a safer default than incorrectly sleeping forever.
        """
        if not self._win32_available:
            return 0.0
        try:
            lii = self._lii_cls()
            lii.cbSize = self._lii_cls.cbSize.size  # type: ignore[attr-defined]
            # cbSize must equal sizeof(LASTINPUTINFO)
            import ctypes
            lii.cbSize = ctypes.sizeof(self._lii_cls)
            if not self._user32.GetLastInputInfo(ctypes.byref(lii)):
                return 0.0
            now_ticks = self._kernel32.GetTickCount()
            elapsed_ms = now_ticks - lii.dwTime
            # Tick count wraps every ~49.7 days; treat negative as 0
            if elapsed_ms < 0:
                return 0.0
            return elapsed_ms / 1000.0
        except Exception:
            return 0.0

    @property
    def available(self) -> bool:
        """True if the underlying OS API is usable (Windows only for now)."""
        return self._win32_available


class IdleBackoff:
    """Computes the next capture interval based on idle state.

    Strategy: stay at base_interval while user is active. Once idle
    threshold is exceeded, double the interval on each subsequent capture
    up to max_interval. Reset to base_interval as soon as input is
    detected (current_idle_seconds drops below the threshold).
    """

    def __init__(
        self,
        base_interval: float,
        max_interval: float,
        idle_threshold_seconds: float,
        backoff_factor: float = 2.0,
    ) -> None:
        if base_interval <= 0:
            raise ValueError("base_interval must be positive")
        if max_interval < base_interval:
            raise ValueError("max_interval must be >= base_interval")
        self.base_interval = base_interval
        self.max_interval = max_interval
        self.idle_threshold_seconds = idle_threshold_seconds
        self.backoff_factor = backoff_factor
        self._current_interval = base_interval

    @property
    def current_interval(self) -> float:
        return self._current_interval

    def update(self, seconds_since_last_input: float) -> float:
        """Recompute interval based on current idle state. Returns new interval."""
        if seconds_since_last_input >= self.idle_threshold_seconds:
            # Idle — back off
            self._current_interval = min(
                self._current_interval * self.backoff_factor,
                self.max_interval,
            )
        else:
            # Active — reset
            self._current_interval = self.base_interval
        return self._current_interval

    def reset(self) -> None:
        """Force reset to base interval (e.g. on session start)."""
        self._current_interval = self.base_interval
