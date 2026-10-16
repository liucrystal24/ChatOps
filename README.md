# 企业知识库 RAG 问答助手

面向本地部署和技术展示的模块化单体，按阶段实现权限隔离、文档索引、可追溯问答与可复现评测。

**阶段5：真实多轮问答及业务质量修复已实现，独立人工验收待完成。** 已接入云端BAAI/bge-m3、Qwen/Qwen3-8B、SSE、权限复核与引用。最新业务36题回归Agent判断36/36正确；完整原始/修复后结果、双口径召回差异及能力边界见[业务修复交付](docs/stage-5-business-fix-delivery.md)。工程测试通过不代替真人正确率/事实支持率验收，暂不标记MVP整体完成。

2026-10-16新增5篇原创虚构业务文章与36题压力测试：Agent逐题判断32题正确、4题错误，暴露普通表格、套餐/角色/属性错配等阶段5待办；原文一致与引用可访问仍不能保证答对。见[业务集审核与解决方案](docs/business-v1-quality-review.md)、[36题实际回答表](docs/business-v1-test-cases.md)。该合成测试不是独立真人或真实客户资料验收。

上述4题已完成补充工程修复：业务36题回归Agent判断36/36正确，证据35/35，6道无答案全部拒答；后端175项、前端18项通过。新输入20条固定上下文断言通过。原40题v2证据仍41/41，v1指定来源口径降至21/22（s14使用已预先标注的API等价来源，原因单列）。完整取舍、前后回答及仍待真人验收的边界见[阶段5业务修复交付](docs/stage-5-business-fix-delivery.md)和[修复后36题表](docs/business-v1-fixed-test-cases.md)。

## 启动与升级

需要 Docker Desktop/Compose 和生成配置用的 Python。首次构建访问 Docker Hub、GHCR、PyPI、npm 和 Hugging Face；只下载合计约 28 MB 的两个固定分词器，不下载模型权重、无需显卡。

在项目根目录执行：

```bash
# 仅新安装生成；已有 .env 不要覆盖
python3 scripts/init_env.py
# 旧版配置只补齐缺失的认证配置
python3 scripts/init_env.py --upgrade
```

在已有 `.env` 末尾配置 `SILICONFLOW_API_KEY=你的真实Key`，不提交密钥。选择非 Pro `BAAI/bge-m3` 和 `Qwen/Qwen3-8B`，共用已有 Key；模型标识不能任意替换。两者均使用云端 API，不需要本地部署模型。索引和问答需要联网，获授权的文件片段、问题与必要追问历史会发送给供应商。当前价格及账号可用性以供应商控制台为准。

```bash
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
docker compose ps --all
curl -fsS http://127.0.0.1:8080/api/health/live
curl -fsS http://127.0.0.1:8080/api/health/ready
```

访问[知识库工作空间](http://127.0.0.1:8080)。没有默认账号，先按下节创建用户。MySQL、Qdrant、API、Web 应 healthy，Worker 应 running，migrate 成功退出 0 属正常。Worker 尚无独立容器健康探针，任务租约与状态负责暴露恢复情况。

缺 Embedding Key 不影响基础服务 ready，但索引任务明确报 `MODEL_NOT_CONFIGURED`，不会产生假向量。补好 Key 后重建服务环境，再手动重试失败任务：

```bash
docker compose up -d --force-recreate api worker
```

本次迁移到 `0003_document_jobs`：在原用户/知识库表基础上新增 documents、jobs、document_index_versions、chunks 及复合外键。升级保留账号、成员及命名卷；不要重新生成已有数据卷使用的数据库密码。

```bash
docker compose run --rm migrate
docker compose down
docker compose up -d --wait --wait-timeout 180
```

以上分别为手动迁移、停止并保留卷、再次启动。MySQL、Qdrant、原文件各有命名卷；不要使用 `down -v` 删除数据。卷持久化及阶段 5 冷备份/恢复已验证，具体步骤见 [备份与恢复](docs/backup-and-restore.md)。

## 登录与权限

在 Mac 终端、项目根目录执行，密码交互输入两次，不回显：

```bash
docker compose exec api python -m app.cli users create alice --admin
docker compose exec api python -m app.cli users create bob
docker compose exec api python -m app.cli users list
docker compose exec api python -m app.cli users disable bob
docker compose exec api python -m app.cli users enable bob
```

密码 12–256 字符；用户名规范化后为 1–64 位字母、数字、下划线、点或连字符，首位字母/数字。自动化仅使用显式 `--password-stdin`，不把密码写进命令或终端历史。CLI 是可信本机管理入口，不是 HTTP 管理后台；实例管理员没有隐含知识库读取权。

创建知识库自动成为 owner。owner 管知识库和成员，editor 管文档，viewer 可读文档/来源；owner 用 `users list` 的 UUID 添加成员。停用/撤权对后续请求生效，不能移除、降级或停用最后一个有效 owner。空库删除要求没有未删除文档、活动任务，而且已删除文件均清理成功；清理失败先重试。

JWT 默认 30 分钟，仅存浏览器内存，刷新需重新登录；无 refresh token 或服务端逐个 token 注销。重新启用用户后，未过期的旧 token 可再次使用。登录限流为单 API 进程每用户名 10 次/60 秒、每直接来源 100 次/60 秒；代理来源共享配额，重启重置。

## 上传、索引和来源

进入知识库，选择 TXT、MD 或文字 PDF，可附最多 10 个标签。单文件 10 MiB，每库最多 200 份未删除文档/500 MiB，文件卷默认上限 2 GiB；PDF 最多 100 页、提取正文最多 200 万字符、索引最多 5000 个片段。超过限制明确失败，不静默截断。扫描 PDF 不做 OCR。

上传成功只代表文件和排队任务已持久化。页面每 2 秒轮询所见活动任务，终态停止；“可检索”和“最新任务状态”分别展示。默认按段落/列表项分开主题，再对长段落按 800 字符、目标重叠 120 字符递归切分；真实 BGE tokenizer 另算 token，不把字数等同 token。已有文档需在页面手动重建索引才采用新切分，失败保留旧有效版本。

- “查看来源”读取当前有效版本正文、PDF 页码或字符位置；“下载原文件”也重新鉴权。
- “重建索引”处理原文件，新版本完整发布前保留旧版本；失败可继续检索旧版。
- 修改展示名称不改内部路径/格式，修改标签会提交新索引任务；同步期间可能少召回，不能返回不符合当前标签的内容。
- “重试任务”创建新任务并保留失败历史；有后继时按提示查看新任务，不能重复重试祖先。
- 删除立即撤销文档的检索资格；文件、向量后台清理，失败可重试。物理残留不代表还可读取。

Dense 检索目前通过同一应用服务及独立 live 验收入口调用，没有临时检索 HTTP 路由或聊天框。支持 Top-K 1–20，以及文档/类型/标签交集过滤；所有 Qdrant 分支强制知识库与 MySQL 有效版本范围，返回正文前再复核授权。模型供应商没有提供权重修订快照，不能保证检测同维度的静默权重变化。

## 本机开发与检查

Python 3.12、uv 0.12.5；Node 22.22.2、npm 10.9.7。frontend 内用 `nvm use`，再确认 `node -p 'process.version + " " + process.execPath'`。依赖有冻结锁文件。

```bash
python3 scripts/fetch_tokenizer.py
cd backend
uv sync --frozen --python 3.12
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
cd ../frontend
npm ci --no-fund --no-audit
npm run typecheck
npm test
npm run build
```

本机开发显式开放数据服务 loopback 端口：

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --wait mysql qdrant
cd backend
uv run --frozen --env-file ../.env alembic upgrade head
uv run --frozen --env-file ../.env uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

另一个终端在 backend 执行 `uv run --frozen --env-file ../.env python -m app.worker`，API/Worker 必须使用同一个 `FILES_DIR`；默认均为 backend/.local-files。frontend 执行 `nvm use`、`npm run dev`，访问 [localhost:5173](http://127.0.0.1:5173)。不要同时让本机 Worker 和 Compose Worker 操作不同的原文件目录。

生产只开放 Web loopback；Navicat 本机连接需叠加开发配置启动 mysql，使用 127.0.0.1:3307、`.env` 的 MYSQL_USER/MYSQL_PASSWORD、MYSQL_DATABASE。实际表名是 `knowledge_base_members`。

[实际 OpenAPI 快照](docs/openapi.json) 只含已经实现的健康、身份、知识库、文档、任务、来源、会话和问答流接口。宿主 API 启动后访问 [OpenAPI](http://127.0.0.1:8000/openapi.json)、[Swagger](http://127.0.0.1:8000/docs)；Web 不代理这些元数据入口。

## 独立真实服务验收

```bash
python3 scripts/verify_stage5.py
```

仅复查已构建服务的网络、故障恢复与持久化，可执行 `python3 scripts/verify_stage5.py --services-only`；该模式不构建或运行 pytest。本轮测试与服务检查分别完成，精确执行记录见交付文档。

使用专用 `chat-rag-stage5-test` 项目、`*_test` 数据库、18083 Web 端口和独立数据卷，构建运行/测试镜像并执行 unit、真实 MySQL/Qdrant integration 及真实 Embedding/LLM live。Key 从进程环境或本地 `.env` 读取，不复制到测试配置文件、不打印；其他测试账号/JWT/密码仅用于本次测试，日志脱敏。

验收包括迁移回退/升级、权限/配额、真实 Worker 进程终止后的租约恢复、迟到发布、有限重试、真实中文与 PDF 页码检索、重复 upsert、撤权/删除残留过滤、原文件共享卷、数据服务故障和重建持久化。只清理本次 UUID/collection，不清空业务库；迁移回退前要求专用库没有业务记录，否则拒绝。

默认 `pytest` 仅 unit，integration/live 必须显式开启；测试向量只出现在明确的测试替身中，live 使用真实模型。详细执行结果、未验证项在交付记录中，未运行项不算通过。停止测试项目并保留卷：

```bash
docker compose --env-file .env.stage5-test -p chat-rag-stage5-test down
```

## 配置与健康

| 配置 | 用途 |
| --- | --- |
| MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD / MYSQL_ROOT_PASSWORD | MySQL 初始化；业务使用独立账号 |
| DATABASE_URL | 本机 mysql+asyncmy 连接串，charset=utf8mb4 |
| QDRANT_URL / QDRANT_API_KEY | 向量库地址及至少 32 字符的密钥 |
| CHUNK_STRUCTURE / CHUNK_SIZE / CHUNK_OVERLAP | 默认 paragraph-v2 / 800 / 120；recursive-v1 用于旧切分对照 |
| EMBEDDING_TITLE_CONTEXT | 默认 false；可选标题前缀只参与向量输入，不改原文引用 |
| AUTH_SECRET / AUTH_TOKEN_TTL_SECONDS | 至少 32 字符的签名密钥；默认 1800 秒 |
| SILICONFLOW_API_KEY | BAAI/bge-m3 和 Qwen3-8B 共用云端凭据，只在服务端 |
| EMBEDDING_BATCH_SIZE / EMBEDDING_TIMEOUT_SECONDS | 每批默认 8 条（1–32），单次默认 30 秒（1–60） |
| CHAT_MODEL / CHAT_OUTPUT_TOKENS / CHAT_DEADLINE_SECONDS | 固定 Qwen/Qwen3-8B；输出默认1024 token，总问答默认120秒 |
| FILES_DIR | API/Worker 共同文件目录，Compose 固定 /data/files |
| HEALTH_TIMEOUT_SECONDS | 基础依赖探测默认 3 秒 |
| WEB_PORT / MYSQL_PORT / QDRANT_PORT | 默认 8080/3307/6333；后两者仅开发配置开放 |
| COMPOSE_PROJECT_NAME | 默认 chat-rag，隔离容器和卷 |

保护上限还可在后端环境中调低：UPLOAD_MAX_BYTES、KB_DOCUMENT_LIMIT、KB_MAX_BYTES、FILES_MAX_BYTES；解析/任务配置见 `Settings`。Compose 的常用环境转发范围在 compose.yaml 中明确，其余需要同时更新 Compose 才会生效。租约默认 90 秒/心跳 20 秒，总任务 15 分钟，单次解析 60 秒，自动最多尝试 3 次，瞬时故障退避重试；永久格式/模型错误直接失败。

`/health/live` 只检查进程；`/health/ready` 检查 MySQL、Qdrant 和实际迁移版本，不消耗模型额度。`rag_configured` 表示 Key 已配置，`rag_available` 表示配置和基础依赖就绪；健康检查不证明供应商实时连通，实际调用失败会在问答中显示。API/Worker 日志不记录正文、密码、token、供应商原始响应或内部 SQL；本地 `.env` 和运行产物均被忽略。

## 文档与下一阶段

面试准备可先读[RAG优化图文与代码讲解](docs/interview-rag-optimization-guide.md)，再读[一分钟口述与追问卡](docs/interview-rag-optimization-pitch.md)。内容按实际错题、代码修改及同口径评测整理。

- [架构与边界](docs/architecture.md)
- [数据模型](docs/data-model.md)
- [接口契约](docs/api-contract.md) / [实际 OpenAPI](docs/openapi.json)
- [逐阶段计划与验收](docs/development-plan.md)
- [测试与评测](docs/testing-and-evaluation.md)
- [环境与依赖](docs/environment-and-dependencies.md)
- [阶段 2 交付记录](docs/stage-2-delivery.md)
- [阶段 3 交付记录](docs/stage-3-delivery.md)
- [阶段 4 交付记录](docs/stage-4-delivery.md)
- [阶段 5 交付记录](docs/stage-5-delivery.md) / [召回改进记录](docs/stage-5-improvement-delivery.md)
- [备份与恢复](docs/backup-and-restore.md)
- [公开评测与人工复核](evaluation/README.md)
- [协作规则](AGENTS.md)

阶段 5 已获授权并实现真实流式问答；当前继续修正召回与验证事实支持。工程功能可运行不等于人工质量验收完成。


## 问答演示

登录后选择知识库，上传公开或获授权的 TXT/Markdown/文字 PDF，等待文档显示“可检索”。在“知识库问答”输入问题，再追问“那地点在哪里？”；流中内容标为草稿，保存后显示最终引用，点击引用重新鉴权并查看原始片段。没有依据时显示固定拒答。可新建/切换会话、刷新历史、停止生成；浏览器断线会核验消息状态，不自动重发问题。生成中的同一会话不接受第二个请求。

固定公开题集运行方式见 evaluation/README.md；真实报告在忽略提交的 evaluation/outputs。初版标注由 Agent 起草，不能冒充用户人工质量验收。阶段5质量门槛通过前不进入阶段6的 Hybrid/Rerank 实现。

本机已为 Navicat 开放 3307 时，升级也应叠加相同开发配置以保留端口：`docker compose -f compose.yaml -f compose.dev.yaml up --build -d --wait`。本轮更新现有实例采用先运行 migrate，再仅重建 API/Worker/Web，保留原 MySQL/Qdrant 容器及数据卷。
