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

// 满幅页面(聊天页/归因页):去掉外壳留白,让页面自己管理铺满与滚动
// (外壳是 h-screen overflow-hidden,这些页面在内部各自滚动)
const isChatView = computed(
  () => route.name === 'db-chat' || route.name === 'dataset-chat' || route.name === 'attribution',
)

const navItems = [
  // 不再有「默认数据源」:数据库问数统一从「数据源」页选库后「开启问数」进入。
  { key: 'source', label: '数据源', desc: 'MySQL 连接管理', to: '/sources' },
  { key: 'dataset', label: '数据集问答', desc: '上传的 Excel', to: '/datasets' },
]

// 各入口按路径前缀高亮('/db' '/sources' '/datasets' 互不为前缀)
function isActive(item: { to: string }): boolean {
  return route.path.startsWith(item.to)
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
      class="relative z-10 flex w-60 shrink-0 flex-col border-r-2 border-slate-200 bg-white/90 px-4 py-6 shadow-[3px_0_10px_rgba(15,23,42,0.05)] backdrop-blur"
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
            isActive(item)
              ? 'border-sky-200 bg-sky-50 text-sky-700 shadow-sm'
              : 'border-transparent text-slate-600 hover:border-slate-200 hover:bg-slate-50'
          "
        >
          <span
            class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition"
            :class="
              isActive(item)
                ? 'bg-sky-100 text-sky-600'
                : 'bg-slate-100 text-slate-400 group-hover:bg-slate-200 group-hover:text-slate-600'
            "
            aria-hidden="true"
          >
            <!-- 数据源:数据库圆柱 -->
            <svg
              v-if="item.key === 'source'"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              class="h-5 w-5"
            >
              <ellipse cx="12" cy="5" rx="7" ry="3" />
              <path d="M5 5v14c0 1.66 3.13 3 7 3s7-1.34 7-3V5" />
              <path d="M5 12c0 1.66 3.13 3 7 3s7-1.34 7-3" />
            </svg>
            <!-- 数据集问答:表格 -->
            <svg
              v-else
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              class="h-5 w-5"
            >
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M3 9.5h18M3 14.5h18M9 4v16" />
            </svg>
          </span>
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
      </div>
    </aside>

    <main class="relative flex-1 overflow-hidden">
      <div class="pointer-events-none absolute inset-0 overflow-hidden">
        <div class="absolute left-[-8rem] top-[-6rem] h-72 w-72 rounded-full bg-sky-200/50 blur-3xl" />
        <div
          class="absolute bottom-[-8rem] right-[-4rem] h-80 w-80 rounded-full bg-emerald-200/50 blur-3xl"
        />
      </div>

      <div
        class="relative h-full"
        :class="isChatView ? '' : 'p-4 sm:p-6 lg:p-8'"
      >
        <router-view />
      </div>
    </main>
  </div>
</template>
