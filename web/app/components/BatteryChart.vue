<template>
  <div class="space-y-3">
    <!-- 三張圖各自一個實例，圖例可分別開關；間距與 Section 內其他圖表相同 -->
    <div v-for="(o, i) in options" :key="i">
      <div class="flex items-center gap-1 text-xs text-slate-400 mb-1">{{ PANELS[i]?.title }} <Help :k="PANELS[i]?.help" /></div>
      <VChart :option="o" :update-options="{ notMerge: true }" autoresize class="h-48! w-full" />
    </div>
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

const props = defineProps<{ samples: Sample[]; zoom?: boolean }>()
const PANELS = [
  { title: '電量與電池溫度', help: 'battery_chart_level' },
  { title: '電壓與電流', help: 'battery_chart_power' },
  { title: 'Charge counter', help: 'battery_chart_charge' },
]

interface Line { name: string; axis: 0 | 1; pick: (s: Sample) => number | null | undefined; color: string; dashed?: boolean }
interface Axis { name: string; min?: number; max?: number; scale?: boolean }

const { chart } = useTheme()
const options = computed(() => {
  const c = chart.value
  const last = props.samples.at(-1)?.elapsed ?? 0
  const panel = (lines: Line[], left: Axis, right?: Axis) => ({
    backgroundColor: 'transparent',
    animation: false,
    legend: { top: 0, textStyle: { color: c.legend } },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v == null ? '-' : Number(v).toFixed(1)) },
    grid: { left: 56, right: right ? 56 : 16, top: 30, bottom: props.zoom ? 56 : 24 },
    dataZoom: props.zoom ? [{ type: 'inside', xAxisIndex: 0 }, { type: 'slider', xAxisIndex: 0, height: 18, bottom: 6 }] : undefined,
    xAxis: { type: 'value', min: 0, max: last || undefined, axisLabel: { color: c.label }, splitLine: { show: false } },
    yAxis: [
      { type: 'value', ...left, axisLabel: { color: c.label }, splitLine: { lineStyle: { color: c.grid } } },
      { type: 'value', ...(right ?? { name: '' }), show: !!right, axisLabel: { color: c.label }, splitLine: { show: false } },
    ],
    series: lines.map((l) => ({
      name: l.name, type: 'line', yAxisIndex: l.axis, showSymbol: false, connectNulls: true,
      lineStyle: { width: 1.5, type: l.dashed ? 'dashed' : 'solid' }, itemStyle: { color: l.color },
      data: props.samples.map((s) => [s.elapsed, l.pick(s) ?? null]),
    })),
  })
  return [
    panel(
      [{ name: '電量 %', axis: 0, pick: (s) => s.battery_level_pct, color: '#38bdf8' }, { name: '電池溫度 °C', axis: 1, pick: (s) => s.battery_c, color: '#fb923c', dashed: true }],
      { name: '%', min: 0, max: 100 }, { name: '°C' },
    ),
    panel(
      [{ name: '電壓 mV', axis: 0, pick: (s) => (s.battery_voltage_v == null ? null : s.battery_voltage_v * 1000), color: '#a78bfa' }, { name: '電流 mA', axis: 1, pick: (s) => s.battery_current_ma, color: '#f472b6', dashed: true }],
      { name: 'mV', scale: true }, { name: 'mA' },
    ),
    panel([{ name: 'Charge counter mAh', axis: 0, pick: (s) => s.battery_charge_mah, color: '#34d399' }], { name: 'mAh', scale: true }),
  ]
})
</script>
