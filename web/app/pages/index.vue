<template>
  <div class="space-y-6">
    <DeviceBar :specs="dev.specs.value" :loading="dev.loading.value" :error="dev.error.value" :serial="serial" :transport="devices.find((d) => d.serial === serial)?.transport">
      <template #selector>
        <select v-model="serial" class="input" :class="{ 'text-slate-500': !devices.length }">
          <option v-if="!devices.length" :value="serial">未偵測到裝置</option>
          <option v-for="d in devices" :key="d.serial" :value="d.serial">{{ deviceLabel(d, devices) }}</option>
        </select>
        <button class="btn" :disabled="!serial || loading" @click="runDoctor">
          {{ loading ? '探測中…（約 5 秒）' : '執行診斷' }}
        </button>
        <button class="btn-ghost" @click="loadDevices">重新整理</button>
      </template>
    </DeviceBar>
    <span v-if="error" class="text-red-400 text-sm">{{ error }}</span>

    <p v-if="devices.length === 0" class="text-slate-500 text-sm">沒有偵測到裝置。請確認 adb 可用且已授權 USB 偵錯。</p>

    <section v-if="report" class="space-y-3">
      <h2 class="text-lg">
        能力探測
        <span v-if="report.device.is_rooted" class="text-amber-400 text-xs ml-2">rooted</span>
      </h2>
      <table class="w-full text-sm">
        <tbody>
          <template v-for="c in report.checks" :key="c.name">
            <tr class="border-t border-slate-800 cursor-pointer" @click="toggle(c.name)">
              <td class="py-2 w-12 text-xs" :class="{ 'text-emerald-300': c.status === 'pass', 'text-amber-300': c.status === 'warn', 'text-red-300': c.status === 'fail', 'text-slate-500': c.status === 'skip' }">{{ STATUS_LABEL[c.status] }}</td>
              <td class="py-2 w-48 font-mono">{{ c.name }}</td>
              <td class="py-2" :class="{ 'text-red-300': c.status === 'fail', 'text-amber-300': c.status === 'warn' }">
                {{ c.summary }}
                <span v-if="c.detail" class="text-slate-500 text-xs ml-2">{{ open.has(c.name) ? '▲' : '▼ 原始輸出' }}</span>
              </td>
            </tr>
            <tr v-if="c.detail && open.has(c.name)">
              <td></td>
              <td colspan="2"><pre class="text-xs bg-slate-900 p-3 rounded overflow-auto max-h-80 whitespace-pre-wrap">{{ c.detail }}</pre></td>
            </tr>
          </template>
        </tbody>
      </table>
      <div class="flex gap-6 text-sm">
        <span>即時模式：<b :class="ok('timestats') ? 'text-emerald-400' : 'text-red-400'">{{ ok('timestats') ? '可用' : '不可用' }}</b></span>
        <span>錄製模式：<b :class="recordOk ? 'text-emerald-400' : 'text-red-400'">{{ recordOk ? '可用' : '不可用' }}</b></span>
        <NuxtLink v-if="ok('timestats')" :to="{ path: '/watch', query: { serial } }" class="text-sky-400 underline">→ 開始即時監看</NuxtLink>
      </div>
      <p v-if="report.layer_candidates.length" class="text-xs text-slate-500">
        活躍圖層：{{ report.layer_candidates.slice(0, 6).join('、') }}
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { ProbeReport } from '~/composables/useApi'
import { deviceLabel, useDeviceSpecs } from '~/composables/useApi'
import type { AdbDevice } from '~/composables/useApi'

const api = useApi()
const devices = ref<AdbDevice[]>([])
const serial = ref<string>('')
const report = ref<ProbeReport | null>(null)
const loading = ref(false)
const error = ref('')
const open = reactive(new Set<string>())
const STATUS_LABEL = { pass: '通過', warn: '警告', fail: '失敗', skip: '略過' }
const dev = useDeviceSpecs()
watch(serial, (s) => dev.load(s))

async function loadDevices() {
  error.value = ''
  try {
    devices.value = await api.get('/api/devices')
    if (!serial.value && devices.value[0]) serial.value = devices.value[0].serial
  } catch (e) {
    error.value = String(e)
  }
}
async function runDoctor() {
  loading.value = true
  error.value = ''
  report.value = null
  try {
    report.value = await api.get(`/api/devices/${serial.value}/doctor`)
    if (report.value?.device.specs) dev.specs.value = report.value.device.specs
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}
const ok = (name: string) => ['pass', 'warn'].includes(report.value?.checks.find((c) => c.name === name)?.status ?? '')
const recordOk = computed(() => ok('perfetto_binary') && ok('frametimeline_supported'))
const toggle = (name: string) => (open.has(name) ? open.delete(name) : open.add(name))
onMounted(loadDevices)
</script>
