<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { confirmHeader, getHeaderReview } from '@/services/dataset'
import type { HeaderConfirmSpec, HeaderReview } from '@/types/dataset'

const props = defineProps<{ open: boolean; datasetId: number | null }>()
const emit = defineEmits<{ close: []; confirmed: [] }>()

const review = ref<HeaderReview | null>(null)
const loading = ref(false)
const error = ref('')
const submitting = ref(false)
// 每个 sheet 选中的「表头行」下标(0 起,可多选 → 合并表头)
const selected = ref<Record<string, number[]>>({})

const sheetNames = computed(() => Object.keys(review.value?.sheets ?? {}))

// ── 列名扁平化:与后端 header_detect 的 _ffill_row/_flatten_header 行为一致 ──
function ffillRow(row: string[]): string[] {
  const out: string[] = []
  let last = ''
  for (const c of row) {
    if (c) last = c
    out.push(last)
  }
  return out
}

function flattenHeader(rows: string[][], width: number): string[] {
  let names: string[]
  if (rows.length <= 1) {
    const top = rows[0] ?? []
    names = Array.from({ length: width }, (_, i) => top[i] ?? '')
  } else {
    const filled = rows.map((r) =>
      ffillRow([...r, ...Array(Math.max(0, width - r.length)).fill('')]),
    )
    names = Array.from({ length: width }, (_, i) => {
      const parts: string[] = []
      for (const r of filled) {
        const v = r[i]
        if (v && !parts.includes(v)) parts.push(v)
      }
      return parts.join('')
    })
  }
  const used = new Set<string>()
  return names.map((n, i) => {
    const base = (n ?? '').trim() || `列${i + 1}`
    let name = base
    let k = 1
    while (used.has(name)) {
      k += 1
      name = `${base}_${k}`
    }
    used.add(name)
    return name
  })
}

function headerRows(sheet: string): number[] {
  return selected.value[sheet] ?? []
}

function columnsOf(sheet: string): string[] {
  const sh = review.value?.sheets?.[sheet]
  if (!sh) return []
  const rows = headerRows(sheet).map((i) => sh.grid[i] ?? [])
  return flattenHeader(rows, sh.width)
}

function dataStartOf(sheet: string): number {
  const rows = headerRows(sheet)
  return rows.length ? Math.max(...rows) + 1 : 0
}

function firstDataRow(sheet: string): string[] | null {
  const sh = review.value?.sheets?.[sheet]
  if (!sh) return null
  return sh.grid[dataStartOf(sheet)] ?? null
}

function toggleRow(sheet: string, idx: number) {
  const cur = selected.value[sheet] ?? []
  const next = cur.includes(idx) ? cur.filter((i) => i !== idx) : [...cur, idx].sort((a, b) => a - b)
  selected.value = { ...selected.value, [sheet]: next }
}

const canSubmit = computed(
  () => sheetNames.value.length > 0 && sheetNames.value.every((s) => headerRows(s).length > 0),
)

async function load() {
  if (props.datasetId == null) return
  loading.value = true
  error.value = ''
  review.value = null
  try {
    const data = await getHeaderReview(props.datasetId)
    if (!data.needs_review || !data.sheets) {
      error.value = '该数据集当前不需要确认表头(可能已处理完成)'
      return
    }
    review.value = data
    // 预选后端建议的表头行
    const init: Record<string, number[]> = {}
    for (const [name, sh] of Object.entries(data.sheets)) {
      init[name] = [...(sh.suggested.header_rows ?? [])]
    }
    selected.value = init
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载预览失败'
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (props.datasetId == null || !canSubmit.value || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const specs: Record<string, HeaderConfirmSpec> = {}
    for (const name of sheetNames.value) {
      specs[name] = { data_start_row: dataStartOf(name), columns: columnsOf(name) }
    }
    await confirmHeader(props.datasetId, specs)
    emit('confirmed')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '提交失败，请重试'
  } finally {
    submitting.value = false
  }
}

function close() {
  if (submitting.value) return
  emit('close')
}

watch(
  () => [props.open, props.datasetId],
  () => {
    if (props.open) void load()
  },
  { immediate: true },
)
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm"
    @click.self="close"
  >
    <div class="flex max-h-[88vh] w-full max-w-3xl flex-col rounded-3xl border border-white/70 bg-white shadow-2xl">
      <!-- 头部 -->
      <div class="flex items-start justify-between border-b border-slate-100 p-5">
        <div>
          <h2 class="text-lg font-semibold text-slate-900">确认表头</h2>
          <p class="mt-1 text-xs leading-relaxed text-slate-500">
            自动识别不确定。请在每个表里<span class="font-medium text-slate-700">点击作为表头的行</span>（合并表头可多选连续行），下方会实时显示解析出的列名。
          </p>
        </div>
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          @click="close"
        >
          ✕
        </button>
      </div>

      <!-- 主体 -->
      <div class="flex-1 overflow-y-auto p-5">
        <p v-if="loading" class="py-12 text-center text-sm text-slate-400">加载预览中…</p>
        <p v-else-if="error && !review" class="py-12 text-center text-sm text-rose-500">{{ error }}</p>

        <div v-else-if="review" class="space-y-6">
          <section
            v-for="name in sheetNames"
            :key="name"
            class="rounded-2xl border p-4"
            :class="review.sheets?.[name]?.flagged ? 'border-amber-300 bg-amber-50/40' : 'border-slate-200'"
          >
            <div class="mb-2 flex items-center gap-2">
              <h3 class="text-sm font-semibold text-slate-800">{{ name }}</h3>
              <span
                v-if="review.sheets?.[name]?.flagged"
                class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700"
              >
                需确认
              </span>
              <span class="ml-auto text-xs text-slate-400">点击行选为表头</span>
            </div>

            <!-- 原始网格预览 -->
            <div class="overflow-x-auto rounded-lg border border-slate-200">
              <table class="min-w-full border-collapse text-xs">
                <tbody>
                  <tr
                    v-for="(row, ri) in review.sheets?.[name]?.grid ?? []"
                    :key="ri"
                    class="cursor-pointer border-b border-slate-100 transition last:border-0"
                    :class="
                      headerRows(name).includes(ri)
                        ? 'bg-emerald-100/70 hover:bg-emerald-100'
                        : ri >= dataStartOf(name)
                          ? 'hover:bg-slate-50'
                          : 'bg-slate-50/60 text-slate-400 hover:bg-slate-100'
                    "
                    @click="toggleRow(name, ri)"
                  >
                    <td class="whitespace-nowrap px-2 py-1.5 text-center align-middle">
                      <span
                        class="inline-flex h-4 w-4 items-center justify-center rounded border text-[10px]"
                        :class="
                          headerRows(name).includes(ri)
                            ? 'border-emerald-500 bg-emerald-500 text-white'
                            : 'border-slate-300 text-transparent'
                        "
                      >
                        ✓
                      </span>
                    </td>
                    <td class="whitespace-nowrap px-2 py-1.5 text-right text-[10px] text-slate-400">
                      行{{ ri }}
                    </td>
                    <td
                      v-for="(cell, ci) in (review.sheets?.[name]?.grid ?? [])[ri]"
                      :key="ci"
                      class="max-w-[140px] truncate border-l border-slate-100 px-2 py-1.5"
                      :title="cell"
                    >
                      {{ cell || '∅' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 解析出的列名（实时） -->
            <div class="mt-3">
              <p class="mb-1.5 text-xs font-medium text-slate-600">
                解析出的列名
                <span class="font-normal text-slate-400">
                  （表头 {{ headerRows(name).length }} 行 · 数据从「行{{ dataStartOf(name) }}」起）
                </span>
              </p>
              <div v-if="headerRows(name).length" class="flex flex-wrap gap-1.5">
                <span
                  v-for="(col, ci) in columnsOf(name)"
                  :key="ci"
                  class="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-700"
                >
                  {{ col }}
                </span>
              </div>
              <p v-else class="text-xs text-rose-500">请至少选择一行作为表头</p>
              <p v-if="firstDataRow(name)" class="mt-2 text-[11px] text-slate-400">
                首行数据预览：{{ (firstDataRow(name) ?? []).map((c) => c || '∅').join(' · ') }}
              </p>
            </div>
          </section>
        </div>
      </div>

      <!-- 底部 -->
      <div class="flex items-center justify-between gap-3 border-t border-slate-100 p-5">
        <p class="text-xs text-rose-500">{{ review && error ? error : '' }}</p>
        <div class="flex gap-3">
          <button
            type="button"
            class="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
            :disabled="submitting"
            @click="close"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-xl bg-emerald-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
            :disabled="!canSubmit || submitting"
            @click="submit"
          >
            {{ submitting ? '提交中…' : '确认并重新解析' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
