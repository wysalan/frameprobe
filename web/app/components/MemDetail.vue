<template>
  <div v-if="detail" class="text-sm">
    <div class="text-slate-400 mb-1 flex items-center gap-1">Memory Detail 峰值（dumpsys meminfo，PSS）<Help k="mem_detail" /><span v-if="stale" class="text-slate-500">（沿用上次）</span></div>
    <div class="flex flex-wrap gap-x-5 gap-y-1">
      <span v-for="[key, label] in MEM_DETAIL_LABELS" :key="key" v-show="detail[key] !== undefined">
        <span class="text-slate-400">{{ label }}</span> {{ fmt(detail[key], 0, ' MB') }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { MEM_DETAIL_LABELS, fmt } from '~/composables/useApi'

defineProps<{ detail: Record<string, number> | null | undefined; stale?: boolean }>()
</script>
