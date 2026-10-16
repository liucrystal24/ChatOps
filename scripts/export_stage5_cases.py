"""把真实运行结果转为可阅读表格；不生成/改写答案或填写人工评分。"""

import argparse
import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", "<br>")


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
    ]


def main(args):
    before, after, quality = [
        json.loads(p.read_text()) for p in (args.before, args.after, args.quality)
    ]
    assert after.get("finished_at") and quality.get("finished_at"), "只导出已完成运行"
    cases = {
        c["case_id"]: c
        for c in map(json.loads, (ROOT / "evaluation/cases-v2.jsonl").read_text().splitlines())
    }
    assert len(after["rows"]) == len(cases) == 40
    rows, details = [], []
    for row in after["rows"]:
        key = row["case_id"]
        case = cases[key]
        source_map = {s["source_label"]: s for s in row["sources"]}
        actual = [source_map[c["source_label"]] for c in row["citations"]]
        question = case["question"]
        if case.get("history_question"):
            question = (
                f"上文：{case['history_question']}\n追问：{question}\n"
                f"独立检索问题：{row['retrieval_query']}"
            )
        source_summary = "\n".join(
            f"{s['source_label']} · {s['filename']} · 字符[{s['char_start']},{s['char_end']})\n"
            f"{s['section_title']}\n原文：{s['excerpt'][:160]}"
            + ("…（完整原文见下方同题号详情）" if len(s["excerpt"]) > 160 else "")
            for s in actual
        )
        if not actual:
            source_summary = "未引用：没有直接答案依据。候选片段不能当作答案证据。"
        verdict = f"状态：{row['status']}；"
        verdict += "证据召回全中；" if row.get("evidence_v2", {}).get("complete") else ""
        verdict += "人工答案/支持率待复核"
        rows.append(
            [key, question, row["content"], case["reference_answer"], source_summary, verdict]
        )
        details += [
            f"\n## {key} 来源切片详情\n",
            f"问题：{case['question']}\n",
            f"实际完成状态：{row['status']}；回答类型：{row['answer_type']}。\n",
        ]
        if actual:
            for s in actual:
                details += [
                    f"{s['source_label']}：`{s['filename']}`；章节：{s['section_title']}；"
                    f"页码：{s['page_no'] or '不适用（Markdown）'}；"
                    f"原文字符区间：[{s['char_start']}, {s['char_end']})。\n",
                    f"切片ID：`{s['chunk_id']}`；文档ID：`{s['document_id']}`；版本ID：`{s['index_version_id']}`。\n",
                    "\n".join("> " + line for line in s["excerpt"].splitlines()) + "\n",
                ]
        else:
            details.append(
                "未使用任何切片作为最终答案依据。以下仅为检索到的干扰候选（首条），用于解释拒答：\n"
            )
            if row["sources"]:
                s = row["sources"][0]
                details += [
                    f"候选 {s['source_label']}：`{s['filename']}`；{s['section_title']}。\n",
                    "\n".join("> " + line for line in s["excerpt"].splitlines()) + "\n",
                ]
        details.append("预期答案的标注依据（与实际引用分开）：\n")
        if case["answerable"]:
            for unit in case["evidence_units"]:
                alt = unit["alternatives"][0]
                details += [
                    f"`{alt['file']}` 字符[{alt['char_start']},{alt['char_end']})："
                    f"{unit['fact']}\n",
                    "> " + alt["quote"].replace("\n", "\n> ") + "\n",
                ]
        else:
            details.append("冻结语料没有该事实，预期固定拒答；没有‘标准答案切片’可引用。\n")
    lines = [
        "# 阶段5测试用例与实际回答对照\n",
        "本表直接导出本轮真实运行结果，没有用预期答案替换实际回答。"
        "前40题使用真实 MySQL、Qdrant、Embedding 和聊天模型；"
        "后17条专项对照固定候选原文，只测生成与校验。\n",
        "阅读方式：‘回答答案’是系统实际返回文本；‘预期答案’是在测试前依据原文标注的参考答案；‘答案来源切片’是实际采用的文档片段。S1/S2仅在本题有效，完整原文和定位见下方同题号的详情。字符区间从0开始、右端不含；Markdown无PDF页码。\n",
        "原40题及其标准未改。标注由Agent整理，真人审核按用户要求保留待办；"
        "自动召回全中或引用可访问，不等于人工判断答案正确。"
        "公开测试资源在测试后清理，数据库UUID用于审计；"
        "原文仍保存在 evaluation/corpus/v1。\n",
        f"完整运行：`{args.after.relative_to(ROOT)}`；专项运行：`{args.quality.relative_to(ROOT)}`。\n",
        "## 40题完整回归\n",
        *table(["题号", "问题", "回答答案（实际）", "预期答案", "答案来源切片", "检查状态"], rows),
        "\n## 17条新增专项对照\n",
        "以下为开发回归集，已在真实模型首次调用前冻结；不是新的盲测集。‘通过’只表示预先写明的必含词、禁含词及拒答类型断言通过。\n",
    ]
    contrast_rows = []
    for row in quality["rows"]:
        if row["case_id"] in cases:
            continue
        expected = (
            "固定拒答：知识库中未找到足够依据。"
            if row["expect_abstention"]
            else "必须含：" + "、".join(row["required"])
        )
        if row["forbidden"]:
            expected += "；不得含：" + "、".join(row["forbidden"])
        contrast_rows.append(
            [
                row["case_id"],
                row["question"],
                row["content"],
                expected,
                "\n".join(s["excerpt"] for s in row["sources"]),
                "自动断言通过；人工待核" if row["automatic_assertions_passed"] else "自动断言失败",
            ]
        )
    lines += table(
        ["题号", "问题", "回答答案（实际）", "预期答案/断言", "给定来源切片（原文）", "检查状态"],
        contrast_rows,
    )
    lines += details
    lines += [
        "\n## 测试文件与复现入口\n",
        "- 原题集与参考证据：`evaluation/cases-v2.jsonl`；原文：`evaluation/corpus/v1/`。",
        "- 新专项题集：`evaluation/answer-quality-cases-v1.jsonl`；"
        "冻结SHA：`evaluation/answer-quality-manifest-v1.json`。",
        "- 规则测试：`backend/tests/unit/test_answer_evidence.py`；"
        "真实服务：`backend/tests/integration/test_chat.py`；"
        "真实模型端到端：`backend/tests/integration/test_live_chat.py`。",
        "- 40题入口：`scripts/evaluate_stage5.py`；"
        "固定候选入口：`scripts/evaluate_answer_quality.py`；"
        "表格导出：`scripts/export_stage5_cases.py`。\n",
    ]
    path = ROOT / "docs/stage-5-test-cases.md"
    path.write_text("\n".join(lines) + "\n")
    summary = dict(
        before_path=str(args.before.relative_to(ROOT)),
        after_path=str(args.after.relative_to(ROOT)),
        quality_path=str(args.quality.relative_to(ROOT)),
        report_sha256={
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [args.before, args.after, args.quality]
        },
        before_summary=before["summary"],
        after_summary=after["summary"],
        legacy_v1_summary=after["legacy_v1_summary"],
        evidence_v2_summary=after["evidence_v2_summary"],
        quality_summary=quality["summary"],
        model_calls=after["model_calls"],
        source_hashes=after["source_hashes"],
        human_reviewed=False,
        documents=["docs/stage-5-test-cases.md", "docs/stage-5-quality-fix-delivery.md"],
    )
    (ROOT / "evaluation/stage5-quality-results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(path.relative_to(ROOT), "40 full +", len(contrast_rows), "contrasts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("before", "after", "quality"):
        parser.add_argument("--" + name, type=lambda p: Path(p).resolve(), required=True)
    main(parser.parse_args())
