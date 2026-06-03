<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import ChatConsole from '@/components/ChatConsole.vue'
import { streamAgentQuery } from '@/services/agent'
import type { StreamFn } from '@/types/agent'

const route = useRoute()

// 从「数据源」页点「开启问数」会带 ?datasource=<id>&name=<名>;直接进 /db 则默认 ds_default。
const datasourceId = computed(() => (route.query.datasource as string) || 'ds_default')
const datasourceName = computed(() => (route.query.name as string) || '默认数据源')
const subtitle = computed(() => `Text to SQL · ${datasourceName.value}`)

// 闭包注入当前数据源(同 DatasetChatView 注入 datasetId 的写法)
const streamFn: StreamFn = (query, options) =>
  streamAgentQuery(query, { ...options, datasourceId: datasourceId.value })
</script>

<template>
  <ChatConsole
    :stream-fn="streamFn"
    source="db"
    title="智能数据分析工作台"
    :subtitle="subtitle"
    placeholder="请输入想查询的问题，例如：统计各区域的总销售额"
    guide-text="你好，我是 Text2SQL，能将您的需求转换为 SQL 语句进行查询，您可以像下面一样提问"
  />
</template>
