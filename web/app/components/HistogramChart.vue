<template>
  <div>
    <div v-if="title" class="flex items-center gap-1 text-xs text-slate-400 mb-1">{{ title }} <Help k="histogram" /></div>
    <VChart :option="option" autoresize class="h-56! w-full" />
  </div>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { useTheme } from '~/composables/useTheme'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

const props = defineProps<{ histogram: Record<string, number>; title?: string }>()

const { chart } = useTheme()
const option = computed(() => {
  const c = chart.value
  // bucket 間距非等距，直接用解析出的 key 排序，不假設等距
  const keys = Object.keys(props.histogram).map(Number).sort((a, b) => a - b)
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    xAxis: { type: 'category', name: '幀間隔', data: keys.map((k) => `${k}ms`), axisLabel: { color: c.label } },
    yAxis: { type: 'value', name: 'frames', axisLabel: { color: c.label }, splitLine: { lineStyle: { color: c.grid } } },
    series: [{ type: 'bar', data: keys.map((k) => props.histogram[String(k)]), itemStyle: { color: '#38bdf8' } }],
  }
})
</script>
