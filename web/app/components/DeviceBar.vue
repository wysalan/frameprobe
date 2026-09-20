<template>
  <section class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-3">
    <header class="flex flex-wrap items-center gap-3">
      <h3 class="text-sm font-semibold text-slate-200">裝置</h3>
      <slot name="selector" />
      <span v-if="loading" class="text-xs text-slate-500">讀取裝置規格…</span>
      <span v-else-if="!specs && error" class="text-xs text-red-400">{{ error }}</span>
      <span v-else-if="!specs" class="text-xs text-slate-500">尚未取得裝置規格</span>
      <button v-if="specs" class="btn-ghost ml-auto text-xs" @click="open = !open">{{ open ? '收合' : '全部規格' }}</button>
    </header>
    <div v-if="serial" class="flex flex-wrap items-center gap-3">
      <h3 class="text-sm font-semibold text-slate-200">連線資訊</h3>
      <span class="text-xs text-slate-400 break-all">{{ serial }}</span>
      <span v-if="transport" class="text-xs rounded-full px-2 py-0.5 border" :class="transport === 'usb' ? 'border-emerald-700 text-emerald-300' : transport === 'wireless' ? 'border-sky-700 text-sky-300' : 'border-slate-700 text-slate-400'">
        {{ TRANSPORT_LABEL[transport] }}
      </span>
    </div>
    <DeviceSpecsGrid v-if="specs" :specs="specs" :all="open" />
  </section>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { ref } from 'vue'
import type { DeviceSpecs } from '~/composables/useApi'
import { TRANSPORT_LABEL } from '~/composables/useApi'

defineProps<{ specs: DeviceSpecs | null | undefined; loading?: boolean; error?: string; serial?: string; transport?: string }>()
const open = ref(false)
</script>
