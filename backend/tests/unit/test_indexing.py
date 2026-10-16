import json

import httpx
import pytest
from pydantic import ValidationError

from app.infrastructure.embedding import Embedding
from app.infrastructure.vectors import Vectors
from app.modules.indexing.errors import IndexFailure
from app.modules.indexing.parser import parse
from app.modules.retrieval.schemas import RetrievalInput
from tests.unit.test_config import config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "wrong", "data": [{"index": 0, "embedding": [1.0]}]},
        {"model": "BAAI/bge-m3", "data": []},
        {"model": "BAAI/bge-m3", "data": [{"index": 0, "embedding": [0.0, 0.0]}]},
        {"model": "BAAI/bge-m3", "data": [{"index": 0, "embedding": [float("inf")]}]},
        {"model": "BAAI/bge-m3", "data": [{"index": 0, "embedding": [True]}]},
        {"model": "BAAI/bge-m3", "data": [{"index": 1, "embedding": [1.0]}]},
    ],
)
async def test_embedding_invalid_response_never_publishes(payload):
    embedding = Embedding(config(siliconflow_api_key="test-only-private-key"))
    await embedding.client.aclose()
    # Infinity 的响应使用 JSON 文本，模拟不遵守 JSON 数值约束的外部服务。
    embedding.client = httpx.AsyncClient(
        base_url="https://provider.invalid/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=json.dumps(payload))
        ),
    )
    try:
        with pytest.raises(IndexFailure, match="EMBEDDING_RESPONSE_INVALID"):
            await embedding.embed(["private document text"])
    finally:
        await embedding.close()


@pytest.mark.parametrize(
    ("status", "retryable"), [(401, False), (403, False), (429, True), (503, True)]
)
async def test_provider_errors_are_safe_and_retry_classified(status, retryable):
    embedding = Embedding(config(siliconflow_api_key="test-private-key"))
    await embedding.client.aclose()
    embedding.client = httpx.AsyncClient(
        base_url="https://provider.invalid/",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="secret response document content")
        ),
    )
    try:
        with pytest.raises(IndexFailure) as exc:
            await embedding.embed(["private body"])
        assert exc.value.retryable == retryable
        assert "secret" not in exc.value.message
    finally:
        await embedding.close()


async def test_no_effective_versions_never_queries_qdrant():
    vectors = Vectors(config())
    calls = []
    await vectors.client.aclose()
    vectors.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    )
    try:
        assert await vectors.query({}, "kb", [], [1.0], 5) == []
        assert not calls
    finally:
        await vectors.close()


def test_chinese_chunking_covers_content_with_verifiable_positions(tmp_path):
    text = "# 安全设计\n" + "索引必须完整发布。权限必须由服务端验证。" * 100
    path = tmp_path / "sample.md"
    path.write_text(text)
    chunks = parse(path, "md", config().tokenizer_path, 100, 20)
    covered = set()
    for chunk in chunks:
        assert text[chunk["char_start"] : chunk["char_end"]] == chunk["text"]
        assert chunk["section_title"] == "安全设计"
        assert 0 < chunk["token_count"] <= 8192 and len(chunk["text"]) <= 100
        covered.update(range(chunk["char_start"], chunk["char_end"]))
    assert all(i in covered for i, char in enumerate(text) if not char.isspace())
    assert len(chunks) > 1
    # 标题和段落边界不保证恰好 overlap；连续正文应实际保留上下文交叠。
    path.write_text("0123456789" * 30)
    overlapping = parse(path, "txt", config().tokenizer_path, 100, 20)
    assert overlapping[1]["char_start"] < overlapping[0]["char_end"]


@pytest.mark.parametrize(
    "value",
    [
        {"question": "   "},
        {"question": "x", "top_k": True},
        {"question": "x", "top_k": 1.5},
        {"question": "x", "strategy": "hybrid"},
        {"question": "x", "metadata_filter": {"file_types": []}},
        {"question": "x", "metadata_filter": {"document_ids": []}},
        {"question": "x", "metadata_filter": {"kb_id": "other"}},
    ],
)
def test_retrieval_filter_cannot_expand_scope(value):
    with pytest.raises(ValidationError):
        RetrievalInput.model_validate(value)


def test_structural_chunks_preserve_each_topic_and_exact_source_offsets(tmp_path):
    text = "# 产品\n\n## 图片压缩\n\n压缩质量可配置。\n\n## 上传限制\n\n文件不得大于指定上限。\n"
    path = tmp_path / "结构.md"
    path.write_text(text)
    chunks = parse(path, "md", config().tokenizer_path, 100, 20, "paragraph-v2", True)
    quality = next(c for c in chunks if "压缩质量可配置" in c["text"])
    limit = next(c for c in chunks if "文件不得" in c["text"])
    assert quality["section_title"] == "产品 > 图片压缩"
    assert limit["section_title"] == "产品 > 上传限制"
    assert "上传限制" not in quality["text"]
    for c in chunks:
        assert text[c["char_start"] : c["char_end"]] == c["text"]
    covered = set().union(*(set(range(c["char_start"], c["char_end"])) for c in chunks))
    assert all(i in covered for i, c in enumerate(text) if not c.isspace())


def test_code_fences_do_not_change_markdown_topic_and_table_rows_remain_together(tmp_path):
    text = (
        "# 配置\n\n~~~python\n# 这不是章节\n\nprint(1)\n~~~\n\n"
        "|角色|操作|\n|---|---|\n|只读者|阅读|\n"
    )
    path = tmp_path / "代码.md"
    path.write_text(text)
    chunks = parse(path, "md", config().tokenizer_path, 300, 20, "paragraph-v2", True)
    assert all(c["section_title"] == "配置" for c in chunks)
    table = next(c for c in chunks if "只读者" in c["text"])
    assert "|角色|操作|" in table["text"]


def test_list_items_are_independent_topics_without_losing_continuation_lines(tmp_path):
    text = (
        "# 流程\n\n1. 校验权限。\n   必须检查当前成员。\n2. 设置块长度与重叠。\n3. 完整发布索引。\n"
    )
    path = tmp_path / "流程.md"
    path.write_text(text)
    chunks = parse(path, "md", config().tokenizer_path, 800, 120, "paragraph-v2")
    permission = next(c for c in chunks if "校验权限" in c["text"])
    assert "当前成员" in permission["text"] and "设置块长度" not in permission["text"]
    assert any("设置块长度" in c["text"] and "完整发布" not in c["text"] for c in chunks)


def test_document_heading_and_first_metadata_paragraph_keep_event_context(tmp_path):
    text = (
        "# 发布记录\n\n日期：2025-04-02。\n\n## 参数\n\n"
        "1. 缓存有效期为十秒。\n2. 请求超时为二十秒。\n"
    )
    path = tmp_path / "记录.md"
    path.write_text(text)
    chunks = parse(path, "md", config().tokenizer_path, 800, 120, "paragraph-v2")
    date = next(c for c in chunks if "2025-04-02" in c["text"])
    assert "# 发布记录" in date["text"]
    first = next(c for c in chunks if "缓存有效期" in c["text"])
    assert "## 参数" in first["text"] and "请求超时" not in first["text"]
    assert all(text[c["char_start"] : c["char_end"]] == c["text"] for c in chunks)
