<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { hasDisplayableResult } from '@/lib/result-display'
import { exportRowsToCsv } from '@/lib/export'
import { toErrorMessage } from '@/services/agent'
import {
  type ConversationBrief,
  type ConversationSource,
  createConversation,
  deleteConversation,
  getConversationMessages,
  listConversations,
  renameConversation,
} from '@/services/conversation'
import type { AgentReplyMessage, ChatMessage, ResultRow, StreamFn } from '@/types/agent'

import MetricCard from '@/components/MetricCard.vue'
import ErrorCard from '@/components/ErrorCard.vue'
import EmptyCard from '@/components/EmptyCard.vue'
import ChartPanel from '@/components/ChartPanel.vue'

const props = withDefaults(
  defineProps<{
    streamFn: StreamFn
    // 会话历史归属:主图传 'db';数据集传 'dataset' + datasetId
    source?: ConversationSource
    datasetId?: number
    title?: string
    subtitle?: string
    placeholder?: string
    guideText?: string
    backTo?: string
  }>(),
  {
    source: 'db',
    datasetId: undefined,
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

// 有输入内容 且 上一轮已结束(非加载中)才能发送 —— 上一轮回答完毕前禁止新提问
const canSend = computed(() => inputValue.value.trim().length > 0 && !isLoading.value)
const emptyResultMessage = '没有查询到您想要的结果。'

// ── 会话历史 ────────────────────────────────────────────
const conversations = ref<ConversationBrief[]>([])
const activeConversationId = ref<number | null>(null)
const historyLoading = ref(false)
const search = ref('')

// 按标题搜索过滤(本地)
const filteredConversations = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return conversations.value
  return conversations.value.filter((c) => c.title.toLowerCase().includes(q))
})

// 按时间分组:今天 / 更早以前(后端已按 updated_at 倒序,组内保持顺序)
const groupedConversations = computed(() => {
  const today: ConversationBrief[] = []
  const earlier: ConversationBrief[] = []
  const now = new Date()
  for (const c of filteredConversations.value) {
    const t = c.updated_at ? new Date(c.updated_at) : null
    const isToday =
      !!t &&
      t.getFullYear() === now.getFullYear() &&
      t.getMonth() === now.getMonth() &&
      t.getDate() === now.getDate()
    ;(isToday ? today : earlier).push(c)
  }
  const groups: { label: string; items: ConversationBrief[] }[] = []
  if (today.length) groups.push({ label: '今天', items: today })
  if (earlier.length) groups.push({ label: '更早以前', items: earlier })
  return groups
})

async function loadConversations() {
  try {
    conversations.value = await listConversations(props.source, props.datasetId)
  } catch (e) {
    // 历史列表加载失败不阻断主流程,但打到控制台便于排查(如开发代理未覆盖 /conversations)
    console.error('[历史会话] 加载失败:', e)
  }
}

// 切到某条历史会话:中止当前流,拉取消息回填
async function selectConversation(id: number) {
  if (id === activeConversationId.value) return
  activeController?.abort()
  activeController = null
  isLoading.value = false
  historyLoading.value = true
  try {
    const msgs = await getConversationMessages(id)
    messages.value = msgs
    activeConversationId.value = id
    await scrollToBottom()
  } catch {
    /* 拉取失败保持原状 */
  } finally {
    historyLoading.value = false
  }
}

// 新建对话:清空当前会话(下次提问由后端建会话并回传 id)
function newConversation() {
  activeController?.abort()
  activeController = null
  isLoading.value = false
  messages.value = []
  activeConversationId.value = null
  inputValue.value = ''
}

// 新建对话弹框:用户起名 → 后端立即建一个空白会话 → 切到它
const showCreateModal = ref(false)
const newTitle = ref('')
const creating = ref(false)
const createInput = ref<HTMLInputElement | null>(null)

function openCreateModal() {
  newTitle.value = ''
  showCreateModal.value = true
  nextTick(() => createInput.value?.focus())
}
function closeCreateModal() {
  if (creating.value) return
  showCreateModal.value = false
}
async function confirmCreate() {
  if (creating.value) return
  creating.value = true
  try {
    const conv = await createConversation(props.source, newTitle.value.trim() || '新对话', props.datasetId)
    await loadConversations()
    // 切到这个新空白会话(清空对话区,后续提问会带上它的 id 续写)
    newConversation()
    activeConversationId.value = conv.id
    showCreateModal.value = false
  } catch (e) {
    console.error('[新建对话] 创建失败:', e)
  } finally {
    creating.value = false
  }
}

// 重命名弹框
const renameTarget = ref<ConversationBrief | null>(null)
const renameTitle = ref('')
const renaming = ref(false)
const renameInput = ref<HTMLInputElement | null>(null)

function renameConv(conv: ConversationBrief) {
  renameTarget.value = conv
  renameTitle.value = conv.title
  nextTick(() => renameInput.value?.focus())
}
function closeRename() {
  if (renaming.value) return
  renameTarget.value = null
}
async function confirmRename() {
  const conv = renameTarget.value
  if (!conv || renaming.value) return
  const title = renameTitle.value.trim()
  if (!title || title === conv.title) {
    renameTarget.value = null
    return
  }
  renaming.value = true
  try {
    await renameConversation(conv.id, title)
    await loadConversations()
    renameTarget.value = null
  } catch (e) {
    console.error('[重命名] 失败:', e)
  } finally {
    renaming.value = false
  }
}

// 删除确认弹框
const deleteTarget = ref<ConversationBrief | null>(null)
const deleting = ref(false)

function removeConv(conv: ConversationBrief) {
  deleteTarget.value = conv
}
function closeDelete() {
  if (deleting.value) return
  deleteTarget.value = null
}
async function confirmDelete() {
  const conv = deleteTarget.value
  if (!conv || deleting.value) return
  deleting.value = true
  try {
    await deleteConversation(conv.id)
    if (activeConversationId.value === conv.id) newConversation()
    await loadConversations()
    deleteTarget.value = null
  } catch (e) {
    console.error('[删除会话] 失败:', e)
  } finally {
    deleting.value = false
  }
}

onMounted(loadConversations)

function createReplyMessage(): AgentReplyMessage {
  return {
    id: crypto.randomUUID(),
    role: 'assistant',
    steps: [],
    result: [],
    chartConfig: null,
    interpretation: null,
    sql: null,
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

// ── 查看 SQL / 复制 / 导出 ──────────────────────────────
// 每条回复的「查看 SQL」展开状态(默认折叠)
const sqlExpanded = ref<Record<string, boolean>>({})
function isSqlExpanded(message: AgentReplyMessage): boolean {
  return sqlExpanded.value[message.id] ?? false
}
function toggleSql(message: AgentReplyMessage) {
  sqlExpanded.value = { ...sqlExpanded.value, [message.id]: !isSqlExpanded(message) }
}

// 复制 SQL,短暂显示「已复制」
const copiedId = ref<string | null>(null)
async function copySql(message: AgentReplyMessage) {
  if (!message.sql) return
  try {
    await navigator.clipboard.writeText(message.sql)
    copiedId.value = message.id
    window.setTimeout(() => {
      if (copiedId.value === message.id) copiedId.value = null
    }, 1500)
  } catch {
    /* 剪贴板不可用(非 https / 权限)时静默 */
  }
}

// 导出当前回复的结果为 CSV(文件名带时间戳,避免重名覆盖)
function exportCsv(message: AgentReplyMessage) {
  if (!shouldShowResult(message.result)) return
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')
  exportRowsToCsv(message.result, `query_result_${stamp}.csv`)
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
  // 上一轮还在执行时,禁止发起新提问:必须等本轮(数据解读 + 图表)全部完成
  if (isLoading.value) return

  const userMessage = { id: crypto.randomUUID(), role: 'user' as const, content: query }
  const replyMessage = createReplyMessage()

  messages.value.push(userMessage, replyMessage)
  inputValue.value = ''
  isLoading.value = true
  await scrollToBottom()

  const controller = new AbortController()
  activeController = controller

  // 记录提问前是否为新会话:用于决定结束后是否刷新历史列表(新建/标题变化)
  const wasNewConversation = activeConversationId.value === null

  try {
    await props.streamFn(query, {
      signal: controller.signal,
      conversationId: activeConversationId.value,
      onConversation: (id) => {
        // 后端新建/确认的会话 id;后续同会话续聊都带它
        activeConversationId.value = id
        // 新会话:后端在发出此事件前已建好并提交,这里立刻刷新侧栏,
        // 让历史记录在「提问瞬间」就出现并选中,不必等整轮执行结束。
        if (wasNewConversation) {
          void loadConversations()
        }
      },
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
    // 新会话或标题可能已变 → 刷新历史列表;新会话置顶选中
    if (wasNewConversation) {
      await loadConversations()
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
  <!-- 左右布局:左历史侧栏 + 右聊天主区 -->
  <div class="flex h-full w-full overflow-hidden bg-white/82 backdrop-blur-xl">
    <!-- 历史会话侧栏 -->
    <aside class="flex h-full w-64 shrink-0 flex-col border-r border-slate-200/70 bg-slate-50/60">
      <!-- 新建对话 -->
      <div class="p-3">
        <button
          type="button"
          class="flex w-full items-center justify-center gap-2 rounded-xl border border-sky-200 bg-sky-50 px-4 py-2.5 text-sm font-semibold text-sky-700 shadow-sm transition hover:border-sky-300 hover:bg-sky-100"
          @click="openCreateModal"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v8M8 12h8" stroke-linecap="round" />
          </svg>
          新建对话
        </button>
      </div>

      <!-- 搜索 -->
      <div class="px-3 pb-2">
        <div class="relative">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.3-4.3" stroke-linecap="round" />
          </svg>
          <input
            v-model="search"
            type="text"
            placeholder="搜索"
            class="w-full rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-600 outline-none transition placeholder:text-slate-400 focus:border-sky-300"
          />
        </div>
      </div>

      <!-- 列表 -->
      <div class="flex-1 overflow-y-auto px-2 pb-3">
        <p
          v-if="filteredConversations.length === 0"
          class="px-3 py-6 text-center text-xs text-slate-400"
        >
          {{ search ? '没有匹配的会话' : '暂无历史，发起提问即可保存' }}
        </p>

        <div v-for="group in groupedConversations" :key="group.label" class="mb-2">
          <p class="px-3 py-1.5 text-xs font-medium text-slate-400">{{ group.label }}</p>
          <ul class="space-y-0.5">
            <li
              v-for="conv in group.items"
              :key="conv.id"
              class="group relative rounded-lg border transition"
              :class="
                conv.id === activeConversationId
                  ? 'border-sky-300 bg-sky-50 shadow-[0_0_0_3px_rgba(186,230,253,0.7)]'
                  : 'border-transparent hover:bg-slate-100'
              "
            >
              <button
                type="button"
                class="block w-full truncate rounded-lg px-3 py-2.5 pr-14 text-left text-sm"
                :class="
                  conv.id === activeConversationId
                    ? 'font-medium text-sky-700'
                    : 'text-slate-600'
                "
                :title="conv.title"
                @click="selectConversation(conv.id)"
              >
                {{ conv.title }}
              </button>

              <!-- 悬浮操作:重命名 / 删除 -->
              <div
                class="absolute right-1.5 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 group-hover:flex"
              >
                <button
                  type="button"
                  class="rounded-md p-1 text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                  title="重命名"
                  @click.stop="renameConv(conv)"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5">
                    <path d="M12 20h9" stroke-linecap="round" />
                    <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" stroke-linejoin="round" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="rounded-md p-1 text-slate-400 transition hover:bg-rose-100 hover:text-rose-600"
                  title="删除"
                  @click.stop="removeConv(conv)"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5">
                    <path
                      d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </aside>

    <!-- 聊天主区 -->
    <div class="flex h-full flex-1 flex-col overflow-hidden">
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
        class="mx-auto flex w-full max-w-5xl"
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
          class="w-full max-w-full rounded-[28px] rounded-bl-md border border-white/75 bg-white/92 px-5 py-5 shadow-[0_18px_40px_rgba(148,163,184,0.16)] sm:px-6"
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

          <!-- 操作栏:查看 SQL / 导出 CSV(成功且有 SQL 或有结果行时显示) -->
          <div
            v-if="message.status === 'success' && (message.sql || shouldShowResult(message.result))"
            class="mt-6 flex flex-wrap items-center gap-2"
          >
            <button
              v-if="message.sql"
              type="button"
              class="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-sky-400 hover:bg-sky-50 hover:text-sky-700"
              @click="toggleSql(message)"
            >
              查看 SQL
              <span class="text-[10px]" aria-hidden="true">{{ isSqlExpanded(message) ? '▲' : '▼' }}</span>
            </button>

            <button
              v-if="shouldShowResult(message.result)"
              type="button"
              class="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-emerald-400 hover:bg-emerald-50 hover:text-emerald-700"
              @click="exportCsv(message)"
            >
              
              导出数据
              <span aria-hidden="true">⬇</span>
            </button>
          </div>

          <!-- SQL 展开区:等宽显示真正执行的 SQL + 复制按钮 -->
          <div
            v-if="message.status === 'success' && message.sql && isSqlExpanded(message)"
            class="mt-3 overflow-hidden rounded-2xl border border-slate-700 bg-slate-900"
          >
            <div class="flex items-center justify-between border-b border-slate-700 px-4 py-2">
              <span class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">SQL</span>
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded-md border border-slate-600 bg-slate-800 px-2.5 py-1 text-[11px] font-medium text-slate-200 transition hover:border-sky-500 hover:text-sky-300"
                @click="copySql(message)"
              >
                {{ copiedId === message.id ? '已复制' : '复制' }}
              </button>
            </div>
            <pre class="overflow-x-auto px-4 py-3 text-xs leading-6 text-slate-100"><code>{{ message.sql }}</code></pre>
          </div>

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
            {{ isLoading ? '回答中…' : '发送查询' }}
          </button>
        </div>
      </form>
    </footer>
    </div>
    <!-- /聊天主区 -->

    <!-- 新建对话弹框 -->
    <div
      v-if="showCreateModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      @click.self="closeCreateModal"
    >
      <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <h3 class="text-base font-semibold text-slate-800">新建对话</h3>
        <p class="mt-1 text-xs text-slate-400">给这个会话起个名字，方便日后查找</p>
        <input
          ref="createInput"
          v-model="newTitle"
          type="text"
          maxlength="50"
          placeholder="例如：Q1 各产线产量分析"
          class="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-sky-300"
          @keydown.enter="confirmCreate"
          @keydown.esc="closeCreateModal"
        />
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
            :disabled="creating"
            @click="closeCreateModal"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-xl bg-sky-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-sky-600 disabled:cursor-not-allowed disabled:bg-sky-300"
            :disabled="creating"
            @click="confirmCreate"
          >
            {{ creating ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 重命名弹框 -->
    <div
      v-if="renameTarget"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      @click.self="closeRename"
    >
      <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <h3 class="text-base font-semibold text-slate-800">重命名会话</h3>
        <input
          ref="renameInput"
          v-model="renameTitle"
          type="text"
          maxlength="50"
          class="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm text-slate-700 outline-none transition placeholder:text-slate-400 focus:border-sky-300"
          @keydown.enter="confirmRename"
          @keydown.esc="closeRename"
        />
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
            :disabled="renaming"
            @click="closeRename"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-xl bg-sky-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-sky-600 disabled:cursor-not-allowed disabled:bg-sky-300"
            :disabled="renaming"
            @click="confirmRename"
          >
            {{ renaming ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 删除确认弹框 -->
    <div
      v-if="deleteTarget"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      @click.self="closeDelete"
    >
      <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <div class="flex items-start gap-3">
          <span
            class="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sky-50 text-sky-500"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-5 w-5">
              <path
                d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <div class="min-w-0">
            <h3 class="text-base font-semibold text-slate-800">删除会话</h3>
            <p class="mt-1 break-words text-sm text-slate-500">
              确定删除「<span class="font-medium text-slate-700">{{ deleteTarget.title }}</span
              >」？此操作不可恢复。
            </p>
          </div>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
            :disabled="deleting"
            @click="closeDelete"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-xl bg-sky-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-sky-600 disabled:cursor-not-allowed disabled:bg-sky-300"
            :disabled="deleting"
            @click="confirmDelete"
          >
            {{ deleting ? '删除中…' : '删除' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
