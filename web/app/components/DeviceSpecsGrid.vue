<template>
  <!-- 收合（或報告卡的固定欄數）只顯示摘要五張；展開後依類型分群，每群一行。規格是文字資訊，全部用 compact 字級 -->
  <div v-if="!all" class="metric-grid" :style="fixedCols ? { gridTemplateColumns: `repeat(${fixedCols}, minmax(0, 1fr))` } : undefined">
    <Stat v-for="c in summaryCards" :key="c.label" v-bind="c" compact />
  </div>
  <div v-else class="space-y-3">
    <div v-for="g in GROUPS" :key="g">
      <div class="text-xs text-slate-500 mb-1">{{ g }}</div>
      <div class="metric-grid">
        <Stat v-for="c in cards.filter((c) => c.group === g)" :key="c.label" v-bind="c" compact />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed } from 'vue'
import type { DeviceSpecs } from '~/composables/useApi'
import { cpuClusters } from '~/composables/useApi'

const props = defineProps<{ specs: DeviceSpecs; all?: boolean; fixedCols?: number }>()

const GROUPS = ['裝置', '處理器', '記憶體與儲存', '螢幕', '電池'] as const
interface Card { group: (typeof GROUPS)[number] | '摘要'; label: string; value: string; unit?: string; sub?: string; summary?: boolean }

/** 叢集由快到慢：大核心、中核心（多個時編號）、小核心；只有一個叢集就叫核心 */
function clusterName(i: number, n: number): string {
  if (n <= 1) return '核心'
  if (i === 0) return '大核心'
  if (i === n - 1) return '小核心'
  return n > 3 ? `中核心 ${i}` : '中核心'
}
const clusterText = (c: { cores: number[]; maxMhz: number }) => `${c.cores.length} × ${(c.maxMhz / 1000).toFixed(2)} GHz`
/** [4,5,6] → 'cpu4–6'；不連續就逐一列出 */
function coreRange(cores: number[]): string {
  const first = cores[0] ?? 0
  const last = cores.at(-1) ?? first
  const contiguous = last - first === cores.length - 1
  return contiguous ? (cores.length > 1 ? `cpu${first}–${last}` : `cpu${first}`) : cores.map((c) => `cpu${c}`).join('、')
}

/** 收合與報告卡的摘要列：固定這個順序，不受分群排列影響 */
const SUMMARY = ['型號', 'SoC / 平台', 'CPU', 'GPU', 'RAM', '系統']
const summaryCards = computed(() => SUMMARY.map((l) => cards.value.find((c) => c.summary && c.label === l)).filter((c) => c !== undefined))

const cards = computed<Card[]>(() => {
  const s = props.specs
  const battery = [
    s.battery_design_mah ? `設計容量 ${s.battery_design_mah} mAh` : '',
    s.battery_cycles !== null ? `循環 ${s.battery_cycles} 次` : '',
    s.battery_health_pct !== null ? `健康度 ${s.battery_health_pct}%` : '',
  ].filter(Boolean)
  const cores = s.cpu_max_mhz?.length ?? 0
  const clusters = cores ? cpuClusters(cores, s.cpu_max_mhz) : []
  const collectedAt = s.collected_at ? new Date(s.collected_at * 1000).toLocaleString() : ''
  return [
    { group: '裝置', label: '型號', value: s.display_name, sub: s.codename ? `代號 ${s.codename}` : '', summary: true },
    // 摘要列的 CPU：一個叢集一行，不寫總核心數（SoC 另有一張卡）
    { group: '摘要', label: 'CPU', value: clusters.map(clusterText).join('\n') || '-', summary: true },
    { group: '記憶體與儲存', label: 'RAM', value: s.ram_mb ? (s.ram_mb / 1024).toFixed(1) : '-', unit: s.ram_mb ? 'GB' : '', sub: s.ram_mb ? `${s.ram_mb} MB` : '', summary: true },
    { group: '裝置', label: '系統', value: s.android_release ? `Android ${s.android_release}` : '-', sub: [s.sdk_int ? `API ${s.sdk_int}` : '', s.security_patch ? `安全性更新 ${s.security_patch}` : ''].filter(Boolean).join(' · '), summary: true },
    { group: '裝置', label: 'Build', value: s.build_id || '-', sub: s.build_type },
    { group: '裝置', label: 'Kernel', value: s.kernel || '-' },
    { group: '裝置', label: 'SELinux / root', value: `${s.selinux || '-'} / ${s.is_rooted === null ? '-' : s.is_rooted ? '是' : '否'}` },
    ...(collectedAt ? [{ group: '裝置', label: '規格擷取時間', value: collectedAt } as Card] : []),
    { group: '處理器', label: 'SoC / 平台', value: s.soc || '-', sub: s.platform, summary: true },
    ...clusters.map((c, i): Card => ({
      group: '處理器', label: `CPU ${clusterName(i, clusters.length)}`, value: clusterText(c), sub: coreRange(c.cores),
    })),
    { group: '處理器', label: 'GPU', value: s.gpu_name || s.gpu_driver || '-', sub: s.gpu_name ? s.gpu_driver_version : s.gpu_driver ? '僅驅動名，此裝置無 GLES 資訊行' : '', summary: true },
    { group: '處理器', label: 'CPU 架構', value: s.abi || '-' },
    { group: '記憶體與儲存', label: '儲存', value: s.storage_total_gb ? `${s.storage_total_gb} GB` : '-', sub: s.storage_free_gb !== null ? `剩 ${s.storage_free_gb} GB` : '' },
    { group: '螢幕', label: '螢幕', value: s.screen_px || '-', sub: s.screen_dpi ? `${s.screen_dpi} dpi` : '' },
    { group: '螢幕', label: '螢幕更新率（目前）', value: s.refresh_rate_hz ? String(s.refresh_rate_hz) : 'unknown', unit: s.refresh_rate_hz ? 'Hz' : '', sub: s.arr ? `ARR：實際在 1～${s.refresh_rate_hz} Hz 之間變動` : s.refresh_rate_hz ? '固定模式' : '' },
    { group: '螢幕', label: '更新率（支援）', value: s.refresh_rates_hz.length ? s.refresh_rates_hz.join(' / ') : '-', unit: s.refresh_rates_hz.length ? 'Hz' : '' },
    { group: '電池', label: '電池', value: battery.length ? battery.join('，') : '無法讀取' },
  ]
})
</script>
