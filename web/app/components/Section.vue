<template>
  <section class="rounded-xl border border-slate-800 bg-slate-950/60 p-4 space-y-3">
    <header class="flex items-center gap-2">
      <h3 class="text-sm font-semibold text-slate-200">{{ title }}</h3>
      <Help :k="help" />
      <span v-if="note" class="text-xs text-slate-500">{{ note }}</span>
      <div class="ml-auto"><slot name="actions" /></div>
    </header>
    <slot />
  </section>
</template>

<script setup lang="ts">
defineProps<{ title: string; help?: string; note?: string }>()
</script>
