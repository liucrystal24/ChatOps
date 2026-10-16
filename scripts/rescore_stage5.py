"""按冻结的 v2 候选标注重评历史报告；只写新文件，绝不覆盖原报告。"""

import argparse
import hashlib
import json
from pathlib import Path

from stage5_metrics import score_case, summarize_evidence, validate_cases

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("输出已存在，拒绝覆盖")
    cases_path = ROOT / "evaluation/cases-v2.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text().splitlines()]
    manifest = json.loads((ROOT / "evaluation/manifest-v2.json").read_text())
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest() == manifest["cases_sha256"]
    corpus = {p.name: p.read_text() for p in (ROOT / "evaluation/corpus/v1").glob("*.md")}
    validate_cases(cases, corpus)
    report = json.loads(args.report.read_text())
    frozen = json.loads((ROOT / "evaluation/manifest-v1.json").read_text())
    assert report["corpus"] == frozen, "报告语料不是冻结 v1"
    mappings = report.get("document_mappings", {})
    for row in report["rows"]:
        for s in row["sources"]:
            if s["filename"] in mappings and mappings[s["filename"]] != s["document_id"]:
                raise ValueError("来源文件映射有歧义")
            mappings[s["filename"]] = s["document_id"]
    by_id = {c["case_id"]: c for c in cases}
    rows = [
        {**r, "evidence_v2": score_case(by_id[r["case_id"]], r["sources"], mappings)}
        for r in report["rows"]
    ]
    result = dict(
        source_report=str(args.report.resolve()),
        source_report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
        annotation_sha256=manifest["cases_sha256"],
        summary=summarize_evidence(rows),
        cases=[dict(case_id=r["case_id"], score=r["evidence_v2"]) for r in rows],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
