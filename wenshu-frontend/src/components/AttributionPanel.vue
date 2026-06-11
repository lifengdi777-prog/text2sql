<script setup lang="ts">
/**
 * 归因分析右侧滑出面板(非阻塞):独立消费 /agent/attribution 的 SSE,
 * 不走 runTurn、不碰聊天状态 —— 归因进行中聊天照常可用。
 *
 * 结构:进度区(步骤卡)→ 结果区(指标卡+变化徽章 → 核心结论 →
 *       维度 chips → 贡献度横向条形(ECharts)→ 维度明细表 → 查看 SQL)。
 * 说明卡带 suggest_compare_type 时渲染「改用环比/同比重试」按钮(带新口径重发请求)。
 *
 * 父组件通过 defineExpose 的 open(params) 发起归因;重复 open 会中止上一次。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
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
import type { AgentEvent } from '@/lib/sse'
import type { StepStatus } from '@/types/agent'
import { isAttributionResult, type AttributionResult } from '@/types/attribution'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

const COMPARE_CN: Record<CompareType, string> = { mom: '环比', yoy: '同比' }

interface PanelStep {
  step: string
  status: StepStatus
  detail?: string
}

const visible = ref(false)
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

async function open(req: AttributionRequest) {
  controller?.abort()
  const current = new AbortController()
  controller = current
  lastRequest = req
  resetState(req)
  visible.value = true
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

// 「改用环比/同比重试」:同一份数据,换口径重发
function retryWithSuggested() {
  if (!lastRequest || !suggestCompareType.value) return
  void open({ ...lastRequest, compareType: suggestCompareType.value })
}

function close() {
  controller?.abort()
  controller = null
  running.value = false
  visible.value = false
}

onBeforeUnmount(() => controller?.abort())

defineExpose({ open })

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

const chartHeight = computed(() => Math.max(150, chartMembers.value.length * 32 + 30))

const barOption = computed(() => {
  const members = [...chartMembers.value].reverse()
  return {
    grid: { left: 8, right: 56, top: 8, bottom: 8, containLabel: true },
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
      axisLabel: { formatter: '{value}%', fontSize: 10, color: '#94a3b8' },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    yAxis: {
      type: 'category' as const,
      data: members.map((m) => m.member),
      axisLabel: { fontSize: 11, color: '#475569' },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
    },
    series: [
      {
        type: 'bar' as const,
        barMaxWidth: 16,
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
          fontSize: 10,
          color: '#64748b',
          formatter: '{c}%',
        },
      },
    ],
  }
})
</script>

<template>
  <Transition
    enter-active-class="transition-transform duration-300 ease-out"
    enter-from-class="translate-x-full"
    enter-to-class="translate-x-0"
    leave-active-class="transition-transform duration-200 ease-in"
    leave-from-class="translate-x-0"
    leave-to-class="translate-x-full"
  >
    <aside
      v-show="visible"
      class="fixed inset-y-0 right-0 z-40 flex w-[480px] max-w-[94vw] flex-col border-l border-slate-200 bg-white shadow-[-24px_0_60px_rgba(15,23,42,0.18)]"
    >
      <!-- 头部 -->
      <header class="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <h3 class="text-base font-semibold text-slate-900">归因分析</h3>
            <span
              class="inline-flex items-center rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-700"
            >
              {{ COMPARE_CN[compareType] }}
            </span>
            <span
              v-if="running"
              class="h-4 w-4 rounded-full border-2 border-sky-200 border-t-sky-500 animate-spin"
            />
          </div>
          <p class="mt-1 truncate text-xs text-slate-500" :title="query">{{ query }}</p>
        </div>
        <button
          type="button"
          class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
          title="关闭"
          @click="close"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-5 w-5">
            <path d="M6 6l12 12M18 6L6 18" stroke-linecap="round" />
          </svg>
        </button>
      </header>

      <!-- 内容区 -->
      <div class="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <!-- 进度区:结果出来后折叠为一行,可展开回看 -->
        <section v-if="steps.length > 0">
          <button
            v-if="showStepsCollapsed"
            type="button"
            class="flex w-full items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-left transition hover:border-sky-300 hover:bg-sky-50"
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
              class="flex items-start gap-2.5 rounded-xl border border-slate-100 bg-slate-50/80 px-3.5 py-2.5"
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
            <p class="text-xs leading-6 text-amber-800">{{ clarify }}</p>
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
          class="rounded-2xl border border-rose-200 bg-rose-50/80 p-4 text-xs leading-6 text-rose-700"
        >
          {{ errorMessage }}
        </section>

        <!-- 结果区 -->
        <template v-if="result && phenomenon">
          <!-- 指标卡:本期总值 + 变化徽章 -->
          <section class="rounded-2xl border border-slate-200 bg-gradient-to-br from-slate-50 to-white p-4">
            <p class="text-xs text-slate-500">
              {{ phenomenon.target_period }}{{ phenomenon.scope || '' }} · {{ phenomenon.metric }}
            </p>
            <div class="mt-1.5 flex flex-wrap items-baseline gap-2.5">
              <span class="text-2xl font-bold tracking-tight text-slate-900">
                {{ fmtNum(phenomenon.target_value) }}
              </span>
              <span
                class="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold"
                :class="changeUp ? 'bg-emerald-50 text-emerald-600' : 'bg-rose-50 text-rose-600'"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="h-3 w-3">
                  <path v-if="changeUp" d="M12 19V5M5 12l7-7 7 7" stroke-linecap="round" stroke-linejoin="round" />
                  <path v-else d="M12 5v14M19 12l-7 7-7-7" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                {{ fmtSigned(phenomenon.change) }}({{ fmtPct(phenomenon.change_pct, true) }})
              </span>
            </div>
            <p class="mt-1.5 text-[11px] text-slate-400">
              {{ COMPARE_CN[compareType] }}基准:{{ phenomenon.baseline_period }} ·
              {{ fmtNum(phenomenon.baseline_value) }}
            </p>
          </section>

          <!-- 核心结论 -->
          <section class="rounded-2xl border border-violet-100 bg-violet-50/60 p-4">
            <div class="mb-2 flex items-center gap-2">
              <span class="h-1.5 w-1.5 rounded-full bg-violet-400"></span>
              <span class="text-xs font-semibold text-violet-700">核心结论</span>
            </div>
            <p class="whitespace-pre-line text-xs leading-6 text-slate-700">
              {{ result.conclusion }}
            </p>
          </section>

          <!-- 维度 chips -->
          <div class="flex flex-wrap items-center gap-2">
            <button
              v-for="(dim, i) in result.dimensions"
              :key="dim.name"
              type="button"
              class="rounded-full border px-3.5 py-1.5 text-xs font-medium transition"
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
            <div class="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-4 py-2.5">
              <h4 class="text-xs font-semibold text-slate-700">
                {{ activeDim.name }} · 贡献度排名
              </h4>
              <span class="text-[10px] text-slate-400">
                <span :style="{ color: CONTRIB_POS }">■</span> 推动变化
                <span class="ml-1.5" :style="{ color: CONTRIB_NEG }">■</span> 反向变动
              </span>
            </div>
            <div class="px-2 py-3">
              <VChart
                :option="barOption"
                :autoresize="true"
                :style="{ height: `${chartHeight}px`, width: '100%' }"
              />
            </div>
          </section>

          <!-- 维度明细表 -->
          <section v-if="activeDim" class="overflow-hidden rounded-2xl border border-slate-200">
            <div class="overflow-x-auto">
              <table class="w-full text-xs">
                <thead>
                  <tr class="border-b border-slate-200 bg-slate-50 text-left text-slate-500">
                    <th class="px-3 py-2 font-medium">{{ activeDim.name }}</th>
                    <th class="px-3 py-2 text-right font-medium">观察期</th>
                    <th class="px-3 py-2 text-right font-medium">基准期</th>
                    <th class="px-3 py-2 text-right font-medium">变化量</th>
                    <th class="px-3 py-2 text-right font-medium">增幅</th>
                    <th class="px-3 py-2 text-right font-medium">贡献度</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="m in activeDim.members"
                    :key="m.member"
                    class="border-b border-slate-100 last:border-0 hover:bg-slate-50/70"
                  >
                    <td class="max-w-[120px] truncate px-3 py-2 font-medium text-slate-700" :title="m.member">
                      {{ m.member }}
                    </td>
                    <td class="px-3 py-2 text-right tabular-nums text-slate-600">{{ fmtNum(m.target_value) }}</td>
                    <td class="px-3 py-2 text-right tabular-nums text-slate-600">{{ fmtNum(m.baseline_value) }}</td>
                    <td
                      class="px-3 py-2 text-right font-medium tabular-nums"
                      :class="m.change > 0 ? 'text-emerald-600' : m.change < 0 ? 'text-rose-600' : 'text-slate-500'"
                    >
                      {{ fmtSigned(m.change) }}
                    </td>
                    <td class="px-3 py-2 text-right tabular-nums text-slate-600">
                      {{ m.change_pct === null ? '新增' : fmtPct(m.change_pct, true) }}
                    </td>
                    <td class="px-3 py-2 text-right font-semibold tabular-nums text-slate-700">
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
              class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700"
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
          class="py-8 text-center text-xs text-slate-400"
        >
          正在发起归因分析…
        </p>
      </div>
    </aside>
  </Transition>
</template>
