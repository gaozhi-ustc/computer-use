# Offline Analysis — Phase 2: AnalysisPool

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Server-side background workers drain the `pending` queue by calling qwen and updating rows to `done` / `failed`. One worker per API key loaded from `./api_keys.txt`.

**Architecture:** `AnalysisPool` spawns N daemon threads at app startup (one per line in `api_keys.txt`). Each `AnalysisWorker` binds to one key, reuses one `VisionClient`, loops: `claim_next_pending_frame()` → `analyze_frame()` → `mark_frame_done` or on error `mark_frame_failed` (after 3 attempts) / `reset_frame_to_pending` (retry). Pool lifecycle wired to FastAPI's `@app.on_event("startup")` and `@app.on_event("shutdown")`.

**Tech Stack:** `threading.Thread` + `threading.Event`, existing `VisionClient` from `src/workflow_recorder/analysis/vision_client.py`, existing DB helpers from Phase 1.

**Depends on:** Phase 1 (DB helpers `claim_next_pending_frame`, `mark_frame_done`, `mark_frame_failed`, `reset_frame_to_pending`).

---

## File Structure

### New files

```
server/
├── api_keys.py              # load_api_keys() — parse api_keys.txt
├── analysis_pool.py         # AnalysisPool + AnalysisWorker
tests/
├── test_api_keys_loader.py  # parsing tests
└── test_analysis_pool.py    # worker lifecycle + analyze flow with mocked VisionClient
```

### Modified files

```
server/app.py                # _startup: load keys + start pool; _shutdown: stop pool
api_keys.txt.example         # committed sample
```

---

### Task 1: API keys loader

**Files:**
- Create: `server/api_keys.py`
- Create: `tests/test_api_keys_loader.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_keys_loader.py`:

```python
"""Tests for api_keys.txt parser."""

from __future__ import annotations

import pytest


def test_load_keys_happy_path(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("sk-sp-aaa\nsk-sp-bbb\nsk-sp-ccc\n", encoding="utf-8")
    assert load_api_keys(f) == ["sk-sp-aaa", "sk-sp-bbb", "sk-sp-ccc"]


def test_load_keys_strips_whitespace(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("  sk-sp-aaa  \n\t sk-sp-bbb\n", encoding="utf-8")
    assert load_api_keys(f) == ["sk-sp-aaa", "sk-sp-bbb"]


def test_load_keys_skips_blank_lines_and_comments(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text(
        "# header comment\n"
        "\n"
        "sk-sp-aaa\n"
        "   \n"
        "# another comment\n"
        "sk-sp-bbb  # inline is NOT stripped\n",
        encoding="utf-8",
    )
    assert load_api_keys(f) == ["sk-sp-aaa", "sk-sp-bbb  # inline is NOT stripped"]


def test_load_keys_missing_file_returns_empty(tmp_path):
    from server.api_keys import load_api_keys
    assert load_api_keys(tmp_path / "nope.txt") == []


def test_load_keys_empty_file_returns_empty(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("", encoding="utf-8")
    assert load_api_keys(f) == []


def test_load_keys_all_comments_returns_empty(tmp_path):
    from server.api_keys import load_api_keys
    f = tmp_path / "keys.txt"
    f.write_text("# all comments\n# nothing else\n", encoding="utf-8")
    assert load_api_keys(f) == []


def test_load_keys_default_path(tmp_path, monkeypatch):
    """When called without args, reads ./api_keys.txt from CWD."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api_keys.txt").write_text("sk-sp-default\n", encoding="utf-8")
    from server.api_keys import load_api_keys
    assert load_api_keys() == ["sk-sp-default"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_api_keys_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.api_keys'`

- [ ] **Step 3: Implement `server/api_keys.py`**

```python
"""Load DashScope API keys from ./api_keys.txt (one per line).

File format:
- One key per line, whitespace stripped
- Lines starting with # are treated as comments and skipped
- Blank lines are skipped
- Inline # comments are NOT stripped (keep keys opaque)

Missing file returns [] — caller is responsible for logging/warning.
"""

from __future__ import annotations

from pathlib import Path


DEFAULT_PATH = "./api_keys.txt"


def load_api_keys(path: str | Path | None = None) -> list[str]:
    """Parse api_keys.txt and return the list of keys."""
    p = Path(path) if path is not None else Path(DEFAULT_PATH)
    if not p.is_file():
        return []
    keys: list[str] = []
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        keys.append(line)
    return keys
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python -m pytest tests/test_api_keys_loader.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add server/api_keys.py tests/test_api_keys_loader.py
git commit -m "feat: api_keys.txt loader for AnalysisPool"
```

---

### Task 2: AnalysisWorker class

**Files:**
- Create: `server/analysis_pool.py` (AnalysisWorker only for this task)
- Create: `tests/test_analysis_pool.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_analysis_pool.py`:

```python
"""Tests for AnalysisPool + AnalysisWorker."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# AnalysisWorker tests with a mocked VisionClient
# ---------------------------------------------------------------------------


class FakeVisionClient:
    """Stand-in for workflow_recorder.analysis.vision_client.VisionClient.

    Return values are controlled via attributes set by the test.
    """

    def __init__(self, *args, **kwargs):
        self.analyze_frame_calls: list[dict] = []
        self._result = None
        self._raise: Exception | None = None

    def set_result(self, result):
        self._result = result
        self._raise = None

    def set_raise(self, exc: Exception):
        self._raise = exc
        self._result = None

    def analyze_frame(self, image_path: Path, window_context=None,
                      frame_index: int = 0, timestamp=None):
        self.analyze_frame_calls.append({
            "image_path": image_path,
            "frame_index": frame_index,
            "timestamp": timestamp,
        })
        if self._raise is not None:
            raise self._raise
        return self._result


def _fake_analysis(frame_index: int):
    """Build a FrameAnalysis-shaped object (only the fields mark_frame_done uses)."""
    class FA:
        def model_dump(self) -> dict:
            return {
                "frame_index": frame_index,
                "timestamp": 100.0,
                "application": "chrome.exe",
                "window_title": "Test",
                "user_action": "clicking Save",
                "ui_elements_visible": [],
                "text_content": "hello",
                "mouse_position_estimate": [10, 20],
                "confidence": 0.88,
                "context_data": {"page_title": "Test"},
            }
    return FA()


@pytest.fixture
def fresh_db_with_pending(tmp_path, monkeypatch):
    """DB with 3 pending frames ready to be analyzed."""
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    from server import db
    db.init_db()
    # Create dummy image files so the worker can "analyze" them
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    for i in range(1, 4):
        img = img_dir / f"{i}.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)
        db.insert_pending_frame(
            employee_id="E001", session_id="s1", frame_index=i,
            timestamp=float(i), image_path=str(img),
        )
    return db


def test_worker_processes_pending_frame(fresh_db_with_pending):
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_result(_fake_analysis(frame_index=1))

    stop_event = threading.Event()
    worker = AnalysisWorker(
        key="sk-test", key_index=0, stop_event=stop_event,
        vision_client=fake_client,  # injected for test
    )

    # Manually claim + analyze one (simulating one iteration)
    frame = db.claim_next_pending_frame()
    worker._analyze_one(frame)

    updated = db.get_frame(frame["id"])
    assert updated["analysis_status"] == "done"
    assert updated["application"] == "chrome.exe"
    assert updated["user_action"] == "clicking Save"
    assert updated["confidence"] == pytest.approx(0.88)
    assert updated["context_data"] == {"page_title": "Test"}

    # Worker must have been called with the right image
    assert len(fake_client.analyze_frame_calls) == 1
    assert fake_client.analyze_frame_calls[0]["frame_index"] == 1


def test_worker_retries_on_exception_resets_to_pending(fresh_db_with_pending):
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_raise(RuntimeError("api down"))

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    frame = db.claim_next_pending_frame()
    worker._analyze_one(frame)
    refreshed = db.get_frame(frame["id"])
    # First failure: status goes back to 'pending', attempts stays at 1
    assert refreshed["analysis_status"] == "pending"
    assert refreshed["analysis_attempts"] == 1


def test_worker_fails_after_3_attempts(fresh_db_with_pending):
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_raise(RuntimeError("persistent error"))

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    # Claim three times, fail three times
    frame_id = None
    for attempt in range(3):
        frame = db.claim_next_pending_frame()
        assert frame is not None, f"attempt {attempt}: expected a claimable frame"
        if frame_id is None:
            frame_id = frame["id"]
        worker._analyze_one(frame)

    final = db.get_frame(frame_id)
    assert final["analysis_status"] == "failed"
    assert final["analysis_attempts"] == 3
    assert "persistent error" in final["analysis_error"]


def test_worker_handles_empty_result_as_failure(fresh_db_with_pending):
    """VisionClient returning None (empty response) should count as a failure."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_result(None)  # qwen returned nothing parseable

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    frame = db.claim_next_pending_frame()
    worker._analyze_one(frame)
    refreshed = db.get_frame(frame["id"])
    assert refreshed["analysis_status"] == "pending"  # will retry
    assert refreshed["analysis_attempts"] == 1


def test_worker_run_loop_drains_queue_then_waits(fresh_db_with_pending):
    """Worker's main run loop: process all pending, then wait on stop_event."""
    from server.analysis_pool import AnalysisWorker
    db = fresh_db_with_pending
    fake_client = FakeVisionClient()
    fake_client.set_result(_fake_analysis(frame_index=0))

    stop_event = threading.Event()
    worker = AnalysisWorker("sk-test", 0, stop_event, vision_client=fake_client)

    t = threading.Thread(target=worker.run, daemon=True)
    t.start()

    # Poll until all three pending are done, max 3 seconds
    deadline = time.time() + 3.0
    while time.time() < deadline:
        stats = db.get_analysis_queue_stats()
        if stats["done"] == 3:
            break
        time.sleep(0.05)

    stop_event.set()
    t.join(timeout=5.0)

    final = db.get_analysis_queue_stats()
    assert final["done"] == 3
    assert final["pending"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_analysis_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.analysis_pool'`

- [ ] **Step 3: Implement AnalysisWorker in `server/analysis_pool.py`**

```python
"""Background worker pool that analyzes pending frames using qwen.

One AnalysisWorker per DashScope API key. Each worker:
- Polls the DB's pending queue (atomic claim via UPDATE ... RETURNING)
- Calls VisionClient.analyze_frame for each claimed frame
- On success: mark_frame_done with the analysis result
- On failure: reset to pending (for retry) or mark_frame_failed after 3 attempts
- Sleeps briefly when queue is empty
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import structlog

from server import db

log = structlog.get_logger()


# How long a worker waits between polls when the queue is empty
EMPTY_QUEUE_POLL_INTERVAL_SECONDS = 2.0

# Max consecutive failures before marking a frame 'failed' permanently
MAX_ANALYSIS_ATTEMPTS = 3


class AnalysisWorker:
    """One worker bound to one API key."""

    def __init__(
        self,
        key: str,
        key_index: int,
        stop_event: threading.Event,
        vision_client: Any = None,  # injected for tests; None = build real one
    ):
        self.key = key
        self.key_index = key_index
        self.label = f"worker-{key_index}"
        self._stop = stop_event
        self._vision = vision_client if vision_client is not None else self._build_vision()

    def _build_vision(self):
        """Build a real VisionClient using this worker's API key."""
        from workflow_recorder.analysis.vision_client import VisionClient
        from workflow_recorder.config import AnalysisConfig
        cfg = AnalysisConfig(
            openai_api_key=self.key,
            base_url="https://coding.dashscope.aliyuncs.com/v1",
            model="qwen3.5-plus",
            detail="low",
            max_tokens=1000,
            temperature=0.1,
        )
        return VisionClient(cfg)

    def run(self) -> None:
        """Main loop: claim → analyze → repeat. Stops when stop_event is set."""
        log.info("analysis_worker_started", label=self.label)
        while not self._stop.is_set():
            frame = db.claim_next_pending_frame()
            if frame is None:
                # Queue empty — wait (interruptible by stop_event)
                self._stop.wait(timeout=EMPTY_QUEUE_POLL_INTERVAL_SECONDS)
                continue
            self._analyze_one(frame)
        log.info("analysis_worker_stopped", label=self.label)

    def _analyze_one(self, frame: dict) -> None:
        """Analyze a single claimed frame and update the DB accordingly."""
        frame_id = frame["id"]
        attempts = frame.get("analysis_attempts", 0)
        try:
            result = self._vision.analyze_frame(
                image_path=Path(frame["image_path"]),
                window_context=None,
                frame_index=frame["frame_index"],
                timestamp=None,
            )
            if result is None:
                self._handle_failure(frame_id, attempts, "empty response")
                return
            db.mark_frame_done(frame_id, result.model_dump())
            log.debug("frame_analyzed", frame_id=frame_id,
                      worker=self.label, frame_index=frame["frame_index"])
        except Exception as exc:
            self._handle_failure(frame_id, attempts, f"{type(exc).__name__}: {exc}")

    def _handle_failure(self, frame_id: int, attempts: int, reason: str) -> None:
        """Retry by resetting to pending, or mark failed after MAX_ANALYSIS_ATTEMPTS."""
        if attempts >= MAX_ANALYSIS_ATTEMPTS:
            db.mark_frame_failed(frame_id, reason)
            log.warning("frame_failed", frame_id=frame_id,
                        worker=self.label, attempts=attempts, reason=reason)
        else:
            db.reset_frame_to_pending(frame_id)
            log.info("frame_retry_scheduled", frame_id=frame_id,
                     worker=self.label, attempts=attempts, reason=reason)
```

- [ ] **Step 4: Run worker tests**

Run: `PYTHONPATH=src python -m pytest tests/test_analysis_pool.py -v`
Expected: 5 passed (workflow tests). If `test_worker_run_loop_drains_queue_then_waits` flakes, increase the deadline.

- [ ] **Step 5: Commit**

```bash
git add server/analysis_pool.py tests/test_analysis_pool.py
git commit -m "feat: AnalysisWorker with 3-attempt retry + mocked-client unit tests"
```

---

### Task 3: AnalysisPool wrapper

**Files:**
- Modify: `server/analysis_pool.py` — add AnalysisPool class
- Modify: `tests/test_analysis_pool.py` — add pool lifecycle tests

- [ ] **Step 1: Write failing tests**

Append to `tests/test_analysis_pool.py`:

```python
# ---------------------------------------------------------------------------
# AnalysisPool lifecycle
# ---------------------------------------------------------------------------


def test_pool_starts_one_thread_per_key(fresh_db_with_pending):
    """N keys -> N threads, all alive until stop()."""
    from server.analysis_pool import AnalysisPool, AnalysisWorker
    # Use a factory that returns FakeVisionClient so no real API calls happen
    fake_clients = []

    def worker_factory(key, key_index, stop_event):
        fc = FakeVisionClient()
        fc.set_result(_fake_analysis(frame_index=0))
        fake_clients.append(fc)
        return AnalysisWorker(key, key_index, stop_event, vision_client=fc)

    pool = AnalysisPool(keys=["sk-a", "sk-b", "sk-c"], worker_factory=worker_factory)
    pool.start()
    try:
        assert len(pool._threads) == 3
        for t in pool._threads:
            assert t.is_alive()
    finally:
        pool.stop(timeout=5.0)

    # After stop, all threads should be joined
    for t in pool._threads:
        assert not t.is_alive()


def test_pool_drains_queue_with_multiple_workers(fresh_db_with_pending):
    """3 workers + 3 pending frames -> all become 'done'."""
    from server.analysis_pool import AnalysisPool, AnalysisWorker
    db = fresh_db_with_pending

    def worker_factory(key, key_index, stop_event):
        fc = FakeVisionClient()
        fc.set_result(_fake_analysis(frame_index=0))
        return AnalysisWorker(key, key_index, stop_event, vision_client=fc)

    pool = AnalysisPool(keys=["sk-a", "sk-b", "sk-c"], worker_factory=worker_factory)
    pool.start()

    # Wait up to 3s for drain
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if db.get_analysis_queue_stats()["done"] == 3:
            break
        time.sleep(0.05)

    pool.stop(timeout=5.0)
    assert db.get_analysis_queue_stats()["done"] == 3


def test_pool_with_empty_keys_is_noop():
    """No keys -> pool does nothing, stop is safe."""
    from server.analysis_pool import AnalysisPool
    pool = AnalysisPool(keys=[])
    pool.start()  # no-op
    assert pool._threads == []
    pool.stop()  # no-op
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python -m pytest tests/test_analysis_pool.py -v -k "pool"`
Expected: FAIL — `AnalysisPool` not defined

- [ ] **Step 3: Implement AnalysisPool in `server/analysis_pool.py`**

Append to `server/analysis_pool.py`:

```python
class AnalysisPool:
    """Manages the set of AnalysisWorker threads, one per API key."""

    def __init__(
        self,
        keys: list[str],
        worker_factory=None,  # for tests to inject FakeVisionClient
    ):
        self._keys = list(keys)
        self._worker_factory = worker_factory
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Spawn one daemon thread per key."""
        if not self._keys:
            log.warning("analysis_pool_no_keys",
                        msg="api_keys.txt empty/missing — uploaded frames "
                            "will sit in 'pending' forever.")
            return

        for i, key in enumerate(self._keys):
            if self._worker_factory is not None:
                worker = self._worker_factory(key, i, self._stop_event)
            else:
                worker = AnalysisWorker(key, i, self._stop_event)
            t = threading.Thread(
                target=worker.run,
                name=f"analysis-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

        log.info("analysis_pool_started", worker_count=len(self._keys))

    def stop(self, timeout: float = 30.0) -> None:
        """Signal all workers to exit and wait."""
        if not self._threads:
            return
        self._stop_event.set()
        per_thread = max(1.0, timeout / len(self._threads))
        for t in self._threads:
            t.join(timeout=per_thread)
        log.info("analysis_pool_stopped", worker_count=len(self._threads))
```

- [ ] **Step 4: Run pool tests**

Run: `PYTHONPATH=src python -m pytest tests/test_analysis_pool.py -v`
Expected: 8 passed (5 worker + 3 pool)

- [ ] **Step 5: Commit**

```bash
git add server/analysis_pool.py tests/test_analysis_pool.py
git commit -m "feat: AnalysisPool manages one worker thread per API key"
```

---

### Task 4: Wire pool into FastAPI lifecycle

**Files:**
- Modify: `server/app.py` — startup loads keys + starts pool; shutdown stops pool
- Modify: `tests/test_frames_router.py` — ensure existing TestClient fixture doesn't spawn real workers

- [ ] **Step 1: Add lifecycle hooks to `server/app.py`**

Locate the existing `_startup` function in server/app.py:

```python
@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # Seed default admin if no users exist yet
    if not db.list_users(limit=1):
        from server.auth import hash_password
        db.insert_user(
            username="admin",
            password_hash=hash_password("admin"),
            display_name="System Admin",
            role="admin",
        )
```

Replace with:

```python
# AnalysisPool is assigned during startup, so tests that don't want workers
# can monkeypatch WORKFLOW_DISABLE_ANALYSIS_POOL to any non-empty value.
_analysis_pool = None


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # Seed default admin if no users exist yet
    if not db.list_users(limit=1):
        from server.auth import hash_password
        db.insert_user(
            username="admin",
            password_hash=hash_password("admin"),
            display_name="System Admin",
            role="admin",
        )

    # Load API keys and start the analysis worker pool.
    if os.environ.get("WORKFLOW_DISABLE_ANALYSIS_POOL"):
        return
    global _analysis_pool
    from server.analysis_pool import AnalysisPool
    from server.api_keys import load_api_keys
    keys = load_api_keys()
    _analysis_pool = AnalysisPool(keys=keys)
    _analysis_pool.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    global _analysis_pool
    if _analysis_pool is not None:
        _analysis_pool.stop(timeout=30.0)
        _analysis_pool = None
```

(Add `import os` at the top of the file if not already present.)

- [ ] **Step 2: Update existing TestClient fixtures to disable the pool**

In `tests/test_frames_router.py`, `tests/test_auth_api.py`, `tests/test_users_api.py`, `tests/test_sessions_api.py`, `tests/test_sops_api.py`, `tests/test_stats_api.py`: each has a `client` fixture that sets env vars. Add one more `monkeypatch.setenv` call to each fixture BEFORE `from server.app import app`:

```python
monkeypatch.setenv("WORKFLOW_DISABLE_ANALYSIS_POOL", "1")
```

This prevents tests from spawning real worker threads that would then try to actually call qwen.

(If multiple test files share a fixture via conftest.py, patch once there.)

- [ ] **Step 3: Run full suite**

Run: `PYTHONPATH=src python -m pytest tests/ -q --no-header`
Expected: all pass (237 existing + 10 new = ~247)

If any test hangs (worker thread alive at shutdown), confirm the `WORKFLOW_DISABLE_ANALYSIS_POOL=1` env var is set in the affected fixture.

- [ ] **Step 4: Commit**

```bash
git add server/app.py tests/*.py
git commit -m "feat: wire AnalysisPool to FastAPI startup/shutdown hooks"
```

---

### Task 5: `api_keys.txt.example` for documentation

**Files:**
- Create: `api_keys.txt.example`

- [ ] **Step 1: Create sample file**

```
# api_keys.txt — one DashScope API key per line.
#
# Lines starting with '#' are comments and ignored.
# Blank lines are ignored.
# Inline comments are NOT stripped — keep the file opaque.
#
# Get keys from: https://bailian.console.aliyun.com/ → API-KEY 管理
#
# THIS FILE IS GITIGNORED. Copy to api_keys.txt and fill in real keys.
# Each key becomes one AnalysisPool worker thread.

# sk-sp-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# sk-sp-yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
# sk-sp-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz
```

- [ ] **Step 2: Commit**

```bash
git add api_keys.txt.example
git commit -m "docs: api_keys.txt.example sample file"
```

---

### Task 6: End-to-end integration test (real worker, fake vision)

**Files:**
- Create: `tests/integration/test_analysis_pool_e2e.py`

- [ ] **Step 1: Write the test**

```python
"""E2E smoke: upload frame → pool analyzes it → GET returns 'done'.

Uses FakeVisionClient (no real API calls) via the AnalysisPool's worker_factory
hook, so this runs without keys. Tagged integration because it exercises the
full startup/shutdown lifecycle through FastAPI's TestClient.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
def test_upload_then_analyzed_by_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_SERVER_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKFLOW_IMAGE_DIR", str(tmp_path / "imgs"))
    monkeypatch.setenv("WORKFLOW_SERVER_KEY", "sk-test")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "secret")
    # Don't disable the pool — we want it running
    monkeypatch.delenv("WORKFLOW_DISABLE_ANALYSIS_POOL", raising=False)

    # Stub AnalysisPool's keys + worker_factory before startup runs.
    # Strategy: write api_keys.txt with one fake key, then monkeypatch
    # AnalysisWorker to use a FakeVisionClient.
    (tmp_path / "api_keys.txt").write_text("sk-fake-1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Monkeypatch _build_vision to return a fake that always succeeds
    class FA:
        def model_dump(self):
            return {
                "frame_index": 1, "timestamp": 100.0,
                "application": "Chrome", "window_title": "Title",
                "user_action": "typing", "ui_elements_visible": [],
                "text_content": "", "mouse_position_estimate": [],
                "confidence": 0.9, "context_data": {},
            }

    class FakeVision:
        def analyze_frame(self, image_path, window_context=None,
                          frame_index=0, timestamp=None):
            return FA()

    from server import analysis_pool as ap_mod
    monkeypatch.setattr(
        ap_mod.AnalysisWorker, "_build_vision", lambda self: FakeVision()
    )

    from server.app import app
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDAT\x08\x99c\xfa\x0f\x00\x00\x01\x01\x00\x01"
        b"\xae\xf0\x18\x95\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(app) as client:
        # Upload one frame
        r = client.post(
            "/frames/upload",
            headers={"X-API-Key": "sk-test"},
            data={"employee_id": "E1", "session_id": "s1",
                  "frame_index": "1", "timestamp": "100.0",
                  "cursor_x": "50", "cursor_y": "60", "focus_rect": ""},
            files={"image": ("1.png", png, "image/png")},
        )
        assert r.status_code == 200
        frame_id = r.json()["id"]

        # Wait up to 5s for the worker to pick it up and mark done
        from server import db as db_mod
        deadline = time.time() + 5.0
        while time.time() < deadline:
            frame = db_mod.get_frame(frame_id)
            if frame["analysis_status"] == "done":
                break
            time.sleep(0.05)
        else:
            pytest.fail(
                f"Frame never reached 'done'; final state: "
                f"{db_mod.get_frame(frame_id)}"
            )

        final = db_mod.get_frame(frame_id)
        assert final["analysis_status"] == "done"
        assert final["application"] == "Chrome"
        assert final["user_action"] == "typing"
        assert final["cursor_x"] == 50  # OS coord preserved
```

- [ ] **Step 2: Run it**

Run: `PYTHONPATH=src python -m pytest tests/integration/test_analysis_pool_e2e.py --run-integration -v`
Expected: 1 passed

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_analysis_pool_e2e.py
git commit -m "test: E2E smoke for upload → pool analysis → done"
```

---

## Phase 2 Completion Criteria

After all 6 tasks:

1. `server/api_keys.py::load_api_keys()` parses `./api_keys.txt` correctly (comments, blanks, whitespace)
2. `server/analysis_pool.py::AnalysisWorker` claims pending frames, calls qwen, updates DB
3. `AnalysisWorker` retries up to 3 times, then marks `failed` with error message
4. `AnalysisPool` spawns one daemon thread per key, lifecycle wired to FastAPI startup/shutdown
5. No keys → pool is a no-op and server startup continues (with warning log)
6. `WORKFLOW_DISABLE_ANALYSIS_POOL=1` env var skips pool startup (for unit tests)
7. E2E test: uploaded frame goes from `pending` → `done` via a running worker
8. Full test suite passes (Phase 1 tests + ~16 new Phase 2 tests)

**Verification command:**

```bash
PYTHONPATH=src python -m pytest tests/ -q --no-header
# Expected: ~253 passed (237 Phase 1 + ~16 Phase 2), 8 skipped
```
