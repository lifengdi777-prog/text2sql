<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 启动时若已有 token,拉一次 /auth/me 校验是否仍有效(失效会自动登出)
onMounted(() => {
  void auth.refreshMe()
})

// 登录页用独立全屏布局,不套侧边栏外壳
const isAuthPage = computed(() => route.name === 'login')

// 数据集问答下的所有子路由(列表 / 对某数据集问数)都高亮"数据集问答"
const isDatasetSection = computed(() => route.path.startsWith('/datasets'))

const navItems = [
  { key: 'db', label: '数据库问答', desc: 'MySQL 数仓', to: '/db', icon: '🗄️' },
  { key: 'dataset', label: '数据集问答', desc: '上传的 Excel', to: '/datasets', icon: '📊' },
]

function isActive(key: string): boolean {
  return key === 'dataset' ? isDatasetSection.value : route.path.startsWith('/db')
}

async function logout() {
  auth.logout()
  await router.replace('/login')
}
</script>

<template>
  <!-- 登录页:独立全屏,无侧边栏 -->
  <router-view v-if="isAuthPage" />

  <div v-else class="flex h-screen w-screen overflow-hidden bg-slate-100 text-[14px] text-slate-900">
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

      <div class="mt-auto flex flex-col gap-3">
        <div
          v-if="auth.user"
          class="flex items-center justify-between gap-2 rounded-2xl border border-slate-200 bg-slate-50/80 px-3 py-2.5"
        >
          <div class="flex min-w-0 items-center gap-2">
            <span
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sky-100 text-sm font-semibold text-sky-700"
            >
              {{ auth.user.username.charAt(0).toUpperCase() }}
            </span>
            <span class="truncate text-sm font-medium text-slate-700">{{ auth.user.username }}</span>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-lg px-2 py-1 text-xs text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-600"
            title="退出登录"
            @click="logout"
          >
            退出
          </button>
        </div>
        <div class="px-2 text-[11px] text-slate-400">Text2SQL · MVP</div>
      </div>
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
