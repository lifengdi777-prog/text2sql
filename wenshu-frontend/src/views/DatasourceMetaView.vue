<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getDatasourceMeta, saveDatasourceMeta } from '@/services/datasource'
import type { DatasourceMeta, MetaColumn } from '@/types/datasource'

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
const tab = ref<'tables' | 'metrics'>('tables')
const tableSearch = ref('')
const tablePage = ref(1)
const TABLE_PAGE_SIZE = 5
const metricSearch = ref('')
const metricPage = ref(1)
const METRIC_PAGE_SIZE = 8

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
const tableTotalPages = computed(() =>
  Math.max(1, Math.ceil(filteredTables.value.length / TABLE_PAGE_SIZE)),
)
const pagedTables = computed(() => {
  const start = (tablePage.value - 1) * TABLE_PAGE_SIZE
  return filteredTables.value.slice(start, start + TABLE_PAGE_SIZE)
})

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
const metricTotalPages = computed(() =>
  Math.max(1, Math.ceil(filteredMetrics.value.length / METRIC_PAGE_SIZE)),
)
const pagedMetrics = computed(() => {
  const start = (metricPage.value - 1) * METRIC_PAGE_SIZE
  return filteredMetrics.value.slice(start, start + METRIC_PAGE_SIZE)
})

// 搜索/翻页越界保护
watch(tableSearch, () => (tablePage.value = 1))
watch(metricSearch, () => (metricPage.value = 1))
watch(tableTotalPages, (n) => {
  if (tablePage.value > n) tablePage.value = n
})
watch(metricTotalPages, (n) => {
  if (metricPage.value > n) metricPage.value = n
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    meta.value = await getDatasourceMeta(id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function editableRole(c: MetaColumn): boolean {
  // 主外键是物理事实,不可改;只有维度/度量可在两者间切换
  return c.role !== 'primary_key' && c.role !== 'foreign_key'
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
  // 跳到指标 Tab 最后一页,确保看到刚加的
  tab.value = 'metrics'
  metricSearch.value = ''
  metricPage.value = Math.ceil(meta.value.metrics.length / METRIC_PAGE_SIZE)
}
function removeMetric(idx: number) {
  meta.value?.metrics.splice(idx, 1)
}

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
    await saveDatasourceMeta(
      id,
      { tables: meta.value.tables, metrics: meta.value.metrics },
      userPwd.value,
      dbPwd.value,
    )
    // 后端异步重物化(重嵌 Qdrant/重灌 ES);回列表看状态(building→ready)
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
        保存
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
      </div>

      <!-- ===== 表的元数据 ===== -->
      <div v-if="tab === 'tables'" class="flex flex-1 flex-col overflow-hidden">
        <div class="mb-3 flex items-center justify-between gap-3">
          <div class="relative">
            <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">🔍</span>
            <input
              v-model="tableSearch"
              type="search"
              placeholder="搜索 表名 / 字段名 / 字段别名…"
              class="h-9 w-80 rounded-xl border border-slate-200 pl-9 pr-3 text-sm outline-none focus:border-sky-300"
            />
          </div>
          <span class="shrink-0 text-xs text-slate-400">共 {{ filteredTables.length }} 张表</span>
        </div>

        <div class="flex-1 space-y-4 overflow-y-auto pr-1 pb-4">
          <p class="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-700">
            灰色字段（列名/类型/主外键/示例）为物理事实，不可改；其余可编辑。保存后会自动重建向量与值索引。
          </p>
          <p v-if="filteredTables.length === 0" class="py-10 text-center text-sm text-slate-400">
            没有匹配的表或字段
          </p>

          <!-- 表 + 列 -->
          <section
            v-for="t in pagedTables"
            :key="t.name"
            class="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
        <div class="flex flex-wrap items-center gap-3">
          <span class="font-mono text-sm font-semibold text-slate-800">{{ t.name }}</span>
          <select
            v-model="t.role"
            class="rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 outline-none focus:border-sky-300"
          >
            <option value="dim">维度表 dim</option>
            <option value="fact">事实表 fact</option>
            <option value="bridge">桥接表 bridge</option>
          </select>
        </div>
        <label class="mt-3 block text-[11px] font-medium text-slate-400">表的描述</label>
        <textarea
          v-model="t.description"
          rows="2"
          placeholder="这张表在业务中代表什么、典型用于哪些分析"
          class="mt-0.5 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-sky-300"
        />

        <div class="mt-3 overflow-x-auto">
          <table class="w-full text-left text-xs">
            <thead class="text-slate-400">
              <tr class="border-b border-slate-100">
                <th class="py-1.5 pr-3 font-medium">列名</th>
                <th class="py-1.5 pr-3 font-medium">类型</th>
                <th class="py-1.5 pr-3 font-medium">角色</th>
                <th class="py-1.5 pr-3 font-medium">描述</th>
                <th class="py-1.5 pr-3 font-medium">别名（顿号/逗号分隔）</th>
                <th class="py-1.5 pr-3 font-medium">入ES</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in t.columns" :key="c.name" class="border-b border-slate-50 align-top">
                <td class="py-1.5 pr-3 font-mono text-slate-700">{{ c.name }}</td>
                <td class="py-1.5 pr-3 text-slate-400">{{ c.type }}</td>
                <td class="py-1.5 pr-3">
                  <select
                    v-if="editableRole(c)"
                    v-model="c.role"
                    class="rounded border border-slate-200 px-1.5 py-1 text-xs outline-none focus:border-sky-300"
                  >
                    <option value="dimension">维度</option>
                    <option value="measure">度量</option>
                  </select>
                  <span v-else class="text-slate-400">{{ c.role === 'primary_key' ? '主键' : '外键' }}</span>
                </td>
                <td class="py-1.5 pr-3">
                  <input
                    v-model="c.description"
                    type="text"
                    class="w-full min-w-[12rem] rounded border border-slate-200 px-2 py-1 text-xs outline-none focus:border-sky-300"
                  />
                </td>
                <td class="py-1.5 pr-3">
                  <input
                    :value="joinList(c.alias)"
                    type="text"
                    :disabled="!editableRole(c)"
                    placeholder="主外键不需别名"
                    class="w-full min-w-[12rem] rounded border border-slate-200 px-2 py-1 text-xs outline-none focus:border-sky-300 disabled:bg-slate-50 disabled:text-slate-300"
                    @input="c.alias = parseList(($event.target as HTMLInputElement).value)"
                  />
                </td>
                <td class="py-1.5 pr-3">
                  <input type="checkbox" v-model="c.sync" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
          </section>

          <!-- 关系(只读) -->
          <section
            v-if="meta.relationships.length"
            class="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <h2 class="mb-2 text-sm font-semibold text-slate-800">外键关系（只读）</h2>
            <ul class="space-y-1 text-xs text-slate-500">
              <li v-for="(r, ri) in meta.relationships" :key="ri" class="font-mono">
                {{ r.from_table }}.{{ r.from_column }} → {{ r.to_table }}.{{ r.to_column }}
              </li>
            </ul>
          </section>
        </div>

        <!-- 表分页 -->
        <div
          v-if="tableTotalPages > 1"
          class="mt-3 flex items-center justify-center gap-3 text-sm text-slate-500"
        >
          <button
            type="button"
            class="rounded-lg border border-slate-200 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="tablePage <= 1"
            @click="tablePage--"
          >
            上一页
          </button>
          <span>第 {{ tablePage }} / {{ tableTotalPages }} 页</span>
          <button
            type="button"
            class="rounded-lg border border-slate-200 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="tablePage >= tableTotalPages"
            @click="tablePage++"
          >
            下一页
          </button>
        </div>
      </div>

      <!-- ===== 指标信息 ===== -->
      <div v-else class="flex flex-1 flex-col overflow-hidden">
        <div class="mb-3 flex items-center justify-between gap-3">
          <div class="relative">
            <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">🔍</span>
            <input
              v-model="metricSearch"
              type="search"
              placeholder="搜索 指标名 / 别名 / 关联列…"
              class="h-9 w-80 rounded-xl border border-slate-200 pl-9 pr-3 text-sm outline-none focus:border-sky-300"
            />
          </div>
          <button
            type="button"
            class="shrink-0 rounded-xl border border-emerald-200 px-4 py-2 text-sm font-medium text-emerald-600 transition hover:bg-emerald-50"
            @click="addMetric"
          >
            ＋ 新增指标
          </button>
        </div>

        <div class="flex-1 space-y-2 overflow-y-auto pr-1 pb-4">
          <p v-if="filteredMetrics.length === 0" class="py-10 text-center text-sm text-slate-400">
            {{ metricSearch ? '没有匹配的指标' : '暂无指标，点右上角「新增指标」' }}
          </p>
          <div
            v-for="{ m, idx } in pagedMetrics"
            :key="idx"
            class="mb-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <label class="block text-[11px] font-medium text-slate-400">指标名称</label>
            <div class="mt-0.5 flex items-center gap-2">
              <input
                v-model="m.name"
                type="text"
                placeholder="如 良品率"
                class="h-8 flex-1 rounded border border-slate-200 px-2 text-sm font-medium outline-none focus:border-sky-300"
              />
              <button
                type="button"
                class="rounded px-2 py-1 text-xs text-slate-400 transition hover:bg-slate-100 hover:text-rose-500"
                @click="removeMetric(idx)"
              >
                删除
              </button>
            </div>
            <label class="mt-2 block text-[11px] font-medium text-slate-400">口径说明（算法）</label>
            <textarea
              v-model="m.description"
              rows="2"
              placeholder="如：良品率 = 合格数量 / 实际产量"
              class="mt-0.5 w-full rounded border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-sky-300"
            />
            <div class="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label class="block text-[11px] font-medium text-slate-400">关联列（表名.列名，顿号/逗号分隔）</label>
                <input
                  :value="joinList(m.relevant_columns)"
                  type="text"
                  placeholder="如 table_order.order_amount、table_order.order_id"
                  class="mt-0.5 w-full rounded border border-slate-200 px-2 py-1 text-xs outline-none focus:border-sky-300"
                  @input="m.relevant_columns = parseList(($event.target as HTMLInputElement).value)"
                />
              </div>
              <div>
                <label class="block text-[11px] font-medium text-slate-400">别名（同义词，顿号/逗号分隔）</label>
                <input
                  :value="joinList(m.alias)"
                  type="text"
                  placeholder="如 销售额、营收、GMV"
                  class="mt-0.5 w-full rounded border border-slate-200 px-2 py-1 text-xs outline-none focus:border-sky-300"
                  @input="m.alias = parseList(($event.target as HTMLInputElement).value)"
                />
              </div>
            </div>
          </div>
        </div>

        <!-- 指标分页 -->
        <div
          v-if="metricTotalPages > 1"
          class="mt-3 flex items-center justify-center gap-3 text-sm text-slate-500"
        >
          <button
            type="button"
            class="rounded-lg border border-slate-200 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="metricPage <= 1"
            @click="metricPage--"
          >
            上一页
          </button>
          <span>第 {{ metricPage }} / {{ metricTotalPages }} 页</span>
          <button
            type="button"
            class="rounded-lg border border-slate-200 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="metricPage >= metricTotalPages"
            @click="metricPage++"
          >
            下一页
          </button>
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
