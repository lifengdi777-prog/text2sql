<script setup lang="ts">
// ER 图里的「表实体」节点:表名做标题,下面逐行列出字段(列名 : 类型)。
// 每行字段左右各挂一个 Handle(连线锚点):右=source(从这里拖出),左=target(连到这里)。
// 这样连线能精确落到「字段对字段」,且 FK→PK 的方向就是 source→target。
import { Handle, Position } from '@vue-flow/core'

defineProps<{
  data: {
    name: string
    role: string
    columns: { name: string; type: string; role: string }[]
  }
}>()
</script>

<template>
  <div class="er-node">
    <div class="er-node__head">{{ data.name }}</div>
    <div class="er-node__body">
      <div v-for="col in data.columns" :key="col.name" class="er-row">
        <!-- 每列左右各「目标+源」两个锚点(视觉重叠成一个点)。源在后、位于上层,便于发起拖拽;
             目标供连线落入。连线走哪侧由编辑器按两表相对位置选择(/sl /sr /tl /tr)。 -->
        <Handle :id="`${col.name}/tl`" type="target" :position="Position.Left" class="er-handle" />
        <Handle :id="`${col.name}/sl`" type="source" :position="Position.Left" class="er-handle" />
        <span class="er-row__name">
          <!-- 主键:金色钥匙 -->
          <svg
            v-if="col.role === 'primary_key'"
            class="er-row__mark" viewBox="0 0 24 24" fill="none"
            stroke="#f59e0b" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          >
            <circle cx="7.5" cy="15.5" r="5.5" />
            <path d="m21 2-9.6 9.6" />
            <path d="m15.5 7.5 3 3L22 7l-3-3" />
          </svg>
          <!-- 外键:蓝色链接 -->
          <svg
            v-else-if="col.role === 'foreign_key'"
            class="er-row__mark" viewBox="0 0 24 24" fill="none"
            stroke="#0ea5e9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          >
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
          {{ col.name }}
        </span>
        <span class="er-row__type">{{ col.type }}</span>
        <Handle :id="`${col.name}/tr`" type="target" :position="Position.Right" class="er-handle" />
        <Handle :id="`${col.name}/sr`" type="source" :position="Position.Right" class="er-handle" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.er-node {
  width: 300px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(2, 6, 23, 0.45);
  overflow: hidden;
  font-size: 16px;
}
.er-node__head {
  background: #3b82f6;
  color: #fff;
  padding: 9px 14px;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.er-node__body {
  padding: 2px 0;
}
.er-row {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 14px;
  border-bottom: 1px solid #f1f5f9;
}
.er-row:last-child {
  border-bottom: none;
}
.er-row__name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #1e293b;
  white-space: nowrap;
}
.er-row__mark {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
}
.er-row__type {
  color: #94a3b8;
  white-space: nowrap;
}
/* 连接锚点:放大到 11px 好抓;hover 再放大并变深色,拖拽更容易命中 */
.er-handle {
  width: 11px;
  height: 11px;
  background: #93c5fd;
  border: 2px solid #fff;
  transition: transform 0.12s, background 0.12s;
}
.er-handle:hover {
  background: #2563eb;
  transform: scale(1.5);
}
</style>
