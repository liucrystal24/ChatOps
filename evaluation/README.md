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
