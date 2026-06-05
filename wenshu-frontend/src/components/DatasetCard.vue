<script setup lang="ts">
import { computed, ref } from 'vue'

import type { DatasetSummary } from '@/types/dataset'

const props = defineProps<{ dataset: DatasetSummary }>()

const emit = defineEmits<{
  open: [dataset: DatasetSummary]
  assist: [dataset: DatasetSummary]
  preview: [dataset: DatasetSummary]
  remove: [dataset: DatasetSummary]
}>()

const menuOpen = ref(false)

const isReady = () => props.dataset.status === 'ready'

// 清洗入库 / ES 值索引未完成 → 卡片只显示「索引创建中 + 进度条」,不暴露文件名与任何操作
const isBuilding = computed(
  () => props.dataset.status === 'cleaning' || props.dataset.status === 'indexing',
)

function onOpen() {
  if (isReady()) emit('open', props.dataset)
}

function onAssist() {
  if (isReady()) emit('assist', props.dataset)
}

function choosePreview() {
  menuOpen.value = false
  emit('preview', props.dataset)
}

function chooseRemove() {
  menuOpen.value = false
  emit('remove', props.dataset)
}
</script>

<template>
  <!-- 建设中:仅展示「索引创建中」+ 不确定进度条,完成前不暴露文件名/操作 -->
  <div
    v-if="isBuilding"
    class="relative flex h-44 flex-col justify-center gap-4 rounded-2xl border border-slate-200 bg-white p-5"
  >
    <div class="flex items-center gap-2 text-sm font-medium text-slate-600">
      <span class="h-2 w-2 animate-pulse rounded-full bg-sky-400" aria-hidden="true" />
      解析处理中
    </div>
    <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <div class="dataset-progress h-full w-1/3 rounded-full bg-sky-400" />
    </div>
    <p class="text-xs text-slate-400">AI 解析中,完成后即可开始问数</p>
  </div>

  <!-- 已就绪 / 失败:正常卡片 -->
  <div
    v-else
    class="group relative flex h-44 cursor-pointer flex-col rounded-2xl border border-slate-200 bg-white p-5 transition hover:border-sky-300 hover:shadow-[0_12px_30px_rgba(148,163,184,0.18)]"
    @click="emit('preview', dataset)"
  >
    <!-- 头部:图标 + 名称 + 类型 -->
    <div class="flex items-start gap-3">
      <span
        class="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-emerald-500 text-xs font-bold text-white"
        aria-hidden="true"
      >
        XLS
      </span>
      <div class="min-w-0">
        <h3 class="truncate text-base font-semibold text-slate-900" :title="dataset.name">
          {{ dataset.name }}
        </h3>
        <p class="text-xs text-slate-400">Excel/CSV</p>
      </div>

      <!-- 失败角标 -->
      <span
        v-if="dataset.status === 'failed'"
        class="ml-auto rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600"
      >
        失败
      </span>
    </div>

    <!-- 底部:表数量 / 失败原因 + hover 操作 -->
    <div class="mt-auto flex items-center justify-between gap-2">
      <span
        v-if="dataset.status === 'failed'"
        class="truncate text-xs text-rose-500"
        :title="dataset.error_message || '处理失败'"
      >
        {{ dataset.error_message || '处理失败,请重试' }}
      </span>
      <span v-else class="inline-flex items-center gap-1.5 text-xs text-slate-500">
        <span aria-hidden="true">▦</span>
        {{ dataset.sheet_count }} 个表 · {{ dataset.total_rows }} 行
      </span>

      <div
        class="flex items-center gap-1.5"
        @click.stop
      >
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-lg bg-indigo-500 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-indigo-600 disabled:cursor-not-allowed disabled:bg-slate-300"
          :disabled="!isReady()"
          :title="isReady() ? '用自然语言增删改并下载' : '数据集尚未就绪'"
          @click="onAssist"
        >
          <span aria-hidden="true">✦</span>
          智能助手
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-lg bg-emerald-500 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
          :disabled="!isReady()"
          :title="isReady() ? '' : '数据集尚未就绪'"
          @click="onOpen"
        >
          <span aria-hidden="true">⊕</span>
          开启问数
        </button>

        <div class="relative">
          <button
            type="button"
            class="inline-flex h-7 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-700"
            aria-label="更多操作"
            @click="menuOpen = !menuOpen"
          >
            ⋯
          </button>

          <!-- 关闭菜单的透明遮罩 -->
          <div v-if="menuOpen" class="fixed inset-0 z-10" @click="menuOpen = false" />

          <div
            v-if="menuOpen"
            class="absolute right-0 z-20 mt-1 w-28 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg"
          >
            <button
              type="button"
              class="block w-full px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-50"
              @click="choosePreview"
            >
              预览数据
            </button>
            <button
              type="button"
              class="block w-full px-3 py-2 text-left text-xs text-rose-600 hover:bg-rose-50"
              @click="chooseRemove"
            >
              删除
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 不确定进度条:一小段高亮在轨道里来回滑动,表示「正在处理、无具体百分比」 */
@keyframes dataset-progress-slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(300%);
  }
}

.dataset-progress {
  animation: dataset-progress-slide 1.4s ease-in-out infinite;
}
</style>
