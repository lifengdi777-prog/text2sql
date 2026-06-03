<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import DatasourceWizard from '@/components/DatasourceWizard.vue'
import { deleteDatasource, listDatasources } from '@/services/datasource'
import type { DatasourceSummary } from '@/types/datasource'

const router = useRouter()
const wizardOpen = ref(false)

const sources = ref<DatasourceSummary[]>([])
const loading = ref(false)
const loadError = ref('')
const search = ref('')

const deleteTarget = ref<DatasourceSummary | null>(null)
const deleting = ref(false)

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return sources.value
  return sources.value.filter((d) => d.name.toLowerCase().includes(kw))
})

// 接入状态徽章
function badge(s: DatasourceSummary): { text: string; cls: string } {
  switch (s.build_status) {
    case 'ready':
      return { text: '可问数', cls: 'bg-emerald-50 text-emerald-600' }
    case 'building':
      return { text: '接入中…', cls: 'bg-sky-50 text-sky-600' }
    case 'failed':
      return { text: '接入失败', cls: 'bg-rose-50 text-rose-600' }
    default:
      return { text: '待接入', cls: 'bg-slate-100 text-slate-500' }
  }
}

// 有源还在 building → 轮询直到全部就绪
const POLL_MS = 3000
let pollTimer: ReturnType<typeof setTimeout> | null = null
function hasPending() {
  return sources.value.some((d) => d.build_status === 'building')
}
function stopPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}
function schedulePoll() {
  stopPolling()
  if (!hasPending()) return
  pollTimer = setTimeout(async () => {
    try {
      sources.value = await listDatasources()
    } catch {
      /* 轮询失败静默,下次 reload 纠正 */
    }
    schedulePoll()
  }, POLL_MS)
}

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    sources.value = await listDatasources()
    schedulePoll()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载数据源失败'
  } finally {
    loading.value = false
  }
}

function openChat(ds: DatasourceSummary) {
  if (ds.build_status !== 'ready') return
  // 带上 datasource 走问数页;DbChatView 据此把 datasource_id 传给后端(第 5 步打通)
  router.push({ path: '/db', query: { datasource: ds.id, name: ds.name } })
}

function onCreate() {
  wizardOpen.value = true
}

// 向导触发接入后:关弹窗 + 刷新列表(新卡片以 building 出现,轮询转 ready)
function onCreated() {
  wizardOpen.value = false
  void reload()
}

function onRemove(ds: DatasourceSummary) {
  deleteTarget.value = ds
}
function closeDelete() {
  if (!deleting.value) deleteTarget.value = null
}
async function confirmDelete() {
  const ds = deleteTarget.value
  if (!ds || deleting.value) return
  deleting.value = true
  try {
    await deleteDatasource(ds.id)
    deleteTarget.value = null
    await reload()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    deleting.value = false
  }
}

onMounted(reload)
onUnmounted(stopPolling)
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden">
    <!-- 头部 -->
    <div class="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <h1 class="text-2xl font-semibold tracking-tight text-slate-900">数据源</h1>
      <div class="flex items-center gap-3">
        <div class="relative">
          <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">🔍</span>
          <input
            v-model="search"
            type="search"
            placeholder="搜索"
            class="h-10 w-64 rounded-xl border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-700 outline-none transition focus:border-sky-300"
          />
        </div>
        <button
          type="button"
          class="inline-flex h-10 items-center gap-1.5 rounded-xl bg-emerald-500 px-4 text-sm font-semibold text-white transition hover:bg-emerald-600"
          @click="onCreate"
        >
          <span aria-hidden="true">＋</span>
          新建数据源
        </button>
      </div>
    </div>

    <!-- 网格 -->
    <div class="flex-1 overflow-y-auto pr-1">
      <p v-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</p>
      <p v-else-if="loadError" class="py-16 text-center text-sm text-rose-500">{{ loadError }}</p>
      <div v-else-if="filtered.length === 0" class="flex flex-col items-center gap-3 py-20 text-center">
        <span class="text-4xl" aria-hidden="true">🗃️</span>
        <p class="text-sm text-slate-500">
          {{ search ? '没有匹配的数据源' : '还没有数据源，点击右上角「新建数据源」连接一个 MySQL 库' }}
        </p>
      </div>

      <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="ds in filtered"
          :key="ds.id"
          class="flex flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:shadow-md"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0">
              <h3 class="truncate text-base font-semibold text-slate-800">{{ ds.name }}</h3>
              <p class="mt-0.5 text-xs text-slate-400">{{ ds.type }} · {{ ds.default_database || '—' }}</p>
            </div>
            <span class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium" :class="badge(ds).cls">
              {{ badge(ds).text }}
            </span>
          </div>

          <p class="mt-3 truncate text-xs text-slate-400">{{ ds.host }}:{{ ds.port }}</p>

          <div class="mt-4 flex items-center justify-between">
            <span class="text-xs text-slate-400">
              {{ ds.table_count != null ? `${ds.table_count} 张表` : '未接入' }}
            </span>
            <div class="flex items-center gap-2">
              <button
                type="button"
                class="rounded-lg px-2 py-1.5 text-xs text-slate-400 transition hover:bg-slate-100 hover:text-rose-500"
                title="删除"
                @click="onRemove(ds)"
              >
                删除
              </button>
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
                :disabled="ds.build_status !== 'ready'"
                :title="ds.build_status === 'ready' ? '' : '接入完成后才能问数'"
                @click="openChat(ds)"
              >
                开启问数
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建数据源向导 -->
    <DatasourceWizard :open="wizardOpen" @close="wizardOpen = false" @created="onCreated" />

    <!-- 删除确认 -->
    <div
      v-if="deleteTarget"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      @click.self="closeDelete"
    >
      <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <h3 class="text-base font-semibold text-slate-800">删除数据源</h3>
        <p class="mt-1 break-words text-sm text-slate-500">
          确定删除「<span class="font-medium text-slate-700">{{ deleteTarget.name }}</span
          >」？将一并清除它的元数据、向量与值索引，不可恢复。
        </p>
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
            class="rounded-xl bg-rose-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-600 disabled:bg-rose-300"
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
