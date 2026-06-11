<script setup lang="ts">
/**
 * 归因分析独立页面:从聊天页结果卡发起,window.open 新标签页打开(完全不占用聊天)。
 *
 * 数据交接:URL 只带 ?id=,请求体(结果行/问题/口径等)经 localStorage 传递
 * (见 lib/attribution-handoff.ts);F5 刷新凭 id 重跑,子查询命中后端 SQL 缓存。
 *
 * 结构:进度区(步骤卡)→ 结果区(指标卡+变化徽章 → 核心结论 →
 *       维度 chips → 贡献度横向条形(ECharts)→ 维度明细表 → 查看 SQL)。
 * 头部可直接切换口径(环比/同比)对同一份数据重跑;
 * 说明卡带 suggest_compare_type 时渲染「改用环比/同比重试」按钮。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import VChart from 'vue-echarts'

import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import {
  streamAttributionEvents,
  toErrorMessage,
  type AttributionRequest,
  type CompareType,
} from '@/services/agent'
import { loadAttributionRequest, restashAttributionRequest } from '@/lib/attribution-handoff'
import type { AgentEvent } from '@/lib/sse'
import type { StepStatus } from '@/types/agent'
import { isAttributionResult, type AttributionResult } from '@/types/attribution'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

const COMPARE_CN: Record<CompareType, string> = { mom: '环比', yoy: '同比' }
const COMPARE_OPTIONS: { value: CompareType; label: string; desc: string }[] = [
  { value: 'mom', label: '环比', desc: '与上一期对比' },
  { value: 'yoy', label: '同比', desc: '与去年同期对比' },
]

interface PanelStep {
  step: string
  status: StepStatus
  detail?: string
}

const route = useRoute()
const handoffId = computed(() => (typeof route.query.id === 'string' ? route.query.id : ''))

// 交接数据缺失(链接过期/直接访问):显示引导而非空白页
const missing = ref(false)
const running = ref(false)
const query = ref('')
const compareType = ref<CompareType>('mom')
const steps = ref<PanelStep[]>([])
const result = ref<AttributionResult | null>(null)
// 提前收尾的说明(基准期无数据/现象不成立/不可归因等)
const clarify = ref<string | null>(null)
// 说明卡附带的改口径建议(渲染「改用环比/同比重试」按钮)
const suggestCompareType = ref<CompareType | null>(null)
const errorMessage = ref<string | null>(null)
const activeDimIndex = ref(0)
const sqlExpanded = ref(false)
// 结果出来后进度区默认折叠,可点开回看
const stepsCollapsed = ref<boolean | null>(null)

let controller: AbortController | null = null
let lastRequest: AttributionRequest | null = null

const showStepsCollapsed = computed(() => {
  if (stepsCollapsed.value !== null) return stepsCollapsed.value
  return result.value !== null
})

function resetState(req: AttributionRequest) {
  query.value = req.query
  compareType.value = req.compareType
  steps.value = []
  result.value = null
  clarify.value = null
  suggestCompareType.value = null
  errorMessage.value = null
  activeDimIndex.value = 0
  sqlExpanded.value = false
  stepsCollapsed.value = null
}

function extractDetail(data: AgentEvent['data']): string | undefined {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return undefined
  const d = data as Record<string, unknown>
  if (typeof d.error === 'string') return d.error
  if (typeof d.description === 'string') return d.description
  if (Array.isArray(d.dimensions)) return `拆解维度:${(d.dimensions as string[]).join('、')}`
  return undefined
}

function onEvent(event: AgentEvent) {
  // 流末结构化结果:不进步骤列表,直接渲染结果区
  if (event.step === 'attribution_result') {
    if (isAttributionResult(event.data)) {
      result.value = event.data
      activeDimIndex.value = 0
    }
    return
  }

  const detail = extractDetail(event.data)
  const index = steps.value.findIndex((s) => s.step === event.step)
  if (index >= 0) {
    const prev = steps.value[index]!
    steps.value[index] = { ...prev, status: event.status, detail: detail ?? prev.detail }
  } else {
    steps.value.push({ step: event.step, status: event.status, detail })
  }

  // 说明卡(澄清/无数据/现象不成立):带 clarify 文案,可能附改口径建议
  if (event.data && typeof event.data === 'object' && !Array.isArray(event.data)) {
    const d = event.data as Record<string, unknown>
    if (typeof d.clarify === 'string') {
      clarify.value = d.clarify
      suggestCompareType.value =
        d.suggest_compare_type === 'mom' || d.suggest_compare_type === 'yoy'
          ? d.suggest_compare_type
          : null
    }
    if (event.finish && event.status === 'error' && typeof d.error === 'string') {
      errorMessage.value = d.error
    }
  }
}

async function run(req: AttributionRequest) {
  controller?.abort()
  const current = new AbortController()
  controller = current
  lastRequest = req
  resetState(req)
  running.value = true

  try {
    await streamAttributionEvents(req, {
      signal: current.signal,
      onEvent,
    })
  } catch (error) {
    if (!current.signal.aborted) {
      errorMessage.value = toErrorMessage(error)
    }
  } finally {
    if (controller === current) {
      running.value = false
    }
  }
}

// 切换口径:同一份数据换口径重跑;回写交接条目让 F5 保留最后选择
function switchCompare(ct: CompareType) {
  if (!lastRequest || ct === compareType.value) return
  const req = { ...lastRequest, compareType: ct }
  if (handoffId.value) restashAttributionRequest(handoffId.value, req)
  void run(req)
}

// 「改用环比/同比重试」(基准期无数据时的建议)
function retryWithSuggested() {
  if (suggestCompareType.value) switchCompare(suggestCompareType.value)
}

onMounted(() => {
  const req = handoffId.value ? loadAttributionRequest(handoffId.value) : null
  if (!req) {
    missing.value = true
    return
  }
  document.title = `归因分析 · ${req.query}`
  void run(req)
})

onBeforeUnmount(() => controller?.abort())

// ── 展示派生 ────────────────────────────────────────────
const phenomenon = computed(() => result.value?.phenomenon ?? null)
const changeUp = computed(() => (phenomenon.value?.change ?? 0) > 0)
const activeDim = computed(() => result.value?.dimensions[activeDimIndex.value] ?? null)
// 条形图只画前 12 个成员(明细表是全量),反转让贡献最大的排最上面
const chartMembers = computed(() => (activeDim.value?.members ?? []).slice(0, 12))

function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return Number.isInteger(v)
    ? v.toLocaleString('zh-CN')
    : v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function fmtSigned(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${v > 0 ? '+' : ''}${fmtNum(v)}`
}

function fmtPct(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined) return '—'
  return `${signed && v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

// 正贡献(与总变化同向,推动)= 玫红;负贡献(反向变动)= 翠绿
const CONTRIB_POS = '#f43f5e'
const CONTRIB_NEG = '#10b981'

const chartHeight = computed(() => Math.max(170, chartMembers.value.length * 34 + 40))

const barOption = computed(() => {
  const members = [...chartMembers.value].reverse()
  return {
    grid: { left: 8, right: 64, top: 12, bottom: 8, containLabel: true },
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
      formatter: (params: unknown) => {
        const p = (params as { dataIndex: number }[])[0]
        const m = members[p?.dataIndex ?? -1]
        if (!m) return ''
        return (
          `<b>${m.member}</b><br/>` +
          `观察期:${fmtNum(m.target_value)}<br/>基准期:${fmtNum(m.baseline_value)}<br/>` +
          `变化:${fmtSigned(m.change)}(${fmtPct(m.change_pct, true)})<br/>` +
          `贡献度:${fmtPct(m.contribution_pct)}`
        )
      },
    },
    xAxis: {
      type: 'value' as const,
      axisLabel: { formatter: '{value}%', fontSize: 11, color: '#94a3b8' },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    yAxis: {
      type: 'category' as const,
      data: members.map((m) => m.member),
      axisLabel: { fontSize: 12, color: '#475569' },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
    },
    series: [
      {
        type: 'bar' as const,
        barMaxWidth: 18,
        data: members.map((m) => ({
          value: m.contribution_pct === null ? 0 : Number(m.contribution_pct.toFixed(1)),
          itemStyle: {
            color: (m.contribution_pct ?? 0) >= 0 ? CONTRIB_POS : CONTRIB_NEG,
            borderRadius: 3,
          },
        })),
        label: {
          show: true,
          position: 'right' as const,
          fontSize: 11,
          color: '#64748b',
          formatter: '{c}%',
        },
      },
    ],
  }
})
</script>

<template>
  <!-- 应用外壳是 h-screen overflow-hidden:这里自己撑满高度、内部滚动(否则长内容滚不动) -->
  <div class="h-full overflow-y-auto bg-[linear-gradient(180deg,rgba(255,255,255,0.6),rgba(241,245,249,0.9))]">
    <!-- 顶栏 -->
    <header class="sticky top-0 z-10 border-b border-slate-200/80 bg-white/90 backdrop-blur">
      <div class="mx-auto flex max-w-3xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <div class="min-w-0">
          <div class="flex items-center gap-2.5">
            <span
              class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-amber-400 to-rose-500 text-white shadow-sm"
            >
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="6" cy="6" r="3" />
                <circle cx="6" cy="18" r="3" />
                <circle cx="18" cy="12" r="3" />
                <path d="M9 6h4a2 2 0 0 1 2 2v0" />
                <path d="M9 18h4a2 2 0 0 0 2-2v0" />
              </svg>
            </span>
            <div class="min-w-0">
              <h1 class="text-base font-semibold text-slate-900">归因分析</h1>
              <p class="truncate text-xs text-slate-500" :title="query">{{ query }}</p>
            </div>
            <span
              v-if="running"
              class="h-4 w-4 shrink-0 rounded-full border-2 border-sky-200 border-t-sky-500 animate-spin"
            />
          </div>
        </div>

        <!-- 口径切换:对同一份数据换口径重跑 -->
        <div v-if="!missing" class="flex shrink-0 items-center gap-1.5">
          <button
            v-for="opt in COMPARE_OPTIONS"
            :key="opt.value"
            type="button"
            class="rounded-full border px-3 py-1 text-xs font-medium transition"
            :class="
              compareType === opt.value
                ? 'border-amber-400 bg-amber-50 text-amber-700'
                : 'border-slate-200 bg-white text-slate-500 hover:border-amber-300 hover:text-amber-600'
            "
            :title="opt.desc"
            @click="switchCompare(opt.value)"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
    </header>

    <main class="mx-auto max-w-3xl space-y-4 px-4 py-5 sm:px-6">
      <!-- 交接数据缺失:链接过期或直接访问 -->
      <section
        v-if="missing"
        class="rounded-2xl border border-slate-200 bg-white p-8 text-center"
      >
        <p class="text-sm font-medium text-slate-700">没有找到归因数据</p>
        <p class="mt-2 text-xs leading-6 text-slate-500">
          归因链接已过期或被直接访问。请回到问数页面，在查询结果卡上点「归因分析」重新发起。
        </p>
      </section>

      <template v-else>
        <!-- 进度区:结果出来后折叠为一行,可展开回看 -->
        <section v-if="steps.length > 0">
          <button
            v-if="showStepsCollapsed"
            type="button"
            class="flex w-full items-center gap-2.5 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-left transition hover:border-sky-300 hover:bg-sky-50"
            @click="stepsCollapsed = false"
          >
            <span
              class="inline-flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-[10px] font-bold text-white"
            >
              ✓
            </span>
            <span class="text-xs font-medium text-slate-600">分析完成 · 共 {{ steps.length }} 步</span>
            <span class="ml-auto text-xs font-semibold text-sky-600">展开 ▼</span>
          </button>

          <div v-else class="space-y-2">
            <div
              v-for="step in steps"
              :key="step.step"
              class="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-4 py-2.5"
            >
              <span
                v-if="step.status === 'running'"
                class="mt-0.5 h-4 w-4 shrink-0 rounded-full border-2 border-sky-200 border-t-sky-500 animate-spin"
              />
              <span
                v-else-if="step.status === 'success'"
                class="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-[10px] font-bold text-white"
              >
                ✓
              </span>
              <span
                v-else
                class="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-rose-500 text-[10px] font-bold text-white"
              >
                !
              </span>
              <div class="min-w-0">
                <p class="text-xs font-medium text-slate-700">{{ step.step }}</p>
                <p v-if="step.detail" class="mt-0.5 break-words text-[11px] leading-5 text-slate-500">
                  {{ step.detail }}
                </p>
              </div>
            </div>
            <button
              v-if="result"
              type="button"
              class="w-full rounded-lg py-1 text-center text-xs font-medium text-sky-600 transition hover:bg-sky-50"
              @click="stepsCollapsed = true"
            >
              收起 ▲
            </button>
          </div>
        </section>

        <!-- 说明卡:提前收尾(无数据/不成立/不可归因),可附改口径重试 -->
        <section
          v-if="clarify && !result"
          class="rounded-2xl border border-amber-200 bg-amber-50/80 p-4"
        >
          <div class="flex items-start gap-2">
            <span class="text-base leading-6">💡</span>
            <p class="text-xs leading-6 text-amber-800 sm:text-sm">{{ clarify }}</p>
          </div>
          <button
            v-if="suggestCompareType"
            type="button"
            class="mt-3 inline-flex items-center gap-1.5 rounded-xl border border-amber-300 bg-white px-4 py-2 text-xs font-semibold text-amber-700 shadow-sm transition hover:border-amber-400 hover:bg-amber-50"
            @click="retryWithSuggested"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5">
              <path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            改用{{ COMPARE_CN[suggestCompareType] }}重试
          </button>
        </section>

        <!-- 错误卡 -->
        <section
          v-if="errorMessage"
          class="rounded-2xl border border-rose-200 bg-rose-50/80 p-4 text-xs leading-6 text-rose-700 sm:text-sm"
        >
          {{ errorMessage }}
        </section>

        <!-- 结果区 -->
        <template v-if="result && phenomenon">
          <!-- 指标卡:本期总值 + 变化徽章 -->
          <section class="rounded-2xl border border-slate-200 bg-white p-5">
            <p class="text-xs text-slate-500 sm:text-sm">
              {{ phenomenon.target_period }}{{ phenomenon.scope || '' }} · {{ phenomenon.metric }}
            </p>
            <div class="mt-2 flex flex-wrap items-baseline gap-3">
              <span class="text-3xl font-bold tracking-tight text-slate-900">
                {{ fmtNum(phenomenon.target_value) }}
              </span>
              <span
                class="inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold sm:text-sm"
                :class="changeUp ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-600'"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="h-3.5 w-3.5">
                  <path v-if="changeUp" d="M12 19V5M5 12l7-7 7 7" stroke-linecap="round" stroke-linejoin="round" />
                  <path v-else d="M12 5v14M19 12l-7 7-7-7" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                {{ fmtSigned(phenomenon.change) }}({{ fmtPct(phenomenon.change_pct, true) }})
              </span>
            </div>
            <p class="mt-2 text-xs text-slate-400">
              {{ COMPARE_CN[compareType] }}基准:{{ phenomenon.baseline_period }} ·
              {{ fmtNum(phenomenon.baseline_value) }}
            </p>
          </section>

          <!-- 核心结论 -->
          <section class="rounded-2xl border border-violet-100 bg-violet-50/60 p-5">
            <div class="mb-2 flex items-center gap-2">
              <span class="h-1.5 w-1.5 rounded-full bg-violet-400"></span>
              <span class="text-xs font-semibold text-violet-700 sm:text-sm">核心结论</span>
            </div>
            <p class="whitespace-pre-line text-xs leading-7 text-slate-700 sm:text-sm">
              {{ result.conclusion }}
            </p>
          </section>

          <!-- 维度 chips -->
          <div class="flex flex-wrap items-center gap-2">
            <button
              v-for="(dim, i) in result.dimensions"
              :key="dim.name"
              type="button"
              class="rounded-full border px-4 py-1.5 text-xs font-medium transition sm:text-sm"
              :class="
                i === activeDimIndex
                  ? 'border-sky-400 bg-sky-50 text-sky-700 shadow-[0_0_0_3px_rgba(186,230,253,0.6)]'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-sky-300 hover:text-sky-600'
              "
              @click="activeDimIndex = i; sqlExpanded = false"
            >
              按{{ dim.name }}
            </button>
          </div>

          <!-- 贡献度排名(横向条形) -->
          <section v-if="activeDim" class="overflow-hidden rounded-2xl border border-slate-200 bg-white">
            <div class="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-5 py-3">
              <h4 class="text-xs font-semibold text-slate-700 sm:text-sm">
                {{ activeDim.name }} · 贡献度排名
              </h4>
              <span class="text-[11px] text-slate-400">
                <span :style="{ color: CONTRIB_POS }">■</span> 推动变化
                <span class="ml-1.5" :style="{ color: CONTRIB_NEG }">■</span> 反向变动
              </span>
            </div>
            <div class="px-3 py-4">
              <VChart
                :option="barOption"
                :autoresize="true"
                :style="{ height: `${chartHeight}px`, width: '100%' }"
              />
            </div>
          </section>

          <!-- 维度明细表 -->
          <section v-if="activeDim" class="overflow-hidden rounded-2xl border border-slate-200 bg-white">
            <div class="overflow-x-auto">
              <table class="w-full text-xs sm:text-sm">
                <thead>
                  <tr class="border-b border-slate-200 bg-slate-50 text-left text-slate-500">
                    <th class="px-4 py-2.5 font-medium">{{ activeDim.name }}</th>
                    <th class="px-4 py-2.5 text-right font-medium">观察期</th>
                    <th class="px-4 py-2.5 text-right font-medium">基准期</th>
                    <th class="px-4 py-2.5 text-right font-medium">变化量</th>
                    <th class="px-4 py-2.5 text-right font-medium">增幅</th>
                    <th class="px-4 py-2.5 text-right font-medium">贡献度</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="m in activeDim.members"
                    :key="m.member"
                    class="border-b border-slate-100 last:border-0 hover:bg-slate-50/70"
                  >
                    <td class="max-w-[160px] truncate px-4 py-2.5 font-medium text-slate-700" :title="m.member">
                      {{ m.member }}
                    </td>
                    <td class="px-4 py-2.5 text-right tabular-nums text-slate-600">{{ fmtNum(m.target_value) }}</td>
                    <td class="px-4 py-2.5 text-right tabular-nums text-slate-600">{{ fmtNum(m.baseline_value) }}</td>
                    <td
                      class="px-4 py-2.5 text-right font-medium tabular-nums"
                      :class="m.change > 0 ? 'text-emerald-600' : m.change < 0 ? 'text-rose-600' : 'text-slate-500'"
                    >
                      {{ fmtSigned(m.change) }}
                    </td>
                    <td class="px-4 py-2.5 text-right tabular-nums text-slate-600">
                      {{ m.change_pct === null ? '新增' : fmtPct(m.change_pct, true) }}
                    </td>
                    <td class="px-4 py-2.5 text-right font-semibold tabular-nums text-slate-700">
                      {{ fmtPct(m.contribution_pct) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- 查看该维度 SQL -->
          <section v-if="activeDim && (activeDim.target_sql || activeDim.baseline_sql)">
            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700"
              @click="sqlExpanded = !sqlExpanded"
            >
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="16 18 22 12 16 6" />
                <polyline points="8 6 2 12 8 18" />
              </svg>
              查看 SQL
              <span class="text-[10px]" aria-hidden="true">{{ sqlExpanded ? '▲' : '▼' }}</span>
            </button>
            <div v-if="sqlExpanded" class="mt-2 space-y-2">
              <div
                v-for="(sql, label) in { 观察期: activeDim.target_sql, 基准期: activeDim.baseline_sql }"
                :key="label"
              >
                <div v-if="sql" class="overflow-hidden rounded-xl border border-slate-700 bg-slate-900">
                  <p class="border-b border-slate-700 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                    {{ label }}
                  </p>
                  <pre class="overflow-x-auto px-3 py-2 text-[11px] leading-5 text-slate-100"><code>{{ sql }}</code></pre>
                </div>
              </div>
            </div>
          </section>
        </template>

        <!-- 起始占位:还没有任何事件时 -->
        <p
          v-if="steps.length === 0 && !result && !errorMessage && running"
          class="py-10 text-center text-xs text-slate-400 sm:text-sm"
        >
          正在发起归因分析…
        </p>
      </template>
    </main>
  </div>
</template>
