# 问数 Wenshu

自然语言问数（Text2SQL）平台：中文提问 → 生成 SQL → 在你的数据库执行 → 给出结果、图表与结论。也支持上传 Excel 直接问答。

后端 FastAPI + LangGraph，前端 Vue。

---

## 快速开始

> 准备：Docker（只用来起基础设施）、[uv](https://docs.astral.sh/uv/)、Python ≥ 3.13、Node.js；
> 以及一个大模型 API key（DeepSeek 或 OpenAI 兼容）、一个 embedding key（DashScope 或 OpenAI 兼容）。

```bash
# 1. 起基础设施(mysql / elasticsearch / qdrant / minio)
docker compose -f docker/docker-compose.yaml up -d

# 2. 复制配置模板,然后编辑 conf/app_config.yaml,填好:
#    - llm.api_key、embedding_fallback.api_key   (必填:大模型 / embedding 的 key)
#    - auth.admin_password                        (管理员密码;留空则随机生成,见启动日志)
cp conf/app_config.yaml.example conf/app_config.yaml

# 3. 起后端(:8000)。首启自动建库建表、创建管理员 admin
uv sync
uv run python main.py

# 4. 起前端(另开一个终端,默认 http://localhost:5173)
cd wenshu-frontend && npm install && npm run dev
```

5. 打开 **http://localhost:5173**，用 `admin` + 你设的密码登录（留空的话看后端启动日志里打印的随机密码）。
6. 进「数据源管理」→ 注册业务库（host/port/账号/库名）→ 点「构建」，完成后即可开始问数。

> **想先用示例数据体验**：仓库自带演示库，手动导入后，第 6 步注册数据源时连这个 `dw` 库即可。
> ```bash
> docker exec -i mysql mysql -uroot -proot < docker/mysql/dw.sql
> ```

---

## 技术栈与端口

后端 `8000` · 前端开发服务器 `5173` · MySQL `3307` · Qdrant `6333` · Elasticsearch `9200` · MinIO `9000/9001` · Langfuse `3000`（可选）。

基础设施都在 `docker/docker-compose.yaml`。MySQL 里：`meta`（元数据）、`wenshu`（用户/会话），以及你自己的业务库。

---

## 数据源接入原理

让一个库能被问数，需把它的表/列/指标/值「物化」进 meta + Qdrant + ES。两种方式：

- **界面（推荐）**：数据源管理 → 注册 → 构建（自动完成）。
- **CLI**：`uv run python -m scripts.generate_draft <id>` → 检查草稿 → `uv run python -m scripts.materialize <id>`。

---

## 上生产前必改 ⚠️

仍用默认值时后端启动会告警提醒，但不阻止运行：

- **`auth.secret`**：设随机强密钥 —— `python -c "import secrets;print(secrets.token_urlsafe(48))"`
- **管理员密码**：在 `auth.admin_password` 设置（留空则随机生成，见启动日志）
- **DB / MinIO 默认口令**：`root/root`、`minioadmin/minioadmin` 全部换掉
- **`cors.allow_origins`**：填你的前端域名（默认 `*`）
- **前端**：生产用 `npm run build` 出静态包（`dist/`），交给 nginx 等托管
- **HTTPS / 并发**：前置 nginx 终止 TLS；并发高用 `uvicorn main:app --workers N`

---

## 评测 & 故障排查

- 评测框架见 [`evals/README.md`](evals/README.md)。
- **起不来** → 确认填了 2 个 key；`curl localhost:8000/readyz` 看依赖是否就绪。
- **召回为空** → 数据源是否已「构建」；Qdrant / ES 是否在跑。
- **Qdrant 维度报错** → `qdrant.embedding_size` 要与 embedding 模型输出维度一致。
