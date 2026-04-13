<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { sessionsApi, type SessionInfo, type SessionDetail } from '@/api/sessions'
import { sopsApi } from '@/api/sops'
import {
  NCard, NSpace, NInput, NDatePicker, NTag, NBadge, NEmpty,
  NSpin, NTimeline, NTimelineItem, NProgress, NGrid, NGi,
  NScrollbar, NDescriptions, NDescriptionsItem, NCollapse, NCollapseItem,
  NButton, useMessage
} from 'naive-ui'

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

function getConfidenceType(confidence: number): 'success' | 'warning' | 'error' {
  if (confidence >= 0.8) return 'success'
  if (confidence >= 0.5) return 'warning'
  return 'error'
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
                  {{ selectedSession.frames.length }} 帧已加载
                </NDescriptionsItem>
              </NDescriptions>

              <NSpace style="margin-bottom: 16px">
                <NButton
                  v-if="auth.isAdmin || auth.isManager"
                  type="primary"
                  :loading="generatingSop"
                  @click="generateSop"
                >
                  从此会话生成 SOP
                </NButton>
              </NSpace>

              <NScrollbar style="max-height: calc(100vh - 360px)">
                <NEmpty
                  v-if="selectedSession.frames.length === 0"
                  description="该会话暂无帧数据"
                />
                <NTimeline v-else>
                  <NTimelineItem
                    v-for="frame in selectedSession.frames"
                    :key="frame.id"
                    :title="frame.application || '未知应用'"
                    :time="formatDate(frame.recorded_at)"
                  >
                    <NSpace vertical :size="8">
                      <span v-if="frame.user_action" class="frame-action">
                        {{ frame.user_action }}
                      </span>
                      <NSpace align="center" :size="8">
                        <span class="confidence-label">置信度</span>
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
                          <NDescriptions
                            label-placement="left"
                            :column="1"
                            size="small"
                          >
                            <NDescriptionsItem label="窗口标题">
                              {{ frame.window_title || '-' }}
                            </NDescriptionsItem>
                            <NDescriptionsItem label="文本内容">
                              {{ frame.text_content || '-' }}
                            </NDescriptionsItem>
                            <NDescriptionsItem label="鼠标位置">
                              {{ frame.mouse_position?.join(', ') || '-' }}
                            </NDescriptionsItem>
                            <NDescriptionsItem label="UI元素">
                              <NSpace v-if="frame.ui_elements && frame.ui_elements.length > 0" vertical :size="4">
                                <NTag
                                  v-for="(el, idx) in frame.ui_elements"
                                  :key="idx"
                                  size="small"
                                >
                                  {{ el.name }} ({{ el.element_type }}) [{{ el.coordinates.join(', ') }}]
                                </NTag>
                              </NSpace>
                              <span v-else>-</span>
                            </NDescriptionsItem>
                          </NDescriptions>
                        </NCollapseItem>
                      </NCollapse>
                    </NSpace>
                  </NTimelineItem>
                </NTimeline>
              </NScrollbar>
            </template>
          </NSpin>
        </NCard>
      </NGi>
    </NGrid>
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

.frame-action {
  font-size: 14px;
  color: #333;
}

.confidence-label {
  font-size: 12px;
  color: #666;
  white-space: nowrap;
}
</style>
