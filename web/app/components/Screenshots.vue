<template>
  <div v-if="shots.length" class="text-sm">
    <div class="text-slate-400 mb-1">截圖（{{ shots.length }}）</div>
    <div class="flex gap-2 overflow-x-auto pb-2">
      <button v-for="(s, i) in shots" :key="s.screenshot!" type="button" class="shrink-0 text-center" @click="open = i">
        <img :src="api.shotUrl(sessionId, s.screenshot!)" class="h-32 rounded border border-slate-800 hover:border-slate-500" loading="lazy" />
        <div class="text-xs text-slate-500">{{ s.elapsed.toFixed(0) }}s</div>
      </button>
    </div>

    <!-- 放大預覽：點背景或 Esc 關閉，← → 切換 -->
    <div v-if="current" class="fixed inset-0 z-50 m-0 bg-black/80 flex flex-col items-center justify-center p-4 gap-2" @click="open = null">
      <img :src="api.shotUrl(sessionId, current.screenshot!)" class="max-w-full max-h-[85vh] rounded border border-slate-700" @click.stop />
      <div class="text-xs text-slate-300">
        {{ current.elapsed.toFixed(0) }}s · {{ open! + 1 }} / {{ shots.length }}
        <span v-if="current.fps !== null"> · FPS {{ current.fps.toFixed(1) }}</span>
        <span class="text-slate-500 ml-2">← → 切換，Esc 關閉</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { Sample } from '~/composables/useApi'

const props = defineProps<{ sessionId: string; samples: Sample[] }>()
const api = useApi()
const shots = computed(() => props.samples.filter((s) => s.screenshot))
const open = ref<number | null>(null)
const current = computed(() => (open.value === null ? null : shots.value[open.value] ?? null))

function onKey(e: KeyboardEvent) {
  if (open.value === null) return
  if (e.key === 'Escape') open.value = null
  else if (e.key === 'ArrowRight') open.value = (open.value + 1) % shots.value.length
  else if (e.key === 'ArrowLeft') open.value = (open.value - 1 + shots.value.length) % shots.value.length
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>
