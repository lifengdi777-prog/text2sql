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
const notice = ref('')

// 服务端处理阶段(由后端 SSE 实时推送,真实进度)
const processing = ref(false) // 已进入服务端处理(收到首个阶段事件 / 字节已传完)
const currentStep = ref('')   // 当前阶段展示文案

// 后端阶段名 → 友好展示文案
const STEP_LABEL: Record<string, string> = {
  'AI 识别表头': '🤖 AI 正在解析文件…',
  清洗字段: '清洗字段、推断类型…',
  写入存储: '写入存储…',
}

const ALLOWED = ['.xlsx', '.xls']

function pickFile(f: File | null | undefined) {
  error.value = ''
  notice.value = ''
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
  currentStep.value = ''
  processing.value = false
  error.value = ''
  notice.value = ''
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
  notice.value = ''
  progress.value = 0
  currentStep.value = ''
  processing.value = false
  try {
    const result = await uploadDataset(file.value, {
      onProgress: (p) => {
        progress.value = p
        if (p >= 100 && !processing.value) {
          processing.value = true
          currentStep.value = '处理中…'
        }
      },
      onStep: (step, status) => {
        processing.value = true
        if (status === 'running') currentStep.value = STEP_LABEL[step] ?? `${step}…`
      },
    })
    if (result.duplicated) {
      // 后端按 文件名 + 内容 SHA-256 去重命中,没有新建数据集
      notice.value = `「${result.name}」之前已上传过,已复用现有数据集,未重复创建。`
    } else {
      emit('uploaded', result)
      reset()
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '上传失败，请重试'
  } finally {
    processing.value = false
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
          <!-- 字节上传:真实进度;服务端处理:不确定流动条 -->
          <div
            v-if="!processing"
            class="h-full rounded-full bg-sky-500 transition-all"
            :style="{ width: `${progress}%` }"
          />
          <div v-else class="indeterminate-bar h-full rounded-full bg-sky-500" />
        </div>
        <p class="mt-1.5 text-xs font-medium text-slate-600">
          {{ processing ? currentStep : `上传中 ${progress}%` }}
        </p>
        <p v-if="processing" class="mt-0.5 text-[11px] text-slate-400">
          正在解析(含 AI 表头识别),请稍候…
        </p>
      </div>

      <p v-if="error" class="mt-3 text-xs text-rose-500">{{ error }}</p>
      <p
        v-if="notice"
        class="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-700"
      >
        {{ notice }}
      </p>

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

<style scoped>
/* 服务端处理阶段的不确定进度条:一段在轨道里来回滑动,表示"还在干活" */
.indeterminate-bar {
  width: 40%;
  animation: indeterminate-slide 1.1s ease-in-out infinite;
}
@keyframes indeterminate-slide {
  0% {
    margin-left: -40%;
  }
  100% {
    margin-left: 100%;
  }
}
</style>
