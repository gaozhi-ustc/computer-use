<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  NCard, NGrid, NGi, NDatePicker, NInput, NButton, NDataTable,
  NSpin, NProgress, NH2, NText, NSpace, NTag,
} from 'naive-ui'
import { statsApi, type FrameStats } from '@/api/stats'

const loading = ref(false)
const employeeId = ref('')
const dateRange = ref<[number, number] | null>(null)

const stats = ref<FrameStats>({
  app_usage: [],
  heatmap: [],
  daily: [],
})

function appMaxCount(): number {
  if (stats.value.app_usage.length === 0) return 1
  return stats.value.app_usage[0].frame_count
}

// Heatmap: build 7x24 grid from data
const weekdayLabels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const hours = Array.from({ length: 24 }, (_, i) => i)

const heatmapGrid = computed(() => {
  const grid: number[][] = Array.from({ length: 7 }, () => Array(24).fill(0))
  for (const cell of stats.value.heatmap) {
    if (cell.weekday >= 0 && cell.weekday < 7 && cell.hour >= 0 && cell.hour < 24) {
      grid[cell.weekday][cell.hour] = cell.count
    }
  }
  return grid
})

const heatmapMax = computed(() => {
  let mx = 1
  for (const cell of stats.value.heatmap) {
    if (cell.count > mx) mx = cell.count
  }
  return mx
})

function heatmapColor(count: number): string {
  if (count === 0) return '#f5f5f5'
  const intensity = Math.min(count / heatmapMax.value, 1)
  const r = Math.round(255 - intensity * 175)
  const g = Math.round(255 - intensity * 85)
  const b = Math.round(255 - intensity * 35)
  return `rgb(${r}, ${g}, ${b})`
}

const dailyColumns = [
  { title: '日期', key: 'date', width: 120 },
  { title: '帧数', key: 'frame_count', width: 100 },
  { title: '应用数', key: 'app_count', width: 100 },
  { title: '首次活跃', key: 'first_at', width: 200 },
  { title: '末次活跃', key: 'last_at', width: 200 },
]

async function loadStats() {
  loading.value = true
  try {
    const params: Record<string, string> = {}
    if (employeeId.value) params.employee_id = employeeId.value
    if (dateRange.value) {
      params.date_from = new Date(dateRange.value[0]).toISOString().slice(0, 10)
      params.date_to = new Date(dateRange.value[1]).toISOString().slice(0, 10)
    }
    const { data } = await statsApi.frameStats(params)
    stats.value = data
  } finally {
    loading.value = false
  }
}

onMounted(() => loadStats())
</script>

<template>
  <div>
    <n-h2 style="margin: 0 0 16px;">
      <n-text>效率分析</n-text>
    </n-h2>

    <!-- Filters -->
    <n-card style="margin-bottom: 16px;">
      <n-space>
        <n-input
          v-model:value="employeeId"
          placeholder="员工 ID"
          clearable
          style="width: 160px;"
        />
        <n-date-picker
          v-model:value="dateRange"
          type="daterange"
          clearable
          style="width: 300px;"
        />
        <n-button type="primary" @click="loadStats" :loading="loading">
          查询
        </n-button>
      </n-space>
    </n-card>

    <n-spin :show="loading">
      <n-grid :cols="2" :x-gap="16" :y-gap="16">
        <!-- App Usage -->
        <n-gi>
          <n-card title="应用使用分布">
            <div v-if="stats.app_usage.length === 0" style="color: #999; text-align: center; padding: 24px;">
              暂无数据
            </div>
            <div v-for="item in stats.app_usage.slice(0, 15)" :key="item.application ?? 'unknown'" style="margin-bottom: 10px;">
              <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                <span>{{ item.application || '(未知)' }}</span>
                <n-tag size="small" :bordered="false">{{ item.frame_count }} 帧</n-tag>
              </div>
              <n-progress
                type="line"
                :percentage="Math.round((item.frame_count / appMaxCount()) * 100)"
                :show-indicator="false"
                :height="10"
              />
            </div>
          </n-card>
        </n-gi>

        <!-- Heatmap -->
        <n-gi>
          <n-card title="活跃时段热力图">
            <div v-if="stats.heatmap.length === 0" style="color: #999; text-align: center; padding: 24px;">
              暂无数据
            </div>
            <div v-else style="overflow-x: auto;">
              <table style="border-collapse: collapse; width: 100%; font-size: 12px;">
                <thead>
                  <tr>
                    <th style="padding: 2px 4px; text-align: left; width: 40px;"></th>
                    <th
                      v-for="h in hours"
                      :key="h"
                      style="padding: 2px 1px; text-align: center; font-weight: normal; color: #999;"
                    >
                      {{ h }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(wd, wdIdx) in weekdayLabels" :key="wdIdx">
                    <td style="padding: 2px 4px; color: #666; white-space: nowrap;">{{ wd }}</td>
                    <td
                      v-for="h in hours"
                      :key="h"
                      :style="{
                        padding: '0',
                        textAlign: 'center',
                      }"
                    >
                      <div
                        :style="{
                          width: '100%',
                          height: '20px',
                          backgroundColor: heatmapColor(heatmapGrid[wdIdx][h]),
                          borderRadius: '2px',
                          margin: '1px',
                        }"
                        :title="`${wd} ${h}:00 - ${heatmapGrid[wdIdx][h]} 次`"
                      />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </n-card>
        </n-gi>
      </n-grid>

      <!-- Daily Activity Table -->
      <n-card title="每日活跃统计" style="margin-top: 16px;">
        <n-data-table
          :columns="dailyColumns"
          :data="stats.daily"
          :bordered="false"
          size="small"
          :max-height="400"
        />
      </n-card>
    </n-spin>
  </div>
</template>
