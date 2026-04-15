<script setup lang="ts">
import { ref, computed } from 'vue'
import type { FrameInfo } from '@/api/sessions'
import FrameImage from './FrameImage.vue'
import { NButton, NSpace } from 'naive-ui'

const props = defineProps<{
  frames: FrameInfo[]
  maxWidth?: string
}>()

const currentIndex = ref(0)

const currentFrame = computed(() =>
  props.frames.length > 0 ? props.frames[currentIndex.value] : null
)

function prev() {
  if (currentIndex.value > 0) currentIndex.value--
}

function next() {
  if (currentIndex.value < props.frames.length - 1) currentIndex.value++
}
</script>

<template>
  <div class="frame-carousel" :style="{ maxWidth: maxWidth || '100%' }">
    <FrameImage
      v-if="currentFrame"
      :frame="currentFrame"
      :max-width="maxWidth || '100%'"
      :clickable="false"
    />
    <div v-else class="carousel-empty">No frames</div>
    <NSpace justify="center" align="center" style="margin-top: 8px" :size="12">
      <NButton size="tiny" :disabled="currentIndex <= 0" @click="prev">
        &#9664;
      </NButton>
      <span class="carousel-counter">
        {{ frames.length > 0 ? `${currentIndex + 1} / ${frames.length}` : '0 / 0' }}
      </span>
      <NButton size="tiny" :disabled="currentIndex >= frames.length - 1" @click="next">
        &#9654;
      </NButton>
    </NSpace>
  </div>
</template>

<style scoped>
.frame-carousel {
  display: inline-block;
}
.carousel-counter {
  font-size: 12px;
  color: #666;
  min-width: 60px;
  text-align: center;
}
.carousel-empty {
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  background: #f5f5f5;
  border-radius: 4px;
}
</style>
