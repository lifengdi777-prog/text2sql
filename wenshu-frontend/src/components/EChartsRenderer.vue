<script setup lang="ts">
/**
 * 通用 ECharts 渲染器。
 *
 * 设计思路:
 * - 后端 chart_config 本身就是合法的 ECharts option(我们在 templates/*.py 里就是按 ECharts option 写的)
 * - 前端只需要剥离我们自己加的元字段(chart_type 等),把剩下的喂给 VChart
 * - 一个组件搞定 line / bar / pie / multi_line / stacked_bar 5 种图表
 *
 * 为什么不写 5 个独立组件:
 * - 每种图表的差异已经在后端 templates/*.py 里处理掉了
 * - 前端无需重复"线 vs 柱 vs 饼"的判断逻辑,保持单一职责
 */
import { computed } from 'vue'
import VChart from 'vue-echarts'

// ECharts 按需注册(避免全量 bundle)
import { use } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DatasetComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { ChartConfig } from '@/types/agent'

use([
  CanvasRenderer,
  BarChart,
  LineChart,
  PieChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DatasetComponent,
])

interface Props {
  config: ChartConfig
}

const props = defineProps<Props>()

// 把 chart_config 转成 ECharts option:剥离我们自己加的元字段
const option = computed(() => {
  // chart_type / metrics / message / hint / original_sql / row_count / columns / rows
  // 这些都是我们自己的元字段,不是 ECharts option,要排除
  const meta = new Set([
    'chart_type',
    'metrics',
    'message',
    'hint',
    'original_sql',
    'row_count',
    'columns',
    'rows',
    '_fallback_reason',
  ])
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(props.config)) {
    if (!meta.has(key)) {
      result[key] = value
    }
  }
  return result
})

const title = computed(() => {
  const t = props.config.title
  if (typeof t === 'string') return t
  if (t && typeof t === 'object' && 'text' in t) return String(t.text)
  return ''
})
</script>

<template>
  <section class="mt-6 overflow-hidden rounded-3xl border border-slate-200 bg-white">
    <div
      class="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-3"
    >
      <h4 class="text-xs font-semibold text-slate-700 sm:text-sm">
        {{ title || '查询结果' }}
      </h4>
      <span
        class="rounded-full bg-white px-2 py-0.5 text-[10px] font-medium text-slate-500 sm:text-xs"
        :title="`Chart Agent 决策:${config.chart_type}`"
      >
        {{ config.chart_type }}
      </span>
    </div>

    <div class="px-2 py-4 sm:px-4 sm:py-5">
      <VChart
        :option="option"
        :autoresize="true"
        style="height: 380px; width: 100%"
      />
    </div>
  </section>
</template>
