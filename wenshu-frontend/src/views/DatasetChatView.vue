<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'

import ChatConsole from '@/components/ChatConsole.vue'
import { streamDatasetQuery } from '@/services/agent'
import { getDataset } from '@/services/dataset'
import type { StreamFn } from '@/types/agent'

const route = useRoute()
const datasetId = computed(() => Number(route.params.id))
const datasetName = ref('')

const loading = ref(true)
const notFound = ref(false)

const streamFn: StreamFn = (query, options) => streamDatasetQuery(datasetId.value, query, options)

onMounted(async () => {
  try {
    const detail = await getDataset(datasetId.value)
    datasetName.value = detail.name
  } catch (e) {
    // 401 已由 dataset.ts 拦截器跳登录,这里不处理(页面会被整页跳走)
    if (axios.isAxiosError(e) && e.response?.status === 401) return
    // 404 = 数据集不存在 / 不属于当前用户;其他错误也按"无法访问"处理,
    // 一律不放进聊天页(避免对不存在的数据集发起提问)。
    notFound.value = true
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <!-- 加载中:先校验数据集是否存在且归属当前用户,再决定是否进入 -->
  <div v-if="loading" class="flex h-full w-full items-center justify-center text-sm text-slate-400">
    加载中…
  </div>

  <!-- 不存在 / 无权访问:不进入聊天,给返回入口 -->
  <div
    v-else-if="notFound"
    class="flex h-full w-full flex-col items-center justify-center gap-4 px-6 text-center"
  >
    <span class="text-5xl" aria-hidden="true">🔍</span>
    <div>
      <p class="text-lg font-semibold text-slate-700">数据集不存在或你无权访问</p>
      <p class="mt-1 text-sm text-slate-400">它可能已被删除，或不属于当前账号。</p>
    </div>
    <router-link
      to="/datasets"
      class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600"
    >
      返回数据集列表
    </router-link>
  </div>

  <!-- 校验通过,进入聊天 -->
  <ChatConsole
    v-else
    :stream-fn="streamFn"
    source="dataset"
    :dataset-id="datasetId"
    :title="datasetName || `数据集 #${datasetId}`"
    subtitle="Text to SQL · 数据集"
    placeholder="针对该数据集提问，例如：各工厂的产量合计是多少"
    guide-text="基于这个数据集提问吧，下面是一些示例"
    back-to="/datasets"
  />
</template>
