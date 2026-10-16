# 接口契约 v0.2

阶段 2 实现范围仅为两个健康接口，其余接口仍是后续阶段设计契约。各阶段通过 Pydantic 和 FastAPI OpenAPI 落实，只声明当阶段已实现的路由；SSE 事件另按本文件验证。报文示例用于说明结构，不是实际运行数据。

## 1. 通用约定

- 业务前缀 `/api/v1`；JSON 字段 snake_case，UUID 字符串，UTC ISO 8601 时间。
- Bearer JWT：`Authorization: Bearer <token>`。除登录和健康接口外均鉴权。
- `X-Request-ID` 可选，服务端校验格式或重新生成，并在响应头及错误体回传；它用于追踪，不是业务幂等键。
- JSON 请求禁止未知字段；筛选字段采用白名单，禁止接收任意 Qdrant filter、collection 名、模型密钥或 user_id。
- 列表使用 `limit`（默认 20，上限 100）和不透明 cursor，稳定按时间与 ID 排序。返回 `{items, next_cursor}`。
- 单资源直接返回对象；删除 204 无 body；异步动作 202 返回资源与任务定位。
- 未登录/失效身份 401；资源不存在或无权知晓统一 404；已确认可见但角色不足 403；状态冲突 409；大小超限 413；格式不支持 415；参数错误 422；限流 429；依赖不可用 503。
- 不在错误中泄露文件内部路径、SQL、供应商原始响应或 token。

统一错误体：

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "文档不存在或不可访问",
    "request_id": "<request-id>",
    "details": {}
  }
}
```

验证错误 details 只包含安全的字段位置和原因，不回显密码、token、完整正文。

## 2. 身份与健康（阶段 2–3）

| 方法与路径 | 请求 | 成功 | 规则 |
| --- | --- | --- | --- |
| `GET /health/live` | 无 | 200 `{status:"ok"}` | 仅检查 API 进程 |
| `GET /health/ready` | 无 | 200 状态；依赖失败 503 | MySQL、Qdrant、迁移；无密钥及内部 URL |
| `POST /api/v1/auth/login` | `{username, password}` | 200 `{access_token, token_type:"bearer", expires_in}` | 泛化凭据错误；登录限流 |
| `GET /api/v1/auth/me` | 无 | 200 `{id, username, is_admin}` | 每次检查用户有效状态 |

登录 username 1–64 字符，password 1–256 字符；创建用户的密码策略由管理 CLI 验证，登录不回显密码。JWT 默认有效 1,800 秒，签名密钥必须配置。MVP 注销为前端清除内存 token，不声称支持服务端单 token 即时吊销。

ready 输出还可包含 `capabilities.rag_configured`，只是配置状态，不代表模型远端连通。第二阶段没有模型配置也可以 ready，但问答阶段未配置时返回 `MODEL_NOT_CONFIGURED`。

### 阶段 2 健康接口细化（2026-10-15）

`/health/ready` 返回固定结构：`{status: "ok" | "unavailable", checks: {mysql: {status}, qdrant: {status}, migrations: {status}}, capabilities: {rag_configured: false, rag_available: false}}`。检查状态为 `ok`、`unavailable` 或 `pending`；迁移未到代码中的 Alembic head（含空库）为 pending，数据库不可连时为 unavailable。任一检查非 ok，HTTP 为 503；此响应属于健康报告，不套通用错误体。

MySQL 执行真实查询；Qdrant 请求带 API key 的 `/collections`，同时验证连通和凭据，避免仅检查无需鉴权的 `/readyz` 造成错误配置也显示就绪。每项探测有超时，不调用模型。第二阶段没有模型适配器，两个 RAG 能力值固定 false；到 AI 阶段再根据完整配置与实现计算。Web 同源代理 `/api/health/*` 到后端 `/health/*`，业务路径 `/api/v1/*` 原样转发。

`X-Request-ID` 接受 1–64 位 ASCII 字母、数字、`_`、`-`，非法值重新生成 UUID；所有 JSON 响应回传该头。阶段 2 错误码为 `VALIDATION_ERROR` (422)、`RESOURCE_NOT_FOUND` (404)、`METHOD_NOT_ALLOWED` (405)、`HTTP_ERROR`（其他 HTTP 错误）、`INTERNAL_ERROR` (500)。校验错误只返回字段位置与类型，不返回输入值、异常消息或异常上下文。

## 3. 知识库与成员（阶段 3）

| 方法与路径（业务前缀省略） | 请求 | 成功 | 权限 |
| --- | --- | --- | --- |
| `POST /knowledge-bases` | `{name, description?}` | 201 KnowledgeBase | 已登录，事务创建 owner |
| `GET /knowledge-bases` | 分页 | 200 列表 | 只返回当前成员的库 |
| `GET /knowledge-bases/{kb_id}` | 无 | 200 KnowledgeBase | viewer+ |
| `PATCH /knowledge-bases/{kb_id}` | `{name?, description?}` | 200 KnowledgeBase | owner |
| `DELETE /knowledge-bases/{kb_id}` | 无 | 204 | owner；仅允许无未删除文档、无活动任务的库 |
| `GET /knowledge-bases/{kb_id}/members` | 分页 | 200 成员列表 | owner |
| `PUT /knowledge-bases/{kb_id}/members/{user_id}` | `{role}` | 200 Member | owner，目标用户有效 |
| `DELETE /knowledge-bases/{kb_id}/members/{user_id}` | 无 | 204 | owner，不能移除最后有效 owner |

name 为 1–120 字符，description 最多 2,000 字符。KnowledgeBase 包含 `id, name, description, my_role, embedding_profile_id, created_at, updated_at`，不返回供应商密钥或内部 collection 配置。

profile 由服务端配置并在首次真实 Embedding 接入时验证；第二阶段不为此调用模型。若建库已在阶段 3 提供，可暂为未绑定 profile，阶段 4 首次索引前完成绑定；未绑定时不能假造有效向量。

## 4. 文档与任务（阶段 4）

| 方法与路径 | 请求 | 成功 | 权限/说明 |
| --- | --- | --- | --- |
| `POST /knowledge-bases/{kb_id}/documents` | multipart `file`、可选 `tags` JSON 数组 | 202 `{document, job}`，Location 为任务 URL | editor+；单文件上传 |
| `GET /knowledge-bases/{kb_id}/documents` | 分页、可选 availability | 200 列表 | viewer+ |
| `GET /documents/{document_id}` | 无 | 200 Document | viewer+，校验所属库 |
| `PATCH /documents/{document_id}` | `{filename?, tags?}` | 200 Document 或标签重建时 202 `{document, job}` | editor+；有当前运行索引时冲突 409 |
| `GET /documents/{document_id}/file` | 无 | 200 文件 | viewer+；attachment 下载，不能直接公开文件路径 |
| `DELETE /documents/{document_id}` | 无 | 202 `{document_id, deleted:true, cleanup_job_id}` | editor+；MySQL 先逻辑删除 |
| `POST /documents/{document_id}/reindex` | 无 | 202 `{job}` | editor+；保留旧版本至新发布 |
| `GET /knowledge-bases/{kb_id}/jobs` | 分页、可选 document_id/status | 200 列表 | viewer+，筛选不能跨库 |
| `GET /jobs/{job_id}` | 无 | 200 Job | viewer+，通过 job 归属鉴权 |
| `POST /jobs/{job_id}/retry` | 无 | 202 `{job}` | editor+，仅 failed 且无重试后继；新 job 的 retry_of_job_id 指向旧任务 |
| `GET /chunks/{chunk_id}` | 无 | 200 来源正文与定位 | viewer+；只能读取未删除文档的当前有效版本 |

tags 最多 10 项，每项 1–32 字符、去重；filename 是展示名称，1–255 字符，不能改变内部路径或媒体类型。标签更新在一个事务修改 MySQL 并创建新的 index 任务；MVP 通过重建同步 Qdrant 元数据，后续再优化为专用同步任务。重建期间保留旧版本，筛选必须按 MySQL 当前标签复核，允许暂时少召回。

文件大小和类型依据 [architecture.md](architecture.md)；扩展名与实际格式同时验证，限制读取量及解析资源。单库文档配额在锁定事务内验证。

Document：`id, kb_id, filename, media_type, size_bytes, tags, availability, active_index_version_id, latest_job, created_at, updated_at`。内部 storage_key 不返回。

Job：`id, document_id, kind, status, stage, attempt_count, max_attempts, progress_completed, progress_total, available_at, error?, created_at, started_at, finished_at`。error 只有脱敏 code/message；lease token、Worker 内部信息不返回。

上传返回 202 只代表文件与任务持久化完成，不代表可检索。前端默认每 2 秒轮询任务，终态停止；失败重试创建新任务而不是清空错误记录。同一文档并发重建/重试中只有一个成功创建当前任务，其他请求 409；已有后继时返回 409 并附后继 ID，后继失败则重试后继。purge 重试不要求文档未删除，但必须校验当前知识库权限；索引重试要求文档未删除。删除后的清理任务仍可由该库成员查询状态，不提供正文；删除响应丢失后再删可返回 404，客户端刷新列表确认结果。

## 5. 检索配置

MVP 仅 `strategy="dense"`，`top_k` 默认 5、整数 1–20；可选 `score_threshold` 必须在相应向量距离/profile 合法范围内。禁止以分数伪装置信度。

用户 metadata filter 支持：`document_ids`（最多 50 个 UUID）、`file_types`（txt/md/pdf 的子集）、`tags_any`（最多 10 个字符串）。空数组是参数错误，避免与“无筛选”歧义；未传字段才表示不增加该条件。

客户端请求范围与服务端授权范围取交集。document_ids 中含任何不属于当前知识库或不可访问文档时，统一返回 `FILTER_RESOURCE_NOT_FOUND`，不能告诉用户具体哪个私有 ID 存在。不允许客户端覆盖 kb_id、有效版本集合或 collection。

Hybrid/Rerank 到阶段 6 才新增明确配置；MVP 请求这些能力时返回 422 `RETRIEVAL_STRATEGY_UNSUPPORTED`，不能忽略参数后仍宣称已使用。

## 6. 会话和流式问答（阶段 5）

| 方法与路径 | 请求 | 成功 | 规则 |
| --- | --- | --- | --- |
| `POST /conversations` | `{kb_id, title?}` | 201 Conversation | 当前知识库 viewer+；归当前用户 |
| `GET /conversations` | 分页、可选 kb_id | 200 列表 | 只返回本人且当前有库权限的会话 |
| `GET /conversations/{conversation_id}` | 无 | 200 Conversation | 同时检查 owner user 和知识库权限 |
| `GET /conversations/{conversation_id}/messages` | 分页 | 200 消息列表 | 同上；包括真实状态和可见引用 |
| `GET /messages/{message_id}` | 无 | 200 Message | 根据所属会话鉴权，断流后查最终状态 |
| `POST /conversations/{conversation_id}/messages:stream` | 下方结构 | 200 `text/event-stream` | 同会话一个生成槽 |

请求结构：

```json
{
  "client_request_id": "<uuid>",
  "question": "<用户问题>",
  "retrieval": {
    "strategy": "dense",
    "top_k": 5,
    "metadata_filter": {
      "file_types": ["pdf"],
      "tags_any": ["架构"]
    }
  }
}
```

question 去首尾空白后 1–4,000 字符，另外受模型 token 预算约束；title 可选 1–120 字符，不调用额外模型生成标题。

事务创建用户消息和助手生成记录，唯一 `(conversation_id, client_request_id)` 防止重复执行。相同请求 ID 返回 409 `REQUEST_ALREADY_EXISTS` 和已存在消息 ID；客户端读取消息，不自动重放。失败后显式重试用新的请求 ID。

HTTP 响应开始前完成身份、参数、会话、生成槽、模型配置等检查，失败返回普通 JSON 错误。进入流后检索、供应商、超时或落库错误通过 SSE `error` 事件传递；已发出的 200 不能改成 HTTP 500。

### 6.1 SSE 事件

每个应用事件使用 `event: <名称>`、`id: <递增序号>`、`data: <单行 JSON>`，以空行结束；id 只在当前流内用于去重，不表示服务端支持续传。事件数据包含 message_id/request_id 或由 start 建立关联。

| 事件 | data 主要字段 | 时机 |
| --- | --- | --- |
| `start` | request_id、user_message_id、message_id | 生成记录已提交 |
| `sources` | candidates 数组 | 召回复核、上下文裁剪之后，候选不等于最终引用 |
| `delta` | text | 真实模型文本片段，或无证据时的明确拒答文本 |
| `done` | message_id、status、answer_type、citations、usage、timings | 最终消息与引用已提交，仅成功终止时 |
| `error` | code、message、retryable、message_id、request_id | 流内失败，随后关闭；不会再发 done |

无消息时每 15 秒发送 SSE comment 心跳（例如 `: ping`），心跳不算答案 delta，不延长 LLM 总 deadline。[SSE 格式](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)

候选来源字段：`source_label, chunk_id, document_id, index_version_id, filename, page_no?, section_title?, excerpt, retrieval_score, rerank_score?`；excerpt 为已鉴权、长度受限的正文。客户端链接必须走受保护接口，不使用内部文件 URL。

done 的 `status="completed"`；`answer_type` 为 `answer` 或 `abstention`；citations 仅包含服务端验证存在于本轮候选的来源。未知引用、需要证据的回答缺少有效引用时，发送 `CITATION_INVALID` error 并将消息设 failed，不发送 done；不能伪造来源来补齐。usage 无供应商数据时 NULL，timings 区分检索、首 token 和总耗时。

示意事件：

```text
event: start
id: 1
data: {"request_id":"<request-id>","user_message_id":"<uuid>","message_id":"<uuid>"}

event: sources
id: 2
data: {"candidates":[]}

event: delta
id: 3
data: {"text":"知识库中未找到足够依据。"}

event: done
id: 4
data: {"message_id":"<uuid>","status":"completed","answer_type":"abstention","citations":[],"usage":null,"timings":{}}

```

响应头：`Content-Type: text/event-stream`、`Cache-Control: no-cache, no-transform`、`X-Accel-Buffering: no`；代理关闭缓冲、读取超时大于服务总 deadline。前端 fetch POST 带 Authorization，验证 response.ok 与内容类型后才消费 SSE。

客户端不能把网络 packet 当完整事件；解析器必须处理一个事件跨多个网络块、多个事件在一块、UTF-8 汉字拆分、JSON 转义和终止事件。正文作为流中草稿展示，收到 done 后确认保存。EOF 未收到 done/error 时显示连接中断，并通过 GET 消息核验；禁止自动二次发起 POST。

断开连接触发模型取消及 `cancelled` 落库；服务崩溃留下 `generating` 后转为 `interrupted`。message status 是事实，客户端不得只凭已显示正文标记完成。Markdown 禁止原始 HTML，清理 javascript 等危险协议，引用点击重新鉴权。

## 7. 核心错误码

| 错误码 | 场景 |
| --- | --- |
| AUTH_INVALID / USER_DISABLED | 身份无效或用户停用，对外凭据错误不区分账号存在性 |
| RESOURCE_NOT_FOUND / ROLE_FORBIDDEN | 资源不可见或可见但不能写 |
| LAST_OWNER_REQUIRED | 会留下没有有效 owner 的知识库 |
| DOCUMENT_TOO_LARGE / DOCUMENT_TYPE_UNSUPPORTED / DOCUMENT_TEXT_EMPTY | 文件大小、格式、正文不满足要求 |
| KB_DOCUMENT_LIMIT / INDEX_JOB_CONFLICT / JOB_NOT_RETRYABLE | 配额或任务状态冲突 |
| FILTER_RESOURCE_NOT_FOUND / RETRIEVAL_STRATEGY_UNSUPPORTED | 非法筛选或未实现策略 |
| CONVERSATION_BUSY / REQUEST_ALREADY_EXISTS | 并发生成或重复请求 |
| MODEL_NOT_CONFIGURED / EMBEDDING_PROFILE_MISMATCH | 缺模型配置或模型空间不一致 |
| UPSTREAM_TIMEOUT / UPSTREAM_UNAVAILABLE / STORAGE_UNAVAILABLE | 依赖超时或故障 |
| CITATION_INVALID / MESSAGE_PERSIST_FAILED | 引用校验或最终落库失败 |

错误码表在实现时细化，并有契约测试验证 HTTP 状态、响应结构和敏感字段处理。
