# Workflow Recorder Dashboard Design Spec

> Status: **Approved**
> Date: 2026-04-13
> Author: gaozhi + Claude Opus 4.6

## 1. Overview

Web dashboard for the Workflow Recorder system. Provides visualization of per-employee desktop recording data, SOP (Standard Operating Procedure) authoring from recorded sessions, efficiency analytics, and compliance audit capabilities.

### Core Value Proposition

**Primary goal**: Turn raw screen recording frames into publishable, machine-replayable SOPs through an AI-assisted + human-reviewed editing workflow.

**Secondary goals**: Employee efficiency analytics and compliance audit trail.

### Architecture Decision

**Approach A: Monolithic Enhancement** — extend the existing FastAPI collection server with new API endpoints and serve a Vue 3 SPA as static files from the same process.

```
[Vue 3 SPA]  <->  [FastAPI (extended)]  <->  [SQLite -> future PostgreSQL]
                       |
                       +-- /api/frames   (existing)
                       +-- /api/auth     (new: JWT + DingTalk SSO)
                       +-- /api/sops     (new: SOP CRUD + publish)
                       +-- /api/stats    (new: efficiency aggregation)
                       +-- /api/audit    (new: audit log queries)
```

Rationale: minimal complexity for small-team deployment, single `uvicorn` process, DB abstraction layer (Repository pattern) allows future migration to PostgreSQL without rewriting API logic.

## 2. Roles & Permissions

### Role Definitions

| Role | Data Scope | Capabilities |
|------|-----------|-------------|
| **admin** | All employees, all data | User management, system config, client monitoring, audit logs, full SOP lifecycle |
| **manager** | Own department members | View recordings, SOP review/edit/publish, efficiency reports, audit queries for department |
| **employee** | Own data only | View own recordings, view published SOPs, submit SOP drafts |

### Data Isolation

- `employee`: API filters to `employee_id = current_user.employee_id`
- `manager`: API filters to `employee_id IN (department members)`
- `admin`: no filter, full access
- All authorization enforced server-side; frontend only hides UI elements

### Authentication

**Primary: DingTalk SSO (scan-to-login)**

- Employee scans DingTalk QR code on login page
- Backend exchanges auth code for DingTalk userid + user info via DingTalk Open API
- Auto-creates dashboard user on first login, maps DingTalk department to role:
  - Department manager (`is_dept_manager=true` from DingTalk) -> `manager` role
  - Regular member -> `employee` role
  - `admin` role assigned manually by existing admin

**Fallback: Username + Password**

- For external consultants or accounts without DingTalk
- `password_hash` field nullable; only accounts with a set password can use password login
- Admin can set/reset passwords in User Management

**Token Strategy**: JWT with access token (30min) + refresh token (7 days), bcrypt password hashing.

### User Table

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dingtalk_userid TEXT UNIQUE,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,              -- nullable; set = password login enabled
    display_name TEXT NOT NULL,
    avatar_url TEXT DEFAULT '',
    role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'employee')),
    employee_id TEXT,                -- links to frames.employee_id
    department TEXT DEFAULT '',
    department_id TEXT DEFAULT '',
    is_dept_manager BOOLEAN DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);
```

Department membership derived from `users.department_id` — no separate team_members table needed. DingTalk org tree synced on login or via manual "Sync DingTalk" button (admin only).

## 3. Page Structure & Navigation

### Sidebar Navigation (role-aware)

| Page | employee | manager | admin | Description |
|------|----------|---------|-------|-------------|
| Dashboard (Overview) | Y | Y | Y | KPI cards, app distribution chart, recent sessions |
| Recording Playback | Y (own) | Y (dept) | Y (all) | Session browser, frame timeline, per-frame AI analysis detail |
| SOP Management | Y (read published) | Y (edit/publish) | Y (all) | SOP list by status, SOP editor, export |
| Efficiency Analytics | Y (own) | Y (dept) | Y (all) | App usage pie chart, activity heatmap, trend lines, team leaderboard |
| Audit Query | - | Y (dept) | Y (all) | Full-text search across frames, CSV export |
| User Management | - | - | Y | User CRUD, role assignment, DingTalk sync |
| System Settings | - | - | Y | Client status monitoring, DB config, push key management |

### Overview Dashboard Cards

```
+------------+  +------------+  +------------+  +------------+
| Frames     |  | Active     |  | SOPs       |  | Online     |
| Today      |  | Sessions   |  | Draft/Pub  |  | Clients    |
|   1,234    |  |    12      |  |   8 / 3    |  |   5 / 7    |
+------------+  +------------+  +------------+  +------------+

+-- App Distribution (Pie) --+  +-- Recent Sessions (Table) --+
```

## 4. SOP Editor (Core Feature)

### Lifecycle

```
[Recorded Frames] -> [Auto-extract Draft] -> [Human Review/Edit] -> [Submit for Review] -> [Publish] -> [Export]
                                                    ^                         |
                                                    +--- Reject to Draft -----+
```

Status flow: `draft` -> `in_review` -> `published` (can reject from `in_review` back to `draft`)

### Data Model

```sql
CREATE TABLE sops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('draft', 'in_review', 'published')),
    created_by TEXT NOT NULL,
    assigned_reviewer TEXT,
    source_session_id TEXT,
    source_employee_id TEXT,
    tags TEXT DEFAULT '[]',           -- JSON array
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE sop_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sop_id INTEGER NOT NULL REFERENCES sops(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    application TEXT DEFAULT '',
    action_type TEXT DEFAULT '',      -- click/type/key/scroll/wait
    action_detail TEXT DEFAULT '{}',  -- JSON: target, coordinates, text, keys...
    screenshot_ref TEXT DEFAULT '',
    source_frame_ids TEXT DEFAULT '[]',
    confidence REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_sops_status ON sops(status);
CREATE INDEX idx_sop_steps_sop ON sop_steps(sop_id, step_order);
```

### Editor Layout

Three-panel layout:

- **Left panel**: Draggable step list (title + app icon + confidence badge), add step button, source frame thumbnail browser at bottom
- **Right panel**: Selected step detail editor (title, application, description, action type/detail, coordinates, associated screenshot preview, confidence bar, delete button)
- **Top bar**: SOP title, status badge, action buttons (Submit for Review / Publish / Export)
- **Bottom bar**: Export options (Markdown / JSON for computer-use / Copy)

### Key Interactions

| Interaction | Description |
|-------------|-------------|
| Auto-extract | From Recording Playback, select a session -> "Generate SOP" -> backend runs workflow_builder logic -> creates draft SOP |
| Drag & drop | Step list supports reorder via drag, auto-updates step_order |
| Inline edit | All step fields editable in right panel, auto-save with 500ms debounce |
| Frame association | Bottom frame browser shows source session thumbnails, click to link frame to current step |
| Add/delete steps | Manual insert empty step or "add as new step" from frame browser |
| Export Markdown | Renders step-by-step operation manual with screenshots |
| Export JSON | Outputs Workflow schema-compatible JSON for computer-use replay |

## 5. Efficiency Analytics

### Metrics (computed from existing frames table, no extra instrumentation)

| Metric | Computation |
|--------|------------|
| App usage duration | Group by `application`, sum time gaps between consecutive frames of same app |
| Active hours | Bucket frames by hour, any frames = active |
| Idle periods | Frame gap > threshold (5min) = idle period |
| App switch frequency | Count of consecutive frames with different `application` |
| Daily active duration | First-to-last frame span minus total idle time |

### Page Components

- **Filter bar**: Employee selector, date range picker, department filter, granularity (day/week/month)
- **App usage pie chart**: Top applications by duration
- **Activity heatmap**: 7x24 grid (weekday x hour), color = frame density
- **Trend line chart**: Daily active hours over time, multi-employee overlay
- **Team leaderboard** (manager/admin only): Table with active hours, primary app, SOP output per employee

## 6. Audit Query

### Search Capabilities

- Filter by: employee, application, time range, confidence threshold
- Keyword search across `user_action` and `text_content` fields
- Results in paginated table with expandable row detail (full frame info + UI elements)
- CSV export (up to 10,000 rows default, admin-configurable)

## 7. API Routes

### Auth

```
POST   /api/auth/login              # password login -> JWT
POST   /api/auth/dingtalk/callback  # DingTalk scan callback -> JWT
POST   /api/auth/refresh            # refresh token
GET    /api/auth/me                 # current user info + permissions
```

### Users (admin only)

```
GET    /api/users/                  # paginated user list
POST   /api/users/                  # create user (password fallback)
PUT    /api/users/:id               # edit role/status
DELETE /api/users/:id               # delete user
POST   /api/users/sync-dingtalk     # trigger DingTalk org sync
```

### Frames (existing, extended with auth filtering)

```
GET    /api/frames/                 # frame list (auto-filtered by role)
GET    /api/frames/stats            # efficiency aggregation
GET    /api/frames/heatmap          # activity heatmap data
GET    /api/frames/export           # CSV export
POST   /api/frames/                 # frame ingest (existing, client-side)
POST   /api/frames/batch            # batch ingest (existing)
```

### Sessions (new)

```
GET    /api/sessions/               # session list (by employee/date)
GET    /api/sessions/:id            # session detail with frame timeline
```

### SOPs (new)

```
GET    /api/sops/                   # SOP list (filter by status/creator)
POST   /api/sops/                   # create SOP (manual or auto-extract from session)
GET    /api/sops/:id                # SOP detail + all steps
PUT    /api/sops/:id                # update SOP metadata
DELETE /api/sops/:id                # delete SOP
POST   /api/sops/:id/generate       # re-extract steps from source session
PUT    /api/sops/:id/status         # status transition (draft->in_review->published / reject)
GET    /api/sops/:id/export/md      # export Markdown
GET    /api/sops/:id/export/json    # export computer-use JSON
```

### SOP Steps (new)

```
GET    /api/sops/:sop_id/steps/     # step list (ordered)
POST   /api/sops/:sop_id/steps/     # add step
PUT    /api/sops/:sop_id/steps/:id  # edit step
DELETE /api/sops/:sop_id/steps/:id  # delete step
PUT    /api/sops/:sop_id/steps/reorder  # batch reorder (after drag & drop)
```

### Dashboard (new)

```
GET    /api/dashboard/summary       # overview card data
GET    /api/dashboard/recent-sessions  # recent active sessions
```

## 8. Frontend Architecture

### Tech Stack

- **Vue 3** + Composition API + TypeScript
- **Naive UI** component library (Chinese-friendly, built-in data table + ECharts integration)
- **Pinia** state management (auth store + app store)
- **Vue Router** with navigation guards (role-based route access)
- **Axios** HTTP client with JWT interceptor (auto-attach token, 401 redirect to login)
- **vuedraggable** for SOP step drag & drop
- **ECharts** (via Naive UI integration) for all charts
- **Vite** build tooling

### Project Structure

```
dashboard/
├── index.html
├── package.json
├── vite.config.ts                 # proxy /api -> FastAPI :8000
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/index.ts            # routes + role-based guards
│   ├── stores/
│   │   ├── auth.ts                # user/token/role state
│   │   └── app.ts                 # global UI state
│   ├── api/
│   │   ├── client.ts              # axios instance + interceptors
│   │   ├── auth.ts
│   │   ├── frames.ts
│   │   ├── sessions.ts
│   │   ├── sops.ts
│   │   └── users.ts
│   ├── views/
│   │   ├── Login.vue              # DingTalk QR + password dual-tab
│   │   ├── Dashboard.vue
│   │   ├── Recording.vue
│   │   ├── SopList.vue
│   │   ├── SopEditor.vue          # core page
│   │   ├── Analytics.vue
│   │   ├── Audit.vue
│   │   ├── UserManagement.vue
│   │   └── Settings.vue
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.vue
│   │   │   └── Header.vue
│   │   ├── sop/
│   │   │   ├── StepList.vue
│   │   │   ├── StepDetail.vue
│   │   │   └── FrameBrowser.vue
│   │   ├── charts/
│   │   │   ├── AppPieChart.vue
│   │   │   ├── HeatmapChart.vue
│   │   │   └── TrendLine.vue
│   │   └── common/
│   │       ├── DataTable.vue
│   │       └── ExportButton.vue
│   └── utils/
│       ├── permission.ts
│       └── format.ts
└── dist/                          # build output, served by FastAPI
```

## 9. Build & Deployment

### Development

```bash
# Terminal 1: Frontend dev server with hot reload
cd dashboard && npm run dev        # Vite :5173, proxies /api -> :8000

# Terminal 2: Backend API server
uvicorn server.app:app --reload    # FastAPI :8000
```

### Production (single process)

```bash
# Build frontend
cd dashboard && npm run build      # -> dashboard/dist/

# FastAPI serves both API and static files
# Add to server/app.py:
#   app.mount("/", StaticFiles(directory="dashboard/dist", html=True))

uvicorn server.app:app --host 0.0.0.0 --port 8000
```

### DB Migration Path

Current: SQLite (single file, WAL mode for concurrent reads)
Future: Replace `server/db.py` implementation with PostgreSQL driver (same Repository interface, swap connection logic). No API or frontend changes needed.

## 10. DingTalk Integration

### Required Setup (operator action)

1. Create an "Internal Enterprise App" on [DingTalk Open Platform](https://open.dingtalk.com/)
2. Enable permissions: `Contact.User.Read`, `Login.SSO`
3. Configure scan-login callback URL: `https://<dashboard-host>/api/auth/dingtalk/callback`
4. Obtain `AppKey` + `AppSecret`, set as environment variables:
   - `DINGTALK_APP_KEY`
   - `DINGTALK_APP_SECRET`

### Org Sync Strategy

- On each DingTalk login: fetch user's department info, update `users` table
- Manual full sync via "Sync DingTalk" button (admin only): walks the department tree, creates/updates all users
- No scheduled sync (keep it simple); admin clicks sync when org changes

## 11. Out of Scope (v1)

- Mobile-responsive layout (desktop-only for v1)
- Real-time WebSocket push (polling-based refresh is sufficient for small teams)
- Multi-language i18n (Chinese-only for v1)
- Screenshot image storage and serving (frames table stores metadata only; screenshot files stay on client machines for now)
- Automated SOP quality scoring
- SOP version history / diff view
