<script setup lang="ts">
import { computed } from 'vue'
import type { ChartConfig } from '@/types/agent'

interface Props {
  config: ChartConfig
}

const props = defineProps<Props>()

const title = computed(() => {
  const t = props.config.title
  if (typeof t === 'string') return t
  if (t && typeof t === 'object' && 'text' in t) return t.text
  return '查询无数据'
})

const message = computed(() => props.config.message ?? '未找到符合条件的数据')
const hint = computed(() => props.config.hint ?? '')
</script>

<template>
  <section class="mt-6 overflow-hidden rounded-3xl border border-slate-200 bg-slate-50/80">
    <div class="flex flex-col items-center gap-3 px-5 py-8 text-center sm:px-6">
      <span class="inline-flex h-12 w-12 items-center justify-center rounded-full bg-slate-200/70 text-2xl">
        📭
      </span>
      <div class="space-y-1">
        <h4 class="text-sm font-semibold text-slate-700 sm:text-base">{{ title }}</h4>
        <p class="text-xs text-slate-500 sm:text-sm">{{ message }}</p>
      </div>
      <p v-if="hint" class="max-w-md text-xs text-slate-500 sm:text-sm">
        💡 {{ hint }}
      </p>
    </div>
  </section>
</template>
