<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

import { hasDisplayableResult } from '@/lib/result-display'
import { toErrorMessage } from '@/services/agent'
import type { AgentReplyMessage, ChatMessage, ResultRow, StreamFn } from '@/types/agent'

import MetricCard from '@/components/MetricCard.vue'
import ErrorCard from '@/components/ErrorCard.vue'
import EmptyCard from '@/components/EmptyCard.vue'
import ChartPanel from '@/components/ChartPanel.vue'

const props = withDefaults(
  defineProps<{
    streamFn: StreamFn
    title?: string
    subtitle?: string
    placeholder?: string
    guideText?: string
    backTo?: string
  }>(),
  {
    title: '智能数据分析工作台',
    subtitle: 'Text to SQL',
    placeholder: '请输入想查询的问题，例如：统计 2026 年各工厂的实际产量',
    guideText: '你好，我是 Text2SQL，能将您的需求转换为查询，您可以像下面一样提问',
    backTo: '',
  },
)

// 判断 chart_type 是否走 ECharts 渲染(否则走表格 / 状态卡)
function isEChartsType(t: string | undefined | null): boolean {
  return ['line', 'bar', 'pie', 'multi_line', 'stacked_bar'].includes(t || '')
}

const inputValue = ref('')
const messages = ref<ChatMessage[]>([])
const isLoading = ref(false)
const scrollContainer = ref<HTMLElement | null>(null)

let activeController: AbortController | null = null

const canSend = computed(() => inputValue.value.trim().length > 0)
const emptyResultMessage = '没有查询到您想要的结果。'

function createReplyMessage(): AgentReplyMessage {
  return {
    id: crypto.randomUUID(),
    role: 'assistant',
    steps: [],
    result: [],
    chartConfig: null,
    interpretation: null,
    guideQueries: [],
    status: 'streaming',
  }
}

function isReplyMessage(message: ChatMessage): message is AgentReplyMessage {
  return message.role === 'assistant'
}

function shouldShowResult(rows: ResultRow[]): boolean {
  return hasDisplayableResult(rows)
}

function applyGuideQuery(query: string) {
  inputValue.value = query
}

// 执行链路折叠:执行完成(success)后默认折叠,用户可点击展开/再折叠。
const collapseOverride = ref<Record<string, boolean>>({})
function isStepsCollapsed(message: AgentReplyMessage): boolean {
  const override = collapseOverride.value[message.id]
  if (override !== undefined) return override
  return message.status === 'success'
}
function toggleSteps(message: AgentReplyMessage) {
  collapseOverride.value = {
    ...collapseOverride.value,
    [message.id]: !isStepsCollapsed(message),
  }
}

async function scrollToBottom() {
  await nextTick()
  if (!scrollContainer.value) return
  scrollContainer.value.scrollTo({ top: scrollContainer.value.scrollHeight, behavior: 'smooth' })
}

async function submitQuery() {
  const query = inputValue.value.trim()
  if (!query) return

  if (activeController) {
    activeController.abort()
    activeController = null
  }

  const userMessage = { id: crypto.randomUUID(), role: 'user' as const, content: query }
  const replyMessage = createReplyMessage()

  messages.value.push(userMessage, replyMessage)
  inputValue.value = ''
  isLoading.value = true
  await scrollToBottom()

  const controller = new AbortController()
  activeController = controller

  try {
    await props.streamFn(query, {
      signal: controller.signal,
      onStep: (nextMessage) => {
        const index = messages.value.findIndex((item) => item.id === replyMessage.id)
        if (index === -1) return
        messages.value[index] = { ...nextMessage, id: replyMessage.id }
        void scrollToBottom()
      },
    })

    const index = messages.value.findIndex((item) => item.id === replyMessage.id)
    const currentMessage = index >= 0 ? messages.value[index] : undefined
    if (currentMessage && isReplyMessage(currentMessage) && currentMessage.status === 'streaming') {
      messages.value[index] = { ...currentMessage, status: 'success' }
    }
  } catch (error) {
    const index = messages.value.findIndex((item) => item.id === replyMessage.id)
    const currentMessage = index >= 0 ? messages.value[index] : undefined
    if (currentMessage && isReplyMessage(currentMessage)) {
      messages.value[index] = {
        ...currentMessage,
        status: 'error',
        errorMessage: controller.signal.aborted
          ? '本轮查询已被新的提问中止。'
          : toErrorMessage(error),
      }
    }
  } finally {
    if (activeController === controller) {
      activeController = null
      isLoading.value = false
    }
    await scrollToBottom()
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void submitQuery()
  }
}

onBeforeUnmount(() => {
  activeController?.abort()
})
</script>

<template>
  <!-- 全屏铺满:去掉圆角/外阴影/外边框,跟左侧侧栏的 border-r 自然分隔 -->
  <div
    class="flex h-full w-full flex-col overflow-hidden bg-white/82 backdrop-blur-xl"
  >
    <header class="border-b border-slate-200/70 px-6 py-3 sm:px-8">
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex items-center gap-3">
          <router-link
            v-if="backTo"
            :to="backTo"
            class="inline-flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-sky-300 hover:text-sky-600"
            aria-label="返回"
          >
            ←
          </router-link>
          <div class="space-y-0.5">
            <p class="text-[11px] font-semibold uppercase tracking-[0.35em] text-sky-600">
              {{ subtitle }}
            </p>
            <h1 class="text-lg font-semibold tracking-tight text-slate-900 sm:text-xl">
              {{ title }}
            </h1>
          </div>
        </div>

        <div
          class="inline-flex items-center gap-3 rounded-full border border-slate-200 bg-slate-50/90 px-4 py-2 text-xs text-slate-500 sm:text-sm"
        >
          <span
            class="inline-flex h-2.5 w-2.5 rounded-full"
            :class="isLoading ? 'animate-pulse bg-amber-400' : 'bg-emerald-500'"
          />
          {{ isLoading ? '执行中' : '等待提问' }}
        </div>
      </div>
    </header>

    <div
      ref="scrollContainer"
      class="flex-1 space-y-6 overflow-y-auto bg-[linear-gradient(180deg,rgba(255,255,255,0.48),rgba(241,245,249,0.72))] px-4 py-6 sm:px-6 lg:px-8"
    >
      <div
        v-for="message in messages"
        :key="message.id"
        class="mx-auto flex w-full max-w-4xl"
        :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
      >
        <article
          v-if="message.role === 'user'"
          class="max-w-2xl rounded-[26px] rounded-br-md bg-slate-900 px-5 py-4 text-xs leading-6 text-white shadow-[0_18px_40px_rgba(15,23,42,0.16)] sm:text-sm"
        >
          {{ message.content }}
        </article>

        <article
          v-else
          class="w-full max-w-3xl rounded-[28px] rounded-bl-md border border-white/75 bg-white/92 px-5 py-5 shadow-[0_18px_40px_rgba(148,163,184,0.16)] sm:px-6"
        >
          <div class="mb-4 flex items-center justify-between gap-4">
            <div>
              <p class="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
                Agent Status
              </p>
              <h3 class="mt-1 text-base font-semibold text-slate-900">执行链路</h3>
            </div>

            <div class="flex items-center gap-2">
              <span
                class="inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold"
                :class="
                  message.status === 'success'
                    ? 'bg-emerald-50 text-emerald-600'
                    : message.status === 'error'
                      ? 'bg-rose-50 text-rose-600'
                      : 'bg-amber-50 text-amber-600'
                "
              >
                {{
                  message.status === 'success'
                    ? '已完成'
                    : message.status === 'error'
                      ? '已中断'
                      : '处理中'
                }}
              </span>

              <button
                v-if="!isStepsCollapsed(message)"
                type="button"
                class="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-sky-400 hover:bg-sky-50 hover:text-sky-700"
                @click="toggleSteps(message)"
              >
                收起
                <span class="text-[10px]" aria-hidden="true">▲</span>
              </button>
            </div>
          </div>

          <button
            v-if="isStepsCollapsed(message)"
            type="button"
            class="flex w-full items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50/85 px-4 py-3 text-left transition hover:border-sky-300 hover:bg-sky-50"
            @click="toggleSteps(message)"
          >
            <span
              v-if="message.status === 'success'"
              class="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-xs font-bold text-white"
            >
              ✓
            </span>
            <span
              v-else-if="message.status === 'error'"
              class="inline-flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 text-xs font-bold text-white"
            >
              !
            </span>
            <span
              v-else
              class="h-5 w-5 rounded-full border-2 border-sky-200 border-t-sky-500 animate-spin"
            />

            <span class="text-xs font-medium text-slate-700 sm:text-sm">
              {{ message.status === 'error' ? '执行链路已中断' : '执行链路已完成' }}
              · 共 {{ message.steps.length }} 步
            </span>

            <span class="ml-auto inline-flex items-center gap-1 text-xs font-semibold text-sky-600">
              点击展开查看执行过程
              <span aria-hidden="true">▼</span>
            </span>
          </button>

          <div v-else class="space-y-3">
            <div
              v-for="step in message.steps"
              :key="step.step"
              class="flex items-center gap-3 rounded-2xl border border-slate-100 bg-slate-50/85 px-4 py-3"
            >
              <span
                v-if="step.status === 'running'"
                class="h-5 w-5 rounded-full border-2 border-sky-200 border-t-sky-500 animate-spin"
              />
              <span
                v-else-if="step.status === 'success'"
                class="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-xs font-bold text-white"
              >
                ✓
              </span>
              <span
                v-else
                class="inline-flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 text-xs font-bold text-white"
              >
                !
              </span>

              <span class="text-xs font-medium text-slate-700 sm:text-sm">{{ step.step }}</span>
            </div>
          </div>

          <p
            v-if="message.status === 'error' && message.errorMessage"
            class="mt-4 text-xs text-rose-500 sm:text-sm"
          >
            {{ message.errorMessage }}
          </p>

          <section
            v-if="message.status === 'success' && message.guideQueries.length > 0"
            class="mt-6 rounded-3xl border border-sky-100 bg-sky-50/70 p-4 sm:p-5"
          >
            <div class="mb-3">
              <p class="mt-1 text-xs text-sky-600/80 sm:text-sm">
                {{ guideText }}
              </p>
            </div>

            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <button
                v-for="guideQuery in message.guideQueries"
                :key="`${message.id}-${guideQuery}`"
                type="button"
                class="rounded-2xl border border-sky-200 bg-white px-4 py-3 text-left text-xs leading-6 text-slate-700 transition hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700 sm:text-sm"
                @click="applyGuideQuery(guideQuery)"
              >
                {{ guideQuery }}
              </button>
            </div>
          </section>

          <section
            v-if="message.interpretation"
            class="mt-6 rounded-3xl border border-violet-100 bg-violet-50/60 p-4 sm:p-5"
          >
            <div class="mb-2 flex items-center gap-2">
              <span class="h-1.5 w-1.5 rounded-full bg-violet-400"></span>
              <span class="text-xs font-semibold text-violet-700 sm:text-sm">数据解读</span>
            </div>
            <p class="whitespace-pre-line text-xs leading-7 text-slate-700 sm:text-sm">
              {{ message.interpretation }}
            </p>
          </section>

          <MetricCard
            v-if="message.status === 'success' && message.chartConfig?.chart_type === 'metric'"
            :config="message.chartConfig"
          />
          <ErrorCard
            v-else-if="message.status === 'success' && message.chartConfig?.chart_type === 'error'"
            :config="message.chartConfig"
          />
          <EmptyCard
            v-else-if="message.status === 'success' && message.chartConfig?.chart_type === 'empty'"
            :config="message.chartConfig"
          />
          <ChartPanel
            v-else-if="
              message.status === 'success' &&
              message.chartConfig &&
              (isEChartsType(message.chartConfig.chart_type) ||
                message.chartConfig.chart_type === 'table')
            "
            :config="message.chartConfig"
            :rows="message.result"
          />

          <section
            v-else-if="message.status === 'success' && !shouldShowResult(message.result)"
            class="mt-6 rounded-3xl border border-slate-200 bg-slate-50 px-4 py-4 text-xs text-slate-600 sm:px-5 sm:text-sm"
          >
            {{ emptyResultMessage }}
          </section>
        </article>
      </div>
    </div>

    <footer class="border-t border-slate-200/70 bg-white/95 px-4 py-3 sm:px-6 lg:px-8">
      <form class="mx-auto flex w-full max-w-4xl flex-col gap-2" @submit.prevent="submitQuery">
        <label for="query-input" class="text-xs font-medium text-slate-500">
          输入你的业务问题，按 Enter 发送，Shift + Enter 换行
        </label>

        <div
          class="flex flex-col gap-2 rounded-[24px] border border-slate-200 bg-slate-50/85 p-2 shadow-inner shadow-slate-200/30 sm:flex-row sm:items-end"
        >
          <textarea
            id="query-input"
            v-model="inputValue"
            rows="2"
            class="min-h-[48px] flex-1 resize-none rounded-[18px] border border-white bg-white px-4 py-2.5 text-xs leading-6 text-slate-700 outline-none ring-0 transition placeholder:text-slate-400 focus:border-sky-300 sm:text-sm"
            :placeholder="placeholder"
            @keydown="handleKeydown"
          />

          <button
            type="submit"
            class="inline-flex h-11 items-center justify-center rounded-2xl bg-slate-900 px-6 text-xs font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300 sm:min-w-[112px] sm:text-sm"
            :disabled="!canSend"
          >
            {{ isLoading ? '重新提问' : '发送查询' }}
          </button>
        </div>
      </form>
    </footer>
  </div>
</template>
