<script setup lang="ts">
import { ref, watch } from 'vue'

import { getDataset } from '@/services/dataset'
import type { ColumnProfile, DatasetDetail } from '@/types/dataset'

const props = defineProps<{ open: boolean; datasetId: number | null }>()

const emit = defineEmits<{
  close: []
  openChat: [datasetId: number]
}>()

const detail = ref<DatasetDetail | null>(null)
const loading = ref(false)
const error = ref('')

watch(
  () => [props.open, props.datasetId] as const,
  async ([open, id]) => {
    if (!open || id == null) return
    loading.value = true
    error.value = ''
    detail.value = null
    try {
      detail.value = await getDataset(id)
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载失败'
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

// 列详情:示例值(枚举/top_k)或数值/时间范围,给用户一眼看清列长什么样
function columnDetail(col: ColumnProfile): string {
  if (col.semantic_type === 'numeric' || col.semantic_type === 'temporal') {
    if (col.min !== undefined && col.max !== undefined) return `${col.min} ~ ${col.max}`
    return '-'
  }
  const sample = col.values ?? col.top_k
  if (sample && sample.length > 0) {
    const shown = sample.slice(0, 8).map((v) => String(v)).join(', ')
    return sample.length > 8 ? `${shown} …` : shown
  }
  return '-'
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
          <p class="text-xs font-semibold uppercase tracking-[0.3em] text-sky-600">数据预览</p>
          <h2 class="mt-0.5 truncate text-lg font-semibold text-slate-900">
            {{ detail?.name ?? '加载中…' }}
          </h2>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="detail && detail.status === 'ready'"
            type="button"
            class="rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-600"
            @click="emit('openChat', detail.dataset_id)"
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
        <p v-if="loading" class="py-10 text-center text-sm text-slate-400">加载中…</p>
        <p v-else-if="error" class="py-10 text-center text-sm text-rose-500">{{ error }}</p>

        <template v-else-if="detail?.schema">
          <div v-for="(sheet, name) in detail.schema.sheets" :key="name" class="mb-7 last:mb-0">
            <h3 class="mb-2 text-sm font-semibold text-slate-800">
              {{ name }}
              <span class="ml-1 text-xs font-normal text-slate-400">共 {{ sheet.row_count }} 行</span>
            </h3>
            <div class="overflow-hidden rounded-xl border border-slate-200">
              <table class="w-full text-left text-xs">
                <thead class="bg-slate-50 text-slate-500">
                  <tr>
                    <th class="px-3 py-2 font-medium">列名</th>
                    <th class="px-3 py-2 font-medium">类型</th>
                    <th class="px-3 py-2 font-medium">详情</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-100">
                  <tr v-for="col in sheet.columns" :key="col.name">
                    <td class="px-3 py-2 font-medium text-slate-700">{{ col.name }}</td>
                    <td class="px-3 py-2 text-slate-500">{{ col.semantic_type }}</td>
                    <td class="px-3 py-2 text-slate-500">{{ columnDetail(col) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </template>

        <p v-else class="py-10 text-center text-sm text-slate-400">该数据集暂无 schema 信息</p>
      </div>
    </div>
  </div>
</template>
