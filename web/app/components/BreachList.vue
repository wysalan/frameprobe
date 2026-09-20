<template>
  <div v-if="breaches.length" class="text-sm">
    <div class="text-orange-300 mb-1">超標區間（{{ breaches.length }}）</div>
    <ul class="grid md:grid-cols-2 gap-x-6">
      <li v-for="b in breaches" :key="b.metric + b.start" class="text-slate-300">
        {{ b.start.toFixed(0) }}–{{ b.end.toFixed(0) }}s · {{ b.metric }}（最差 {{ b.worst.toFixed(1) }}）
      </li>
    </ul>
  </div>
  <p v-else class="text-sm text-emerald-400">沒有超標區間。</p>
</template>

<script setup lang="ts">
import type { Breach } from '~/composables/useApi'
defineProps<{ breaches: Breach[] }>()
</script>
