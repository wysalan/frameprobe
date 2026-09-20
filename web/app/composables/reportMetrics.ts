import type { SessionSummary } from '~/composables/useApi'
import { clusterAvgText, clusterLabel, cpuClusters, fmt } from '~/composables/useApi'

export interface MetricCell { value: string; unit?: string; sub?: string; warn?: boolean }
export interface ReportMetric { group: string; key: string; label: string; /** HELP 的鍵，預設同 key */ help?: string; get: (s: SessionSummary) => MetricCell }

/** 匯出圖可選的指標；group 對應 REPORT_SECTIONS 的 metricGroup，卡片顯示順序由使用者在匯出設定裡拖曳決定 */
export const REPORT_METRICS: ReportMetric[] = [
  { group: '幀率與卡頓', key: 'avg_fps', label: 'Avg FPS', get: (s) => ({ value: fmt(s.average_fps) }) },
  { group: '幀率與卡頓', key: 'low_1', label: '1% Low', get: (s) => ({ value: fmt(s.low_1_fps) }) },
  { group: '幀率與卡頓', key: 'p90', label: 'P90', get: (s) => ({ value: fmt(s.p90_fps) }) },
  { group: '幀率與卡頓', key: 'p99', label: 'P99', get: (s) => ({ value: fmt(s.p99_fps) }) },
  { group: '幀率與卡頓', key: 'jank', label: 'Jank / BigJank', get: (s) => ({ value: `${s.jank_total} / ${s.big_jank_total}`, sub: s.jank_method === 'histogram' ? '近似值' : '', warn: s.big_jank_total > 0 }) },
  { group: '幀率與卡頓', key: 'stutter', label: 'Stutter', get: (s) => ({ value: fmt(s.stutter_ratio === null ? null : s.stutter_ratio * 100, 2), unit: s.stutter_ratio === null ? '' : '%' }) },
  { group: '幀率與卡頓', key: 'fps_std', label: 'Std(FPS)', get: (s) => ({ value: fmt(s.fps_std, 1) }) },
  { group: '幀率與卡頓', key: 'drop_fps', label: 'Drop(FPS)', get: (s) => ({ value: String(s.drop_fps), warn: s.drop_fps > 0 }) },
  { group: '幀率與卡頓', key: 'dropped', label: '掉幀', get: (s) => ({ value: String(s.dropped_frames) }) },
  { group: '幀率與卡頓', key: 'total_frames', label: '總幀數', get: (s) => ({ value: String(s.total_frames) }) },
  { group: '溫度與節流', key: 'time_to_throttle', label: '開始節流', get: (s) => (s.time_to_throttle_s === null ? { value: '未節流' } : { value: fmt(s.time_to_throttle_s, 0), unit: 's 後', warn: true }) },
  { group: '溫度與節流', key: 'throttle_duration', label: '節流累計', get: (s) => ({ value: fmt(s.throttle_duration_s, 0), unit: 's' }) },
  { group: '溫度與節流', key: 'peak_skin', help: 'temp_skin', label: '峰值 SKIN', get: (s) => ({ value: fmt(s.peak_skin_c), unit: s.peak_skin_c === null ? '' : '°C' }) },
  { group: '溫度與節流', key: 'peak_cpu_temp', help: 'temp_cpu', label: '峰值 CPU 溫度', get: (s) => ({ value: fmt(s.peak_cpu_c), unit: s.peak_cpu_c === null ? '' : '°C' }) },
  { group: '溫度與節流', key: 'peak_gpu_temp', help: 'temp_gpu', label: '峰值 GPU 溫度', get: (s) => ({ value: fmt(s.peak_gpu_c), unit: s.peak_gpu_c === null ? '' : '°C' }) },
  { group: 'CPU、GPU 與記憶體', key: 'avg_cpu_app', help: 'cpu_app', label: 'App CPU 平均', get: (s) => ({ value: fmt(s.avg_cpu_app_pct, 0), unit: s.avg_cpu_app_pct === null ? '' : '%' }) },
  { group: 'CPU、GPU 與記憶體', key: 'peak_cpu_app', help: 'cpu_app', label: 'App CPU 峰值', get: (s) => ({ value: fmt(s.peak_cpu_app_pct, 0), unit: s.peak_cpu_app_pct === null ? '' : '%' }) },
  { group: 'CPU、GPU 與記憶體', key: 'avg_gpu', help: 'gpu_busy', label: 'GPU 平均', get: (s) => ({ value: fmt(s.avg_gpu_busy_pct, 0), unit: s.avg_gpu_busy_pct === null ? '' : '%' }) },
  { group: 'CPU、GPU 與記憶體', key: 'avg_cpu_freq', help: 'cpu_freq', label: 'CPU 頻率平均', get: (s) => ({ value: clusterAvgText(s.avg_cpu_freq_mhz, s.device.specs?.cpu_max_mhz), unit: s.avg_cpu_freq_mhz?.length ? 'GHz' : '', sub: s.avg_cpu_freq_mhz?.length ? clusterLabel(cpuClusters(s.avg_cpu_freq_mhz.length, s.device.specs?.cpu_max_mhz).length) : '' }) },
  { group: 'CPU、GPU 與記憶體', key: 'avg_gpu_freq', help: 'gpu_freq', label: 'GPU 頻率平均 / 峰值', get: (s) => ({ value: `${fmt(s.avg_gpu_freq_mhz, 0)} / ${fmt(s.peak_gpu_freq_mhz, 0)}`, unit: s.avg_gpu_freq_mhz == null ? '' : 'MHz' }) },
  { group: 'CPU、GPU 與記憶體', key: 'peak_mem', help: 'pss', label: '記憶體峰值', get: (s) => ({ value: fmt(s.peak_mem_pss_mb ?? s.peak_mem_rss_mb, 0), unit: (s.peak_mem_pss_mb ?? s.peak_mem_rss_mb) === null ? '' : 'MB', sub: s.peak_mem_pss_mb !== null ? 'PSS' : 'RSS' }) },
  { group: 'CPU、GPU 與記憶體', key: 'peak_gpu_mem', help: 'gpu_mem', label: 'GPU 記憶體峰值', get: (s) => ({ value: fmt(s.peak_gpu_mem_mb, 0), unit: s.peak_gpu_mem_mb === null ? '' : 'MB' }) },
  { group: '功耗', key: 'battery_start', help: 'battery_level', label: '開始電量', get: (s) => ({ value: fmt(s.battery_level_start ?? null, 0), unit: s.battery_level_start == null ? '' : '%' }) },
  { group: '功耗', key: 'battery_end', help: 'battery_level', label: '結束電量', get: (s) => ({ value: fmt(s.battery_level_end ?? null, 0), unit: s.battery_level_end == null ? '' : '%' }) },
  { group: '功耗', key: 'battery_charge', label: 'Charge counter', get: (s) => ({ value: fmt(s.battery_consumed_mah ?? null, 0), unit: s.battery_consumed_mah == null ? '' : 'mAh', sub: '開始 − 結束' }) },
  { group: '功耗', key: 'avg_power', help: 'power', label: '平均功耗', get: (s) => ({ value: fmt(s.avg_power_w, 2), unit: s.avg_power_w === null ? '' : 'W', sub: s.plugged_ratio === 1 ? '全程接電源，無有效值' : s.avg_power_w === null ? '無資料' : '未接電源時' }) },
  { group: '功耗', key: 'fpower', label: 'FPower', get: (s) => ({ value: fmt(s.avg_fpower_mw, 1), unit: s.avg_fpower_mw === null ? '' : 'mW/幀' }) },
]
export const METRIC_BY_KEY = Object.fromEntries(REPORT_METRICS.map((m) => [m.key, m])) as Record<string, ReportMetric>

/** 依 REPORT_METRICS 定義順序重排（只保留存在的鍵） */
export function sortByDefinition(keys: string[]): string[] {
  return REPORT_METRICS.map((m) => m.key).filter((k) => keys.includes(k))
}
/** 一列卡片的張數；區塊的圖表啟用時最多這麼多張，圖表停用（或本來就沒圖）就沒有上限，超過會換行 */
export const MAX_REPORT_METRICS = 7

/** FpsChart 可切換的線／標記 */
export interface ChartShow {
  /** FPS 圖開關 */
  fpsChart: boolean
  fps: boolean
  p90: boolean
  jank: boolean
  cpu: boolean
  gpu: boolean
  skin: boolean
  npu: boolean
  bands: boolean
  markers: boolean
  fpsMin: boolean
  /** 獨立溫度圖：開了才多一張只有溫度的圖，線由 temp* 決定 */
  tempChart: boolean
  tempCpu: boolean
  tempGpu: boolean
  tempSkin: boolean
  tempNpu: boolean
  /** 頻率圖開關：開了才多一張頻率圖 */
  freq: boolean
  cpuFreq: boolean
  gpuFreq: boolean
}
/** 報告區塊：每塊是「指標卡片 + 圖表」，功耗沒有圖。enable 是該塊圖表的開關；metricGroup 是可選卡片的來源（REPORT_METRICS 的 group） */
/** items：[ChartShow 鍵, 顯示文字, HELP 的鍵] */
export type SectionKey = 'fpsChart' | 'tempChart' | 'freq' | 'power'
export interface ReportSection { key: SectionKey; title: string; enable?: 'fpsChart' | 'tempChart' | 'freq'; metricGroup: string; items: [keyof ChartShow, string, string][] }
export const REPORT_SECTIONS: ReportSection[] = [
  {
    key: 'fpsChart',
    title: 'FPS 圖',
    enable: 'fpsChart',
    metricGroup: '幀率與卡頓',
    items: [
      ['fps', 'FPS', 'fps'], ['p90', 'P90', 'p90'], ['jank', 'Jank 點', 'jank'],
      ['cpu', 'CPU 溫度', 'temp_cpu'], ['gpu', 'GPU 溫度', 'temp_gpu'], ['skin', 'SKIN 溫度', 'temp_skin'], ['npu', 'NPU 溫度', 'temp_npu'],
      ['bands', '節流色帶', 'throttle'], ['markers', '場景標籤', 'markers'], ['fpsMin', 'FPS 門檻線', 'thresholds'],
    ],
  },
  { key: 'tempChart', title: '溫度圖', enable: 'tempChart', metricGroup: '溫度與節流', items: [['tempCpu', 'CPU', 'temp_cpu'], ['tempGpu', 'GPU', 'temp_gpu'], ['tempSkin', 'SKIN', 'temp_skin'], ['tempNpu', 'NPU', 'temp_npu']] },
  { key: 'freq', title: '頻率圖', enable: 'freq', metricGroup: 'CPU、GPU 與記憶體', items: [['cpuFreq', 'CPU 各核心', 'cpu_freq'], ['gpuFreq', 'GPU', 'gpu_freq']] },
  { key: 'power', title: '功耗', metricGroup: '功耗', items: [] },
]
/** 把一串指標鍵依所屬類型分到各區塊（預設值與舊版設定的轉換共用） */
export function distributeMetrics(keys: string[]): Record<SectionKey, string[]> {
  const out: Record<SectionKey, string[]> = { fpsChart: [], tempChart: [], freq: [], power: [] }
  for (const k of keys) {
    const sec = REPORT_SECTIONS.find((g) => g.metricGroup === METRIC_BY_KEY[k]?.group)
    if (sec) out[sec.key].push(k)
  }
  return out
}
export function defaultSectionMetrics(): Record<SectionKey, string[]> {
  return distributeMetrics(['avg_fps', 'low_1', 'time_to_throttle', 'peak_skin', 'avg_power'])
}
export const DEFAULT_CHART_SHOW: ChartShow = {
  fpsChart: true, fps: true, p90: false, jank: false, cpu: true, gpu: true, skin: true, npu: false, bands: true, markers: true, fpsMin: false,
  tempChart: false, tempCpu: true, tempGpu: true, tempSkin: true, tempNpu: false,
  freq: false, cpuFreq: true, gpuFreq: true,
}
/** 頁面上互動圖表用的預設：全部打開（NPU 除外） */
export const FULL_CHART_SHOW: ChartShow = { ...DEFAULT_CHART_SHOW, p90: true, jank: true, fpsMin: true }
/** 獨立溫度圖用的 show：只畫溫度線與節流色帶／場景標籤，不畫 FPS */
export function tempChartShow(c: ChartShow): ChartShow {
  return { ...c, fps: false, p90: false, jank: false, fpsMin: false, cpu: c.tempCpu, gpu: c.tempGpu, skin: c.tempSkin, npu: c.tempNpu }
}

export interface ExportOptions {
  deviceName: string
  /** 各區塊的指標卡片，陣列順序即顯示順序 */
  metrics: Record<SectionKey, string[]>
  chart: ChartShow
}
const KEY = 'frameprobe.export'
export function loadExportOptions(): ExportOptions {
  const base: ExportOptions = { deviceName: '', metrics: defaultSectionMetrics(), chart: { ...DEFAULT_CHART_SHOW } }
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return base
    const saved = JSON.parse(raw)
    // 舊版 metrics 是單一陣列（總覽一列），依類型分到各區塊
    const metrics = Array.isArray(saved.metrics) ? distributeMetrics(saved.metrics) : { ...base.metrics, ...(saved.metrics ?? {}) }
    return { deviceName: saved.deviceName ?? '', metrics, chart: { ...base.chart, ...(saved.chart ?? {}) } }
  } catch {
    return base
  }
}
export function saveExportOptions(o: ExportOptions) {
  try { localStorage.setItem(KEY, JSON.stringify(o)) } catch {}
}
