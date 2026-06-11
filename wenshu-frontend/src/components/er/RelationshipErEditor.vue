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
import { effectiveColumnRole } from '@/lib/metaRoles'

import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

const props = defineProps<{ tables: MetaTable[] }>()
// 与父组件 meta.relationships 共享同一数组引用,增删即时反映到保存数据里
const relationships = defineModel<MetaRelationship[]>({ required: true })

const NODE_WIDTH = 300
const HEAD_HEIGHT = 44
const ROW_HEIGHT = 34

const nodes = ref<Node[]>([])
const edges = ref<Edge[]>([])

// 待删除的关系(打开自定义确认弹窗;null=未打开)。存四端点而非数组下标,
// 确认时再 findIndex,避免弹窗期间数组变动导致下标失效、误删别的边。
const pendingDelete = ref<{
  from_table: string
  from_column: string
  to_table: string
  to_column: string
} | null>(null)

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
        // 图标用「有效角色」:叠加当前(含未保存)关系,拖线/删线即时反映外键身份。
        columns: t.columns.map((c) => ({
          name: c.name,
          type: c.type,
          role: effectiveColumnRole(c.role, t.name, c.name, relationships.value),
        })),
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
        style: { stroke: '#10b981', strokeWidth: 3 },
        markerEnd: MarkerType.ArrowClosed,
      }
    })
}

// 从锚点 id(形如 product_id/sr、product_id/tl)还原列名
function colOf(handle: string | null | undefined): string {
  return (handle ?? '').replace(/\/[st][lr]$/, '')
}

function relayout() {
  // 先按 dagre 重排节点位置,再据新位置重算连线 —— 否则连线还挂在旧位置算出的锚点侧上,
  // 会错位/穿表(与拖动表后的 onNodeDragStop 同理,都要 buildEdges 重新就近布线)。
  nodes.value = computeNodes()
  edges.value = buildEdges()
}

onMounted(() => {
  nodes.value = computeNodes()
  edges.value = buildEdges()
})

// 关系变化(拖线新增 / 点线删除 / 列表视图改动)→ 重算连线 + 就地刷新字段图标;
// 节点位置保持不动(只更新 data.columns 的角色,不重跑 dagre 布局,不打乱已拖好的布局)
watch(
  relationships,
  () => {
    edges.value = buildEdges()
    refreshRoles()
  },
  { deep: true },
)

// 按当前关系重算各节点字段的「有效角色」,原地更新节点 data(保留位置)。
function refreshRoles() {
  // 原地更新 n.data,不 {...n} 整个 Node 重建 —— spread Node 会让 TS 展开
  // @vue-flow 的深层泛型联合直接 TS2589;只动 data 字段类型最浅,语义不变。
  for (const n of nodes.value) {
    const t = props.tables.find((tt) => tt.name === n.id)
    if (!t) continue
    n.data = {
      ...n.data,
      columns: t.columns.map((c) => ({
        name: c.name,
        type: c.type,
        role: effectiveColumnRole(c.role, t.name, c.name, relationships.value),
      })),
    }
  }
  // 不再整体重赋值 nodes.value:ref 是深响应的,n.data 的原地赋值即可触发更新
  // (对 Node[] 做 spread/重赋值还会让 TS 的 UnwrapRef 递归展开深层泛型,报 TS2589)
}

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
  // 打开自定义确认弹窗(替代浏览器原生 confirm,统一 UI),确认后再删。
  pendingDelete.value = {
    from_table: edge.source,
    from_column: fromCol,
    to_table: edge.target,
    to_column: toCol,
  }
}

// 确认删除:按四端点重新定位再删(弹窗期间数组可能变动,不复用打开时的下标)。
function confirmDelete() {
  const d = pendingDelete.value
  if (!d) return
  const idx = relationships.value.findIndex(
    (r) =>
      r.from_table === d.from_table &&
      r.from_column === d.from_column &&
      r.to_table === d.to_table &&
      r.to_column === d.to_column,
  )
  if (idx !== -1) relationships.value.splice(idx, 1)
  pendingDelete.value = null
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

    <div class="er-canvas flex-1 overflow-hidden rounded-2xl border border-slate-700 bg-slate-800">
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
        <Background variant="lines" :gap="20" :size="1" pattern-color="#334155" />
      </VueFlow>
    </div>

    <!-- 删除关系确认弹窗(替代原生 confirm,与保存确认弹窗同款风格) -->
    <div
      v-if="pendingDelete"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      @click.self="pendingDelete = null"
    >
      <div class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <h3 class="text-base font-semibold text-slate-800">删除表关系</h3>
        <p class="mt-1 text-xs text-slate-500">
          确认删除以下连接关系？删除后需点右上角「保存关系」才会生效。
        </p>
        <div class="mt-3 space-y-1 break-all rounded-xl bg-slate-50 px-3 py-2.5 font-mono text-sm">
          <div class="text-sky-600">{{ pendingDelete.from_table }}.{{ pendingDelete.from_column }}</div>
          <div class="text-slate-400">↓</div>
          <div class="text-amber-600">{{ pendingDelete.to_table }}.{{ pendingDelete.to_column }}</div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
            @click="pendingDelete = null"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-xl bg-rose-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-rose-600"
            @click="confirmDelete"
          >
            删除
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.er-canvas {
  min-height: 0;
}
/* 连线 hover 时变红 + 更粗,提示「可点删除」(用 !important 盖过内联 strokeWidth) */
.er-canvas :deep(.vue-flow__edge-path:hover) {
  stroke: #f43f5e !important;
  stroke-width: 4 !important;
  cursor: pointer;
}
</style>
