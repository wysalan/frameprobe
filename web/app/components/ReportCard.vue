<template>
  <!-- 匯出成圖片用的報告卡：固定寬度、明確背景色（html-to-image 不會帶頁面背景） -->
  <div class="text-slate-100 bg-slate-950 space-y-4" style="width: 1200px; padding: 28px; font-family: 'Inter Variable', 'Noto Sans TC Variable', ui-sans-serif, system-ui, sans-serif;">
    <header class="space-y-0.5">
      <div class="flex items-baseline gap-3">
        <span v-if="options.deviceName" class="text-xl font-semibold text-sky-300">{{ options.deviceName }}</span>
        <span class="text-xl font-semibold">{{ summary.package }}</span>
        <span class="ml-auto font-mono text-xs text-slate-500">{{ sessionTime(summary.session_id) }}</span>
      </div>
      <div class="text-sm text-slate-400">{{ summary.mode === 'record' ? '錄製（Perfetto）' : '即時（timestats）' }} · 時長 {{ fmt(summary.duration_s, 0, 's') }}</div>
    </header>

    <section class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
      <h3 class="text-sm font-semibold text-slate-200">裝置</h3>
      <DeviceSpecsGrid v-if="summary.device.specs" :specs="summary.device.specs" :fixed-cols="6" />
      <p v-else class="text-xs text-slate-500">{{ summary.device.manufacturer }} {{ summary.device.model }} · Android {{ summary.device.android_release }}（此 Session 沒有存完整規格）</p>
    </section>

    <!-- 每個區塊＝指標卡片＋圖表；圖表停用但有選卡片時只出卡片 -->
    <section v-if="options.chart.fpsChart || cells.fpsChart.length" class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
      <h3 class="text-sm font-semibold text-slate-200">{{ options.chart.fpsChart ? chartTitle : '幀率與卡頓' }}</h3>
      <div v-if="cells.fpsChart.length" class="grid gap-2" :style="rowStyle(cells.fpsChart.length)">
        <Stat v-for="c in cells.fpsChart" :key="c.key" :label="c.label" :value="c.value" :unit="c.unit" :sub="c.sub" :warn="c.warn" />
      </div>
      <FpsChart v-if="options.chart.fpsChart" :series="[{ name: summary.session_id, samples }]" :markers="summary.markers" :show="options.chart" :fps-min="fpsMin" tight />
    </section>

    <section v-if="options.chart.tempChart || cells.tempChart.length" class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
      <h3 class="text-sm font-semibold text-slate-200">溫度</h3>
      <div v-if="cells.tempChart.length" class="grid gap-2" :style="rowStyle(cells.tempChart.length)">
        <Stat v-for="c in cells.tempChart" :key="c.key" :label="c.label" :value="c.value" :unit="c.unit" :sub="c.sub" :warn="c.warn" />
      </div>
      <FpsChart v-if="options.chart.tempChart" :series="[{ name: summary.session_id, samples }]" :markers="summary.markers" :show="tempChartShow(options.chart)" tight />
    </section>

    <section v-if="freqChart || cells.freq.length" class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
      <h3 class="text-sm font-semibold text-slate-200">{{ freqChart ? `${[options.chart.cpuFreq ? 'CPU' : '', options.chart.gpuFreq ? 'GPU' : ''].filter(Boolean).join(' / ')} 頻率` : 'CPU、GPU 與記憶體' }}</h3>
      <div v-if="cells.freq.length" class="grid gap-2" :style="rowStyle(cells.freq.length)">
        <Stat v-for="c in cells.freq" :key="c.key" :label="c.label" :value="c.value" :unit="c.unit" :sub="c.sub" :warn="c.warn" />
      </div>
      <FreqChart v-if="freqChart" :samples="samples" :max-mhz="summary.device.specs?.cpu_max_mhz" :cpu="options.chart.cpuFreq" :gpu="options.chart.gpuFreq" tight />
    </section>

    <section v-if="cells.power.length" class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-2">
      <h3 class="text-sm font-semibold text-slate-200">功耗</h3>
      <div v-if="cells.power.length" class="grid gap-2" :style="rowStyle(cells.power.length)">
        <Stat v-for="c in cells.power" :key="c.key" :label="c.label" :value="c.value" :unit="c.unit" :sub="c.sub" :warn="c.warn" />
      </div>
    </section>

    <footer class="text-base text-slate-500">Powered by <span class="font-bold">frameprobe</span></footer>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed } from 'vue'
import type { Sample, SessionSummary } from '~/composables/useApi'
import { fmt, loadThresholds, sessionTime } from '~/composables/useApi'
import { MAX_REPORT_METRICS, METRIC_BY_KEY, tempChartShow, type ExportOptions } from '~/composables/reportMetrics'

const props = defineProps<{ summary: SessionSummary; samples: Sample[]; options: ExportOptions }>()
// 各區塊的卡片，順序即 options.metrics 的順序（使用者在匯出設定拖曳決定）
function cellsOf(keys: string[]) {
  return keys.map((k) => METRIC_BY_KEY[k]).filter((m) => m !== undefined).map((m) => ({ key: m.key, label: m.label, ...m.get(props.summary) }))
}
const cells = computed(() => ({
  fpsChart: cellsOf(props.options.metrics.fpsChart),
  tempChart: cellsOf(props.options.metrics.tempChart),
  freq: cellsOf(props.options.metrics.freq),
  power: cellsOf(props.options.metrics.power),
}))
const freqChart = computed(() => props.options.chart.freq && (props.options.chart.cpuFreq || props.options.chart.gpuFreq))
function rowStyle(n: number) {
  return { gridTemplateColumns: `repeat(${Math.min(n, MAX_REPORT_METRICS)}, minmax(0, 1fr))` }
}
const fpsMin = computed(() => (props.options.chart.fpsMin ? loadThresholds().fpsMin : undefined))
const chartTitle = computed(() => {
  const c = props.options.chart
  const parts = [c.fps || c.p90 ? 'FPS' : '', c.cpu || c.gpu || c.skin || c.npu ? '溫度' : ''].filter(Boolean)
  return parts.join(' 與 ') || '圖表'
})
</script>
