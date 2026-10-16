# 企业知识库 RAG 问答助手

面向本地部署和技术面试展示的模块化单体，按阶段实现权限隔离、文档索引、可追溯问答与可复现评测。

**阶段 2：工程骨架。已实现 FastAPI、真实 MySQL/Qdrant 健康检查、迁移基线和 Vue 状态页；尚无登录、上传、检索或问答，无需模型密钥。** 验证结果见[阶段 2 交付记录](docs/stage-2-delivery.md)。阶段 5 完成后才称为 RAG MVP 可用。

## 启动与停止

需要可用的 Docker daemon、Compose 和生成本地配置用的 Python 3.12；容器内工具链已固定。首次构建需要访问 Docker Hub、GHCR、PyPI 和 npm。

根目录执行：

```bash
python3.12 scripts/init_env.py
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
docker compose ps --all
curl -fsS http://127.0.0.1:8080/api/health/live
curl -fsS http://127.0.0.1:8080/api/health/ready
```

访问[服务状态页面](http://127.0.0.1:8080)，每 10 秒读取真实状态，也可手动刷新。`migrate` 成功后 `Exited (0)` 属正常；MySQL、Qdrant、API、Web 应为 healthy。默认仅开放 Web 的 loopback 端口。

配置生成器使用随机密码/API key、权限 0600，拒绝覆盖既有文件。`.env.example` 仅含占位配置；私有配置均被 Git 和 Docker build context 排除。不要重新生成已有数据卷使用的密码；修改 `.env` 不会修改 MySQL 既有账号。

```bash
docker compose run --rm migrate
docker compose down
docker compose up -d --wait --wait-timeout 180
```

以上分别为手动执行迁移、停止并保留命名卷、再次启动。API 不自动迁移，启动顺序为 MySQL 健康 → migrate 成功 → API 就绪 → Web。当前只有 `alembic_version`，没有业务表。备份与恢复操作将在阶段 5 随业务数据增加；当前只验证卷持久化，不宣称已有备份保障。

## 安装、检查与本机开发

Python 固定 3.12，uv 0.12.5；Node 22.22.2、npm 10.9.7。使用 nvm 时先在 frontend 执行 `nvm use`。用 `node -p 'process.version + " " + process.execPath'` 确认真正选中的解释器；本机另有旧 Node，前端开启 engine-strict 防止静默使用不兼容版本。

```bash
cd backend
uv sync --frozen --python python3.12
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
cd ../frontend
npm ci --no-fund --no-audit
npm run typecheck
npm test
npm run build
```

本机调试时，在根目录显式叠加开发配置，只启动数据服务并开放 loopback 端口：

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait mysql qdrant
cd backend
uv run --frozen --env-file ../.env alembic upgrade head
uv run --frozen --env-file ../.env uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

另一个终端执行 `cd frontend`、`nvm use`、`npm run dev`。开发页为 [localhost:5173](http://127.0.0.1:5173)，Vite 与生产 Nginx 均同源代理健康路径及 `/api/v1`。API 的 [OpenAPI](http://127.0.0.1:8000/openapi.json) 和 [Swagger](http://127.0.0.1:8000/docs) 在本机 API 启动后访问；提交的 [OpenAPI 快照](docs/openapi.json) 只声明两个已实现的健康路由。

## 独立真实服务验收

根目录执行：

```bash
python3.12 scripts/verify_stage2.py
```

脚本生成 `.env.stage2-test`，强制使用 `chat-rag-stage2-test` 项目、`*_test` 数据库、18080 Web 端口和独立命名卷。它构建运行/测试镜像，执行单元与真实服务集成测试，验证空库迁移、重复迁移、Qdrant 鉴权、页面/代理/OpenAPI、依赖故障恢复、重建容器后持久化，以及日志/静态资源中的后端密钥泄漏。

脚本暂时停止并恢复测试项目的数据服务；结束时清理专用临时表/collection，保留测试卷。测试向量仅验证存储协议，不代表真实 Embedding。脱敏命令记录在被忽略的 `artifacts/stage2-verification.log`。停止测试实例：

```bash
docker compose --env-file .env.stage2-test -p chat-rag-stage2-test down
```

默认 `pytest` 仅执行 unit，显式排除 integration/live；阶段 2 无 live 模型测试。集成测试通过上面的独立脚本执行，必须显式允许专用测试数据库，不能在业务库运行。

## 配置与健康检查

| 配置 | 用途 |
| --- | --- |
| `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` | 容器初始化；应用使用独立账号，root 仅用于初始化 |
| `DATABASE_URL` | 本机 mysql+asyncmy 连接串；需要账号密码、库名和 charset=utf8mb4 |
| `QDRANT_URL` / `QDRANT_API_KEY` | 后端地址及至少 32 字符的密钥 |
| `HEALTH_TIMEOUT_SECONDS` | 单项探测总超时，默认 3 秒，允许 0.1–10 |
| `WEB_PORT` / `MYSQL_PORT` / `QDRANT_PORT` | 默认 8080/3307/6333；后两项仅开发配置开放 |
| `COMPOSE_PROJECT_NAME` | 默认 chat-rag，隔离容器和卷 |

Compose 使用根据 MYSQL 配置生成的内部 `mysql:3306` 连接串与 `http://qdrant:6333`。应用只从环境读取配置，本机使用 uv 的 `--env-file` 显式注入。缺配置时仅报告字段名和错误类型。AUTH_SECRET、Embedding/LLM 配置到对应阶段才引入。

`/health/live` 只检查进程，返回 200。`/health/ready` 检查 MySQL 查询、数据库 revision 与代码 head 一致、Qdrant 带 API key 的 `/collections`；任一失败返回 503，版本不一致显示 pending。报告无凭据/内部 URL、不调用模型；`rag_configured` 与 `rag_available` 均为 false。Web 外部路径增加 `/api` 前缀。

API JSON 日志只记录 request_id、方法、路由模板、状态、耗时和异常类型，不记录 query、body、Authorization 或异常原文。当前安全异常日志不保存堆栈；后续若引入堆栈诊断，须先落实可靠脱敏。

## 文档与下一阶段

- [架构与边界](docs/architecture.md)
- [数据模型](docs/data-model.md)
- [接口契约](docs/api-contract.md) / [实际 OpenAPI](docs/openapi.json)
- [逐阶段计划与验收](docs/development-plan.md)
- [测试与评测](docs/testing-and-evaluation.md)
- [环境与依赖](docs/environment-and-dependencies.md)
- [阶段 2 交付记录](docs/stage-2-delivery.md)
- [协作规则](AGENTS.md)

阶段 3 须单独授权后开展：用户与知识库成员迁移、管理 CLI、Argon2/JWT、即时权限校验及知识库/成员页面；验收重点是两个真实账号的隔离、角色边界和真实 MySQL 下最后 owner 的并发约束。
