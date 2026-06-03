<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { buildDatasource, listDatasourceTables, registerDatasource } from '@/services/datasource'
import type { DatasourceTable } from '@/types/datasource'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; created: [] }>()

const STEPS = ['选择类型', '连接配置', '选择数据表']
const step = ref(1)
const busy = ref(false)
const error = ref('')

// 第②步表单
const form = ref({
  type: 'mysql',
  name: '',
  host: '',
  port: 3306,
  username: 'root',
  password: '',
  default_database: '',
})

// 第③步:注册后拿到的 id + 库里的表
const newId = ref<string | null>(null)
const tables = ref<DatasourceTable[]>([])
const selected = ref<string[]>([])

const allSelected = computed(
  () => tables.value.length > 0 && selected.value.length === tables.value.length,
)

function reset() {
  step.value = 1
  busy.value = false
  error.value = ''
  form.value = { type: 'mysql', name: '', host: '', port: 3306, username: 'root', password: '', default_database: '' }
  newId.value = null
  tables.value = []
  selected.value = []
}

function close() {
  if (busy.value) return
  reset()
  emit('close')
}

// 关闭时重置(下次打开是干净的)
watch(
  () => props.open,
  (v) => {
    if (!v) reset()
  },
)

const canNext2 = computed(
  () =>
    form.value.name.trim() &&
    form.value.host.trim() &&
    form.value.port &&
    form.value.username.trim() &&
    form.value.default_database.trim(),
)

// ②→③:注册(后端测连通,通过才入库)→ 拉表
async function registerAndNext() {
  if (!canNext2.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const { id } = await registerDatasource({
      name: form.value.name.trim(),
      host: form.value.host.trim(),
      port: Number(form.value.port),
      username: form.value.username.trim(),
      password: form.value.password,
      type: form.value.type,
      default_database: form.value.default_database.trim(),
    })
    newId.value = id
    tables.value = await listDatasourceTables(id)
    selected.value = tables.value.map((t) => t.name) // 默认全选
    step.value = 3
  } catch (e) {
    error.value = e instanceof Error ? e.message : '连接/注册失败'
  } finally {
    busy.value = false
  }
}

function toggleAll() {
  selected.value = allSelected.value ? [] : tables.value.map((t) => t.name)
}

function toggleOne(name: string) {
  const i = selected.value.indexOf(name)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(name)
}

// ③ 完成:触发异步接入(草稿+物化),关闭弹窗,列表轮询看进度
async function finish() {
  if (!newId.value || selected.value.length === 0 || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await buildDatasource(newId.value, selected.value)
    emit('created')
    reset()
    emit('close')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '触发接入失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm"
    @click.self="close"
  >
    <div class="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-3xl border border-white/70 bg-white shadow-2xl">
      <!-- 头 + 步骤条 -->
      <div class="flex items-center justify-between border-b border-slate-100 px-6 py-4">
        <h2 class="text-lg font-semibold text-slate-900">新建数据源</h2>
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          @click="close"
        >
          ✕
        </button>
      </div>
      <div class="flex items-center gap-2 px-6 py-3 text-xs">
        <template v-for="(s, i) in STEPS" :key="s">
          <span
            class="inline-flex items-center gap-1.5"
            :class="step >= i + 1 ? 'text-emerald-600' : 'text-slate-400'"
          >
            <span
              class="inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold"
              :class="step >= i + 1 ? 'bg-emerald-500 text-white' : 'bg-slate-200 text-slate-500'"
            >
              {{ i + 1 }}
            </span>
            {{ s }}
          </span>
          <span v-if="i < STEPS.length - 1" class="h-px w-8 bg-slate-200" />
        </template>
      </div>

      <div class="flex-1 overflow-y-auto px-6 py-4">
        <!-- ① 选类型 -->
        <div v-if="step === 1" class="grid grid-cols-2 gap-3">
          <button
            type="button"
            class="flex items-center gap-3 rounded-2xl border-2 border-emerald-300 bg-emerald-50 px-4 py-4 text-left"
          >
            <span class="text-2xl">🐬</span>
            <span>
              <span class="block text-sm font-semibold text-slate-800">MySQL</span>
              <span class="block text-xs text-slate-400">关系型数据库</span>
            </span>
          </button>
          <div class="flex items-center gap-3 rounded-2xl border-2 border-dashed border-slate-200 px-4 py-4 text-slate-300">
            <span class="text-2xl">＋</span>
            <span class="text-xs">更多类型敬请期待</span>
          </div>
        </div>

        <!-- ② 连接配置 -->
        <div v-else-if="step === 2" class="flex flex-col gap-3">
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-500">名称 *</label>
            <input v-model="form.name" type="text" placeholder="如：电商库"
              class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300" />
          </div>
          <div class="grid grid-cols-3 gap-3">
            <div class="col-span-2">
              <label class="mb-1 block text-xs font-medium text-slate-500">主机 *</label>
              <input v-model="form.host" type="text" placeholder="localhost"
                class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300" />
            </div>
            <div>
              <label class="mb-1 block text-xs font-medium text-slate-500">端口 *</label>
              <input v-model.number="form.port" type="number"
                class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300" />
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="mb-1 block text-xs font-medium text-slate-500">用户名 *</label>
              <input v-model="form.username" type="text"
                class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300" />
            </div>
            <div>
              <label class="mb-1 block text-xs font-medium text-slate-500">密码</label>
              <input v-model="form.password" type="password"
                class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300" />
            </div>
          </div>
          <div>
            <label class="mb-1 block text-xs font-medium text-slate-500">数据库 *</label>
            <input v-model="form.default_database" type="text" placeholder="如：e-commerce"
              class="h-10 w-full rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-sky-300" />
          </div>
        </div>

        <!-- ③ 选表 -->
        <div v-else class="flex flex-col gap-2">
          <div class="flex items-center justify-between">
            <label class="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" :checked="allSelected" @change="toggleAll" />
              全选（共 {{ tables.length }} 张表）
            </label>
            <span class="text-xs text-slate-400">已选 {{ selected.length }}</span>
          </div>
          <div class="max-h-72 overflow-y-auto rounded-xl border border-slate-100">
            <label
              v-for="t in tables"
              :key="t.name"
              class="flex cursor-pointer items-center gap-2 border-b border-slate-50 px-3 py-2 text-sm last:border-0 hover:bg-slate-50"
            >
              <input type="checkbox" :checked="selected.includes(t.name)" @change="toggleOne(t.name)" />
              <span class="font-medium text-slate-700">{{ t.name }}</span>
              <span v-if="t.comment" class="truncate text-xs text-slate-400">— {{ t.comment }}</span>
              <span v-if="t.rows != null" class="ml-auto text-xs text-slate-300">~{{ t.rows }} 行</span>
            </label>
          </div>
          <p class="text-xs text-slate-400">点「开始接入」后，系统会自动生成元数据并建索引（后台进行，可在列表看进度）。</p>
        </div>

        <p v-if="error" class="mt-3 text-xs text-rose-500">{{ error }}</p>
      </div>

      <!-- 底部按钮 -->
      <div class="flex justify-between border-t border-slate-100 px-6 py-4">
        <button
          type="button"
          class="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
          :disabled="busy"
          @click="close"
        >
          取消
        </button>
        <div class="flex gap-2">
          <button
            v-if="step === 1"
            type="button"
            class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600"
            @click="step = 2"
          >
            下一步
          </button>
          <template v-else-if="step === 2">
            <button
              type="button"
              class="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
              :disabled="busy"
              @click="step = 1"
            >
              上一步
            </button>
            <button
              type="button"
              class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
              :disabled="!canNext2 || busy"
              @click="registerAndNext"
            >
              {{ busy ? '连接中…' : '测试连接并下一步' }}
            </button>
          </template>
          <button
            v-else
            type="button"
            class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
            :disabled="selected.length === 0 || busy"
            @click="finish"
          >
            {{ busy ? '提交中…' : `开始接入（${selected.length}）` }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
