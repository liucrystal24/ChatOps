import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_business import (  # noqa: E402
    catalog_sources,
    load_suite,
    saved_usage_summary,
    track_last_usage,
)
from stage5_metrics import score_case  # noqa: E402

pytestmark = pytest.mark.unit
SUITE = ROOT / "evaluation/business-v1"


def test_business_suite_frozen_source_positions_and_unanswerable_labels():
    manifest, cases, corpus = load_suite(SUITE)
    assert len(cases) == manifest["case_count"]
    assert len(corpus) == 5
    assert {c["kind"] for c in cases} == {"single", "followup", "multi", "unanswerable"}
    assert all(not c["human_annotation_reviewed"] for c in cases)
    assert all(
        c["reference_answer"] == "知识库中未找到足够依据。" for c in cases if not c["answerable"]
    )


def test_business_suite_rejects_changed_source_instead_of_rescoring_it(tmp_path):
    folder = tmp_path / "suite"
    shutil.copytree(SUITE, folder)
    path = folder / "corpus/standard-2026.md"
    path.write_text(path.read_text() + "\n篡改后的新结论。")
    with pytest.raises(AssertionError):
        load_suite(folder)


def test_candidate_recall_and_general_table_catalog_keep_original_positions():
    _, cases, corpus = load_suite(SUITE)
    case = next(c for c in cases if c["case_id"] == "a13")
    filename = case["evidence_units"][0]["alternatives"][0]["file"]
    source = dict(
        source_label="S1",
        document_id="test-doc",
        filename=filename,
        section_title="标准版2026回收站与导出",
        char_start=0,
        char_end=len(corpus[filename]),
        excerpt=corpus[filename],
        page_no=None,
    )
    mappings = {filename: "test-doc"}
    assert score_case(case, [source], mappings)["complete"]
    assert score_case(case, catalog_sources(case["question"], [source]), mappings)["complete"]
    # 没有拿另一版本同名主题/相同数值的位置充作本题正确证据。
    assert not score_case(case, [{**source, "document_id": "other-version"}], mappings)["complete"]


def test_labels_are_kept_in_fixture_not_embedded_in_articles():
    cases = [json.loads(line) for line in (SUITE / "cases.jsonl").read_text().splitlines()]
    _, _, corpus = load_suite(SUITE)
    assert all(c["case_id"] not in text for c in cases for text in corpus.values())


async def test_repeated_cumulative_usage_is_counted_once_per_stream():
    async def parts():
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 1}}
        yield {"text": "partial"}
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    records = []
    assert len([p async for p in track_last_usage(parts(), records)]) == 4
    assert records == [{"prompt_tokens": 10, "completion_tokens": 2}]


def test_saved_usage_includes_answer_seed_and_rewrite_once_without_invention():
    summary = saved_usage_summary(
        {
            "rows": [
                {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                    "preliminary": {"usage": {"prompt_tokens": 4, "completion_tokens": 1}},
                    "timings": {"rewrite_usage": {"prompt_tokens": 3, "completion_tokens": 1}},
                },
                {"usage": None, "preliminary": None, "timings": {}},
            ]
        }
    )
    assert summary == {"requests_with_usage": 3, "prompt_tokens": 17, "completion_tokens": 4}
