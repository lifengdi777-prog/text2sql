<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import ChatConsole from '@/components/ChatConsole.vue'
import { streamDatasetQuery } from '@/services/agent'
import { getDataset } from '@/services/dataset'
import type { StreamFn } from '@/types/agent'

const route = useRoute()
const datasetId = computed(() => Number(route.params.id))
const datasetName = ref('')

const streamFn: StreamFn = (query, options) => streamDatasetQuery(datasetId.value, query, options)

onMounted(async () => {
  try {
    const detail = await getDataset(datasetId.value)
    datasetName.value = detail.name
  } catch {
    datasetName.value = `数据集 #${datasetId.value}`
  }
})
</script>

<template>
  <ChatConsole
    :stream-fn="streamFn"
    :title="datasetName || `数据集 #${datasetId}`"
    subtitle="Text to SQL · 数据集"
    placeholder="针对该数据集提问，例如：各工厂的产量合计是多少"
    guide-text="基于这个数据集提问吧，下面是一些示例"
    back-to="/datasets"
  />
</template>
