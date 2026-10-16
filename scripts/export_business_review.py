"""导出虚构业务首轮结果与显式Agent审核；保持原始报告和冻结标签不变。"""

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", "<br>")


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
    ]


def main(args):
    from evaluate_business import saved_usage_summary

    from app.modules.chat.evidence import catalog

    suite = ROOT / "evaluation/business-v1"
    report = json.loads(args.report.read_text())
    review = json.loads(args.review.read_text())
    assert report.get("finished_at")
    assert hashlib.sha256(args.report.read_bytes()).hexdigest() == review["baseline_sha256"]
    assert (
        hashlib.sha256((suite / "cases.jsonl").read_bytes()).hexdigest() == review["cases_sha256"]
    )
    cases = {
        c["case_id"]: c for c in map(json.loads, (suite / "cases.jsonl").read_text().splitlines())
    }
    audits = {r["case_id"]: r for r in review["rows"]}
    assert set(cases) == set(audits) == {r["case_id"] for r in report["rows"]}
    rows, details, claim_count = [], [], 0
    for row in report["rows"]:
        cid, answer = row["case_id"], row["content"]
        case, audit = cases[cid], audits[cid]
        available = {
            (u["source_label"], u["text"]) for u in catalog(row["retrieval_query"], row["sources"])
        }
        for claim in audit["claims"]:
            assert (claim["source_label"], claim["quote"]) in available, cid
            claim_count += 1
        extracted = list(re.finditer(r"(?ms)^> (.*?)\n\n\[(S\d+)\](?=\n\n|$)", answer))
        assert len(extracted) == len(audit["claims"]), cid
        source_map = {s["source_label"]: s for s in row["sources"]}
        actual_sources = [source_map[c["source_label"]] for c in row["citations"]]
        question = case["question"]
        if case.get("history_question"):
            question = f"上文：{case['history_question']}\n追问：{question}"
        evidence = "\n".join(
            f"预期：{a['file']} 字符[{a['char_start']},{a['char_end']})\n原文：{a['quote']}"
            for u in case["evidence_units"]
            for a in u["alternatives"][:1]
        )
        if not evidence:
            evidence = "没有直接答案依据；资料中的日期、其他套餐和引用邮件不能补足该事实。"
        actual = "；".join(f"{s['source_label']} {s['filename']}" for s in actual_sources)
        if actual:
            evidence += "\n实际引用：" + actual
        verdict = ("正确" if audit["agent_correct"] else "错误") + "（Agent）\n" + audit["notes"]
        rows.append([cid, question, answer, case["reference_answer"], evidence, verdict])
        details += [
            f"\n## {cid} 详细依据\n",
            f"问题：{question}\n",
            f"独立检索问题：{row['retrieval_query']}\n",
            f"审核意见：{audit['notes']}\n",
            f"Top-5证据：{row['evidence_v2']}；进入摘录目录的证据：{row['catalog_evidence']}。\n",
        ]
        # 错误题公开全部Top-5，正确题公开实际引用；保留干扰上下文供复查。
        shown = row["sources"] if not audit["agent_correct"] else actual_sources
        if not shown:
            shown = row["sources"][:1]
        for source in shown:
            details += [
                f"{source['source_label']} `{source['filename']}`；"
                f"{source['section_title']}；字符[{source['char_start']},{source['char_end']})；"
                f"切片ID `{source['chunk_id']}`。\n",
                "\n".join("> " + line for line in source["excerpt"].splitlines()) + "\n",
            ]
        if not audit["agent_correct"] and case["answerable"]:
            gold = case["evidence_units"][0]["alternatives"][0]
            ranks = [
                i
                for i, c in enumerate(row["diagnostic_candidates"], 1)
                if c["document_id"] == report["document_mappings"][gold["file"]]
                and c["char_start"] <= gold["char_start"]
                and c["char_end"] >= gold["char_end"]
            ]
            details.append(f"正确依据在回答后Top-20诊断中的排名：{ranks or '未出现'}。\n")
    document = [
        "# 虚构业务测试集：问题、实际答案与逐题审核\n",
        "日期：2026-10-16。5篇原创虚构业务文章，36题；以下实际答案来自首次真实MySQL、Qdrant、Embedding和聊天模型运行，没有改写成参考答案。\n",
        "审核者为Codex Agent，与文章/题集作者相同，属于工程复核，不是独立真人验收。"
        "‘正确’须满足问题的套餐、年份、属性、单位和条件；来源可访问或摘录原文一致仍可能答非所问。"
        "所有真人字段保留待复核。\n",
        "仅将 `evaluation/business-v1/corpus/` 下5篇文章导入知识库；"
        "不要导入题集、参考答案或审核结果。"
        "实际测试使用临时独立测试知识库，结束后清理；下列切片ID用于审计，原文文件一直保留。\n",
        f"原始运行：`{args.report.relative_to(ROOT)}`；审核：`evaluation/business-v1/agent-review.json`。\n",
        *table(["题号", "问题", "实际回答", "预期答案", "原文依据与来源切片", "逐题审核"], rows),
        *details,
    ]
    (ROOT / "docs/business-v1-test-cases.md").write_text("\n".join(document) + "\n")
    condensed = {k: v for k, v in report.items() if k != "rows"}
    condensed["baseline_report"] = str(args.report.relative_to(ROOT))
    condensed["baseline_sha256"] = review["baseline_sha256"]
    condensed["agent_review"] = review["summary"]
    condensed["literal_excerpt_count_verified"] = claim_count
    final_usage = saved_usage_summary(report)
    assert final_usage["requests_with_usage"] <= report["model_calls"]["chat_http"]
    condensed["model_calls"] = {**report["model_calls"], **final_usage}
    if report["model_calls"]["requests_with_usage"] != final_usage["requests_with_usage"]:
        condensed["instrumentation_correction"] = dict(
            reason="首轮脚本误将重复累计usage事件相加；按已保存的答案、首轮及改写最终usage分别计一次。"
            "HTTP次数、问题、答案、评分未改；原始报告未覆盖。",
            original_counts=report["model_calls"],
            corrected_counts=condensed["model_calls"],
            first_runner_snapshot="artifacts/business-v1/evaluate_business-first-run.py",
        )
    condensed["postrun_tool_hashes"] = {
        p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
        for p in ("scripts/evaluate_business.py", "scripts/export_business_review.py")
    }
    condensed["rows"] = [
        {k: v for k, v in row.items() if k != "diagnostic_candidates"} for row in report["rows"]
    ]
    (suite / "results.json").write_text(json.dumps(condensed, ensure_ascii=False, indent=2) + "\n")
    print("Exported", len(rows), "answers and verified", claim_count, "literal excerpts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument(
        "--review", type=Path, default=ROOT / "evaluation/business-v1/agent-review.json"
    )
    main(parser.parse_args())
