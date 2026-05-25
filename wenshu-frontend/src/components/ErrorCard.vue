<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ChartConfig } from '@/types/agent'

interface Props {
  config: ChartConfig
}

const props = defineProps<Props>()
const showDetails = ref(false)

const title = computed(() => {
  const t = props.config.title
  if (typeof t === 'string') return t
  if (t && typeof t === 'object' && 'text' in t) return t.text
  return '查询失败'
})

const message = computed(() => props.config.message ?? '未知错误')
const hint = computed(() => props.config.hint ?? '')
const originalSql = computed(() => props.config.original_sql ?? null)
</script>

<template>
  <section class="mt-6 overflow-hidden rounded-3xl border border-rose-200 bg-rose-50/60">
    <div class="flex items-start gap-3 px-5 py-4 sm:px-6">
      <span class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-rose-500 text-base font-bold text-white">!</span>
      <div class="flex-1 space-y-2">
        <h4 class="text-sm font-semibold text-rose-700 sm:text-base">{{ title }}</h4>
        <p class="text-xs leading-6 text-rose-700/90 sm:text-sm">{{ message }}</p>
        <p v-if="hint" class="text-xs leading-6 text-slate-600 sm:text-sm">
          <span class="font-medium text-slate-700">建议:</span> {{ hint }}
        </p>

        <button
          v-if="originalSql"
          type="button"
          class="mt-1 inline-flex items-center gap-1 text-xs text-rose-600 hover:text-rose-800 sm:text-sm"
          @click="showDetails = !showDetails"
        >
          {{ showDetails ? '▲ 收起出错的 SQL' : '▼ 查看出错的 SQL' }}
        </button>

        <pre
          v-if="showDetails && originalSql"
          class="mt-2 max-h-48 overflow-auto rounded-xl bg-slate-900 px-3 py-2 text-[11px] leading-5 text-rose-200 sm:text-xs"
        >{{ originalSql }}</pre>
      </div>
    </div>
  </section>
</template>
