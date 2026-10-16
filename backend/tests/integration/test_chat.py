import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.core.pagination import Cursors, Pagination
from app.infrastructure.chat_model import ModelFailure
from app.modules.chat.prompt import ABSTENTION
from app.modules.chat.schemas import ChatInput, ConversationCreate, ConversationPage
from app.modules.chat.service import ChatService, recover_expired
from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate

pytestmark = pytest.mark.integration


class TestModel:
    __test__ = False

    def __init__(self):
        self.calls = 0
        self.text = '{"evidence_ids":["E1"]}'
        self.wait = None
        self.entered = asyncio.Event()
        self.cancelled = False

    async def ensure_ready(self):
        pass

    async def count(self, messages):
        return sum(len(m["content"]) for m in messages)

    def snapshot(self):
        return {"model": "only-tests"}

    async def rewrite(self, history, question):
        return "独立问题", None

    async def stream(self, messages, max_tokens=None, *, json_mode=False):
        assert json_mode and max_tokens == 256
        self.calls += 1
        try:
            yield {"text": self.text}
            self.entered.set()
            if self.wait:
                await self.wait.wait()
        finally:
            self.cancelled = True


@pytest.fixture
async def chat(bundle):
    b = bundle
    actor = str(b.users["alice"].id)
    kb = await b.knowledge.create(actor, KnowledgeBaseCreate(name="会话测试"))
    model = TestModel()

    class Retrieval:
        calls = 0

        async def scope(self, actor, kb_id, data):
            return SimpleNamespace(versions=())

        async def search(self, actor, kb_id, data):
            self.calls += 1
            return []

    retrieval = Retrieval()
    service = ChatService(
        b.database, retrieval, model, Cursors(b.settings.auth_secret.get_secret_value()), b.settings
    )
    conversation = await service.create(actor, ConversationCreate(kb_id=kb.id))
    yield SimpleNamespace(
        b=b,
        actor=actor,
        kb_id=str(kb.id),
        model=model,
        service=service,
        conversation_id=str(conversation.id),
        retrieval=retrieval,
    )


def data(question="问题"):
    return ChatInput(client_request_id=uuid4(), question=question)


async def start(ctx, request=None):
    return await ctx.service.prepare(ctx.actor, ctx.conversation_id, request or data(), "test-chat")


async def events(service, run):
    return [event async for event in service.stream(run)]


async def test_empty_context_abstention_real_persistence_idempotency_and_acl(chat):
    c = chat
    request = data()
    run = await start(c, request)
    stream = await events(c.service, run)
    assert [e.event for e in stream] == ["start", "sources", "delta", "done"]
    assert c.model.calls == 0
    assert stream[-1].data["answer_type"] == "abstention"
    result = await c.service.message(c.actor, run.message_id)
    assert result.content == ABSTENTION and result.status == "completed"
    with pytest.raises(AppError) as error:
        await start(c, request)
    assert error.value.code == "REQUEST_ALREADY_EXISTS"
    assert error.value.details["message_id"] == run.message_id
    for operation in [
        c.service.get(str(c.b.users["bob"].id), c.conversation_id),
        c.service.message(str(c.b.users["admin"].id), run.message_id),
    ]:
        with pytest.raises(AppError, match="RESOURCE_NOT_FOUND"):
            await operation
    assert len((await c.service.messages(c.actor, c.conversation_id, Pagination())).items) == 2
    assert not (await c.service.list(str(c.b.users["bob"].id), ConversationPage())).items


async def test_concurrent_generation_slot_and_expired_recovery(chat):
    c = chat
    results = await asyncio.gather(start(c), start(c), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert [r.code for r in results if isinstance(r, AppError)] == ["CONVERSATION_BUSY"]
    winner = next(r for r in results if not isinstance(r, Exception))
    async with c.b.database.transaction() as repo:
        message = await repo.message(winner.message_id)
        message.generation_deadline_at = await repo.now() - timedelta(seconds=1)
        message.content = "崩溃前草稿"
    await recover_expired(c.b.database)
    result = await c.service.message(c.actor, winner.message_id)
    assert result.status == "interrupted" and result.content == "崩溃前草稿"
    assert not (await c.service.get(c.actor, c.conversation_id)).active_message_id
    assert [e.event for e in await events(c.service, winner)] == ["error"]
    new = await start(c)
    assert new.sequence == 4
    await c.service.finish(new, "cancelled")


async def test_disconnect_releases_slot_and_excludes_failed_history(chat):
    c = chat
    run = await start(c)
    stream = c.service.stream(run)
    assert (await anext(stream)).event == "start"
    run.text = "仅草稿"
    await stream.aclose()
    result = await c.service.message(c.actor, run.message_id)
    assert result.status == "cancelled" and result.content == "仅草稿"
    next_run = await start(c)
    assert await c.service.history(next_run) == []
    await c.service.finish(next_run, "cancelled")


async def test_missing_model_is_json_before_http_stream_and_duplicate_contract(chat):
    c = chat
    model = c.b.client.app.state.chat_service.model
    configured = model.configured
    model.configured = False
    try:
        response = c.b.client.post(
            f"/api/v1/conversations/{c.conversation_id}/messages:stream",
            json=data().model_dump(mode="json"),
            headers=c.b.headers["alice"],
        )
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
    finally:
        model.configured = configured
    async with c.b.database.transaction() as repo:
        assert not await repo.messages(c.conversation_id)


async def test_guarded_publish_failure_does_not_send_done(chat):
    c = chat
    run = await start(c)

    async def failure(*args):
        raise ModelFailure("UPSTREAM_TIMEOUT", "模型响应超时", True)

    c.retrieval.search = failure
    result = await events(c.service, run)
    assert result[-1].event == "error" and result[-1].data["code"] == "UPSTREAM_TIMEOUT"
    assert (await c.service.message(c.actor, run.message_id)).status == "failed"


async def test_generation_cannot_switch_to_other_conversation_fk(chat):
    from sqlalchemy.exc import IntegrityError

    c = chat
    first = await start(c)
    second = await c.service.create(c.actor, ConversationCreate(kb_id=c.kb_id))
    with pytest.raises(IntegrityError):
        async with c.b.database.transaction() as repo:
            conversation = await repo.conversation(str(second.id), lock=True)
            conversation.active_message_id = first.message_id
    await c.service.finish(first, "cancelled")


# 复用真实 MySQL/Qdrant 索引 fixture，模型替身只出现在此测试文件。
from tests.integration.test_indexing import indexing as indexing_resource  # noqa: E402
from tests.integration.test_indexing import upload  # noqa: E402


@pytest.fixture(name="indexing")
async def indexed_fixture(bundle):
    async for resource in indexing_resource.__wrapped__(bundle):
        yield resource


async def indexed_chat(indexing, text="培训在周二十点，地点为三楼会议室。", filename="资料.txt"):
    from app.modules.retrieval.service import RetrievalService

    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    uploaded = await upload(indexing, text, filename)
    await worker.run_once()
    model = TestModel()
    service = ChatService(
        b.database,
        RetrievalService(b.database, embedding, vectors, b.settings),
        model,
        Cursors(b.settings.auth_secret.get_secret_value()),
        b.settings,
    )
    conversation = await service.create(actor, ConversationCreate(kb_id=kb_id))
    c = SimpleNamespace(
        b=b,
        actor=actor,
        kb_id=kb_id,
        service=service,
        model=model,
        conversation_id=str(conversation.id),
        documents=documents,
        document_id=str(uploaded.document.id),
        worker=worker,
    )
    return c


async def test_answer_citations_rebuild_retention_and_deleted_source(indexing):
    c = await indexed_chat(indexing)
    run = await start(c)
    result = await events(c.service, run)
    assert result[-1].event == "done", result[-1].data
    stored = await c.service.message(c.actor, run.message_id)
    assert (
        stored.status == "completed"
        and len(stored.citations) == 1
        and stored.citations[0].available
    )
    old_chunk = str(stored.citations[0].chunk_id)
    followup = await start(c)
    assert len(await c.service.history(followup)) == 2
    await c.service.finish(followup, "cancelled")
    await c.documents.reindex(c.actor, c.document_id)
    await c.worker.run_once()
    await c.worker.maintain()
    async with c.b.database.transaction() as repo:
        assert await repo.chunk(old_chunk), "引用片段不能被退休版本清理删除"
    stored = await c.service.message(c.actor, run.message_id)
    assert not stored.citations[0].available and stored.citations[0].filename is None
    with pytest.raises(AppError):
        await c.documents.chunk(c.actor, old_chunk)
    new = await start(c)
    assert await c.service.history(new) == []
    await c.service.finish(new, "cancelled")
    await c.documents.delete(c.actor, c.document_id)
    await c.worker.run_once()
    assert not (await c.service.message(c.actor, run.message_id)).citations[0].available


@pytest.mark.parametrize("invalid", ["无依据答案", '{"evidence_ids":["E999"]}'])
async def test_invalid_citation_fails_and_is_not_in_history(indexing, invalid):
    c = await indexed_chat(indexing)
    c.model.text = invalid
    run = await start(c)
    result = await events(c.service, run)
    assert result[-1].event == "error" and result[-1].data["code"] == "ANSWER_PLAN_INVALID"
    assert not any(e.event == "done" for e in result)
    assert not any(e.event == "delta" for e in result)
    stored = await c.service.message(c.actor, run.message_id)
    assert stored.status == "failed" and not stored.citations


async def test_permission_revoked_while_provider_idle_cancels_without_more_output(indexing):
    from app.modules.knowledge_bases.schemas import Role

    c = await indexed_chat(indexing)
    bob = str(c.b.users["bob"].id)
    await c.b.knowledge.put_member(c.actor, c.kb_id, bob, Role.OWNER)
    c.model.wait = asyncio.Event()
    run = await start(c)
    stream = c.service.stream(run)
    names = []
    while True:
        event = await anext(stream)
        names.append(event.event)
        if event.event == "sources":
            break
    await asyncio.wait_for(c.model.entered.wait(), 3)
    await c.b.knowledge.remove_member(bob, c.kb_id, c.actor)
    final = await asyncio.wait_for(anext(stream), 3)
    assert final.event == "error" and final.data["code"] == "RESOURCE_NOT_FOUND"
    await stream.aclose()
    assert c.model.cancelled
    async with c.b.database.transaction() as repo:
        stored = await repo.message(run.message_id)
        assert stored.status == "failed" and not await repo.citations(stored.id)


async def test_source_deleted_during_generation_stops_output(indexing):
    c = await indexed_chat(indexing)
    c.model.wait = asyncio.Event()
    run = await start(c)
    stream = c.service.stream(run)
    while (await anext(stream)).event != "sources":
        pass
    await asyncio.wait_for(c.model.entered.wait(), 3)
    await c.documents.delete(c.actor, c.document_id)
    final = await asyncio.wait_for(anext(stream), 3)
    assert final.event == "error" and final.data["code"] == "SOURCE_CHANGED"
    await stream.aclose()
    assert c.model.cancelled


async def test_post_sse_http_start_idempotency_and_history(chat):
    c = chat
    request = data()
    response = c.b.client.post(
        f"/api/v1/conversations/{c.conversation_id}/messages:stream",
        headers=c.b.headers["alice"],
        json=request.model_dump(mode="json"),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: done" in response.text and "event: error" not in response.text
    duplicate = c.b.client.post(
        f"/api/v1/conversations/{c.conversation_id}/messages:stream",
        headers=c.b.headers["alice"],
        json=request.model_dump(mode="json"),
    )
    assert (
        duplicate.status_code == 409
        and duplicate.json()["error"]["code"] == "REQUEST_ALREADY_EXISTS"
    )
    unreadable = c.b.client.get(
        f"/api/v1/conversations/{c.conversation_id}/messages", headers=c.b.headers["bob"]
    )
    assert unreadable.status_code == 404


async def test_same_request_concurrency_inserts_one_turn(chat):
    c = chat
    request = data()
    results = await asyncio.gather(start(c, request), start(c, request), return_exceptions=True)
    assert [r.code for r in results if isinstance(r, AppError)] == ["REQUEST_ALREADY_EXISTS"]
    winner = next(r for r in results if not isinstance(r, Exception))
    async with c.b.database.transaction() as repo:
        assert len(await repo.messages(c.conversation_id)) == 2
    await c.service.finish(winner, "cancelled")


async def test_commit_failure_rolls_back_citations_and_never_sends_done(indexing):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.infrastructure.models import Message

    c = await indexed_chat(indexing)
    run = await start(c)
    tripped = False

    def reject_commit(session):
        nonlocal tripped
        if not tripped and any(
            isinstance(m, Message) and m.id == run.message_id and m.status == "completed"
            for m in session.identity_map.values()
        ):
            tripped = True
            raise RuntimeError("test-only-commit-rejection")

    event.listen(Session, "before_commit", reject_commit)
    try:
        stream = await events(c.service, run)
    finally:
        event.remove(Session, "before_commit", reject_commit)
    assert tripped and stream[-1].event == "error" and not any(e.event == "done" for e in stream)
    message = await c.service.message(c.actor, run.message_id)
    assert message.status == "failed" and not message.citations


async def test_explicit_refusal_is_stored_without_draft_facts_or_citations(indexing):
    c = await indexed_chat(indexing)
    c.model.text = '{"evidence_ids":[]}'
    run = await start(c)
    result = await events(c.service, run)
    assert result[-1].event == "done" and result[-1].data["answer_type"] == "abstention"
    stored = await c.service.message(c.actor, run.message_id)
    assert stored.content == ABSTENTION and not stored.citations


async def test_signing_location_missing_refuses_before_model_and_persists_no_citation(indexing):
    c = await indexed_chat(indexing)
    run = await start(c, data("培训合同在哪里签署？"))
    result = await events(c.service, run)
    assert result[-1].event == "done" and result[-1].data["answer_type"] == "abstention"
    assert c.model.calls == 0
    stored = await c.service.message(c.actor, run.message_id)
    assert stored.content == ABSTENTION and not stored.citations
    assert stored.timings["answer_mode"] == "extractive-v2"


async def test_general_table_answer_is_original_row_and_source_is_current(indexing):
    raw = "# 基础版2028导出\n\n| 项目 | 限额 |\n| --- | --- |\n| 资产CSV | 1200行 |"
    c = await indexed_chat(indexing, raw, "导出.md")
    run = await start(c, data("基础版2028资产CSV导出限额多少？"))
    result = await events(c.service, run)
    assert result[-1].event == "done", result[-1].data
    stored = await c.service.message(c.actor, run.message_id)
    assert stored.status == "completed" and "1200行" in stored.content
    assert "| 项目 | 限额 |" in stored.content and "| --- | --- |" in stored.content
    assert len(stored.citations) == 1 and stored.citations[0].available
    chunk = await c.documents.chunk(c.actor, str(stored.citations[0].chunk_id))
    assert raw[chunk.char_start : chunk.char_end] == chunk.text
