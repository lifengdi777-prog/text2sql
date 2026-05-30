import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  server: {
    // 用正则 key:vite proxy 的字符串 key 是「前缀匹配」,'/dataset' 会顺带
    // 把 SPA 路由 /datasets(列表页)也代理到后端导致刷新 404。
    // '^/dataset(/|$)' 只匹配 /dataset 与 /dataset/...,放过 /datasets。
    proxy: {
      '^/agent(/|$)': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '^/dataset(/|$)': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '^/auth(/|$)': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // 注意:列表请求是 /conversations?source=db(裸资源+查询串),
      // 末尾要额外匹配 '?',否则 '?' 既非 '/' 也非结尾,会漏匹配 → 不走代理。
      '^/conversations(/|\\?|$)': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
