<script setup lang="ts">
import { NButton, NSpace } from 'naive-ui'

const props = defineProps<{
  currentRevision: number
  maxRevision: number
  isHistorical: boolean
}>()

const emit = defineEmits<{
  (e: 'navigate', revision: number): void
  (e: 'restore'): void
}>()

function prev() {
  if (props.currentRevision > 1) {
    emit('navigate', props.currentRevision - 1)
  }
}

function next() {
  if (props.currentRevision < props.maxRevision) {
    emit('navigate', props.currentRevision + 1)
  }
}
</script>

<template>
  <NSpace align="center" :size="8">
    <NButton size="tiny" :disabled="currentRevision <= 1" @click="prev">
      &#9664;
    </NButton>
    <span class="rev-label">rev {{ currentRevision }}/{{ maxRevision }}</span>
    <NButton size="tiny" :disabled="currentRevision >= maxRevision" @click="next">
      &#9654;
    </NButton>
    <NButton
      v-if="isHistorical"
      size="tiny"
      type="warning"
      @click="emit('restore')"
    >
      Restore
    </NButton>
  </NSpace>
</template>

<style scoped>
.rev-label { font-size: 12px; color: #666; min-width: 70px; text-align: center; }
</style>
