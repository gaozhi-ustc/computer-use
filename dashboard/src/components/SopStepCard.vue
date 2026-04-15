<script setup lang="ts">
import { computed } from 'vue'
import type { StepInfo } from '@/api/sops'
import { NCard, NTag, NSpace, NButton, NCollapse, NCollapseItem } from 'naive-ui'

const props = defineProps<{
  step: StepInfo
  index: number
  active: boolean
  readonly: boolean
}>()

const emit = defineEmits<{
  (e: 'select'): void
  (e: 'feedback', stepOrder: number): void
}>()

const actions = computed(() => {
  if (!props.step.machine_actions) return []
  return Array.isArray(props.step.machine_actions)
    ? props.step.machine_actions
    : []
})

function actionLabel(a: { type: string; target?: string; x?: number; y?: number }) {
  const pos = a.x !== undefined ? ` [${a.x}, ${a.y}]` : ''
  return `${a.type} ${a.target || ''}${pos}`.trim()
}
</script>

<template>
  <NCard
    size="small"
    hoverable
    :class="{ 'step-card-active': active }"
    class="sop-step-card"
    @click="emit('select')"
  >
    <NSpace vertical :size="6">
      <NSpace align="center" :size="8">
        <span class="step-number">{{ index + 1 }}</span>
        <span class="step-title">{{ step.title }}</span>
        <NTag v-if="step.application" size="small">{{ step.application }}</NTag>
      </NSpace>

      <div v-if="step.human_description" class="step-description">
        {{ step.human_description }}
      </div>
      <div v-else-if="step.description" class="step-description">
        {{ step.description }}
      </div>

      <NCollapse v-if="actions.length > 0">
        <NCollapseItem title="Machine Actions" name="actions">
          <div v-for="(a, i) in actions" :key="i" class="action-item">
            <NTag size="tiny" type="info">{{ a.type }}</NTag>
            <span class="action-detail">{{ actionLabel(a) }}</span>
          </div>
        </NCollapseItem>
      </NCollapse>

      <NButton
        v-if="!readonly"
        size="tiny"
        quaternary
        @click.stop="emit('feedback', step.step_order)"
      >
        Feedback on this step
      </NButton>
    </NSpace>
  </NCard>
</template>

<style scoped>
.sop-step-card { cursor: pointer; transition: border-color 0.2s; }
.sop-step-card.step-card-active { border-color: var(--primary-color, #18a058); }
.step-number { font-weight: 700; color: #18a058; min-width: 24px; }
.step-title { font-weight: 600; font-size: 14px; }
.step-description { font-size: 13px; color: #555; line-height: 1.5; }
.action-item { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.action-detail { font-size: 12px; color: #666; font-family: monospace; }
</style>
