"""固定公开候选的真实模型生成回归；不冒充重新检索或人工评分。"""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def run(args):
    from app.core.config import load_settings
    from app.infrastructure.chat_model import ChatModel
    from app.modules.chat.evidence import catalog, render_plan
    from app.modules.chat.prompt import ABSTENTION, messages_for, validate_answer

    cases_path = args.cases
    manifest = json.loads(args.manifest.read_text())
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest() == manifest["cases_sha256"]
    cases = [json.loads(line) for line in cases_path.read_text().splitlines()]
    baseline = json.loads(args.baseline.read_text())
    frozen = {
        c["case_id"]: c
        for c in map(json.loads, (ROOT / "evaluation/cases-v2.jsonl").read_text().splitlines())
    }
    for row in baseline["rows"]:
        if not args.skip_historical and row["case_id"] in {"u05", "f04", "s15", "s02"}:
            case = frozen[row["case_id"]]
            cases.append(
                dict(
                    case_id=row["case_id"],
                    question=row["retrieval_query"],
                    original_question=case["question"],
                    sources=row["sources"],
                    before=row["content"],
                    reference_answer=case["reference_answer"],
                    expect_abstention=not case["answerable"],
                    required={
                        "f04": ["viewer", "只读", "问答"],
                        "s15": ["500 MiB", "2 GiB"],
                        "s02": ["本地部署应用", "真实供应商 API"],
                    }.get(row["case_id"], []),
                    forbidden={"f04": ["成员列表"], "s15": ["200 个"]}.get(row["case_id"], []),
                )
            )
    model = ChatModel(load_settings())
    report = dict(
        started_at=datetime.now(UTC).isoformat(),
        mode="fixed-public-context-generation",
        cases_sha256=manifest["cases_sha256"],
        baseline_path=str(args.baseline),
        baseline_sha256=hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        model=model.snapshot(),
        source_hashes={
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                ROOT / "backend/app/modules/chat/evidence.py",
                ROOT / "backend/app/modules/chat/constraints.py",
                ROOT / "backend/app/modules/documents/markdown_tables.py",
                ROOT / "backend/app/modules/chat/prompt.py",
                ROOT / "scripts/evaluate_answer_quality.py",
            ]
        },
        rows=[],
    )
    output = (
        ROOT / "evaluation/outputs" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-quality")
    )
    output.mkdir(parents=True)
    print(output.relative_to(ROOT), flush=True)
    try:
        for case in cases:
            started, raw, usage = monotonic(), "", None
            if catalog(case["question"], case["sources"]):
                async for part in model.stream(
                    messages_for([], case["question"], case["sources"]),
                    max_tokens=256,
                    json_mode=True,
                ):
                    raw += part.get("text", "")
                    usage = part.get("usage", usage)
                answer = render_plan(raw, case["question"], case["sources"])
            else:
                answer = ABSTENTION
            answer_type, citations = validate_answer(answer, case["sources"])
            passed = (
                (answer_type == "abstention") == case["expect_abstention"]
                and all(w in answer for w in case["required"])
                and not any(w in answer for w in case["forbidden"])
            )
            report["rows"].append(
                dict(
                    case,
                    content=answer,
                    answer_type=answer_type,
                    raw_plan=raw,
                    usage=usage,
                    citations=citations,
                    elapsed_ms=round((monotonic() - started) * 1000, 2),
                    automatic_assertions_passed=passed,
                    human_reviewed=False,
                )
            )
            (output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(case["case_id"], "PASS" if passed else "FAIL", flush=True)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["summary"] = dict(
            total=len(report["rows"]),
            passed=sum(r["automatic_assertions_passed"] for r in report["rows"]),
            human_correctness=None,
        )
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    finally:
        await model.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "evaluation/answer-quality-cases-v1.jsonl"
    )
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "evaluation/answer-quality-manifest-v1.json"
    )
    parser.add_argument("--skip-historical", action="store_true")
    asyncio.run(run(parser.parse_args()))
