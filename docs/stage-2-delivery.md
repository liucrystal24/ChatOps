# 阶段 2 交付记录

日期：2026-10-15。阶段 2 全部验收通过；范围是可运行工程骨架，不包含阶段 3 及之后业务。

## 已实现

- 初始化本地 Git，未创建 commit 或远端；忽略配置、虚拟环境、依赖目录及运行产物。
- Python 3.12、uv.lock、npm package-lock.json；FastAPI lifespan 管理真实 SQLAlchemy 连接池与 HTTP client。
- 必填配置校验、SecretStr、无输入值的启动错误、统一 HTTP/422/500 错误、request_id JSON 日志。
- 两个健康路由及实际 OpenAPI 快照；ready 探测 MySQL、迁移 head 和带 API key 的 Qdrant。
- Vue 3/TypeScript/Vite/Element Plus 状态页，10 秒刷新、503 报告与网络失败状态；不展示未实现的上传或问答操作。
- 固定版本/digest 的 Compose 数据服务、一次性迁移任务、API 与 Web；默认只开放 Web loopback。
- 空业务迁移 0001_baseline，只由 Alembic 创建版本表；真实业务模型留到对应阶段。
- 单元、服务集成、前端行为测试和独立验收脚本；更新架构、模型、契约、测试、依赖及 README。

## 已执行检查

| 检查 | 当前结果 |
| --- | --- |
| Docker daemon | 28.3.2 可连接，Docker Desktop 已启动 |
| uv 冻结安装 / Ruff | 通过，Python 3.12.14；锁定实际安装版本 |
| 本机 pytest | 16 passed，3 integration deselected；无模型测试 |
| 前端 | TypeScript 6.0.3 下 typecheck、3 项行为测试、production build 通过 |
| 容器内 pytest | 最终锁文件下 19 passed（16 unit + 3 integration），无警告 |
| 完整 Compose | 两个独立项目均已 build/start；migrate exit 0，MySQL/Qdrant/API/Web healthy |
| 故障与持久化 | 分别停止 MySQL/Qdrant：ready 503、live 200；恢复后 200。重建两个数据容器后，MySQL 专用记录与 Qdrant 专用点仍存在，清理成功 |
| 代理与 OpenAPI | Web 入口与 request_id 正常；本机 Uvicorn 和容器 OpenAPI 均与提交快照一致 |
| 敏感信息 | 统一错误/日志脱敏测试；本次真实密钥未出现在容器日志、静态资源或 59 个候选仓库文件；.env 权限 0600、拒绝覆盖、Git 忽略通过 |
| 页面 | Playwright 实际浏览器：1440×1000 和 390×844 检查、手动刷新；390 宽度无横向溢出，控制台 0 errors/0 warnings |
| 本机开发 | 叠加 compose.dev.yaml、uv 环境文件迁移/Uvicorn、Vite 启动和健康代理均通过；临时进程已结束 |

精确版本及 TypeScript 兼容性选择见 [environment-and-dependencies.md](environment-and-dependencies.md)。`scripts/verify_stage2.py` 仅允许专用测试项目，日志保存在忽略的 `artifacts/` 中，写出前脱敏。

实际执行的主要命令：`uv sync --frozen --python python3.12`、`uv run --frozen pytest -q`、Ruff check/format、Node 22.22.2 下的 `npm ci` / typecheck / test / build、`python3.12 scripts/verify_stage2.py`、`docker compose up --build -d --wait --wait-timeout 180` 以及 README 中的本机开发命令。独立脚本最终输出全部 7 步成功；首次旧测试镜像出现 TestClient 弃用警告，加入官方建议的仅测试 httpx2 后复验无警告。

修复了默认并行构建时 API/migrate 导出同名镜像的竞争：改用不同本地标签，共用相同 Dockerfile runtime 阶段。修复后 README 默认构建启动命令通过。页面截图在本地忽略的 `output/playwright/`，未提交浏览器产物。未创建 Git 提交或推送远端。

交付时默认实例在 `http://127.0.0.1:8080` 运行，4 个常驻服务 healthy，迁移 exit 0；手动迁移、`docker compose down` 保留卷和再次 `up --wait` 已验证。测试实例已通过 README 命令停止并保留测试卷，临时本机 Uvicorn/Vite 进程已结束。最终 `build api migrate web test`、Ruff、OpenAPI/文档链接、默认实例日志扫描复核通过。

## 镜像基线

| 来源 | 精确标签 | 多平台 manifest digest |
| --- | --- | --- |
| docker.io/library/mysql | 8.4.11 | sha256:24989179f58856db763f418010c1169fecc944e3ec1ea7e921572d83eb070f70 |
| docker.io/qdrant/qdrant | v1.19.2 | sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad |
| docker.io/library/python | 3.12.14-slim-bookworm | sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e |
| ghcr.io/astral-sh/uv | 0.12.5 | sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 |
| docker.io/library/node | 22.22.2-bookworm-slim | sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e |
| docker.io/library/nginx | 1.30.6-alpine | sha256:ff8328b4279ec385af48cde9ef486e7165ad74b7065cb3cd86c377384e2e2dd5 |

本次宿主机与容器为 amd64；未验证 ARM 运行。依赖通过注册源及官方文档核对日期为 2026-10-15，镜像通过真实拉取核对 digest。首次拉取的 OAuth 网络超时已通过重试处理。

## 未实现和下一阶段验收

无认证、知识库业务表、文件卷/Worker、解析、真实 Embedding/LLM、SSE 或问答。未执行模型 API、RAG 质量评测、业务权限/锁/租约测试、备份恢复或压力测试。健康报告中的 RAG 能力明确为 false，不用测试替身作为运行回退。

阶段 3 需要新的推进授权，按开发计划新增用户/知识库/成员迁移、管理 CLI、Argon2/JWT 和页面；至少两个真实账号验证私有库隔离、viewer/editor/owner 边界、停用与撤权即时生效，并在真实 MySQL 并发事务中验证最后 owner 约束。
