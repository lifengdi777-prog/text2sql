<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import DatasetCard from '@/components/DatasetCard.vue'
import UploadDatasetModal from '@/components/UploadDatasetModal.vue'
import DatasetPreviewModal from '@/components/DatasetPreviewModal.vue'
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

function openPreview(ds: DatasetSummary) {
  previewId.value = ds.dataset_id
  previewOpen.value = true
}

async function onRemove(ds: DatasetSummary) {
  if (!window.confirm(`确定删除数据集「${ds.name}」吗？此操作不可恢复。`)) return
  try {
    await deleteDataset(ds.dataset_id)
    await reload()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '删除失败'
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
          新建数据源
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
          {{ search ? '没有匹配的数据集' : '还没有数据集，点击右上角「新建数据源」上传 Excel' }}
        </p>
      </div>

      <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <DatasetCard
          v-for="ds in paged"
          :key="ds.dataset_id"
          :dataset="ds"
          @open="openChat"
          @preview="openPreview"
          @remove="onRemove"
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
  </div>
</template>
