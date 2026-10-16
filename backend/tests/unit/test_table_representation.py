import pytest

from app.modules.indexing.representation import embedding_text, representation_name

pytestmark = pytest.mark.unit


def test_table_fields_link_header_value_without_changing_raw_text():
    raw = "| 操作 | 权限 |\n| --- | --- |\n| 导出账单 | owner；须先审批 |"
    assert embedding_text(raw) == raw
    represented = embedding_text(raw, "业务权限", table_context=True)
    assert "章节：业务权限" in represented
    assert "操作：导出账单；权限：owner；须先审批" in represented
    assert raw.startswith("| 操作")
    assert embedding_text("普通段落。", "章节", table_context=True) == "普通段落。"


def test_old_title_mode_and_new_table_modes_are_distinct_snapshots():
    assert representation_name(False, False) == "body-v1"
    assert representation_name(True, False) == "heading-body-v2"
    assert representation_name(False, True) == "table-rows-v2"
    assert representation_name(True, True) == "heading-table-rows-v2"


def test_row_windows_are_contiguous_original_context_but_encode_one_target(tmp_path):
    from app.modules.indexing.parser import parse
    from tests.unit.test_config import config

    raw = (
        "# 权限\n\n| 操作 | 权限 |\n| --- | --- |\n"
        "| 查看账单 | viewer+ |\n| 导出账单 | owner；须先审批 |\n"
    )
    path = tmp_path / "规则.md"
    path.write_text(raw)
    chunks = parse(path, "md", config().tokenizer_path, 800, 120, "paragraph-v2", False, True)
    assert len(chunks) == 2 and all(c["table_row"] for c in chunks)
    assert all(raw[c["char_start"] : c["char_end"]] == c["text"] for c in chunks)
    assert "查看账单" in chunks[1]["text"] and "导出账单" in chunks[1]["text"]
    represented = embedding_text(chunks[1]["text"], "权限", table_context=True, target_row=True)
    assert "导出账单" in represented and "查看账单" not in represented
