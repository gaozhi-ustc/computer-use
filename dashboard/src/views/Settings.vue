<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import {
  NCard, NDescriptions, NDescriptionsItem, NH2, NText, NSpin, NTag,
  NGrid, NGi, NStatistic, NAlert, NSpace,
} from 'naive-ui'
import client from '@/api/client'
import { framesApi, type QueueStats } from '@/api/frames'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const isAdmin = computed(() => auth.isAdmin)

const loading = ref(true)
const healthInfo = ref<{
  status: string
  db_path: string
  auth_enabled: boolean
}>({
  status: '',
  db_path: '',
  auth_enabled: false,
})

const queueStats = ref<QueueStats | null>(null)
const queueErr = ref<string>('')
let queueTimer: ReturnType<typeof setInterval> | null = null

async function fetchQueue() {
  try {
    const { data } = await framesApi.queueStatus()
    queueStats.value = data
    queueErr.value = ''
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    queueErr.value = `获取分析队列状态失败: ${msg}`
  }
}

onMounted(async () => {
  try {
    const { data } = await client.get('/health')
    healthInfo.value = data
  } finally {
    loading.value = false
  }

  if (isAdmin.value) {
    fetchQueue()
    queueTimer = setInterval(fetchQueue, 5000)
  }
})

onUnmounted(() => {
  if (queueTimer) {
    clearInterval(queueTimer)
    queueTimer = null
  }
})
</script>

<template>
  <div>
    <n-h2 style="margin: 0 0 16px;">
      <n-text>系统设置</n-text>
    </n-h2>

    <n-space vertical :size="16">
      <n-card title="系统信息">
        <n-spin :show="loading">
          <n-descriptions bordered :column="1" label-placement="left" size="medium">
            <n-descriptions-item label="服务状态">
              <n-tag :type="healthInfo.status === 'ok' ? 'success' : 'error'" size="small">
                {{ healthInfo.status || '--' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="数据库路径">
              {{ healthInfo.db_path || '--' }}
            </n-descriptions-item>
            <n-descriptions-item label="API 认证">
              <n-tag :type="healthInfo.auth_enabled ? 'success' : 'warning'" size="small">
                {{ healthInfo.auth_enabled ? '已启用' : '未启用' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="服务版本">
              0.1.0
            </n-descriptions-item>
            <n-descriptions-item label="前端版本">
              Dashboard Phase 5
            </n-descriptions-item>
          </n-descriptions>
        </n-spin>
      </n-card>

      <n-card v-if="isAdmin" title="分析队列">
        <n-alert v-if="queueErr" type="error" style="margin-bottom: 12px;">
          {{ queueErr }}
        </n-alert>
        <n-grid :cols="4" :x-gap="16">
          <n-gi>
            <n-statistic label="待分析" :value="queueStats?.pending ?? 0" />
          </n-gi>
          <n-gi>
            <n-statistic label="分析中" :value="queueStats?.running ?? 0" />
          </n-gi>
          <n-gi>
            <n-statistic label="已分析" :value="queueStats?.done ?? 0" />
          </n-gi>
          <n-gi>
            <n-statistic label="失败" :value="queueStats?.failed ?? 0" />
          </n-gi>
        </n-grid>
      </n-card>
    </n-space>
  </div>
</template>
