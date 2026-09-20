<template>
  <div>
    <div v-if="title" class="flex items-center gap-1 text-xs text-slate-400 mb-1">{{ title }} <Help k="freq_chart" /></div>
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
import { cpuClusters } from '~/composables/useApi'

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent])

const props = withDefaults(defineProps<{
  samples: Sample[]
  /** 各核心的 cpuinfo_max_freq（MHz），用來分叢集；沒有就全部同一組 */
  maxMhz?: number[]
  window?: number
  zoom?: boolean
  title?: string
  /** 預設都畫；匯出時可個別關掉 */
  cpu?: boolean
  gpu?: boolean
  /** 匯出用：貼齊邊緣 */
  tight?: boolean
}>(), { cpu: true, gpu: true })

// 叢集色系：大核暖色、中核黃、小核綠、再多用紫；同叢集內用深淺區分
const CLUSTER_COLORS = [
  ['#f87171', '#fb923c', '#f472b6', '#dc2626'],
  ['#facc15', '#fde047', '#eab308', '#ca8a04'],
  ['#34d399', '#6ee7b7', '#10b981', '#059669'],
  ['#a78bfa', '#c4b5fd', '#8b5cf6', '#7c3aed'],
]

const { chart } = useTheme()
const option = computed(() => {
  const c = chart.value
  const showCpu = props.cpu
  const showGpu = props.gpu
  const last = props.samples.at(-1)?.elapsed ?? 0
  const xMin = props.window ? Math.max(0, last - props.window) : 0
  const cores = Math.max(0, ...props.samples.map((s) => s.cpu_freq_mhz?.length ?? 0))
  const clusters = cpuClusters(cores, props.maxMhz)
  const cpuSeries = showCpu
    ? clusters.flatMap((c, ci) => c.cores.map((core, k) => ({
      name: `cpu${core}`, type: 'line', yAxisIndex: 0, showSymbol: false, connectNulls: true,
      lineStyle: { width: 1.2 }, itemStyle: { color: CLUSTER_COLORS[ci % CLUSTER_COLORS.length]![k % 4] },
      data: props.samples.map((s) => [s.elapsed, s.cpu_freq_mhz?.[core] || null]),
    })))
    : []
  const gpuSeries = showGpu
    ? [{
      name: 'GPU', type: 'line', yAxisIndex: 1, showSymbol: false, connectNulls: true,
      lineStyle: { width: 2 }, itemStyle: { color: '#38bdf8' },
      data: props.samples.map((s) => [s.elapsed, s.gpu_freq_mhz]),
    }]
    : []
  return {
    backgroundColor: 'transparent',
    animation: false,
    legend: { top: 0, textStyle: { color: c.legend } },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v == null ? '-' : `${Number(v).toFixed(0)} MHz`) },
    grid: props.tight
      ? { left: 8, right: 8, top: 36, bottom: 8, containLabel: true }
      : { left: 56, right: 56, top: 36, bottom: props.zoom ? 56 : 28 },
    dataZoom: props.zoom ? [{ type: 'inside', xAxisIndex: 0 }, { type: 'slider', xAxisIndex: 0, height: 18, bottom: 6 }] : undefined,
    xAxis: { type: 'value', min: xMin, max: props.window ? Math.max(last, xMin + props.window) : last || undefined, axisLabel: { color: c.label } },
    yAxis: [
      { type: 'value', name: 'CPU MHz', min: 0, axisLabel: { color: c.label }, splitLine: { lineStyle: { color: c.grid } }, show: showCpu },
      { type: 'value', name: 'GPU MHz', min: 0, axisLabel: { color: c.label }, splitLine: { show: !showCpu, lineStyle: { color: c.grid } }, show: showGpu },
    ],
    series: [...cpuSeries, ...gpuSeries],
  }
})
</script>
