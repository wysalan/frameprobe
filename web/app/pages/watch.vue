<template>
  <div class="space-y-4">
    <DeviceBar :specs="dev.specs.value" :loading="dev.loading.value" :error="dev.error.value">
      <template #selector>
        <select v-model="form.serial" class="input" :class="{ 'text-slate-500': !devices.length }" :disabled="running">
          <option v-if="!devices.length" :value="form.serial">未偵測到裝置</option>
          <option v-for="d in devices" :key="d.serial" :value="d.serial">{{ deviceLabel(d, devices) }}</option>
        </select>
        <button class="btn-ghost" :disabled="running" @click="loadDevices">重新整理</button>
      </template>
    </DeviceBar>

    <!-- 控制列 -->
    <section class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-3">
      <div class="flex flex-wrap items-end gap-3">
        <label class="field"><span class="flex items-center gap-1">套件名稱 <Help k="package" /></span>
          <div class="flex gap-1">
            <input v-model="form.package" list="pkgs" class="input w-72 transition-colors duration-300" :class="detectState === 'ok' ? 'border-emerald-500! ring-1 ring-emerald-500/60' : detectState === 'fail' ? 'border-red-500! ring-1 ring-red-500/60' : ''" :disabled="running" placeholder="com.example.game" :title="detectState === 'fail' ? '偵測不到前景 App，請確認遊戲在前景且螢幕已解鎖' : ''" />
            <datalist id="pkgs"><option v-for="p in packages" :key="p" :value="p" /></datalist>
            <button class="btn-ghost whitespace-nowrap" :disabled="!form.serial || running || detecting" @click="detectForeground">
              {{ detecting ? '偵測中…' : '偵測前景 App' }}
            </button>
          </div>
        </label>
        <label class="field"><span class="flex items-center gap-1">layer <Help k="layer" /></span>
          <div class="flex gap-1">
            <select v-model="form.layer" class="input w-64" :disabled="running">
              <option value="">自動</option>
              <option v-for="l in layers" :key="l.layer_name" :value="l.layer_name">{{ l.layer_name }} ({{ l.total_frames }})</option>
            </select>
            <button class="btn-ghost whitespace-nowrap" :disabled="!form.package || running" @click="loadLayers">列出圖層</button>
          </div>
        </label>
        <span v-if="ambiguous" class="text-amber-300 text-xs pb-2">候選圖層幀數相近，請手動選 layer</span>
      </div>
      <div class="flex flex-wrap items-end gap-3">
        <label class="field"><span class="flex items-center gap-1">模式 <Help k="record_mode" /></span>
          <select v-model="form.mode" class="input" :disabled="running">
            <option value="realtime">即時（timestats）</option>
            <option value="record">錄製（Perfetto）</option>
          </select>
        </label>
        <label v-if="form.mode === 'realtime'" class="field">間隔(s)
          <input v-model.number="form.interval" type="number" min="0.5" step="0.5" class="input w-20" :disabled="running" />
        </label>
        <label v-else class="field">上限秒數（空白＝手動停）
          <input v-model.number="form.duration" type="number" min="1" step="5" class="input w-28" :disabled="running" placeholder="不限" />
        </label>
        <label class="field"><span class="flex items-center gap-1">截圖間隔(s) <Help k="screenshot" /></span>
          <input v-model.number="form.screenshotInterval" type="number" min="0" step="1" class="input w-20" :disabled="running" placeholder="關" />
        </label>
        <label class="flex items-center gap-1 pb-2 text-sm"><input v-model="form.thermal" type="checkbox" :disabled="running" />溫度</label>
        <div class="ml-auto flex items-center gap-2">
          <button class="btn-ghost" :disabled="!form.serial || !form.package || running || launching" @click="measureLaunch">
            {{ launching ? '啟動中…' : '測啟動時間' }}
          </button>
          <button v-if="!running" class="btn" :disabled="!form.serial || !form.package" @click="start">
            {{ form.mode === 'record' ? '開始錄製' : '開始' }}
          </button>
          <button v-else class="btn bg-red-600 hover:bg-red-500" :disabled="stopping || !!transfer" @click="stop">
            {{ stopping ? '停止中…' : (form.mode === 'record' ? '停止錄製並分析' : '停止') }}
          </button>
        </div>
      </div>
      <div class="flex flex-wrap gap-4 text-xs">
        <span v-if="launch" class="text-slate-300 flex items-center gap-1">
          啟動 {{ launch.launch_state || '' }} 第一幀 <b>{{ launch.total_ms }} ms</b>（WaitTime {{ launch.wait_ms ?? '-' }} ms）<Help k="launch" />
        </span>
        <span v-if="state" class="text-slate-400">{{ state }}</span>
        <span v-if="error" class="text-red-400">{{ error }}</span>
        <span v-if="attachable.length && !running" class="text-slate-400">
          執行中的 Session：
          <button v-for="r in attachable" :key="r.session_id" class="underline mr-2" @click="attach(r)">{{ r.package }}</button>
        </span>
        <span v-if="sessionId" class="text-slate-500 ml-auto">
          session <span class="font-mono">{{ sessionId }}</span> ·
          <NuxtLink :to="`/sessions/${sessionId}`" class="underline">報告</NuxtLink>
        </span>
      </div>
      <p v-if="form.mode === 'record' && sessionId" class="text-xs text-slate-400">
        {{ running ? '錄製中：畫面為 timestats 即時資料，停止後會以 Perfetto trace 的精確分析取代。' : '已停止：畫面為 trace 分析結果。' }}
      </p>
    </section>

    <TransferDialog v-if="transfer" :info="transfer" :session-id="sessionId" @done="transfer = null" />

    <ThresholdSettings @change="(t: Thresholds) => (thresholds = t)" />

    <!-- 幀率 -->
    <Section title="幀率" :note="latest ? `第 ${latest.elapsed.toFixed(0)} 秒` : ''">
      <div class="metric-grid">
        <Stat label="FPS" :value="fmt(latest?.fps)" help="fps" :warn="!!latest && latest.fps !== null && latest.fps < thresholds.fpsMin" />
        <Stat label="P90" :value="fmt(latest?.p90_fps)" help="p90" />
        <Stat label="P99" :value="fmt(latest?.p99_fps)" help="p99" />
        <Stat label="掉幀率" :value="fmt(latest ? latest.dropped_ratio * 100 : null, 1)" unit="%" help="dropped" />
        <Stat :label="latest?.jank_method === 'exact' ? 'Jank / BigJank' : 'Jank / BigJank（近似）'" :value="latest ? `${latest.jank} / ${latest.big_jank}` : '-'" help="jank" :warn="!!latest && latest.jank > thresholds.jankMax" />
        <Stat label="累計 Jank / Big" :value="`${jankTotal} / ${bigJankTotal}`" help="jank_total" />
      </div>
      <FpsChart :series="[{ name: sessionId || 'live', samples }]" :window="running ? 120 : undefined" :markers="markers" :fps-min="thresholds.fpsMin" title="FPS 與溫度" />
      <HistogramChart :histogram="latest?.histogram ?? {}" title="幀間隔分布（最近一秒）" />
    </Section>

    <!-- 溫度 -->
    <Section title="溫度與節流" help="throttle" :note="latest?.thermal_stale ? '沿用上次讀數' : ''">
      <div class="metric-grid">
        <Stat label="CPU" :value="fmt(latest?.cpu_c)" unit="°C" help="temp_cpu" :dim="latest?.thermal_stale" />
        <Stat label="GPU" :value="fmt(latest?.gpu_c)" unit="°C" help="temp_gpu" :dim="latest?.thermal_stale" />
        <Stat label="SKIN" :value="fmt(latest?.skin_c)" unit="°C" help="temp_skin" :dim="latest?.thermal_stale" :warn="!!latest && latest.skin_c !== null && latest.skin_c > thresholds.skinMax" />
        <Stat v-if="latest?.npu_c !== null && latest?.npu_c !== undefined" label="NPU" :value="fmt(latest?.npu_c)" unit="°C" help="temp_npu" :dim="latest?.thermal_stale" />
        <Stat label="電池" :value="fmt(latest?.battery_c)" unit="°C" :dim="latest?.thermal_stale" />
        <Stat label="節流狀態" :value="latest?.throttling_label ?? 'unknown'" help="throttle" :warn="(latest?.throttling_status ?? 0) >= 2" />
      </div>
      <p v-if="thermalDisabled" class="text-xs text-amber-300">溫度採集已停用（連續失敗）。</p>
    </Section>

    <!-- CPU / GPU -->
    <Section title="CPU 與 GPU" help="sys_chart">
      <div class="metric-grid">
        <Stat label="App CPU" :value="fmt(latest?.cpu_app_pct, 0)" unit="%" help="cpu_app" :warn="!!latest && latest.cpu_app_pct !== null && latest.cpu_app_pct > thresholds.cpuMax" />
        <Stat label="App CPU 正規化" :value="fmt(latest?.cpu_app_norm_pct, 0)" unit="%" help="cpu_norm" />
        <Stat label="整機 CPU" :value="fmt(latest?.cpu_total_pct, 0)" unit="%" help="cpu_total" />
        <Stat label="CPU 頻率" :value="clusterAvgText(latest?.cpu_freq_mhz, dev.specs.value?.cpu_max_mhz)" :unit="latest?.cpu_freq_mhz?.length ? 'GHz' : ''" :sub="latest?.cpu_freq_mhz?.length ? clusterLabel(cpuClusters(latest.cpu_freq_mhz.length, dev.specs.value?.cpu_max_mhz).length) : ''" help="cpu_freq" />
        <Stat label="GPU 使用率" :value="fmt(latest?.gpu_busy_pct, 0)" unit="%" help="gpu_busy" />
        <Stat label="GPU 頻率" :value="fmt(latest?.gpu_freq_mhz, 0)" unit="MHz" help="gpu_freq" />
        <Stat label="GPU 記憶體" :value="fmt(latest?.gpu_mem_mb, 0)" unit="MB" help="gpu_mem" />
        <Stat label="Wakeups / CSwitch" :value="`${latest?.wakeups ?? '-'} / ${latest?.cswitch ?? '-'}`" help="cswitch" />
      </div>
      <SysChart :samples="samples" :window="running ? 120 : undefined" title="CPU / GPU / 記憶體 / 功耗" />
      <FreqChart :samples="samples" :max-mhz="dev.specs.value?.cpu_max_mhz" :window="running ? 120 : undefined" title="CPU / GPU 頻率" />
    </Section>

    <!-- 記憶體 -->
    <Section title="記憶體" help="mem_detail" :note="latest?.mem_detail_stale ? 'Memory Detail 沿用上次' : ''">
      <div class="metric-grid">
        <Stat v-if="!latest || latest.mem_pss_mb !== null" label="PSS" :value="fmt(latest?.mem_pss_mb, 0)" unit="MB" help="pss" />
        <Stat v-else label="RSS（PSS 不可讀）" :value="fmt(latest.mem_rss_mb, 0)" unit="MB" help="rss" />
        <Stat label="Swap" :value="fmt(latest?.mem_swap_mb, 0)" unit="MB" help="swap" />
        <Stat label="整機可用" :value="fmt(latest?.mem_available_mb, 0)" unit="MB" help="mem_available" />
        <template v-if="latest?.mem_detail_mb">
          <Stat v-for="[key, label] in MEM_DETAIL_LABELS.filter(([k]) => k !== 'total_pss' && k !== 'total_swap_pss')" :key="key" :label="label" :value="fmt(latest.mem_detail_mb[key], 0)" unit="MB" :dim="latest.mem_detail_stale" />
        </template>
      </div>
    </Section>

    <!-- 功耗與電池 -->
    <Section title="功耗與電池" :note="latest?.battery_plugged ? '接著電源：功耗為淨充電值，不代表整機耗電' : ''">
      <div class="metric-grid">
        <Stat label="電池狀態" :value="latest?.battery_status == null ? '-' : BATTERY_STATUS[latest.battery_status] ?? String(latest.battery_status)" help="battery_status" />
        <Stat label="開始電量" :value="fmt(startLevel, 0)" :unit="startLevel == null ? '' : '%'" help="battery_level" />
        <Stat label="結束電量" :value="fmt(latest?.battery_level_pct ?? null, 0)" :unit="latest?.battery_level_pct == null ? '' : '%'" help="battery_level" />
        <Stat label="功耗" :value="fmt(latest?.battery_power_w, 2)" unit="W" help="power" :warn="!!latest?.battery_plugged" :dim="latest?.power_stale" />
        <Stat label="FPower" :value="fmt(latest?.fpower_mw, 1)" unit="mW/幀" help="fpower" :dim="latest?.power_stale" />
      </div>
    </Section>

    <!-- 場景 / 超標 / 截圖 -->
    <Section v-if="running || samples.length" title="場景標籤與超標" help="markers">
      <div v-if="running" class="flex items-center gap-2 text-sm">
        <input v-model="markerLabel" class="input w-56" placeholder="場景名稱（例：boss 戰）" @keyup.enter="addMarker" />
        <button class="btn-ghost" :disabled="!markerLabel" @click="addMarker">標記場景</button>
        <span v-for="m in markers" :key="m[0]" class="text-slate-400">{{ m[1] }}@{{ m[0].toFixed(0) }}s</span>
      </div>
      <SceneTable :samples="samples" :markers="markers" />
      <BreachList v-if="samples.length" :breaches="findBreaches(samples, thresholds)" />
      <Screenshots v-if="sessionId" :session-id="sessionId" :samples="samples" />
    </Section>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { LaunchResult, RunningSession, Sample, Thresholds, TransferInfo, WsMessage } from '~/composables/useApi'
import { deviceLabel, useDeviceSpecs } from '~/composables/useApi'
import type { AdbDevice } from '~/composables/useApi'
import { BATTERY_STATUS, DEFAULT_THRESHOLDS, MEM_DETAIL_LABELS, clusterAvgText, clusterLabel, cpuClusters, findBreaches, fmt } from '~/composables/useApi'

const api = useApi()
const route = useRoute()
const devices = ref<AdbDevice[]>([])
const packages = ref<string[]>([])
const layers = ref<{ layer_name: string; total_frames: number }[]>([])
const ambiguous = ref(false)
const attachable = ref<RunningSession[]>([])
const form = reactive({
  serial: String(route.query.serial ?? ''), package: '', mode: 'realtime' as 'realtime' | 'record',
  interval: 1, duration: null as number | null, layer: '', thermal: true,
  screenshotInterval: null as number | null,
})
const thresholds = ref<Thresholds>({ ...DEFAULT_THRESHOLDS })
const dev = useDeviceSpecs()
const launch = ref<LaunchResult | null>(null)
const launching = ref(false)
const detecting = ref(false)
// 偵測結果只用輸入框邊框顏色提示：綠＝成功、紅＝失敗，3 秒後恢復
const detectState = ref<'' | 'ok' | 'fail'>('')
let detectTimer = 0
function flashDetect(state: 'ok' | 'fail') {
  detectState.value = state
  clearTimeout(detectTimer)
  detectTimer = window.setTimeout(() => (detectState.value = ''), 3000)
}
async function detectForeground() {
  detecting.value = true
  try {
    const res = await api.get<{ foreground: string | null; packages: string[] }>(`/api/devices/${form.serial}/packages`)
    packages.value = res.packages
    if (res.foreground) {
      form.package = res.foreground
      flashDetect('ok')
    } else {
      flashDetect('fail')
    }
  } catch {
    flashDetect('fail')
  } finally {
    detecting.value = false
  }
}
const stopping = ref(false)
const transfer = ref<TransferInfo | null>(null)

const samples = ref<Sample[]>([])
const latest = ref<Sample | null>(null)
// 開始電量：samples 會被截到 MAX_POINTS，所以另外記；samples 清空（新 session／reset）時一併歸零
const startLevel = ref<number | null>(null)
watch(samples, (v) => { if (!v.length) startLevel.value = null })
const sessionId = ref('')
const running = ref(false)
const state = ref('')
const error = ref('')
const MAX_POINTS = 600
const markers = ref<[number, string][]>([])
const markerLabel = ref('')

// WebSocket 進來的資料先進 buffer，用 requestAnimationFrame 批次 flush，不每筆觸發 reactive 更新
let ws: WebSocket | null = null
let buffer: Sample[] = []
let raf = 0
function flush() {
  raf = 0
  if (!buffer.length) return
  const next = samples.value.concat(buffer)
  samples.value = next.length > MAX_POINTS ? next.slice(-MAX_POINTS) : next
  startLevel.value ??= buffer.find((b) => b.battery_level_pct != null)?.battery_level_pct ?? null
  latest.value = buffer[buffer.length - 1] ?? null
  buffer = []
}
function onMessage(ev: MessageEvent) {
  const msg = JSON.parse(ev.data) as WsMessage
  if (msg.type === 'sample') {
    buffer.push(msg.data)
    if (!raf) raf = requestAnimationFrame(flush)
  } else if (msg.type === 'marker') {
    markers.value = [...markers.value, [msg.data.elapsed, msg.data.label]]
  } else if (msg.type === 'transfer') {
    transfer.value = msg.data
    stopping.value = false
  } else if (msg.type === 'reset') {
    // record 模式：trace 分析完成，丟掉即時資料，接下來收到的是精確的逐秒結果
    buffer = []
    samples.value = []
    latest.value = null
  } else {
    state.value = msg.data.message
    if (msg.data.status === 'awaiting_transfer') return
    if (msg.data.status !== 'running') {
      running.value = false
      stopping.value = false
      transfer.value = null
      if (msg.data.status === 'error') error.value = msg.data.message
      ws?.close()
    }
  }
}
function connect(id: string) {
  ws?.close()
  ws = new WebSocket(api.wsUrl(`/ws/sessions/${id}?replay=120`))
  ws.onmessage = onMessage
  ws.onclose = () => { if (running.value) setTimeout(() => running.value && connect(id), 1000) }
}

const jankTotal = computed(() => samples.value.reduce((n, s) => n + s.jank, 0))
const bigJankTotal = computed(() => samples.value.reduce((n, s) => n + s.big_jank, 0))
const thermalDisabled = computed(() => samples.value.length > 5 && samples.value.slice(-5).every((s) => s.cpu_c === null && s.thermal_stale))

async function start() {
  error.value = ''
  samples.value = []
  latest.value = null
  markers.value = []
  try {
    const run = await api.post<RunningSession>('/api/sessions', {
      serial: form.serial, package: form.package, mode: form.mode,
      interval: form.interval, duration: form.mode === 'record' ? form.duration || null : null,
      layer: form.layer || null, thermal: form.thermal,
      screenshot_interval: form.screenshotInterval || null,
    })
    sessionId.value = run.session_id
    running.value = true
    connect(run.session_id)
  } catch (e) {
    error.value = String(e)
  }
}
async function stop() {
  stopping.value = true
  try {
    await api.del(`/api/sessions/${sessionId.value}`)
  } catch (e) {
    error.value = String(e)
    stopping.value = false
  }
}
function attach(r: RunningSession) {
  sessionId.value = r.session_id
  form.package = r.package
  form.mode = r.mode === 'record' ? 'record' : 'realtime'
  samples.value = []
  running.value = true
  if (r.transfer) transfer.value = r.transfer
  connect(r.session_id)
}
async function addMarker() {
  try {
    await api.post(`/api/sessions/${sessionId.value}/markers`, { label: markerLabel.value })
    markerLabel.value = ''
  } catch (e) {
    error.value = String(e)
  }
}
async function measureLaunch() {
  launching.value = true
  error.value = ''
  try {
    launch.value = await api.post<LaunchResult>(`/api/devices/${form.serial}/launch`, { package: form.package, cold: true })
  } catch (e) {
    error.value = String(e)
  } finally {
    launching.value = false
  }
}
async function loadLayers() {
  error.value = ''
  try {
    const res = await api.get<{ ambiguous: boolean; candidates: typeof layers.value }>(
      `/api/devices/${form.serial}/layers?package=${encodeURIComponent(form.package)}`)
    layers.value = res.candidates
    ambiguous.value = res.ambiguous
  } catch (e) {
    error.value = String(e)
  }
}
watch(() => form.serial, async (s) => {
  if (!s) return
  dev.load(s)
  try {
    const res = await api.get<{ foreground: string | null; packages: string[] }>(`/api/devices/${s}/packages`)
    packages.value = res.packages
    if (!form.package && res.foreground) form.package = res.foreground
  } catch {}
})
async function loadDevices() {
  error.value = ''
  try {
    devices.value = await api.get('/api/devices')
    if (!form.serial && devices.value[0]) form.serial = devices.value[0].serial
  } catch (e) {
    error.value = String(e)
  }
}
onMounted(async () => {
  await loadDevices()
  try {
    attachable.value = (await api.get<{ running: RunningSession[] }>('/api/sessions')).running
  } catch (e) {
    error.value = String(e)
  }
})
onBeforeUnmount(() => { running.value = false; ws?.close(); if (raf) cancelAnimationFrame(raf) })
</script>
