<script setup lang="ts">
/**
 * 图表面板:统一承载「切换按钮 + 图表/表格渲染」。
 *
 * - 默认类型(后端 LLM 选的)用后端精修过的 chart_config 渲染
 * - 用户在 compatible_types 内切换时,本地用 buildChartOption 重新构图,不回后端
 * - table 类型直接渲染 HTML 表格(全量 rows)
 */
import { computed, ref, watch } from 'vue'
import VChart from 'vue-echarts'
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

import type { ChartConfig, ChartType, ResultRow } from '@/types/agent'
import { buildChartOption } from '@/lib/chartBuilder'

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
  rows: ResultRow[]
}
const props = defineProps<Props>()

const ECHARTS_TYPES = new Set<ChartType>(['line', 'bar', 'pie', 'multi_line', 'stacked_bar'])
const TYPE_LABEL: Record<string, string> = {
  line: '折线图',
  bar: '柱状图',
  pie: '饼图',
  multi_line: '多线图',
  stacked_bar: '堆叠柱',
  table: '表格',
}

// 当前激活的类型,默认 = 后端选的类型;新配置进来时重置
const activeType = ref<ChartType>(props.config.chart_type)
watch(
  () => props.config,
  (c) => {
    activeType.value = c.chart_type
  },
)

const compatibleTypes = computed<ChartType[]>(() => {
  const ct = props.config.compatible_types
  return Array.isArray(ct) && ct.length > 0 ? ct : [props.config.chart_type]
})
const showToggle = computed(() => compatibleTypes.value.length > 1)

const title = computed(() => {
  const t = props.config.title
  if (typeof t === 'string') return t
  if (t && typeof t === 'object' && 'text' in t) return String((t as { text: string }).text)
  return '查询结果'
})

// 渲染前清洗:单系列 bar/line 的图例只是重复那一个列名,还默认靠左挡住柱子 —— 移除。
// 坐标轴名保留(列名为中文别名,直接当轴标题展示);
// pie 的图例是分类名、多系列图例是各序列名,都有意义,也保留。
function sanitizeOption(opt: Record<string, unknown>): Record<string, unknown> {
  const o: Record<string, unknown> = { ...opt }
  const series = o.series
  if (Array.isArray(series) && series.length <= 1) {
    const t = (series[0] as { type?: string } | undefined)?.type
    if (t === 'bar' || t === 'line') delete o.legend
  }
  return o
}

// 要渲染的 ECharts option:默认类型用后端配好的,切换则本地构造;再剥掉元字段
const activeOption = computed(() => {
  const isDefault = activeType.value === props.config.chart_type
  const cfg: ChartConfig =
    isDefault && ECHARTS_TYPES.has(activeType.value)
      ? props.config
      : buildChartOption(activeType.value, props.rows, props.config.field_map ?? {}, title.value)

  const meta = new Set([
    'chart_type',
    'compatible_types',
    'field_map',
    'metrics',
    'message',
    'hint',
    'notice',
    'original_sql',
    'row_count',
    'columns',
    'rows',
    '_fallback_reason',
  ])
  const opt: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(cfg)) {
    if (!meta.has(k)) opt[k] = v
  }
  return sanitizeOption(opt)
})

const columns = computed(() => {
  const first = props.rows[0]
  return first ? Object.keys(first) : []
})

// 表格分页:每页 10 行,不限制总行数,用户翻页查看全部。
// 仅影响表格的展示渲染——图表仍用全量 rows 绘制(趋势等需要所有点),导出也走全量。
const PAGE_SIZE = 10
const currentPage = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(props.rows.length / PAGE_SIZE)))
const pagedRows = computed(() => {
  const start = (currentPage.value - 1) * PAGE_SIZE
  return props.rows.slice(start, start + PAGE_SIZE)
})
// 页码下拉选项:[1, 2, ..., totalPages]
const pageOptions = computed(() => Array.from({ length: totalPages.value }, (_, i) => i + 1))
function goToPage(p: number) {
  currentPage.value = Math.min(totalPages.value, Math.max(1, p))
}
// 新结果进来时回到第一页,避免停在已不存在的页码
watch(
  () => props.rows,
  () => {
    currentPage.value = 1
  },
)

function fmt(v: unknown): string {
  if (v === null || v === undefined) return '-'
  return String(v)
}
</script>

<template>
  <section class="mt-6 overflow-hidden rounded-3xl border border-slate-200 bg-white">
    <div class="flex items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3">
      <h4 class="text-xs font-semibold text-slate-700 sm:text-sm">{{ title }}</h4>

      <div v-if="showToggle" class="flex flex-wrap gap-1">
        <button
          v-for="t in compatibleTypes"
          :key="t"
          type="button"
          class="rounded-full px-2.5 py-0.5 text-[10px] font-medium transition sm:text-xs"
          :class="
            t === activeType
              ? 'bg-sky-600 text-white'
              : 'bg-white text-slate-500 hover:bg-slate-100'
          "
          @click="activeType = t"
        >
          {{ TYPE_LABEL[t] ?? t }}
        </button>
      </div>
    </div>

    <!-- 后端提示:如「点名的图型画不出 → 原因 + 可生成的图型」,落库在 chartConfig 里,历史回放同样显示 -->
    <p
      v-if="config.notice"
      class="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700 sm:text-sm"
    >
      {{ config.notice }}
    </p>

    <div class="px-2 py-4 sm:px-4 sm:py-5">
      <!-- 表格 -->
      <div v-if="activeType === 'table'" class="overflow-x-auto">
        <table class="min-w-full divide-y divide-slate-200 text-left text-xs sm:text-sm">
          <thead class="bg-slate-900 text-slate-100">
            <tr>
              <th
                v-for="c in columns"
                :key="c"
                class="whitespace-nowrap px-4 py-3 font-medium tracking-wide"
              >
                {{ c }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 bg-white">
            <tr
              v-for="(row, i) in pagedRows"
              :key="(currentPage - 1) * PAGE_SIZE + i"
              class="hover:bg-slate-50/80"
            >
              <td
                v-for="c in columns"
                :key="c"
                class="whitespace-nowrap px-4 py-3 text-slate-600"
              >
                {{ fmt(row[c]) }}
              </td>
            </tr>
          </tbody>
        </table>

        <!-- 分页:每页 20 行,翻页查看全部;仅在多于一页时显示 -->
        <div
          v-if="totalPages > 1"
          class="mt-3 flex items-center justify-between px-4 py-2 text-xs text-slate-500"
        >
          <span>共 {{ rows.length }} 行 · 第 {{ currentPage }} / {{ totalPages }} 页</span>
          <div class="flex items-center gap-1">
            <button
              type="button"
              :disabled="currentPage === 1"
              class="rounded-md border border-slate-300 bg-white px-2.5 py-1 font-medium text-slate-600 transition hover:border-sky-400 hover:text-sky-700 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-slate-300 disabled:hover:text-slate-600"
              @click="goToPage(currentPage - 1)"
            >
              上一页
            </button>
            <select
              v-model.number="currentPage"
              class="rounded-md border border-slate-300 bg-white px-1.5 py-1 font-medium text-slate-600 outline-none transition hover:border-sky-400 focus:border-sky-400"
              aria-label="跳转到指定页"
            >
              <option v-for="p in pageOptions" :key="p" :value="p">第 {{ p }} 页</option>
            </select>
            <button
              type="button"
              :disabled="currentPage === totalPages"
              class="rounded-md border border-slate-300 bg-white px-2.5 py-1 font-medium text-slate-600 transition hover:border-sky-400 hover:text-sky-700 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-slate-300 disabled:hover:text-slate-600"
              @click="goToPage(currentPage + 1)"
            >
              下一页
            </button>
          </div>
        </div>
      </div>

      <!-- 图表 -->
      <!-- notMerge: 切换图表类型时全量替换,清掉上一种图残留的 xAxis/yAxis/轴名
           (默认合并模式会把柱图的坐标轴留在饼图上,导致标注错乱) -->
      <VChart
        v-else
        :option="activeOption"
        :update-options="{ notMerge: true }"
        :autoresize="true"
        style="height: 380px; width: 100%"
      />
    </div>
  </section>
</template>
