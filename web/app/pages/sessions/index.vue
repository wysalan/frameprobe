<template>
  <div class="space-y-4">
    <div v-if="running.length" class="text-sm text-slate-400">
      執行中：<span v-for="r in running" :key="r.session_id" class="mr-3">{{ r.package }} ({{ r.session_id }}) · {{ r.message || r.status }}</span>
    </div>
    <!-- 工具列：平常只有「移除」；按下後進入勾選模式，左「取消」右「確認」 -->
    <div class="flex items-center gap-3 text-sm">
      <template v-if="!selecting">
        <button class="btn-ghost text-xs" :disabled="!sessions.length" @click="selecting = true">移除</button>
        <Help k="remove_sessions" />
      </template>
      <template v-else>
        <button class="btn-ghost text-xs" :disabled="removing" @click="cancelSelect">取消</button>
        <label class="flex items-center gap-1 text-slate-400"><input type="checkbox" :checked="allSelected" :indeterminate="selected.size > 0 && !allSelected" @change="toggleAll" />全選</label>
        <span class="ml-auto inline-flex items-center gap-2">
          <Help k="remove_confirm" />
          <button class="btn text-xs" :class="{ '!bg-red-600 hover:!bg-red-500': armed }" :disabled="!selected.size || removing" @click="confirmRemove">
            {{ removing ? '移除中…' : armed ? `再按一次確認移除 ${selected.size} 筆` : `確認（${selected.size}）` }}
          </button>
        </span>
      </template>
    </div>
    <!-- 篩選：裝置 → 套件名稱／模式的選項會跟著縮到該裝置有的 -->
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <select v-model="filter.device" class="input">
        <option value="">全部裝置</option>
        <option v-for="d in deviceOptions" :key="d.key" :value="d.key">{{ d.label }}</option>
      </select>
      <select v-model="filter.pkg" class="input">
        <option value="">全部套件</option>
        <option v-for="p in pkgOptions" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="filter.mode" class="input">
        <option value="">全部模式</option>
        <option v-for="m in modeOptions" :key="m" :value="m">{{ m === 'record' ? '錄製' : '即時' }}</option>
      </select>
      <button v-if="filter.device || filter.pkg || filter.mode" class="btn-ghost text-xs" @click="filter.device = filter.pkg = filter.mode = ''">清除篩選</button>
      <span class="text-xs text-slate-500">{{ filtered.length }} / {{ sessions.length }} 筆</span>
    </div>
    <table class="w-full text-sm">
      <thead class="text-slate-400 text-left">
        <tr><th v-if="selecting" class="py-2 w-8"></th><th class="py-2">Session</th><th>裝置</th><th>套件名稱</th><th>模式</th><th>時長</th><th>Avg FPS</th><th>1% Low</th><th>開始節流</th><th>峰值 SKIN</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in filtered" :key="s.session_id" class="border-t border-slate-800 hover:bg-slate-900" :class="{ 'bg-slate-900/60': selected.has(s.session_id) }">
          <td v-if="selecting" class="py-2"><input type="checkbox" :checked="selected.has(s.session_id)" @change="toggle(s.session_id)" /></td>
          <td class="py-2 font-mono text-xs"><NuxtLink :to="`/sessions/${s.session_id}`" class="text-sky-400 underline" :title="s.session_id">{{ sessionTime(s.session_id) }}</NuxtLink></td>
          <td class="whitespace-nowrap">
            {{ deviceName(s) }}
            <span class="text-slate-500 text-xs">Android {{ s.device.android_release || '?' }}</span>
          </td>
          <td>{{ s.package }}</td>
          <td>{{ s.mode === 'record' ? '錄製' : '即時' }}</td>
          <td>{{ fmt(s.duration_s, 0, 's') }}</td>
          <td>{{ fmt(s.average_fps) }}</td>
          <td>{{ fmt(s.low_1_fps) }}</td>
          <td>{{ s.time_to_throttle_s === null ? '未節流' : fmt(s.time_to_throttle_s, 0, 's') }}</td>
          <td>{{ fmt(s.peak_skin_c, 1, '°C') }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="!sessions.length" class="text-slate-500 text-sm">尚無 Session。</p>
    <p v-else-if="!filtered.length" class="text-slate-500 text-sm">沒有符合篩選的 Session。</p>
    <p v-if="error" class="text-red-400 text-sm">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { RunningSession, SessionSummary } from '~/composables/useApi'
import { fmt, sessionTime } from '~/composables/useApi'

const api = useApi()
const sessions = ref<SessionSummary[]>([])
const running = ref<RunningSession[]>([])
const error = ref('')
const removing = ref(false)
const selecting = ref(false)
const armed = ref(false) // 確認鈕已按過一次，等第二次
const selected = reactive(new Set<string>())
function cancelSelect() {
  selecting.value = false
  armed.value = false
  selected.clear()
}
const allSelected = computed(() => filtered.value.length > 0 && filtered.value.every((s) => selected.has(s.session_id)))

// ---- 篩選 ----
const filter = reactive({ device: '', pkg: '', mode: '' })
const deviceName = (s: SessionSummary) =>
  s.device.specs?.display_name || `${s.device.manufacturer} ${s.device.model}`.trim() || s.device.serial
const uniq = (xs: string[]) => [...new Set(xs)].sort()
// 舊 session 沒存規格時是「廠牌 型號」組出來的，大小寫可能跟規格的 display_name 不同（Google vs google），比對一律小寫
const deviceKey = (s: SessionSummary) => deviceName(s).toLowerCase()
const deviceOptions = computed(() => {
  const seen = new Map<string, string>()
  for (const s of sessions.value) if (!seen.has(deviceKey(s))) seen.set(deviceKey(s), deviceName(s))
  return [...seen.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, label]) => ({ key, label }))
})
/** 選了裝置就只列該裝置有的套件與模式 */
const byDevice = computed(() => (filter.device ? sessions.value.filter((s) => deviceKey(s) === filter.device) : sessions.value))
const pkgOptions = computed(() => uniq(byDevice.value.map((s) => s.package)))
const modeOptions = computed(() => uniq(byDevice.value.map((s) => s.mode)))
const filtered = computed(() =>
  byDevice.value.filter((s) => (!filter.pkg || s.package === filter.pkg) && (!filter.mode || s.mode === filter.mode)))
// 換裝置後原本選的套件／模式不存在就清掉
watch(() => filter.device, () => {
  if (filter.pkg && !pkgOptions.value.includes(filter.pkg)) filter.pkg = ''
  if (filter.mode && !modeOptions.value.includes(filter.mode)) filter.mode = ''
})
function toggle(id: string) {
  armed.value = false // 勾選有變就重新確認
  if (selected.has(id)) selected.delete(id)
  else selected.add(id)
}
function toggleAll() {
  armed.value = false
  if (allSelected.value) selected.clear()
  else for (const s of filtered.value) selected.add(s.session_id)
}
/** 第一次按只「上膛」，第二次才真的移除；按住 Shift 直接移除 */
function confirmRemove(e: MouseEvent) {
  if (e.shiftKey || armed.value) return removeSelected()
  armed.value = true
}
async function load() {
  try {
    const res = await api.get<{ running: RunningSession[]; sessions: SessionSummary[] }>('/api/sessions')
    sessions.value = res.sessions
    running.value = res.running
  } catch (e) {
    error.value = String(e)
  }
}
/** 移除只是把資料夾搬到 sessions/removed/，不刪檔；要釋放空間得手動刪 */
async function removeSelected() {
  removing.value = true
  armed.value = false
  try {
    for (const id of [...selected]) {
      await api.post(`/api/sessions/${id}/remove`, {})
      selected.delete(id)
      sessions.value = sessions.value.filter((x) => x.session_id !== id)
    }
  } catch (e) {
    error.value = String(e)
  } finally {
    removing.value = false
    if (!selected.size) selecting.value = false
  }
}
onMounted(load)
</script>
