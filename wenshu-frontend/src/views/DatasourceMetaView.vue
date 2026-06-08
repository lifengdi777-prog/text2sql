<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getDatasourceMeta,
  saveDatasourceTables,
  saveDatasourceMetrics,
  saveDatasourceRelationships,
} from '@/services/datasource'
import type { ColumnRole, DatasourceMeta, MetaColumn, MetaRelationship } from '@/types/datasource'
import { effectiveColumnRole } from '@/lib/metaRoles'
import RelationshipErEditor from '@/components/er/RelationshipErEditor.vue'

const route = useRoute()
const router = useRouter()
const id = route.params.id as string
const name = computed(() => (route.query.name as string) || id)

const meta = ref<DatasourceMeta | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')

// 保存确认弹窗:必须输入登录账号密码 + 该数据源的数据库密码,后端校验通过才保存
const confirmOpen = ref(false)
const userPwd = ref('')
const dbPwd = ref('')
const confirmError = ref('')

// ── Tab + 搜索 + 分页 ──────────────────────────────
const tab = ref<'tables' | 'metrics' | 'relations'>('tables')
const tableSearch = ref('')
const selectedTableName = ref('')   // 主从布局:当前选中的表名
const metricSearch = ref('')
const selectedMetricIdx = ref(-1)   // 主从布局:当前选中指标在 meta.metrics 里的下标

// 表搜索:匹配表名 / 表描述 / 任一列名·列描述·列别名
const filteredTables = computed(() => {
  const all = meta.value?.tables ?? []
  const q = tableSearch.value.trim().toLowerCase()
  if (!q) return all
  return all.filter(
    (t) =>
      t.name.toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q) ||
      t.columns.some(
        (c) =>
          c.name.toLowerCase().includes(q) ||
          (c.description || '').toLowerCase().includes(q) ||
          c.alias.some((a) => a.toLowerCase().includes(q)),
      ),
  )
})
// 主从布局:当前选中的表(找不到 → 取过滤后第一张)。返回的是 meta.tables 里的真实对象,编辑即生效。
const selectedTable = computed(() => {
  const list = filteredTables.value
  return list.find((t) => t.name === selectedTableName.value) ?? list[0] ?? null
})
function selectTable(n: string) {
  selectedTableName.value = n
}

// 指标搜索:带上在完整数组里的下标(删除/编辑用),匹配名/口径/别名/关联列
const filteredMetrics = computed(() => {
  const all = meta.value?.metrics ?? []
  const q = metricSearch.value.trim().toLowerCase()
  const withIdx = all.map((m, idx) => ({ m, idx }))
  if (!q) return withIdx
  return withIdx.filter(
    ({ m }) =>
      m.name.toLowerCase().includes(q) ||
      (m.description || '').toLowerCase().includes(q) ||
      m.alias.some((a) => a.toLowerCase().includes(q)) ||
      m.relevant_columns.some((c) => c.toLowerCase().includes(q)),
  )
})
// 主从布局:当前选中指标(找不到 → 过滤后第一条)。{m,idx},m 是 meta.metrics 里的真实对象,编辑即生效。
const selectedMetric = computed(() => {
  const list = filteredMetrics.value
  return list.find((p) => p.idx === selectedMetricIdx.value) ?? list[0] ?? null
})
function selectMetric(idx: number) {
  selectedMetricIdx.value = idx
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    meta.value = await getDatasourceMeta(id)
    selectedTableName.value = meta.value?.tables?.[0]?.name ?? ''   // 默认选中第一张表
    snapshotRelationships()   // 快照已保存的表关系,供「还原」一键重置
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function isNonKey(r: ColumnRole): boolean {
  return r === 'dimension' || r === 'measure'
}

// 列在当前(可能含未保存关系编辑的)ER 关系下的有效角色,用于即时预览徽章。
function effRole(c: MetaColumn): ColumnRole {
  return effectiveColumnRole(c.role, selectedTable.value?.name ?? '', c.name, meta.value?.relationships ?? [])
}

function roleLabel(r: ColumnRole): string {
  return ({ primary_key: '主键', foreign_key: '外键', dimension: '维度', measure: '度量' } as const)[r]
}

// 徽章配色:外键=蓝(与 ER 蓝链接一致)、主键=琥珀(与 ER 金钥匙一致),其余灰。
function roleBadgeClass(r: ColumnRole): string {
  if (r === 'foreign_key') return 'bg-sky-50 text-sky-600'
  if (r === 'primary_key') return 'bg-amber-50 text-amber-600'
  return 'bg-slate-100 text-slate-400'
}

function editableRole(c: MetaColumn): boolean {
  // 角色可在「维度/度量」间切换的前提:持久化与有效角色都不是主外键(纯维度/度量列)。
  // 这样关系新增使某列即时变外键 → 立即只读;关系删除使外键即时预览为维度 → 仍只读,
  // 待「保存关系」落库、重进编辑页后才成为真正可编辑的维度(避免本地改 role 经表保存漂移)。
  return isNonKey(c.role) && isNonKey(effRole(c))
}

// 表类型 → 徽章文案 + 配色
function tableRole(r: string): { label: string; cls: string } {
  if (r === 'fact') return { label: '事实表', cls: 'bg-emerald-50 text-emerald-600' }
  if (r === 'bridge') return { label: '桥接表', cls: 'bg-amber-50 text-amber-600' }
  return { label: '维度表', cls: 'bg-sky-50 text-sky-600' }
}

// 别名/关联列:数组 ↔ 顿号分隔字符串
function joinList(arr: string[]): string {
  return arr.join('、')
}
function parseList(s: string): string[] {
  return s.split(/[,，、\n]/).map((x) => x.trim()).filter(Boolean)
}

function addMetric() {
  if (!meta.value) return
  meta.value.metrics.push({ name: '新指标', description: '', relevant_columns: [], alias: [] })
  tab.value = 'metrics'
  metricSearch.value = ''
  selectedMetricIdx.value = meta.value.metrics.length - 1   // 选中刚加的
}
function removeMetric(idx: number) {
  meta.value?.metrics.splice(idx, 1)
  selectedMetricIdx.value = -1   // 选中回落到第一条
}

// ── 表关系(单独编辑,不从 DB 拉,直接写 data_relationship) ──────────
// 两种视图:'er' 可视化拖拽(默认) / 'list' 下拉框列表,共用同一份 meta.relationships
const relationView = ref<'er' | 'list'>('er')
const tableNames = computed(() => meta.value?.tables.map((t) => t.name) ?? [])
function columnsOf(tableName: string): string[] {
  return meta.value?.tables.find((t) => t.name === tableName)?.columns.map((c) => c.name) ?? []
}

function addRelation() {
  meta.value?.relationships.push({
    from_table: '', from_column: '', to_table: '', to_column: '', description: null,
  })
}
function removeRelation(idx: number) {
  meta.value?.relationships.splice(idx, 1)
}

// ── 表关系「还原」:把本次未保存的增删丢弃,重置回上次保存(=进页面时加载)的状态。──
// 保存表关系会跳回数据源列表,故"进页面时的快照"即"上次保存态"。
const savedRelationships = ref<MetaRelationship[]>([])
function snapshotRelationships() {
  savedRelationships.value = (meta.value?.relationships ?? []).map((r) => ({ ...r }))
}
function relSig(list: MetaRelationship[]): string {
  return list
    .map((r) => `${r.from_table}.${r.from_column}->${r.to_table}.${r.to_column}#${r.description ?? ''}`)
    .sort()
    .join('|')
}
// 有未保存改动才允许「还原」(也避免误点清空当前编辑)。
const relationsDirty = computed(
  () => relSig(meta.value?.relationships ?? []) !== relSig(savedRelationships.value),
)
function restoreRelationships() {
  if (!meta.value) return
  meta.value.relationships = savedRelationships.value.map((r) => ({ ...r }))
}

// 顶部保存按钮文案随 Tab 变
const saveLabel = computed(() =>
  tab.value === 'tables' ? '保存表元数据' : tab.value === 'metrics' ? '保存指标信息' : '保存表关系',
)

function openConfirm() {
  if (!meta.value) return
  userPwd.value = ''
  dbPwd.value = ''
  confirmError.value = ''
  confirmOpen.value = true
}

async function doSave() {
  if (!meta.value || saving.value) return
  if (!userPwd.value || !dbPwd.value) {
    confirmError.value = '请输入账号密码和数据库密码'
    return
  }
  saving.value = true
  confirmError.value = ''
  try {
    if (tab.value === 'tables') {
      await saveDatasourceTables(id, meta.value.tables, userPwd.value, dbPwd.value)
    } else if (tab.value === 'metrics') {
      await saveDatasourceMetrics(id, meta.value.metrics, userPwd.value, dbPwd.value)
    } else {
      // 只存四个端点都填了的边
      const edges = meta.value.relationships.filter(
        (r) => r.from_table && r.from_column && r.to_table && r.to_column,
      )
      await saveDatasourceRelationships(
        id, edges, userPwd.value, dbPwd.value,
        meta.value.join_max_extra, meta.value.join_k,
      )
    }
    // 表/指标是异步重物化(回列表看 building→ready);关系即时生效。统一回数据源列表。
    router.push('/sources')
  } catch (e) {
    confirmError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden">
    <!-- 头部 -->
    <div class="mb-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <button
          type="button"
          class="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50"
          @click="router.push('/sources')"
        >
          ← 返回
        </button>
        <div>
          <h1 class="text-xl font-semibold tracking-tight text-slate-900">编辑元数据</h1>
          <p class="text-xs text-slate-400">{{ name }}</p>
        </div>
      </div>
      <button
        type="button"
        class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
        :disabled="saving || loading || !meta"
        @click="openConfirm"
      >
        {{ saveLabel }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{{ error }}</p>
    <p v-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</p>

    <div v-else-if="meta" class="flex flex-1 flex-col overflow-hidden">
      <!-- Tab 切换 -->
      <div class="mb-3 flex items-center gap-1 border-b border-slate-200">
        <button
          type="button"
          class="-mb-px border-b-2 px-4 py-2 text-sm font-medium transition"
          :class="tab === 'tables' ? 'border-emerald-500 text-emerald-600' : 'border-transparent text-slate-500 hover:text-slate-700'"
          @click="tab = 'tables'"
        >
          表的元数据
        </button>
        <button
          type="button"
          class="-mb-px border-b-2 px-4 py-2 text-sm font-medium transition"
          :class="tab === 'metrics' ? 'border-emerald-500 text-emerald-600' : 'border-transparent text-slate-500 hover:text-slate-700'"
          @click="tab = 'metrics'"
        >
          指标信息
        </button>
        <button
          type="button"
          class="-mb-px border-b-2 px-4 py-2 text-sm font-medium transition"
          :class="tab === 'relations' ? 'border-emerald-500 text-emerald-600' : 'border-transparent text-slate-500 hover:text-slate-700'"
          @click="tab = 'relations'"
        >
          表关系
        </button>
      </div>

      <!-- ===== 表的元数据(主从两栏)===== -->
      <div v-if="tab === 'tables'" class="flex min-h-0 flex-1 gap-4 overflow-hidden">
        <!-- 左:表目录 -->
        <aside class="flex w-60 shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div class="border-b border-slate-100 p-3">
            <div class="relative">
              <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4">
                  <circle cx="11" cy="11" r="7" />
                  <path d="m21 21-4.3-4.3" />
                </svg>
              </span>
              <input
                v-model="tableSearch"
                type="search"
                placeholder="搜索 表名 / 字段…"
                class="h-9 w-full rounded-xl border border-slate-200 pl-9 pr-3 text-sm outline-none focus:border-sky-300"
              />
            </div>
            <p class="mt-2 text-[11px] text-slate-400">共 {{ filteredTables.length }} 张表</p>
          </div>
          <div class="flex-1 overflow-y-auto p-2">
            <p v-if="filteredTables.length === 0" class="py-8 text-center text-xs text-slate-400">没有匹配的表</p>
            <button
              v-for="t in filteredTables"
              :key="t.name"
              type="button"
              class="mb-1 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left transition"
              :class="t.name === selectedTable?.name ? 'bg-sky-50 ring-1 ring-sky-200' : 'hover:bg-slate-50'"
              @click="selectTable(t.name)"
            >
              <span class="min-w-0 flex-1">
                <span
                  class="block truncate font-mono text-base font-semibold"
                  :class="t.name === selectedTable?.name ? 'text-sky-700' : 'text-slate-700'"
                >{{ t.name }}</span>
                <span class="text-xs text-slate-400">{{ t.columns.length }} 列</span>
              </span>
              <span class="shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium" :class="tableRole(t.role).cls">
                {{ tableRole(t.role).label }}
              </span>
            </button>
          </div>
        </aside>

        <!-- 右:选中表的编辑区 -->
        <section v-if="selectedTable" class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div class="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-3">
            <span class="font-mono text-sm font-semibold text-slate-800">{{ selectedTable.name }}</span>
            <select
              v-model="selectedTable.role"
              class="rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 outline-none focus:border-sky-300"
            >
              <option value="dim">维度表 dim</option>
              <option value="fact">事实表 fact</option>
              <option value="bridge">桥接表 bridge</option>
            </select>
            <span class="ml-auto inline-flex items-center gap-1 text-xs text-slate-400">
              <span class="inline-block h-2.5 w-2.5 rounded-sm bg-slate-100 ring-1 ring-slate-200" />灰色 = 物理事实,不可改
            </span>
          </div>

          <div class="flex-1 overflow-y-auto px-4 py-3">
            <label class="block text-xs font-medium text-slate-400">表的描述</label>
            <textarea
              v-model="selectedTable.description"
              rows="2"
              placeholder="这张表在业务中代表什么、典型用于哪些分析"
              class="mt-0.5 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-sky-300"
            />

            <table class="mt-3 w-full text-left text-sm">
              <thead class="text-slate-400">
                <tr class="border-b border-slate-100">
                  <th class="py-1.5 pr-3 font-medium">列名</th>
                  <th class="py-1.5 pr-3 font-medium">类型</th>
                  <th class="py-1.5 pr-3 font-medium">角色</th>
                  <th class="py-1.5 pr-3 font-medium">描述</th>
                  <th class="py-1.5 pr-3 font-medium">别名（顿号/逗号分隔）</th>
                  <th class="py-1.5 pr-3 text-center font-medium">是否建立索引</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="c in selectedTable.columns"
                  :key="c.name"
                  class="border-b border-slate-50 align-top transition hover:bg-slate-50/60"
                >
                  <td class="py-2 pr-3 font-mono text-slate-700">{{ c.name }}</td>
                  <td class="py-2 pr-3">
                    <span class="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-400">{{ c.type }}</span>
                  </td>
                  <td class="py-2 pr-3">
                    <select
                      v-if="editableRole(c)"
                      v-model="c.role"
                      class="rounded border px-1.5 py-1 text-sm outline-none transition focus:border-sky-300"
                      :class="c.role === 'dimension' ? 'border-sky-200 bg-sky-50 text-sky-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'"
                    >
                      <option value="dimension">维度</option>
                      <option value="measure">度量</option>
                    </select>
                    <span
                      v-else
                      class="inline-flex items-center rounded px-1.5 py-0.5 text-xs"
                      :class="roleBadgeClass(effRole(c))"
                    >
                      {{ roleLabel(effRole(c)) }}
                    </span>
                  </td>
                  <td class="py-2 pr-3">
                    <input
                      v-model="c.description"
                      type="text"
                      class="w-full min-w-[12rem] rounded border border-slate-200 px-2 py-1 text-sm outline-none focus:border-sky-300"
                    />
                  </td>
                  <td class="py-2 pr-3">
                    <input
                      :value="joinList(c.alias)"
                      type="text"
                      :disabled="!editableRole(c)"
                      placeholder="主外键不需别名"
                      class="w-full min-w-[12rem] rounded border border-slate-200 px-2 py-1 text-sm outline-none focus:border-sky-300 disabled:bg-slate-50 disabled:text-slate-300"
                      @input="c.alias = parseList(($event.target as HTMLInputElement).value)"
                    />
                  </td>
                  <td class="py-2 pr-3 text-center">
                    <label class="relative inline-flex cursor-pointer items-center align-middle">
                      <input type="checkbox" v-model="c.sync" class="peer sr-only" />
                      <div class="h-4 w-7 rounded-full bg-slate-200 transition after:absolute after:left-0.5 after:top-0.5 after:h-3 after:w-3 after:rounded-full after:bg-white after:transition-all peer-checked:bg-emerald-400 peer-checked:after:translate-x-3" />
                    </label>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
        <section v-else class="flex flex-1 items-center justify-center rounded-2xl border border-slate-200 bg-white text-sm text-slate-400">
          没有匹配的表
        </section>
      </div>

      <!-- ===== 指标信息(主从两栏)===== -->
      <div v-else-if="tab === 'metrics'" class="flex min-h-0 flex-1 gap-4 overflow-hidden">
        <!-- 左:指标目录 -->
        <aside class="flex w-60 shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div class="space-y-2 border-b border-slate-100 p-3">
            <div class="relative">
              <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4">
                  <circle cx="11" cy="11" r="7" />
                  <path d="m21 21-4.3-4.3" />
                </svg>
              </span>
              <input
                v-model="metricSearch"
                type="search"
                placeholder="搜索 指标名 / 别名…"
                class="h-9 w-full rounded-xl border border-slate-200 pl-9 pr-3 text-sm outline-none focus:border-sky-300"
              />
            </div>
            <button
              type="button"
              class="w-full rounded-xl border border-emerald-200 px-3 py-1.5 text-sm font-medium text-emerald-600 transition hover:bg-emerald-50"
              @click="addMetric"
            >
              ＋ 新增指标
            </button>
          </div>
          <div class="flex-1 overflow-y-auto p-2">
            <p v-if="filteredMetrics.length === 0" class="py-8 text-center text-xs text-slate-400">
              {{ metricSearch ? '没有匹配的指标' : '暂无指标,点上方「新增指标」' }}
            </p>
            <button
              v-for="{ m, idx } in filteredMetrics"
              :key="idx"
              type="button"
              class="mb-1 block w-full rounded-xl px-3 py-2 text-left transition"
              :class="idx === selectedMetric?.idx ? 'bg-sky-50 ring-1 ring-sky-200' : 'hover:bg-slate-50'"
              @click="selectMetric(idx)"
            >
              <span
                class="block truncate text-sm font-semibold"
                :class="idx === selectedMetric?.idx ? 'text-sky-700' : 'text-slate-700'"
              >{{ m.name || '未命名指标' }}</span>
              <span class="block truncate text-xs text-slate-400">{{ m.alias.length ? m.alias.join('、') : '无别名' }}</span>
            </button>
          </div>
        </aside>

        <!-- 右:选中指标的编辑区 -->
        <section v-if="selectedMetric" class="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div class="flex items-center gap-2 border-b border-slate-100 px-4 py-3">
            <span class="text-sm font-semibold text-slate-800">编辑指标</span>
            <button
              type="button"
              class="ml-auto rounded-lg px-2.5 py-1 text-xs text-slate-400 transition hover:bg-rose-50 hover:text-rose-500"
              @click="removeMetric(selectedMetric.idx)"
            >
              删除该指标
            </button>
          </div>
          <div class="flex-1 space-y-3 overflow-y-auto px-4 py-3">
            <div>
              <label class="block text-xs font-medium text-slate-400">指标名称</label>
              <input
                v-model="selectedMetric.m.name"
                type="text"
                placeholder="如 良品率"
                class="mt-0.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-sm font-medium outline-none focus:border-sky-300"
              />
            </div>
            <div>
              <label class="block text-xs font-medium text-slate-400">口径说明（算法）</label>
              <textarea
                v-model="selectedMetric.m.description"
                rows="3"
                placeholder="如：良品率 = 合格数量 / 实际产量"
                class="mt-0.5 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-sky-300"
              />
            </div>
            <div>
              <label class="block text-xs font-medium text-slate-400">关联列（表名.列名，顿号/逗号分隔）</label>
              <input
                :value="joinList(selectedMetric.m.relevant_columns)"
                type="text"
                placeholder="如 table_order.order_amount、table_order.order_id"
                class="mt-0.5 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-sky-300"
                @input="selectedMetric.m.relevant_columns = parseList(($event.target as HTMLInputElement).value)"
              />
            </div>
            <div>
              <label class="block text-xs font-medium text-slate-400">别名（同义词，顿号/逗号分隔）</label>
              <input
                :value="joinList(selectedMetric.m.alias)"
                type="text"
                placeholder="如 销售额、营收、GMV"
                class="mt-0.5 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-sky-300"
                @input="selectedMetric.m.alias = parseList(($event.target as HTMLInputElement).value)"
              />
            </div>
          </div>
        </section>
        <section v-else class="flex flex-1 items-center justify-center rounded-2xl border border-slate-200 bg-white text-sm text-slate-400">
          {{ metricSearch ? '没有匹配的指标' : '暂无指标,点左侧「新增指标」' }}
        </section>
      </div>

      <!-- ===== 表关系 ===== -->
      <div v-else class="flex flex-1 flex-col overflow-hidden">
        <div class="mb-3 flex items-start justify-between gap-3">
          <p class="text-xs text-slate-500">
            关系用于多表 JOIN 与扇出检测。首次接入会从数据库外键种入；此处可人工增删，点右上角「保存关系」<b>即时生效</b>（不重建索引）。
          </p>
          <div class="flex shrink-0 items-center gap-2">
            <!-- 视图切换:ER 图 / 列表 -->
            <div class="flex overflow-hidden rounded-lg border border-slate-200 text-sm">
              <button
                type="button"
                class="px-3 py-1.5 transition"
                :class="relationView === 'er' ? 'bg-emerald-500 text-white' : 'text-slate-500 hover:bg-slate-50'"
                @click="relationView = 'er'"
              >
                ER 图
              </button>
              <button
                type="button"
                class="px-3 py-1.5 transition"
                :class="relationView === 'list' ? 'bg-emerald-500 text-white' : 'text-slate-500 hover:bg-slate-50'"
                @click="relationView = 'list'"
              >
                列表
              </button>
            </div>
            <button
              v-if="relationView === 'list'"
              type="button"
              class="rounded-xl border border-emerald-200 px-4 py-2 text-sm font-medium text-emerald-600 transition hover:bg-emerald-50"
              @click="addRelation"
            >
              ＋ 新增关系
            </button>
            <button
              type="button"
              class="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="!relationsDirty"
              title="丢弃本次未保存的增删,还原回上次保存的表关系"
              @click="restoreRelationships"
            >
              还原
            </button>
          </div>
        </div>

        <!-- JOIN 选路参数(每数据源,管理员可调):随「保存表关系」一起存 -->
        <div class="mb-3 flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-slate-50/60 px-4 py-2.5">
          <span class="text-xs font-semibold text-slate-600">JOIN 选路</span>
          <label class="flex items-center gap-2 text-xs text-slate-500">
            候选额外跳数
            <input
              v-model.number="meta.join_max_extra" type="number" min="0" max="5"
              class="h-8 w-16 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 outline-none focus:border-emerald-300"
            />
          </label>
          <label class="flex items-center gap-2 text-xs text-slate-500">
            候选最多条数
            <input
              v-model.number="meta.join_k" type="number" min="1" max="10"
              class="h-8 w-16 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 outline-none focus:border-emerald-300"
            />
          </label>
          <span class="text-xs text-slate-400">默认 1 / 3;稠密或"一表多重关系"的库可调大,普通星型/雪花库保持默认即可</span>
        </div>

        <!-- ER 图视图:拖线增 / 点线删,共用 meta.relationships -->
        <RelationshipErEditor
          v-if="relationView === 'er'"
          :tables="meta.tables"
          v-model="meta.relationships"
          class="flex-1 overflow-hidden"
        />

        <!-- 列表视图 -->
        <div v-else class="flex-1 overflow-y-auto pr-1 pb-4">
          <p v-if="meta.relationships.length === 0" class="py-10 text-center text-sm text-slate-400">
            暂无关系，点右上角「新增关系」
          </p>
          <div
            v-for="(r, ri) in meta.relationships"
            :key="ri"
            class="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 text-xs shadow-sm"
          >
            <select
              v-model="r.from_table"
              class="rounded border border-slate-200 px-2 py-1 outline-none focus:border-sky-300"
              @change="r.from_column = ''"
            >
              <option value="" disabled>本表</option>
              <option v-for="t in tableNames" :key="t" :value="t">{{ t }}</option>
            </select>
            <span class="text-slate-400">.</span>
            <select
              v-model="r.from_column"
              class="rounded border border-slate-200 px-2 py-1 outline-none focus:border-sky-300"
            >
              <option value="" disabled>列</option>
              <option v-for="c in columnsOf(r.from_table)" :key="c" :value="c">{{ c }}</option>
            </select>
            <span class="px-1 font-semibold text-slate-400">→</span>
            <select
              v-model="r.to_table"
              class="rounded border border-slate-200 px-2 py-1 outline-none focus:border-sky-300"
              @change="r.to_column = ''"
            >
              <option value="" disabled>目标表</option>
              <option v-for="t in tableNames" :key="t" :value="t">{{ t }}</option>
            </select>
            <span class="text-slate-400">.</span>
            <select
              v-model="r.to_column"
              class="rounded border border-slate-200 px-2 py-1 outline-none focus:border-sky-300"
            >
              <option value="" disabled>列</option>
              <option v-for="c in columnsOf(r.to_table)" :key="c" :value="c">{{ c }}</option>
            </select>
            <button
              type="button"
              class="ml-auto rounded px-2 py-1 text-slate-400 transition hover:bg-slate-100 hover:text-rose-500"
              @click="removeRelation(ri)"
            >
              删除
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 保存确认:双密码校验 -->
    <div
      v-if="confirmOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      @click.self="confirmOpen = false"
    >
      <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <h3 class="text-base font-semibold text-slate-800">确认保存</h3>
        <p class="mt-1 text-xs text-slate-500">
          保存会重建该数据源的向量与值索引。请输入账号密码与该数据源的数据库密码以确认。
        </p>
        <div class="mt-4 space-y-3">
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-500">登录账号密码</label>
            <input
              v-model="userPwd"
              type="password"
              autocomplete="current-password"
              class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300"
            />
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-500">数据库密码（该数据源）</label>
            <input
              v-model="dbPwd"
              type="password"
              autocomplete="off"
              class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300"
            />
          </div>
        </div>
        <p v-if="confirmError" class="mt-3 text-xs text-rose-500">{{ confirmError }}</p>
        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
            :disabled="saving"
            @click="confirmOpen = false"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
            :disabled="saving || !userPwd || !dbPwd"
            @click="doSave"
          >
            {{ saving ? '保存中（重建索引）…' : '确认保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
