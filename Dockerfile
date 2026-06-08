# 文枢后端镜像。基于官方 uv + Python 3.13 镜像。
# 构建上下文 = 仓库根目录(见 docker/docker-compose.yaml 的 build.context: ..)。
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# UV_COMPILE_BYTECODE: 预编译 .pyc 加快启动;UV_LINK_MODE=copy: 容器内无硬链接告警。
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 个别依赖(如 asyncmy)可能需从源码编译,装最小编译链;装完即清 apt 缓存。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先只拷依赖清单装依赖,利用 Docker 层缓存:改代码不重装依赖。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# 再拷源码并安装项目本身。
COPY . .
RUN uv sync --frozen

# 让 venv 的可执行文件直接可用(uvicorn 等)。
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# 生产可加 --workers N 提升并发(改 compose 的 command 覆盖即可)。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
