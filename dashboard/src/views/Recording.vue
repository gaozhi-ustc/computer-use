<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { sessionsApi, type SessionInfo, type SessionDetail, type FrameInfo, type AnalysisStatus } from '@/api/sessions'
import { sopsApi } from '@/api/sops'
import FrameImage from '@/components/FrameImage.vue'
import {
  NCard, NSpace, NInput, NDatePicker, NTag, NBadge, NEmpty,
  NSpin, NTimeline, NTimelineItem, NGrid, NGi,
  NScrollbar, NDescriptions, NDescriptionsItem,
  NButton, NModal, useMessage
} from 'naive-ui'

const modalFrame = ref<FrameInfo | null>(null)
function openFrameModal(frame: FrameInfo) {
  modalFrame.value = frame
}

function statusBadgeType(s: AnalysisStatus): 'default' | 'info' | 'success' | 'warning' | 'error' {
  switch (s) {
    case 'pending': return 'default'
    case 'running': return 'info'
    case 'done': return 'success'
    case 'failed': return 'error'
    default: return 'default'
  }
}

function statusLabel(s: AnalysisStatus): string {
  switch (s) {
    case 'pending': return '等待分析'
    case 'running': return '分析中'
    case 'done': return '已分析'
    case 'failed': return '分析失败'
    default: return ''
  }
}

const router = useRouter()
const message = useMessage()

const auth = useAuthStore()
const sessions = ref<SessionInfo[]>([])
const selectedSession = ref<SessionDetail | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const employeeFilter = ref('')
const dateRange = ref<[number, number] | null>(null)
const selectedSessionId = ref('')
const generatingSop = ref(false)
const analyzingSession = ref(false)

const FRAMES_PAGE_SIZE = 100
const framesPage = ref(1)
const pagedFrames = computed(() => {
  if (!selectedSession.value) return []
  const end = framesPage.value * FRAMES_PAGE_SIZE
  return selectedSession.value.frames.slice(0, end)
})
const totalFrames = computed(() => selectedSession.value?.frames.length || 0)
const hasMoreFrames = computed(() => pagedFrames.value.length < totalFrames.value)

async function generateSop() {
  if (!selectedSession.value) return
  generatingSop.value = true
  try {
    const session = selectedSession.value
    const { data: sopData } = await sopsApi.create({
      title: `SOP - ${session.employee_id} / ${session.session_id.slice(0, 8)}`,
      source_session_id: session.session_id,
      source_employee_id: session.employee_id,
    })
    await sopsApi.generate(sopData.id)
    message.success('SOP 已生成')
    router.push(`/sops/${sopData.id}`)
  } catch {
    message.error('生成 SOP 失败')
  } finally {
    generatingSop.value = false
  }
}

async function analyzeSession() {
  if (!selectedSession.value) return
  analyzingSession.value = true
  try {
    await sessionsApi.analyze(selectedSession.value.session_id)
    message.success('Session 分析已触发，后台正在处理分组与 SOP 生成')
    await fetchSessions()
  } catch (e: any) {
    const detail = e?.response?.data?.detail || '触发分析失败'
    message.error(detail)
  } finally {
    analyzingSession.value = false
  }
}

async function fetchSessions() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { limit: 50 }
    if (employeeFilter.value) params.employee_id = employeeFilter.value
    if (dateRange.value) {
      params.date_from = new Date(dateRange.value[0]).toISOString()
      params.date_to = new Date(dateRange.value[1]).toISOString()
    }
    const { data } = await sessionsApi.list(params)
    sessions.value = data.sessions
  } catch (e) {
    console.error('Failed to fetch sessions', e)
  } finally {
    loading.value = false
  }
}

async function selectSession(session: SessionInfo) {
  selectedSessionId.value = session.session_id
  detailLoading.value = true
  framesPage.value = 1
  try {
    const { data } = await sessionsApi.detail(session.session_id)
    selectedSession.value = data
  } catch (e) {
    console.error('Failed to fetch session detail', e)
  } finally {
    detailLoading.value = false
  }
}

function formatTime(isoStr: string): string {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDate(isoStr: string): string {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleDateString('zh-CN') + ' ' + d.toLocaleTimeString('zh-CN')
}

function truncateId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id
}

function getAppColor(app: string): string {
  const colors: Record<string, string> = {
    'chrome': 'info',
    'excel': 'success',
    'word': 'warning',
    'notepad': 'default',
    'explorer': 'error',
  }
  const lower = app.toLowerCase()
  for (const key in colors) {
    if (lower.includes(key)) return colors[key]
  }
  return 'default'
}

onMounted(fetchSessions)
watch([employeeFilter, dateRange], fetchSessions, { deep: true })
</script>

<template>
  <div class="recording-page">
    <NGrid :x-gap="16" :cols="24">
      <!-- Left panel: session list -->
      <NGi :span="8">
        <NCard title="录制会话" size="small">
          <template #header-extra>
            <NBadge :value="sessions.length" />
          </template>

          <NSpace vertical :size="12">
            <NDatePicker
              v-model:value="dateRange"
              type="daterange"
              clearable
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              style="width: 100%"
            />
            <NInput
              v-if="auth.isAdmin || auth.isManager"
              v-model:value="employeeFilter"
              placeholder="按员工ID筛选"
              clearable
            />
          </NSpace>

          <NSpin :show="loading" style="margin-top: 12px; min-height: 200px">
            <NScrollbar style="max-height: calc(100vh - 320px)">
              <NEmpty
                v-if="sessions.length === 0 && !loading"
                description="暂无录制会话"
                style="margin-top: 40px"
              />
              <NSpace v-else vertical :size="8">
                <NCard
                  v-for="session in sessions"
                  :key="session.session_id"
                  size="small"
                  hoverable
                  :class="{ 'session-card-active': selectedSessionId === session.session_id }"
                  class="session-card"
                  @click="selectSession(session)"
                >
                  <NSpace vertical :size="4">
                    <NSpace justify="space-between" align="center">
                      <span class="session-id">
                        {{ session.employee_id }} / {{ truncateId(session.session_id) }}
                      </span>
                      <NBadge
                        :value="session.frame_count"
                        :max="999"
                        type="info"
                      />
                    </NSpace>
                    <span class="session-time">
                      {{ formatTime(session.first_frame_at) }} - {{ formatTime(session.last_frame_at) }}
                    </span>
                    <NSpace :size="4">
                      <NTag
                        v-for="app in session.applications"
                        :key="app"
                        size="small"
                        :type="getAppColor(app) as any"
                      >
                        {{ app }}
                      </NTag>
                    </NSpace>
                  </NSpace>
                </NCard>
              </NSpace>
            </NScrollbar>
          </NSpin>
        </NCard>
      </NGi>

      <!-- Right panel: frame timeline -->
      <NGi :span="16">
        <NCard title="帧时间线" size="small">
          <NEmpty
            v-if="!selectedSession && !detailLoading"
            description="请选择一个会话查看详情"
            style="margin-top: 80px"
          />

          <NSpin :show="detailLoading">
            <template v-if="selectedSession">
              <NDescriptions
                label-placement="left"
                bordered
                :column="2"
                size="small"
                style="margin-bottom: 16px"
              >
                <NDescriptionsItem label="员工ID">
                  {{ selectedSession.employee_id }}
                </NDescriptionsItem>
                <NDescriptionsItem label="会话ID">
                  {{ selectedSession.session_id }}
                </NDescriptionsItem>
                <NDescriptionsItem label="总帧数">
                  {{ selectedSession.frame_count }}
                </NDescriptionsItem>
                <NDescriptionsItem label="帧数据">
                  {{ pagedFrames.length }} / {{ totalFrames }} 帧已显示
                </NDescriptionsItem>
              </NDescriptions>

              <NSpace style="margin-bottom: 16px">
                <NButton
                  v-if="auth.isAdmin || auth.isManager"
                  type="primary"
                  :loading="analyzingSession"
                  @click="analyzeSession"
                >
                  分析此会话并生成 SOP
                </NButton>
                <NButton
                  v-if="auth.isAdmin || auth.isManager"
                  :loading="generatingSop"
                  @click="generateSop"
                >
                  从此会话生成 SOP（旧版）
                </NButton>
              </NSpace>

              <NScrollbar style="max-height: calc(100vh - 360px)">
                <NEmpty
                  v-if="selectedSession.frames.length === 0"
                  description="该会话暂无帧数据"
                />
                <NTimeline v-else>
                  <NTimelineItem
                    v-for="frame in pagedFrames"
                    :key="frame.id"
                    :time="formatDate(frame.recorded_at)"
                  >
                    <template #header>
                      <NSpace align="center" :size="8">
                        <span>{{ frame.application || '未知应用' }}</span>
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
                          <NSpace v-if="frame.group_indices && frame.group_indices.length > 0" :size="4" align="center">
                            <span class="meta-label">所属分组</span>
                            <NTag
                              v-for="gi in frame.group_indices"
                              :key="gi"
                              size="small"
                              type="info"
                            >Group {{ gi }}</NTag>
                          </NSpace>
                          <span v-if="!frame.group_indices || frame.group_indices.length === 0" class="meta-empty">
                            尚未参与分组（session 未分析）
                          </span>

                          <div v-if="frame.sop_steps && frame.sop_steps.length > 0">
                            <span class="meta-label">对应 SOP 步骤</span>
                            <div class="sop-step-list">
                              <div
                                v-for="(step, sidx) in frame.sop_steps"
                                :key="sidx"
                                class="sop-step-item"
                                @click="router.push(`/sops/${step.sop_id}`)"
                              >
                                <NTag size="small" type="success">
                                  SOP#{{ step.sop_id }} · 步骤 {{ step.step_order }}
                                </NTag>
                                <span class="sop-step-title">{{ step.title }}</span>
                                <span v-if="step.application" class="sop-step-app">
                                  [{{ step.application }}]
                                </span>
                              </div>
                            </div>
                          </div>
                          <span v-else-if="frame.group_indices && frame.group_indices.length > 0" class="meta-empty">
                            未被任何 SOP 步骤引用（可能是过渡帧）
                          </span>

                          <NSpace :size="12" style="color: #999; font-size: 12px;">
                            <span>光标: ({{ frame.cursor_x ?? '-' }}, {{ frame.cursor_y ?? '-' }})</span>
                            <span v-if="frame.focus_rect && frame.focus_rect.length === 4">
                              焦点框: [{{ frame.focus_rect.join(', ') }}]
                            </span>
                          </NSpace>
                        </NSpace>
                      </div>
                    </div>
                  </NTimelineItem>
                </NTimeline>
                <NSpace v-if="hasMoreFrames" justify="center" style="padding: 16px 0;">
                  <NButton size="small" @click="framesPage++">
                    加载更多（已显示 {{ pagedFrames.length }} / {{ totalFrames }}）
                  </NButton>
                </NSpace>
              </NScrollbar>
            </template>
          </NSpin>
        </NCard>
      </NGi>
    </NGrid>

    <NModal
      :show="modalFrame !== null"
      preset="card"
      style="width: 90vw; max-width: 1600px;"
      :on-update:show="(v: boolean) => { if (!v) modalFrame = null }"
    >
      <template v-if="modalFrame">
        <div style="display: flex; gap: 16px;">
          <div style="flex: 0 0 auto;">
            <FrameImage :frame="modalFrame" max-width="80vw" :clickable="false" eager />
          </div>
          <div style="flex: 1 1 auto; overflow-y: auto; max-height: 85vh;">
            <h3>Frame #{{ modalFrame.frame_index }}</h3>
            <p v-if="modalFrame.group_indices && modalFrame.group_indices.length > 0">
              所属分组: Group {{ modalFrame.group_indices.join(', Group ') }}
            </p>
            <p>光标: ({{ modalFrame.cursor_x ?? '-' }}, {{ modalFrame.cursor_y ?? '-' }})</p>
            <div v-if="modalFrame.sop_steps && modalFrame.sop_steps.length > 0">
              <h4>对应 SOP 步骤</h4>
              <ul>
                <li v-for="(step, sidx) in modalFrame.sop_steps" :key="sidx">
                  <a @click.prevent="router.push(`/sops/${step.sop_id}`)" style="cursor: pointer; color: #18a058;">
                    SOP#{{ step.sop_id }} · 步骤 {{ step.step_order }}: {{ step.title }}
                  </a>
                  <span v-if="step.application" style="color: #999; margin-left: 6px;">[{{ step.application }}]</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.recording-page {
  padding: 0;
}

.session-card {
  cursor: pointer;
  transition: border-color 0.2s;
}

.session-card-active {
  border-color: var(--n-color-target);
  box-shadow: 0 0 0 1px var(--primary-color, #18a058);
}

.session-id {
  font-weight: 600;
  font-size: 13px;
}

.session-time {
  font-size: 12px;
  color: #999;
}

.meta-label {
  font-size: 12px;
  color: #666;
  font-weight: 500;
  white-space: nowrap;
  margin-right: 4px;
}

.meta-empty {
  font-size: 12px;
  color: #aaa;
  font-style: italic;
}

.sop-step-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 4px;
}

.sop-step-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
}

.sop-step-item:hover {
  background: #f0f9f4;
}

.sop-step-title {
  font-size: 13px;
  color: #333;
}

.sop-step-app {
  font-size: 12px;
  color: #999;
}

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
</style>
