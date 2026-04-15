<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import type { FrameInfo } from '@/api/sessions'
import client from '@/api/client'

const props = withDefaults(defineProps<{
  frame: FrameInfo
  maxWidth?: string
  clickable?: boolean
}>(), {
  maxWidth: '300px',
  clickable: true,
})

const emit = defineEmits<{
  (e: 'click'): void
}>()

const imgRef = ref<HTMLImageElement | null>(null)
const naturalW = ref(0)
const naturalH = ref(0)
const loaded = ref(false)
const errored = ref(false)
const blobUrl = ref<string>('')

async function fetchImage() {
  if (blobUrl.value) {
    URL.revokeObjectURL(blobUrl.value)
    blobUrl.value = ''
  }
  loaded.value = false
  try {
    const resp = await client.get(`/api/frames/${props.frame.id}/image`, {
      responseType: 'blob',
    })
    blobUrl.value = URL.createObjectURL(resp.data)
    errored.value = false
  } catch {
    errored.value = true
  }
}

watch(() => props.frame.id, fetchImage, { immediate: true })

onUnmounted(() => {
  if (blobUrl.value) {
    URL.revokeObjectURL(blobUrl.value)
  }
})

function onLoad() {
  if (imgRef.value) {
    naturalW.value = imgRef.value.naturalWidth
    naturalH.value = imgRef.value.naturalHeight
    loaded.value = true
  }
}

function onError() {
  errored.value = true
}

function onClick() {
  if (props.clickable) {
    emit('click')
  }
}

/** Red solid box at cursor_x/cursor_y. Falls back to qwen's mouse_position. */
const cursorOverlay = computed(() => {
  if (!loaded.value || naturalW.value === 0) return null
  const f = props.frame
  let cx = -1
  let cy = -1
  if (f.cursor_x !== undefined && f.cursor_y !== undefined &&
      f.cursor_x >= 0 && f.cursor_y >= 0) {
    cx = f.cursor_x
    cy = f.cursor_y
  } else if (f.mouse_position && f.mouse_position.length >= 2) {
    cx = f.mouse_position[0]
    cy = f.mouse_position[1]
  }
  if (cx < 0 || cy < 0) return null

  const BOX_PX = 40  // natural-pixel box size
  const pctX = (cx / naturalW.value) * 100
  const pctY = (cy / naturalH.value) * 100
  const pctBoxW = (BOX_PX / naturalW.value) * 100
  const pctBoxH = (BOX_PX / naturalH.value) * 100
  return {
    left: `calc(${pctX}% - ${pctBoxW / 2}%)`,
    top: `calc(${pctY}% - ${pctBoxH / 2}%)`,
    width: `${pctBoxW}%`,
    height: `${pctBoxH}%`,
  }
})

/** Yellow dashed box for the focused control rect. */
const focusOverlay = computed(() => {
  if (!loaded.value || naturalW.value === 0) return null
  const r = props.frame.focus_rect
  if (!r || r.length !== 4) return null
  const [x1, y1, x2, y2] = r
  if (x2 <= x1 || y2 <= y1) return null
  return {
    left: `${(x1 / naturalW.value) * 100}%`,
    top: `${(y1 / naturalH.value) * 100}%`,
    width: `${((x2 - x1) / naturalW.value) * 100}%`,
    height: `${((y2 - y1) / naturalH.value) * 100}%`,
  }
})
</script>

<template>
  <div
    class="frame-img-wrapper"
    :style="{ maxWidth: maxWidth }"
    :class="{ clickable }"
    @click="onClick"
  >
    <img
      v-if="blobUrl"
      ref="imgRef"
      :src="blobUrl"
      class="frame-img"
      alt="recording frame"
      @load="onLoad"
      @error="onError"
    />
    <div v-if="errored" class="frame-error">
      <span>图片加载失败</span>
    </div>
    <div v-if="focusOverlay" class="focus-overlay" :style="focusOverlay"></div>
    <div v-if="cursorOverlay" class="cursor-overlay" :style="cursorOverlay"></div>
  </div>
</template>

<style scoped>
.frame-img-wrapper {
  position: relative;
  display: inline-block;
  line-height: 0;  /* eliminate baseline gap under img */
  background: #f5f5f5;
  border-radius: 4px;
  overflow: hidden;
}
.frame-img-wrapper.clickable {
  cursor: zoom-in;
}
.frame-img {
  width: 100%;
  height: auto;
  display: block;
}
.frame-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  font-size: 12px;
  background: #f5f5f5;
}
.cursor-overlay {
  position: absolute;
  border: 2px solid #f00;
  border-radius: 2px;
  /* White rings on both sides of the red border create a 白-红-白
     sandwich that stays visible on red/orange/pink backgrounds. */
  box-shadow:
    inset 0 0 0 2px #fff,
    0 0 0 2px #fff,
    0 0 8px rgba(255, 0, 0, 0.9);
  pointer-events: none;
}
.focus-overlay {
  position: absolute;
  border: 2px dashed #fc0;
  /* Same technique, tuned down since focus rect is usually larger
     and yellow vs background is less of a conflict case. */
  box-shadow:
    inset 0 0 0 1px rgba(0, 0, 0, 0.6),
    0 0 0 1px rgba(0, 0, 0, 0.6);
  pointer-events: none;
}
</style>
