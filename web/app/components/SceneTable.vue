<template>
  <table v-if="rows.length" class="w-full text-sm">
    <thead class="text-slate-400 text-left">
      <tr><th class="py-1">場景</th><th>區間</th><th>Avg FPS</th><th>P90</th><th>Jank / Big</th><th>App CPU</th><th>峰值 SKIN</th></tr>
    </thead>
    <tbody>
      <tr v-for="r in rows" :key="r.label + r.start" class="border-t border-slate-800">
        <td class="py-1">{{ r.label }}</td>
        <td class="tabular-nums">{{ r.start.toFixed(0) }}–{{ r.end.toFixed(0) }}s</td>
        <td>{{ fmt(r.avg_fps) }}</td>
        <td>{{ fmt(r.p90_fps) }}</td>
        <td :class="r.big_jank ? 'text-orange-300' : ''">{{ r.jank }} / {{ r.big_jank }}</td>
        <td>{{ fmt(r.avg_cpu_app_pct, 0, '%') }}</td>
        <td>{{ fmt(r.peak_skin_c, 1, '°C') }}</td>
      </tr>
    </tbody>
  </table>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed } from 'vue'
import type { Sample } from '~/composables/useApi'
import { fmt, sceneStats } from '~/composables/useApi'

const props = defineProps<{ samples: Sample[]; markers: [number, string][] }>()
const rows = computed(() => (props.markers.length ? sceneStats(props.samples, props.markers) : []))
</script>
