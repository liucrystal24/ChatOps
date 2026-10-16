# 阶段 5 公开文档评测

`corpus/v1` 是项目真实设计/交付文档的固定公开快照，来源和 SHA 在 `manifest-v1.json`。`cases-v1.jsonl` 在推理前冻结：16 单轮、6 追问末轮、8 无答案题；开发/留出分别 20/10。问题和参考事实由 Codex 起草，**未经用户人工复核**，不称为人工标注验收。文档快照描述相应版本，不随开发自动改写。

```bash
python3 scripts/init_env.py --test --stage 5 --output .env.stage5-test
# 按 README 启动专用 MySQL/Qdrant、迁移后，停止测试 Worker，避免争抢离线索引任务。
backend/.venv/bin/python scripts/evaluate_stage5.py \
  --env-file .env.stage5-test --key-env-file .env --split dev
```

`--split all` 执行固定全题集；调参只用 dev。Key 只进服务端环境，不复制或打印。脚本只允许 *_test，创建专用随机用户/知识库，结束按自身 UUID 清理消息/引用/文档/向量/原文件。不要在同一测试库并行运行会回退迁移或领取任务的 pytest/Worker。

每题通过在线相同 ChatService/受限 Dense 检索/真实流模型生成，追问的首轮也真实生成。保存 `outputs/<时间>-<split>/report.json`、`report.md`、`review.jsonl`，输出目录忽略提交。记录模型、profile、索引版本、Prompt/语料/题集哈希及耗时。供应商 usage 原样记录；缺失为 NULL，不能用本地 tokenizer 推算计费。

证据召回按题集指定的稳定证据位置匹配；一个问题标注一个证据单元，单块包含原文或连续覆盖其 90% 时记命中。初版语料有重复说明，其他文件的同义事实不自动算命中，故该指标可能低估语义召回；不能观察答案后修改参考事实或删掉失败题。报告保留所有失败样本，关键事实字符串包含率只作诊断，不能替代人工正确率/支持率。人工复核表默认全部 null，填写 reviewer、correct、supported 和理由后再判定人工质量门槛。来源编号可解析并不证明事实被来源支持。


## v2 候选标注与阶段 5 改进验证

`cases-v2.jsonl` 保留原 30 题的问题和无答案样本，补齐逐事实证据单元、文件 SHA 和 Unicode 字符区间，允许原文中可核验的等价位置。s11 的参考答案补上原问题已要求的 token 有效期；这属于标注修正，不能计作实现收益。旧 holdout 已查看，改称 regression；另新增 b01–b10 作为后续实验的留出题。全部由 Agent 整理，**human_annotation_reviewed=false**，不会冒充真人独立标注或语义完全独立的新基准。

`manifest-v2.json` 固定题集哈希，`annotation-review-v2.md` 是逐题待复核材料。当前指标都是候选标注下的自动结果，最终门槛仍须人工核对。旧题集、语料和原报告保留原样。

`stage5_metrics.py` 严格匹配运行时映射的文档 ID、原文及位置；一个证据单元的任一合法等价位置完整覆盖才算命中，多块连续覆盖可合并，重复覆盖不重复计分，部分事实只计部分单元。v2 的 Recall 分母是证据单元总数，另列所有事实齐全的问题数；不能与 v1 单位置题目比例直接相减当作工程增益。

以下命令需要按上文启动专用真实服务、迁移，并停止测试 Worker；实际执行结果见交付记录：

```bash
# 只在 dev 比较候选覆盖；不生成答案，报告不会将未生成记为问答失败。
CHUNK_STRUCTURE=recursive-v1 EMBEDDING_TITLE_CONTEXT=false \
  backend/.venv/bin/python scripts/evaluate_stage5.py \
  --env-file .env.stage5-test --key-env-file .env --split dev --retrieval-only

# 最终方案的真实问答、前20条诊断；最终来源仍是Top-5。
backend/.venv/bin/python scripts/evaluate_stage5.py \
  --env-file .env.stage5-test --key-env-file .env --split all --diagnose

# 为历史报告生成独立重评文件；拒绝覆盖现有输出。
backend/.venv/bin/python scripts/rescore_stage5.py \
  evaluation/outputs/20261015T081017Z-all/report.json \
  --output evaluation/outputs/stage5-v2-rescore-old-baseline.json
```

默认读取 v2；`--cases evaluation/cases-v1.jsonl` 可运行原题的指定位置对照。v2 的 `legacy_v1_summary` 只统计原 s/f/u 共30题；全量 summary 的指定位置结果包含新增题，二者不可混用。`evidence_v2_summary` 和 `evidence_v2_by_split` 才是多证据评分。检索诊断候选由同一强制权限范围的真实检索服务取得，额外调用计入 embedding_http_attempts。

`--retrieval-only` 的追问只提供先前用户问题，不先生成助手答案，以隔离检索表达；它不能替代真实多轮 ChatService 评测。阶段结束仍运行完整真实问答。没有分级标注时不生成 nDCG，也不将关键词代理或模型自评写入人工正确率。

人工评分口径见 [review-rubric-v2.md](review-rubric-v2.md)；复核表中的 fact_reviews 逐条记录主张及实际引用，空值不会自动通过验收。

未采用的证据充分性检查实验报告包含 evidence_check_ms/status/usage，model_calls 如实累计该实验的额外真实调用。该检查未可靠改善开发集，已从默认运行逻辑移除；保留报告以说明取舍，不将模型判断填入人工质量字段。
