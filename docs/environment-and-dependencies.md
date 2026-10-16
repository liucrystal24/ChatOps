# 环境检查与依赖核对

检查日期：2026-10-15。此记录是第一阶段的观察，不等于项目已经安装或运行成功。

## 1. 项目目录

- 工作目录：`/Users/chrisliu/Desktop/chat-rag`。
- `ls -la` 与 `rg --files --hidden` 确认目录为空，没有现有源码、配置或依赖文件。
- `git status --short --branch` 返回“not a git repository”。本阶段未初始化 Git。
- 逐级检查当前目录及父目录，未发现适用的既有 `AGENTS.md`。

## 2. 本机工具

| 检查 | 实际结果 | 对开发的影响 |
| --- | --- | --- |
| 系统 | Darwin x86_64 | 容器及原生依赖需支持 amd64 |
| 默认 Python | 3.14.3 | 不使用默认解释器创建项目环境 |
| `python3.12` | 3.12.14 | 作为项目基线，显式创建隔离环境 |
| `python3.13` | 3.13.3 | 已安装，暂不作为项目基线 |
| Node | 22.22.2 | 满足本次核对的 Vue/Vite Node 要求 |
| npm / pnpm | 10.9.7 / 10.18.2 | 默认选 npm，避免维护两份前端锁文件 |
| uv | 0.12.5 | 后续用于 Python 依赖解析、锁定和运行 |
| Git | 2.39.2 | 工具可用，仓库尚未初始化 |
| Docker CLI | 28.3.2，API 1.51 | 客户端已安装 |
| Compose | v2.38.2-desktop.1 | 插件已安装 |
| Docker daemon | 无法连接 desktop-linux socket | 阻碍后续容器启动，当前文档阶段不受影响 |
| MySQL CLI | 9.3.0 | 仅证明客户端安装，不能证明服务运行；项目用容器内 8.4 |
| Qdrant CLI | 未发现命令 | 通过容器提供服务，不要求安装本机 CLI |
| 工作目录所在磁盘 | 约 79.0 GiB 可用 | 未进行 CPU、内存或容器资源压力测试 |

未读取密钥或私有配置。未安装依赖、拉取镜像、连接业务数据库或调用模型。Docker 结果仅记录为“无法连接”，不推断具体原因。

## 3. 依赖选择与官方核对

本阶段确定技术路线，**不声称已验证整套依赖兼容性**。以下发布信息是核对快照；下一阶段以干净环境解析、测试和构建后的锁文件为准。

| 组件 | 核对结果与拟采用路线 | 官方依据 |
| --- | --- | --- |
| Vue | 稳定 3.5 系列，发布页可见 3.5.44；不采用 3.6 RC | [发布页](https://github.com/vuejs/core/releases) |
| Vue 工具链 | 官方 Quick Start 要求 Node `^22.18.0 或 >=24.12.0`；本机满足 | [Quick Start](https://vuejs.org/guide/quick-start.html) |
| Vite | 8.3 系列当前接受常规补丁；Node 最低要求 20.19+ / 22.12+ | [支持版本](https://vite.dev/releases)、[安装要求](https://vite.dev/guide/) |
| TypeScript | 发布页显示稳定 7.0.2；须与 Vue 类型工具实际联调后选择 | [发布页](https://github.com/microsoft/TypeScript/releases)、[Vue 类型工具](https://github.com/vuejs/language-tools) |
| Element Plus | 稳定 2.x；优先使用发行 CSS，按需引入复杂度后续再评估 | [安装文档](https://element-plus.org/en-US/guide/installation.html) |
| FastAPI | 发布记录可见 0.143.1（2026-10-14）；拟选当前稳定补丁 | [Release Notes](https://fastapi.tiangolo.com/release-notes/) |
| Pydantic | v2，发布记录可见 2.14.1；配置使用 pydantic-settings | [发布信息](https://pypi.org/project/pydantic/) |
| LangChain | 采用 v1 生态，已核对 1.4.5 发布；仅引入实际需要的组件 | [发布信息](https://pypi.org/project/langchain/1.4.5/)、[安装说明](https://docs.langchain.com/oss/python/langchain/install) |
| MySQL | 选择仍受支持的 8.4 LTS 系列，实际镜像锁定发布补丁 | [LTS 与 Innovation](https://dev.mysql.com/doc/refman/8.4/en/mysql-releases.html) |
| SQLAlchemy | 2.x + asyncmy；驱动、连接池和认证方式一起验证 | [MySQL 方言与 asyncmy](https://docs.sqlalchemy.org/en/20/dialects/mysql.html) |
| Qdrant | 发布页显示稳定 1.19.2；使用匹配的 qdrant-client 稳定版本 | [服务端发布页](https://github.com/qdrant/qdrant/releases) |

TypeScript 7 的发布不自动证明 Vue 类型检查工具已经兼容。若候选组合不能通过安装、类型检查和构建，依据官方兼容说明选仍维护的稳定组合，在此记录理由；不得使用预发布包掩盖不兼容。

后续还需逐阶段核对 Uvicorn、Alembic、asyncmy、JWT/Argon2、python-multipart、PDF 解析器、HTTP 客户端和 pytest 等实际使用组件。不会在文档阶段随意填入一整套未测试的精确版本。

## 4. 已核对的关键 API

- 当前 FastAPI 提供 `fastapi.sse.EventSourceResponse` 和 `ServerSentEvent`，优先使用官方 SSE 功能，避免仅为 SSE 引入额外框架。[官方 SSE 教程](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- LangChain 模型集成采用独立供应商包；RAG 显式使用模型接口、Prompt 和 splitter，避免依赖旧版隐式 Memory/Chain API。[安装说明](https://docs.langchain.com/oss/python/langchain/install)
- `RecursiveCharacterTextSplitter` 的默认长度按字符计算，中文须补充分隔符；字符数不能当成模型 token 数。[切分文档](https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter)
- Qdrant 使用 `query_points` 及 payload filter；过滤字段需要 payload index。Filter 是本项目权限边界的实现手段，应用仍须自行鉴权。[过滤文档](https://qdrant.tech/documentation/search/filtering/)、[隔离示例](https://qdrant.tech/documentation/examples/multitenancy/)
- Hybrid 后续通过 dense/sparse prefetch 和 RRF 融合实现；不能把两个不同量纲的原始分数直接相加。[Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- MySQL `FOR UPDATE SKIP LOCKED` 适合队列表的竞争领取；不将这种不一致视图用于一般业务查询。[锁定读](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)

## 5. 下一阶段的版本验收

1. 显式选择 Python 3.12 和已满足要求的 Node；不改动用户全局默认 Python。
2. Python 生成 `uv.lock`，前端生成 `package-lock.json`，锁定包管理器版本并用冻结模式安装。
3. Compose 镜像固定具体补丁版本，交付时记录 digest；禁止以 `latest` 作为可复现基线。
4. 必要依赖分阶段增加；每次增加都运行相关安装、导入、测试或构建验证。
5. 版本矩阵记录“实际安装版本、来源、验证命令与结果”。安装成功不足以证明 MySQL/Qdrant 或 SSE 的行为兼容。

## 6. 阶段 2 实际选择（2026-10-15）

第一阶段各节保留为历史快照，以下及锁文件是本阶段实际实现依据。Docker Desktop 已启动，daemon 28.3.2 可连接；真实服务结果见 [阶段 2 交付记录](stage-2-delivery.md)。未读取用户已有凭据，项目密钥由生成器创建并保存在忽略的本地配置中。

| 组件 | 实际锁定版本 | 核对与验证依据 |
| --- | --- | --- |
| Python / uv | 3.12.14 / 0.12.5 | Python 3.12 环境、uv.lock、冻结安装；不改变系统默认版本 |
| FastAPI / Pydantic / settings | 0.143.1 / 2.14.1 / 2.16.0 | PyPI 元数据；[lifespan](https://fastapi.tiangolo.com/advanced/events/)、[settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)；单元测试 |
| SQLAlchemy / asyncmy / Alembic | 2.1.4 / 0.2.16 / 1.20.0 | [asyncio](https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html)、[asyncmy](https://docs.sqlalchemy.org/en/21/dialects/mysql.html#asyncmy)、[异步迁移](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)；显式 sqlalchemy[asyncio] 安装 greenlet |
| Uvicorn / HTTPX | 0.54.0 / 0.28.1 | PyPI 稳定版；lifespan、真实 HTTP 健康探测 |
| cryptography | 49.0.0 | MySQL caching_sha2_password 的非 TLS RSA 认证支持；仅本机容器网络使用，非公网部署方案 |
| pytest / pytest-asyncio / Ruff | 9.1.1 / 1.4.0 / 0.17.0 | 标记、测试、lint 与格式检查 |
| httpx2（仅测试） | 2.13.1 | [Starlette TestClient](https://starlette.dev/testclient/) 已推荐 httpx2；避免回退到已弃用的 HTTPX 测试客户端路径，生产 HTTP 探测仍用 HTTPX |
| Node / npm | 22.22.2 / 10.9.7 | .nvmrc、packageManager、engines 与 engine-strict；显式选择正确 PATH |
| Vue / Vite / plugin-vue | 3.5.44 / 8.3.4 / 6.0.10 | npm 注册元数据、[Vite](https://vite.dev/guide/)、[Vue 类型检查](https://vuejs.org/guide/typescript/overview.html) |
| TypeScript / vue-tsc | 6.0.3 / 3.3.13 | TypeScript 7.0.2 实际执行失败：`ERR_PACKAGE_PATH_NOT_EXPORTED: ./lib/tsc`；改用兼容的[稳定 6.0.3](https://github.com/microsoft/TypeScript/releases/tag/v6.0.3) 后 typecheck/build 均通过 |
| Element Plus | 2.14.7 | [组件引入](https://element-plus.org/en-US/guide/quickstart.html)，只引入使用的组件，发行 CSS |
| Vitest / Vue Test Utils / jsdom | 5.0.4 / 2.5.1 / 30.1.2 | 503 报告、网络失败、自动刷新/卸载行为共 3 项测试通过 |
| MySQL / Qdrant / Nginx | 8.4.11 / 1.19.2 / 1.30.6-alpine | Docker 官方仓库精确标签和 digest，见 compose.yaml 与 deploy；不使用 latest |

首次 shell 工具快照显示 Node 22.22.2，但在项目下 `process.execPath` 实际命中过 `/usr/local/bin/node` 22.19.0。安装 jsdom 曾出现 engine warning；后续显式使用 nvm 的 22.22.2，并以 npm ci/typecheck/test/build 重新验证。不将最初 warning 的安装结果当作最终兼容结论。

本机 cryptography 49.0.0 从源码构建成功；容器使用锁文件解析对应 Linux wheel。镜像拉取首次遇到 Docker Hub OAuth 网络超时，重试拉取精确镜像后继续；此网络问题不改变依赖或镜像基线。

阶段 2 未引入 LangChain、Embedding、模型供应商、Qdrant Python SDK 或 Worker。Qdrant 健康使用带 API key 的 `/collections`，容器 healthcheck 使用 `/readyz`；前者同时验证鉴权。[Qdrant 监控](https://qdrant.tech/documentation/operations/monitoring/)与[容器构建文件](https://github.com/qdrant/qdrant/blob/v1.19.2/Dockerfile)已核对。Compose 使用[健康/迁移依赖顺序](https://docs.docker.com/compose/how-tos/startup-order/)，Python 镜像按[uv 官方流程](https://docs.astral.sh/uv/guides/integration/docker/)冻结安装。

## 7. 阶段 3 核对（2026-10-15）

新增并锁定 PyJWT 2.15.1、argon2-cffi 25.1.0（含绑定包，精确传递依赖见 uv.lock），其余生产依赖保持阶段 2 已验证组合。没有新增服务或前端框架。

- 已核对 [FastAPI JWT/密码教程](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)、[PyJWT claims 验证](https://pyjwt.readthedocs.io/en/stable/usage.html)、[Argon2 PasswordHasher](https://argon2-cffi.readthedocs.io/en/stable/howto.html)；选择固定 HS256，校验 issuer/audience 与必要时间/身份 claims，Argon2id 使用该锁定库默认参数，哈希/验证在线程中执行且并发上限 4。
- 已核对 [SQLAlchemy 2.1 事务](https://docs.sqlalchemy.org/en/21/orm/session_transaction.html)。业务使用 READ COMMITTED 和 MySQL 单行 FOR UPDATE 授权变更锁；真实并发测试验证阻塞、有效 owner 计数及锁后重新鉴权。
- MySQL CHECK 错误通过 asyncmy 返回 OperationalError、errno 3819；测试断言真实 errno，不假设一定包装为 IntegrityError。外键、CHECK、迁移均在真实 MySQL 8.4 验证。
- 新增 AUTH_SECRET（至少 32 字符）与 AUTH_TOKEN_TTL_SECONDS（默认 1800，300–7200）。`init_env.py --upgrade` 只追加缺失配置，保留旧值和数据密码。密钥不进入前端，JWT 不放角色和正文。
- 阶段 3 测试项目为 chat-rag-stage3-test，数据库 chat_rag_stage3_test，Web 18081；测试运行日志和临时浏览器凭据被 Git/Docker 忽略，不作为默认账号或服务回退。

实际检查结果与未验证边界见 [stage-3-delivery.md](stage-3-delivery.md)。

## 阶段 4 官方接口核对（2026-10-15）

- [硅基流动 Embeddings](https://api-docs.siliconflow.cn/docs/api/embeddings-post)：POST /v1/embeddings，BAAI/bge-m3 单文本上限 8192 tokens；选免费非 Pro 模型，API Key 来自本地环境。实际探测返回 1024 维，usage 为供应商返回值。
- [BAAI 模型与 tokenizer](https://huggingface.co/BAAI/bge-m3)：固定 revision 5617a9f61b028005a4858fdac845db406aefb181，不下载模型权重。
- [Qdrant 1.19 query points](https://api.qdrant.tech/api-reference/search/query-points)：命名向量 using=dense，filter 强制 server scope；直接 HTTPX，不再引 SDK。
- [FastAPI 上传](https://fastapi.tiangolo.com/tutorial/request-files/)、[pypdf 文本提取](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)：UploadFile 分块读；PDF 受限子进程解析，不支持 OCR。

分块 API 已核对 [RecursiveCharacterTextSplitter](https://reference.langchain.com/python/langchain-text-splitters/character/RecursiveCharacterTextSplitter)，使用当前独立包及 keep_separator=end、add_start_index=True；中文字符长度与真实 tokenizer token_count 分开记录。

官方 Embeddings 文档未列统一批量条数上限；本项目默认每批 8 条、本地参数上限 32，供应商拒绝时明确失败，不以本地上限宣称远端承诺。真实 PDF 两页批量已验证，8192 token 按每个文本限制执行。新增 python-multipart 0.0.32、pypdf 6.20.1、langchain-text-splitters 1.1.3、tokenizers 0.23.3，完整传递依赖在 uv.lock；API/Worker 启动不加载 pypdf/切分框架，只在受限解析子进程中加载。

## 9. 阶段 5 核对（2026-10-15）

- 聊天沿用 SiliconFlow Key，固定非 Pro `Qwen/Qwen3-8B`，关闭 thinking，HTTPX 直接消费 SSE，不新增 SDK/服务框架。官方文档列为免费小模型，当前 Key 的模型列表和真实流请求已成功；价格与账号权限以供应商当时控制台为准。[模型列表](https://api-docs.siliconflow.cn/docs/api/models-get)、[聊天接口](https://api-docs.siliconflow.cn/docs/api/chat-completions-post)、[官方免费模型说明](https://api-docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-rikkahub)。
- Qwen3 tokenizer 固定官方 revision `b968826d9c46dd6066d109eabc6255188de91218`，SHA-256 `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`，约 11.4 MB；加 BGE 约 28 MB，只下载分词器、不下载模型权重。[官方模型文件](https://huggingface.co/Qwen/Qwen3-8B/tree/b968826d9c46dd6066d109eabc6255188de91218)。计数实现对应无工具/无 reasoning 历史/关闭 thinking 的纯文本模板，另留 256 token 服务端模板差异；不是供应商账单计数。模型权重修订仍如实记 provider-managed。
- 使用已锁定 FastAPI 0.143.1 的官方 `EventSourceResponse`/`ServerSentEvent`；已读取当前 routing 源码，响应前校验在普通 dependency 中完成，官方 producer 插入 15 秒 ping。前端 fetch POST 解析 UTF-8、CR/LF 与事件边界。[官方 SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)、[Fetch 流](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch)。
- 新增锁定前端 `markdown-it 15.0.2`、类型 `@types/markdown-it 14.2.0`；禁原始 HTML、禁图片、链接仅 http(s)，加 noreferrer。[当前官方文档](https://markdown-it.github.io/markdown-it/)。其余核心依赖及基础镜像继续使用已验证版本，应用版本升至 0.5.0。
- 配置：CHAT_MODEL 固定；CHAT_CONTEXT_TOKENS 默认/最大 16384、CHAT_OUTPUT_TOKENS 默认1024（128–2048）、CHAT_DEADLINE_SECONDS 默认/最大120、CHAT_READ_TIMEOUT_SECONDS 默认30（1–60），CHAT_TOKENIZER_PATH 默认镜像 assets。Compose 转发常用 model/deadline/output，其他显式配置须同步 Compose。流中每秒授权/来源复核及草稿保存不延长总 deadline。

追问改写另核对 [SiliconFlow JSON mode](https://api-docs.siliconflow.cn/docs/userguide/guides/json-mode)：当前文档称线上语言模型均支持 response_format=json_object。实际 Qwen3-8B JSON 流和两轮改写已通过；仍验证 question 字段/问号/禁止历史引用，格式错误显式回退，不把 JSON 合法性当成语义正确。


阶段 5 召回修正（2026-10-15）：未新增依赖，锁文件不变。CHUNK_STRUCTURE 默认 paragraph-v2（可选 recursive-v1）、CHUNK_SIZE 默认800、CHUNK_OVERLAP 默认120；EMBEDDING_TITLE_CONTEXT 默认false。API/Worker 通过相同 Compose 环境获得配置，重建版本保存实际快照。旧有效版本不自动重建；切分配置与文档表示不是模型权重或维度迁移。
