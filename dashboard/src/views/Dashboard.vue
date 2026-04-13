<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NGrid, NGi, NCard, NStatistic, NDataTable, NTag, NSpin, NProgress, NH2, NText } from 'naive-ui'
import { statsApi, type DashboardSummary, type AppUsage } from '@/api/stats'
import type { SessionInfo } from '@/api/sessions'

const loading = ref(true)
const summary = ref<DashboardSummary>({
  today_frames: 0,
  today_sessions: 0,
  draft_sops: 0,
  published_sops: 0,
  total_employees: 0,
})
const appUsage = ref<AppUsage[]>([])
const recentSessions = ref<SessionInfo[]>([])

const sessionColumns = [
  { title: '会话 ID', key: 'session_id', width: 180, ellipsis: { tooltip: true } },
  { title: '员工 ID', key: 'employee_id', width: 100 },
  { title: '帧数', key: 'frame_count', width: 80 },
  { title: '开始时间', key: 'first_frame_at', width: 180 },
  { title: '结束时间', key: 'last_frame_at', width: 180 },
]

function appMaxCount(): number {
  if (appUsage.value.length === 0) return 1
  return appUsage.value[0].frame_count
}

onMounted(async () => {
  try {
    const [summaryRes, sessionsRes, statsRes] = await Promise.all([
      statsApi.dashboardSummary(),
      statsApi.recentSessions(),
      statsApi.frameStats(),
    ])
    summary.value = summaryRes.data
    recentSessions.value = sessionsRes.data as SessionInfo[]
    appUsage.value = statsRes.data.app_usage.slice(0, 10)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <n-spin :show="loading">
    <div style="min-height: 400px;">
      <n-h2 style="margin: 0 0 16px;">
        <n-text>概览</n-text>
      </n-h2>

      <!-- KPI Cards -->
      <n-grid :cols="5" :x-gap="16" :y-gap="16" style="margin-bottom: 24px;">
        <n-gi>
          <n-card>
            <n-statistic label="今日采集帧数" :value="summary.today_frames" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card>
            <n-statistic label="今日活跃会话" :value="summary.today_sessions" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card>
            <n-statistic label="草稿 SOP" :value="summary.draft_sops" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card>
            <n-statistic label="已发布 SOP" :value="summary.published_sops" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card>
            <n-statistic label="员工数" :value="summary.total_employees" />
          </n-card>
        </n-gi>
      </n-grid>

      <n-grid :cols="2" :x-gap="16" :y-gap="16">
        <!-- App Usage Distribution -->
        <n-gi>
          <n-card title="应用使用分布 (Top 10)">
            <div v-if="appUsage.length === 0" style="color: #999; text-align: center; padding: 24px;">
              暂无数据
            </div>
            <div v-for="item in appUsage" :key="item.application" style="margin-bottom: 12px;">
              <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                <span>{{ item.application || '(未知)' }}</span>
                <span style="color: #999;">{{ item.frame_count }} 帧</span>
              </div>
              <n-progress
                type="line"
                :percentage="Math.round((item.frame_count / appMaxCount()) * 100)"
                :show-indicator="false"
                :height="12"
              />
            </div>
          </n-card>
        </n-gi>

        <!-- Recent Sessions -->
        <n-gi>
          <n-card title="最近会话">
            <n-data-table
              :columns="sessionColumns"
              :data="recentSessions"
              :bordered="false"
              size="small"
              :max-height="360"
            />
          </n-card>
        </n-gi>
      </n-grid>
    </div>
  </n-spin>
</template>
