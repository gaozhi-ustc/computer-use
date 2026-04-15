<script setup lang="ts">
import { ref } from 'vue'
import { NInput, NButton, NSpace } from 'naive-ui'

const props = defineProps<{
  disabled: boolean
  loading: boolean
  defaultScope?: string
}>()

const emit = defineEmits<{
  (e: 'submit', payload: { feedback_text: string; scope: string }): void
}>()

const feedbackText = ref('')
const scope = ref(props.defaultScope || 'full')

function submit() {
  if (!feedbackText.value.trim()) return
  emit('submit', {
    feedback_text: feedbackText.value.trim(),
    scope: scope.value,
  })
  feedbackText.value = ''
}
</script>

<template>
  <div class="feedback-input">
    <NInput
      v-model:value="feedbackText"
      type="textarea"
      :rows="3"
      :disabled="disabled"
      placeholder="Enter modification feedback..."
      @keydown.ctrl.enter="submit"
    />
    <NSpace justify="space-between" style="margin-top: 8px">
      <span class="scope-label">
        Scope: <strong>{{ scope === 'full' ? 'Full SOP' : scope }}</strong>
      </span>
      <NButton
        type="primary"
        :disabled="disabled || !feedbackText.trim()"
        :loading="loading"
        @click="submit"
      >
        Regenerate SOP
      </NButton>
    </NSpace>
  </div>
</template>

<style scoped>
.feedback-input { padding: 12px 0; }
.scope-label { font-size: 12px; color: #666; line-height: 34px; }
</style>
