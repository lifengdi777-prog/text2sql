# 前端镜像:多阶段。stage1 用 node 构建静态产物,stage2 用 nginx 托管 + 反代后端。
# 不设 VITE_API_BASE_URL → 前端走相对路径(同源),由 nginx 把 API 前缀代理到后端,
# 因此「构建一次,到处部署」,后端地址只在 nginx 配置里,不打进 JS。

# ── stage 1: 构建 ──────────────────────────────────────────────────────
FROM node:22-alpine AS build
WORKDIR /app

# 先装依赖(利用层缓存:改源码不重装)
COPY package.json package-lock.json ./
RUN npm ci

# 再拷源码构建。用 build-only(vite build)跳过 vue-tsc 类型检查,
# 加快出包、避免类型告警阻断部署镜像构建(类型检查留给开发期/CI)。
COPY . .
RUN npm run build-only

# ── stage 2: 运行 ──────────────────────────────────────────────────────
FROM nginx:alpine AS runtime
# 自定义站点配置(SPA history fallback + API 反代 + SSE 流式)
COPY nginx.conf /etc/nginx/conf.d/default.conf
# 构建产物
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
