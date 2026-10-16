"""明确使用用户配置的真实免费 Embedding；只操作 *_test 中自己创建的资源。"""

from io import BytesIO

import pytest
from fastapi import UploadFile
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.errors import AppError
from app.core.pagination import Cursors
from app.infrastructure.embedding import Embedding
from app.infrastructure.files import Files
from app.infrastructure.vectors import Vectors
from app.modules.documents.schemas import Metadata
from app.modules.documents.service import DocumentService
from app.modules.indexing.service import IndexingService
from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate, Role
from app.modules.retrieval.schemas import MetadataFilter, RetrievalInput
from app.modules.retrieval.service import RetrievalService

pytestmark = pytest.mark.live


def text_pdf():
    writer = PdfWriter()
    for text in [
        "The observatory studies stars and planets.",
        "The telescope maintenance window is every Wednesday at 09:00 UTC.",
    ]:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def test_live_chinese_pdf_retrieval_and_permission_revocation(bundle):
    b = bundle
    assert b.settings.siliconflow_api_key, "live 必须显式提供 SILICONFLOW_API_KEY"
    actor = str(b.users["alice"].id)
    kb = await b.knowledge.create(actor, KnowledgeBaseCreate(name="真实 Embedding 验收"))
    kb_id = str(kb.id)
    files = Files(b.settings)
    documents = DocumentService(
        b.database, files, Cursors(b.settings.auth_secret.get_secret_value()), b.settings
    )
    embedding, vectors = Embedding(b.settings), Vectors(b.settings)
    worker = IndexingService(b.database, files, embedding, vectors, b.settings)
    retrieval = RetrievalService(b.database, embedding, vectors, b.settings)
    try:
        source = "松柏项目的新员工培训安排在每周二上午十点。培训地点为三楼青松会议室。"
        uploaded = await documents.upload(
            actor,
            kb_id,
            UploadFile(BytesIO(source.encode()), filename="培训说明.txt"),
            Metadata(tags=["人事"]),
        )
        assert await worker.run_once()
        doc = await documents.get(actor, str(uploaded.document.id))
        assert doc.availability == "searchable" and doc.latest_job.status == "succeeded"
        pdf = await documents.upload(
            actor,
            kb_id,
            UploadFile(BytesIO(text_pdf()), filename="observatory.pdf"),
            Metadata(tags=["天文"]),
        )
        await worker.run_once()
        pdf_doc = await documents.get(actor, str(pdf.document.id))
        assert pdf_doc.availability == "searchable"
        chinese = await retrieval.search(
            actor,
            kb_id,
            RetrievalInput(
                question="松柏项目的新人什么时候培训，在哪里？",
                top_k=1,
                metadata_filter=MetadataFilter(file_types=["txt"], tags_any=["人事"]),
            ),
        )
        assert len(chinese) == 1 and "每周二上午十点" in chinese[0]["text"]
        assert chinese[0]["document_id"] == str(doc.id)
        pdf_hits = await retrieval.search(
            actor,
            kb_id,
            RetrievalInput(
                question="When is telescope maintenance scheduled?",
                top_k=1,
                metadata_filter=MetadataFilter(document_ids=[pdf_doc.id]),
            ),
        )
        assert (
            len(pdf_hits) == 1
            and pdf_hits[0]["page_no"] == 2
            and "Wednesday" in pdf_hits[0]["text"]
        )
        chunk = await documents.chunk(actor, pdf_hits[0]["chunk_id"])
        assert chunk.page_no == 2 and chunk.text == pdf_hits[0]["text"]
        profile = await embedding.probe()
        assert profile["dimension"] == 1024
        # 同一点重复 upsert，真实 Qdrant 点数不增长。
        point_id = chinese[0]["chunk_id"]
        point = await vectors.request(
            "GET", f"/collections/{profile['collection']}/points/{point_id}"
        )
        repeat = {"id": point["id"], "vector": point["vector"], "payload": point["payload"]}
        await vectors.upsert(profile, [repeat])
        await vectors.upsert(profile, [repeat])
        assert (
            await vectors.count(profile["collection"], kb_id, str(doc.active_index_version_id)) == 1
        )
        bob = str(b.users["bob"].id)
        with pytest.raises(AppError):
            await retrieval.search(bob, kb_id, RetrievalInput(question="培训"))
        await b.knowledge.put_member(actor, kb_id, bob, Role.VIEWER)
        assert await retrieval.search(bob, kb_id, RetrievalInput(question="培训", top_k=1))
        await b.knowledge.remove_member(actor, kb_id, bob)
        with pytest.raises(AppError):
            await retrieval.search(bob, kb_id, RetrievalInput(question="培训"))
        await documents.delete(actor, str(doc.id))
        assert (
            await retrieval.search(
                actor,
                kb_id,
                RetrievalInput(question="培训", metadata_filter=MetadataFilter(file_types=["txt"])),
            )
            == []
        )
        print(
            "LIVE: model=BAAI/bge-m3 dimension=1024 chinese=ok pdf_page=2 "
            "repeated_upsert=ok revoke=ok deleted=excluded"
        )
    finally:
        async with b.database.transaction() as repo:
            records = await repo.active_versions(kb_id)
            versions = []
            # 包含已经逻辑删除、有效指针已清空的本次文档版本。
            for document_id in [str(uploaded.document.id)] + (
                [str(pdf.document.id)] if "pdf" in locals() else []
            ):
                versions.extend(await repo.document_versions(document_id))
        for version in versions:
            if version.collection_name:
                await vectors.delete_version(version.collection_name, version.id)
        await embedding.close()
        await vectors.close()
