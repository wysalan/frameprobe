<template>
  <div class="rounded-lg bg-slate-900/80 border border-slate-800 px-3 py-2 min-w-0">
    <div class="flex items-center gap-1 text-[11px] tracking-wide text-slate-400 min-w-0">
      <span class="truncate" :title="label">{{ label }}</span>
      <Help :k="help" />
    </div>
    <div
      :class="[
        compact ? 'text-sm font-medium leading-snug break-words whitespace-pre-line' : ['font-semibold tabular-nums leading-tight', long ? 'text-base' : 'text-xl'],
        warn ? 'text-orange-300' : dim ? 'text-slate-500' : '',
      ]"
    >
      {{ value }}<span v-if="unit" class="text-sm font-normal text-slate-400 ml-0.5">{{ unit }}</span>
    </div>
    <div v-if="sub" class="text-xs text-slate-500" :class="compact ? 'break-words' : 'truncate'">{{ sub }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{
  label: string
  value: string
  unit?: string
  sub?: string
  help?: string
  warn?: boolean
  dim?: boolean
  /** 文字型的值（規格、名稱）：字級小一號、允許換行 */
  compact?: boolean
}>()
// 數值一律 text-xl；含單位超過 11 個字元（例如「454 / 719 MHz」）在最窄的卡片會折行，改用小一級
const long = computed(() => (props.value + (props.unit ?? '')).length > 11)
</script>
