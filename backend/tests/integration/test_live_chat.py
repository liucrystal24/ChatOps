"""真实云端模型验证：只使用本测试生成的公开无敏感说明。"""

import json
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import select

from app.core.pagination import Cursors
from app.infrastructure.chat_model import ChatModel
from app.infrastructure.embedding import Embedding
from app.infrastructure.files import Files
from app.infrastructure.models import IndexVersion
from app.infrastructure.vectors import Vectors
from app.modules.chat.schemas import ChatInput, ConversationCreate
from app.modules.chat.service import ChatService
from app.modules.documents.schemas import Metadata
from app.modules.documents.service import DocumentService
from app.modules.indexing.service import IndexingService
from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate
from app.modules.retrieval.service import RetrievalService

pytestmark = pytest.mark.live


async def test_live_stream_followup_citations_and_abstention(bundle):
    b = bundle
    actor = str(b.users["alice"].id)
    kb = await b.knowledge.create(actor, KnowledgeBaseCreate(name="真实问答验收"))
    kb_id = str(kb.id)
    embedding, vectors, model = Embedding(b.settings), Vectors(b.settings), ChatModel(b.settings)
    files = Files(b.settings)
    documents = DocumentService(
        b.database, files, Cursors(b.settings.auth_secret.get_secret_value()), b.settings
    )
    worker = IndexingService(b.database, files, embedding, vectors, b.settings)
    chat = ChatService(
        b.database,
        RetrievalService(b.database, embedding, vectors, b.settings),
        model,
        Cursors(b.settings.auth_secret.get_secret_value()),
        b.settings,
    )
    try:
        uploaded = await documents.upload(
            actor,
            kb_id,
            UploadFile(
                BytesIO(
                    "松柏项目新员工培训安排在每周二上午十点。培训地点为三楼青松会议室。资料未记载培训费用。".encode()
                ),
                filename="培训说明.txt",
            ),
            Metadata(tags=["公开测试"]),
        )
        await worker.run_once()
        document = await documents.get(actor, str(uploaded.document.id))
        assert document.availability == "searchable"
        conv = await chat.create(actor, ConversationCreate(kb_id=kb_id))
        answers = []
        for question in ("松柏项目新人什么时候培训？", "那地点在哪里？", "培训费每人多少元？"):
            run = await chat.prepare(
                actor,
                str(conv.id),
                ChatInput(client_request_id=uuid4(), question=question),
                "live-chat",
            )
            events = [event async for event in chat.stream(run)]
            if events[-1].event != "done":
                # 仅本测试生成的公开语料；诊断产物不提交、不写应用日志。
                b.settings.files_dir.mkdir(parents=True, exist_ok=True)
                (b.settings.files_dir / "live-chat-public-response.json").write_text(
                    json.dumps(
                        {
                            "question": question,
                            "query": run.query,
                            "draft": run.text,
                            "error": events[-1].data,
                        },
                        ensure_ascii=False,
                    )
                )
            assert events[-1].event == "done", events[-1].data
            answers.append(await chat.message(actor, run.message_id))
        assert "周二" in answers[0].content and answers[0].citations
        assert "青松" in answers[1].content and answers[1].citations
        assert answers[1].timings["rewrite_status"] == "completed"
        assert answers[2].answer_type == "abstention" and not answers[2].citations
        assert all(answer.usage for answer in answers)
        await documents.delete(actor, str(uploaded.document.id))
        await worker.run_once()
    finally:
        async with b.database.transaction() as repo:
            versions = (
                await repo.session.scalars(select(IndexVersion).where(IndexVersion.kb_id == kb_id))
            ).all()
        for version in versions:
            if version.collection_name:
                await vectors.delete_version(version.collection_name, version.id)
        await embedding.close()
        await vectors.close()
        await model.close()
