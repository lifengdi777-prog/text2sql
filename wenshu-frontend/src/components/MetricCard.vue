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
  return ''
})

const metrics = computed(() => props.config.metrics ?? [])

function formatValue(value: string | number | null | undefined): string {
  if (value == null) return '-'
  if (typeof value === 'number') {
    // 大数字千分位分隔
    return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  }
  return String(value)
}

// 标签美化:把 SQL 列名翻译成更友好的展示
function prettifyLabel(label: string): string {
  const map: Record<string, string> = {
    actual_quantity: '实际产量',
    planned_quantity: '计划产量',
    qualified_quantity: '合格数量',
    defect_quantity: '不良数量',
    downtime_minutes: '停机时长',
    production_hours: '生产工时',
    qualified_rate: '合格率',
    defect_rate: '不良率',
    achievement_rate: '达成率',
    efficiency: '生产效率',
  }
  return map[label] || label
}
</script>

<template>
  <section class="mt-6 overflow-hidden rounded-3xl border border-slate-200 bg-gradient-to-br from-sky-50 to-white">
    <div class="border-b border-slate-200/70 bg-white/60 px-5 py-3">
      <h4 class="text-xs font-semibold text-slate-700 sm:text-sm">{{ title || '查询结果' }}</h4>
    </div>

    <div
      class="grid gap-4 p-5 sm:p-6"
      :class="metrics.length === 1 ? 'grid-cols-1' : metrics.length === 2 ? 'grid-cols-2' : 'grid-cols-3'"
    >
      <div
        v-for="(m, idx) in metrics"
        :key="`${idx}-${m.label}`"
        class="rounded-2xl border border-white bg-white/85 px-4 py-4 shadow-sm sm:px-5"
      >
        <p class="text-xs uppercase tracking-wider text-slate-400">
          {{ prettifyLabel(m.label) }}
        </p>
        <p class="mt-2 flex items-baseline gap-1">
          <span class="text-2xl font-bold text-slate-900 sm:text-3xl">{{ formatValue(m.value) }}</span>
          <span v-if="m.unit" class="text-sm font-medium text-slate-500">{{ m.unit }}</span>
        </p>
      </div>
    </div>
  </section>
</template>
