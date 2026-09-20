<template>
  <div class="fixed inset-0 z-40 m-0 bg-black/60 flex items-center justify-center p-4">
    <div class="w-full max-w-lg rounded-xl border border-slate-700 bg-slate-950 p-5 space-y-4 text-sm">
      <h3 class="text-base font-semibold">錄製已停止，選擇傳輸 trace 的連線</h3>
      <p class="text-slate-300">
        錄了 {{ Math.round(info.elapsed_s / 60) }} 分鐘，trace 約 <b>{{ info.trace_size_mb ?? '?' }} MB</b>。
        無線 adb 只有幾 MB/s，大檔建議接 USB 線。
      </p>
      <p v-if="info.error" class="text-red-400">上次傳輸失敗：{{ info.error }}</p>

      <!-- 第一步：選連線 -->
      <div v-if="step === 'choose'" class="space-y-2">
        <button v-for="d in info.options" :key="d.serial" class="w-full text-left btn-ghost flex items-center gap-2" @click="pick(d)">
          <span class="font-medium">{{ TRANSPORT_LABEL[d.transport ?? 'unknown'] }}</span>
          <span class="font-mono text-xs text-slate-500 ml-auto">{{ d.serial }}</span>
        </button>
        <button v-if="!info.options.some((d) => d.transport === 'usb')" class="w-full text-left btn-ghost flex items-center gap-2" @click="step = 'usb'">
          <span class="font-medium">改接 USB 線再傳</span>
        </button>
        <p class="text-xs text-slate-500">trace 會留在手機上直到傳輸成功；也可以之後用 `adb pull` 手動處理。</p>
      </div>

      <!-- 第二步：確認有線連線 -->
      <div v-else class="space-y-3">
        <div class="flex items-center gap-2">
          <span class="text-xs rounded-full px-2 py-0.5 border" :class="usb ? 'border-emerald-700 text-emerald-300' : 'border-slate-700 text-slate-400'">
            {{ usb ? `已偵測到 USB 連線（${usb.serial}）` : '尚未偵測到這支手機的 USB 連線…' }}
          </span>
          <span v-if="!usb" class="text-xs text-slate-500">每 2 秒重新檢查</span>
        </div>
        <ol class="list-decimal pl-5 text-slate-300 text-xs space-y-0.5">
          <li>用 USB 線接上手機，手機若跳出「允許 USB 偵錯」請按允許。</li>
          <li>上方變綠後按「確認執行」開始傳輸。</li>
        </ol>
        <div class="flex justify-end gap-2">
          <button class="btn-ghost" @click="step = 'choose'">返回</button>
          <button class="btn" :disabled="!usb || busy" @click="usb && confirm(usb)">{{ busy ? '傳輸中…' : '確認執行' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AdbDevice, TransferInfo } from '~/composables/useApi'
import { TRANSPORT_LABEL } from '~/composables/useApi'

const props = defineProps<{ info: TransferInfo; sessionId: string }>()
const emit = defineEmits<{ done: [] }>()
const api = useApi()
// 有上次錯誤（例如無線失敗）就直接進第二步
const step = ref<'choose' | 'usb'>(props.info.error ? 'usb' : 'choose')
const busy = ref(false)
const devices = ref<AdbDevice[]>([])
const sameDevice = (d: AdbDevice) => {
  const me = props.info.options.find((o) => o.serial === props.info.current_serial) ?? props.info.options[0]
  return !me || (d.model === me.model)
}
const usb = computed(() => devices.value.find((d) => d.state === 'device' && d.transport === 'usb' && sameDevice(d)))

let timer = 0
async function poll() {
  try { devices.value = await api.get<AdbDevice[]>('/api/devices') } catch {}
}
onMounted(() => { poll(); timer = window.setInterval(poll, 2000) })
onBeforeUnmount(() => clearInterval(timer))
watch(() => props.info.error, (e) => { if (e) { step.value = 'usb'; busy.value = false } })

async function confirm(d: AdbDevice) {
  busy.value = true
  try {
    await api.post(`/api/sessions/${props.sessionId}/transfer`, { serial: d.serial })
    emit('done')
  } catch {
    busy.value = false
  }
}
function pick(d: AdbDevice) {
  if (d.transport === 'usb') { step.value = 'usb'; return }
  confirm(d)
}
</script>
