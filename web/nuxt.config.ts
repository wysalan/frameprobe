import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  ssr: false,
  nitro: { preset: 'static' },
  vite: { plugins: [tailwindcss()] },
  css: ['~/assets/css/main.css'],
  devtools: { enabled: false },
  runtimeConfig: {
    public: {
      // NUXT_PUBLIC_API_BASE 可覆寫；空字串代表與頁面同源（由 frameprobe serve 提供）
      apiBase: 'http://127.0.0.1:8420',
    },
  },
  app: {
    head: {
      title: 'frameprobe',
      // 在 app 載入前就套用主題，避免切換到淺色後每次重整先閃深色
      script: [{ innerHTML: "try{document.documentElement.dataset.theme=localStorage.getItem('frameprobe.theme')||(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark')}catch(e){}" }],
    },
  },
})
