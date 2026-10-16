import json

import pytest

from app.modules.chat.evidence import catalog, render_plan, units
from app.modules.chat.prompt import ABSTENTION, validate_answer
from app.modules.documents.markdown_tables import cells, tables

pytestmark = pytest.mark.unit


def source(text, title=None):
    return [{"source_label": "S1", "excerpt": text, "section_title": title}]


def test_general_table_keeps_headers_separator_rows_and_original_spans():
    text = (
        "套餐 | 格式 | 限额\n--- | :---: | ---:\n"
        "基础版2028 | CSV | 1200行\n企业版2028 | PDF | 2000行"
    )
    table = tables(text)[0]
    assert len(table.rows) == 2
    found = catalog("基础版2028的CSV限额？", source(text))
    assert len(found) == 1 and "1200行" in found[0]["text"]
    assert "2000行" not in found[0]["text"]
    assert len(found[0]["spans"]) == 3
    assert "\n".join(text[a:b] for a, b in found[0]["spans"]) == found[0]["text"]


@pytest.mark.parametrize(
    "text",
    [
        "| 操作 | 权限 |\n| 上传 | owner |",
        "| 操作 | 权限 |\n| --- | --- |\n| 上传 | owner | extra |",
        "````md\n```\n| 操作 | 权限 |\n| --- | --- |\n| 上传 | owner |\n````",
    ],
)
def test_partial_malformed_and_fenced_tables_do_not_invent_evidence(text):
    assert not tables(text)
    assert not units(source(text))


def test_escaped_pipe_and_even_backslash_are_not_confused():
    assert cells(r"| A\|B | viewer+ |") == [r"A\|B", "viewer+"]
    assert cells("| A\\\\|") == ["A\\\\"]


def test_explicit_scope_conflict_cannot_be_repaired_by_a_heading():
    s = source("企业版2028文件上限90 MiB。", "基础版2028说明")
    assert not catalog("基础版2028文件大小？", s)
    assert catalog("企业版2028文件大小？", source("单文件上限90 MiB。", "企业版2028说明"))
    assert not catalog("企业版2029文件大小？", source("单文件上限90 MiB。", "企业版2028说明"))


def test_multiple_requested_scopes_require_complete_evidence():
    s = source("基础版2028容量8 GiB。企业版2028容量80 GiB。")
    q = "基础版2028和企业版2028容量分别多少？"
    c = catalog(q, s)
    assert len(c) == 2
    assert render_plan(json.dumps({"evidence_ids": [c[0]["evidence_id"]]}), q, s) == ABSTENTION
    assert "80 GiB" in render_plan(
        json.dumps({"evidence_ids": [u["evidence_id"] for u in c]}), q, s
    )


def test_owner_extra_condition_cannot_use_viewer_facts_or_other_operations():
    assert not catalog("owner导出账单还需要什么条件？", source("viewer不得导出账单。"))
    assert not catalog("owner导出账单还需要什么条件？", source("owner上传资料需要先完成确认。"))
    s = source("| 操作 | 权限 |\n| --- | --- |\n| 导出账单 | owner；须先由两人审批 |")
    assert "两人审批" in catalog("owner导出账单还需要什么条件？", s)[0]["text"]
    assert catalog(
        "owner可以下载已发布PDF吗？",
        source("| 操作 | 权限 |\n| --- | --- |\n| 下载已发布PDF | viewer+ |"),
    )


@pytest.mark.parametrize(
    "text,title,expected",
    [
        ("合同签署日期为2028年3月4日。", None, False),
        ("合同于2028年3月4日签署。", None, False),
        ("合同在无锡签署。", None, True),
        ("合同签署地点为宁波。", None, True),
        ("地点：大连。", "合同签署记录", True),
        ("培训地点为北楼。", "合同签署记录", False),
        ("合同签署地点为未知。", None, False),
    ],
)
def test_signing_location_is_not_date_or_training_location(text, title, expected):
    assert bool(catalog("合同在哪里签署？", source(text, title))) is expected


def test_signing_date_does_not_refuse_a_place_name_containing_wu():
    assert catalog("合同签署日期？", source("无锡分公司合同签署日期为2028年3月4日。"))


def test_raw_source_reference_is_not_a_server_citation():
    s = {"source_label": "S1"}
    assert validate_answer("> 原文旧引用 [S99]。\n\n[S1]", [s]) == ("answer", [s])

