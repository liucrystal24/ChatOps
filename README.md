# 企业知识库 RAG 问答助手

面向本地部署和技术面试展示的模块化单体，按阶段实现权限隔离、文档索引、可追溯问答与可复现评测。

**阶段 3：身份与知识库隔离。已实现真实用户管理、Argon2/JWT 登录、知识库与成员权限管理；文档上传、索引和问答尚未接入，无需模型密钥。** 验证记录见[阶段 3 交付记录](docs/stage-3-delivery.md)，上一阶段结果保留在[阶段 2 交付记录](docs/stage-2-delivery.md)。阶段 5 完成后才称为 RAG MVP 可用。

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

访问[知识库工作空间](http://127.0.0.1:8080)。首次部署先按下节创建用户，再登录；没有默认账号。导航中的“服务状态”每 10 秒读取真实状态，也可手动刷新。`migrate` 成功后 `Exited (0)` 属正常；MySQL、Qdrant、API、Web 应为 healthy。默认仅开放 Web 的 loopback 端口。

配置生成器使用随机密码/API key、权限 0600，拒绝覆盖既有文件。`.env.example` 仅含占位配置；私有配置均被 Git 和 Docker build context 排除。不要重新生成已有数据卷使用的密码；修改 `.env` 不会修改 MySQL 既有账号。

```bash
docker compose run --rm migrate
docker compose down
docker compose up -d --wait --wait-timeout 180
```

以上分别为手动执行迁移、停止并保留命名卷、再次启动。API 不自动迁移，启动顺序为 MySQL 健康 → migrate 成功 → API 就绪 → Web。当前迁移到 `0002_identity_knowledge_bases`，包含 users、knowledge_bases、knowledge_base_members 和授权短事务的单行锁；尚无文档/任务/会话表。备份与恢复操作将在阶段 5 随业务数据增加；当前只验证卷持久化，不宣称已有备份保障。

## 阶段 2 升级与用户管理

已有 `.env` 不应重新生成，先补齐认证配置再构建：

```bash
python3.12 scripts/init_env.py --upgrade
docker compose up --build -d --wait --wait-timeout 180
```

升级只补充缺失的 AUTH_SECRET/有效期，不修改数据库密码或其他已有值。新安装仍使用上方的 `init_env.py`。迁移会保留原有数据卷。

可信本机管理员在终端运行以下命令（密码交互输入两次，不回显，不作为参数）：

```bash
docker compose exec api python -m app.cli users create alice --admin
docker compose exec api python -m app.cli users create bob
docker compose exec api python -m app.cli users list
docker compose exec api python -m app.cli users disable bob
docker compose exec api python -m app.cli users enable bob
```

自动化创建可用 `--password-stdin`，仅从受保护输入传入，不把密码写进命令、脚本或终端历史。密码 12–256 字符；用户名 NFKC/casefold 后为 1–64 位字母、数字、下划线、点或连字符，首位字母/数字。`users list` 提供 UUID，owner 用 UUID 添加成员。CLI 是可信本机操作，不是登录后的 HTTP 管理接口；`is_admin` 不会赋予所有知识库的读取权。

创建知识库后自动成为 owner；owner 修改库和成员，editor/viewer 可读详情，文档操作在阶段 4 才开放。停用用户和移除成员对后续请求立即生效；会留下最后一个有效 owner 的操作返回 409。停用失败需先向其他有效用户移交 owner。删除库为逻辑删除；阶段 3 尚无文档，所有库都是空库。

JWT 默认 30 分钟，token 只在浏览器内存中，刷新重新登录、退出清除；没有 refresh token 或服务端单 token 注销。启用账号后尚未过期的旧 token 可再次使用。登录限流为单 API 进程每用户名 10 次/60 秒、每直接来源 100 次/60 秒；忽略转发头，代理来源共享配额，重启重置，不支持多进程分布式限流。

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

另一个终端执行 `cd frontend`、`nvm use`、`npm run dev`。开发页为 [localhost:5173](http://127.0.0.1:5173)，Vite 与生产 Nginx 均同源代理健康路径及 `/api/v1`。API 的 [OpenAPI](http://127.0.0.1:8000/openapi.json) 和 [Swagger](http://127.0.0.1:8000/docs) 在本机 API 启动后访问；提交的 [OpenAPI 快照](docs/openapi.json) 声明健康、身份、知识库和成员的已实现路由，不包含上传或问答。

## 独立真实服务验收

根目录执行：

```bash
python3.12 scripts/verify_stage3.py
```

脚本生成 `.env.stage3-test`，强制使用 `chat-rag-stage3-test` 项目、`*_test` 数据库、18081 Web 端口和独立命名卷。它构建运行/测试镜像，执行单元与真实服务集成测试，验证迁移回退/升级、Qdrant 鉴权、真实 CLI/Web HTTP 双账号隔离、角色权限、撤权与停用，以及真实 MySQL owner 并发与锁等待；同时检查页面/代理/OpenAPI、依赖故障恢复、重建容器后持久化和密钥泄漏。测试只清理自己创建的 UUID；迁移回退前要求专用库没有用户/知识库/成员记录，否则拒绝执行。

脚本暂时停止并恢复测试项目的数据服务；结束时清理专用临时表/collection，保留测试卷。测试向量仅验证存储协议，不代表真实 Embedding。脱敏命令记录在被忽略的 `artifacts/stage3-verification.log`。停止测试实例：

```bash
docker compose --env-file .env.stage3-test -p chat-rag-stage3-test down
```

默认 `pytest` 仅执行 unit，显式排除 integration/live；当前无 live 模型测试。集成测试通过上面的独立脚本执行，必须显式允许专用测试数据库，不能在业务库运行。

## 配置与健康检查

| 配置 | 用途 |
| --- | --- |
| `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` | 容器初始化；应用使用独立账号，root 仅用于初始化 |
| `DATABASE_URL` | 本机 mysql+asyncmy 连接串；需要账号密码、库名和 charset=utf8mb4 |
| `QDRANT_URL` / `QDRANT_API_KEY` | 后端地址及至少 32 字符的密钥 |
| `AUTH_SECRET` / `AUTH_TOKEN_TTL_SECONDS` | 至少 32 字符的签名密钥；有效期默认 1800 秒，允许 300–7200 秒 |
| `HEALTH_TIMEOUT_SECONDS` | 单项探测总超时，默认 3 秒，允许 0.1–10 |
| `WEB_PORT` / `MYSQL_PORT` / `QDRANT_PORT` | 默认 8080/3307/6333；后两项仅开发配置开放 |
| `COMPOSE_PROJECT_NAME` | 默认 chat-rag，隔离容器和卷 |

Compose 使用根据 MYSQL 配置生成的内部 `mysql:3306` 连接串与 `http://qdrant:6333`。应用只从环境读取配置，本机使用 uv 的 `--env-file` 显式注入。缺配置时仅报告字段名和错误类型。AUTH_SECRET 从本阶段必填；Embedding/LLM 配置留到对应阶段。

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
- [阶段 3 交付记录](docs/stage-3-delivery.md)
- [协作规则](AGENTS.md)

阶段 4 须单独授权后开展：先确认真实 Embedding 供应商、模型限制和凭据环境变量，再分增量完成上传、Worker 租约/重试、解析切分、真实向量索引与权限内检索。接入文档前必须给删除知识库事务补充非空检查；后续验收包括越权零泄漏、迟到 Worker 不能发布、重建失败保留旧版本。
