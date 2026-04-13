<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { sopsApi, type SopInfo } from '@/api/sops'
import {
  NCard, NTabs, NTabPane, NButton, NDataTable, NTag, NSpace,
  NModal, NInput, NSpin, NEmpty, useMessage, useDialog,
  type DataTableColumns
} from 'naive-ui'

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const sops = ref<SopInfo[]>([])
const total = ref(0)
const activeTab = ref('all')
const showCreateModal = ref(false)
const createLoading = ref(false)
const newTitle = ref('')
const newDescription = ref('')

const canCreate = computed(() => auth.isAdmin || auth.isManager)

const statusMap: Record<string, string> = {
  all: '',
  draft: 'draft',
  in_review: 'in_review',
  published: 'published',
}

const statusLabel: Record<string, string> = {
  draft: '草稿',
  in_review: '审核中',
  published: '已发布',
}

const statusType: Record<string, 'default' | 'warning' | 'success'> = {
  draft: 'default',
  in_review: 'warning',
  published: 'success',
}

function formatDate(isoStr: string): string {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleDateString('zh-CN') + ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const columns: DataTableColumns<SopInfo> = [
  {
    title: '标题',
    key: 'title',
    ellipsis: { tooltip: true },
    render(row) {
      return row.title
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render(row) {
      return h(NTag, { type: statusType[row.status] || 'default', size: 'small' }, {
        default: () => statusLabel[row.status] || row.status,
      })
    },
  },
  {
    title: '创建者',
    key: 'created_by',
    width: 120,
  },
  {
    title: '更新时间',
    key: 'updated_at',
    width: 170,
    render(row) {
      return formatDate(row.updated_at)
    },
  },
  {
    title: '步骤数',
    key: 'step_count',
    width: 80,
    render(row) {
      return row.step_count ?? 0
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render(row) {
      const btns = [
        h(NButton, { size: 'small', onClick: () => router.push(`/sops/${row.id}`) }, { default: () => '编辑' }),
      ]
      if (canCreate.value) {
        btns.push(
          h(NButton, { size: 'small', type: 'error', onClick: () => confirmDelete(row) }, { default: () => '删除' }),
        )
      }
      return h(NSpace, { size: 8 }, { default: () => btns })
    },
  },
]

import { h } from 'vue'

async function fetchSops() {
  loading.value = true
  try {
    const params: { status?: string; limit: number; offset: number } = { limit: 50, offset: 0 }
    const statusValue = statusMap[activeTab.value]
    if (statusValue) {
      params.status = statusValue
    }
    const { data } = await sopsApi.list(params)
    sops.value = data.sops
    total.value = data.total
  } catch {
    message.error('加载 SOP 列表失败')
  } finally {
    loading.value = false
  }
}

function onTabChange(tab: string) {
  activeTab.value = tab
  fetchSops()
}

async function handleCreate() {
  if (!newTitle.value.trim()) {
    message.warning('请输入标题')
    return
  }
  createLoading.value = true
  try {
    const { data } = await sopsApi.create({
      title: newTitle.value.trim(),
      description: newDescription.value.trim() || undefined,
    })
    message.success('创建成功')
    showCreateModal.value = false
    newTitle.value = ''
    newDescription.value = ''
    router.push(`/sops/${data.id}`)
  } catch {
    message.error('创建失败')
  } finally {
    createLoading.value = false
  }
}

function confirmDelete(sop: SopInfo) {
  dialog.warning({
    title: '确认删除',
    content: `确定要删除「${sop.title}」吗？此操作不可撤销。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await sopsApi.delete(sop.id)
        message.success('已删除')
        fetchSops()
      } catch {
        message.error('删除失败')
      }
    },
  })
}

function onRowClick(row: SopInfo) {
  router.push(`/sops/${row.id}`)
}

onMounted(fetchSops)
</script>

<template>
  <div class="sop-list-page">
    <NCard>
      <template #header>
        <NSpace justify="space-between" align="center" style="width: 100%">
          <span>SOP 管理</span>
          <NButton v-if="canCreate" type="primary" @click="showCreateModal = true">
            新建 SOP
          </NButton>
        </NSpace>
      </template>

      <NTabs :value="activeTab" type="line" @update:value="onTabChange">
        <NTabPane name="all" tab="全部" />
        <NTabPane name="draft" tab="草稿" />
        <NTabPane name="in_review" tab="审核中" />
        <NTabPane name="published" tab="已发布" />
      </NTabs>

      <NSpin :show="loading" style="min-height: 200px">
        <NEmpty v-if="sops.length === 0 && !loading" description="暂无 SOP 数据" style="margin-top: 60px" />
        <NDataTable
          v-else
          :columns="columns"
          :data="sops"
          :row-props="(row: SopInfo) => ({ style: 'cursor: pointer', onClick: () => onRowClick(row) })"
          :bordered="false"
          striped
        />
      </NSpin>
    </NCard>

    <NModal
      v-model:show="showCreateModal"
      preset="dialog"
      title="新建 SOP"
      positive-text="创建"
      negative-text="取消"
      :loading="createLoading"
      @positive-click="handleCreate"
    >
      <NSpace vertical :size="12" style="margin-top: 16px">
        <NInput v-model:value="newTitle" placeholder="SOP 标题" />
        <NInput v-model:value="newDescription" type="textarea" placeholder="描述（可选）" :rows="3" />
      </NSpace>
    </NModal>
  </div>
</template>

<style scoped>
.sop-list-page {
  padding: 0;
}
</style>
