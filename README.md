# 文枢 wenshu

自然语言问数（Text2SQL）+ 数据分析平台。把中文问题转成 SQL，在你的数据仓库上执行，并给出图表与结论；同时支持 Excel 上传后直接问答。

后端 FastAPI + LangGraph，前端 Vue。多数据源、多轮对话、向量+全文混合召回、SQL 安全校验。

---

## 1. 架构与依赖

| 组件 | 用途 | 默认端口 | 是否必需 |
|---|---|---|---|
| MySQL | 元数据库 `meta` + 运营库 `wenshu` + 你的业务库 `dw` | 3307→3306 | ✅ |
| Qdrant | 列/指标向量召回 | 6333 | ✅ |
| Elasticsearch | 值的全文召回（IK 中文分词） | 9200 | ✅ |
| MinIO | Excel 上传/数据集存储（S3 兼容） | 9000 / 9001 | 用上传功能才需要 |
| 大模型 API | 问数主力（DeepSeek 或任意 OpenAI 兼容） | — | ✅（需自备 key） |
| Embedding API | 文本向量化（DashScope / OpenAI 兼容） | — | ✅（需自备 key） |
| Langfuse | 可观测 / 评测对比 | 3000 | 可选 |

**前置工具**：Docker & Docker Compose、[uv](https://docs.astral.sh/uv/)、Python ≥ 3.13、Node.js（构建前端）。

---

## 2. 快速开始

### 2.1 起基础设施

```bash
# 基础服务:mysql / elasticsearch / kibana / qdrant / minio
docker compose -f docker/docker-compose.yaml up -d

# 可选:同时起 Langfuse(可观测/评测)
# docker compose -f docker/docker-compose.yaml --profile observability up -d
```

> 注意：`docker/mysql`（业务库初始化 SQL）已被 gitignore，不随仓库分发。你需要把自己的业务库导入 MySQL，或在 `docker/mysql/` 放初始化脚本。本仓库不自带演示数据。

### 2.2 配置

```bash
cp conf/app_config.yaml.example conf/app_config.yaml
```

编辑 `conf/app_config.yaml`，**至少**填好这两项（其余默认值对应上面的 compose 服务，可不改）：

- `llm.api_key` —— 大模型 key
- `embedding_fallback.api_key` —— embedding key

> `conf/app_config.yaml` 已被 gitignore，含密钥，**不要提交**。

### 2.3 起后端

两种方式任选其一。

**方式 A：Docker（推荐，一键起全套）**

后端镜像已纳入 compose。无需 2.1 单独起基础设施，一条命令拉起基础设施 + 后端：

```bash
docker compose -f docker/docker-compose.yaml up -d --build
```

- 后端容器通过 `WENSHU_*` 环境变量把配置里的 host 自动覆盖成容器服务名，无需改 yaml。
- 真实 `conf/app_config.yaml` 以只读挂载注入容器（不打进镜像），日志落到宿主 `logs/`。
- 会等 MySQL 健康检查通过再启动后端，保证自动建表成功。

> 若你的 `db_dw`（业务库）是**外部独立数据库**，在 `conf/app_config.yaml` 里把 `db_dw.host/port` 写成它的真实地址即可（别用 `${oc.env:...}` 默认值）。

**方式 B：裸机（uv 本地跑）**

```bash
uv sync                       # 安装依赖
uv run python main.py         # 启动,监听 0.0.0.0:8000
```

不设环境变量时，配置里的 host 走默认值 `localhost`，连 2.1 起的基础设施。

首次启动会**自动**完成（幂等，重启无副作用）：
- 建库 `meta` / `wenshu` + 建表
- 旧表补列迁移
- 创建默认管理员 **`admin / admin`** ⚠️ 生产务必登录后改密

健康检查：`GET /healthz`（存活）、`GET /readyz`（依赖就绪）。

### 2.4 起前端

```bash
cd wenshu-frontend
npm install

# 开发模式
npm run dev

# 或生产构建(产物在 dist/,用 nginx 等静态服务托管)
npm run build
```

前端通过 `VITE_API_BASE_URL` 指向后端。开发时建后端同源代理，或新建 `wenshu-frontend/.env`：

```
VITE_API_BASE_URL=http://localhost:8000
```

### 2.5 登录并接入数据源

1. 浏览器打开前端，用 `admin / admin` 登录（**立刻改密**）。
2. 进「数据源管理」→ 注册你的业务库（host/port/账号/库名）。
3. 点「接入 / 构建」——后台自动生成元数据草稿并物化（写 meta 表 + Qdrant + ES），前端轮询状态，完成后即可问数。

CLI 等价流程（脚本方式）见下方「数据源接入详解」。

---

## 3. 数据源接入详解

让某个数据源能被问数，需要把它的表/列/指标/值「物化」进 meta 表、Qdrant、ES。两种方式：

**A. 通过界面（推荐）**：登录 admin → 数据源管理 → 注册 → 构建。对应接口 `POST /datasources`、`POST /datasources/{id}/build`。

**B. 通过脚本（CLI）**：

```bash
# 1) 读库自动生成元数据草稿(不写任何库)→ conf/meta_config.draft.<id>.json
uv run python -m scripts.generate_draft <datasource_id>

# 2)（可选）手工修订草稿:调整列的维度/度量、是否进 ES、指标定义等

# 3) 物化:写 5 张 meta 表 + Qdrant + ES,只动该 datasource_id,增量安全
uv run python -m scripts.materialize <datasource_id>
```

---

## 4. 生产部署清单 ⚠️

上线前**务必**处理以下默认值，否则有安全风险：

- [ ] **JWT secret**：`auth.secret` 默认是 `wenshu-dev-secret-change-me-in-production`，不改可被伪造 token。在 yaml 的 `auth` 段设一个随机强密钥。
- [ ] **管理员密码**：默认 `admin / admin`，登录后立即改。
- [ ] **数据库密码**：compose 与 yaml 里的 `root/root` 改成强密码（两处保持一致）。
- [ ] **MinIO 凭据**：默认 `minioadmin/minioadmin`，改掉。
- [ ] **CORS**：`main.py` 当前 `allow_origins=["*"]` + `allow_credentials=True`，生产收敛为你的前端域名。
- [ ] **HTTPS / 反代**：前置 nginx/caddy 终止 TLS。
- [ ] **多进程**：当前是单进程 uvicorn；生产建议 `uvicorn main:app --workers N`（或上 gunicorn）。
- [ ] **Langfuse key**（若启用）：改掉 dev 默认 key，与 compose 一致。

---

## 5. 评测（可选）

端到端评估框架（Execution Accuracy / Exact Match / 召回率 / 安全）见 [`evals/README.md`](evals/README.md)。本地跑：

```bash
uv run python -m evals.runner --difficulty easy,medium,hard
```

接 Langfuse 做实验对比：先 `--profile observability` 起服务并在 yaml 启用 langfuse，再按 `evals/README.md` 上传数据集、跑实验。

---

## 6. 故障排查

- 启动报配置错误 → 确认已 `cp app_config.yaml.example app_config.yaml` 并填了 key。
- 问数召回为空 → 该数据源是否已「构建/物化」（见第 3 节）；Qdrant/ES 是否在跑。
- `embedding_size` 与 embedding 模型维度不一致 → Qdrant 写入/检索会报维度错，两者须对齐。
- 上传功能报错 → MinIO 是否启动、`s3` 配置是否正确。
- 看依赖是否就绪：`curl localhost:8000/readyz`。
