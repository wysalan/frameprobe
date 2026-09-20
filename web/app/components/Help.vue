<template>
  <span v-if="text" class="help" tabindex="0" :aria-label="text" @mouseenter="place" @focus="place">
    <span class="help-icon">?</span>
    <span ref="pop" class="help-pop" role="tooltip">{{ text }}</span>
  </span>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, ref } from 'vue'
import { HELP } from '~/composables/metrics'

const props = defineProps<{ k?: string; text?: string }>()
const text = computed(() => props.text ?? (props.k ? HELP[props.k] : undefined))
const pop = ref<HTMLElement | null>(null)

// position: fixed 讓說明框不被卡片的 overflow 裁掉；靠近右邊時往左靠
function place(ev: Event) {
  const el = pop.value
  const icon = (ev.currentTarget as HTMLElement).getBoundingClientRect()
  if (!el) return
  const width = Math.min(352, window.innerWidth * 0.8)
  const left = Math.max(8, Math.min(icon.left, window.innerWidth - width - 8))
  el.style.left = `${left}px`
  el.style.top = `${icon.bottom}px`
  el.style.width = `${width}px`
}
</script>
