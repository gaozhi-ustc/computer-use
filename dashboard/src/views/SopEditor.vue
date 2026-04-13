<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { sopsApi, type SopDetail, type StepInfo } from '@/api/sops'
import {
  NCard, NButton, NInput, NTag, NSpace, NSelect, NSlider,
  NProgress, NModal, NSpin, NEmpty, NDivider, NDropdown,
  NPopconfirm, useMessage
} from 'naive-ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const sopId = computed(() => Number(route.params.id))
const loading = ref(false)
const sop = ref<SopDetail | null>(null)
const selectedStepId = ref<number | null>(null)
const editTitle = ref('')
const savingTitle = ref(false)
const addStepLoading = ref(false)
const showAddStepModal = ref(false)
const newStepTitle = ref('')

// Drag state
const dragIndex = ref<number | null>(null)
const dragOverIndex = ref<number | null>(null)

// Step editing
const stepForm = ref<{
  title: string
  application: string
  description: string
  action_type: string
  confidence: number
}>({
  title: '',
  application: '',
  description: '',
  action_type: 'click',
  confidence: 0.8,
})
const autoSaveTimer = ref<ReturnType<typeof setTimeout> | null>(null)

const selectedStep = computed(() => {
  if (!sop.value || selectedStepId.value === null) return null
  return sop.value.steps.find(s => s.id === selectedStepId.value) || null
})

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

const actionTypeOptions = [
  { label: '点击 (click)', value: 'click' },
  { label: '输入 (type)', value: 'type' },
  { label: '按键 (key)', value: 'key' },
  { label: '滚动 (scroll)', value: 'scroll' },
  { label: '等待 (wait)', value: 'wait' },
]

const exportOptions = [
  { label: '导出 Markdown', key: 'md' },
  { label: '导出 JSON', key: 'json' },
]

async function fetchSop() {
  loading.value = true
  try {
    const { data } = await sopsApi.detail(sopId.value)
    sop.value = data
    editTitle.value = data.title
    // If previously selected step is gone, clear selection
    if (selectedStepId.value !== null) {
      const exists = data.steps.some(s => s.id === selectedStepId.value)
      if (!exists) selectedStepId.value = null
    }
  } catch {
    message.error('加载 SOP 详情失败')
  } finally {
    loading.value = false
  }
}

async function saveTitle() {
  if (!sop.value || editTitle.value.trim() === sop.value.title) return
  savingTitle.value = true
  try {
    await sopsApi.update(sopId.value, { title: editTitle.value.trim() })
    sop.value.title = editTitle.value.trim()
    message.success('标题已保存')
  } catch {
    message.error('保存标题失败')
  } finally {
    savingTitle.value = false
  }
}

async function changeStatus(newStatus: string) {
  try {
    await sopsApi.updateStatus(sopId.value, { status: newStatus })
    message.success('状态已更新')
    await fetchSop()
  } catch {
    message.error('更新状态失败')
  }
}

function handleExport(key: string) {
  if (key === 'md') exportMarkdown()
  else if (key === 'json') exportJson()
}

async function exportMarkdown() {
  try {
    const { data } = await sopsApi.exportMd(sopId.value)
    const blob = new Blob([data], { type: 'text/markdown;charset=utf-8' })
    downloadBlob(blob, `${sop.value?.title || 'sop'}.md`)
    message.success('已导出 Markdown')
  } catch {
    message.error('导出失败')
  }
}

async function exportJson() {
  try {
    const { data } = await sopsApi.exportJson(sopId.value)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
    downloadBlob(blob, `${sop.value?.title || 'sop'}.json`)
    message.success('已导出 JSON')
  } catch {
    message.error('导出失败')
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function selectStep(step: StepInfo) {
  selectedStepId.value = step.id
  stepForm.value = {
    title: step.title,
    application: step.application,
    description: step.description,
    action_type: step.action_type || 'click',
    confidence: step.confidence,
  }
}

// Auto-save step edits with debounce
watch(
  () => ({ ...stepForm.value }),
  () => {
    if (selectedStepId.value === null || !sop.value) return
    if (autoSaveTimer.value) clearTimeout(autoSaveTimer.value)
    autoSaveTimer.value = setTimeout(() => {
      saveStep()
    }, 500)
  },
  { deep: true },
)

async function saveStep() {
  if (selectedStepId.value === null) return
  try {
    await sopsApi.updateStep(sopId.value, selectedStepId.value, {
      title: stepForm.value.title,
      application: stepForm.value.application,
      description: stepForm.value.description,
      action_type: stepForm.value.action_type,
      confidence: stepForm.value.confidence,
    } as Partial<StepInfo>)
    // Update local data
    if (sop.value) {
      const step = sop.value.steps.find(s => s.id === selectedStepId.value)
      if (step) {
        step.title = stepForm.value.title
        step.application = stepForm.value.application
        step.description = stepForm.value.description
        step.action_type = stepForm.value.action_type
        step.confidence = stepForm.value.confidence
      }
    }
  } catch {
    // Silent fail for auto-save; user can retry
  }
}

async function handleAddStep() {
  if (!newStepTitle.value.trim()) {
    message.warning('请输入步骤标题')
    return
  }
  addStepLoading.value = true
  try {
    const stepOrder = sop.value ? sop.value.steps.length + 1 : 1
    await sopsApi.addStep(sopId.value, {
      title: newStepTitle.value.trim(),
      step_order: stepOrder,
    })
    message.success('步骤已添加')
    showAddStepModal.value = false
    newStepTitle.value = ''
    await fetchSop()
  } catch {
    message.error('添加步骤失败')
  } finally {
    addStepLoading.value = false
  }
}

async function deleteStep() {
  if (selectedStepId.value === null) return
  try {
    await sopsApi.deleteStep(sopId.value, selectedStepId.value)
    message.success('步骤已删除')
    selectedStepId.value = null
    await fetchSop()
  } catch {
    message.error('删除步骤失败')
  }
}

// Drag-and-drop
function onDragStart(index: number, event: DragEvent) {
  dragIndex.value = index
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(index))
  }
}

function onDragOver(index: number, event: DragEvent) {
  event.preventDefault()
  dragOverIndex.value = index
}

function onDragLeave() {
  dragOverIndex.value = null
}

async function onDrop(targetIndex: number) {
  if (dragIndex.value === null || dragIndex.value === targetIndex || !sop.value) {
    dragIndex.value = null
    dragOverIndex.value = null
    return
  }

  const steps = [...sop.value.steps]
  const [moved] = steps.splice(dragIndex.value, 1)
  steps.splice(targetIndex, 0, moved)
  sop.value.steps = steps

  dragIndex.value = null
  dragOverIndex.value = null

  // Persist new order
  try {
    const stepIds = steps.map(s => s.id)
    await sopsApi.reorderSteps(sopId.value, stepIds)
    message.success('步骤顺序已更新')
    await nextTick()
  } catch {
    message.error('更新顺序失败')
    await fetchSop()
  }
}

function onDragEnd() {
  dragIndex.value = null
  dragOverIndex.value = null
}

function goBack() {
  router.push('/sops')
}

function getConfidenceStatus(c: number): 'success' | 'warning' | 'error' {
  if (c >= 0.8) return 'success'
  if (c >= 0.5) return 'warning'
  return 'error'
}

onMounted(fetchSop)
</script>

<template>
  <div class="sop-editor-page">
    <NSpin :show="loading">
      <template v-if="sop">
        <!-- Top bar -->
        <NCard size="small" style="margin-bottom: 16px">
          <NSpace align="center" justify="space-between" style="width: 100%">
            <NSpace align="center" :size="12">
              <NButton text @click="goBack">&larr; 返回列表</NButton>
              <NDivider vertical />
              <NInput
                v-model:value="editTitle"
                style="width: 300px; font-weight: 600"
                @blur="saveTitle"
                @keyup.enter="($event.target as HTMLInputElement)?.blur()"
              />
              <NTag :type="statusType[sop.status] || 'default'" size="small">
                {{ statusLabel[sop.status] || sop.status }}
              </NTag>
            </NSpace>

            <NSpace :size="8">
              <NButton
                v-if="sop.status === 'draft'"
                type="warning"
                size="small"
                @click="changeStatus('in_review')"
              >
                提交审核
              </NButton>
              <NButton
                v-if="sop.status === 'in_review'"
                type="success"
                size="small"
                @click="changeStatus('published')"
              >
                发布
              </NButton>
              <NButton
                v-if="sop.status === 'in_review'"
                size="small"
                @click="changeStatus('draft')"
              >
                打回
              </NButton>
              <NDropdown :options="exportOptions" @select="handleExport">
                <NButton size="small">导出</NButton>
              </NDropdown>
            </NSpace>
          </NSpace>
        </NCard>

        <!-- Main content: left (steps) + right (editor) -->
        <div class="editor-layout">
          <!-- Left panel: step list -->
          <div class="step-list-panel">
            <NCard title="步骤列表" size="small">
              <template #header-extra>
                <NButton size="small" type="primary" @click="showAddStepModal = true">
                  添加步骤
                </NButton>
              </template>

              <NEmpty v-if="sop.steps.length === 0" description="暂无步骤，请添加" />

              <div v-else class="step-cards">
                <div
                  v-for="(step, index) in sop.steps"
                  :key="step.id"
                  class="step-card"
                  :class="{
                    'step-card-active': selectedStepId === step.id,
                    'step-card-drag-over': dragOverIndex === index,
                  }"
                  draggable="true"
                  @dragstart="onDragStart(index, $event)"
                  @dragover="onDragOver(index, $event)"
                  @dragleave="onDragLeave"
                  @drop="onDrop(index)"
                  @dragend="onDragEnd"
                  @click="selectStep(step)"
                >
                  <NSpace align="center" :size="8" style="width: 100%">
                    <span class="step-number">{{ index + 1 }}</span>
                    <div style="flex: 1; min-width: 0">
                      <div class="step-title">{{ step.title }}</div>
                      <NSpace :size="4" style="margin-top: 4px">
                        <NTag v-if="step.application" size="tiny">{{ step.application }}</NTag>
                      </NSpace>
                      <NProgress
                        type="line"
                        :percentage="Math.round(step.confidence * 100)"
                        :status="getConfidenceStatus(step.confidence)"
                        :height="4"
                        :show-indicator="false"
                        style="margin-top: 4px"
                      />
                    </div>
                  </NSpace>
                </div>
              </div>
            </NCard>
          </div>

          <!-- Right panel: step editor -->
          <div class="step-editor-panel">
            <NCard v-if="selectedStep" title="步骤详情" size="small">
              <NSpace vertical :size="16">
                <div>
                  <label class="field-label">标题</label>
                  <NInput v-model:value="stepForm.title" placeholder="步骤标题" />
                </div>
                <div>
                  <label class="field-label">应用程序</label>
                  <NInput v-model:value="stepForm.application" placeholder="应用程序名称" />
                </div>
                <div>
                  <label class="field-label">描述</label>
                  <NInput
                    v-model:value="stepForm.description"
                    type="textarea"
                    placeholder="步骤描述"
                    :rows="4"
                  />
                </div>
                <div>
                  <label class="field-label">动作类型</label>
                  <NSelect
                    v-model:value="stepForm.action_type"
                    :options="actionTypeOptions"
                    placeholder="选择动作类型"
                  />
                </div>
                <div>
                  <label class="field-label">置信度: {{ Math.round(stepForm.confidence * 100) }}%</label>
                  <NSlider
                    v-model:value="stepForm.confidence"
                    :min="0"
                    :max="1"
                    :step="0.01"
                  />
                </div>

                <NDivider />

                <NPopconfirm @positive-click="deleteStep">
                  <template #trigger>
                    <NButton type="error" block>删除此步骤</NButton>
                  </template>
                  确定要删除此步骤吗？
                </NPopconfirm>
              </NSpace>
            </NCard>

            <NCard v-else>
              <NEmpty description="请从左侧选择一个步骤进行编辑" style="margin-top: 80px" />
            </NCard>
          </div>
        </div>
      </template>

      <NEmpty v-if="!sop && !loading" description="SOP 不存在或加载失败" />
    </NSpin>

    <!-- Add step modal -->
    <NModal
      v-model:show="showAddStepModal"
      preset="dialog"
      title="添加步骤"
      positive-text="添加"
      negative-text="取消"
      :loading="addStepLoading"
      @positive-click="handleAddStep"
    >
      <div style="margin-top: 16px">
        <NInput v-model:value="newStepTitle" placeholder="步骤标题" />
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.sop-editor-page {
  padding: 0;
}

.editor-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.step-list-panel {
  width: 40%;
  min-width: 300px;
  flex-shrink: 0;
}

.step-editor-panel {
  flex: 1;
  min-width: 0;
}

.step-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.step-card {
  padding: 12px;
  border: 1px solid #e0e0e6;
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  user-select: none;
}

.step-card:hover {
  border-color: #18a058;
}

.step-card-active {
  border-color: #18a058;
  box-shadow: 0 0 0 1px #18a058;
}

.step-card-drag-over {
  border-color: #2080f0;
  background-color: #f5f8ff;
}

.step-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #18a058;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.step-title {
  font-weight: 600;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.field-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #666;
  margin-bottom: 4px;
}
</style>
