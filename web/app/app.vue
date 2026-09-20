<template>
  <div class="min-h-screen bg-slate-950 text-slate-100">
    <header class="border-b border-slate-800 px-6 py-3 flex items-center gap-6">
      <NuxtLink to="/" class="font-semibold tracking-wide">frameprobe</NuxtLink>
      <nav class="flex gap-4 text-sm text-slate-400">
        <NuxtLink to="/" class="hover:text-slate-100" active-class="text-slate-100">裝置 / 診斷</NuxtLink>
        <NuxtLink to="/watch" class="hover:text-slate-100" active-class="text-slate-100">即時</NuxtLink>
        <NuxtLink to="/sessions" class="hover:text-slate-100" active-class="text-slate-100">歷史</NuxtLink>
      </nav>
      <button class="btn-ghost ml-auto text-xs py-1" @click="toggle">{{ theme === 'dark' ? '淺色' : '深色' }}</button>
    </header>
    <main class="p-6 max-w-7xl mx-auto">
      <NuxtPage />
    </main>
  </div>
</template>

<script setup lang="ts">
const { theme, toggle } = useTheme()
</script>
