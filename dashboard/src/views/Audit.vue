<script setup lang="ts">
import { ref, h } from 'vue'
import {
  NCard, NInput, NButton, NDataTable, NSpace, NDatePicker,
  NSlider, NH2, NText, NSpin, NTag, NGrid, NGi,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { statsApi, type SearchFrame } from '@/api/stats'

const loading = ref(false)
const keyword = ref('')
const employeeId = ref('')
const application = ref('')
const dateRange = ref<[number, number] | null>(null)
const minConfidence = ref(0)
const frames = ref<SearchFrame[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)

const columns: DataTableColumns<SearchFrame> = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '员工 ID', key: 'employee_id', width: 90 },
  { title: '会话 ID', key: 'session_id', width: 140, ellipsis: { tooltip: true } },
  { title: '帧序号', key: 'frame_index', width: 70 },
  { title: '应用', key: 'application', width: 120 },
  { title: '窗口标题', key: 'window_title', width: 160, ellipsis: { tooltip: true } },
  { title: '用户操作', key: 'user_action', width: 200, ellipsis: { tooltip: true } },
  {
    title: '置信度',
    key: 'confidence',
    width: 90,
    render(row) {
      const conf = row.confidence
      const type = conf >= 0.9 ? 'success' : conf >= 0.7 ? 'warning' : 'error'
      return h(NTag, { size: 'small', type, bordered: false }, () => conf.toFixed(2))
    },
  },
  { title: '记录时间', key: 'recorded_at', width: 180 },
]

const expandColumns: DataTableColumns<SearchFrame> = [
  { title: '文本内容', key: 'text_content' },
]

async function doSearch() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    }
    if (keyword.value) params.keyword = keyword.value
    if (employeeId.value) params.employee_id = employeeId.value
    if (application.value) params.application = application.value
    if (minConfidence.value > 0) params.min_confidence = minConfidence.value / 100
    if (dateRange.value) {
      params.date_from = new Date(dateRange.value[0]).toISOString().slice(0, 10)
      params.date_to = new Date(dateRange.value[1]).toISOString().slice(0, 10)
    }
    const { data } = await statsApi.searchFrames(params as Record<string, string>)
    frames.value = data.frames
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function handlePageChange(p: number) {
  page.value = p
  doSearch()
}

function handlePageSizeChange(ps: number) {
  pageSize.value = ps
  page.value = 1
  doSearch()
}

async function exportCsv() {
  const params: Record<string, string> = {}
  if (employeeId.value) params.employee_id = employeeId.value
  if (dateRange.value) {
    params.date_from = new Date(dateRange.value[0]).toISOString().slice(0, 10)
    params.date_to = new Date(dateRange.value[1]).toISOString().slice(0, 10)
  }
  const { data } = await statsApi.exportCsv(params)
  const blob = data as Blob
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'frames_export.csv'
  a.click()
  URL.revokeObjectURL(url)
}

function renderExpand(row: SearchFrame) {
  return h('div', { style: 'padding: 8px 16px; color: #666;' }, [
    h('div', {}, [
      h('strong', {}, '文本内容: '),
      h('span', {}, row.text_content || '(无)'),
    ]),
    h('div', { style: 'margin-top: 4px;' }, [
      h('strong', {}, '窗口标题: '),
      h('span', {}, row.window_title || '(无)'),
    ]),
  ])
}
</script>

<template>
  <div>
    <n-h2 style="margin: 0 0 16px;">
      <n-text>审计查询</n-text>
    </n-h2>

    <!-- Search Form -->
    <n-card style="margin-bottom: 16px;">
      <n-grid :cols="6" :x-gap="12" :y-gap="12">
        <n-gi :span="2">
          <n-input v-model:value="keyword" placeholder="关键词搜索 (操作/文本)" clearable />
        </n-gi>
        <n-gi>
          <n-input v-model:value="employeeId" placeholder="员工 ID" clearable />
        </n-gi>
        <n-gi>
          <n-input v-model:value="application" placeholder="应用名称" clearable />
        </n-gi>
        <n-gi :span="2">
          <n-date-picker v-model:value="dateRange" type="daterange" clearable style="width: 100%;" />
        </n-gi>
      </n-grid>
      <div style="margin-top: 12px; display: flex; align-items: center; gap: 16px;">
        <span style="white-space: nowrap; color: #666;">最低置信度: {{ minConfidence }}%</span>
        <n-slider v-model:value="minConfidence" :min="0" :max="100" :step="5" style="flex: 1; max-width: 300px;" />
        <n-space>
          <n-button type="primary" @click="doSearch" :loading="loading">搜索</n-button>
          <n-button @click="exportCsv">导出 CSV</n-button>
        </n-space>
      </div>
    </n-card>

    <!-- Results -->
    <n-card>
      <n-spin :show="loading">
        <n-data-table
          :columns="columns"
          :data="frames"
          :row-key="(row: SearchFrame) => row.id"
          :bordered="false"
          size="small"
          :max-height="500"
          :pagination="{
            page: page,
            pageSize: pageSize,
            itemCount: total,
            pageSizes: [20, 50, 100],
            showSizePicker: true,
            onChange: handlePageChange,
            onUpdatePageSize: handlePageSizeChange,
            prefix: () => `共 ${total} 条记录`,
          }"
          :render-expand="renderExpand"
          :row-props="() => ({ style: 'cursor: pointer;' })"
        />
      </n-spin>
    </n-card>
  </div>
</template>
