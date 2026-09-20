<template>
  <!-- 父層若是 space-y 容器會給子元素加 margin，fixed 元素的 inset-0 高度會被吃掉，故 m-0 -->
  <div class="fixed inset-0 z-40 m-0 bg-black/60 flex items-center justify-center p-4" @click.self="$emit('cancel')">
    <!-- 標題與按鈕列固定，只有中間設定區捲動 -->
    <div class="w-full max-w-2xl max-h-full flex flex-col rounded-xl border border-slate-700 bg-slate-950 text-sm">
      <h3 class="text-base font-semibold px-5 pt-5 pb-3">匯出圖片設定</h3>
      <div class="min-h-0 overflow-y-auto px-5 space-y-4">
      <label class="field">裝置名稱（顯示在套件名稱左側，可留空）
        <input v-model="opts.deviceName" class="input w-full" :placeholder="placeholder" />
      </label>

      <div class="space-y-2">
        <div class="flex items-center gap-2">
          <span class="text-slate-400">報告內容</span>
          <button class="btn-ghost text-xs ml-auto" @click="opts.metrics = defaultSectionMetrics()">指標卡片恢復預設</button>
        </div>
        <!-- 每個區塊＝指標卡片＋圖表內容；功耗沒有圖，只有卡片 -->
        <div v-for="g in REPORT_SECTIONS" :key="g.key" class="rounded-lg border border-slate-800 p-2 space-y-3">
          <div class="flex items-center gap-2">
            <span class="font-medium">{{ g.title }}</span>
            <button v-if="g.enable" class="btn-ghost text-xs ml-auto" :class="{ 'border-sky-600 text-sky-300': opts.chart[g.enable] }" @click="opts.chart[g.enable] = !opts.chart[g.enable]">
              {{ opts.chart[g.enable] ? `停用${g.title}` : `啟用${g.title}` }}
            </button>
          </div>
          <div class="space-y-1">
            <div class="text-xs text-slate-400">指標卡片
              <span :class="opts.metrics[g.key].length >= limitOf(g) ? 'text-orange-300' : 'text-slate-500'">
                已選 {{ opts.metrics[g.key].length }}{{ limitOf(g) === Infinity ? '' : ` / 最多 ${limitOf(g)}` }}
              </span>
            </div>
            <div class="flex flex-wrap gap-1">
              <!-- 不用 disabled：Chrome 對 disabled 按鈕不送滑鼠事件，裡面的 tooltip 會定不了位；改 aria-disabled 由 toggleMetric 擋 -->
              <button v-for="m in REPORT_METRICS.filter((m) => m.group === g.metricGroup)" :key="m.key" class="btn-ghost text-xs inline-flex items-center gap-1" :class="{ 'border-sky-600 text-sky-300': opts.metrics[g.key].includes(m.key), 'opacity-40': isFull(g, m.key) }" :aria-disabled="isFull(g, m.key)" @click="toggleMetric(g, m.key)">
                {{ m.label }}<Help :k="m.help ?? m.key" @click.stop />
              </button>
            </div>
            <!-- 已選項目：原生 HTML5 拖放排序，拖曳經過時就即時換位，順序即報告卡片順序；拖到空白處會排到最後 -->
            <template v-if="opts.metrics[g.key].length > 1">
              <div class="flex items-center gap-2 pt-1">
                <span class="text-xs text-slate-400">顯示順序</span>
                <span class="text-xs text-slate-500">拖曳調整</span>
              </div>
              <div class="flex items-center gap-2">
                <div class="flex flex-1 flex-wrap gap-1 rounded-lg border border-dashed border-slate-700 p-1" @dragover.prevent @dragenter.self="moveTo(g, null)" @drop.prevent>
                  <span v-for="k in opts.metrics[g.key]" :key="k" draggable="true" class="cursor-grab select-none rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs" :class="{ 'opacity-40 border-sky-600': dragging === k }" @dragstart="dragging = k" @dragend="dragging = null" @dragenter="moveTo(g, k)">
                    {{ METRIC_BY_KEY[k]?.label ?? k }}
                  </span>
                </div>
                <button class="btn-ghost text-xs whitespace-nowrap" @click="opts.metrics[g.key] = sortByDefinition(opts.metrics[g.key])">重設順序</button>
              </div>
            </template>
            <p v-if="opts.metrics[g.key].length > limitOf(g)" class="text-xs text-orange-300">圖表啟用時最多 {{ MAX_REPORT_METRICS }} 張，請先取消 {{ opts.metrics[g.key].length - MAX_REPORT_METRICS }} 張</p>
          </div>
          <div v-if="g.enable" class="space-y-1" :class="{ 'opacity-40': !opts.chart[g.enable] }">
            <div class="text-xs text-slate-400">圖表內容</div>
            <div class="flex flex-wrap gap-1">
              <button v-for="[key, label, help] in g.items" :key="key" class="btn-ghost text-xs inline-flex items-center gap-1" :class="{ 'border-sky-600 text-sky-300': opts.chart[key] }" :aria-disabled="!opts.chart[g.enable]" @click="toggleChart(g, key)">
                {{ label }}<Help :k="help" @click.stop />
              </button>
            </div>
          </div>
        </div>
      </div>
      </div>
      <div class="flex justify-end gap-2 px-5 pt-3 pb-5">
        <button class="btn-ghost" @click="$emit('cancel')">取消</button>
        <button class="btn" :disabled="empty || overLimit" @click="confirm">匯出</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed, reactive, ref } from 'vue'
import {
  MAX_REPORT_METRICS, METRIC_BY_KEY, REPORT_METRICS, REPORT_SECTIONS,
  defaultSectionMetrics, loadExportOptions, saveExportOptions, sortByDefinition, type ChartShow, type ExportOptions, type ReportSection,
} from '~/composables/reportMetrics'

defineProps<{ placeholder?: string }>()
const emit = defineEmits<{ cancel: []; confirm: [ExportOptions] }>()
const opts = reactive<ExportOptions>(loadExportOptions())

/** 區塊的圖表啟用時卡片最多一列；圖表停用或沒有圖就不限（超過會換行） */
function limitOf(g: ReportSection): number {
  return g.enable && opts.chart[g.enable] ? MAX_REPORT_METRICS : Infinity
}
function isFull(g: ReportSection, key: string): boolean {
  return !opts.metrics[g.key].includes(key) && opts.metrics[g.key].length >= limitOf(g)
}
// 先在圖表停用時勾了很多張，再把圖表打開就會超標：擋住匯出，請使用者減少
const overLimit = computed(() => REPORT_SECTIONS.some((g) => opts.metrics[g.key].length > limitOf(g)))
const empty = computed(() => REPORT_SECTIONS.every((g) => !opts.metrics[g.key].length && !(g.enable && opts.chart[g.enable])))

/** 圖表停用時項目只是灰掉，不切換 */
function toggleChart(g: ReportSection, key: keyof ChartShow) {
  if (g.enable && !opts.chart[g.enable]) return
  opts.chart[key] = !opts.chart[key]
}
function toggleMetric(g: ReportSection, key: string) {
  const cur = opts.metrics[g.key]
  if (cur.includes(key)) opts.metrics[g.key] = cur.filter((k) => k !== key)
  else if (cur.length < limitOf(g)) opts.metrics[g.key] = [...cur, key]
}

const dragging = ref<string | null>(null)
/** 拖曳經過 target 時即時把拖曳中的項目移到 target 的位置（由前往後放它後面、由後往前放它前面，游標下方才會一直是同一顆）；null 表示移到最後。拖到別的區塊時 from 不在 list 裡，不動作 */
function moveTo(g: ReportSection, target: string | null) {
  const from = dragging.value
  if (!from || from === target) return
  const list = [...opts.metrics[g.key]]
  const fi = list.indexOf(from)
  const ti = target ? list.indexOf(target) : list.length - 1
  if (fi < 0 || ti < 0 || fi === ti) return
  list.splice(fi, 1)
  list.splice(ti, 0, from)
  opts.metrics[g.key] = list
}

function confirm() {
  const result: ExportOptions = JSON.parse(JSON.stringify({ ...opts, deviceName: opts.deviceName.trim() }))
  saveExportOptions(result)
  emit('confirm', result)
}
</script>
