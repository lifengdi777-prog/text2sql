<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  openEditSession,
  streamEditMessage,
  undoEdit,
  discardEditSession,
  downloadEdit,
  previewPage,
} from '@/services/dataset_edit'
import type { EditSheetPreview, EditTurn, EditOpRecord } from '@/types/datasetEdit'

const route = useRoute()
const router = useRouter()
const datasetId = Number(route.params.id)

const sessionId = ref<number | null>(null)
const sheets = ref<EditSheetPreview[]>([]) // 各 sheet 第0页(用于 tab:名称+行数)
const view = ref<EditSheetPreview | null>(null) // 当前展示的那一页
const pageBusy = ref(false)
const opsCount = ref(0)

const turns = ref<EditTurn[]>([])
const input = ref('')
const sending = ref(false)
const busy = ref(false) // 撤销/下载等
const loadError = ref('')
const loading = ref(true)

const activeSheet = computed(() => view.value?.sheet ?? '')

const EXAMPLES = ['把某列的空值填成 0', '删掉状态为停机的行', '新增一列“达标”', '把“产量”这列改名为“月产量”']

// 设置 sheet 列表,并把 view 定位到(之前的或第一个)sheet 的第 0 页
function setSheets(list: EditSheetPreview[]) {
  sheets.value = list
  const cur = view.value?.sheet
  view.value = list.find((s) => s.sheet === cur) ?? list[0] ?? null
}

async function loadPage(sheet: string, page: number) {
  if (!sessionId.value) return
  pageBusy.value = true
  try {
    view.value = await previewPage(datasetId, sessionId.value, sheet, page)
  } finally {
    pageBusy.value = false
  }
}

function selectSheet(sheet: string) {
  if (sheet !== view.value?.sheet) void loadPage(sheet, 0)
}
function prevPage() {
  if (view.value && view.value.page > 0) void loadPage(view.value.sheet, view.value.page - 1)
}
function nextPage() {
  if (view.value && view.value.page < view.value.pages - 1)
    void loadPage(view.value.sheet, view.value.page + 1)
}

function historyFromOps(ops: EditOpRecord[]): EditTurn[] {
  return ops.map((o) => ({
    id: `op-${o.seq}`,
    instruction: o.nl ?? '',
    steps: [],
    sql: o.sql,
    reason: null,
    status: 'success' as const,
    summary: o.affected,
    diff: o.affected?.changes?.length
      ? { cell_changes: o.affected.changes, deleted: [], renames: [], added_cols: [], dropped_cols: [] }
      : null,
    preview: null,
    pendingSql: null,
    hint: null,
    error: null,
    guidance: null,
  }))
}

onMounted(async () => {
  try {
    const resp = await openEditSession(datasetId)
    sessionId.value = resp.session_id
    opsCount.value = resp.ops_count
    setSheets(resp.sheets)
    turns.value = historyFromOps(resp.ops)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '打开编辑会话失败'
  } finally {
    loading.value = false
  }
})

function upsertTurn(turn: EditTurn) {
  const i = turns.value.findIndex((t) => t.id === turn.id)
  if (i >= 0) turns.value[i] = turn
  else turns.value.push(turn)
}

async function send(instruction: string, confirmed: boolean) {
  if (!sessionId.value || !instruction.trim() || sending.value) return
  sending.value = true
  try {
    const last = await streamEditMessage(
      datasetId,
      sessionId.value,
      instruction.trim(),
      confirmed,
      view.value?.sheet ?? null, // 当前选中的 sheet → 默认操作对象
      { onStep: upsertTurn },
    )
    // 应用成功 → 用受影响 sheet 的预览页刷新左栏(新增行时后端已给末页),更新 tab 行数 + 计数
    if (last.status === 'success' && last.preview) {
      const pv = last.preview
      const tab = sheets.value.find((s) => s.sheet === pv.sheet)
      if (tab) tab.total = pv.total // 同步 tab 行数(增删行后变化)
      else sheets.value.push(pv) // 新建的汇总 sheet → 加一个 tab
      view.value = pv
      opsCount.value += 1
    }
  } catch (e) {
    const t = [...turns.value].reverse().find((x) => x.status === 'streaming')
    if (t) {
      t.status = 'error'
      t.error = e instanceof Error ? e.message : '请求失败'
    }
  } finally {
    sending.value = false
  }
}

function onSend() {
  const text = input.value
  input.value = ''
  void send(text, false)
}
function onConfirm(turn: EditTurn) {
  void send(turn.instruction, true)
}

async function onUndo() {
  if (!sessionId.value || busy.value) return
  busy.value = true
  try {
    const resp = await undoEdit(datasetId, sessionId.value)
    opsCount.value = resp.ops_count
    setSheets(resp.sheets)
    turns.value = historyFromOps(resp.ops)
  } finally {
    busy.value = false
  }
}

async function onDownload() {
  if (!sessionId.value || busy.value) return
  busy.value = true
  try {
    await downloadEdit(datasetId, sessionId.value)
  } finally {
    busy.value = false
  }
}

async function onDiscard() {
  if (sessionId.value) {
    try {
      await discardEditSession(datasetId, sessionId.value)
    } catch {
      /* 忽略,直接回列表 */
    }
  }
  router.push('/datasets')
}
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden">
    <!-- 顶栏 -->
    <header class="mb-4 flex items-center gap-3">
      <button
        type="button"
        class="rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50"
        @click="router.push('/datasets')"
      >
        ← 返回
      </button>
      <div class="flex items-center gap-2 text-slate-800">
        <span aria-hidden="true">✦</span>
        <h1 class="text-lg font-semibold">智能助手</h1>
        <span class="text-xs text-slate-400">已应用 {{ opsCount }} 步编辑</span>
      </div>
      <div class="ml-auto flex items-center gap-2">
        <button
          type="button"
          class="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
          :disabled="busy || opsCount === 0"
          @click="onUndo"
        >
          ↩ 撤销
        </button>
        <button
          type="button"
          class="rounded-lg bg-sky-500 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-sky-600 disabled:bg-sky-300"
          :disabled="busy"
          @click="onDownload"
        >
          ⬇ 下载
        </button>
        <button
          type="button"
          class="rounded-lg border border-rose-200 px-3 py-1.5 text-sm text-rose-600 transition hover:bg-rose-50"
          @click="onDiscard"
        >
          ✕ 放弃
        </button>
      </div>
    </header>

    <p v-if="loadError" class="py-16 text-center text-sm text-rose-500">{{ loadError }}</p>
    <p v-else-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</p>

    <!-- 主体:左预览 + 右问答 -->
    <div v-else class="flex min-h-0 flex-1 gap-4">
      <!-- 左:预览 -->
      <section
        class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white"
      >
        <!-- sheet tabs -->
        <div class="flex items-center gap-1 border-b border-slate-200 px-3 py-2">
          <button
            v-for="s in sheets"
            :key="s.sheet"
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm transition"
            :class="
              s.sheet === activeSheet
                ? 'bg-indigo-50 font-semibold text-indigo-600'
                : 'text-slate-500 hover:bg-slate-50'
            "
            @click="selectSheet(s.sheet)"
          >
            {{ s.sheet }}
            <span class="ml-1 text-xs text-slate-400">{{ s.total }}</span>
          </button>
        </div>
        <!-- 表格 -->
        <div class="min-h-0 flex-1 overflow-auto">
          <table v-if="view" class="w-full text-left text-xs">
            <thead class="sticky top-0 bg-slate-50 text-slate-500">
              <tr>
                <th
                  v-for="col in view.columns"
                  :key="col"
                  class="whitespace-nowrap px-3 py-2 font-medium"
                >
                  {{ col }}
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
              <tr v-for="(row, ri) in view.rows" :key="ri" class="hover:bg-slate-50/60">
                <td
                  v-for="col in view.columns"
                  :key="col"
                  class="whitespace-nowrap px-3 py-1.5 text-slate-700"
                >
                  {{ row[col] ?? '' }}
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="py-10 text-center text-sm text-slate-400">无数据</p>
        </div>
        <!-- 分页 -->
        <div
          v-if="view && view.pages > 1"
          class="flex items-center justify-center gap-3 border-t border-slate-200 px-3 py-2 text-xs text-slate-500"
        >
          <button
            type="button"
            class="rounded-lg border border-slate-200 px-2.5 py-1 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="pageBusy || view.page <= 0"
            @click="prevPage"
          >
            上一页
          </button>
          <span>第 {{ view.page + 1 }} / {{ view.pages }} 页 · 共 {{ view.total }} 行</span>
          <button
            type="button"
            class="rounded-lg border border-slate-200 px-2.5 py-1 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="pageBusy || view.page >= view.pages - 1"
            @click="nextPage"
          >
            下一页
          </button>
        </div>
      </section>

      <!-- 右:问答 -->
      <aside class="flex w-[360px] shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div class="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700">
          🤖 对话编辑
        </div>

        <!-- 对话流 -->
        <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
          <p v-if="turns.length === 0" class="pt-6 text-center text-xs text-slate-400">
            用自然语言描述你想怎么改这份表
          </p>

          <div v-for="t in turns" :key="t.id" class="space-y-1.5">
            <!-- 用户气泡 -->
            <div class="flex justify-end">
              <div class="max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-500 px-3 py-2 text-xs text-white">
                {{ t.instruction }}
              </div>
            </div>
            <!-- 助手气泡 -->
            <div class="rounded-2xl rounded-bl-sm bg-slate-50 px-3 py-2 text-xs text-slate-700">
              <!-- 步骤 -->
              <div class="space-y-0.5 text-[11px] text-slate-400">
                <div v-for="s in t.steps" :key="s.step">
                  <span v-if="s.status === 'success'" class="text-emerald-500">✓</span>
                  <span v-else-if="s.status === 'error'" class="text-rose-500">✕</span>
                  <span v-else class="text-slate-300">…</span>
                  {{ s.step }}
                </div>
              </div>

              <!-- 待确认 -->
              <div v-if="t.status === 'need_confirm'" class="mt-2 rounded-lg bg-amber-50 p-2">
                <p class="text-[11px] text-amber-700">⚠ {{ t.hint }}</p>
                <p v-if="t.summary" class="mt-1 text-[11px] text-amber-600">
                  预计:改 {{ t.summary.changed }} · 删 {{ t.summary.deleted }} 行
                </p>
                <button
                  type="button"
                  class="mt-2 rounded-lg bg-amber-500 px-3 py-1 text-[11px] font-semibold text-white transition hover:bg-amber-600 disabled:opacity-50"
                  :disabled="sending"
                  @click="onConfirm(t)"
                >
                  确认执行
                </button>
              </div>

              <!-- 成功摘要 -->
              <div v-else-if="t.status === 'success' && t.summary" class="mt-1.5 text-emerald-600">
                <template v-if="t.summary.created_sheet">
                  ✓ 已生成汇总表「{{ t.summary.created_sheet }}」({{ t.summary.rows }} 行)
                </template>
                <template v-else>
                  ✓ 已应用:改 {{ t.summary.changed }} 格 · 删 {{ t.summary.deleted }} 行
                  <template v-if="t.summary.new_rows">· 加 {{ t.summary.new_rows }} 行</template>
                  <template v-if="t.summary.added_cols.length">· 加列 {{ t.summary.added_cols.join('、') }}</template>
                  <template v-if="t.summary.dropped_cols.length">· 删列 {{ t.summary.dropped_cols.join('、') }}</template>
                  <template v-if="t.summary.renames.length">· 改名 {{ t.summary.renames.join('、') }}</template>
                  <ul v-if="t.diff && t.diff.cell_changes.length" class="mt-1 space-y-0.5 text-[11px] text-slate-500">
                    <li v-for="(c, ci) in t.diff.cell_changes.slice(0, 5)" :key="ci">
                      {{ c.col }}: {{ c.old }} → {{ c.new }}
                    </li>
                  </ul>
                </template>
              </div>

              <!-- 查询类 → 引导去问数 -->
              <div v-else-if="t.guidance" class="mt-1.5 rounded-lg bg-sky-50 p-2 text-[11px] text-sky-700">
                💡 {{ t.guidance }}
              </div>

              <!-- 失败 -->
              <div v-else-if="t.status === 'error'" class="mt-1.5 text-rose-500">✕ {{ t.error }}</div>

              <!-- 查看 SQL -->
              <details v-if="t.sql" class="mt-1.5">
                <summary class="cursor-pointer text-[11px] text-slate-400">查看 SQL</summary>
                <pre class="mt-1 overflow-x-auto rounded bg-slate-900 p-2 text-[10px] text-slate-100">{{ t.sql }}</pre>
              </details>
            </div>
          </div>
        </div>

        <!-- 输入 -->
        <div class="border-t border-slate-200 p-3">
          <div class="mb-1.5 flex flex-wrap gap-1">
            <button
              v-for="ex in EXAMPLES"
              :key="ex"
              type="button"
              class="rounded-full border border-slate-200 px-2 py-0.5 text-[10px] text-slate-500 transition hover:bg-slate-50"
              @click="input = ex"
            >
              {{ ex }}
            </button>
          </div>
          <div class="flex items-end gap-2">
            <textarea
              v-model="input"
              rows="2"
              placeholder="输入编辑指令…"
              class="min-w-0 flex-1 resize-none rounded-xl border border-slate-200 px-3 py-2 text-xs text-slate-700 outline-none focus:border-indigo-300"
              @keydown.enter.exact.prevent="onSend"
            />
            <button
              type="button"
              class="rounded-xl bg-indigo-500 px-3 py-2 text-xs font-semibold text-white transition hover:bg-indigo-600 disabled:bg-indigo-300"
              :disabled="sending || !input.trim()"
              @click="onSend"
            >
              {{ sending ? '处理中' : '发送' }}
            </button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
