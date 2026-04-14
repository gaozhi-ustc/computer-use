# Offline Analysis Architecture

> Status: **Approved**
> Date: 2026-04-14
> Author: gaozhi + Claude Opus 4.6

## 1. Overview

Shift from **client-side online analysis** (client captures → calls qwen → pushes JSON) to **server-side offline analysis** (client captures → uploads image → server workers analyze with pool of API keys → writes results to DB).

Dashboard displays both the analysis fields and the original screenshot, with the mouse cursor and focused-control positions overlaid as a red / yellow box — these positions are captured from the OS at screenshot time, not inferred by the vision model.

### Primary goals

1. **Shorter capture interval** — default 15s → **3s**, because client no longer waits for the ~12s qwen call per frame.
2. **Centralized key management** — all DashScope API keys live on the server in `api_keys.txt`; employee machines never handle keys.
3. **Parallel analysis throughput** — N keys ⇒ N workers, each a dedicated thread pulling from a shared pending queue.
4. **Pixel-accurate interaction markers** — mouse and focus positions come from Win32 `GetCursorPos()` / `GetFocus()`, not the vision model.
5. **Image-backed replay** — Dashboard "录制回放" shows the actual screenshot alongside the analysis, enabling visual auditing and forensic review.

## 2. Architecture Shift

### Before (v0.3.3 — current)

```
[mss capture] → [qwen vision (blocking ~12s)] → [FramePusher queue] → POST /frames (JSON) → [SQLite frames table]
      │                    │
      └─ 15s interval      └─ API key on every client
```

### After (v0.4.0 — this spec)

```
CLIENT (Windows employee machine)
[mss capture every 3s]
  ├─ capture image
  ├─ capture cursor_(x,y) via Win32 GetCursorPos()
  └─ capture focus_rect via Win32 GetFocus() + GetWindowRect()
  │
  ▼
[ImageUploader queue + JSONL buffer]
  │
  ▼ POST /frames/upload (multipart: image + OS coords + metadata)
  ▼
SERVER
[upload handler] → save image to filesystem → INSERT frames (status=pending)
                                                       │
                                                       ▼
                                            [AnalysisPool]
                                            ├─ worker_0 (key #0) ─┐
                                            ├─ worker_1 (key #1) ─┤ claim_next_pending()
                                            ├─ worker_2 (key #2) ─┤ → qwen analyze
                                            └─ worker_N (key #N) ─┘ → UPDATE status=done

DASHBOARD (Vue 3)
GET /api/sessions/:id → frames[] with image_path, analysis_status, cursor, focus, AI results
<FrameImage> component → <img> + CSS red/yellow overlay for cursor/focus
```

## 3. Client-Side Changes

### 3.1 Removals

- **`frame_pusher.py`** → deleted, replaced by `image_uploader.py`
- **`analysis/vision_client.py`** → stays in repo but **no longer invoked** from daemon; becomes a library used only by server `AnalysisPool`
- **`daemon._analysis_loop`** → entire thread + logic removed
- **`analysis.openai_api_key`** config field → ignored by daemon (silently accepted for backward compat with old installed configs; server holds the keys)

### 3.2 New: `image_uploader.py`

Mirrors the old `frame_pusher.py` architecture:

- Background thread + bounded `queue.Queue`
- Non-blocking `enqueue(image_path, frame_index, timestamp, cursor, focus_rect)`
- Uploads via `httpx.post(url, data=form_fields, files={"image": png_bytes}, headers={"X-API-Key": key})`
- On upload failure: retry with exponential backoff (1s, 2s, 4s), after `max_retries` write payload (metadata only — image path stays on disk) to `./logs/push_buffer.jsonl`
- On startup: scan buffer file, re-upload any entries whose image file still exists; drop entries whose image was garbage-collected
- Clean up locally-captured image file after successful upload (default) or after N days (configurable)

### 3.3 OS Data Capture (`capture/screenshot.py`)

Extend `CaptureResult` dataclass:

```python
@dataclass
class CaptureResult:
    file_path: Path
    timestamp: float
    width: int                        # image dimensions AFTER downscale
    height: int
    monitor_index: int
    cursor_x: int = -1                # NEW: cursor position in image pixels, -1 if unavailable
    cursor_y: int = -1
    focus_rect: list[int] | None = None  # NEW: [x1, y1, x2, y2] in image pixels, None if no focused control
```

Capture flow in `capture_screenshot()`:

1. `mon = sct.monitors[...]` (existing)
2. Before grab: `cursor_x_screen, cursor_y_screen = GetCursorPos()`
3. Optional: `hwnd = GetFocus(); rect = GetWindowRect(hwnd)` if hwnd is non-zero
4. Screenshot + downscale (existing)
5. Convert screen coordinates to image coordinates:
   - `img_cursor_x = int((cursor_x_screen - mon["left"]) * downscale_factor)`
   - `img_cursor_y = int((cursor_y_screen - mon["top"]) * downscale_factor)`
   - Clamp to [0, width-1] and [0, height-1]; if outside the captured monitor, set to -1

Non-Windows fallback: `cursor_x/y = -1`, `focus_rect = None`. Dashboard gracefully hides the overlay when coords are -1.

### 3.4 Daemon Simplification (`daemon.py`)

Before this change `daemon.py` has two threads (capture + analysis) coordinated via `queue.Queue`. After:

- **One thread**: capture loop only
- Per iteration: `capture_once()` → `uploader.enqueue(capture_result)` → `_stop_event.wait(interval)`
- Idle backoff logic from v0.3.3 **kept as-is**
- `_finalize()` no longer aggregates a workflow locally (server's analysis + SOP editor is the new source of truth); daemon just stops the uploader and exits

### 3.5 Config Changes

```python
class CaptureConfig(BaseModel):
    interval_seconds: float = 3.0   # was 15.0
    # ... rest unchanged

class AnalysisConfig(BaseModel):
    # openai_api_key stays in schema for backward compat but daemon ignores it
    # (informational only)
    ...

class ServerConfig(BaseModel):
    # All existing fields kept. No new required fields.
    ...
```

`model_config.json` files from v0.3.x continue to load without error; the `openai_api_key` field just goes unused on the client.

## 4. Server-Side Changes

### 4.1 Schema Migration (idempotent `ALTER TABLE`)

Added to `server/db.py` → `_migrate_add_columns()`:

```sql
-- Image storage & analysis status
ALTER TABLE frames ADD COLUMN image_path         TEXT    DEFAULT '';
ALTER TABLE frames ADD COLUMN analysis_status    TEXT    DEFAULT 'done';
ALTER TABLE frames ADD COLUMN analysis_error     TEXT    DEFAULT '';
ALTER TABLE frames ADD COLUMN analysis_attempts  INTEGER DEFAULT 0;
ALTER TABLE frames ADD COLUMN analyzed_at        TEXT    DEFAULT '';

-- OS-captured interaction coords (authoritative over qwen's estimate)
ALTER TABLE frames ADD COLUMN cursor_x           INTEGER DEFAULT -1;
ALTER TABLE frames ADD COLUMN cursor_y           INTEGER DEFAULT -1;
ALTER TABLE frames ADD COLUMN focus_rect_json    TEXT    DEFAULT '';

-- Index for worker pending-queue lookup
CREATE INDEX IF NOT EXISTS idx_frames_status ON frames(analysis_status, id);
```

**Migration semantics:**
- Existing rows → `analysis_status = 'done'` (they already have analysis fields filled by v0.3.x clients), `image_path = ''` (no image), `cursor_x/y = -1`
- New rows → inserted by `/frames/upload` handler with `analysis_status = 'pending'`, analysis fields NULL/empty until a worker fills them

**Status values**: `'pending'` (awaiting analysis) | `'running'` (a worker claimed it) | `'done'` (analysis complete) | `'failed'` (exceeded 3 attempts).

### 4.2 API Changes

**New endpoints:**

```
POST /frames/upload                 multipart form
  Auth: X-API-Key header
  Form fields:
    employee_id: str
    session_id: str
    frame_index: int
    timestamp: float
    cursor_x: int         (-1 if unavailable)
    cursor_y: int         (-1 if unavailable)
    focus_rect: str       JSON array "[x1,y1,x2,y2]" or "" if unavailable
    image: UploadFile     (PNG)
  Response: {ok: true, id: <db_id>}

GET /api/frames/{frame_id}/image    Serve raw PNG
  Auth: JWT Bearer
  Permission: filter_employee_ids(current_user) must include frame.employee_id
  Returns: FileResponse with image/png media type

POST /api/frames/{frame_id}/retry   Reset failed frame to pending
  Auth: JWT + admin
  Body: empty
  Response: {ok: true}

GET /api/frames/queue               Pool status snapshot
  Auth: JWT + admin
  Response: {pending: N, running: M, failed: K, done: D, workers: W}
```

**Deleted endpoints (hard cut):**

- `POST /frames` — old JSON-push route
- `POST /frames/batch` — old batch JSON route
- Corresponding Pydantic models (`FrameIn`, `IngestResult`, `BatchIngestResult`) deleted

**Modified endpoints:**

- `GET /api/sessions/:id` — frames array now includes `image_path`, `analysis_status`, `cursor_x`, `cursor_y`, `focus_rect` fields
- `GET /api/frames/stats`, `/search`, `/export` — auto-filter to `analysis_status = 'done'` (incomplete analyses don't contribute to efficiency metrics)
- `POST /api/sops/:id/generate` — only use `analysis_status = 'done'` frames for SOP step extraction

### 4.3 New module: `server/analysis_pool.py`

```python
class AnalysisWorker:
    """One worker thread bound to one API key."""
    def __init__(self, key: str, key_index: int, stop_event: threading.Event):
        self.key = key
        self.label = f"worker-{key_index}"
        self._stop = stop_event
        self._vision = VisionClient(AnalysisConfig(
            openai_api_key=key,
            base_url="https://coding.dashscope.aliyuncs.com/v1",
            model="qwen3.5-plus",
            detail="low",
            max_tokens=1000,
        ))

    def run(self) -> None:
        while not self._stop.is_set():
            frame = db.claim_next_pending_frame()  # atomic UPDATE ... RETURNING
            if frame is None:
                self._stop.wait(timeout=2.0)
                continue
            self._analyze_one(frame)

    def _analyze_one(self, frame: dict) -> None:
        try:
            result = self._vision.analyze_frame(
                image_path=Path(frame["image_path"]),
                window_context=None,       # server lacks this; qwen infers from image
                frame_index=frame["frame_index"],
                timestamp=frame.get("timestamp_float"),
            )
            if result:
                db.mark_frame_done(frame["id"], result.model_dump())
            else:
                self._handle_failure(frame, "empty response")
        except Exception as e:
            self._handle_failure(frame, str(e))

    def _handle_failure(self, frame: dict, reason: str) -> None:
        attempts = frame.get("analysis_attempts", 0) + 1
        if attempts >= 3:
            db.mark_frame_failed(frame["id"], reason)
        else:
            db.reset_frame_to_pending(frame["id"])


class AnalysisPool:
    def __init__(self, keys: list[str]):
        self._keys = keys
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for i, key in enumerate(self._keys):
            worker = AnalysisWorker(key, i, self._stop_event)
            t = threading.Thread(target=worker.run, name=f"analysis-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("analysis_pool_started", worker_count=len(self._keys))

    def stop(self, timeout: float = 30.0) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=max(1.0, timeout / max(len(self._threads), 1)))
```

**Lifecycle integration** in `server/app.py`:

```python
_pool: AnalysisPool | None = None

@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # ... existing admin seed ...
    global _pool
    keys = load_api_keys_file("./api_keys.txt")
    if keys:
        _pool = AnalysisPool(keys)
        _pool.start()
    else:
        log.warning("no_analysis_keys", msg="api_keys.txt empty/missing — "
                    "uploaded frames will sit in pending forever until keys are added")

@app.on_event("shutdown")
def _shutdown() -> None:
    if _pool:
        _pool.stop(timeout=30)
```

### 4.4 `api_keys.txt` Format

```
# api_keys.txt — one DashScope key per line. # comments ignored. Blank lines ignored.
# Added to .gitignore; never commit.
sk-sp-aaaabbbbcccc...
sk-sp-ddddeeeeffff...
# The key below is rate-limited on weekdays:
sk-sp-gggghhhhiiii...
```

Parser: strip whitespace, skip `# ...` and empty lines. Order in the file becomes `key_index` on worker labels.

### 4.5 Image Storage Layout

```
./frame_images/
└── <employee_id>/
    └── <YYYY-MM-DD>/
        └── <session_id>/
            └── <frame_index>.png
```

Example: `./frame_images/11171/2026-04-14/0da165be-1d71-46d7-9556-4eb517b43334/42.png`

- Base directory configurable via env `WORKFLOW_IMAGE_DIR`, default `./frame_images`
- Date subdirectory uses `YYYY-MM-DD` of `received_at` (server-side)
- **Permanent retention** (decision point 1 in brainstorm = C); no automated cleanup in v0.4.0
- Admin can safely `rm -rf` old date directories if needed; DB rows will 404 on image fetch

### 4.6 New DB Helpers (`server/db.py`)

```python
def insert_pending_frame(
    employee_id: str, session_id: str, frame_index: int,
    timestamp: float, image_path: str,
    cursor_x: int = -1, cursor_y: int = -1,
    focus_rect: list[int] | None = None,
) -> int | None: ...

def claim_next_pending_frame() -> dict | None:
    """Atomic UPDATE ... RETURNING: pick oldest pending row, mark running.
    Returns None if none available."""

def mark_frame_done(frame_id: int, analysis_result: dict) -> None: ...

def mark_frame_failed(frame_id: int, reason: str) -> None: ...

def reset_frame_to_pending(frame_id: int, clear_attempts: bool = False) -> None: ...

def get_analysis_queue_stats() -> dict:
    """Returns {'pending': N, 'running': M, 'failed': K, 'done': D}."""

def get_frame(frame_id: int) -> dict | None: ...
```

SQLite supports `UPDATE ... RETURNING` from 3.35 (Python 3.11+ bundles new enough). Atomic claim SQL:

```sql
UPDATE frames
SET analysis_status = 'running',
    analysis_attempts = analysis_attempts + 1
WHERE id = (
    SELECT id FROM frames
    WHERE analysis_status IN ('pending')
    ORDER BY id ASC
    LIMIT 1
)
RETURNING id, employee_id, session_id, frame_index, image_path,
          cursor_x, cursor_y, focus_rect_json, analysis_attempts;
```

Failed rows are not re-queued automatically — only `/api/frames/:id/retry` resets them.

## 5. Dashboard Changes

### 5.1 Image Serving

`dashboard/src/api/sessions.ts` — extend `FrameInfo`:

```typescript
export interface FrameInfo {
  // ... existing fields
  image_path: string
  analysis_status: 'pending' | 'running' | 'done' | 'failed'
  analysis_error: string
  cursor_x: number   // -1 if unavailable
  cursor_y: number
  focus_rect: number[] | null  // [x1, y1, x2, y2] in image pixels
}
```

New helper in `dashboard/src/api/frames.ts`:

```typescript
export const framesApi = {
  imageUrl: (frameId: number) => `/api/frames/${frameId}/image`,
  retry: (frameId: number) => client.post(`/api/frames/${frameId}/retry`),
  queueStatus: () => client.get<QueueStats>('/api/frames/queue'),
}
```

### 5.2 `<FrameImage>` Component (new)

File: `dashboard/src/components/FrameImage.vue`

```vue
<script setup lang="ts">
import { ref, computed } from 'vue'
import type { FrameInfo } from '@/api/sessions'

const props = defineProps<{
  frame: FrameInfo
  maxWidth?: string  // '300px' for thumbnail, '90vw' for modal
}>()

const imgRef = ref<HTMLImageElement | null>(null)
const naturalW = ref(0)
const naturalH = ref(0)

function onLoad() {
  if (imgRef.value) {
    naturalW.value = imgRef.value.naturalWidth
    naturalH.value = imgRef.value.naturalHeight
  }
}

const cursorOverlay = computed(() => {
  const f = props.frame
  if (f.cursor_x < 0 || f.cursor_y < 0 || !naturalW.value) return null
  const pctX = (f.cursor_x / naturalW.value) * 100
  const pctY = (f.cursor_y / naturalH.value) * 100
  const boxPctW = (40 / naturalW.value) * 100
  const boxPctH = (40 / naturalH.value) * 100
  return {
    left: `calc(${pctX}% - ${boxPctW / 2}%)`,
    top: `calc(${pctY}% - ${boxPctH / 2}%)`,
    width: `${boxPctW}%`,
    height: `${boxPctH}%`,
  }
})

const focusOverlay = computed(() => {
  const f = props.frame
  if (!f.focus_rect || f.focus_rect.length !== 4 || !naturalW.value) return null
  const [x1, y1, x2, y2] = f.focus_rect
  return {
    left: `${(x1 / naturalW.value) * 100}%`,
    top: `${(y1 / naturalH.value) * 100}%`,
    width: `${((x2 - x1) / naturalW.value) * 100}%`,
    height: `${((y2 - y1) / naturalH.value) * 100}%`,
  }
})
</script>

<template>
  <div class="frame-img-wrapper" :style="{ maxWidth: maxWidth || '300px' }">
    <img
      ref="imgRef"
      :src="`/api/frames/${frame.id}/image`"
      @load="onLoad"
      class="frame-img"
    />
    <div v-if="focusOverlay" class="focus-overlay" :style="focusOverlay"></div>
    <div v-if="cursorOverlay" class="cursor-overlay" :style="cursorOverlay"></div>
  </div>
</template>

<style scoped>
.frame-img-wrapper { position: relative; display: inline-block; }
.frame-img { width: 100%; display: block; }
.cursor-overlay {
  position: absolute; border: 2px solid #f00; border-radius: 2px;
  box-shadow: 0 0 6px rgba(255, 0, 0, 0.6); pointer-events: none;
}
.focus-overlay {
  position: absolute; border: 2px dashed #fc0; pointer-events: none;
}
</style>
```

Red solid box = cursor position (mouse / click). Yellow dashed box = keyboard focus rect. Both hidden if corresponding data is missing.

### 5.3 `Recording.vue` Frame Detail Panel (updated)

Layout per-frame in the timeline:

```
┌── Frame at 03:24:15 ── [status: done ✓]
│
│  ┌── 左：缩略图 (300px wide) ──┐  ┌── 右：分析面板 ──────────┐
│  │                              │  │ 应用: Chrome              │
│  │   [screenshot with           │  │ 操作: clicked Save...     │
│  │    red cursor box            │  │ 置信度: ████████ 0.92     │
│  │    yellow focus box]         │  │                           │
│  │                              │  │ [详细信息 折叠面板]        │
│  │   [点击放大 →]                │  │   窗口标题: ...           │
│  │                              │  │   文本内容: ...           │
│  └──────────────────────────────┘  │   UI 元素: [tags]         │
│                                    │   Excel 表头 (如有): ...   │
│                                    │   页面标题 (如有): ...     │
│                                    └───────────────────────────┘
```

For `analysis_status ∈ {'pending', 'running'}`: show image, but analysis panel shows spinner + "分析中...". For `'failed'`: show image, analysis panel shows error + "重试" button (admin only).

Click thumbnail → Naive UI `n-modal` with full-size image + same overlays (the component scales naturally via its `maxWidth` prop).

### 5.4 Dashboard System Settings Page (updated)

`Settings.vue` gains a new section "分析队列状态" showing:

```
Pending: 12 frames
Running: 3 frames (by workers: worker-0, worker-2, worker-4)
Failed:  2 frames    [查看失败列表 →]
Done:    15,420 frames
Workers: 5 (keys loaded from api_keys.txt)
```

Polled every 5s via `/api/frames/queue`. Failed-list dialog shows per-frame error + admin-only "重试" button.

## 6. Data Migration & Backward Compatibility

- **Schema migration**: additive only, via idempotent `ALTER TABLE ADD COLUMN` in `_migrate_add_columns()`. Existing frames preserved as-is with `analysis_status = 'done'` default.
- **Client backward compat**: None. Decision point 3 = A (hard cut). Old v0.3.x clients will get **404** when POSTing to `/frames` and need to upgrade. Only one installed client (employee 11171) affected.
- **API keys migration**: admin must create `./api_keys.txt` on server before first start of v0.4.0 (or frames will sit in `pending` forever — server logs a warning).
- **Client config**: `openai_api_key` in `model_config.json` silently ignored; no migration needed.

## 7. Testing Plan

- **Client**: unit tests for `image_uploader.py` using `FakeClient` (same pattern as `frame_pusher` tests), cursor coordinate transformation tests (screen-to-image math)
- **Server DB**: new tests for `insert_pending_frame`, `claim_next_pending_frame` (concurrency — two workers can't claim the same row), `mark_frame_done`, `mark_frame_failed`, `reset_frame_to_pending`, `get_analysis_queue_stats`
- **Server API**: TestClient tests for `/frames/upload` (multipart parsing, image save, DB insert), `/api/frames/:id/image` (permission filtering, 404 on missing file), `/api/frames/:id/retry` (admin-only)
- **AnalysisPool**: test that `claim_next_pending_frame` returns None when queue empty, that failure increments attempts, that 3rd failure transitions to `'failed'`; mock `VisionClient` to avoid real API calls
- **End-to-end smoke**: spin up server with one real key in `api_keys.txt`, upload a real screenshot via `httpx`, wait for analysis_status to transition to `'done'`, assert DB has application/user_action filled

## 8. Out of Scope (v0.4.0)

- Image thumbnails (generated, smaller versions for faster list views) — v0.5.0 candidate
- Image deduplication across frames (perceptual hash) — v0.5.0 candidate
- Automatic cleanup of very old images — kept manual for now since retention is "permanent"
- Cross-key rate limiting / dynamic key rebalancing — each worker handles its own key's limits via VisionClient's built-in retry
- OCR extraction from image (qwen already extracts `text_content`)
- WebSocket push for live queue status updates — polling every 5s is sufficient at small scale
- Multi-monitor cursor capture (currently only primary monitor coords are meaningful)

## 9. Version & Release

Target version: **v0.4.0** (minor bump — breaking change for client, new server capabilities).

Artifacts:
- `WorkflowRecorder-0.4.0-Setup.exe` — new client (mandatory upgrade)
- Server code + `api_keys.txt.example` sample file
- DB auto-migrates on server start
- Dashboard rebuild (no breaking API changes exposed externally — all internal routes)

Rollout sequence:
1. Prepare `api_keys.txt` on server
2. Deploy new server (DB migrates, pool starts, old upload endpoint gone)
3. Release v0.4.0 installer
4. Employees install new client; old v0.3.x clients start failing 404 (log noise, but harmless)
