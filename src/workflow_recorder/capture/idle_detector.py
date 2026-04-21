"""Detect how long the user has been idle (no mouse/keyboard input).

Platform support:
  • Windows: GetLastInputInfo() — system-wide tick count of the most
    recent input event.
  • macOS:   CGEventSourceSecondsSinceLastEventType with
    kCGAnyInputEventType — system-wide idle time in seconds.

On unsupported platforms (or when the underlying call fails), the
detector reports 0.0 seconds idle, which effectively disables backoff
so the recorder keeps running at the base interval. That matches what
operators running the recorder expect, while keeping dev environments
usable for testing.
"""

from __future__ import annotations

import sys


class IdleDetector:
    """Reports how many seconds since the last system-wide input event."""

    def __init__(self) -> None:
        self._impl = None
        if sys.platform == "win32":
            self._impl = _Win32IdleImpl()
            if not self._impl.ok:
                self._impl = None
        elif sys.platform == "darwin":
            self._impl = _MacOSIdleImpl()
            if not self._impl.ok:
                self._impl = None

    def seconds_since_last_input(self) -> float:
        """Return seconds since the last mouse/keyboard event.

        Returns 0.0 on unsupported platforms or if the OS call fails —
        this means the recorder will treat the user as always active and
        never enter backoff. That's a safer default than incorrectly
        sleeping forever.
        """
        if self._impl is None:
            return 0.0
        try:
            return self._impl.seconds_since_last_input()
        except Exception:
            return 0.0

    @property
    def available(self) -> bool:
        """True if the underlying OS API is usable on this platform."""
        return self._impl is not None


class _Win32IdleImpl:
    def __init__(self) -> None:
        self.ok = False
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
            self.ok = True
        except (ImportError, OSError, AttributeError):
            pass

    def seconds_since_last_input(self) -> float:
        import ctypes
        lii = self._lii_cls()
        lii.cbSize = ctypes.sizeof(self._lii_cls)
        if not self._user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        now_ticks = self._kernel32.GetTickCount()
        elapsed_ms = now_ticks - lii.dwTime
        if elapsed_ms < 0:
            return 0.0
        return elapsed_ms / 1000.0


class _MacOSIdleImpl:
    def __init__(self) -> None:
        self.ok = False
        try:
            from Quartz import (
                CGEventSourceSecondsSinceLastEventType,
                kCGEventSourceStateHIDSystemState,
                kCGAnyInputEventType,
            )
            self._fn = CGEventSourceSecondsSinceLastEventType
            self._source_state = kCGEventSourceStateHIDSystemState
            self._any = kCGAnyInputEventType
            self.ok = True
        except ImportError:
            pass

    def seconds_since_last_input(self) -> float:
        v = self._fn(self._source_state, self._any)
        if v is None or v < 0:
            return 0.0
        return float(v)


class IdleBackoff:
    """Computes the next capture interval based on idle state.

    Three-tier strategy:
      • Active (idle < light_idle_threshold):
            current_interval = base_interval
      • Light idle (light_idle_threshold ≤ idle < idle_threshold):
            current_interval = max(base_interval, light_idle_interval)
        — i.e. when base is already >= light_idle_interval this tier is a
        no-op and we stay at base until deep idle. When base < light_idle
        (e.g. 1s base), we bump to light_idle (3s) so we spend fewer shots
        on short pauses between keystrokes.
      • Deep idle (idle ≥ idle_threshold):
            exponential × backoff_factor, capped at max_interval
    """

    def __init__(
        self,
        base_interval: float,
        max_interval: float,
        idle_threshold_seconds: float,
        backoff_factor: float = 2.0,
        light_idle_threshold_seconds: float | None = None,
        light_idle_interval_seconds: float | None = None,
    ) -> None:
        if base_interval <= 0:
            raise ValueError("base_interval must be positive")
        if max_interval < base_interval:
            raise ValueError("max_interval must be >= base_interval")
        self.base_interval = base_interval
        self.max_interval = max_interval
        self.idle_threshold_seconds = idle_threshold_seconds
        self.backoff_factor = backoff_factor
        self.light_idle_threshold_seconds = light_idle_threshold_seconds
        self.light_idle_interval_seconds = light_idle_interval_seconds
        self._current_interval = base_interval

    @property
    def current_interval(self) -> float:
        return self._current_interval

    def update(self, seconds_since_last_input: float) -> float:
        """Recompute interval based on current idle state. Returns new interval."""
        if seconds_since_last_input >= self.idle_threshold_seconds:
            self._current_interval = min(
                self._current_interval * self.backoff_factor,
                self.max_interval,
            )
        elif (
            self.light_idle_threshold_seconds is not None
            and self.light_idle_interval_seconds is not None
            and seconds_since_last_input >= self.light_idle_threshold_seconds
        ):
            target = max(self.base_interval, self.light_idle_interval_seconds)
            self._current_interval = min(target, self.max_interval)
        else:
            self._current_interval = self.base_interval
        return self._current_interval

    def reset(self) -> None:
        """Force reset to base interval (e.g. on session start)."""
        self._current_interval = self.base_interval
