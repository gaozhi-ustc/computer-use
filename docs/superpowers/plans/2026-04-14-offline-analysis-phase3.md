# Offline Analysis — Phase 3: Client Rewrite

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite the client daemon to upload raw screenshots to the server instead of calling qwen locally. Capture cursor position + focused-control rect from Win32 APIs at screenshot time, bundle them with the image, and push via the new `/frames/upload` endpoint.

**Architecture:** Single capture thread (no more analysis thread). Each capture collects: PNG image + Win32 `GetCursorPos()` + `GetFocus() + GetWindowRect()` coords, converted to image-pixel coordinates via the downscale factor. `ImageUploader` replaces `FramePusher` — same queue+buffer architecture but uploads multipart image instead of JSON.

**Tech Stack:** `ctypes.windll` for Win32 cursor/focus queries, existing `mss` for screen capture, `httpx.Client` multipart upload, existing `threading.Thread` + `queue.Queue` pattern.

**Depends on:** Phase 1 (`POST /frames/upload` endpoint). Phase 2 (AnalysisPool) not required at build time but needed for end-to-end validation.

---

## File Structure

### New files

```
src/workflow_recorder/
├── capture/
│   └── cursor_focus.py      # Win32 GetCursorPos + GetFocus helpers with fallback
└── image_uploader.py        # thread + queue + JSONL buffer, multipart upload
tests/
├── test_cursor_focus.py
└── test_image_uploader.py
```

### Modified files

```
src/workflow_recorder/
├── capture/
│   └── screenshot.py        # capture cursor+focus into CaptureResult
├── daemon.py                # remove _analysis_loop; capture_loop uploads via ImageUploader
├── config.py                # interval_seconds default 15→3
└── __main__.py              # banner tweaks (no more "qwen API key" line — server has the key)
```

### Deleted files

```
src/workflow_recorder/frame_pusher.py   # superseded by image_uploader.py
tests/test_frame_pusher.py              # superseded by test_image_uploader.py
```

---

### Task 1: Win32 cursor + focus helpers

**Files:**
- Create: `src/workflow_recorder/capture/cursor_focus.py`
- Create: `tests/test_cursor_focus.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cursor_focus.py`:

```python
"""Tests for Win32 cursor / focus helpers with non-Windows fallback."""

from __future__ import annotations

import sys

import pytest


def test_get_cursor_position_returns_tuple_or_none():
    """On Windows returns (x, y); on other OS returns None."""
    from workflow_recorder.capture.cursor_focus import get_cursor_position
    result = get_cursor_position()
    if sys.platform == "win32":
        assert result is not None
        assert len(result) == 2
        x, y = result
        assert isinstance(x, int)
        assert isinstance(y, int)
    else:
        assert result is None


def test_get_focus_rect_returns_rect_or_none():
    """On Windows returns [x1,y1,x2,y2] when a focused control exists, else None."""
    from workflow_recorder.capture.cursor_focus import get_focus_rect
    result = get_focus_rect()
    if sys.platform != "win32":
        assert result is None
    else:
        # Platform is Windows but there may or may not be a focused control
        assert result is None or (isinstance(result, list) and len(result) == 4)


def test_screen_to_image_coords_identity_no_downscale():
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # monitor at (0,0), 1920x1080, no downscale
    x, y = screen_to_image_coords(
        screen_x=500, screen_y=300,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=1.0,
    )
    assert x == 500
    assert y == 300


def test_screen_to_image_coords_with_downscale():
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # 1920x1080 monitor at (0,0), downscale 0.5
    x, y = screen_to_image_coords(
        screen_x=1000, screen_y=600,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=0.5,
    )
    assert x == 500
    assert y == 300


def test_screen_to_image_coords_offset_monitor():
    """Secondary monitor at (1920, 0) — coords should be relative to its origin."""
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    x, y = screen_to_image_coords(
        screen_x=2500, screen_y=400,
        monitor_left=1920, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=1.0,
    )
    assert x == 580  # 2500 - 1920
    assert y == 400


def test_screen_to_image_coords_returns_none_if_outside_monitor():
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # Cursor on secondary monitor, asking for primary's coords
    result = screen_to_image_coords(
        screen_x=3000, screen_y=400,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=1.0,
    )
    assert result is None


def test_screen_to_image_coords_clamps_to_image_bounds():
    """If cursor is right at the edge, result stays within image dimensions."""
    from workflow_recorder.capture.cursor_focus import screen_to_image_coords
    # Cursor at (1919, 1079) on a 1920x1080 monitor with 0.5 downscale -> (959, 539)
    x, y = screen_to_image_coords(
        screen_x=1919, screen_y=1079,
        monitor_left=0, monitor_top=0,
        monitor_width=1920, monitor_height=1080,
        downscale_factor=0.5,
    )
    assert 0 <= x < 960
    assert 0 <= y < 540
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_cursor_focus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow_recorder.capture.cursor_focus'`

- [ ] **Step 3: Implement `src/workflow_recorder/capture/cursor_focus.py`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_cursor_focus.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/workflow_recorder/capture/cursor_focus.py tests/test_cursor_focus.py
git commit -m "feat: Win32 cursor + focus rect capture helpers with coord transforms"
```

---

### Task 2: Extend `CaptureResult` with cursor + focus data

**Files:**
- Modify: `src/workflow_recorder/capture/screenshot.py`
- Modify: `tests/test_screenshot.py`

- [ ] **Step 1: Read existing `CaptureResult` dataclass**

Current `CaptureResult` in `src/workflow_recorder/capture/screenshot.py`:

```python
@dataclass
class CaptureResult:
    file_path: Path
    timestamp: float
    width: int
    height: int
    monitor_index: int
```

- [ ] **Step 2: Write failing test**

Add to `tests/test_screenshot.py`:

```python
def test_capture_includes_cursor_and_focus_fields(tmp_path):
    from workflow_recorder.capture.screenshot import capture_screenshot
    result = capture_screenshot(output_dir=tmp_path, monitor=0,
                                 image_format="png", downscale_factor=1.0)
    # On Windows there's always a cursor; on other OS fields may be defaults (-1 / None)
    assert hasattr(result, "cursor_x")
    assert hasattr(result, "cursor_y")
    assert hasattr(result, "focus_rect")
    assert isinstance(result.cursor_x, int)
    assert isinstance(result.cursor_y, int)
    assert result.focus_rect is None or (isinstance(result.focus_rect, list) and len(result.focus_rect) == 4)
```

- [ ] **Step 3: Run test, expect FAIL**

Run: `PYTHONPATH=src python -m pytest tests/test_screenshot.py::test_capture_includes_cursor_and_focus_fields -v`
Expected: FAIL with AttributeError for `cursor_x`

- [ ] **Step 4: Update `CaptureResult` + `capture_screenshot()`**

In `src/workflow_recorder/capture/screenshot.py`, extend the dataclass:

```python
@dataclass
class CaptureResult:
    file_path: Path
    timestamp: float
    width: int
    height: int
    monitor_index: int
    cursor_x: int = -1
    cursor_y: int = -1
    focus_rect: list[int] | None = None
```

And in the `capture_screenshot()` function, after the existing image save and before the `return CaptureResult(...)`, add cursor/focus capture. The full function becomes:

```python
def capture_screenshot(
    output_dir: Path,
    monitor: int = 0,
    image_format: str = "png",
    image_quality: int = 85,
    downscale_factor: float = 1.0,
) -> CaptureResult:
    """Capture a screenshot and save to output_dir."""
    from workflow_recorder.capture.cursor_focus import (
        get_cursor_position, get_focus_rect,
        screen_to_image_coords, rect_to_image_coords,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = time.time()

    # Read cursor position BEFORE the screenshot so we capture what the user
    # was pointing at when the frame was taken.
    cursor_screen = get_cursor_position()
    focus_rect_screen = get_focus_rect()

    with mss.mss() as sct:
        if monitor == -1:
            mon = sct.monitors[0]
            mon_idx = -1
        else:
            mon_idx = monitor
            mss_idx = monitor + 1
            if mss_idx >= len(sct.monitors):
                mss_idx = 1
            mon = sct.monitors[mss_idx]

        raw = sct.grab(mon)

    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    if downscale_factor < 1.0:
        new_w = int(img.width * downscale_factor)
        new_h = int(img.height * downscale_factor)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    filename = f"capture_{int(ts * 1000)}.{image_format}"
    filepath = output_dir / filename

    if image_format == "jpg":
        img.save(filepath, "JPEG", quality=image_quality)
    else:
        img.save(filepath, "PNG")

    # Convert screen coords -> image coords
    cursor_x_img = -1
    cursor_y_img = -1
    if cursor_screen is not None:
        coords = screen_to_image_coords(
            cursor_screen[0], cursor_screen[1],
            mon["left"], mon["top"], mon["width"], mon["height"],
            downscale_factor,
        )
        if coords is not None:
            cursor_x_img, cursor_y_img = coords

    focus_rect_img: list[int] | None = None
    if focus_rect_screen is not None:
        focus_rect_img = rect_to_image_coords(
            focus_rect_screen,
            mon["left"], mon["top"], mon["width"], mon["height"],
            downscale_factor,
        )

    return CaptureResult(
        file_path=filepath,
        timestamp=ts,
        width=img.width,
        height=img.height,
        monitor_index=mon_idx,
        cursor_x=cursor_x_img,
        cursor_y=cursor_y_img,
        focus_rect=focus_rect_img,
    )
```

- [ ] **Step 5: Run all tests**

Run: `PYTHONPATH=src python -m pytest tests/test_screenshot.py tests/test_cursor_focus.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/workflow_recorder/capture/screenshot.py tests/test_screenshot.py
git commit -m "feat: CaptureResult carries OS-captured cursor + focus rect"
```

---

### Task 3: ImageUploader module

**Files:**
- Create: `src/workflow_recorder/image_uploader.py`
- Create: `tests/test_image_uploader.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_image_uploader.py`:

```python
"""Tests for ImageUploader — replaces FramePusher for offline analysis."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from workflow_recorder.image_uploader import ImageUploader


# ---------------------------------------------------------------------------
# FakeHttpxClient — captures calls and returns configurable responses
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None):
        self.status_code = status_code
        self._json = json_body or {"ok": True, "id": 1}
        self.text = json.dumps(self._json)

    def json(self):
        return self._json


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        self._response = FakeResponse(200)
        self._raise = None

    def set_response(self, resp: FakeResponse):
        self._response = resp

    def set_raise(self, exc: Exception):
        self._raise = exc

    def post(self, url, data=None, files=None, headers=None, timeout=None):
        self.calls.append({
            "url": url, "data": data,
            "filenames": list((files or {}).keys()),
            "headers": headers,
        })
        if self._raise is not None:
            raise self._raise
        return self._response

    def close(self):
        pass


@pytest.fixture
def sample_png(tmp_path):
    p = tmp_path / "frame-001.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return p


def _make_uploader(tmp_path, **kwargs):
    defaults = dict(
        server_url="http://127.0.0.1:8000",
        api_key="sk-test",
        employee_id="E001",
        session_id="sess-1",
        buffer_path=str(tmp_path / "buffer.jsonl"),
        timeout=2.0,
        max_retries=2,
    )
    defaults.update(kwargs)
    return ImageUploader(**defaults)


def test_enqueue_uploads_multipart(tmp_path, sample_png, monkeypatch):
    """Uploader posts multipart (image + form fields including cursor/focus)."""
    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)

    up = _make_uploader(tmp_path)
    up.start()
    try:
        up.enqueue(
            image_path=sample_png, frame_index=1, timestamp=100.0,
            cursor_x=50, cursor_y=60, focus_rect=[10, 20, 100, 200],
        )
        # Wait for worker to process
        deadline = time.time() + 2.0
        while time.time() < deadline and not fake.calls:
            time.sleep(0.01)
    finally:
        up.stop(timeout=2.0)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "http://127.0.0.1:8000/frames/upload"
    assert call["data"]["employee_id"] == "E001"
    assert call["data"]["session_id"] == "sess-1"
    assert call["data"]["frame_index"] == "1"
    assert call["data"]["cursor_x"] == "50"
    assert call["data"]["cursor_y"] == "60"
    assert call["data"]["focus_rect"] == "[10, 20, 100, 200]"
    assert call["headers"]["X-API-Key"] == "sk-test"
    assert call["filenames"] == ["image"]


def test_enqueue_empty_focus_rect_sends_empty_string(tmp_path, sample_png, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    try:
        up.enqueue(image_path=sample_png, frame_index=1, timestamp=1.0,
                   cursor_x=-1, cursor_y=-1, focus_rect=None)
        deadline = time.time() + 2.0
        while time.time() < deadline and not fake.calls:
            time.sleep(0.01)
    finally:
        up.stop(timeout=2.0)
    assert fake.calls[0]["data"]["focus_rect"] == ""


def test_upload_failure_writes_to_buffer(tmp_path, sample_png, monkeypatch):
    """After max_retries the payload (minus image) is written to buffer_path."""
    fake = FakeClient()
    fake.set_response(FakeResponse(status_code=500))
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    monkeypatch.setattr("time.sleep", lambda *_: None)  # don't wait on backoff

    up = _make_uploader(tmp_path, max_retries=2)
    up.start()
    try:
        up.enqueue(image_path=sample_png, frame_index=7, timestamp=77.0,
                   cursor_x=0, cursor_y=0, focus_rect=None)
        # Wait for worker to exhaust retries and buffer the item
        deadline = time.time() + 5.0
        buf = Path(tmp_path / "buffer.jsonl")
        while time.time() < deadline and not buf.exists():
            time.sleep(0.05)
    finally:
        up.stop(timeout=2.0)

    buf = Path(tmp_path / "buffer.jsonl")
    assert buf.exists()
    lines = buf.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["frame_index"] == 7
    assert entry["image_path"] == str(sample_png)
    assert entry["cursor_x"] == 0


def test_buffer_replay_on_start(tmp_path, sample_png, monkeypatch):
    """On startup the uploader re-posts any buffer entries whose images still exist."""
    # Pre-populate buffer as if a previous session crashed
    buf = tmp_path / "buffer.jsonl"
    buf.write_text(json.dumps({
        "image_path": str(sample_png),
        "frame_index": 99, "timestamp": 50.0,
        "cursor_x": 1, "cursor_y": 2, "focus_rect": None,
    }) + "\n", encoding="utf-8")

    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and not fake.calls:
            time.sleep(0.01)
    finally:
        up.stop(timeout=2.0)

    assert len(fake.calls) == 1
    assert fake.calls[0]["data"]["frame_index"] == "99"
    # Buffer cleared after successful replay
    assert not buf.exists()


def test_buffer_replay_drops_missing_images(tmp_path, monkeypatch):
    """If the image file in a buffer entry is gone, that entry is dropped."""
    buf = tmp_path / "buffer.jsonl"
    buf.write_text(json.dumps({
        "image_path": "/nonexistent/gone.png",
        "frame_index": 88, "timestamp": 40.0,
        "cursor_x": 1, "cursor_y": 2, "focus_rect": None,
    }) + "\n", encoding="utf-8")

    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    try:
        time.sleep(0.5)  # give worker time to skip the entry
    finally:
        up.stop(timeout=2.0)

    assert len(fake.calls) == 0
    assert not buf.exists() or buf.read_text(encoding="utf-8").strip() == ""


def test_stop_drains_queue_before_returning(tmp_path, sample_png, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr("workflow_recorder.image_uploader._build_client",
                        lambda: fake)
    up = _make_uploader(tmp_path)
    up.start()
    for i in range(3):
        up.enqueue(image_path=sample_png, frame_index=i, timestamp=float(i),
                   cursor_x=0, cursor_y=0, focus_rect=None)
    up.stop(timeout=5.0)

    # All 3 should have been posted
    assert len(fake.calls) == 3


def test_enqueue_before_start_raises_or_ignored(tmp_path, sample_png):
    """Pre-start enqueue should not crash (and not block)."""
    up = _make_uploader(tmp_path)
    # Don't start — enqueue should be a no-op or buffer-write, not raise
    up.enqueue(image_path=sample_png, frame_index=1, timestamp=1.0,
               cursor_x=0, cursor_y=0, focus_rect=None)
    up.stop()  # safe even without start
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_image_uploader.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/workflow_recorder/image_uploader.py`**

```python
"""Background image uploader for the offline analysis architecture.

Same pattern as the old FramePusher (thread + bounded queue + JSONL buffer
on failure, with startup replay), but the payload is a multipart PNG upload
to /frames/upload instead of a JSON POST to /frames.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

import structlog


log = structlog.get_logger()


# Queue size — bounded so a stuck uploader doesn't OOM the client
DEFAULT_QUEUE_SIZE = 200


_STOP_SENTINEL = object()


def _build_client():
    """Factory for the httpx client. Indirection lets tests monkey-patch it."""
    import httpx
    return httpx.Client()


class ImageUploader:
    """Uploads captured PNGs with OS metadata to /frames/upload."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        employee_id: str,
        session_id: str,
        buffer_path: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.employee_id = employee_id
        self.session_id = session_id
        self.buffer_path = Path(buffer_path)
        self.timeout = timeout
        self.max_retries = max_retries

        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the uploader thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="image-uploader", daemon=True
        )
        self._thread.start()
        log.info("image_uploader_started", server_url=self.server_url,
                 employee_id=self.employee_id, session_id=self.session_id)

    def stop(self, timeout: float = 15.0) -> None:
        """Drain the queue and stop. Safe to call without start."""
        if self._thread is None:
            return
        try:
            self._queue.put_nowait(_STOP_SENTINEL)
        except queue.Full:
            pass
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._thread = None

    def enqueue(
        self,
        image_path: Path,
        frame_index: int,
        timestamp: float,
        cursor_x: int = -1,
        cursor_y: int = -1,
        focus_rect: list[int] | None = None,
    ) -> None:
        """Queue one image for upload. Non-blocking; spills to buffer on full queue."""
        item = {
            "image_path": str(image_path),
            "frame_index": int(frame_index),
            "timestamp": float(timestamp),
            "cursor_x": int(cursor_x),
            "cursor_y": int(cursor_y),
            "focus_rect": focus_rect,
        }
        if self._thread is None:
            # Not started — buffer for next start
            self._append_to_buffer(item)
            return
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            log.warning("image_uploader_queue_full", frame_index=frame_index)
            self._append_to_buffer(item)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        client = _build_client()
        try:
            # Replay any leftover buffer from a prior session
            self._replay_buffer(client)
            # Serve live queue
            while True:
                item = self._queue.get()
                if item is _STOP_SENTINEL:
                    break
                self._upload_item(client, item)
        finally:
            try:
                client.close()
            except Exception:
                pass
            log.info("image_uploader_stopped")

    def _upload_item(self, client, item: dict) -> None:
        image_path = Path(item["image_path"])
        if not image_path.exists():
            log.warning("image_uploader_image_missing", path=str(image_path))
            return

        focus_str = (
            json.dumps(item["focus_rect"])
            if item.get("focus_rect") is not None
            else ""
        )
        data = {
            "employee_id": self.employee_id,
            "session_id": self.session_id,
            "frame_index": str(item["frame_index"]),
            "timestamp": str(item["timestamp"]),
            "cursor_x": str(item.get("cursor_x", -1)),
            "cursor_y": str(item.get("cursor_y", -1)),
            "focus_rect": focus_str,
        }
        url = f"{self.server_url}/frames/upload"
        headers = {"X-API-Key": self.api_key} if self.api_key else {}

        for attempt in range(self.max_retries):
            try:
                with open(image_path, "rb") as fp:
                    files = {"image": (image_path.name, fp, "image/png")}
                    response = client.post(
                        url, data=data, files=files,
                        headers=headers, timeout=self.timeout,
                    )
                if 200 <= response.status_code < 300:
                    log.debug("image_uploaded",
                              frame_index=item["frame_index"],
                              response_id=response.json().get("id"))
                    return
                log.warning("image_upload_non_2xx",
                            status=response.status_code,
                            frame_index=item["frame_index"])
            except Exception as exc:
                log.warning("image_upload_error",
                            error=str(exc),
                            attempt=attempt + 1,
                            frame_index=item["frame_index"])
            # Exponential backoff: 1s, 2s, 4s, ...
            time.sleep(2 ** attempt)

        # All retries exhausted — write to buffer
        self._append_to_buffer(item)

    # ------------------------------------------------------------------
    # Buffer persistence
    # ------------------------------------------------------------------

    def _append_to_buffer(self, item: dict) -> None:
        """Persist a queued item to the JSONL buffer for later replay."""
        try:
            self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.buffer_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.error("image_uploader_buffer_write_failed", error=str(exc))

    def _replay_buffer(self, client) -> None:
        """Attempt to re-upload each item in buffer. Delete file on success."""
        if not self.buffer_path.exists():
            return
        try:
            lines = self.buffer_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return

        survivors: list[dict] = []
        replayed = 0
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            image_path = Path(item.get("image_path", ""))
            if not image_path.exists():
                # Image was cleaned up — drop the entry
                continue
            # Try to upload (one attempt — we don't want to thrash on startup)
            try:
                self._upload_item(client, item)
                replayed += 1
            except Exception:
                survivors.append(item)

        # Rewrite buffer with only the ones that failed to replay
        if survivors:
            with open(self.buffer_path, "w", encoding="utf-8") as f:
                for item in survivors:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        else:
            try:
                self.buffer_path.unlink()
            except OSError:
                pass
        if replayed:
            log.info("image_uploader_buffer_replayed", count=replayed)
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_image_uploader.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/workflow_recorder/image_uploader.py tests/test_image_uploader.py
git commit -m "feat: ImageUploader — multipart PNG upload with JSONL buffer fallback"
```

---

### Task 4: Daemon simplification — remove analysis loop, wire uploader

**Files:**
- Modify: `src/workflow_recorder/daemon.py`
- Modify: `tests/test_daemon.py` (if exists) or no-op

- [ ] **Step 1: Read the current daemon**

Current `src/workflow_recorder/daemon.py` has two threads (capture + analysis) and uses FramePusher. The target is: single capture thread that enqueues into ImageUploader.

- [ ] **Step 2: Replace the file with the simplified version**

Write `src/workflow_recorder/daemon.py`:

```python
"""Main daemon loop for the workflow recorder.

Single-thread capture loop. Each capture:
1. Take screenshot + OS cursor/focus (via capture/screenshot.py)
2. Enqueue for upload via ImageUploader
3. Optionally back off if user is idle (idle_detector)
4. Sleep until next interval
"""

from __future__ import annotations

import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import structlog

from workflow_recorder.capture.privacy import apply_masks, should_skip_frame
from workflow_recorder.capture.screenshot import capture_screenshot
from workflow_recorder.capture.window_info import get_active_window
from workflow_recorder.capture.idle_detector import IdleBackoff, IdleDetector
from workflow_recorder.config import AppConfig
from workflow_recorder.image_uploader import ImageUploader
from workflow_recorder.utils.storage import get_temp_capture_dir


log = structlog.get_logger()


class RecordingSession:
    """One recording session — just a session_id and start time."""

    def __init__(self, employee_id: str):
        self.session_id = str(uuid.uuid4())
        self.employee_id = employee_id
        self.start_time = time.time()
        self.frames_captured = 0
        self.frames_skipped = 0


class Daemon:
    """Recorder daemon: capture screenshots, push to server for analysis."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._stop_event = threading.Event()
        self.session: Optional[RecordingSession] = None
        self.uploader: Optional[ImageUploader] = None
        self._capture_dir = get_temp_capture_dir()

    def run(self) -> None:
        """Start capture loop. Blocks until stop() or max_duration."""
        self.session = RecordingSession(employee_id=self.config.employee_id)
        log.info("daemon_starting", session_id=self.session.session_id,
                 employee_id=self.session.employee_id)

        self.uploader = ImageUploader(
            server_url=self.config.server.url,
            api_key=self.config.server.api_key,
            employee_id=self.session.employee_id,
            session_id=self.session.session_id,
            buffer_path=self.config.server.buffer_path,
            timeout=self.config.server.timeout_seconds,
            max_retries=self.config.server.max_retries,
        )
        if self.config.server.enabled:
            self.uploader.start()
        else:
            log.warning("uploader_disabled",
                        msg="server.enabled is False — captures won't be uploaded")

        # Idle backoff
        idle_cfg = self.config.idle_detection
        idle_detector = IdleDetector() if idle_cfg.enabled else None
        idle_backoff = IdleBackoff(
            base_interval=self.config.capture.interval_seconds,
            max_interval=idle_cfg.max_interval_seconds,
            idle_threshold_seconds=idle_cfg.idle_threshold_seconds,
            backoff_factor=idle_cfg.backoff_factor,
        ) if idle_cfg.enabled else None

        max_duration = self.config.session.max_duration_seconds
        start = self.session.start_time

        try:
            while not self._stop_event.is_set():
                self._capture_and_enqueue()

                # Backoff or fixed interval
                if idle_detector is not None and idle_backoff is not None:
                    idle_secs = idle_detector.seconds_since_last_input()
                    interval = idle_backoff.update(idle_secs)
                else:
                    interval = self.config.capture.interval_seconds

                # Max-duration check (0 = unlimited)
                if max_duration > 0 and (time.time() - start) >= max_duration:
                    log.info("max_duration_reached",
                             duration=max_duration)
                    break
                self._stop_event.wait(timeout=interval)
        finally:
            if self.uploader is not None:
                self.uploader.stop(timeout=15.0)
            log.info("daemon_stopped",
                     frames_captured=self.session.frames_captured,
                     frames_skipped=self.session.frames_skipped,
                     duration=time.time() - start)

    def stop(self) -> None:
        """Signal the capture loop to stop."""
        self._stop_event.set()

    def _capture_and_enqueue(self) -> None:
        """Take one screenshot, run privacy filters, enqueue for upload."""
        try:
            window_ctx = get_active_window()
            if should_skip_frame(window_ctx, self.config.privacy):
                self.session.frames_skipped += 1
                return

            result = capture_screenshot(
                output_dir=self._capture_dir,
                monitor=self.config.capture.monitor,
                image_format=self.config.capture.image_format,
                image_quality=self.config.capture.image_quality,
                downscale_factor=self.config.capture.downscale_factor,
            )
            apply_masks(result.file_path, self.config.privacy)

            self.session.frames_captured += 1
            if self.uploader is not None and self.config.server.enabled:
                self.uploader.enqueue(
                    image_path=result.file_path,
                    frame_index=self.session.frames_captured,
                    timestamp=result.timestamp,
                    cursor_x=result.cursor_x,
                    cursor_y=result.cursor_y,
                    focus_rect=result.focus_rect,
                )
        except Exception as exc:
            log.exception("capture_failed", error=str(exc))


def install_signal_handlers(daemon: Daemon) -> None:
    """SIGINT / SIGTERM cleanly stops the daemon."""
    def handler(signum, _frame):
        log.info("signal_received", signum=signum)
        daemon.stop()

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)
```

- [ ] **Step 3: Remove or update any tests that poke the old `_analysis_loop`**

Search existing `tests/` for references to the old architecture:

```bash
grep -n "analysis_loop\|FramePusher\|frame_pusher" tests/ -r
```

For any test that asserts on the old two-thread architecture or directly constructs FramePusher, either:
- Update to assert on the uploader instead, OR
- Delete if the test was specifically validating the old qwen-in-client behavior.

If no such tests exist, skip this step.

- [ ] **Step 4: Run full suite for regression**

Run: `PYTHONPATH=src python -m pytest tests/ -q --no-header`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/workflow_recorder/daemon.py tests/
git commit -m "refactor: daemon is single-threaded capture loop using ImageUploader"
```

---

### Task 5: Config tweak — default interval 15→3

**Files:**
- Modify: `src/workflow_recorder/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Update default**

Find in `config.py`:

```python
class CaptureConfig(BaseModel):
    interval_seconds: float = 15.0
    ...
```

Change to:

```python
class CaptureConfig(BaseModel):
    interval_seconds: float = 3.0
    ...
```

- [ ] **Step 2: Update any test that asserts the old default**

Search:

```bash
grep -n "interval_seconds" tests/test_config.py
```

Update matching assertions from `15.0` to `3.0`.

- [ ] **Step 3: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_config.py -v`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/workflow_recorder/config.py tests/test_config.py
git commit -m "chore: default capture interval 15s -> 3s for offline analysis"
```

---

### Task 6: Delete legacy `frame_pusher.py` + its tests

**Files:**
- Delete: `src/workflow_recorder/frame_pusher.py`
- Delete: `tests/test_frame_pusher.py`

- [ ] **Step 1: Verify no imports remain**

```bash
grep -rn "from workflow_recorder.frame_pusher\|import frame_pusher" src/ tests/
```

Should return nothing (daemon.py was updated in Task 4).

- [ ] **Step 2: Delete files**

```bash
git rm src/workflow_recorder/frame_pusher.py tests/test_frame_pusher.py
```

- [ ] **Step 3: Run full suite**

Run: `PYTHONPATH=src python -m pytest tests/ -q --no-header`
Expected: all pass (count drops by however many frame_pusher tests there were)

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: remove legacy frame_pusher.py (superseded by image_uploader)"
```

---

### Task 7: `__main__.py` banner tweaks

**Files:**
- Modify: `src/workflow_recorder/__main__.py`

- [ ] **Step 1: Read the banner**

Current banner prints model, endpoint, API key — these are now server concerns. Simplify to client-relevant info.

- [ ] **Step 2: Update `_print_banner()`**

Replace the banner function body in `src/workflow_recorder/__main__.py`:

```python
def _print_banner(config) -> None:
    """Print startup information to console."""
    output_dir = Path(config.output.directory).resolve()
    duration = config.session.max_duration_seconds
    if duration <= 0:
        duration_str = "unlimited (Ctrl+C to stop)"
    else:
        mins = int(duration // 60)
        secs = int(duration % 60)
        duration_str = f"{mins}m {secs}s"

    print()
    print("=" * 58)
    print("  Workflow Recorder v0.4.0")
    print("=" * 58)
    print()

    server_target = config.server.url if config.server.enabled else "disabled"
    if config.idle_detection.enabled:
        idle_str = (f"idle backoff: >{int(config.idle_detection.idle_threshold_seconds)}s "
                    f"-> up to {int(config.idle_detection.max_interval_seconds)}s")
    else:
        idle_str = "idle backoff: disabled"

    print(f"  Employee ID:          {config.employee_id or '(not set)'}")
    print(f"  Screenshot interval:  {config.capture.interval_seconds}s ({idle_str})")
    print(f"  Max recording time:   {duration_str}")
    print(f"  Upload target:        {server_target}")
    print(f"  Output directory:     {output_dir}")
    print()
    print("  Analysis now runs on the server — no API key needed on this machine.")
    print("  Press Ctrl+C to stop early.")
    print()
    print("-" * 58)
```

- [ ] **Step 3: Verify**

Run: `PYTHONPATH=src python -c "from workflow_recorder.__main__ import _print_banner; from workflow_recorder.config import AppConfig; _print_banner(AppConfig(employee_id='E123'))"`
Expected: nicely formatted banner, no crash.

- [ ] **Step 4: Commit**

```bash
git add src/workflow_recorder/__main__.py
git commit -m "chore: update CLI banner for v0.4.0 — no API key on client"
```

---

## Phase 3 Completion Criteria

After all 7 tasks:

1. `capture/cursor_focus.py` provides `get_cursor_position()`, `get_focus_rect()`, `screen_to_image_coords()`, `rect_to_image_coords()`
2. `CaptureResult` includes `cursor_x`, `cursor_y`, `focus_rect` fields populated from OS
3. `image_uploader.ImageUploader` uploads multipart images with OS metadata to `/frames/upload`
4. ImageUploader has the same buffer+replay robustness as the old FramePusher
5. `daemon.py` is a single-thread capture loop, no more `_analysis_loop`
6. Default `capture.interval_seconds = 3.0`
7. `frame_pusher.py` and its tests are deleted
8. Banner reflects the new architecture (no API key field)
9. Full test suite passes — new tests for cursor/focus + image_uploader; old frame_pusher tests removed

**Verification command:**

```bash
PYTHONPATH=src python -m pytest tests/ -q --no-header
# Expected: ~255-260 passed (252 Phase 2 + ~15 new cursor/focus/uploader tests
# - however many frame_pusher tests were removed). Exact count varies by
# tests/test_frame_pusher.py size.
```
