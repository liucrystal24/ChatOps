# 阶段 4 交付记录

日期：2026-10-15。状态：实现及验收完成。阶段 5 未开始。

## 实现范围

- 0003_document_jobs：文档、任务、独立 attempt 版本、Chunk；当前任务/有效版本循环复合 FK 后建，跨库/跨文档关联由真实 MySQL 约束。
- 文档上传/列表/详情/元数据/原文件/逻辑删除；配额、格式、UTF-8、内部 UUID 文件名、失败落盘回收；空库删除要求清理成功。
- 同代码 Worker，MySQL SKIP LOCKED 领取、随机租约、数据库时钟、心跳、有限重试、永久失败、独立手动重试后继；旧 token/过期/超时 attempt 不能发布。
- 子进程受限解析 UTF-8 TXT/MD/文字 PDF，页码/字符定位、中文递归切分、固定 BGE tokenizer 真实 token 计数；扫描 PDF 明确失败。
- 硅基流动非 Pro BAAI/bge-m3，真实探测 1024 维、校验模型/数量/维度/有限非零数值；MySQL 原子绑定 profile，完整 Qdrant 写入后原子发布。
- Dense 服务：服务器权限范围、有效版本、文档/类型/标签交集、返回前正文复核及最多两轮补召回；没有临时 HTTP 检索路由。
- 删除/清理、失效版本巡检、迟到向量重复清理、点数缺失时撤下并修复、一天以上无业务记录文件清理。
- 前端上传、两秒活动任务轮询/终态停止、失败原因与重试、重建/删除确认、元数据、分页来源和受保护下载。
- Compose 加 Worker/共享文件卷，0.4.0 镜像、固定 tokenizer 修订与哈希；锁文件、契约与 OpenAPI 同步。

## 实际检查结果

| 检查 | 实际结果 |
| --- | --- |
| Docker 后端回归 | **80 passed**（49 unit + 31 integration）；另 1 项 live 显式排除，不计跳过或通过 |
| 真实供应商 live | **1 passed**；中文培训说明召回、文字 PDF 第 2 页、1024 维、重复 upsert、成员撤权和删除过滤 |
| 前端 | **9 passed**；vue-tsc 与 Vite production build 通过，冻结 npm 锁文件 |
| 迁移/约束 | 专用空库 downgrade base、upgrade head、重复 upgrade；实际 FK/CHECK/唯一键、跨文档 Chunk 复合 FK |
| Worker | 同任务只领取一次、SKIP LOCKED、实际 SQL 续租、过期 token/attempt deadline 拒绝、三次上限与手动后继竞争 |
| 进程终止 | 独立 Worker 进程组 SIGKILL；使用真实数据库时钟推进租约过期，新的 attempt 继续处理原文件并成功 |
| 版本与修复 | 部分写入不可检索、重建失败保留旧版、清理后迟到 upsert 被再次清除、缺点时撤下并安排修复 |
| 权限/文件 | 上传/配额/失败回收、同 collection 跨库过滤、模型调用中撤权复核、来源/原文件/任务保护；未清理成功不能删除父库 |
| Web 实际网络 | CLI 双账号、Nginx multipart、真实 Worker、来源/原文件、重建 API/Worker 后共享文件保留、删除残留不召回、413 JSON 与 request_id |
| 故障与卷 | 分别停止 MySQL/Qdrant，ready=503/live=200，恢复后 ready=200；重建数据容器后专用记录仍存在 |
| 浏览器 | 真实上传 MD/两页 PDF/扫描件，失败重试保留历史，PDF 页码、标签与名称变更后重建、删除确认及清理成功；浏览器下载 PDF 与原文件字节一致 |
| UI/会话 | owner 1440×1200、viewer 390×844 截图已目视核验；scrollWidth/clientWidth 均 390，无横向溢出；viewer 无写按钮；刷新回到登录，local/session storage 均 0 |
| 浏览器日志 | Errors=0、Warnings=0 |
| 默认实例 | 0.4.0，0003 迁移 exit 0，API/Web/MySQL/Qdrant healthy，Worker running；现有账号/知识库/成员指纹一致，MySQL 3307 保留 |
| 凭据与检查 | 默认/测试服务日志、前端静态资源和候选文件密钥扫描通过；私有环境文件 0600，Ruff/格式、锁文件冻结安装、文档本地链接检查通过 |

本轮实际执行方式：`python3 scripts/verify_stage4.py` 完成镜像构建、80 项后端回归和 1 项 live。网络验收脚本最初发生 `http` 变量命名冲突；修复后使用 **`python3 scripts/verify_stage4.py --services-only` 完成步骤 3–7**，该次没有再次构建或运行 pytest。没有把该模式的未运行测试算作通过。结果分别在忽略的 `artifacts/stage4-tests-console.log`、`stage4-services-console.log`、`stage4-verification.log`。

执行中修复 Worker 冷启动提前加载 PDF/切分框架的问题：常量/分词器独立，重依赖只在解析子进程加载；实际进程终止恢复在最终容器回归中通过。修复 Nginx 超大上传的 HTML 错误，统一为 JSON 413 和 request_id。

截图为 `output/playwright/stage4-owner.png`、`stage4-viewer-mobile.png`，只包含生成的公开测试资料。临时浏览器账号、库、文档、任务、版本、原文件和向量已按本次 UUID 清理，凭据和登录辅助脚本已删除，浏览器已关闭。测试项目停止并保留卷，默认 8080 实例继续运行；没有预置测试账号到默认实例，没有自动提交或推送 Git。

## 边界和下一阶段

单 API 进程，上传落盘与文件卷配额在该进程串行检查；多 API 写入前要增加跨进程配额预留。授权短事务仍使用 guard，未做吞吐压力测试或生产高可用。Worker 无独立容器健康探针，依赖任务租约和状态恢复；没有备份恢复验收。

供应商提供的是模型名称而非不可变权重快照；记录 provider-managed，无法识别同维度静默权重漂移。Key 仅本地/运行环境，测试和日志不保存。真实调用不代表已完成 RAG 质量评测；目前无聊天、LLM、SSE、OCR 或 DOCX。

阶段 5 需用户单独授权和聊天模型配置；验收要求多轮流式问答、来源/拒答、持久化会话、取消/失败恢复及固定真实题集质量报告。当前不开始阶段 5。
