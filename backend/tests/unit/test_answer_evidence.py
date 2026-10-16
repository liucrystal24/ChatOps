import json

import pytest

from app.infrastructure.chat_model import ModelFailure
from app.modules.chat.evidence import catalog, render_plan, units
from app.modules.chat.prompt import ABSTENTION

pytestmark = pytest.mark.unit


def source(text):
    return [{"source_label": "S1", "excerpt": text}]


def test_event_dates_require_same_event_and_keep_real_signing_positive():
    s = source("资料更新日期2026-04-02。合同签署日期2025-03-01。")
    c = catalog("合同签署日期是哪天？", s)
    assert len(c) == 1 and "2025-03-01" in c[0]["text"]
    assert not catalog("合同签订日期？", source("交付日期2026-04-02。"))
    assert not catalog("合同签署日期？", source("合同尚未签署，文件更新于2026-04-02。"))
    assert not catalog("合同签署日期？", source("## 合同签署\n更新日期：2026-04-02。"))
    assert "2025-03-01" in render_plan(
        json.dumps({"evidence_ids": [c[0]["evidence_id"]]}), "合同签署日期？", s
    )


def test_capacity_keeps_original_unit_and_requires_both_scopes():
    s = source("每库最多200个文件。每库累计容量500 MiB；文件卷容量2 GiB。单文件10 MB。")
    c = catalog("每个知识库文件累计和文件卷限制是多少？", s)
    assert len(c) == 3 and all("200个" not in u["text"] for u in c)
    raw = json.dumps({"evidence_ids": [c[0]["evidence_id"]]})
    assert render_plan(raw, "每个知识库文件累计和文件卷限制是多少？", s) == ABSTENTION
    answer = render_plan(
        json.dumps({"evidence_ids": [u["evidence_id"] for u in c]}),
        "每个知识库文件累计和文件卷限制是多少？",
        s,
    )
    assert "500 MiB" in answer and "2 GiB" in answer and "10 MB" in answer
    assert "10 MiB" not in answer


@pytest.mark.parametrize("reverse", [False, True])
def test_permission_header_row_binding_independent_of_order(reverse):
    rows = ["| owner | 查看成员 |", "| viewer+ | 查看知识库 |"]
    if reverse:
        rows.reverse()
    s = source("| 权限 | 操作 |\n| --- | --- |\n" + "\n".join(rows))
    c = catalog("知识库中viewer角色能做什么？", s)
    assert len(c) == 1 and "查看知识库" in c[0]["text"]
    assert "权限 | 操作" in c[0]["text"] and "查看成员" not in c[0]["text"]
    assert len(catalog("owner 能做什么？", s)) == 2


def test_missing_header_and_escaped_pipe_never_invent_role_column():
    assert not units(source("| 查看成员 | owner |"))
    s = source("| 操作 | 权限 |\n| --- | --- |\n| 查看 A\\|B | viewer+ |")
    assert len(catalog("viewer 能做什么？", s)) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        '{"evidence_ids":["E999"]}',
        '{"evidence_ids":["E1","E1"]}',
        '{"evidence_ids":[1]}',
        '{"evidence_ids":[],"answer":"编造答案"}',
    ],
)
def test_invalid_plan_fails_instead_of_repairing_or_inventing_citations(raw):
    with pytest.raises(ModelFailure, match="ANSWER_PLAN_INVALID"):
        render_plan(raw, "问题", source("培训周二十点。"))


def test_render_only_exact_original_spans_with_server_owned_citation():
    s = source("培训时间周二十点；地点三楼。")
    assert render_plan('{"evidence_ids":["E2"]}', "地点？", s) == "> 地点三楼。\n\n[S1]"
    assert render_plan('{"evidence_ids":[]}', "问题", s) == ABSTENTION


def test_semicolon_conditions_stay_bound_but_independent_time_attributes_separate():
    s = source("任务最多执行3次；仅瞬时错误重试，永久格式错误直接失败。")
    assert len(units(s)) == 1 and "永久格式错误" in units(s)[0]["text"]
    s = source("Qdrant只返回定位payload；按ID从MySQL加载正文。")
    assert len(units(s)) == 1
    s = source("任务总超时15分钟；Worker租约90秒，每20秒续租。")
    c = catalog("Worker租约多久、多久续租？", s)
    assert len(c) == 1 and "15分钟" not in c[0]["text"]
