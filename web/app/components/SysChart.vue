<template>
  <div>
    <div v-if="title" class="flex items-center gap-1 text-xs text-slate-400 mb-1">{{ title }} <Help k="sys_chart" /></div>
    <VChart :option="option" :update-options="{ notMerge: true }" autoresize class="h-56! w-full" />
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { useTheme } from '~/composables/useTheme'
import type { Sample } from '~/composables/useApi'

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent])

const props = defineProps<{ samples: Sample[]; window?: number; zoom?: boolean; title?: string }>()

const { chart } = useTheme()
const option = computed(() => {
  const c = chart.value
  const last = props.samples.at(-1)?.elapsed ?? 0
  const xMin = props.window ? Math.max(0, last - props.window) : 0
  const line = (name: string, axis: number, pick: (s: Sample) => number | null, dashed = false) => ({
    name, type: 'line', yAxisIndex: axis, showSymbol: false, connectNulls: true,
    lineStyle: { width: 1.5, type: dashed ? 'dashed' : 'solid' },
    data: props.samples.map((s) => [s.elapsed, pick(s)]),
  })
  const memName = props.samples.some((s) => s.mem_pss_mb !== null) ? 'PSS MB' : 'RSS MB'
  return {
    backgroundColor: 'transparent',
    animation: false,
    color: ['#f59e0b', '#fbbf24', '#f472b6', '#a78bfa', '#34d399', '#f87171'],
    legend: { top: 0, textStyle: { color: c.legend } },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v == null ? '-' : Number(v).toFixed(0)) },
    grid: { left: 48, right: 56, top: 36, bottom: props.zoom ? 56 : 28 },
    dataZoom: props.zoom ? [{ type: 'inside', xAxisIndex: 0 }, { type: 'slider', xAxisIndex: 0, height: 18, bottom: 6 }] : undefined,
    xAxis: { type: 'value', min: xMin, max: props.window ? Math.max(last, xMin + props.window) : last || undefined, axisLabel: { color: c.label } },
    yAxis: [
      { type: 'value', name: 'CPU %', min: 0, axisLabel: { color: c.label }, splitLine: { lineStyle: { color: c.grid } } },
      { type: 'value', name: 'MB', min: 0, axisLabel: { color: c.label }, splitLine: { show: false } },
    ],
    series: [
      line('App CPU %', 0, (s) => s.cpu_app_pct),
      line('整機 CPU %', 0, (s) => s.cpu_total_pct, true),
      line('GPU %', 0, (s) => s.gpu_busy_pct),
      line(memName, 1, (s) => s.mem_pss_mb ?? s.mem_rss_mb),
      line('功耗 ×100 mW', 1, (s) => (s.battery_power_w === null ? null : Math.abs(s.battery_power_w) * 10), true),
    ],
  }
})
</script>
