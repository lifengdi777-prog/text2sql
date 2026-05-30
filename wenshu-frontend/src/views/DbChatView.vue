<script setup lang="ts">
import ChatConsole from '@/components/ChatConsole.vue'
import { streamAgentQuery } from '@/services/agent'
</script>

<template>
  <ChatConsole
    :stream-fn="streamAgentQuery"
    source="db"
    title="智能数据分析工作台"
    subtitle="Text to SQL · 数据库"
    placeholder="请输入想查询的生产数据问题，例如：统计 2026 年各工厂的实际产量"
    guide-text="你好，我是 Text2SQL，能将您的需求转换为 SQL 语句进行查询，您可以像下面一样提问"
  />
</template>
