<script setup lang="ts">
// 数据源卡片点击后的「简单预览」:仿数据集预览弹窗。
// 上半展示连接信息(类型/地址/默认库/表数/接入状态),下半展示已物化的表与字段(列名/类型/主外键)。
// 结构数据复用 GET /datasources/{id}/meta;只有 ready 的源才拉结构,其余给提示。
import { ref, watch } from 'vue'

import { getDatasourceMeta } from '@/services/datasource'
import type { DatasourceMeta, DatasourceSummary, MetaColumn } from '@/types/datasource'

const props = defineProps<{ open: boolean; datasource: DatasourceSummary | null }>()

const emit = defineEmits<{
  close: []
  openChat: [datasourceId: string]
}>()

const meta = ref<DatasourceMeta | null>(null)
const loading = ref(false)
const error = ref('')

// 预览只是「看个大概」,给上限防止表/列多时把弹窗撑爆:最多 8 张表、每张表 12 列,超出给提示
const MAX_TABLES = 8
const MAX_COLS = 12

watch(
  () => [props.open, props.datasource?.id] as const,
  async ([open, id]) => {
    if (!open || !id) return
    meta.value = null
    error.value = ''
    // 未接入完成的源没有结构可看,跳过请求(下面模板给提示)
    if (props.datasource?.build_status !== 'ready') return
    loading.value = true
    try {
      meta.value = await getDatasourceMeta(id)
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载失败'
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

function roleMark(c: MetaColumn): string {
  if (c.role === 'primary_key') return '🔑'
  if (c.role === 'foreign_key') return '🔗'
  return ''
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm"
    @click.self="emit('close')"
  >
    <div
      class="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-white/70 bg-white shadow-2xl"
    >
      <header class="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <div class="min-w-0">
          <p class="text-xs font-semibold uppercase tracking-[0.3em] text-sky-600">数据源预览</p>
          <h2 class="mt-0.5 truncate text-lg font-semibold text-slate-900">
            {{ datasource?.name ?? '加载中…' }}
          </h2>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="datasource && datasource.build_status === 'ready'"
            type="button"
            class="rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-600"
            @click="emit('openChat', datasource.id)"
          >
            开启问数
          </button>
          <button
            type="button"
            class="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>
      </header>

      <div class="flex-1 overflow-y-auto px-6 py-5">
        <!-- 连接信息 -->
        <div v-if="datasource" class="mb-5 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
          <div><span class="text-slate-400">类型</span><p class="mt-0.5 font-medium text-slate-700">{{ datasource.type }}</p></div>
          <div><span class="text-slate-400">地址</span><p class="mt-0.5 truncate font-medium text-slate-700">{{ datasource.host }}:{{ datasource.port }}</p></div>
          <div><span class="text-slate-400">默认库</span><p class="mt-0.5 font-medium text-slate-700">{{ datasource.default_database || '—' }}</p></div>
          <div><span class="text-slate-400">表数</span><p class="mt-0.5 font-medium text-slate-700">{{ datasource.table_count ?? '—' }}</p></div>
        </div>

        <p v-if="loading" class="py-10 text-center text-sm text-slate-400">加载中…</p>
        <p v-else-if="error" class="py-10 text-center text-sm text-rose-500">{{ error }}</p>
        <p
          v-else-if="datasource && datasource.build_status !== 'ready'"
          class="py-10 text-center text-sm text-slate-400"
        >
          该数据源尚未接入完成，暂无结构预览
        </p>

        <template v-else-if="meta && meta.tables.length">
          <div v-for="t in meta.tables.slice(0, MAX_TABLES)" :key="t.name" class="mb-7 last:mb-0">
            <h3 class="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-800">
              <span class="font-mono">{{ t.name }}</span>
              <span class="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-normal text-slate-500">{{ t.role }}</span>
              <span class="text-xs font-normal text-slate-400">{{ t.columns.length }} 列</span>
            </h3>
            <div class="overflow-hidden rounded-xl border border-slate-200">
              <table class="w-full text-left text-xs">
                <thead class="bg-slate-50 text-slate-500">
                  <tr>
                    <th class="px-3 py-2 font-medium">列名</th>
                    <th class="px-3 py-2 font-medium">类型</th>
                    <th class="px-3 py-2 font-medium">描述</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-100">
                  <tr v-for="c in t.columns.slice(0, MAX_COLS)" :key="c.name">
                    <td class="px-3 py-2 font-mono text-slate-700">
                      <span v-if="roleMark(c)" class="mr-1">{{ roleMark(c) }}</span>{{ c.name }}
                    </td>
                    <td class="px-3 py-2 text-slate-500">{{ c.type }}</td>
                    <td class="px-3 py-2 text-slate-500">{{ c.description || '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="t.columns.length > MAX_COLS" class="mt-1 text-[11px] text-slate-400">
              还有 {{ t.columns.length - MAX_COLS }} 列未显示…
            </p>
          </div>

          <!-- 表数超上限的整体提示 + 引导去编辑元数据看全量 -->
          <p v-if="meta.tables.length > MAX_TABLES" class="pt-1 text-center text-xs text-slate-400">
            仅预览前 {{ MAX_TABLES }} 张表（共 {{ meta.tables.length }} 张）。完整结构请到「编辑元数据」查看。
          </p>
        </template>

        <p v-else class="py-10 text-center text-sm text-slate-400">该数据源暂无结构信息</p>
      </div>
    </div>
  </div>
</template>
