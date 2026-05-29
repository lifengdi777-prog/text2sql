<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { toAuthError } from '@/services/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const submitting = ref(false)
const error = ref('')

function switchMode(next: 'login' | 'register') {
  mode.value = next
  error.value = ''
}

async function submit() {
  if (submitting.value) return
  error.value = ''

  const name = username.value.trim()
  if (name.length < 3 || name.length > 32) {
    error.value = '用户名长度需为 3-32 个字符'
    return
  }
  if (password.value.length < 6) {
    error.value = '密码至少 6 位'
    return
  }

  submitting.value = true
  try {
    if (mode.value === 'login') {
      await auth.login(name, password.value)
    } else {
      await auth.register(name, password.value)
    }
    const redirect = (route.query.redirect as string) || '/db'
    await router.replace(redirect)
  } catch (e) {
    error.value = toAuthError(e)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="relative flex h-screen w-screen items-center justify-center overflow-hidden bg-slate-100">
    <div class="pointer-events-none absolute inset-0 overflow-hidden">
      <div class="absolute left-[-8rem] top-[-6rem] h-72 w-72 rounded-full bg-sky-200/50 blur-3xl" />
      <div class="absolute bottom-[-8rem] right-[-4rem] h-80 w-80 rounded-full bg-emerald-200/50 blur-3xl" />
    </div>

    <div class="relative w-full max-w-sm rounded-3xl border border-white/70 bg-white p-8 shadow-2xl">
      <div class="text-center">
        <p class="text-xs font-semibold uppercase tracking-[0.35em] text-sky-600">Wenshu</p>
        <h1 class="mt-1 text-2xl font-semibold tracking-tight text-slate-900">智能问数</h1>
        <p class="mt-2 text-sm text-slate-400">
          {{ mode === 'login' ? '登录以继续' : '创建一个新账号' }}
        </p>
      </div>

      <!-- 登录 / 注册切换 -->
      <div class="mt-6 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1 text-sm font-medium">
        <button
          type="button"
          class="rounded-lg py-2 transition"
          :class="mode === 'login' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500'"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          type="button"
          class="rounded-lg py-2 transition"
          :class="mode === 'register' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500'"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <form class="mt-6 flex flex-col gap-4" @submit.prevent="submit">
        <label class="flex flex-col gap-1.5">
          <span class="text-xs font-medium text-slate-500">用户名</span>
          <input
            v-model="username"
            type="text"
            autocomplete="username"
            placeholder="3-32 个字符"
            class="h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none transition focus:border-sky-300"
          />
        </label>

        <label class="flex flex-col gap-1.5">
          <span class="text-xs font-medium text-slate-500">密码</span>
          <input
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            placeholder="至少 6 位"
            class="h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none transition focus:border-sky-300"
          />
        </label>

        <p v-if="error" class="text-xs text-rose-500">{{ error }}</p>

        <button
          type="submit"
          class="mt-1 h-11 rounded-xl bg-emerald-500 text-sm font-semibold text-white transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-300"
          :disabled="submitting"
        >
          {{ submitting ? '处理中…' : mode === 'login' ? '登录' : '注册并登录' }}
        </button>
      </form>
    </div>
  </div>
</template>
