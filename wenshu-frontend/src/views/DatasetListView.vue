<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import DatasetCard from '@/components/DatasetCard.vue'
import UploadDatasetModal from '@/components/UploadDatasetModal.vue'
import DatasetPreviewModal from '@/components/DatasetPreviewModal.vue'
import HeaderConfirmModal from '@/components/HeaderConfirmModal.vue'
import { deleteDataset, listDatasets } from '@/services/dataset'
import type { DatasetSummary } from '@/types/dataset'

const router = useRouter()

const datasets = ref<DatasetSummary[]>([])
const loading = ref(false)
const loadError = ref('')

const search = ref('')
const page = ref(1)
const PAGE_SIZE = 9

const uploadOpen = ref(false)
const previewId = ref<number | null>(null)
const previewOpen = ref(false)

// 表头确认弹窗(status=needs_header 的数据集)
const headerReviewId = ref<number | null>(null)
const headerReviewOpen = ref(false)

// 删除确认弹框(自定义,替代浏览器原生 confirm,与会话删除弹框风格统一)
const deleteTarget = ref<DatasetSummary | null>(null)
const deleting = ref(false)

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return datasets.value
  return datasets.value.filter((d) => d.name.toLowerCase().includes(kw))
})

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))

const paged = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return filtered.value.slice(start, start + PAGE_SIZE)
})

function clampPage() {
  if (page.value > totalPages.value) page.value = totalPages.value
  if (page.value < 1) page.value = 1
}

// 有卡片还在「清洗 / 索引」中 → 需要轮询直到全部就绪
const POLL_MS = 3000
let pollTimer: ReturnType<typeof setTimeout> | null = null

function hasPending() {
  return datasets.value.some((d) => d.status === 'cleaning' || d.status === 'indexing')
}

function stopPolling() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

// 静默刷新(不显示「加载中」,避免卡片闪烁);仍有未就绪卡片才继续排下一次
function schedulePoll() {
  stopPolling()
  if (!hasPending()) return
  pollTimer = setTimeout(async () => {
    try {
      datasets.value = await listDatasets()
      clampPage()
    } catch {
      // 轮询失败静默处理,下次 reload 再纠正
    }
    schedulePoll()
  }, POLL_MS)
}

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    datasets.value = await listDatasets()
    clampPage()
    schedulePoll()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载数据集失败'
  } finally {
    loading.value = false
  }
}

function openChat(ds: DatasetSummary) {
  router.push(`/datasets/${ds.dataset_id}/chat`)
}

function openAssist(ds: DatasetSummary) {
  router.push(`/datasets/${ds.dataset_id}/edit`)
}

function openPreview(ds: DatasetSummary) {
  previewId.value = ds.dataset_id
  previewOpen.value = true
}

function openHeaderReview(ds: DatasetSummary) {
  headerReviewId.value = ds.dataset_id
  headerReviewOpen.value = true
}

function onHeaderConfirmed() {
  // 确认后后端转 cleaning 重跑 → 关弹窗并刷新,轮询会接管直到 ready
  headerReviewOpen.value = false
  void reload()
}

function onRemove(ds: DatasetSummary) {
  deleteTarget.value = ds
}

function closeDelete() {
  if (deleting.value) return
  deleteTarget.value = null
}

async function confirmDelete() {
  const ds = deleteTarget.value
  if (!ds || deleting.value) return
  deleting.value = true
  try {
    await deleteDataset(ds.dataset_id)
    deleteTarget.value = null
    await reload()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    deleting.value = false
  }
}

function onUploaded() {
  // 上传成功后留在列表页(不再自动跳转到问数页),刷新出新卡片
  uploadOpen.value = false
  void reload()
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
          <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
            🔍
          </span>
          <input
            v-model="search"
            type="search"
            placeholder="搜索"
            class="h-10 w-64 rounded-xl border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-700 outline-none transition focus:border-sky-300"
            @input="page = 1"
          />
        </div>

        <button
          type="button"
          class="inline-flex h-10 items-center gap-1.5 rounded-xl bg-emerald-500 px-4 text-sm font-semibold text-white transition hover:bg-emerald-600"
          @click="uploadOpen = true"
        >
          <span aria-hidden="true">＋</span>
          新建数据集
        </button>
      </div>
    </div>

    <!-- 网格 -->
    <div class="flex-1 overflow-y-auto pr-1">
      <p v-if="loading" class="py-16 text-center text-sm text-slate-400">加载中…</p>
      <p v-else-if="loadError" class="py-16 text-center text-sm text-rose-500">{{ loadError }}</p>
      <div
        v-else-if="filtered.length === 0"
        class="flex flex-col items-center gap-3 py-20 text-center"
      >
        <span class="text-4xl" aria-hidden="true">📂</span>
        <p class="text-sm text-slate-500">
          {{ search ? '没有匹配的数据集' : '还没有数据集，点击右上角「新建数据集」上传 Excel' }}
        </p>
      </div>

      <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <DatasetCard
          v-for="ds in paged"
          :key="ds.dataset_id"
          :dataset="ds"
          @open="openChat"
          @assist="openAssist"
          @preview="openPreview"
          @remove="onRemove"
          @confirm-header="openHeaderReview"
        />
      </div>
    </div>

    <!-- 分页 -->
    <div
      v-if="!loading && totalPages > 1"
      class="mt-4 flex items-center justify-center gap-3 text-sm text-slate-500"
    >
      <button
        type="button"
        class="rounded-lg border border-slate-200 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
        :disabled="page <= 1"
        @click="page--"
      >
        上一页
      </button>
      <span>第 {{ page }} / {{ totalPages }} 页</span>
      <button
        type="button"
        class="rounded-lg border border-slate-200 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
        :disabled="page >= totalPages"
        @click="page++"
      >
        下一页
      </button>
    </div>

    <UploadDatasetModal :open="uploadOpen" @close="uploadOpen = false" @uploaded="onUploaded" />
    <DatasetPreviewModal
      :open="previewOpen"
      :dataset-id="previewId"
      @close="previewOpen = false"
      @open-chat="(id) => router.push(`/datasets/${id}/chat`)"
    />
    <HeaderConfirmModal
      :open="headerReviewOpen"
      :dataset-id="headerReviewId"
      @close="headerReviewOpen = false"
      @confirmed="onHeaderConfirmed"
    />

    <!-- 删除确认弹框(与会话删除弹框风格统一) -->
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
            <h3 class="text-base font-semibold text-slate-800">删除数据集</h3>
            <p class="mt-1 break-words text-sm text-slate-500">
              确定删除「<span class="font-medium text-slate-700">{{ deleteTarget.name }}</span
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
