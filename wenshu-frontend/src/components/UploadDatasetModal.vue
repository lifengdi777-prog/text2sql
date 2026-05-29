<script setup lang="ts">
import { ref } from 'vue'

import { uploadDataset } from '@/services/dataset'
import type { UploadResult } from '@/types/dataset'

defineProps<{ open: boolean }>()

const emit = defineEmits<{
  close: []
  uploaded: [result: UploadResult]
}>()

const file = ref<File | null>(null)
const dragging = ref(false)
const uploading = ref(false)
const progress = ref(0)
const error = ref('')

const ALLOWED = ['.xlsx', '.xls']

function pickFile(f: File | null | undefined) {
  error.value = ''
  if (!f) return
  const lower = f.name.toLowerCase()
  if (!ALLOWED.some((ext) => lower.endsWith(ext))) {
    error.value = '仅支持 .xlsx / .xls 文件'
    return
  }
  file.value = f
}

function onInputChange(e: Event) {
  pickFile((e.target as HTMLInputElement).files?.[0])
}

function onDrop(e: DragEvent) {
  dragging.value = false
  pickFile(e.dataTransfer?.files?.[0])
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function reset() {
  file.value = null
  progress.value = 0
  error.value = ''
  uploading.value = false
}

function close() {
  if (uploading.value) return
  reset()
  emit('close')
}

async function submit() {
  if (!file.value || uploading.value) return
  uploading.value = true
  error.value = ''
  progress.value = 0
  try {
    const result = await uploadDataset(file.value, 'anonymous', (p) => (progress.value = p))
    emit('uploaded', result)
    reset()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '上传失败，请重试'
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm"
    @click.self="close"
  >
    <div class="w-full max-w-md rounded-3xl border border-white/70 bg-white p-6 shadow-2xl">
      <div class="mb-4 flex items-center justify-between">
        <h2 class="text-lg font-semibold text-slate-900">新建数据源</h2>
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          @click="close"
        >
          ✕
        </button>
      </div>

      <label
        class="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition"
        :class="dragging ? 'border-sky-400 bg-sky-50' : 'border-slate-200 bg-slate-50/60 hover:border-sky-300'"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <span class="text-3xl" aria-hidden="true">📄</span>
        <span class="text-sm font-medium text-slate-700">
          {{ file ? file.name : '点击选择或拖拽 Excel 文件到此' }}
        </span>
        <span v-if="file" class="text-xs text-slate-400">{{ formatSize(file.size) }}</span>
        <span v-else class="text-xs text-slate-400">支持 .xlsx / .xls，最大 100MB</span>
        <input type="file" accept=".xlsx,.xls" class="hidden" @change="onInputChange" />
      </label>

      <div v-if="uploading" class="mt-4">
        <div class="h-2 w-full overflow-hidden rounded-full bg-slate-100">
          <div class="h-full rounded-full bg-sky-500 transition-all" :style="{ width: `${progress}%` }" />
        </div>
        <p class="mt-1 text-xs text-slate-500">
          {{ progress < 100 ? `上传中 ${progress}%` : '正在解析与清洗…' }}
        </p>
      </div>

      <p v-if="error" class="mt-3 text-xs text-rose-500">{{ error }}</p>

      <div class="mt-6 flex justify-end gap-3">
        <button
          type="button"
          class="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
          :disabled="uploading"
          @click="close"
        >
          取消
        </button>
        <button
          type="button"
          class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
          :disabled="!file || uploading"
          @click="submit"
        >
          {{ uploading ? '处理中…' : '上传' }}
        </button>
      </div>
    </div>
  </div>
</template>
