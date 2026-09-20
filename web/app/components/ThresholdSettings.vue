<template>
  <details class="text-sm">
    <summary class="cursor-pointer text-slate-400"><Help k="thresholds" /> 門檻設定（FPS &lt; {{ model.fpsMin }}、SKIN &gt; {{ model.skinMax }}°C、Jank &gt; {{ model.jankMax }}/s、App CPU &gt; {{ model.cpuMax }}%）</summary>
    <div class="flex flex-wrap gap-3 mt-2">
      <label>FPS 下限 <input v-model.number="model.fpsMin" type="number" class="input w-20" @change="save" /></label>
      <label>SKIN 上限 °C <input v-model.number="model.skinMax" type="number" class="input w-20" @change="save" /></label>
      <label>Jank/s 上限 <input v-model.number="model.jankMax" type="number" class="input w-20" @change="save" /></label>
      <label>App CPU % 上限 <input v-model.number="model.cpuMax" type="number" class="input w-20" @change="save" /></label>
    </div>
  </details>
</template>

<script setup lang="ts">
// 明確從 vue 匯入：Nuxt 3.21 產生的 auto-import 型別指向 vue/index，該檔不存在會退化成 any
import { onMounted, reactive, watch } from 'vue'
import { DEFAULT_THRESHOLDS, loadThresholds, saveThresholds, type Thresholds } from '~/composables/useApi'

const emit = defineEmits<{ change: [Thresholds] }>()
const model = reactive<Thresholds>({ ...DEFAULT_THRESHOLDS })
onMounted(() => { Object.assign(model, loadThresholds()); emit('change', { ...model }) })
const save = () => { saveThresholds({ ...model }); emit('change', { ...model }) }
watch(model, () => emit('change', { ...model }))
</script>
