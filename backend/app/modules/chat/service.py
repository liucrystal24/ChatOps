import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic
from uuid import uuid4

import anyio
from fastapi.sse import ServerSentEvent
from sqlalchemy import select

from app.core.errors import AppError
from app.infrastructure.chat_model import ModelFailure
from app.infrastructure.models import Citation, Conversation, Message
from app.modules.chat.prompt import ABSTENTION, messages_for, normalize_answer, validate_answer
from app.modules.chat.schemas import (
    CitationResponse,
    ConversationList,
    ConversationResponse,
    MessageList,
    MessageResponse,
)
from app.modules.identity.service import active_user
from app.modules.indexing.errors import IndexFailure
from app.modules.knowledge_bases.service import access
from app.modules.retrieval.schemas import RetrievalInput

logger = logging.getLogger("chat_rag.chat")


def missing():
    return AppError(404, "RESOURCE_NOT_FOUND", "会话不存在或不可访问")


async def conversation_access(repo, actor, conversation_id, *, lock=False):
    conversation = await repo.conversation(conversation_id, lock=lock)
    if not conversation or conversation.user_id != actor:
        raise missing()
    await access(repo, actor, conversation.kb_id)
    return conversation


async def source_current(repo, kb_id, source):
    document = await repo.document(str(source["document_id"]))
    return bool(
        document
        and document.kb_id == kb_id
        and document.deleted_at is None
        and document.active_index_version_id == str(source["index_version_id"])
    )


@dataclass
class Generation:
    actor: str
    conversation_id: str
    kb_id: str
    user_message_id: str
    message_id: str
    sequence: int
    data: object
    request_id: str
    started: float = field(default_factory=monotonic)
    text: str = ""
    sources: list = field(default_factory=list)
    query: str | None = None
    usage: dict | None = None
    timings: dict = field(default_factory=dict)
    finalized: bool = False
    authenticate: object = None
    disconnected: object = None


async def recover_expired(database):
    async with database.transaction() as repo:
        await repo.lock_authorization()
        for conversation in await repo.expired_conversations():
            message = await repo.message(conversation.active_message_id)
            message.status, message.error_code = "interrupted", "GENERATION_INTERRUPTED"
            message.completed_at = await repo.now()
            conversation.active_message_id = None


class ChatService:
    def __init__(self, database, retrieval, model, cursors, settings):
        self.database, self.retrieval, self.model = database, retrieval, model
        self.cursors, self.settings = cursors, settings

    async def create(self, actor, data):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            await access(repo, actor, str(data.kb_id))
            conversation = await repo.save(
                Conversation(
                    id=str(uuid4()),
                    user_id=actor,
                    kb_id=str(data.kb_id),
                    title=data.title,
                    active_message_id=None,
                )
            )
            return ConversationResponse.model_validate(conversation, from_attributes=True)

    async def list(self, actor, page):
        scope = f"conversations:{actor}:{page.kb_id}"
        position = self.cursors.decode(page.cursor, scope)
        async with self.database.transaction() as repo:
            await active_user(repo, actor)
            if page.kb_id:
                await access(repo, actor, str(page.kb_id))
            rows = await repo.conversations(
                actor, str(page.kb_id) if page.kb_id else None, page.limit + 1, position
            )
            return ConversationList(
                items=[
                    ConversationResponse.model_validate(c, from_attributes=True)
                    for c in rows[: page.limit]
                ],
                next_cursor=self.cursor(rows, page, scope),
            )

    def cursor(self, rows, page, scope):
        last = rows[page.limit - 1] if len(rows) > page.limit else None
        return self.cursors.encode(scope, last.created_at, last.id) if last else None

    async def get(self, actor, conversation_id):
        await recover_expired(self.database)
        async with self.database.transaction() as repo:
            conversation = await conversation_access(repo, actor, conversation_id)
            return ConversationResponse.model_validate(conversation, from_attributes=True)

    async def message_response(self, repo, message, kb_id):
        citations = []
        for citation in await repo.citations(message.id):
            current = await source_current(
                repo,
                kb_id,
                dict(document_id=citation.document_id, index_version_id=citation.index_version_id),
            )
            locator = citation.locator_snapshot if current else {}
            citations.append(
                CitationResponse(
                    source_label=citation.source_label,
                    chunk_id=citation.chunk_id,
                    document_id=citation.document_id,
                    index_version_id=citation.index_version_id,
                    available=current,
                    filename=locator.get("filename"),
                    page_no=locator.get("page_no"),
                    section_title=locator.get("section_title"),
                )
            )
        values = {
            name: getattr(message, name)
            for name in MessageResponse.model_fields
            if name != "citations"
        }
        return MessageResponse(**values, citations=citations)

    async def messages(self, actor, conversation_id, page):
        await recover_expired(self.database)
        scope = f"messages:{actor}:{conversation_id}"
        position = self.cursors.decode(page.cursor, scope)
        async with self.database.transaction() as repo:
            conversation = await conversation_access(repo, actor, conversation_id)
            rows = await repo.messages(conversation_id, page.limit + 1, position)
            return MessageList(
                items=[
                    await self.message_response(repo, m, conversation.kb_id)
                    for m in rows[: page.limit]
                ],
                next_cursor=self.cursor(rows, page, scope),
            )

    async def message(self, actor, message_id):
        await recover_expired(self.database)
        async with self.database.transaction() as repo:
            message = await repo.message(message_id)
            if not message:
                raise missing()
            conversation = await conversation_access(repo, actor, message.conversation_id)
            return await self.message_response(repo, message, conversation.kb_id)

    async def prepare(
        self, actor, conversation_id, data, request_id, authenticate=None, disconnected=None
    ):
        # dependency 在 HTTP 200 开始前执行；外部调用和 tokenizer 加载不占数据库事务。
        await self.get(actor, conversation_id)
        await self.model.ensure_ready()
        if (
            await self.model.count(messages_for([], data.question, []))
            + self.settings.chat_output_tokens
            > self.settings.chat_context_tokens
        ):
            raise AppError(422, "CONTEXT_TOKEN_LIMIT", "问题超过聊天模型 token 预算")
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            conversation = await conversation_access(repo, actor, conversation_id, lock=True)
            existing = await repo.request_message(conversation_id, str(data.client_request_id))
            if existing:
                answer = await repo.session.scalar(
                    select(Message).where(Message.reply_to_message_id == existing.id)
                )
                raise AppError(
                    409,
                    "REQUEST_ALREADY_EXISTS",
                    "该请求已处理，请读取已有消息",
                    details={"user_message_id": existing.id, "message_id": answer.id},
                )
            if conversation.active_message_id:
                raise AppError(
                    409,
                    "CONVERSATION_BUSY",
                    "本会话正在生成，请等待完成或停止后重试",
                    details={"message_id": conversation.active_message_id},
                )
            now = await repo.now()
            user_id, message_id = str(uuid4()), str(uuid4())
            user = Message(
                id=user_id,
                conversation_id=conversation_id,
                sequence_no=conversation.next_sequence_no,
                role="user",
                status="completed",
                content=data.question,
                client_request_id=str(data.client_request_id),
                completed_at=now,
                created_at=now,
            )
            await repo.save(user)
            assistant = Message(
                id=message_id,
                conversation_id=conversation_id,
                sequence_no=conversation.next_sequence_no + 1,
                role="assistant",
                status="generating",
                reply_to_message_id=user_id,
                content="",
                retrieval_config=data.retrieval.model_dump(mode="json"),
                model_config=self.model.snapshot(),
                generation_deadline_at=now + timedelta(seconds=self.settings.chat_deadline_seconds),
                heartbeat_at=now,
                created_at=now + timedelta(microseconds=1),
            )
            await repo.save(assistant)
            conversation.next_sequence_no += 2
            conversation.active_message_id = message_id
            return Generation(
                actor,
                conversation_id,
                conversation.kb_id,
                user_id,
                message_id,
                assistant.sequence_no,
                data,
                request_id,
                authenticate=authenticate,
                disconnected=disconnected,
            )

    async def check(self, run, *, checkpoint=False, allow_completed=False):
        if run.authenticate:
            run.authenticate()
        async with self.database.transaction() as repo:
            if checkpoint:
                await repo.lock_authorization()
            conversation = await conversation_access(
                repo, run.actor, run.conversation_id, lock=checkpoint
            )
            message = await repo.message(run.message_id)
            completed = allow_completed and message.status == "completed"
            if not completed and (
                conversation.active_message_id != run.message_id or message.status != "generating"
            ):
                raise ModelFailure("GENERATION_INTERRUPTED", "生成任务已中断")
            if not completed and message.generation_deadline_at <= await repo.now():
                raise ModelFailure("GENERATION_TIMEOUT", "问答超过总时间限制", True)
            for source in run.sources:
                if not await source_current(repo, run.kb_id, source):
                    raise ModelFailure("SOURCE_CHANGED", "生成期间来源已删除或更新，请重新提问")
            if checkpoint and not completed:
                message.content, message.retrieval_query = run.text, run.query
                message.heartbeat_at = await repo.now()
                message.timings, message.usage = dict(run.timings), run.usage

    async def history(self, run):
        async with self.database.transaction() as repo:
            await conversation_access(repo, run.actor, run.conversation_id)
            history = []
            for assistant in reversed(
                await repo.history_pairs(run.conversation_id, run.sequence - 1)
            ):
                citations = await repo.citations(assistant.id)
                if any(
                    [
                        not await source_current(
                            repo,
                            run.kb_id,
                            dict(document_id=c.document_id, index_version_id=c.index_version_id),
                        )
                        for c in citations
                    ]
                ):
                    continue
                user = await repo.message(assistant.reply_to_message_id)
                history += [
                    {"role": "user", "content": user.content},
                    {"role": "assistant", "content": assistant.content},
                ]
        while (
            history
            and await self.model.count(messages_for(history, run.data.question, []))
            > self.settings.chat_context_tokens // 2
        ):
            history = history[2:]
        return history

    async def monitor(self, run):
        while True:
            await asyncio.sleep(1)
            if run.finalized:
                return
            if run.disconnected and await run.disconnected():
                raise ModelFailure("CLIENT_DISCONNECTED", "客户端连接已关闭")
            await self.check(run, checkpoint=True, allow_completed=True)

    async def finish(self, run, status, code=None, answer_type=None, citations=()):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            conversation = await repo.conversation(run.conversation_id, lock=True)
            message = await repo.message(run.message_id)
            if (
                not conversation
                or conversation.active_message_id != run.message_id
                or message.status != "generating"
            ):
                if status == "completed":
                    raise ModelFailure("GENERATION_INTERRUPTED", "生成任务已中断")
                return
            if status == "completed":
                await conversation_access(repo, run.actor, run.conversation_id)
                if message.generation_deadline_at <= await repo.now():
                    raise ModelFailure("GENERATION_TIMEOUT", "问答超过总时间限制", True)
                for source in run.sources:
                    if not await source_current(repo, run.kb_id, source):
                        raise ModelFailure("SOURCE_CHANGED", "来源已变更，请重新提问")
                for source in citations:
                    await repo.save(
                        Citation(
                            id=str(uuid4()),
                            message_id=message.id,
                            source_label=source["source_label"],
                            document_id=source["document_id"],
                            index_version_id=source["index_version_id"],
                            chunk_id=source["chunk_id"],
                            locator_snapshot={
                                k: source[k]
                                for k in (
                                    "filename",
                                    "page_no",
                                    "section_title",
                                    "char_start",
                                    "char_end",
                                )
                            },
                            retrieval_score=source["retrieval_score"],
                        )
                    )
            message.status, message.content, message.error_code = status, run.text, code
            message.answer_type, message.retrieval_query = answer_type, run.query
            message.timings, message.usage = dict(run.timings), run.usage
            message.completed_at = await repo.now()
            conversation.active_message_id = None
        run.finalized = True

    async def produce(self, run, queue):
        async def emit(name, data):
            await self.check(run)
            await queue.put((name, data))

        async with asyncio.timeout(self.settings.chat_deadline_seconds):
            history = await self.history(run)
            run.query = run.data.question
            run.timings["rewrite_status"] = "not_needed"
            # 历史只用于消解追问；空权限/有效版本范围不调用任何模型。
            scope = await self.retrieval.scope(
                run.actor,
                run.kb_id,
                RetrievalInput(question=run.query, **run.data.retrieval.model_dump()),
            )
            if history and scope.versions:
                started = monotonic()
                try:
                    run.query, rewrite_usage = await self.model.rewrite(history, run.data.question)
                    run.timings["rewrite_status"] = "completed"
                    run.timings["rewrite_usage"] = rewrite_usage
                except (ModelFailure, TimeoutError):
                    run.timings["rewrite_status"] = "fallback_original"
                    logger.info("query_rewrite_fallback")
                run.timings["rewrite_ms"] = round((monotonic() - started) * 1000, 2)
            started = monotonic()
            hits = await self.retrieval.search(
                run.actor,
                run.kb_id,
                RetrievalInput(question=run.query, **run.data.retrieval.model_dump()),
            )
            run.timings["retrieval_ms"] = round((monotonic() - started) * 1000, 2)
            # 改写已解决追问指代，生成只使用本轮证据，避免把历史结论误作新来源的事实。
            if run.timings["rewrite_status"] == "completed":
                history = []
            answer_question = run.query
            sources = []
            for hit in hits:
                source = dict(
                    source_label=f"S{len(sources) + 1}",
                    chunk_id=hit["chunk_id"],
                    document_id=hit["document_id"],
                    index_version_id=hit["index_version_id"],
                    filename=hit["filename"],
                    page_no=hit["page_no"],
                    section_title=hit["section_title"],
                    char_start=hit["char_start"],
                    char_end=hit["char_end"],
                    excerpt=hit["text"][:2000],
                    retrieval_score=hit["score"],
                    rerank_score=None,
                )
                candidate = [*sources, source]
                while (
                    history
                    and await self.model.count(messages_for(history, answer_question, candidate))
                    + self.settings.chat_output_tokens
                    > self.settings.chat_context_tokens
                ):
                    history = history[2:]
                if (
                    await self.model.count(messages_for(history, answer_question, candidate))
                    + self.settings.chat_output_tokens
                    <= self.settings.chat_context_tokens
                ):
                    sources.append(source)
            run.sources = sources
            await emit("sources", {"candidates": sources})
            if not sources:
                run.text = ABSTENTION
                run.timings["first_token_ms"] = round((monotonic() - run.started) * 1000, 2)
                await emit("delta", {"text": ABSTENTION})
            else:
                async for part in self.model.stream(
                    messages_for(history, answer_question, sources)
                ):
                    if "usage" in part:
                        run.usage = part["usage"]
                    if part.get("text"):
                        if not run.text:
                            run.timings["first_token_ms"] = round(
                                (monotonic() - run.started) * 1000, 2
                            )
                        run.text += part["text"]
                        if len(run.text) > 50000:
                            raise ModelFailure("MODEL_RESPONSE_INVALID", "答案超过长度限制")
                        await emit("delta", {"text": part["text"]})
            run.text = normalize_answer(run.text)
            answer_type, citations = validate_answer(run.text, sources)
            run.timings["total_ms"] = round((monotonic() - run.started) * 1000, 2)
            await self.finish(run, "completed", answer_type=answer_type, citations=citations)
            await queue.put(
                (
                    "done",
                    dict(
                        message_id=run.message_id,
                        status="completed",
                        answer_type=answer_type,
                        citations=citations,
                        usage=run.usage,
                        timings=run.timings,
                    ),
                )
            )

    async def stream(self, run):
        queue = asyncio.Queue(maxsize=1)
        producer = monitor = getter = None
        sequence = 0
        try:
            await self.check(run)
            sequence += 1
            yield ServerSentEvent(
                event="start",
                id=str(sequence),
                data=dict(
                    request_id=run.request_id,
                    user_message_id=run.user_message_id,
                    message_id=run.message_id,
                ),
            )
            producer = asyncio.create_task(self.produce(run, queue))
            monitor = asyncio.create_task(self.monitor(run))
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    [getter, producer, monitor], return_when=asyncio.FIRST_COMPLETED
                )
                for task in (producer, monitor):
                    if task in done and not task.cancelled() and task.exception():
                        raise task.exception()
                if getter not in done:
                    # 正常结束仍需消费 done；monitor 只在保存后正常结束。
                    if queue.empty() and producer.done():
                        raise ModelFailure("GENERATION_INTERRUPTED", "问答流提前结束")
                    name, data = await getter
                else:
                    name, data = getter.result()
                getter = None
                await self.check(run, allow_completed=True)
                sequence += 1
                yield ServerSentEvent(event=name, id=str(sequence), data=data)
                if name == "done":
                    return
        except (AppError, ModelFailure, IndexFailure, TimeoutError) as error:
            code = getattr(error, "code", "GENERATION_TIMEOUT")
            message = getattr(error, "message", "问答超过总时间限制")
            await self.safe_finish(
                run, "cancelled" if code == "CLIENT_DISCONNECTED" else "failed", code
            )
            sequence += 1
            yield ServerSentEvent(
                event="error",
                id=str(sequence),
                data=dict(
                    code=code,
                    message=message,
                    retryable=getattr(error, "retryable", False),
                    message_id=run.message_id,
                    request_id=run.request_id,
                ),
            )
        except Exception:
            await self.safe_finish(run, "failed", "GENERATION_FAILED")
            sequence += 1
            yield ServerSentEvent(
                event="error",
                id=str(sequence),
                data=dict(
                    code="GENERATION_FAILED",
                    message="问答失败，草稿未标记成功",
                    retryable=True,
                    message_id=run.message_id,
                    request_id=run.request_id,
                ),
            )
        finally:
            # 先中止供应商连接，再以 shield 确保 ASGI 断开取消不会跳过终态保存。
            with anyio.CancelScope(shield=True):
                for task in (producer, monitor, getter):
                    if task and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *(t for t in (producer, monitor, getter) if t), return_exceptions=True
                )
                if not run.finalized:
                    await self.safe_finish(run, "cancelled", "CLIENT_DISCONNECTED")

    async def safe_finish(self, run, status, code):
        if run.finalized:
            return
        with anyio.CancelScope(shield=True):
            try:
                async with asyncio.timeout(5):
                    await self.finish(run, status, code)
            except Exception:
                # MySQL 故障时保留 generating，由 deadline 巡检收敛，不发送虚假 done。
                logger.warning("generation_finalization_unavailable")
