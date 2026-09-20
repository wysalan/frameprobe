<template>
  <div>
    <div v-if="title" class="flex items-center gap-1 text-xs text-slate-400 mb-1">{{ title }} <Help k="fps_chart" /></div>
    <VChart :option="option" :update-options="{ notMerge: true }" autoresize class="h-80! w-full" />
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, ScatterChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  DataZoomComponent,
  VisualMapComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import { useTheme } from '~/composables/useTheme'
import type { Sample } from '~/composables/useApi'
import { FULL_CHART_SHOW, type ChartShow } from '~/composables/reportMetrics'

use([CanvasRenderer, LineChart, ScatterChart, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent, VisualMapComponent, DataZoomComponent])

const props = defineProps<{
  series: { name: string; samples: Sample[] }[]
  /** 滾動視窗秒數；不給就顯示全部 */
  window?: number
  /** 場景標籤 (elapsed, label) */
  markers?: [number, string][]
  /** FPS 下限，畫一條紅色水平線 */
  fpsMin?: number
  /** 顯示 dataZoom（歷史頁用） */
  zoom?: boolean
  title?: string
  /** 要畫哪些線／標記；不給就全部（NPU 除外） */
  show?: ChartShow
  /** 匯出圖用：grid 以 containLabel 貼齊，不留固定邊距 */
  tight?: boolean
}>()
const show = computed(() => props.show ?? FULL_CHART_SHOW)

// Thermal Status 色帶（SPEC §12.8）：NONE 透明 / LIGHT 黃 / MODERATE 橘 / SEVERE+ 紅
const BAND_COLORS: Record<number, string> = {
  1: 'rgba(250, 204, 21, 0.12)',
  2: 'rgba(249, 115, 22, 0.18)',
  3: 'rgba(239, 68, 68, 0.22)',
}
const bandColor = (status: number) => BAND_COLORS[Math.min(status, 3)]

function thermalBands(samples: Sample[]) {
  const areas: unknown[] = []
  let start: number | null = null
  let current = 0
  let prevElapsed = 0
  for (const s of samples) {
    const status = s.throttling_status ?? 0
    if (status !== current) {
      if (current > 0 && start !== null) areas.push([{ xAxis: start, itemStyle: { color: bandColor(current) } }, { xAxis: prevElapsed }])
      start = prevElapsed
      current = status
    }
    prevElapsed = s.elapsed
  }
  if (current > 0 && start !== null) areas.push([{ xAxis: start, itemStyle: { color: bandColor(current) } }, { xAxis: prevElapsed }])
  return areas
}

const { chart } = useTheme()
const option = computed(() => {
  const c = chart.value
  const primary = props.series[0]?.samples ?? []
  // X 軸上限貼齊最後一筆（疊圖取最長者），否則 ECharts 會自動湊成整數刻度而在右側留白
  const last = Math.max(0, ...props.series.map((s) => s.samples.at(-1)?.elapsed ?? 0))
  const xMin = props.window ? Math.max(0, last - props.window) : 0
  const compare = props.series.length > 1
  const PALETTE = ['#38bdf8', '#a78bfa', '#34d399', '#fb923c', '#f472b6']

  // 節流色帶與場景標籤／門檻線：掛在第一條 FPS 線上；沒畫 FPS 時改掛在第一條溫度線
  const deco = {
    markArea: show.value.bands ? { silent: true, data: thermalBands(primary) } : undefined,
    markLine: (show.value.markers && props.markers?.length) || props.fpsMin ? {
      silent: true, symbol: 'none',
      lineStyle: { color: c.mark, type: 'dotted' },
      label: { color: c.mark, formatter: (p: { name: string }) => p.name, position: 'insideEndTop' },
      data: [
        ...(show.value.markers ? (props.markers ?? []) : []).map(([x, name]) => ({ xAxis: x, name })),
        ...(props.fpsMin ? [{ yAxis: props.fpsMin, name: `FPS ${props.fpsMin}`, lineStyle: { color: '#f87171', type: 'dashed' } }] : []),
      ],
    } : undefined,
  }
  const fpsSeries = props.series.flatMap((s, i) => {
    if (!show.value.fps && !show.value.p90) return []
    const base = [{
      name: compare ? `${s.name} FPS` : 'FPS',
      type: 'line', yAxisIndex: 0, showSymbol: false, smooth: false,
      lineStyle: { width: 2 },
      itemStyle: compare ? { color: PALETTE[i % PALETTE.length] } : undefined,
      data: show.value.fps ? s.samples.map((p) => [p.elapsed, p.fps]) : [],
      markArea: i === 0 ? deco.markArea : undefined,
      markLine: i === 0 ? deco.markLine : undefined,
    }]
    if (!compare && show.value.p90) {
      base.push({
        name: 'P90', type: 'line', yAxisIndex: 0, showSymbol: false, smooth: false,
        lineStyle: { width: 1, type: 'dashed' } as never,
        itemStyle: { color: '#a5b4fc' },
        data: s.samples.map((p) => [p.elapsed, p.p90_fps]),
        markArea: undefined,
        markLine: undefined,
      })
    }
    return base
  })

  const jankPoints = {
    name: 'Jank', type: 'scatter', yAxisIndex: 0, symbolSize: (v: number[]) => ((v[2] ?? 0) > 0 ? 10 : 6),
    itemStyle: { color: '#f87171' },
    data: primary.filter((p) => p.jank > 0).map((p) => [p.elapsed, p.fps, p.big_jank]),
    tooltip: { valueFormatter: (v: number) => String(v) },
  }

  // 溫度：第 3 維是 stale 旗標，visualMap 把 stale 的線段畫淡
  const tempKeys = (['cpu_c', 'gpu_c', 'skin_c', 'npu_c'] as const).filter((k) =>
    ({ cpu_c: show.value.cpu, gpu_c: show.value.gpu, skin_c: show.value.skin, npu_c: show.value.npu })[k])
  const tempNames: Record<string, string> = { cpu_c: 'CPU °C', gpu_c: 'GPU °C', skin_c: 'SKIN °C', npu_c: 'NPU °C' }
  // 固定顏色：不管有沒有畫 FPS／P90／Jank，同一種溫度在每張圖都同色
  const tempColors: Record<string, string> = { cpu_c: '#818cf8', gpu_c: '#f472b6', skin_c: '#fb923c', npu_c: '#a3e635' }
  const temps = tempKeys.map((key, i) => ({
    name: tempNames[key],
    type: 'line', yAxisIndex: 1, showSymbol: false, smooth: false,
    lineStyle: { width: 1.5 },
    itemStyle: { color: tempColors[key] },
    connectNulls: true,
    data: primary.map((p) => [p.elapsed, p[key], p.thermal_stale ? 1 : 0]),
    markArea: i === 0 && !fpsSeries.length ? deco.markArea : undefined,
    markLine: i === 0 && !fpsSeries.length ? deco.markLine : undefined,
  }))

  return {
    backgroundColor: 'transparent',
    animation: false,
    color: ['#38bdf8', '#818cf8', '#f472b6', '#fb923c', '#a3e635', '#22d3ee'],
    legend: { top: 0, textStyle: { color: c.legend } },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v == null ? '-' : Number(v).toFixed(1)) },
    grid: props.tight
      ? { left: 8, right: 8, top: 36, bottom: 8, containLabel: true }
      : { left: 48, right: 48, top: 36, bottom: props.zoom ? 56 : 28 },
    dataZoom: props.zoom ? [{ type: 'inside', xAxisIndex: 0 }, { type: 'slider', xAxisIndex: 0, height: 18, bottom: 6 }] : undefined,
    xAxis: { type: 'value', min: xMin, max: props.window ? Math.max(last, xMin + props.window) : last || undefined, name: 's', axisLabel: { color: c.label } },
    yAxis: [
      { type: 'value', name: 'FPS', min: 0, axisLabel: { color: c.label }, splitLine: { lineStyle: { color: c.grid } }, show: fpsSeries.length > 0 },
      { type: 'value', name: '°C', min: 20, max: 100, axisLabel: { color: c.label }, splitLine: { show: false }, show: temps.length > 0 },
    ],
    visualMap: {
      show: false, type: 'piecewise', dimension: 2,
      seriesIndex: temps.map((_, i) => fpsSeries.length + (show.value.jank ? 1 : 0) + i),
      pieces: [{ min: 0.5, max: 1.5, colorAlpha: 0.3 }, { min: -0.5, max: 0.5, colorAlpha: 1 }],
    },
    series: show.value.jank ? [...fpsSeries, jankPoints, ...temps] : [...fpsSeries, ...temps],
  }
})
</script>
