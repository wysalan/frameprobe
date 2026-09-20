<template>
  <div v-if="summary" class="space-y-4">
    <header class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h1 class="text-lg font-semibold">{{ summary.package }}</h1>
      <span class="text-slate-400 text-sm">{{ summary.device.manufacturer }} {{ summary.device.model }} · Android {{ summary.device.android_release }} · {{ summary.mode === 'record' ? '錄製（Perfetto）' : '即時（timestats）' }}</span>
      <span class="font-mono text-xs text-slate-500">{{ summary.session_id }}</span>
      <span v-if="status && status !== 'stopped'" class="text-amber-300 text-sm">{{ status }} {{ message }}</span>
      <span class="ml-auto text-sm flex items-center gap-3">
        <a :href="api.exportUrl(summary.session_id, 'csv')" class="underline text-sky-400">CSV</a>
        <a :href="api.exportUrl(summary.session_id, 'json')" class="underline text-sky-400">JSON</a>
        <button class="btn-ghost text-xs" :disabled="exporting" @click="showExport = true">{{ exporting ? '產生中…' : '匯出圖片' }}</button>
      </span>
    </header>
    <ExportDialog v-if="showExport" :placeholder="summary.device.specs?.display_name || summary.device.model" @cancel="showExport = false" @confirm="exportImage" />
    <!-- 匯出用的報告卡放在畫面外，只有匯出時掛載 -->
    <div v-if="exporting && exportOptions" style="position: fixed; left: -20000px; top: 0;">
      <ReportCard ref="reportCard" :summary="summary" :samples="samples" :options="exportOptions" />
    </div>
    <DeviceBar :specs="summary.device.specs" :error="summary.device.specs ? '' : '此 Session 沒有存裝置規格（採集時取不到或為舊版格式）'" />
    <p class="text-xs text-slate-400">
      layer <span class="font-mono">{{ summary.layer_name || '-' }}</span> · 幀數 {{ summary.total_frames }} · 掉幀 {{ summary.dropped_frames }} · 時長 {{ fmt(summary.duration_s, 0, 's') }}
      <span v-if="summary.app_level_available === false" class="text-amber-300 ml-2 inline-flex items-center gap-1">App 層級 FrameTimeline 無資料，數字為顯示層級 <Help k="app_level" /></span>
      <span v-if="summary.thermal_timeline === 'approximate'" class="text-amber-300 ml-2">溫度時間軸為近似對齊</span>
    </p>

    <!-- 總覽：評測常用數值 + 主圖 -->
    <Section title="總覽">
      <div class="metric-grid">
        <Stat label="Avg FPS" :value="fmt(summary.average_fps)" help="avg_fps" />
        <Stat label="1% Low" :value="fmt(summary.low_1_fps)" help="low_1" />
        <Stat :label="summary.jank_method === 'exact' ? 'Jank / BigJank' : 'Jank / BigJank（近似）'" :value="`${summary.jank_total} / ${summary.big_jank_total}`" help="jank" :warn="summary.big_jank_total > 0" />
        <Stat label="開始節流" :value="summary.time_to_throttle_s === null ? '未節流' : fmt(summary.time_to_throttle_s, 0)" :unit="summary.time_to_throttle_s === null ? '' : 's 後'" help="time_to_throttle" :warn="summary.time_to_throttle_s !== null" />
        <Stat label="峰值 SKIN" :value="fmt(summary.peak_skin_c)" unit="°C" help="temp_skin" />
        <Stat label="平均功耗（未接電）" :value="fmt(summary.avg_power_w, 2)" unit="W" help="power" :warn="summary.plugged_ratio === 1" />
        <Stat label="App CPU 平均" :value="fmt(summary.avg_cpu_app_pct, 0)" unit="%" help="cpu_app" />
      </div>
      <FpsChart :series="series" :markers="summary.markers" :fps-min="thresholds.fpsMin" zoom title="FPS 與溫度" />
      <details class="text-sm">
        <summary class="cursor-pointer text-slate-400"><Help k="compare" /> 疊圖比較{{ compareIds.length ? `（已選 ${compareIds.length}）` : '' }}</summary>
        <table v-if="others.length" class="w-full text-sm mt-2">
          <thead class="text-slate-400 text-left">
            <tr><th class="py-1 w-8"></th><th>Session</th><th>裝置</th><th>模式</th><th>Avg FPS</th><th>1% Low</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in others" :key="s.session_id" class="border-t border-slate-800 hover:bg-slate-900">
              <td class="py-1"><input type="checkbox" :value="s.session_id" v-model="compareIds" :disabled="!compareIds.includes(s.session_id) && compareIds.length >= 4" /></td>
              <td class="font-mono text-xs"><NuxtLink :to="`/sessions/${s.session_id}`" class="text-sky-400 underline">{{ sessionTime(s.session_id) }}</NuxtLink></td>
              <td class="whitespace-nowrap">{{ s.device.specs?.display_name || `${s.device.manufacturer} ${s.device.model}`.trim() || s.device.serial }}</td>
              <td>{{ s.mode === 'record' ? '錄製' : '即時' }}</td>
              <td>{{ fmt(s.average_fps) }}</td>
              <td>{{ fmt(s.low_1_fps) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-slate-500 mt-2">沒有同套件名稱的其他 Session。</div>
      </details>
      <ThresholdSettings @change="(t: Thresholds) => (thresholds = t)" />
    </Section>

    <!-- 幀率 -->
    <Section title="幀率與卡頓">
      <div class="metric-grid">
        <Stat label="P90" :value="fmt(summary.p90_fps)" help="p90_session" />
        <Stat label="P99" :value="fmt(summary.p99_fps)" help="p99_session" />
        <Stat label="Stutter" :value="fmt(summary.stutter_ratio === null ? null : summary.stutter_ratio * 100, 2)" unit="%" help="stutter" />
        <Stat label="Jank / 10min" :value="fmt(summary.jank_per_10min, 1)" help="jank_10min" />
        <Stat label="Std(FPS)" :value="fmt(summary.fps_std, 1)" help="fps_std" />
        <Stat label="Drop(FPS)" :value="String(summary.drop_fps)" help="drop_fps" :warn="summary.drop_fps > 0" />
        <Stat label="Avg / Std FTime" :value="`${fmt(summary.ftime_avg_ms, 1)} / ${fmt(summary.ftime_std_ms, 1)}`" unit="ms" help="ftime" />
        <Stat label=">100ms 幀" :value="String(summary.delta_ftime)" help="delta_ftime" :warn="summary.delta_ftime > 0" />
        <Stat label="掉幀" :value="String(summary.dropped_frames)" help="dropped" />
      </div>
      <p v-if="summary.jank_breakdown" class="text-xs text-slate-400">
        jank 歸因：<span v-for="(v, k) in summary.jank_breakdown" :key="k" class="mr-3">{{ k }} {{ v }}</span>
      </p>
      <HistogramChart :histogram="mergedHistogram" title="幀間隔分布（整段）" />
    </Section>

    <!-- 溫度 -->
    <Section title="溫度與節流" help="throttle">
      <div class="metric-grid">
        <Stat label="峰值 CPU" :value="fmt(summary.peak_cpu_c)" unit="°C" help="temp_cpu" />
        <Stat label="峰值 GPU" :value="fmt(summary.peak_gpu_c)" unit="°C" help="temp_gpu" />
        <Stat label="峰值 SKIN" :value="fmt(summary.peak_skin_c)" unit="°C" help="temp_skin" />
        <Stat v-if="summary.peak_npu_c !== null" label="峰值 NPU" :value="fmt(summary.peak_npu_c)" unit="°C" help="temp_npu" />
        <Stat label="節流累計" :value="fmt(summary.throttle_duration_s, 0)" unit="s" help="throttle_duration" />
        <!-- 內容長度不固定：用小字級、一次變化一行，才不會撐出卡片 -->
        <Stat label="節流狀態變化" :value="summary.throttle_timeline.map(([t, s]) => `${t.toFixed(0)}s→${THROTTLE_LABELS[s] ?? s}`).join('\n') || '-'" help="throttle" compact />
      </div>
      <FpsChart :series="[{ name: id, samples }]" :markers="summary.markers" :show="tempShow" zoom title="溫度時間軸" />
    </Section>

    <!-- CPU / GPU / 記憶體 -->
    <Section title="CPU、GPU 與記憶體" help="sys_chart">
      <div class="metric-grid">
        <Stat label="App CPU 平均 / 峰值" :value="`${fmt(summary.avg_cpu_app_pct, 0)} / ${fmt(summary.peak_cpu_app_pct, 0)}`" unit="%" help="cpu_app" />
        <Stat label="整機 CPU 平均" :value="fmt(summary.avg_cpu_total_pct, 0)" unit="%" help="cpu_total" />
        <Stat label="GPU 平均 / 峰值" :value="`${fmt(summary.avg_gpu_busy_pct, 0)} / ${fmt(summary.peak_gpu_busy_pct, 0)}`" unit="%" help="gpu_busy" />
        <Stat label="CPU 頻率平均" :value="cpuFreqLines" help="cpu_freq" compact />
        <Stat label="GPU 頻率平均 / 峰值" :value="`${fmt(freqStats.gpuAvg, 0)} / ${fmt(freqStats.gpuPeak, 0)}`" unit="MHz" help="gpu_freq" />
        <Stat :label="summary.peak_mem_pss_mb !== null ? '記憶體峰值 PSS' : '記憶體峰值 RSS'" :value="fmt(summary.peak_mem_pss_mb ?? summary.peak_mem_rss_mb, 0)" unit="MB" :help="summary.peak_mem_pss_mb !== null ? 'pss' : 'rss'" />
        <Stat label="GPU 記憶體峰值" :value="fmt(summary.peak_gpu_mem_mb, 0)" unit="MB" help="gpu_mem" />
        <Stat label="Wakeups / CSwitch" :value="`${fmt(summary.avg_wakeups, 0)} / ${fmt(summary.avg_cswitch, 0)}`" sub="每區間平均" help="cswitch" />
      </div>
      <SysChart :samples="samples" zoom title="CPU / GPU / 記憶體 / 功耗" />
      <FreqChart :samples="samples" :max-mhz="summary.device.specs?.cpu_max_mhz" zoom title="CPU / GPU 頻率" />
      <MemDetail :detail="peakMemDetail" />
    </Section>

    <!-- 功耗 / 電池 -->
    <Section title="功耗與電池" :note="summary.plugged_ratio === 1 ? '全程接著電源，功耗不代表整機耗電' : ''">
      <div class="metric-grid">
        <Stat label="電池狀態" :value="summary.battery_status == null ? '-' : BATTERY_STATUS[summary.battery_status] ?? String(summary.battery_status)" help="battery_status" />
        <Stat label="開始電量" :value="fmt(summary.battery_level_start ?? null, 0)" :unit="summary.battery_level_start == null ? '' : '%'" help="battery_level" />
        <Stat label="結束電量" :value="fmt(summary.battery_level_end ?? null, 0)" :unit="summary.battery_level_end == null ? '' : '%'" help="battery_level" />
        <Stat label="接電源比例" :value="fmt(summary.plugged_ratio === null ? null : summary.plugged_ratio * 100, 0)" unit="%" help="power" />
        <Stat label="Charge counter" :value="fmt(summary.battery_consumed_mah ?? null, 0)" :unit="summary.battery_consumed_mah == null ? '' : 'mAh'" sub="開始 − 結束" help="battery_charge" />
        <Stat label="平均功耗（未接電）" :value="fmt(summary.avg_power_w, 2)" unit="W" help="power" :warn="summary.plugged_ratio === 1" />
        <Stat label="FPower" :value="fmt(summary.avg_fpower_mw, 1)" unit="mW/幀" help="fpower" />
      </div>
      <BatteryChart :samples="samples" zoom />
    </Section>

    <!-- 場景 / 超標 / 截圖 -->
    <Section title="場景標籤、超標與截圖" help="markers">
      <div class="flex items-center gap-2 text-sm">
        <label>補標籤：</label>
        <input v-model.number="newMarker.elapsed" type="number" min="0" step="1" class="input w-24" placeholder="秒" />
        <input v-model="newMarker.label" class="input w-56" placeholder="場景名稱" @keyup.enter="addMarker" />
        <button class="btn-ghost" :disabled="!newMarker.label || newMarker.elapsed === null" @click="addMarker">加入</button>
      </div>
      <SceneTable :samples="samples" :markers="summary.markers" />
      <BreachList :breaches="findBreaches(samples, thresholds)" />
      <Screenshots :session-id="id" :samples="samples" />
    </Section>

    <Section v-if="summary.notes.length" title="備註" note="採集過程中的來源與 fallback，任何降級都會寫在這裡">
      <ul class="list-disc pl-5 text-xs text-slate-300 space-y-0.5">
        <li v-for="n in summary.notes" :key="n">{{ n }}</li>
      </ul>
    </Section>
  </div>
  <p v-else-if="error" class="text-red-400">{{ error }}</p>
  <p v-else class="text-slate-500">載入中…</p>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import type { Sample, SessionSummary, Thresholds } from '~/composables/useApi'
import { BATTERY_STATUS, DEFAULT_THRESHOLDS, THROTTLE_LABELS, clusterAvgText, clusterLabel, cpuClusters, findBreaches, fmt, sessionTime } from '~/composables/useApi'
import { DEFAULT_CHART_SHOW, type ChartShow, type ExportOptions } from '~/composables/reportMetrics'
import { useTheme } from '~/composables/useTheme'

const api = useApi()
const id = String(useRoute().params.id)
const summary = ref<SessionSummary | null>(null)
const samples = ref<Sample[]>([])
const status = ref('')
const message = ref('')
const error = ref('')
const others = ref<SessionSummary[]>([])
const compareIds = ref<string[]>([])
const compareSamples = ref<Record<string, Sample[]>>({})
const thresholds = ref<Thresholds>({ ...DEFAULT_THRESHOLDS })
const newMarker = reactive<{ elapsed: number | null; label: string }>({ elapsed: null, label: '' })
const exporting = ref(false)
const showExport = ref(false)
const exportOptions = ref<ExportOptions | null>(null)
const reportCard = ref<{ $el: HTMLElement } | null>(null)

async function exportImage(options: ExportOptions) {
  if (!summary.value) return
  showExport.value = false
  exportOptions.value = options
  exporting.value = true
  try {
    const { toPng } = await import('html-to-image')
    await nextTick()
    await new Promise((r) => setTimeout(r, 600)) // 等 ECharts 在畫面外容器完成第一次繪製
    const node = reportCard.value?.$el
    if (!node) throw new Error('報告卡尚未渲染')
    const dataUrl = await toPng(node, { pixelRatio: 2, backgroundColor: useTheme().chart.value.reportBg, cacheBust: true })
    const a = document.createElement('a')
    a.href = dataUrl
    a.download = `frameprobe-${summary.value.session_id}.png`
    a.click()
  } catch (e) {
    error.value = `匯出圖片失敗：${String(e)}`
  } finally {
    exporting.value = false
  }
}

const series = computed(() => {
  const out = [{ name: id, samples: samples.value }]
  for (const cid of compareIds.value) if (compareSamples.value[cid]) out.push({ name: cid, samples: compareSamples.value[cid]! })
  return out
})
// 溫度區的圖：只畫溫度線與節流色帶，不畫 FPS
const tempShow = computed<ChartShow>(() => ({
  ...DEFAULT_CHART_SHOW, fps: false, p90: false, jank: false, npu: summary.value?.peak_npu_c != null, fpsMin: false,
}))
// 頻率平均：新 session 由後端 summary 提供；舊 session 沒有這些欄位就從 samples 現算
const freqStats = computed(() => {
  const s = summary.value
  if (s?.avg_cpu_freq_mhz?.length || s?.avg_gpu_freq_mhz != null) {
    return { cpu: s.avg_cpu_freq_mhz ?? [], gpuAvg: s.avg_gpu_freq_mhz ?? null, gpuPeak: s.peak_gpu_freq_mhz ?? null }
  }
  const sums: number[] = []
  const counts: number[] = []
  for (const p of samples.value) p.cpu_freq_mhz?.forEach((f, i) => { if (f > 0) { sums[i] = (sums[i] ?? 0) + f; counts[i] = (counts[i] ?? 0) + 1 } })
  const gpu = samples.value.map((p) => p.gpu_freq_mhz).filter((v): v is number => !!v)
  return {
    cpu: sums.map((v, i) => v / (counts[i] ?? 1)),
    gpuAvg: gpu.length ? gpu.reduce((a, b) => a + b, 0) / gpu.length : null,
    gpuPeak: gpu.length ? Math.max(...gpu) : null,
  }
})
const peakMemDetail = computed(() => {
  const peak: Record<string, number> = {}
  for (const s of samples.value) for (const [k, v] of Object.entries(s.mem_detail_mb ?? {})) peak[k] = Math.max(peak[k] ?? 0, v)
  return Object.keys(peak).length ? peak : null
})
const mergedHistogram = computed(() => {
  const merged: Record<string, number> = {}
  for (const s of samples.value) for (const [k, v] of Object.entries(s.histogram)) merged[k] = (merged[k] ?? 0) + v
  return merged
})

// 三個叢集排成一行放不進最窄的卡片：小字級、一個叢集一行
const cpuFreqLines = computed(() => {
  const vals = clusterAvgText(freqStats.value.cpu, summary.value?.device.specs?.cpu_max_mhz).split(' / ')
  if (vals.length <= 1) return vals[0] === '-' || !vals[0] ? '-' : `${vals[0]} GHz`
  const names = clusterLabel(vals.length).replace('核', '').split(' / ')
  return vals.map((v, i) => `${names[i] ?? ''}核 ${v} GHz`).join('\n')
})

async function addMarker() {
  if (!summary.value || newMarker.elapsed === null) return
  try {
    const m = await api.post<{ elapsed: number; label: string }>(`/api/sessions/${id}/markers`, newMarker)
    const added: [number, string] = [m.elapsed, m.label]
    summary.value.markers = [...summary.value.markers, added].sort((a, b) => a[0] - b[0])
    newMarker.label = ''
  } catch (e) {
    error.value = String(e)
  }
}

watch(compareIds, async (ids) => {
  for (const cid of ids) {
    if (!compareSamples.value[cid]) compareSamples.value = { ...compareSamples.value, [cid]: await api.get<Sample[]>(`/api/sessions/${cid}/samples`) }
  }
})

onMounted(async () => {
  try {
    const res = await api.get<{ status: string; message: string; summary: SessionSummary | null }>(`/api/sessions/${id}`)
    status.value = res.status
    message.value = res.message
    samples.value = await api.get<Sample[]>(`/api/sessions/${id}/samples`)
    summary.value = res.summary ?? ({
      session_id: id, package: '', device: { manufacturer: '', model: '', android_release: '', specs: null }, mode: 'realtime',
      total_frames: 0, dropped_frames: 0, duration_s: 0, notes: [], throttle_duration_s: 0, throttle_timeline: [],
      average_fps: null, p90_fps: null, p99_fps: null, time_to_throttle_s: null,
      peak_cpu_c: null, peak_gpu_c: null, peak_skin_c: null, peak_npu_c: null, jank_breakdown: null,
      app_level_available: null, thermal_timeline: null, layer_name: '', markers: [],
      jank_total: 0, big_jank_total: 0, drop_fps: 0, delta_ftime: 0,
    } as unknown as SessionSummary)
    const all = await api.get<{ sessions: SessionSummary[] }>('/api/sessions')
    others.value = all.sessions.filter((s) => s.session_id !== id && s.package === summary.value?.package)
  } catch (e) {
    error.value = String(e)
  }
})
</script>
