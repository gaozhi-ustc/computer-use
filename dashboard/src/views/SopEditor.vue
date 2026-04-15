<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { sopsApi, type SopDetail, type StepInfo } from '@/api/sops'
import type { FrameInfo } from '@/api/sessions'
import SopStepCard from '@/components/SopStepCard.vue'
import SopFeedbackInput from '@/components/SopFeedbackInput.vue'
import SopRevisionNav from '@/components/SopRevisionNav.vue'
import FrameCarousel from '@/components/FrameCarousel.vue'
import {
  NCard, NSpace, NInput, NTag, NButton, NSpin, NEmpty,
  NScrollbar, NDivider, NDropdown, useMessage, NSkeleton,
} from 'naive-ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const sopId = Number(route.params.id)
const loading = ref(false)
const sop = ref<SopDetail | null>(null)
const selectedStepId = ref<number | null>(null)
const editTitle = ref('')
const savingTitle = ref(false)
const feedbackLoading = ref(false)

const currentRevision = ref(1)
const maxRevision = ref(1)
const viewingHistorical = ref(false)
const historicalSteps = ref<StepInfo[]>([])

let pollTimer: ReturnType<typeof setInterval> | null = null

const statusLabel: Record<string, string> = {
  draft: 'Draft', regenerating: 'Regenerating...', in_review: 'In Review', published: 'Published',
}
const statusType: Record<string, string> = {
  draft: 'default', regenerating: 'warning', in_review: 'info', published: 'success',
}

const displaySteps = computed(() => {
  if (viewingHistorical.value) return historicalSteps.value
  return sop.value?.steps || []
})

const selectedStep = computed(() =>
  displaySteps.value.find(s => s.id === selectedStepId.value) || null
)

const isReadonly = computed(() =>
  viewingHistorical.value ||
  sop.value?.status === 'published' ||
  sop.value?.status === 'regenerating'
)

const selectedStepFrames = computed<FrameInfo[]>(() => {
  if (!selectedStep.value) return []
  const ids = selectedStep.value.source_frame_ids || []
  return ids.map((id: number) => ({ id, frame_index: 0 } as unknown as FrameInfo))
})

const feedbackScope = ref('full')

async function fetchSop() {
  loading.value = true
  try {
    const { data } = await sopsApi.detail(sopId)
    sop.value = data
    editTitle.value = data.title
    currentRevision.value = (data as unknown as { revision?: number }).revision || 1
    maxRevision.value = currentRevision.value
    viewingHistorical.value = false

    if (data.status === 'regenerating') startPolling()
    else stopPolling()
  } catch {
    message.error('Failed to load SOP')
  } finally {
    loading.value = false
  }
}

async function saveTitle() {
  if (!sop.value || editTitle.value === sop.value.title) return
  savingTitle.value = true
  try {
    await sopsApi.update(sopId, { title: editTitle.value })
    sop.value.title = editTitle.value
  } catch { message.error('Failed to save title') }
  finally { savingTitle.value = false }
}

async function changeStatus(newStatus: string) {
  try {
    await sopsApi.updateStatus(sopId, { status: newStatus })
    await fetchSop()
    message.success(`Status changed to ${newStatus}`)
  } catch { message.error('Failed to change status') }
}

async function submitFeedback(payload: { feedback_text: string; scope: string }) {
  feedbackLoading.value = true
  try {
    const { data } = await sopsApi.submitFeedback(sopId, payload)
    message.success(`Feedback submitted, generating revision ${data.new_revision}`)
    currentRevision.value = data.new_revision
    maxRevision.value = data.new_revision
    if (sop.value) sop.value.status = 'regenerating'
    startPolling()
  } catch { message.error('Failed to submit feedback') }
  finally { feedbackLoading.value = false }
}

function handleStepFeedback(stepOrder: number) {
  feedbackScope.value = `step:${stepOrder}`
}

async function navigateRevision(rev: number) {
  if (rev === maxRevision.value) {
    viewingHistorical.value = false
    await fetchSop()
    return
  }
  try {
    const { data } = await sopsApi.getRevision(sopId, rev)
    const steps = JSON.parse(data.steps_snapshot_json)
    historicalSteps.value = steps
    currentRevision.value = rev
    viewingHistorical.value = true
  } catch { message.error('Failed to load revision') }
}

async function restoreRevision() {
  try {
    await sopsApi.restoreRevision(sopId, currentRevision.value)
    message.success('Revision restored')
    await fetchSop()
  } catch { message.error('Failed to restore revision') }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const { data } = await sopsApi.getStatus(sopId)
      if (data.status !== 'regenerating') {
        stopPolling()
        await fetchSop()
        message.success('SOP regeneration complete')
      }
    } catch { /* ignore polling errors */ }
  }, 3000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function goBack() { router.push('/sops') }

const exportOptions = [
  { label: 'Markdown', key: 'md' },
  { label: 'JSON', key: 'json' },
]

async function handleExport(key: string) {
  try {
    const resp = key === 'md'
      ? await sopsApi.exportMd(sopId)
      : await sopsApi.exportJson(sopId)
    const blob = new Blob([typeof resp.data === 'string' ? resp.data : JSON.stringify(resp.data, null, 2)])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `sop-${sopId}.${key}`
    a.click()
    URL.revokeObjectURL(url)
  } catch { message.error('Export failed') }
}

onMounted(fetchSop)
onUnmounted(stopPolling)
</script>

<template>
  <div class="sop-editor-page">
    <NSpin :show="loading">
      <template v-if="sop">
        <!-- Top bar -->
        <NCard size="small" style="margin-bottom: 16px">
          <NSpace align="center" justify="space-between" style="width: 100%">
            <NSpace align="center" :size="12">
              <NButton text @click="goBack">&larr; Back</NButton>
              <NDivider vertical />
              <NInput
                v-model:value="editTitle"
                style="width: 280px; font-weight: 600"
                :disabled="isReadonly"
                @blur="saveTitle"
                @keyup.enter="($event.target as HTMLInputElement)?.blur()"
              />
              <NTag :type="(statusType[sop.status] || 'default') as any" size="small">
                {{ statusLabel[sop.status] || sop.status }}
              </NTag>
              <SopRevisionNav
                :current-revision="currentRevision"
                :max-revision="maxRevision"
                :is-historical="viewingHistorical"
                @navigate="navigateRevision"
                @restore="restoreRevision"
              />
            </NSpace>

            <NSpace :size="8">
              <NButton
                v-if="sop.status === 'draft'"
                type="warning" size="small"
                @click="changeStatus('in_review')"
              >Submit Review</NButton>
              <NButton
                v-if="sop.status === 'in_review'"
                type="success" size="small"
                @click="changeStatus('published')"
              >Publish</NButton>
              <NButton
                v-if="sop.status === 'in_review'"
                size="small"
                @click="changeStatus('draft')"
              >Reject</NButton>
              <NDropdown :options="exportOptions" @select="handleExport">
                <NButton size="small">Export</NButton>
              </NDropdown>
            </NSpace>
          </NSpace>
        </NCard>

        <!-- Main content -->
        <div class="editor-layout">
          <!-- Left: step list -->
          <div class="step-list-panel">
            <NCard title="Steps" size="small">
              <NSkeleton v-if="sop.status === 'regenerating'" :repeat="4" text style="margin: 8px 0" />
              <NEmpty v-else-if="displaySteps.length === 0" description="No steps yet" />
              <NScrollbar v-else style="max-height: calc(100vh - 380px)">
                <NSpace vertical :size="8">
                  <SopStepCard
                    v-for="(step, idx) in displaySteps"
                    :key="step.id || idx"
                    :step="step"
                    :index="idx"
                    :active="selectedStepId === step.id"
                    :readonly="isReadonly"
                    @select="selectedStepId = step.id"
                    @feedback="handleStepFeedback"
                  />
                </NSpace>
              </NScrollbar>
            </NCard>
          </div>

          <!-- Right: frame preview -->
          <div class="frame-preview-panel">
            <NCard title="Screenshots" size="small">
              <FrameCarousel
                v-if="selectedStepFrames.length > 0"
                :frames="selectedStepFrames"
                max-width="100%"
              />
              <NEmpty v-else description="Select a step to view screenshots" style="margin-top: 40px" />
            </NCard>
          </div>
        </div>

        <!-- Bottom: feedback -->
        <NCard v-if="!isReadonly || sop.status === 'in_review'" size="small" style="margin-top: 16px">
          <SopFeedbackInput
            :disabled="sop.status === 'regenerating'"
            :loading="feedbackLoading"
            :default-scope="feedbackScope"
            @submit="submitFeedback"
          />
        </NCard>
      </template>

      <NEmpty v-if="!sop && !loading" description="SOP not found" />
    </NSpin>
  </div>
</template>

<style scoped>
.sop-editor-page { padding: 0; }
.editor-layout { display: flex; gap: 16px; align-items: flex-start; }
.step-list-panel { width: 50%; min-width: 0; }
.frame-preview-panel { width: 50%; min-width: 0; }

@media (max-width: 900px) {
  .editor-layout { flex-direction: column; }
  .step-list-panel, .frame-preview-panel { width: 100%; }
}
</style>
