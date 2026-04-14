# Offline Analysis — Phase 4: Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show the original screenshot alongside the analysis fields in the Recording Playback page, with red solid box on cursor position and yellow dashed box on focused control rect. Add admin queue widget to Settings page.

**Architecture:** New reusable `<FrameImage>` component wraps `<img>` with CSS percentage-positioned overlays. Uses `frame.cursor_x/y` (OS-captured) as primary marker, Qwen's `mouse_position_estimate` as fallback. Yellow dashed rect for `frame.focus_rect`. Auth-protected image URLs served via `/api/frames/:id/image` from Phase 1. Settings.vue adds queue stats widget polling `/api/frames/queue` every 5s.

**Tech Stack:** Vue 3 Composition API, Naive UI components, native DnD already in place.

**Depends on:** Phase 1 (image serving endpoint exists), Phase 2 (analysis_status transitions work), Phase 3 (client uploads real cursor/focus coords).

---

## File Structure

### New files

```
dashboard/src/components/
└── FrameImage.vue          # <img> + CSS overlays for cursor + focus rect
dashboard/src/api/
└── frames.ts               # imageUrl(id), retry(id), queueStatus()
```

### Modified files

```
dashboard/src/api/sessions.ts          # FrameInfo: +image_path +analysis_status
                                       # +cursor_x +cursor_y +focus_rect
dashboard/src/views/Recording.vue      # show FrameImage + left/right layout
dashboard/src/views/Settings.vue       # add queue status widget
```

---

### Task 1: Extend TypeScript types

**Files:**
- Modify: `dashboard/src/api/sessions.ts`

- [ ] **Step 1: Read current FrameInfo**

Current `dashboard/src/api/sessions.ts` has:

```typescript
export interface FrameInfo {
  id: number
  employee_id: string
  session_id: string
  frame_index: number
  recorded_at: string
  received_at: string
  application: string | null
  window_title: string | null
  user_action: string | null
  text_content: string | null
  confidence: number
  mouse_position: number[]
  ui_elements: Array<{ name: string; element_type: string; coordinates: number[] }>
  context_data: Record<string, unknown>
}
```

- [ ] **Step 2: Add new fields**

Replace `FrameInfo` with the extended version:

```typescript
export type AnalysisStatus = 'pending' | 'running' | 'done' | 'failed'

export interface FrameInfo {
  id: number
  employee_id: string
  session_id: string
  frame_index: number
  recorded_at: string
  received_at: string
  application: string | null
  window_title: string | null
  user_action: string | null
  text_content: string | null
  confidence: number
  mouse_position: number[]
  ui_elements: Array<{ name: string; element_type: string; coordinates: number[] }>
  context_data: Record<string, unknown>
  // v0.4.0 offline-analysis fields
  image_path?: string
  analysis_status?: AnalysisStatus
  analysis_error?: string
  cursor_x?: number  // -1 if OS capture unavailable
  cursor_y?: number
  focus_rect?: number[] | null  // [x1, y1, x2, y2] in image pixels
}
```

- [ ] **Step 3: Verify type check**

Run: `cd dashboard && npx vue-tsc --noEmit`
Expected: 0 errors (new fields are optional, nothing else references them yet)

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/api/sessions.ts
git commit -m "feat(dashboard): FrameInfo carries image_path + analysis_status + cursor/focus"
```

---

### Task 2: frames API module

**Files:**
- Create: `dashboard/src/api/frames.ts`

- [ ] **Step 1: Write the module**

```typescript
import client from './client'

export interface QueueStats {
  pending: number
  running: number
  failed: number
  done: number
}

export const framesApi = {
  /** URL for the <img> tag to fetch the raw PNG. Axios interceptor adds JWT. */
  imageUrl: (frameId: number): string => `/api/frames/${frameId}/image`,

  /** Admin: reset a failed frame back to pending. */
  retry: (frameId: number) =>
    client.post<{ ok: boolean }>(`/api/frames/${frameId}/retry`),

  /** Admin: snapshot of pending/running/failed/done counts. */
  queueStatus: () => client.get<QueueStats>('/api/frames/queue'),
}
```

- [ ] **Step 2: Verify type check**

Run: `cd dashboard && npx vue-tsc --noEmit`
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/api/frames.ts
git commit -m "feat(dashboard): frames API module (imageUrl, retry, queueStatus)"
```

---

### Task 3: `<FrameImage>` component

**Files:**
- Create: `dashboard/src/components/FrameImage.vue`

- [ ] **Step 1: Write the component**

```vue
<script setup lang="ts">
import { ref, computed } from 'vue'
import type { FrameInfo } from '@/api/sessions'

const props = withDefaults(defineProps<{
  frame: FrameInfo
  maxWidth?: string
  clickable?: boolean
}>(), {
  maxWidth: '300px',
  clickable: true,
})

const emit = defineEmits<{
  (e: 'click'): void
}>()

const imgRef = ref<HTMLImageElement | null>(null)
const naturalW = ref(0)
const naturalH = ref(0)
const loaded = ref(false)
const errored = ref(false)

function onLoad() {
  if (imgRef.value) {
    naturalW.value = imgRef.value.naturalWidth
    naturalH.value = imgRef.value.naturalHeight
    loaded.value = true
  }
}

function onError() {
  errored.value = true
}

function onClick() {
  if (props.clickable) {
    emit('click')
  }
}

/** Red solid box at cursor_x/cursor_y. Falls back to qwen's mouse_position. */
const cursorOverlay = computed(() => {
  if (!loaded.value || naturalW.value === 0) return null
  const f = props.frame
  let cx = -1
  let cy = -1
  if (f.cursor_x !== undefined && f.cursor_y !== undefined &&
      f.cursor_x >= 0 && f.cursor_y >= 0) {
    cx = f.cursor_x
    cy = f.cursor_y
  } else if (f.mouse_position && f.mouse_position.length >= 2) {
    cx = f.mouse_position[0]
    cy = f.mouse_position[1]
  }
  if (cx < 0 || cy < 0) return null

  const BOX_PX = 40  // natural-pixel box size
  const pctX = (cx / naturalW.value) * 100
  const pctY = (cy / naturalH.value) * 100
  const pctBoxW = (BOX_PX / naturalW.value) * 100
  const pctBoxH = (BOX_PX / naturalH.value) * 100
  return {
    left: `calc(${pctX}% - ${pctBoxW / 2}%)`,
    top: `calc(${pctY}% - ${pctBoxH / 2}%)`,
    width: `${pctBoxW}%`,
    height: `${pctBoxH}%`,
  }
})

/** Yellow dashed box for the focused control rect. */
const focusOverlay = computed(() => {
  if (!loaded.value || naturalW.value === 0) return null
  const r = props.frame.focus_rect
  if (!r || r.length !== 4) return null
  const [x1, y1, x2, y2] = r
  if (x2 <= x1 || y2 <= y1) return null
  return {
    left: `${(x1 / naturalW.value) * 100}%`,
    top: `${(y1 / naturalH.value) * 100}%`,
    width: `${((x2 - x1) / naturalW.value) * 100}%`,
    height: `${((y2 - y1) / naturalH.value) * 100}%`,
  }
})
</script>

<template>
  <div
    class="frame-img-wrapper"
    :style="{ maxWidth: maxWidth }"
    :class="{ clickable }"
    @click="onClick"
  >
    <img
      ref="imgRef"
      :src="`/api/frames/${frame.id}/image`"
      class="frame-img"
      alt="recording frame"
      @load="onLoad"
      @error="onError"
    />
    <div v-if="errored" class="frame-error">
      <span>图片加载失败</span>
    </div>
    <div v-if="focusOverlay" class="focus-overlay" :style="focusOverlay"></div>
    <div v-if="cursorOverlay" class="cursor-overlay" :style="cursorOverlay"></div>
  </div>
</template>

<style scoped>
.frame-img-wrapper {
  position: relative;
  display: inline-block;
  line-height: 0;  /* eliminate baseline gap under img */
  background: #f5f5f5;
  border-radius: 4px;
  overflow: hidden;
}
.frame-img-wrapper.clickable {
  cursor: zoom-in;
}
.frame-img {
  width: 100%;
  height: auto;
  display: block;
}
.frame-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  font-size: 12px;
  background: #f5f5f5;
}
.cursor-overlay {
  position: absolute;
  border: 2px solid #f00;
  border-radius: 2px;
  box-shadow: 0 0 6px rgba(255, 0, 0, 0.6);
  pointer-events: none;
}
.focus-overlay {
  position: absolute;
  border: 2px dashed #fc0;
  pointer-events: none;
}
</style>
```

- [ ] **Step 2: Verify type check**

Run: `cd dashboard && npx vue-tsc --noEmit`
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/FrameImage.vue
git commit -m "feat(dashboard): FrameImage component with red cursor + yellow focus overlays"
```

---

### Task 4: Patch Axios client to attach JWT to `<img>` requests

**Files:**
- Modify: `dashboard/src/api/client.ts` if it doesn't already cover this
- OR: change FrameImage to fetch with axios and convert to blob URL

**Context:** `<img src="/api/frames/1/image">` is a plain browser GET; it does NOT go through the Axios interceptor, so the JWT header isn't attached and the server returns 401.

Simplest fix: inline the token into the URL as a query param (the server accepts `Authorization` header OR `?token=` param). OR use fetch with Authorization header and convert response to blob URL.

Blob URL approach is cleaner (no server change needed).

- [ ] **Step 1: Check current behavior**

Read `dashboard/src/api/client.ts`:

```typescript
import axios from 'axios'

const client = axios.create({
  baseURL: '',
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})
```

This interceptor only works for Axios calls, not for plain `<img src>` requests.

- [ ] **Step 2: Update FrameImage.vue to fetch via Axios + blob URL**

Replace the `<img :src="...">` pattern with blob-URL fetch. Update the `<script setup>` section of `dashboard/src/components/FrameImage.vue`:

Add import at top:

```typescript
import { ref, computed, watch, onUnmounted } from 'vue'
import client from '@/api/client'
```

Add state and a fetch function:

```typescript
const blobUrl = ref<string>('')

async function fetchImage() {
  if (blobUrl.value) {
    URL.revokeObjectURL(blobUrl.value)
    blobUrl.value = ''
  }
  try {
    const resp = await client.get(`/api/frames/${props.frame.id}/image`, {
      responseType: 'blob',
    })
    blobUrl.value = URL.createObjectURL(resp.data)
    errored.value = false
  } catch {
    errored.value = true
  }
}

watch(() => props.frame.id, fetchImage, { immediate: true })

onUnmounted(() => {
  if (blobUrl.value) {
    URL.revokeObjectURL(blobUrl.value)
  }
})
```

Change `<img>` src binding:

```vue
<img
  v-if="blobUrl"
  ref="imgRef"
  :src="blobUrl"
  class="frame-img"
  alt="recording frame"
  @load="onLoad"
  @error="onError"
/>
```

- [ ] **Step 3: Verify type check**

Run: `cd dashboard && npx vue-tsc --noEmit`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/components/FrameImage.vue
git commit -m "fix(dashboard): FrameImage uses Axios blob fetch so JWT auth works"
```

---

### Task 5: Update Recording.vue frame detail layout

**Files:**
- Modify: `dashboard/src/views/Recording.vue`

- [ ] **Step 1: Read current structure**

Current `Recording.vue` has a frame detail section inside the timeline `NTimelineItem`. It shows frame metadata (application, confidence, window_title, text_content, UI elements, context_data) but no image.

- [ ] **Step 2: Add image column next to the frame detail**

Locate the `NTimelineItem` content block inside Recording.vue. Currently it looks like:

```vue
<NTimelineItem v-for="frame in selectedSession.frames" ... >
  <template #header>
    ...timestamp + application + status...
  </template>
  <NSpace vertical>
    ...confidence, details, UI elements, etc...
  </NSpace>
</NTimelineItem>
```

Add FrameImage inline to the left of the content. Wrap the inner content in a flex row:

Add import at top of `<script setup>`:

```typescript
import FrameImage from '@/components/FrameImage.vue'
import { NModal } from 'naive-ui'
```

Add state for the modal:

```typescript
const modalFrame = ref<FrameInfo | null>(null)
function openFrameModal(frame: FrameInfo) {
  modalFrame.value = frame
}
```

In the template, restructure the frame content inside `NTimelineItem`. Wrap the per-frame content in a flex container:

```vue
<NTimelineItem
  v-for="frame in selectedSession.frames"
  :key="frame.id"
  :type="getConfidenceType(frame.confidence)"
>
  <template #header>
    <NSpace :size="8" align="center">
      <span>{{ formatTime(frame.recorded_at) }}</span>
      <NTag v-if="frame.application" size="small" :color="{ color: getAppColor(frame.application) }">
        {{ frame.application }}
      </NTag>
      <NTag
        v-if="frame.analysis_status && frame.analysis_status !== 'done'"
        size="small"
        :type="statusBadgeType(frame.analysis_status)"
      >
        {{ statusLabel(frame.analysis_status) }}
      </NTag>
    </NSpace>
  </template>

  <div class="frame-row">
    <div class="frame-left">
      <FrameImage
        :frame="frame"
        max-width="280px"
        @click="openFrameModal(frame)"
      />
    </div>
    <div class="frame-right">
      <NSpace vertical :size="8">
        <NSpace align="center">
          <span style="font-weight: 500;">{{ frame.user_action || '(无分析)' }}</span>
          <NProgress
            type="line"
            :percentage="Math.round(frame.confidence * 100)"
            :status="getConfidenceType(frame.confidence)"
            :show-indicator="true"
            style="width: 160px"
          />
        </NSpace>
        <NCollapse>
          <NCollapseItem title="详细信息" name="detail">
            <!-- existing NDescriptions block stays the same -->
            ...
          </NCollapseItem>
        </NCollapse>
      </NSpace>
    </div>
  </div>
</NTimelineItem>

<!-- Modal at the root of the template -->
<NModal
  :show="modalFrame !== null"
  preset="card"
  style="width: 90vw; max-width: 1600px;"
  :on-update:show="(v: boolean) => !v && (modalFrame = null)"
>
  <template v-if="modalFrame">
    <div style="display: flex; gap: 16px;">
      <div style="flex: 0 0 auto;">
        <FrameImage :frame="modalFrame" max-width="80vw" :clickable="false" />
      </div>
      <div style="flex: 1 1 auto; overflow-y: auto; max-height: 85vh;">
        <h3>{{ modalFrame.user_action || '(无分析)' }}</h3>
        <p v-if="modalFrame.application">应用: {{ modalFrame.application }}</p>
        <p v-if="modalFrame.window_title">窗口: {{ modalFrame.window_title }}</p>
        <p v-if="modalFrame.text_content">文本: {{ modalFrame.text_content }}</p>
      </div>
    </div>
  </template>
</NModal>
```

Add helper functions in `<script setup>`:

```typescript
type AnalysisStatus = 'pending' | 'running' | 'done' | 'failed'
function statusBadgeType(s: AnalysisStatus): 'default' | 'info' | 'warning' | 'error' | 'success' {
  switch (s) {
    case 'pending': return 'default'
    case 'running': return 'info'
    case 'done':    return 'success'
    case 'failed':  return 'error'
    default:        return 'default'
  }
}
function statusLabel(s: AnalysisStatus): string {
  return {
    pending: '等待分析',
    running: '分析中',
    done:    '已分析',
    failed:  '分析失败',
  }[s] || s
}
```

Add CSS at the bottom of the file:

```css
.frame-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.frame-left {
  flex: 0 0 auto;
}
.frame-right {
  flex: 1 1 auto;
  min-width: 0;
}
```

- [ ] **Step 3: Verify type check + build**

```
cd dashboard && npx vue-tsc --noEmit
cd dashboard && npm run build
```
Expected: both succeed with 0 errors.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/views/Recording.vue
git commit -m "feat(dashboard): Recording page shows screenshot + cursor/focus overlays"
```

---

### Task 6: Settings page — admin queue widget

**Files:**
- Modify: `dashboard/src/views/Settings.vue`

- [ ] **Step 1: Add queue widget**

In `dashboard/src/views/Settings.vue` add a new card showing pending/running/failed/done counts, polled every 5s. Only visible for admin role.

Modify the `<script setup>`:

```typescript
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { framesApi, type QueueStats } from '@/api/frames'
import {
  NCard, NDescriptions, NDescriptionsItem, NGrid, NGi, NStatistic,
  NSpin, NAlert, NSpace,
} from 'naive-ui'

const auth = useAuthStore()
const isAdmin = computed(() => auth.isAdmin)

// ... keep any existing state for server info ...

const queueStats = ref<QueueStats | null>(null)
const queueErr = ref<string>('')
let queueTimer: ReturnType<typeof setInterval> | null = null

async function fetchQueue() {
  if (!isAdmin.value) return
  try {
    const { data } = await framesApi.queueStatus()
    queueStats.value = data
    queueErr.value = ''
  } catch (e) {
    queueErr.value = String(e)
  }
}

onMounted(() => {
  if (isAdmin.value) {
    fetchQueue()
    queueTimer = setInterval(fetchQueue, 5000)
  }
})

onUnmounted(() => {
  if (queueTimer !== null) {
    clearInterval(queueTimer)
  }
})
```

In the template, add the queue card (keep existing content):

```vue
<NCard
  v-if="isAdmin"
  title="分析队列状态"
  style="margin-bottom: 16px;"
  size="small"
>
  <NSpin :show="queueStats === null">
    <NGrid v-if="queueStats" :cols="4" :x-gap="16">
      <NGi>
        <NStatistic label="待分析" :value="queueStats.pending" />
      </NGi>
      <NGi>
        <NStatistic label="分析中" :value="queueStats.running" />
      </NGi>
      <NGi>
        <NStatistic label="已分析" :value="queueStats.done" />
      </NGi>
      <NGi>
        <NStatistic label="失败" :value="queueStats.failed" />
      </NGi>
    </NGrid>
    <NAlert v-if="queueErr" type="warning" :show-icon="false" style="margin-top: 8px;">
      {{ queueErr }}
    </NAlert>
  </NSpin>
  <p style="color: #999; font-size: 12px; margin: 8px 0 0;">
    每 5 秒刷新一次。服务端 AnalysisPool 状态实时快照。
  </p>
</NCard>
```

Make sure the existing server-info card stays intact below or above this new card.

- [ ] **Step 2: Verify type check + build**

```
cd dashboard && npx vue-tsc --noEmit
cd dashboard && npm run build
```
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/views/Settings.vue
git commit -m "feat(dashboard): Settings page shows admin-only AnalysisPool queue stats"
```

---

## Phase 4 Completion Criteria

After all 6 tasks:

1. `FrameInfo` TS interface has `image_path`, `analysis_status`, `cursor_x`, `cursor_y`, `focus_rect`, `analysis_error` (all optional for backward-compat during rollout)
2. `frames.ts` API module exposes `imageUrl(id)`, `retry(id)`, `queueStatus()`
3. `<FrameImage>` renders PNG via Axios blob fetch (JWT-authed) with red solid cursor overlay and yellow dashed focus overlay
4. Recording.vue per-frame detail: left column shows clickable thumbnail, right column shows analysis fields + status badge; modal for full-size view
5. Settings.vue: admin-only queue widget polling `/api/frames/queue` every 5s showing pending/running/done/failed counts
6. `vue-tsc --noEmit` passes with 0 errors
7. `npm run build` succeeds
8. Backend tests remain unchanged at 255 passed

**Verification steps:**

```bash
# Type check + build
cd dashboard && npx vue-tsc --noEmit
cd dashboard && npm run build   # → dashboard/dist/

# Backend + Dashboard integration (manual smoke)
# terminal 1:
WORKFLOW_SERVER_DB=./frames.db WORKFLOW_DISABLE_ANALYSIS_POOL=1 \
  uvicorn server.app:app --host 127.0.0.1 --port 8000

# Then open http://localhost:8000 (served from dashboard/dist),
# login as admin/admin, go to "录制回放" — frames from the last v0.3.3 client
# still render (status = 'done', no image) with the layout unchanged.
# New frames uploaded via /frames/upload in Phase 3 will show the screenshot
# once analyzed by Phase 2's pool.
```
