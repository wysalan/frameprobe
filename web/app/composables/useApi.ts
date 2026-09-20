export interface Sample {
  ts: number
  elapsed: number
  frames: number
  fps: number | null
  p90_fps: number | null
  p99_fps: number | null
  dropped: number
  dropped_ratio: number
  histogram: Record<string, number>
  cpu_c: number | null
  gpu_c: number | null
  battery_c: number | null
  skin_c: number | null
  npu_c: number | null
  throttling_status: number | null
  throttling_label: string | null
  thermal_stale: boolean
  cpu_total_pct: number | null
  cpu_app_pct: number | null
  cpu_app_norm_pct: number | null
  cpu_freq_mhz: number[]
  mem_pss_mb: number | null
  mem_rss_mb: number | null
  mem_swap_mb: number | null
  mem_available_mb: number | null
  gpu_busy_pct: number | null
  gpu_freq_mhz: number | null
  gpu_mem_mb: number | null
  wakeups: number | null
  cswitch: number | null
  net_rx_kbps: number | null
  net_tx_kbps: number | null
  mem_detail_mb: Record<string, number> | null
  mem_detail_stale: boolean
  battery_voltage_v: number | null
  battery_current_ma: number | null
  battery_power_w: number | null
  battery_plugged: boolean | null
  battery_level_pct?: number | null
  battery_charge_mah?: number | null
  battery_status?: number | null
  fpower_mw: number | null
  power_stale: boolean
  screenshot: string | null
  jank: number
  big_jank: number
  stutter_ms: number
  jank_method: 'exact' | 'histogram'
}

export interface AdbDevice {
  serial: string
  state: string
  model?: string
  transport?: 'usb' | 'wireless' | 'unknown'
}
// 選單只顯示型號；同型號同時走 USB 與無線時才補連線方式以便分辨。沒型號（未授權等）改用序號＋狀態
export function deviceLabel(d: AdbDevice, all: AdbDevice[]): string {
  if (!d.model) return `${d.serial} · ${d.state}`
  const dup = all.filter((x) => x.model === d.model).length > 1
  return dup ? `${d.model}（${d.transport === 'wireless' ? '無線' : d.transport === 'usb' ? 'USB' : d.serial}）` : d.model
}
export const TRANSPORT_LABEL: Record<string, string> = { usb: 'USB 有線', wireless: '無線（Wi-Fi 偵錯）', unknown: '連線方式未知' }

export interface DeviceSpecs {
  brand: string
  manufacturer: string
  model: string
  marketname: string
  codename: string
  android_release: string
  sdk_int: number | null
  security_patch: string
  build_id: string
  build_type: string
  soc: string
  platform: string
  abi: string
  cpu_cores: number | null
  cpu_max_mhz: number[]
  gpu_driver: string
  gpu_name: string
  gles_version: string
  gpu_driver_version: string
  ram_mb: number | null
  storage_total_gb: number | null
  storage_free_gb: number | null
  screen_px: string
  screen_dpi: number | null
  refresh_rate_hz: number | null
  refresh_rates_hz: number[]
  arr: boolean | null
  kernel: string
  selinux: string
  is_rooted: boolean | null
  battery_design_mah: number | null
  battery_cycles: number | null
  battery_health_pct: number | null
  collected_at: number
  display_name: string
  cpu_summary: string
  gpu_summary: string
  os_summary: string
}

export interface DeviceInfo {
  serial: string
  manufacturer: string
  model: string
  android_release: string
  sdk_int: number
  is_rooted: boolean
  specs: DeviceSpecs | null
  label?: string
}

export interface CheckResult {
  name: string
  status: 'pass' | 'warn' | 'fail' | 'skip'
  summary: string
  detail: string
}

export interface ProbeReport {
  device: DeviceInfo
  checks: CheckResult[]
  layer_candidates: string[]
}

export interface SessionSummary {
  session_id: string
  device: DeviceInfo
  package: string
  layer_name: string
  mode: 'realtime' | 'record'
  started_at: string
  duration_s: number
  total_frames: number
  average_fps: number | null
  p90_fps: number | null
  p99_fps: number | null
  dropped_frames: number
  jank_breakdown: Record<string, number> | null
  app_level_available: boolean | null
  peak_cpu_c: number | null
  peak_gpu_c: number | null
  peak_skin_c: number | null
  peak_npu_c: number | null
  time_to_throttle_s: number | null
  throttle_duration_s: number
  throttle_timeline: [number, number][]
  thermal_timeline: 'exact' | 'approximate' | null
  avg_cpu_app_pct: number | null
  peak_cpu_app_pct: number | null
  avg_cpu_total_pct: number | null
  peak_mem_pss_mb: number | null
  peak_mem_rss_mb: number | null
  avg_gpu_busy_pct: number | null
  peak_gpu_busy_pct: number | null
  avg_cpu_freq_mhz?: number[]
  avg_gpu_freq_mhz?: number | null
  peak_gpu_freq_mhz?: number | null
  peak_gpu_mem_mb: number | null
  avg_wakeups: number | null
  avg_cswitch: number | null
  avg_net_rx_kbps: number | null
  avg_net_tx_kbps: number | null
  avg_power_w: number | null
  avg_fpower_mw: number | null
  plugged_ratio: number | null
  battery_level_start?: number | null
  battery_level_end?: number | null
  battery_consumed_mah?: number | null
  battery_status?: number | null
  jank_total: number
  big_jank_total: number
  jank_per_10min: number | null
  big_jank_per_10min: number | null
  stutter_ratio: number | null
  jank_method: 'exact' | 'histogram' | 'mixed' | null
  low_1_fps: number | null
  fps_std: number | null
  ftime_avg_ms: number | null
  ftime_std_ms: number | null
  drop_fps: number
  delta_ftime: number
  markers: [number, string][]
  notes: string[]
}

export interface RunningSession {
  session_id: string
  serial: string | null
  package: string
  mode: string
  status: 'running' | 'stopped' | 'error' | 'awaiting_transfer'
  message: string
  started_at: string
  samples: number
  transfer?: TransferInfo | null
}

export interface TransferInfo {
  elapsed_s: number
  trace_size_mb: number | null
  error: string | null
  current_serial: string | null
  options: AdbDevice[]
}

export type WsMessage =
  | { type: 'sample'; data: Sample }
  | { type: 'marker'; data: { elapsed: number; label: string } }
  | { type: 'reset'; data: Record<string, never> }
  | { type: 'transfer'; data: TransferInfo }
  | { type: 'state'; data: { status: 'running' | 'stopped' | 'error' | 'awaiting_transfer'; message: string } }

export const THROTTLE_LABELS = ['NONE', 'LIGHT', 'MODERATE', 'SEVERE', 'CRITICAL', 'EMERGENCY', 'SHUTDOWN']

import { ref } from 'vue'

export function useApi() {
  const config = useRuntimeConfig()
  const apiBase = (config.public.apiBase as string) || (import.meta.client ? location.origin : '')

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(apiBase + path, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init?.headers || {}) },
    })
    if (!res.ok) {
      let detail = res.statusText
      try {
        detail = (await res.json()).detail ?? detail
      } catch {}
      throw new Error(`${res.status} ${detail}`)
    }
    return res.json() as Promise<T>
  }

  return {
    apiBase,
    get: <T>(path: string) => request<T>(path),
    post: <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
    del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
    wsUrl: (path: string) => apiBase.replace(/^http/, 'ws') + path,
    exportUrl: (id: string, fmt: 'csv' | 'json') => `${apiBase}/api/sessions/${id}/export?format=${fmt}`,
    shotUrl: (id: string, rel: string) => `${apiBase}/api/sessions/${id}/${rel}`,
  }
}

export const fmt = (v: number | null | undefined, digits = 1, suffix = '') =>
  v === null || v === undefined || Number.isNaN(v) ? '-' : `${v.toFixed(digits)}${suffix}`


export interface SceneStats {
  label: string
  start: number
  end: number
  avg_fps: number | null
  p90_fps: number | null
  jank: number
  big_jank: number
  avg_cpu_app_pct: number | null
  peak_skin_c: number | null
}

/** 依標籤把 samples 切段：[0, m1) 為「開始」，[m1, m2) 為 m1 的標籤，最後一段到結尾。 */
export function sceneStats(samples: Sample[], markers: [number, string][]): SceneStats[] {
  if (!samples.length) return []
  const sorted = [...markers].sort((a, b) => a[0] - b[0])
  const bounds: [number, string][] = [[0, '開始'], ...sorted]
  const end = samples[samples.length - 1]!.elapsed
  return bounds.map(([start, label], i) => {
    const stop = i + 1 < bounds.length ? bounds[i + 1]![0] : end + 1e-9
    const seg = samples.filter((s) => s.elapsed > start && s.elapsed <= stop)
    const frames = seg.reduce((n, s) => n + s.frames, 0)
    const hist: Record<number, number> = {}
    for (const s of seg) for (const [k, v] of Object.entries(s.histogram)) hist[Number(k)] = (hist[Number(k)] ?? 0) + v
    let cum = 0
    let p90: number | null = null
    for (const ms of Object.keys(hist).map(Number).sort((a, b) => a - b)) {
      cum += hist[ms]!
      if (cum >= frames * 0.9 && ms > 0) { p90 = 1000 / ms; break }
    }
    const cpu = seg.map((s) => s.cpu_app_pct).filter((v): v is number => v !== null)
    const skin = seg.map((s) => s.skin_c).filter((v): v is number => v !== null)
    const dur = stop - start
    return {
      label, start, end: Math.min(stop, end),
      avg_fps: dur > 0 && seg.length ? frames / (seg[seg.length - 1]!.elapsed - (seg[0]!.elapsed - (seg.length > 1 ? seg[1]!.elapsed - seg[0]!.elapsed : 1))) : null,
      p90_fps: p90,
      jank: seg.reduce((n, s) => n + s.jank, 0),
      big_jank: seg.reduce((n, s) => n + s.big_jank, 0),
      avg_cpu_app_pct: cpu.length ? cpu.reduce((a, b) => a + b, 0) / cpu.length : null,
      peak_skin_c: skin.length ? Math.max(...skin) : null,
    }
  }).filter((s) => s.end > s.start)
}

export const MEM_DETAIL_LABELS: [string, string][] = [
  ['java_heap', 'Java Heap'], ['native_heap', 'Native Heap'], ['code', 'Code'], ['stack', 'Stack'],
  ['graphics', 'Graphics'], ['gl_mtrack', 'GL mtrack'], ['egl_mtrack', 'EGL mtrack'],
  ['private_other', 'Private Other'], ['system', 'System'], ['total_pss', 'TOTAL PSS'], ['total_swap_pss', 'Swap PSS'],
]

export interface LaunchResult {
  package: string
  component: string
  cold: boolean
  status: string | null
  launch_state: string | null
  total_ms: number | null
  wait_ms: number | null
  runs: { total_ms: number; wait_ms: number | null; launch_state: string | null }[]
}

/** 門檻設定（每台瀏覽器各自記在 localStorage）。 */
export interface Thresholds {
  fpsMin: number
  skinMax: number
  jankMax: number
  cpuMax: number
}
export const DEFAULT_THRESHOLDS: Thresholds = { fpsMin: 30, skinMax: 45, jankMax: 1, cpuMax: 300 }
export function loadThresholds(): Thresholds {
  try {
    const raw = localStorage.getItem('frameprobe.thresholds')
    return raw ? { ...DEFAULT_THRESHOLDS, ...JSON.parse(raw) } : { ...DEFAULT_THRESHOLDS }
  } catch {
    return { ...DEFAULT_THRESHOLDS }
  }
}
export function saveThresholds(t: Thresholds) {
  try { localStorage.setItem('frameprobe.thresholds', JSON.stringify(t)) } catch {}
}
export interface Breach { metric: string; start: number; end: number; worst: number }
/** 把 samples 依門檻切成超標區間。 */
export function findBreaches(samples: Sample[], t: Thresholds): Breach[] {
  const checks: [string, (s: Sample) => boolean, (s: Sample) => number][] = [
    ['FPS 低於 ' + t.fpsMin, (s) => s.fps !== null && s.fps < t.fpsMin, (s) => s.fps ?? 0],
    ['SKIN 高於 ' + t.skinMax + '°C', (s) => s.skin_c !== null && s.skin_c > t.skinMax, (s) => s.skin_c ?? 0],
    ['Jank 超過 ' + t.jankMax + '/s', (s) => s.jank > t.jankMax, (s) => s.jank],
    ['App CPU 高於 ' + t.cpuMax + '%', (s) => s.cpu_app_pct !== null && s.cpu_app_pct > t.cpuMax, (s) => s.cpu_app_pct ?? 0],
  ]
  const out: Breach[] = []
  for (const [metric, hit, value] of checks) {
    let cur: Breach | null = null
    let prev = 0
    for (const s of samples) {
      if (hit(s)) {
        if (!cur) cur = { metric, start: prev, end: s.elapsed, worst: value(s) }
        cur.end = s.elapsed
        cur.worst = metric.startsWith('FPS') ? Math.min(cur.worst, value(s)) : Math.max(cur.worst, value(s))
      } else if (cur) { out.push(cur); cur = null }
      prev = s.elapsed
    }
    if (cur) out.push(cur)
  }
  return out.sort((a, b) => a.start - b.start)
}

/** 三個頁面共用：依 serial 取裝置規格，結果快取在記憶體（換裝置才重抓）。 */
const specsCache: Record<string, DeviceSpecs | null> = {}
export function useDeviceSpecs() {
  const api = useApi()
  const specs = ref<DeviceSpecs | null>(null)
  const loading = ref(false)
  const error = ref('')
  async function load(serial: string, force = false) {
    if (!serial) { specs.value = null; return }
    if (!force && serial in specsCache) { specs.value = specsCache[serial]!; return }
    loading.value = true
    error.value = ''
    try {
      const info = await api.get<DeviceInfo>(`/api/devices/${serial}/info`)
      specsCache[serial] = info.specs
      specs.value = info.specs
    } catch (e) {
      error.value = String(e)
      specs.value = null
    } finally {
      loading.value = false
    }
  }
  return { specs, loading, error, load }
}

/** session id 開頭是 YYYYMMDD-HHMMSS，只取日期時間顯示 */
export function sessionTime(id: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id)
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : id
}

export interface FreqCluster { maxMhz: number; cores: number[] }
/** 依各核心的 cpuinfo_max_freq 分叢集（大→小）。沒有規格時全部視為同一叢集。 */
export function cpuClusters(coreCount: number, maxMhz: number[] | undefined): FreqCluster[] {
  const groups = new Map<number, number[]>()
  for (let i = 0; i < coreCount; i++) {
    const m = maxMhz?.[i] ?? 0
    groups.set(m, [...(groups.get(m) ?? []), i])
  }
  return [...groups.entries()].sort((a, b) => b[0] - a[0]).map(([maxMhz, cores]) => ({ maxMhz, cores }))
}
/** 各叢集的平均頻率（GHz），大→小，例如 "2.6 / 2.1 / 1.5"；0（離線）不計 */
export function clusterAvgText(freqs: number[] | undefined, maxMhz: number[] | undefined): string {
  if (!freqs?.length) return '-'
  return cpuClusters(freqs.length, maxMhz).map((c) => {
    const vals = c.cores.map((i) => freqs[i] ?? 0).filter((v) => v > 0)
    return vals.length ? (vals.reduce((a, b) => a + b, 0) / vals.length / 1000).toFixed(1) : '-'
  }).join(' / ')
}
/** 叢集數對應的說明：1 → 全部核心、2 → 大 / 小核、3 → 大 / 中 / 小核 */
export function clusterLabel(n: number): string {
  if (n <= 1) return '全部核心'
  const names = n === 2 ? ['大', '小'] : ['大', ...Array<string>(n - 2).fill('中'), '小']
  return `${names.join(' / ')}核`
}

/** BatteryManager.BATTERY_STATUS_* 的中文標籤 */
export const BATTERY_STATUS: Record<number, string> = { 1: '未知', 2: '充電中', 3: '放電中', 4: '未充電', 5: '已充滿' }
