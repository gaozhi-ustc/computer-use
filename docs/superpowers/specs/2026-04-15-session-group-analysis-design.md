# Session-Based Group Analysis & Interactive SOP Refinement

**Date**: 2026-04-15
**Status**: Approved
**Scope**: Server-side architecture redesign — from per-frame real-time analysis to session-level grouped analysis with interactive SOP refinement

## 1. Overview

### Problem

Current architecture analyzes each frame independently as it arrives. This produces per-frame descriptions but lacks the ability to understand multi-frame logical actions. SOP generation is a simple `itertools.groupby` by application name, which misses the semantic structure of user workflows.

### Solution

Redesign the server-side analysis pipeline:

1. **Stop real-time per-frame analysis** — frames are uploaded and stored, but not immediately sent to the vision model
2. **Detect session completion** — a SessionFinalizer thread monitors for idle sessions (no new frames for 5 minutes)
3. **Group frames by logical action** — a lightweight local algorithm (no LLM) clusters frames into action groups, with overlap at boundaries
4. **Analyze each group with the vision model** — one API call per group, passing the full image sequence + cursor/focus coordinates, producing multi-step SOP output
5. **Interactive SOP refinement** — users review generated SOPs, submit feedback, and the LLM regenerates with revision tracking

## 2. Architecture

```
Client uploads frames
    ↓
POST /frames/upload → save PNG + INSERT frames (status='uploaded')
                      UPDATE sessions SET last_frame_at=now
    ↓
SessionFinalizer thread (polls every 60s)
    → finds sessions where last_frame_at > 5min ago AND status='active'
    → status = 'finalizing'
    → FrameGrouper(frames) → frame_groups records
    → status = 'grouped'
    → each group queued for analysis
    ↓
AnalysisPool workers (one per API key, existing thread pool)
    → claim_next_pending_group()
    → load all frame images in group + cursor/focus data
    → single vision API call → multi-step SOP JSON
    → write sop_steps
    → when all groups done → session status = 'analyzed', auto-create SOP (rev 1)
    ↓
User reviews SOP in Dashboard
    → submits feedback (full or per-step)
    → snapshot current steps → sop_revisions
    → LLM regenerates with: frames + current steps + feedback
    → new revision created
    → repeat until satisfied → publish
```

## 3. Data Model Changes

### 3.1 New table: `sessions`

Sessions become explicit database records instead of virtual aggregations.

```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL,
    status TEXT CHECK(status IN ('active','finalizing','grouped','analyzed','failed'))
        DEFAULT 'active',
    first_frame_at TEXT,
    last_frame_at TEXT,
    frame_count INTEGER DEFAULT 0,
    finalized_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_sessions_employee ON sessions(employee_id);
```

**Status transitions**: `active` → `finalizing` → `grouped` → `analyzed`
Any stage can transition to `failed` on unrecoverable error.

**Upload-time maintenance**: Each `POST /frames/upload` upserts into `sessions`:
- First frame for a session_id → INSERT with status='active'
- Subsequent frames → UPDATE last_frame_at, frame_count += 1

### 3.2 New table: `frame_groups`

```sql
CREATE TABLE frame_groups (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    group_index INTEGER NOT NULL,
    frame_ids_json TEXT NOT NULL,        -- e.g. [233, 234, 235, 236]
    primary_application TEXT,            -- dominant app in group (from window title hint)
    analysis_status TEXT CHECK(analysis_status IN ('pending','running','done','failed'))
        DEFAULT 'pending',
    analysis_error TEXT,
    analysis_attempts INTEGER DEFAULT 0,
    analyzed_at TEXT,
    created_at TEXT,
    UNIQUE(session_id, group_index)
);
CREATE INDEX idx_frame_groups_status ON frame_groups(analysis_status);
```

### 3.3 Modify `frames` table

- On upload, `analysis_status` is set to `'uploaded'` instead of `'pending'`
- Per-frame analysis fields (`application`, `user_action`, `confidence`, etc.) are no longer populated by the real-time pipeline; they may be backfilled from group analysis results if needed
- No new columns required; existing `cursor_x`, `cursor_y`, `focus_rect_json`, `image_path` are sufficient

### 3.4 Extend `sop_steps` table

```sql
ALTER TABLE sop_steps ADD COLUMN human_description TEXT;
ALTER TABLE sop_steps ADD COLUMN machine_actions TEXT;  -- JSON array
ALTER TABLE sop_steps ADD COLUMN revision INTEGER DEFAULT 1;
```

- `human_description`: natural language for humans ("Click the 'Submit' button in the top-right corner")
- `machine_actions`: structured for RPA replay, e.g.:
  ```json
  [
    {"type": "click", "x": 1234, "y": 567, "target": "Submit button"},
    {"type": "type", "text": "hello", "target": "Search box", "x": 800, "y": 300}
  ]
  ```
- `revision`: which SOP revision this step belongs to

### 3.5 Extend `sops` table

```sql
-- Add 'regenerating' to allowed status values
-- SQLite doesn't support ALTER CHECK, so handled in application code
ALTER TABLE sops ADD COLUMN revision INTEGER DEFAULT 1;
ALTER TABLE sops ADD COLUMN source_group_ids_json TEXT;  -- [1, 2, 3] frame_group IDs
```

Status values become: `draft`, `regenerating`, `in_review`, `published`.

### 3.6 New table: `sop_feedbacks`

```sql
CREATE TABLE sop_feedbacks (
    id INTEGER PRIMARY KEY,
    sop_id INTEGER NOT NULL REFERENCES sops(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    feedback_text TEXT NOT NULL,
    feedback_scope TEXT DEFAULT 'full',  -- 'full' or 'step:N'
    created_at TEXT
);
CREATE INDEX idx_sop_feedbacks_sop ON sop_feedbacks(sop_id);
```

### 3.7 New table: `sop_revisions`

```sql
CREATE TABLE sop_revisions (
    id INTEGER PRIMARY KEY,
    sop_id INTEGER NOT NULL REFERENCES sops(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    steps_snapshot_json TEXT NOT NULL,
    feedback_id INTEGER REFERENCES sop_feedbacks(id),
    created_at TEXT,
    UNIQUE(sop_id, revision)
);
```

## 4. SessionFinalizer

A daemon thread started alongside AnalysisPool in `app.startup`.

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SESSION_IDLE_TIMEOUT_SECONDS` | 300 | Seconds after last frame before session is considered ended |
| `FINALIZER_POLL_INTERVAL_SECONDS` | 60 | How often to check for idle sessions |

### Logic

```
Every FINALIZER_POLL_INTERVAL_SECONDS:
    1. Query sessions with status='active' AND last_frame_at < now - SESSION_IDLE_TIMEOUT_SECONDS
    2. For each such session:
        a. Set status = 'finalizing', finalized_at = now
        b. Load all frames for session, ordered by frame_index ASC
        c. Run FrameGrouper → list of frame ID lists
        d. INSERT frame_groups records (status='pending')
        e. Set session status = 'grouped'
    3. On error for any session: set status = 'failed', log error, continue to next
```

### File

New file: `server/session_finalizer.py`

## 5. FrameGrouper Algorithm

A pure-Python module with no LLM dependency. Operates on frame metadata only (plus optional perceptual hash from image files).

### File

New file: `server/frame_grouper.py`

### Boundary Detection (priority order)

Four signals are evaluated. Each produces a set of boundary indices. The union of all boundaries defines the split points.

**P1 — Application switch**: Compare window title or process name between consecutive frames. Different application → boundary.

Source: `frames` table does not store raw window title from the client at upload time.
- **Implementation**: Add optional `window_title` Form field to `POST /frames/upload`. Client already captures this in `window_info.py` — pass it through `image_uploader.py`. Store in a new `window_title_raw` column on `frames`.
- **Fallback**: If field is empty/missing (older clients), skip P1 and rely on P2-P4.

**P2 — Image similarity**: Compute perceptual hash (pHash) for each frame image. If hamming distance between consecutive frames exceeds `PHASH_THRESHOLD` (default: 12), mark as boundary. Uses existing `imagehash` dependency.

**P3 — Time gap**: If interval between consecutive frames exceeds `TIME_GAP_MULTIPLIER × median_interval` (default multiplier: 3), mark as boundary.

**P4 — Cursor/focus jump**: Compute Euclidean distance between consecutive cursor positions. If distance > `CURSOR_JUMP_RATIO × screen_diagonal` (default ratio: 0.3), mark as boundary. Screen diagonal estimated from first frame's image dimensions.

### Overlap

At each boundary index `b`, the preceding group extends to include frames `[b, b+1, b+2]` (3 frames into the next group), and the following group extends to include frames `[b-3, b-2, b-1]` (3 frames from the previous group). Clamped to array bounds.

### Minimum Group Size

If a group after splitting has fewer than 2 frames (excluding overlap), merge it with the adjacent group that has the smaller frame count.

### Output

```python
@dataclass
class FrameGroup:
    group_index: int
    frame_ids: list[int]        # includes overlap frames
    primary_application: str    # most common window title in group
```

## 6. Group-Level Analysis

### AnalysisPool Changes

Replace per-frame claim with per-group claim:

| Before | After |
|--------|-------|
| `claim_next_pending_frame()` | `claim_next_pending_group()` |
| `mark_frame_done(frame_id, ...)` | `mark_group_done(group_id, sop_steps)` |
| `mark_frame_failed(frame_id, ...)` | `mark_group_failed(group_id, error)` |
| Process: 1 image → 1 analysis | Process: N images → M sop_steps |

Worker loop:
```
1. group = claim_next_pending_group()  -- atomic UPDATE...RETURNING
2. frame_ids = JSON.parse(group.frame_ids_json)
3. frames = load frames by IDs, ordered by frame_index
4. images = [read PNG from frame.image_path for each frame]
5. metadata = [{cursor_x, cursor_y, focus_rect, timestamp} for each frame]
6. response = vision_api_call(images, metadata)  -- single call, all images
7. parse response → list of SopStep
8. mark_group_done(group_id, steps)
9. check: all groups for this session done?
   → yes: session.status = 'analyzed', auto-create SOP
```

### Vision API Prompt

**System prompt**:
```
You are a workflow SOP extraction expert. You will receive a sequence of
screenshots captured over time, along with mouse cursor coordinates and
focus region data for each frame.

Your task: identify the discrete user actions and produce reproducible
SOP steps.

For each step, output:
{
    "step_order": <int>,
    "title": "<short action title>",
    "human_description": "<detailed description a human can follow to
        reproduce this action, including specific UI elements, their
        locations, and what to look for>",
    "machine_actions": [
        {
            "type": "click|double_click|right_click|type|key|scroll|drag",
            "x": <pixel x>,
            "y": <pixel y>,
            "target": "<UI element name>",
            "text": "<for type actions>",
            "key": "<for key actions, e.g. Enter, Ctrl+S>"
        }
    ],
    "application": "<application name>",
    "key_frame_indices": [<indices within this group that best represent this step>]
}

Return a JSON object: {"steps": [...]}

Guidelines:
- One step = one logical user action (may span multiple frames)
- Include precise coordinates from the cursor data provided
- human_description should be detailed enough for someone unfamiliar with the workflow
- machine_actions should be precise enough for RPA replay
- key_frame_indices reference the 0-based index within the provided image sequence
```

**User prompt** (per group):
```
Here are {N} sequential screenshots from a recording session.
For each frame I provide: timestamp, cursor position (x, y), and focus
region [x1, y1, x2, y2] if available.

Frame data:
- Frame 0: timestamp={ts}, cursor=({cx}, {cy}), focus_rect={fr}
- Frame 1: ...
...

Please extract the SOP steps from this image sequence.
```

Images are passed as multiple image content blocks in the same message.

### Auto-SOP Creation

When all groups for a session reach `done`:

1. Set `sessions.status = 'analyzed'`
2. Create SOP: `INSERT INTO sops (title, status='draft', revision=1, source_session_id, source_employee_id, source_group_ids_json)`
3. Aggregate all group analysis results, ordered by group_index → step_order renumbered sequentially
4. Write to `sop_steps` with `revision=1`

## 7. Interactive SOP Refinement

### Backend API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/sops/{id}` | all | SOP metadata + current revision steps |
| `GET` | `/api/sops/{id}/status` | all | `{status, revision}` for polling |
| `POST` | `/api/sops/{id}/feedback` | manager, admin | Submit feedback, trigger regeneration |
| `GET` | `/api/sops/{id}/revisions` | manager, admin | List all revisions with feedback |
| `GET` | `/api/sops/{id}/revisions/{rev}` | manager, admin | Get historical step snapshot |
| `POST` | `/api/sops/{id}/revisions/{rev}/restore` | manager, admin | Restore historical version |

### Feedback → Regeneration Flow

```
POST /api/sops/{id}/feedback
Body: { "feedback_text": "Step 3 should include...", "scope": "step:3" }

Server:
  1. Snapshot current steps → sop_revisions (revision=current)
  2. INSERT sop_feedbacks (revision=current, feedback_text, scope)
  3. sops.revision += 1, sops.status = 'regenerating'
  4. Queue regeneration task to AnalysisPool

Worker picks up regeneration task:
  1. Load source frame images (via source_group_ids → frame_groups → frame_ids → frames.image_path)
  2. Load current SOP steps as context
  3. Load feedback history (current + up to 3 previous)
  4. Build prompt:
     SYSTEM: "You are an SOP refinement assistant..."
     USER:
       - Original frame images
       - Current SOP steps JSON
       - User feedback text
       - If scope="step:N", emphasize that step
  5. LLM returns revised steps JSON
  6. Delete old-revision steps, write new steps (revision=new)
  7. sops.status = 'draft'
```

### Regeneration Prompt

```
SYSTEM:
You are an SOP refinement assistant. You will receive:
1. The original screenshot sequence from a workflow recording
2. The current SOP steps (which you or a previous version generated)
3. User feedback requesting specific changes

Revise the SOP steps according to the feedback. Maintain the same
output format as the original generation. Only change what the
feedback requests — preserve steps that are not mentioned.

USER:
Current SOP steps:
{current_steps_json}

User feedback (scope: {scope}):
"{feedback_text}"

[Original frame images attached]

Please output the revised steps.
```

### Frontend Design

#### Page Layout: `SopEditor.vue` (redesigned)

```
┌─────────────────────────────────────────────────────────┐
│  SOP Title    [rev 2/3]  [◀ ▶]           [Submit Review]│
├────────────────────────────┬────────────────────────────┤
│  Step List (scrollable)    │  Frame Preview             │
│                            │                            │
│  ┌──────────────────────┐  │  ┌──────────────────────┐  │
│  │ 1. Open Excel file   │◄─┼──│ [Screenshot carousel]│  │
│  │    human_description  │  │  │  ◀  Frame 3/8  ▶    │  │
│  │    🖱 click [234,56]  │  │  │  + cursor overlay   │  │
│  │    [Feedback on step] │  │  └──────────────────────┘  │
│  └──────────────────────┘  │                            │
│  ┌──────────────────────┐  │                            │
│  │ 2. Select cell A1    │  │                            │
│  │    ...                │  │                            │
│  └──────────────────────┘  │                            │
│  ...                       │                            │
├────────────────────────────┴────────────────────────────┤
│  ┌────────────────────────────────────────────────────┐  │
│  │  Enter modification feedback...                    │  │
│  └────────────────────────────────────────────────────┘  │
│  [Regenerate SOP]                    [View History]      │
└─────────────────────────────────────────────────────────┘
```

#### New Components

| Component | Purpose |
|-----------|---------|
| `SopStepCard.vue` | Single step: title, human_description, machine_actions, source frame link, per-step feedback button |
| `SopFeedbackInput.vue` | Text input with scope selector (full / step:N), submit button, loading state |
| `SopRevisionNav.vue` | `◀ revision 2/3 ▶` navigation, "Restore this version" button on historical views |
| `FrameCarousel.vue` | Horizontal image carousel for group frames, reuses `FrameImage.vue` with nav arrows |

#### Frontend API additions (`api/sops.ts`)

```typescript
getStatus: (sopId: number) =>
    client.get<{ status: string; revision: number }>(`/api/sops/${sopId}/status`)

submitFeedback: (sopId: number, body: { feedback_text: string; scope: string }) =>
    client.post(`/api/sops/${sopId}/feedback`, body)

listRevisions: (sopId: number) =>
    client.get(`/api/sops/${sopId}/revisions`)

getRevision: (sopId: number, rev: number) =>
    client.get(`/api/sops/${sopId}/revisions/${rev}`)

restoreRevision: (sopId: number, rev: number) =>
    client.post(`/api/sops/${sopId}/revisions/${rev}/restore`)
```

#### Interaction States

| State | UI Behavior |
|-------|-------------|
| `draft` | Steps editable, feedback input active, "Submit Review" enabled |
| `regenerating` | Steps show skeleton/loading, feedback input disabled, polling `/status` every 3s |
| `in_review` | Steps read-only, feedback still available for reviewer |
| `published` | Fully read-only |
| Historical revision | Steps read-only with grey background, "Restore" button visible |

## 8. File Changes Summary

### New Files

| File | Purpose |
|------|---------|
| `server/session_finalizer.py` | SessionFinalizer daemon thread |
| `server/frame_grouper.py` | FrameGrouper algorithm (P1-P4 + overlap) |
| `server/group_analysis.py` | Group-level vision prompt construction + response parsing |
| `server/sop_feedback_router.py` | Feedback/revision/regeneration API endpoints |
| `dashboard/src/components/SopStepCard.vue` | Step display card with feedback |
| `dashboard/src/components/SopFeedbackInput.vue` | Feedback input component |
| `dashboard/src/components/SopRevisionNav.vue` | Version navigation |
| `dashboard/src/components/FrameCarousel.vue` | Multi-frame image carousel |

### Modified Files

| File | Changes |
|------|---------|
| `server/app.py` | Start SessionFinalizer on startup, register new router |
| `server/db.py` | New tables (sessions, frame_groups, sop_feedbacks, sop_revisions), ALTER sops/sop_steps, new CRUD functions for groups/sessions/feedbacks/revisions |
| `server/analysis_pool.py` | Replace per-frame claim with per-group claim, add regeneration task handling |
| `server/frames_router.py` | Upload endpoint upserts sessions table, set frame status='uploaded' |
| `server/sops_router.py` | Auto-SOP creation after analysis, integrate with revisions |
| `server/sessions_router.py` | Return session status field, use sessions table |
| `dashboard/src/api/sops.ts` | Add feedback/revision/status API methods |
| `dashboard/src/views/SopEditor.vue` | Redesign to three-panel layout with feedback |

### Unchanged

- Client-side code (capture, upload) — no changes needed
- `server/auth.py`, `server/auth_router.py`, `server/permissions.py` — no changes
- `server/stats_router.py`, `server/users_router.py` — no changes

## 9. Configuration

All new parameters with defaults, configurable via environment variables:

| Env Var | Default | Description |
|---------|---------|-------------|
| `SESSION_IDLE_TIMEOUT` | `300` | Seconds to wait before finalizing a session |
| `FINALIZER_POLL_INTERVAL` | `60` | SessionFinalizer polling interval in seconds |
| `GROUPER_PHASH_THRESHOLD` | `12` | Perceptual hash hamming distance threshold |
| `GROUPER_TIME_GAP_MULTIPLIER` | `3` | Time gap = multiplier × median interval |
| `GROUPER_CURSOR_JUMP_RATIO` | `0.3` | Cursor jump as fraction of screen diagonal |
| `GROUPER_OVERLAP_FRAMES` | `3` | Number of overlap frames at group boundaries |
| `GROUPER_MIN_GROUP_SIZE` | `2` | Minimum frames per group (excluding overlap) |

## 10. Migration & Compatibility

- **Database migration**: All schema changes use `CREATE TABLE IF NOT EXISTS` and idempotent `ALTER TABLE` (same pattern as v0.4.0)
- **Existing frames**: Frames already in DB with `analysis_status='done'` remain untouched. They will not be re-analyzed. Only new sessions (uploaded after this change) enter the new pipeline.
- **Existing SOPs**: Existing SOPs remain functional. New fields (`revision`, `human_description`, `machine_actions`) default to sensible values (revision=1, others NULL).
- **Rollback**: If needed, set `WORKFLOW_DISABLE_SESSION_FINALIZER=1` to revert to per-frame analysis behavior (AnalysisPool would need a flag to switch between modes).
