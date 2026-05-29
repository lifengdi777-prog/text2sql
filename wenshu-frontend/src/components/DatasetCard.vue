<script setup lang="ts">
import { ref } from 'vue'

import type { DatasetSummary } from '@/types/dataset'

const props = defineProps<{ dataset: DatasetSummary }>()

const emit = defineEmits<{
  open: [dataset: DatasetSummary]
  preview: [dataset: DatasetSummary]
  remove: [dataset: DatasetSummary]
}>()

const menuOpen = ref(false)

const isReady = () => props.dataset.status === 'ready'

function onOpen() {
  if (isReady()) emit('open', props.dataset)
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
  <div
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

      <!-- 非 ready 状态角标 -->
      <span
        v-if="dataset.status === 'cleaning'"
        class="ml-auto rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-600"
      >
        处理中
      </span>
      <span
        v-else-if="dataset.status === 'failed'"
        class="ml-auto rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600"
      >
        失败
      </span>
    </div>

    <!-- 底部:表数量 + hover 操作 -->
    <div class="mt-auto flex items-center justify-between">
      <span class="inline-flex items-center gap-1.5 text-xs text-slate-500">
        <span aria-hidden="true">▦</span>
        {{ dataset.sheet_count }} 个表 · {{ dataset.total_rows }} 行
      </span>

      <div
        class="flex items-center gap-2 opacity-0 transition group-hover:opacity-100"
        @click.stop
      >
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
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
