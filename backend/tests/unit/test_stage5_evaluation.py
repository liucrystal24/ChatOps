import copy
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("stage5_metrics", ROOT / "scripts/stage5_metrics.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def fixture():
    cases = [
        json.loads(line) for line in (ROOT / "evaluation/cases-v2.jsonl").read_text().splitlines()
    ]
    corpus = {p.name: p.read_text() for p in (ROOT / "evaluation/corpus/v1").glob("*.md")}
    return cases, corpus


def source(span, **changes):
    return dict(
        document_id=span["file"],
        char_start=span["char_start"],
        char_end=span["char_end"],
        excerpt=span["quote"],
        **changes,
    )


def test_frozen_annotation_has_valid_source_positions_and_all_required_facts():
    cases, corpus = fixture()
    metrics.validate_cases(cases, corpus)
    case = next(c for c in cases if c["case_id"] == "s11")
    assert len(case["evidence_units"]) == 2
    damaged = copy.deepcopy(cases)
    damaged[0]["evidence_units"][0]["alternatives"][0]["quote"] = "伪造证据"
    with pytest.raises(ValueError):
        metrics.validate_cases(damaged, corpus)
    with pytest.raises(ValueError):
        metrics.validate_cases(cases + cases[:1], corpus)


def test_equivalent_location_and_partial_facts_count_separately():
    cases, corpus = fixture()
    mapping = {f: f for f in corpus}
    c = next(c for c in cases if c["case_id"] == "s11")
    alternate = c["evidence_units"][0]["alternatives"][1]
    assert metrics.score_case(c, [source(alternate)], mapping) == dict(
        hits=1, total=2, complete=False
    )
    final = c["evidence_units"][1]["alternatives"][0]
    assert metrics.score_case(c, [source(alternate), source(final)], mapping)["complete"]


def test_wrong_document_wrong_position_and_truncated_text_never_count():
    cases, corpus = fixture()
    mapping = {f: f for f in corpus}
    u = cases[0]["evidence_units"][0]
    s = source(u["alternatives"][0])
    for fields in [
        dict(document_id="another-kb"),
        dict(char_start=0, char_end=len(s["excerpt"])),
        dict(excerpt=""),
    ]:
        assert not metrics.unit_hit(u, [{**s, **fields}], mapping)


def test_adjacent_chunks_can_cover_one_unit_but_duplicates_cannot_fill_gap():
    cases, corpus = fixture()
    mapping = {f: f for f in corpus}
    u = cases[0]["evidence_units"][0]
    s = source(u["alternatives"][0])
    mid = s["char_start"] + 10
    left = {**s, "char_end": mid, "excerpt": s["excerpt"][:10]}
    right = {**s, "char_start": mid, "excerpt": s["excerpt"][10:]}
    assert metrics.unit_hit(u, [left, right], mapping)
    assert not metrics.unit_hit(u, [left, left], mapping)
    assert not metrics.unit_hit(
        u, [left, {**right, "char_start": mid + 1, "excerpt": right["excerpt"][1:]}], mapping
    )


def test_wrong_page_never_counts_and_summary_uses_evidence_units():
    cases, corpus = fixture()
    mapping = {f: f for f in corpus}
    case = next(c for c in cases if c["case_id"] == "s11")
    span = case["evidence_units"][0]["alternatives"][0]
    assert not metrics.unit_hit(case["evidence_units"][0], [source(span, page_no=9)], mapping)
    score = metrics.score_case(case, [source(span)], mapping)
    summary = metrics.summarize_evidence(
        [
            dict(answerable=True, evidence_v2=score),
            dict(answerable=False, evidence_v2={"hits": 0, "total": 0, "complete": False}),
        ]
    )
    assert summary["recall_at_5"] == 0.5 and summary["complete_questions"] == 0
    assert summary["human_annotation_reviewed"] is False
