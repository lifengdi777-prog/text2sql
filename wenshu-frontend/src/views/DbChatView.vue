<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ChatConsole from '@/components/ChatConsole.vue'
import { streamAgentQuery } from '@/services/agent'
import type { StreamFn } from '@/types/agent'

const route = useRoute()
const router = useRouter()

// 必须从「数据源」页「开启问数」带 ?datasource=<id>&name=<名> 进来;没有就回数据源页选一个。
const datasourceId = computed(() => (route.query.datasource as string) || '')
const datasourceName = computed(() => (route.query.name as string) || '')
const subtitle = computed(() => `Text to SQL · ${datasourceName.value || '请选择数据源'}`)

onMounted(() => {
  if (!datasourceId.value) router.replace('/sources')
})

// 闭包注入当前数据源(同 DatasetChatView 注入 datasetId 的写法)
const streamFn: StreamFn = (query, options) =>
  streamAgentQuery(query, { ...options, datasourceId: datasourceId.value })
</script>

<template>
  <ChatConsole
    :stream-fn="streamFn"
    source="db"
    :datasource-id="datasourceId"
    title="智能数据分析工作台"
    :subtitle="subtitle"
    placeholder="请输入想查询的问题，例如：统计各区域的总销售额"
    guide-text="你好，我是 Text2SQL，能将您的需求转换为 SQL 语句进行查询，您可以像下面一样提问"
  />
</template>
