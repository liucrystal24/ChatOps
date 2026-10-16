# 阶段 5 v2 标注待人工复核

状态：Agent 按冻结原文整理，全部未人工复核。v1 原题/报告不变；旧 holdout 改称 regression，新增 b01–b10 是后续实验留出题，不保证与开发题语义独立。

请逐题核对问题、每个事实、等价来源是否完整；在单独 review 文件记录 reviewer、approved、理由，不因系统输出而删改失败题。

## s01 · dev

业务事实以哪个存储为准？

参考：MySQL 是用户、权限、文档、任务、有效索引版本和会话的唯一业务事实来源。

- 事实：MySQL 是用户、权限、文档、任务、有效索引版本和会话的唯一业务事实来源。
  - [architecture.md:1162–1200](corpus/v1/architecture.md)：MySQL 是用户、权限、文档、任务、有效索引版本和会话的唯一业务事实来源。

人工审核：待填写 reviewer / approved / notes。

## s02 · dev

项目本地运行哪些存储，模型怎么调用？

参考：本地部署应用、MySQL、Qdrant 和文件存储，LLM/Embedding 通过真实供应商 API 调用。

- 事实：本地存储 MySQL、Qdrant、文件
  - [architecture.md:294–319](corpus/v1/architecture.md)：本地部署应用、MySQL、Qdrant 和文件存储
- 事实：云端真实模型 API
  - [architecture.md:320–349](corpus/v1/architecture.md)：LLM/Embedding 通过真实供应商 API 调用。

人工审核：待填写 reviewer / approved / notes。

## s03 · dev

MVP 单个知识库最多保留多少个未删除文档？

参考：MVP 默认单知识库最多 200 个未删除文档

- 事实：MVP 默认单知识库最多 200 个未删除文档
  - [architecture.md:234–257](corpus/v1/architecture.md)：MVP 默认单知识库最多 200 个未删除文档

人工审核：待填写 reviewer / approved / notes。

## s04 · dev

项目支持哪些上传格式，扫描图片 PDF 可以识别吗？

参考：MVP 支持 UTF-8 TXT/Markdown、包含可提取文字的 PDF；不做 OCR

- 事实：TXT/Markdown/文字 PDF
  - [architecture.md:2664–2702](corpus/v1/architecture.md)：MVP 支持 UTF-8 TXT/Markdown、包含可提取文字的 PDF
  - [stage4.md:313–340](corpus/v1/stage4.md)：子进程受限解析 UTF-8 TXT/MD/文字 PDF
- 事实：不做扫描件 OCR
  - [architecture.md:2703–2709](corpus/v1/architecture.md)：不做 OCR
  - [stage4.md:385–397](corpus/v1/stage4.md)：扫描 PDF 明确失败。
  - [architecture.md:3384–3431](corpus/v1/architecture.md)：PDF 无可提取正文时返回 `DOCUMENT_TEXT_EMPTY` 并说明不支持 OCR。

人工审核：待填写 reviewer / approved / notes。

## s05 · dev

递归切分默认长度和目标重叠分别多少？

参考：初始默认 800 字符、目标重叠 120 字符

- 事实：块长 800 字符
  - [architecture.md:2971–2982](corpus/v1/architecture.md)：初始默认 800 字符
- 事实：目标重叠 120 字符
  - [architecture.md:2983–2994](corpus/v1/architecture.md)：目标重叠 120 字符

人工审核：待填写 reviewer / approved / notes。

## s06 · dev

单文件大小、PDF 页数、提取文本和 Chunk 数量默认保护上限分别是什么？

参考：单文件 10 MiB、PDF 100 页、提取文本 200 万字符、每次索引 5,000 个 Chunk

- 事实：文件10MiB
  - [architecture.md:3308–3318](corpus/v1/architecture.md)：单文件 10 MiB
- 事实：PDF100页
  - [architecture.md:3319–3328](corpus/v1/architecture.md)：PDF 100 页
- 事实：正文200万字符
  - [architecture.md:3329–3341](corpus/v1/architecture.md)：提取文本 200 万字符
- 事实：5000块
  - [architecture.md:3342–3360](corpus/v1/architecture.md)：每次索引 5,000 个 Chunk

人工审核：待填写 reviewer / approved / notes。

## s07 · dev

索引任务租约多久，每隔多久续租？

参考：租约默认 90 秒，每 20 秒续租

- 事实：租约90秒
  - [architecture.md:3551–3560](corpus/v1/architecture.md)：租约默认 90 秒
- 事实：续租20秒
  - [architecture.md:3561–3569](corpus/v1/architecture.md)：每 20 秒续租

人工审核：待填写 reviewer / approved / notes。

## s08 · dev

索引任务最多自动执行多少次，永久错误会自动重试吗？

参考：任务默认最多自动执行 3 次；仅瞬时网络错误、429、可重试 5xx 等进入指数退避，永久格式/凭据/维度错误直接失败。

- 事实：最多3次
  - [architecture.md:3603–3617](corpus/v1/architecture.md)：任务默认最多自动执行 3 次
- 事实：永久错误直接失败
  - [architecture.md:3646–3663](corpus/v1/architecture.md)：永久格式/凭据/维度错误直接失败。

人工审核：待填写 reviewer / approved / notes。

## s09 · dev

文档删除时先修改数据库还是先删除向量？

参考：删除先在 MySQL 逻辑删除并清空有效版本、撤销任务发布资格，再提交清理任务。

- 事实：删除先在 MySQL 逻辑删除并清空有效版本、撤销任务发布资格，再提交清理任务。
  - [architecture.md:4042–4082](corpus/v1/architecture.md)：删除先在 MySQL 逻辑删除并清空有效版本、撤销任务发布资格，再提交清理任务。

人工审核：待填写 reviewer / approved / notes。

## s10 · dev

实例管理员是否天然拥有所有知识库读取权限？

参考：实例管理员负责用户生命周期，不自动获得所有知识库读取权。

- 事实：管理员无隐含读取权限
  - [architecture.md:4396–4424](corpus/v1/architecture.md)：实例管理员负责用户生命周期，不自动获得所有知识库读取权。
  - [api.md:4332–4348](corpus/v1/api.md)：实例管理员没有隐含知识库读权限。

人工审核：待填写 reviewer / approved / notes。

## s11 · dev

登录 token 默认多久失效，浏览器刷新后怎么办？

参考：JWT 默认 30 分钟（1800秒）；刷新页面后重新登录，token 仅在内存，不做 refresh token。

- 事实：JWT30分钟
  - [architecture.md:4426–4453](corpus/v1/architecture.md)：登录使用短期 Bearer JWT（默认 30 分钟）
  - [api.md:1452–1468](corpus/v1/api.md)：JWT 默认有效 1,800 秒
  - [api.md:3622–3647](corpus/v1/api.md)：JWT 固定 HS256、有效期默认 1800 秒
- 事实：刷新重新登录
  - [architecture.md:4467–4510](corpus/v1/architecture.md)：前端内存保存 token，刷新页面重新登录，MVP 不做 refresh token。

人工审核：待填写 reviewer / approved / notes。

## s12 · regression

无有效检索上下文时系统如何回答？

参考：无上下文时返回明确的“知识库中未找到足够依据”，不调用模型编造文档结论

- 事实：固定拒答
  - [architecture.md:6760–6783](corpus/v1/architecture.md)：无上下文时返回明确的“知识库中未找到足够依据”
- 事实：不调用模型编造
  - [architecture.md:6784–6795](corpus/v1/architecture.md)：不调用模型编造文档结论

人工审核：待填写 reviewer / approved / notes。

## s13 · regression

历史默认最多保留多少个完整轮次用于生成？

参考：多轮默认最多使用最近 6 个完整轮次

- 事实：多轮默认最多使用最近 6 个完整轮次
  - [architecture.md:6366–6384](corpus/v1/architecture.md)：多轮默认最多使用最近 6 个完整轮次

人工审核：待填写 reviewer / approved / notes。

## s14 · regression

SSE 何时允许发送 done 事件？

参考：只在完成消息及引用事务提交后发送 `done`

- 事实：消息与引用提交后done
  - [architecture.md:7228–7251](corpus/v1/architecture.md)：只在完成消息及引用事务提交后发送 `done`
  - [api.md:8700–8717](corpus/v1/api.md)：最终消息与引用已提交，仅成功终止时

人工审核：待填写 reviewer / approved / notes。

## s15 · regression

每个知识库文件累计和整个文件卷的默认限制是多少？

参考：每库未删除文件累计 500 MiB；文件卷使用量达到 2 GiB 时拒绝新上传。

- 事实：单库500MiB
  - [architecture.md:9780–9797](corpus/v1/architecture.md)：每库未删除文件累计 500 MiB
- 事实：文件卷2GiB
  - [architecture.md:9798–9820](corpus/v1/architecture.md)：文件卷使用量达到 2 GiB 时拒绝新上传。

人工审核：待填写 reviewer / approved / notes。

## s16 · regression

Qdrant 是否保存完整正文，召回后正文从哪里取？

参考：Qdrant 只返回定位 payload；按返回 ID 从 MySQL 加载正文

- 事实：Qdrant 只返回定位 payload；按返回 ID 从 MySQL 加载正文
  - [architecture.md:5008–5048](corpus/v1/architecture.md)：Qdrant 只返回定位 payload；按返回 ID 从 MySQL 加载正文

人工审核：待填写 reviewer / approved / notes。

## f01 · dev

那租约多久，多久续一次？

参考：租约默认 90 秒，每 20 秒续租

- 事实：租约90秒
  - [architecture.md:3551–3560](corpus/v1/architecture.md)：租约默认 90 秒
- 事实：续租20秒
  - [architecture.md:3561–3569](corpus/v1/architecture.md)：每 20 秒续租

人工审核：待填写 reviewer / approved / notes。

## f02 · dev

它默认多久有效？

参考：登录使用短期 Bearer JWT（默认 30 分钟）

- 事实：JWT30分钟
  - [architecture.md:4426–4453](corpus/v1/architecture.md)：登录使用短期 Bearer JWT（默认 30 分钟）
  - [api.md:1452–1468](corpus/v1/api.md)：JWT 默认有效 1,800 秒
  - [api.md:3622–3647](corpus/v1/api.md)：JWT 固定 HS256、有效期默认 1800 秒

人工审核：待填写 reviewer / approved / notes。

## f03 · dev

默认块长度和重叠呢？

参考：初始默认 800 字符、目标重叠 120 字符

- 事实：块长 800 字符
  - [architecture.md:2971–2982](corpus/v1/architecture.md)：初始默认 800 字符
- 事实：目标重叠 120 字符
  - [architecture.md:2983–2994](corpus/v1/architecture.md)：目标重叠 120 字符

人工审核：待填写 reviewer / approved / notes。

## f04 · dev

其中 viewer 能做什么？

参考：viewer 只读及问答

- 事实：viewer 只读及问答
  - [architecture.md:4383–4395](corpus/v1/architecture.md)：viewer 只读及问答

人工审核：待填写 reviewer / approved / notes。

## f05 · regression

单个文件最多多大？

参考：单文件 10 MiB

- 事实：单文件 10 MiB
  - [architecture.md:3308–3318](corpus/v1/architecture.md)：单文件 10 MiB

人工审核：待填写 reviewer / approved / notes。

## f06 · regression

自动执行最多几次？

参考：任务默认最多自动执行 3 次

- 事实：任务默认最多自动执行 3 次
  - [architecture.md:3603–3617](corpus/v1/architecture.md)：任务默认最多自动执行 3 次

人工审核：待填写 reviewer / approved / notes。

## u01 · dev

项目出售的年度商业套餐精确价格是多少元？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## u02 · dev

项目公司的客户支持电话号码是什么？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## u03 · dev

项目生产服务器每月的电费是多少元？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## u04 · dev

运营这套项目的法律实体成立于哪一年？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## u05 · dev

这套系统商用合同的签署日期是什么？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## u06 · regression

当前运行机器实际配有几个 CPU 核心？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## u07 · regression

项目 2027 年正式版本的准确发布日期是哪一天？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## u08 · regression

生产部署服务器使用的 GPU 显卡具体型号是什么？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## b01 · holdout

索引任务保证每条任务仅投递一次，还是允许重复投递？

参考：投递语义是 **at-least-once**，不是 exactly-once；重复执行必须安全。

- 事实：投递语义是 **at-least-once**，不是 exactly-once；重复执行必须安全。
  - [architecture.md:3456–3505](corpus/v1/architecture.md)：投递语义是 **at-least-once**，不是 exactly-once；重复执行必须安全。

人工审核：待填写 reviewer / approved / notes。

## b02 · holdout

任务终态失败后，手动重试会覆盖原任务吗？

参考：手动重试仅对终态失败任务有效，创建关联的新任务，保留失败历史；同一失败任务最多创建一个直接后继

- 事实：手动重试仅对终态失败任务有效，创建关联的新任务，保留失败历史；同一失败任务最多创建一个直接后继
  - [architecture.md:3781–3828](corpus/v1/architecture.md)：手动重试仅对终态失败任务有效，创建关联的新任务，保留失败历史；同一失败任务最多创建一个直接后继

人工审核：待填写 reviewer / approved / notes。

## b03 · holdout

为什么不能直接用 Qdrant 已写入的部分向量回答问题？

参考：MySQL 与 Qdrant 没有共享事务。只有 MySQL 指向的完整版本才可检索，避免写了一半的数据进入模型上下文。

- 事实：MySQL 与 Qdrant 没有共享事务。只有 MySQL 指向的完整版本才可检索，避免写了一半的数据进入模型上下文。
  - [architecture.md:3870–3930](corpus/v1/architecture.md)：MySQL 与 Qdrant 没有共享事务。只有 MySQL 指向的完整版本才可检索，避免写了一半的数据进入模型上下文。

人工审核：待填写 reviewer / approved / notes。

## b04 · holdout

成员变更导致知识库失去最后一个有效 owner 时，接口应如何处理？

参考：移除/降级/停用会导致任一未删除知识库失去最后有效 owner 时，整个事务回滚并返回 409 `LAST_OWNER_REQUIRED`。

- 事实：移除/降级/停用会导致任一未删除知识库失去最后有效 owner 时，整个事务回滚并返回 409 `LAST_OWNER_REQUIRED`。
  - [api.md:4262–4332](corpus/v1/api.md)：移除/降级/停用会导致任一未删除知识库失去最后有效 owner 时，整个事务回滚并返回 409 `LAST_OWNER_REQUIRED`。

人工审核：待填写 reviewer / approved / notes。

## b05 · holdout

文档标签最多能设置几项，每项长度是多少？

参考：tags 最多 10 项，每项 1–32 字符、去重

- 事实：tags 最多 10 项，每项 1–32 字符、去重
  - [api.md:5787–5813](corpus/v1/api.md)：tags 最多 10 项，每项 1–32 字符、去重

人工审核：待填写 reviewer / approved / notes。

## b06 · holdout

聊天 Markdown 允许嵌入原始 HTML 和 javascript 链接吗？

参考：Markdown 禁止原始 HTML，清理 javascript 等危险协议，引用点击重新鉴权。

- 事实：Markdown 禁止原始 HTML，清理 javascript 等危险协议，引用点击重新鉴权。
  - [api.md:10194–10242](corpus/v1/api.md)：Markdown 禁止原始 HTML，清理 javascript 等危险协议，引用点击重新鉴权。

人工审核：待填写 reviewer / approved / notes。

## b07 · holdout

项目的备案编号是多少？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## b08 · holdout

供应商承诺的每月赔付金额是多少？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## b09 · holdout

生产数据库当前有多少个付费客户？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。

## b10 · holdout

项目维护者的身份证号码是什么？

参考：知识库中未找到足够依据。


人工审核：待填写 reviewer / approved / notes。
