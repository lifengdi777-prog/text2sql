<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

// 数据集问答下的所有子路由(列表 / 对某数据集问数)都高亮"数据集问答"
const isDatasetSection = computed(() => route.path.startsWith('/datasets'))

const navItems = [
  { key: 'db', label: '数据库问答', desc: 'MySQL 数仓', to: '/db', icon: '🗄️' },
  { key: 'dataset', label: '数据集问答', desc: '上传的 Excel', to: '/datasets', icon: '📊' },
]

function isActive(key: string): boolean {
  return key === 'dataset' ? isDatasetSection.value : route.path.startsWith('/db')
}
</script>

<template>
  <div class="flex h-screen w-screen overflow-hidden bg-slate-100 text-[14px] text-slate-900">
    <aside
      class="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white/90 px-4 py-6 backdrop-blur"
    >
      <div class="px-2">
        <p class="text-xs font-semibold uppercase tracking-[0.35em] text-sky-600">Wenshu</p>
        <h1 class="mt-1 text-lg font-semibold tracking-tight text-slate-900">智能问数</h1>
      </div>

      <nav class="mt-8 flex flex-col gap-2">
        <router-link
          v-for="item in navItems"
          :key="item.key"
          :to="item.to"
          class="group flex items-start gap-3 rounded-2xl border px-4 py-3 transition"
          :class="
            isActive(item.key)
              ? 'border-sky-200 bg-sky-50 text-sky-700 shadow-sm'
              : 'border-transparent text-slate-600 hover:border-slate-200 hover:bg-slate-50'
          "
        >
          <span class="text-lg leading-none" aria-hidden="true">{{ item.icon }}</span>
          <span class="flex flex-col">
            <span class="text-sm font-semibold">{{ item.label }}</span>
            <span class="text-xs text-slate-400">{{ item.desc }}</span>
          </span>
        </router-link>
      </nav>

      <div class="mt-auto px-2 text-[11px] text-slate-400">Text2SQL · MVP</div>
    </aside>

    <main class="relative flex-1 overflow-hidden">
      <div class="pointer-events-none absolute inset-0 overflow-hidden">
        <div class="absolute left-[-8rem] top-[-6rem] h-72 w-72 rounded-full bg-sky-200/50 blur-3xl" />
        <div
          class="absolute bottom-[-8rem] right-[-4rem] h-80 w-80 rounded-full bg-emerald-200/50 blur-3xl"
        />
      </div>

      <div class="relative h-full p-4 sm:p-6 lg:p-8">
        <router-view />
      </div>
    </main>
  </div>
</template>
