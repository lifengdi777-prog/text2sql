# 问数 Wenshu

自然语言问数（Text2SQL）平台：中文提问 → 生成 SQL → 在你的数据库执行 → 给出结果、图表与结论。也支持上传 Excel 直接问答。

后端 FastAPI + LangGraph，前端 Vue。

---

## 快速开始（Docker）

> 准备：Docker；一个大模型 API key（DeepSeek 或 OpenAI 兼容）；一个 embedding key（DashScope 或 OpenAI 兼容）。

```bash
# 1. 复制配置模板,然后编辑 conf/app_config.yaml,填好:
#    - llm.api_key、embedding_fallback.api_key   (必填:大模型 / embedding 的 key)
#    - auth.admin_password                        (管理员密码;留空则随机生成,见第 3 步)
cp conf/app_config.yaml.example conf/app_config.yaml

# 2. 一键起全栈(基础设施 + 后端 + 前端)
docker compose -f docker/docker-compose.yaml up -d --build

# 3. 若上面 admin_password 留空,从日志取随机生成的初始密码
docker compose -f docker/docker-compose.yaml logs wenshu-backend | grep 初始密码
```

4. 打开 **http://localhost:8080**，用 `admin` + 上一步的密码登录。
5. 进「数据源管理」→ 注册业务库 → 点「构建」，完成后即可开始问数。

> **想先用示例数据体验**：导入自带演示库后，注册数据源时连这个 `dw` 库即可。
> ```bash
> docker exec -i mysql mysql -uroot -proot < docker/mysql/dw.sql
> ```

---

## ⚠️ Docker 下注册数据源：地址别用 `localhost`

后端在容器里，`localhost` 指向容器自己。注册业务库时地址要填**容器可达**的：

- compose 自带的 MySQL → 主机 `mysql`、端口 `3306`（容器内端口，**不是**宿主映射的 3307）
- 宿主上的其它库 → `host.docker.internal`（Docker Desktop）或宿主局域网 IP
- 远程库 → 直接填它的真实地址

（启用 Langfuse 观测时，另需在 compose 的 `wenshu-backend` 打开 `WENSHU_LANGFUSE_HOST: http://langfuse-web:3000`。）

---

## 技术栈与端口

前端 `8080` · 后端 `8000` · MySQL `3307` · Qdrant `6333` · Elasticsearch `9200` · MinIO `9000/9001` · Langfuse `3000`（可选）。

所有依赖都在 `docker/docker-compose.yaml`。MySQL 里：`meta`（元数据）、`wenshu`（用户/会话）、以及你的业务库。

---

## 数据源接入原理

让一个库能被问数，需把它的表/列/指标/值「物化」进 meta + Qdrant + ES。两种方式：

- **界面（推荐）**：数据源管理 → 注册 → 构建（自动完成）。
- **CLI**：`uv run python -m scripts.generate_draft <id>` → 检查草稿 → `uv run python -m scripts.materialize <id>`。

---

## 裸机运行（不用 Docker 起应用）

```bash
uv sync && uv run python main.py                    # 后端 :8000
cd wenshu-frontend && npm install && npm run dev    # 前端
```

配置里 host 默认 `localhost`，连 `docker compose up -d` 起的基础设施。首启自动建库建表、创建管理员。

---

## 上生产前必改 ⚠️

仍用默认值时后端启动会告警提醒，但不阻止运行：

- **`auth.secret`**：设随机强密钥 —— `python -c "import secrets;print(secrets.token_urlsafe(48))"`
- **管理员密码**：在 `auth.admin_password` 设置（留空则随机生成，见启动日志）
- **DB / MinIO 默认口令**：`root/root`、`minioadmin/minioadmin` 全部换掉
- **`cors.allow_origins`**：填你的前端域名（默认 `*`）
- **HTTPS**：前置 nginx/caddy 终止 TLS；高并发给后端加 `--workers`

---

## 评测 & 故障排查

- 评测框架见 [`evals/README.md`](evals/README.md)。
- **起不来** → 确认填了 2 个 key；`curl localhost:8000/readyz` 看依赖。
- **召回为空** → 数据源是否已「构建」；Qdrant / ES 是否在跑。
- **Qdrant 维度报错** → `qdrant.embedding_size` 要与 embedding 模型输出维度一致。
