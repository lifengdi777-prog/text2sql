<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { hasDisplayableResult } from '@/lib/result-display'
import { exportRowsToCsv } from '@/lib/export'
import { uuid } from '@/lib/uuid'
import {
  toErrorMessage,
  generateChart,
  generateReport,
  fetchHotQuestions,
  streamAttribution,
  type QueryOptions,
} from '@/services/agent'
import {
  type ConversationBrief,
  type ConversationSource,
  createConversation,
  deleteConversation,
  getConversationMessages,
  listConversations,
  persistMessageChart,
  renameConversation,
} from '@/services/conversation'
import type { AgentReplyMessage, ChartConfig, ChatMessage, ResultRow, StreamFn } from '@/types/agent'

import MetricCard from '@/components/MetricCard.vue'
import ErrorCard from '@/components/ErrorCard.vue'
import EmptyCard from '@/components/EmptyCard.vue'
import ChartPanel from '@/components/ChartPanel.vue'

const props = withDefaults(
  defineProps<{
    streamFn: StreamFn
    // 会话历史归属:主图传 'db' + datasourceId;数据集传 'dataset' + datasetId
    source?: ConversationSource
    datasetId?: number
    // 问数会话绑定的数据源 id(按它隔离历史列表 + 新建会话时落库)
    datasourceId?: string
    title?: string
    subtitle?: string
    placeholder?: string
    guideText?: string
    backTo?: string
  }>(),
  {
    source: 'db',
    datasetId: undefined,
    datasourceId: undefined,
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

// 历史会话侧栏是否收起。收起后侧栏隐藏，聊天主区铺满；在主区 header 提供按钮再展开。
const sidebarCollapsed = ref(false)
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

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
    conversations.value = await listConversations(props.source, props.datasetId, props.datasourceId)
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
    const conv = await createConversation(
      props.source,
      newTitle.value.trim() || '新对话',
      props.datasetId,
      props.datasourceId,
    )
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

// ── 空状态「历史热门问题」:本数据源缓存命中最多的问题 ────────────────
// 点选直接提问:问题文本与缓存逐字相同 → 必然精确命中 SQL 缓存,秒出结果。
const hotQuestions = ref<string[]>([])
async function loadHotQuestions() {
  if (props.source !== 'db' || !props.datasourceId) return
  hotQuestions.value = await fetchHotQuestions(props.datasourceId)
}
function askHotQuestion(q: string) {
  if (isLoading.value) return
  inputValue.value = q
  void submitQuery()
}

onMounted(() => {
  void loadConversations()
  void loadHotQuestions()
})

// 切换数据源(/db 路由数据源在 query 里,切源不会重挂载组件):
// 重置对话区 + 重新拉取该源的历史列表与热门问题,避免残留上一个源的内容。
watch(
  () => props.datasourceId,
  () => {
    if (props.source !== 'db') return
    newConversation()
    void loadConversations()
    void loadHotQuestions()
  },
)

function createReplyMessage(): AgentReplyMessage {
  return {
    id: uuid(),
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

// ── 按需图表:默认只展示表格,用户点「生成图表」才调后端出图 ──
const chartLoading = ref<Record<string, boolean>>({})

// 出图后后端只能给表格(line/bar/pie 等都画不出)→ 提示「该数据无法生成图表」。
// 直接由已落库的 chartConfig 派生(chart_type==='table' 即降级),所以历史回放也能重现提示;
// 未出图时 chartConfig 为 null(默认表格走 TABLE_CONFIG),不会误报。
// 例外:带 notice(点名图型画不出的说明横幅)或还有可切换的图型时不提示——
// 此时数据其实画得出别的图,角标的「无法生成图表」会和提示条信息打架。
function isUnchartable(message: AgentReplyMessage): boolean {
  const cfg = message.chartConfig
  if (!cfg || cfg.chart_type !== 'table') return false
  if (cfg.notice) return false
  const ct = cfg.compatible_types
  return !Array.isArray(ct) || ct.filter((t) => t !== 'table').length === 0
}

// 该回复对应的用户问题(图表标题用):取它前面最近一条 user 消息
function questionFor(message: AgentReplyMessage): string {
  const i = messages.value.findIndex((m) => m.id === message.id)
  for (let j = i - 1; j >= 0; j--) {
    const m = messages.value[j]
    if (m && m.role === 'user') return (m as { content?: string }).content ?? ''
  }
  return ''
}

// 渲染用的配置:已生成图表则用它;否则结果有行 → 用表格配置(ChartPanel 按 rows 渲染表格)
const TABLE_CONFIG = { chart_type: 'table', title: '查询结果', compatible_types: ['table'] } as unknown as ChartConfig

function displayConfig(message: AgentReplyMessage): ChartConfig | null {
  const cfg = message.chartConfig
  if (cfg && (isEChartsType(cfg.chart_type) || cfg.chart_type === 'table')) return cfg
  if (shouldShowResult(message.result)) return TABLE_CONFIG
  return null
}

async function onGenerateChart(message: AgentReplyMessage) {
  if (!shouldShowResult(message.result) || chartLoading.value[message.id]) return
  chartLoading.value = { ...chartLoading.value, [message.id]: true }
  try {
    const cfg = await generateChart(message.result, questionFor(message))
    if (cfg) {
      const idx = messages.value.findIndex((m) => m.id === message.id)
      if (idx !== -1) messages.value[idx] = { ...(messages.value[idx] as AgentReplyMessage), chartConfig: cfg }
      // 把出图结果(含「无法成图」时的 table 降级)回写落库,重开会话能原样重现图表/提示。
      // 需要会话 id + 这条消息的后端 id;直播态消息的 dbId 由流末事件填,缺任一则跳过(不影响本次展示)。
      const convId = activeConversationId.value
      if (convId != null && message.dbId != null) {
        try {
          await persistMessageChart(convId, message.dbId, cfg)
        } catch (e) {
          console.error('[图表落库] 失败:', e)
        }
      }
    }
  } catch {
    /* 出图失败静默,用户可重试 */
  } finally {
    chartLoading.value = { ...chartLoading.value, [message.id]: false }
  }
}

// ── 按需分析报告:对该轮结果生成 HTML 报告,新标签页打开(浏览器可另存/打印 PDF) ──
const reportLoading = ref<Record<string, boolean>>({})
async function onGenerateReport(message: AgentReplyMessage) {
  if (!shouldShowResult(message.result) || reportLoading.value[message.id]) return
  // 必须在点击手势的同步上下文里开窗:await 之后再 window.open 会被浏览器弹窗拦截
  const win = window.open('', '_blank')
  if (win) {
    win.document.write(
      '<title>生成分析报告中…</title><p style="font:14px system-ui;color:#475569;padding:40px">报告生成中,约需 10 秒,请稍候…</p>',
    )
  }
  reportLoading.value = { ...reportLoading.value, [message.id]: true }
  try {
    const html = await generateReport(message.result, questionFor(message), message.sql)
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
    if (win) {
      win.location.href = url
    } else {
      // 开窗被拦截的兜底:转为下载文件
      const a = document.createElement('a')
      a.href = url
      a.download = '分析报告.html'
      a.click()
    }
    // 给新标签页留足加载时间后再释放 blob URL,避免内存常驻
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (e) {
    win?.close()
    console.error('[生成分析报告] 失败:', e)
    /* 失败静默,用户可重试(与生成图表一致) */
  } finally {
    reportLoading.value = { ...reportLoading.value, [message.id]: false }
  }
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

// 发起一轮流式对话的公共骨架:推 user/reply 消息、串回调、收尾状态。
// submitQuery(输入框提问)与 onAttribution(归因按钮)共用。
async function runTurn(
  userContent: string,
  start: (opts: QueryOptions) => Promise<void>,
) {
  // 上一轮还在执行时,禁止发起新一轮:必须等本轮(数据解读 + 图表)全部完成
  if (isLoading.value) return

  const userMessage = { id: uuid(), role: 'user' as const, content: userContent }
  const replyMessage = createReplyMessage()

  messages.value.push(userMessage, replyMessage)
  isLoading.value = true
  await scrollToBottom()

  const controller = new AbortController()
  activeController = controller

  // 记录提问前是否为新会话:用于决定结束后是否刷新历史列表(新建/标题变化)
  const wasNewConversation = activeConversationId.value === null

  try {
    await start({
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
      onMessageId: (mid) => {
        // 流末回传的后端消息 id:存到这条消息上,供之后「生成图表」回写落库
        const index = messages.value.findIndex((item) => item.id === replyMessage.id)
        if (index === -1) return
        const m = messages.value[index]
        if (m && isReplyMessage(m)) messages.value[index] = { ...m, dbId: mid }
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

async function submitQuery() {
  const query = inputValue.value.trim()
  if (!query || isLoading.value) return
  inputValue.value = ''
  await runTurn(query, (opts) => props.streamFn(query, opts))
}

// ── 归因分析:对该轮结果做"为什么变化"的多维拆解(SSE 新一轮,复用整套渲染) ──
async function onAttribution(message: AgentReplyMessage) {
  if (!shouldShowResult(message.result) || isLoading.value) return
  const q = questionFor(message)
  await runTurn(`归因分析:${q}`, (opts) =>
    streamAttribution(
      {
        rows: message.result,
        query: q,
        sql: message.sql,
        datasetId: props.source === 'dataset' ? props.datasetId : undefined,
      },
      { ...opts, datasourceId: props.datasourceId },
    ),
  )
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
    <aside
      v-show="!sidebarCollapsed"
      class="flex h-full w-64 shrink-0 flex-col border-r border-slate-200/70 bg-slate-50/60"
    >
      <!-- 顶部:收起侧边栏 -->
      <div class="flex items-center justify-between px-3 pt-3">
        <span class="text-sm font-semibold text-slate-700">历史会话</span>
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-200/70 hover:text-slate-700"
          title="收起侧边栏"
          @click="toggleSidebar"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-5 w-5">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M9 4v16" stroke-linecap="round" />
          </svg>
        </button>
      </div>

      <!-- 新建对话 -->
      <div class="px-3 pb-3 pt-2">
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
          <!-- 展开侧边栏(仅在侧栏收起时显示) -->
          <button
            v-if="sidebarCollapsed"
            type="button"
            class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-sky-300 hover:text-sky-600"
            title="展开侧边栏"
            @click="toggleSidebar"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-5 w-5">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M9 4v16" stroke-linecap="round" />
            </svg>
          </button>
          <router-link
            v-if="backTo"
            :to="backTo"
            class="group inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-sky-300 hover:bg-sky-50 hover:text-sky-600"
            aria-label="返回"
            title="返回"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-5 w-5 transition-transform group-hover:-translate-x-0.5">
              <path d="M15 18l-6-6 6-6" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
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
      <!-- 空状态:历史热门问题(本数据源缓存命中最多的问题;点选逐字提问 → 必中 SQL 缓存,秒出结果) -->
      <div
        v-if="messages.length === 0 && hotQuestions.length > 0"
        class="mx-auto w-full max-w-5xl"
      >
        <section class="rounded-3xl border border-sky-100 bg-sky-50/70 p-4 sm:p-5">
          <div class="mb-4 flex items-center gap-2.5">
            <span
              class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-sky-500 to-indigo-500 text-white shadow-sm"
            >
              <!-- 火焰图标(热门) -->
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" />
              </svg>
            </span>
            <div>
              <p class="text-sm font-semibold text-slate-800">大家都在问</p>
              <p class="mt-0.5 text-xs text-slate-500">点击直接提问 · 历史已验证的问题，响应更快</p>
            </div>
          </div>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <button
              v-for="q in hotQuestions"
              :key="q"
              type="button"
              class="rounded-2xl border border-sky-200 bg-white px-4 py-3 text-left text-xs leading-6 text-slate-700 transition hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700 sm:text-sm"
              :disabled="isLoading"
              @click="askHotQuestion(q)"
            >
              {{ q }}
            </button>
          </div>
        </section>
      </div>

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
            :class="[
              'mt-6 rounded-3xl border p-4 sm:p-5',
              message.fanout ? 'border-amber-300 bg-amber-50/80' : 'border-sky-100 bg-sky-50/70',
            ]"
          >
            <div class="mb-3">
              <!-- 扇出风险:警告图标 + 危险色文案 -->
              <div v-if="message.fanout" class="flex items-start gap-2">
                <span class="text-base leading-6 sm:text-lg">⚠️</span>
                <p class="mt-0.5 text-xs font-semibold text-rose-600 sm:text-sm">
                  {{ message.fanoutMessage || '检测到扇出风险：直接统计会重复计算，请换一个更明确的口径。' }}
                </p>
              </div>
              <!-- 普通意图引导 -->
              <p v-else class="mt-1 text-xs text-sky-600/80 sm:text-sm">
                {{ guideText }}
              </p>
            </div>

            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <button
                v-for="guideQuery in message.guideQueries"
                :key="`${message.id}-${guideQuery}`"
                type="button"
                :class="[
                  'rounded-2xl border bg-white px-4 py-3 text-left text-xs leading-6 text-slate-700 transition sm:text-sm',
                  message.fanout
                    ? 'border-amber-200 hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700'
                    : 'border-sky-200 hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700',
                ]"
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
            class="mt-6 flex flex-wrap items-center gap-2.5"
          >
            <button
              v-if="message.sql"
              type="button"
              class="group inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700"
              @click="toggleSql(message)"
            >
              <!-- code 图标 -->
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <polyline points="16 18 22 12 16 6" />
                <polyline points="8 6 2 12 8 18" />
              </svg>
              查看 SQL
              <svg
                class="h-3.5 w-3.5 text-slate-400 transition-transform group-hover:text-sky-500"
                :class="isSqlExpanded(message) ? 'rotate-180' : ''"
                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>

            <button
              v-if="shouldShowResult(message.result)"
              type="button"
              class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-emerald-300 hover:bg-emerald-50 hover:text-emerald-700"
              @click="exportCsv(message)"
            >
              <!-- 下载图标 -->
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              导出数据
            </button>

            <!-- 归因分析:对该轮结果做"为什么变化"的多维拆解(新一轮 SSE 对话) -->
            <button
              v-if="shouldShowResult(message.result)"
              type="button"
              :disabled="isLoading"
              class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-slate-200 disabled:hover:bg-white disabled:hover:text-slate-600"
              @click="onAttribution(message)"
            >
              <!-- 分叉/拆解图标 -->
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="6" cy="6" r="3" />
                <circle cx="6" cy="18" r="3" />
                <circle cx="18" cy="12" r="3" />
                <path d="M9 6h4a2 2 0 0 1 2 2v0" />
                <path d="M9 18h4a2 2 0 0 0 2-2v0" />
              </svg>
              归因分析
            </button>

            <!-- 按需分析报告:对该轮结果生成 HTML 报告,新标签页打开 -->
            <button
              v-if="shouldShowResult(message.result)"
              type="button"
              :disabled="reportLoading[message.id]"
              class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-violet-300 hover:bg-violet-50 hover:text-violet-700 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-slate-200 disabled:hover:bg-white disabled:hover:text-slate-600"
              @click="onGenerateReport(message)"
            >
              <!-- 加载中:旋转 spinner;否则文档图标 -->
              <svg
                v-if="reportLoading[message.id]"
                class="h-3.5 w-3.5 animate-spin"
                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"
              >
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
              <svg
                v-else
                class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
              </svg>
              {{ reportLoading[message.id] ? '生成中…' : '生成分析报告' }}
            </button>

            <!-- 按需生成图表:有结果且尚未出图时显示;生成后(echarts 图)自动隐藏 -->
            <button
              v-if="shouldShowResult(message.result) && !isEChartsType(message.chartConfig?.chart_type)"
              type="button"
              :disabled="chartLoading[message.id]"
              class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition-colors hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-slate-200 disabled:hover:bg-white disabled:hover:text-slate-600"
              @click="onGenerateChart(message)"
            >
              <!-- 加载中:旋转 spinner;否则柱状图图标 -->
              <svg
                v-if="chartLoading[message.id]"
                class="h-3.5 w-3.5 animate-spin"
                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"
              >
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
              <svg
                v-else
                class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
              >
                <path d="M3 3v18h18" />
                <path d="M18 17V9" />
                <path d="M13 17V5" />
                <path d="M8 17v-3" />
              </svg>
              {{ chartLoading[message.id] ? '生成中…' : '生成图表' }}
            </button>
          </div>

          <!-- 出图后若无法生成任何图表(后端降级为表格),提醒用户(仍保留表格展示)。
               由 chartConfig 派生,历史回放同样会显示。 -->
          <p
            v-if="message.status === 'success' && isUnchartable(message)"
            class="mt-2.5 inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700"
          >
            <svg class="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            该数据无法生成图表
          </p>

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
            v-else-if="message.status === 'success' && displayConfig(message)"
            :config="displayConfig(message)!"
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
