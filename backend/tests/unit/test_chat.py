import json

import httpx
import pytest

from app.infrastructure.chat_model import ChatModel, ModelFailure
from app.modules.chat.prompt import ABSTENTION, validate_answer
from tests.unit.test_config import config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text", ["没有来源", "结论 [S9]", "结论 [S1] [S99]", "结论 [S0]"])
def test_invalid_citations(text):
    with pytest.raises(ModelFailure, match="CITATION_INVALID"):
        validate_answer(text, [{"source_label": "S1"}])


def test_citations_and_fixed_abstention():
    source = {"source_label": "S1"}
    assert validate_answer("结论 [S1] [S1]", [source]) == ("answer", [source])
    assert validate_answer(ABSTENTION, []) == ("abstention", [])


async def model_with_response(text, status=200):
    model = ChatModel(config(siliconflow_api_key="test-only-model-key"))
    await model.client.aclose()
    model.client = httpx.AsyncClient(
        base_url="https://test.invalid/",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text=text)),
    )
    await model.ensure_ready()
    return model


def chunk(content=None, reason=None, usage=None):
    return (
        "data: "
        + json.dumps(
            {"choices": [{"delta": {"content": content}, "finish_reason": reason}], "usage": usage}
        )
        + "\n\n"
    )


async def test_real_tokenizer_budget_and_model_stream_protocol():
    usage = dict(prompt_tokens=12, completion_tokens=3, total_tokens=15)
    model = await model_with_response(
        chunk("你好") + chunk(reason="stop", usage=usage) + "data: [DONE]\n\n"
    )
    try:
        messages = [{"role": "user", "content": "你好，世界"}]
        count = await model.count(messages)
        assert 256 < count < 400
        result = [part async for part in model.stream(messages)]
        assert result == [{"text": "你好"}, {"usage": usage}]
    finally:
        await model.close()


@pytest.mark.parametrize(
    "response,code",
    [
        (chunk("草稿"), "MODEL_STREAM_INCOMPLETE"),
        (chunk("草稿") + "data: [DONE]\n\n", "MODEL_STREAM_INCOMPLETE"),
        (chunk("草稿") + chunk(reason="length") + "data: [DONE]\n\n", "MODEL_OUTPUT_INCOMPLETE"),
        ("data: {bad}\n\n", "MODEL_RESPONSE_INVALID"),
        ('data: {"error":{"message":"private"}}\n\n', "MODEL_RESPONSE_INVALID"),
    ],
)
async def test_stream_failure_never_fabricates_completion(response, code):
    model = await model_with_response(response)
    try:
        with pytest.raises(ModelFailure, match=code):
            [part async for part in model.stream([{"role": "user", "content": "问题"}])]
    finally:
        await model.close()


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (429, "UPSTREAM_UNAVAILABLE", True),
        (503, "UPSTREAM_UNAVAILABLE", True),
        (401, "MODEL_REQUEST_REJECTED", False),
    ],
)
async def test_provider_safe_errors(status, code, retryable):
    model = await model_with_response("private upstream response", status)
    try:
        with pytest.raises(ModelFailure, match=code) as error:
            [part async for part in model.stream([{"role": "user", "content": "问题"}])]
        assert error.value.retryable == retryable
        assert "private" not in error.value.message
    finally:
        await model.close()


def test_explicit_refusal_discards_unverified_preface_without_inventing_citations():
    from app.modules.chat.prompt import normalize_answer

    assert normalize_answer("资料没记载具体数值，" + ABSTENTION) == ABSTENTION
    assert normalize_answer("事实缺少引用") == "事实缺少引用"
    with pytest.raises(ModelFailure, match="CITATION_INVALID"):
        validate_answer(normalize_answer("猜测 [S99] " + ABSTENTION), [{"source_label": "S1"}])


@pytest.mark.parametrize(
    "value",
    [
        "直接回答 [S2]。",
        '{"question":"答案是三楼 [S2]？"}',
        '{"question":"答案是三楼。"}',
        '{"answer":"三楼"}',
    ],
)
async def test_rewrite_rejects_answers_and_old_citation_labels(value):
    model = await model_with_response(chunk(value) + chunk(reason="stop") + "data: [DONE]\n\n")
    try:
        with pytest.raises(ModelFailure, match="REWRITE_INVALID"):
            await model.rewrite([{"role": "user", "content": "培训安排？"}], "那地点呢？")
    finally:
        await model.close()


async def test_rewrite_uses_data_envelope_json_mode_and_valid_question():
    model = await model_with_response(
        chunk('{"question":"培训地点在哪里？"}') + chunk(reason="stop") + "data: [DONE]\n\n"
    )
    try:
        query, _ = await model.rewrite(
            [{"role": "assistant", "content": "历史回答 [S9]"}], "那地点呢？"
        )
        assert query == "培训地点在哪里？"
    finally:
        await model.close()


@pytest.mark.parametrize(
    "text",
    [
        "提供的资料中未提及培训费用。",
        "资料没有提供足够依据，因此无法回答。",
        "该参数在文档中未记载。",
    ],
)
def test_explicit_missing_evidence_is_conservative_abstention(text):
    from app.modules.chat.prompt import normalize_answer

    assert normalize_answer(text) == ABSTENTION
    assert normalize_answer("资料说明培训在周二。") != "知识库中未找到足够依据。"
    assert normalize_answer(text + " [S99]") == text + " [S99]"
