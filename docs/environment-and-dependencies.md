# 环境检查与依赖核对

> 本提交为补录的设计基线，根据最早保存的阶段2设计资料回溯整理，并非阶段1精确原始快照。

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

