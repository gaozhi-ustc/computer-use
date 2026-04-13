<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NCard, NDescriptions, NDescriptionsItem, NH2, NText, NSpin, NTag } from 'naive-ui'
import client from '@/api/client'

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

onMounted(async () => {
  try {
    const { data } = await client.get('/health')
    healthInfo.value = data
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <n-h2 style="margin: 0 0 16px;">
      <n-text>系统设置</n-text>
    </n-h2>

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
  </div>
</template>
