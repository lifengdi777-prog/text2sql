<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
  meta.value?.metrics.push({ name: '新指标', description: '', relevant_columns: [], alias: [] })
}
function removeMetric(i: number) {
  meta.value?.metrics.splice(i, 1)
}

async function save() {
  if (!meta.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    await saveDatasourceMeta(id, { tables: meta.value.tables, metrics: meta.value.metrics })
    // 后端异步重物化(重嵌 Qdrant/重灌 ES);回列表看状态(building→ready)
    router.push('/sources')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存失败'
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
        @click="save"
      >
        {{ saving ? '保存中（重建索引）…' : '保存' }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600">{{ error }}</p>
    <p v-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</p>

    <div v-else-if="meta" class="flex-1 space-y-6 overflow-y-auto pr-1 pb-8">
      <p class="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-700">
        灰色字段（列名/类型/主外键/示例）为物理事实，不可改；其余可编辑。保存后会自动重建向量与值索引。
      </p>

      <!-- 表 + 列 -->
      <section
        v-for="t in meta.tables"
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
        <textarea
          v-model="t.description"
          rows="2"
          placeholder="表的业务描述"
          class="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-sky-300"
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

      <!-- 指标 -->
      <section class="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-slate-800">业务指标</h2>
          <button
            type="button"
            class="rounded-lg border border-emerald-200 px-3 py-1 text-xs font-medium text-emerald-600 transition hover:bg-emerald-50"
            @click="addMetric"
          >
            ＋ 新增指标
          </button>
        </div>
        <p v-if="meta.metrics.length === 0" class="py-3 text-center text-xs text-slate-400">暂无指标</p>
        <div
          v-for="(m, i) in meta.metrics"
          :key="i"
          class="mb-2 rounded-xl border border-slate-100 p-3"
        >
          <div class="flex items-center gap-2">
            <input
              v-model="m.name"
              type="text"
              placeholder="指标名"
              class="h-8 flex-1 rounded border border-slate-200 px-2 text-sm font-medium outline-none focus:border-sky-300"
            />
            <button
              type="button"
              class="rounded px-2 py-1 text-xs text-slate-400 transition hover:bg-slate-100 hover:text-rose-500"
              @click="removeMetric(i)"
            >
              删除
            </button>
          </div>
          <textarea
            v-model="m.description"
            rows="2"
            placeholder="口径说明，如：良品率 = 合格数 / 实际产量"
            class="mt-2 w-full rounded border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-sky-300"
          />
          <div class="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
            <input
              :value="joinList(m.relevant_columns)"
              type="text"
              placeholder="关联列，如 table_order.order_amount"
              class="rounded border border-slate-200 px-2 py-1 text-xs outline-none focus:border-sky-300"
              @input="m.relevant_columns = parseList(($event.target as HTMLInputElement).value)"
            />
            <input
              :value="joinList(m.alias)"
              type="text"
              placeholder="别名，如 销售额、营收"
              class="rounded border border-slate-200 px-2 py-1 text-xs outline-none focus:border-sky-300"
              @input="m.alias = parseList(($event.target as HTMLInputElement).value)"
            />
          </div>
        </div>
      </section>

      <!-- 关系(只读) -->
      <section v-if="meta.relationships.length" class="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 class="mb-2 text-sm font-semibold text-slate-800">外键关系（只读）</h2>
        <ul class="space-y-1 text-xs text-slate-500">
          <li v-for="(r, i) in meta.relationships" :key="i" class="font-mono">
            {{ r.from_table }}.{{ r.from_column }} → {{ r.to_table }}.{{ r.to_column }}
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>
