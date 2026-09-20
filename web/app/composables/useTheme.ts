import { computed, ref, watch } from 'vue'

export type Theme = 'dark' | 'light'
const KEY = 'frameprobe.theme'

// 起始值由 nuxt.config 的 inline script 在 app 載入前寫進 <html data-theme>（避免閃白／閃黑），這裡只接手
const theme = ref<Theme>(typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')
watch(theme, (t) => {
  document.documentElement.dataset.theme = t
  try { localStorage.setItem(KEY, t) } catch {}
})

// ECharts 畫在 canvas 上，吃不到 CSS 變數：軸標籤／圖例／格線顏色依主題各給一組
const CHART = {
  dark: { label: '#94a3b8', legend: '#cbd5e1', grid: '#1e293b', mark: '#e2e8f0', reportBg: '#020617' },
  light: { label: '#64748b', legend: '#334155', grid: '#e2e8f0', mark: '#1e293b', reportBg: '#f8fafc' },
} as const

export function useTheme() {
  return {
    theme,
    chart: computed(() => CHART[theme.value]),
    toggle: () => { theme.value = theme.value === 'dark' ? 'light' : 'dark' },
  }
}
