<script setup lang="ts">
// 表关系的「可视化编辑器」:把 tables 画成 ER 实体框,relationships 画成字段间的连线。
// 它只是 data_relationship 的另一张操作皮肤——
//   · 从某字段右侧锚点拖到另一字段左侧锚点 → 新增一条关系(push 到 relationships)
//   · 点击一条连线 → 确认后删除该关系(splice)
// relationships 用 v-model 与父组件共享同一个数组,父组件「保存关系」时原样提交,后端逻辑零改动。
import { onMounted, ref, watch } from 'vue'
import { VueFlow, MarkerType, Position } from '@vue-flow/core'
import type { Connection, Edge, EdgeMouseEvent, Node } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import dagre from 'dagre'

import TableNode from './TableNode.vue'
import type { MetaRelationship, MetaTable } from '@/types/datasource'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = defineProps<{ tables: MetaTable[] }>()
// 与父组件 meta.relationships 共享同一数组引用,增删即时反映到保存数据里
const relationships = defineModel<MetaRelationship[]>({ required: true })

const NODE_WIDTH = 240
const HEAD_HEIGHT = 36
const ROW_HEIGHT = 25

const nodes = ref<Node[]>([])
const edges = ref<Edge[]>([])

// dagre 自动布局:按表的字段数估算高度,左→右(rankdir=LR)排开,避免初始全堆在原点
function computeNodes(): Node[] {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 48, ranksep: 110 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const t of props.tables) {
    g.setNode(t.name, { width: NODE_WIDTH, height: HEAD_HEIGHT + t.columns.length * ROW_HEIGHT })
  }
  for (const r of relationships.value) {
    if (r.from_table && r.to_table) g.setEdge(r.from_table, r.to_table)
  }
  dagre.layout(g)
  return props.tables.map((t) => {
    const n = g.node(t.name)
    return {
      id: t.name,
      type: 'table',
      position: { x: n.x - n.width / 2, y: n.y - n.height / 2 },
      // 让连线从右进左出,方向更顺
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        name: t.name,
        role: t.role,
        columns: t.columns.map((c) => ({ name: c.name, type: c.type, role: c.role })),
      },
    }
  })
}

// 当前各节点的 x 坐标(用于判断连线该走哪一侧),拖动后会更新
function nodeXMap(): Map<string, number> {
  const m = new Map<string, number>()
  for (const n of nodes.value) m.set(n.id, n.position.x)
  return m
}

// 只渲染四端点齐全的关系(列表里新加的空白关系不画线,但仍保留在数组中)。
// 连线走哪侧由两表相对位置决定:目标在右 → 从源表右锚点(/r)出、连到目标表左锚点(/l);反之相反。
// 这样线就近进出、不再全挤在右边,曲线(bezier)也比直角折线柔和。
function buildEdges(): Edge[] {
  const xs = nodeXMap()
  return relationships.value
    .filter((r) => r.from_table && r.from_column && r.to_table && r.to_column)
    .map((r, i) => {
      const rightward = (xs.get(r.from_table) ?? 0) <= (xs.get(r.to_table) ?? 0)
      return {
        id: `er-${i}-${r.from_table}.${r.from_column}-${r.to_table}.${r.to_column}`,
        source: r.from_table,
        sourceHandle: `${r.from_column}/${rightward ? 'sr' : 'sl'}`,
        target: r.to_table,
        targetHandle: `${r.to_column}/${rightward ? 'tl' : 'tr'}`,
        style: { stroke: '#10b981', strokeWidth: 1.6 },
        markerEnd: MarkerType.ArrowClosed,
      }
    })
}

// 从锚点 id(形如 product_id/sr、product_id/tl)还原列名
function colOf(handle: string | null | undefined): string {
  return (handle ?? '').replace(/\/[st][lr]$/, '')
}

function relayout() {
  nodes.value = computeNodes()
}

onMounted(() => {
  nodes.value = computeNodes()
  edges.value = buildEdges()
})

// 关系变化(拖线新增 / 点线删除 / 列表视图改动)→ 重算连线;节点位置保持不动(不打乱已拖好的布局)
watch(
  relationships,
  () => {
    edges.value = buildEdges()
  },
  { deep: true },
)

// 拖线:从任一字段锚点拖到另一字段锚点 → 新增一条关系(还原列名、去重、禁止自连)
function onConnect(c: Connection) {
  if (!c.source || !c.target || !c.sourceHandle || !c.targetHandle) return
  const fromCol = colOf(c.sourceHandle)
  const toCol = colOf(c.targetHandle)
  if (c.source === c.target && fromCol === toCol) return
  const dup = relationships.value.some(
    (r) =>
      r.from_table === c.source &&
      r.from_column === fromCol &&
      r.to_table === c.target &&
      r.to_column === toCol,
  )
  if (dup) return
  relationships.value.push({
    from_table: c.source,
    from_column: fromCol,
    to_table: c.target,
    to_column: toCol,
    description: null,
  })
}

// 拖动表后重算连线,让线就近重新进出(否则方向还停在拖动前的判断)
function onNodeDragStop() {
  edges.value = buildEdges()
}

// 点连线:确认后删除该关系。注意 edge 的 handle 带 /sr /tl 后缀,要还原成列名再比对
function onEdgeClick({ edge }: EdgeMouseEvent) {
  const fromCol = colOf(edge.sourceHandle)
  const toCol = colOf(edge.targetHandle)
  const idx = relationships.value.findIndex(
    (r) =>
      r.from_table === edge.source &&
      r.from_column === fromCol &&
      r.to_table === edge.target &&
      r.to_column === toCol,
  )
  if (idx === -1) return
  if (!window.confirm(`删除关系：${edge.source}.${fromCol} → ${edge.target}.${toCol} ？`)) return
  relationships.value.splice(idx, 1)
}
</script>

<template>
  <div class="flex h-full w-full flex-col">
    <div class="mb-2 flex items-center justify-between text-xs text-slate-500">
      <span>
        从外键字段的圆点 <b class="text-slate-600">拖线</b> 到主键字段的圆点 = 新增关系（拖出端=外键，落入端=主键）；
        <b class="text-slate-600">点击连线</b> 可删除。改完点右上角「保存关系」生效。
      </span>
      <button
        type="button"
        class="shrink-0 rounded-lg border border-slate-200 px-3 py-1 text-slate-600 transition hover:bg-slate-50"
        @click="relayout"
      >
        重新布局
      </button>
    </div>

    <div class="er-canvas flex-1 overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <VueFlow
        v-model:nodes="nodes"
        v-model:edges="edges"
        :fit-view-on-init="true"
        :min-zoom="0.2"
        :max-zoom="1.6"
        :default-edge-options="{ type: 'default' }"
        @connect="onConnect"
        @edge-click="onEdgeClick"
        @node-drag-stop="onNodeDragStop"
      >
        <template #node-table="nodeProps">
          <TableNode :data="nodeProps.data" />
        </template>
        <!-- 方格纸网格:lines 变体画横竖网格线(绘图工具画布那种) -->
        <Background variant="lines" :gap="20" :size="1" pattern-color="#e6eaf0" />
      </VueFlow>
    </div>
  </div>
</template>

<style scoped>
.er-canvas {
  min-height: 0;
}
/* 连线 hover 时变红 + 加粗,提示「可点删除」 */
.er-canvas :deep(.vue-flow__edge-path:hover) {
  stroke: #f43f5e !important;
  stroke-width: 2.4;
  cursor: pointer;
}
</style>
