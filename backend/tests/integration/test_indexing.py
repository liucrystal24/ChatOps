import asyncio
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile
from pypdf import PdfWriter
from sqlalchemy import select

from app.core.errors import AppError
from app.core.pagination import Cursors
from app.infrastructure.files import Files
from app.infrastructure.models import Chunk, Job
from app.infrastructure.vectors import Vectors
from app.modules.documents.schemas import DocumentPatch, Metadata
from app.modules.documents.service import DocumentService
from app.modules.indexing.errors import IndexFailure, LeaseLost
from app.modules.indexing.service import IndexingService
from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate
from app.modules.retrieval.schemas import MetadataFilter, RetrievalInput
from app.modules.retrieval.service import RetrievalService

pytestmark = pytest.mark.integration


class TestEmbedding:
    """仅测试用三维向量；真实模型验收由 live 标记单独执行。"""

    __test__ = False
    configured = True

    async def probe(self):
        return dict(
            id="test-profile",
            provider="siliconflow",
            model="BAAI/bge-m3",
            dimension=3,
            collection=self.collection,
            distance="Cosine",
        )

    async def embed(self, texts, dimension=None):
        return [[1.0, 0.0, 0.0] for _ in texts]


@pytest.fixture
async def indexing(bundle):
    b = bundle
    files = Files(b.settings)
    documents = DocumentService(
        b.database, files, Cursors(b.settings.auth_secret.get_secret_value()), b.settings
    )
    vectors = Vectors(b.settings)
    embedding = TestEmbedding()
    embedding.collection = "test_stage4_" + uuid4().hex
    service = IndexingService(b.database, files, embedding, vectors, b.settings)
    kb = await b.knowledge.create(str(b.users["alice"].id), KnowledgeBaseCreate(name="索引测试"))
    actor = str(b.users["alice"].id)
    try:
        yield b, documents, service, embedding, vectors, actor, str(kb.id)
    finally:
        await vectors.request("DELETE", f"/collections/{embedding.collection}", allow_missing=True)
        await vectors.close()


async def upload(ctx, text="这是中文知识库。文档索引必须完整发布。", name="资料.txt"):
    b, documents, worker, embedding, vectors, actor, kb_id = ctx
    return await documents.upload(
        actor, kb_id, UploadFile(BytesIO(text.encode()), filename=name), Metadata(tags=["测试"])
    )


async def test_worker_publish_filter_sources_reindex_and_delete(indexing):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    result = await upload(indexing)
    assert await worker.run_once()
    document = await documents.get(actor, str(result.document.id))
    assert document.availability == "searchable" and document.latest_job.status == "succeeded"
    profile = await embedding.probe()
    assert (
        await vectors.count(profile["collection"], kb_id, str(document.active_index_version_id))
        == 1
    )
    retrieval = RetrievalService(b.database, embedding, vectors, b.settings)
    hits = await retrieval.search(actor, kb_id, RetrievalInput(question="如何发布索引？"))
    assert len(hits) == 1 and "完整发布" in hits[0]["text"]
    chunk = await documents.chunk(actor, hits[0]["chunk_id"])
    assert chunk.char_start == 0 and chunk.text[chunk.char_start : chunk.char_end] == chunk.text
    with pytest.raises(AppError):
        await retrieval.search(str(b.users["bob"].id), kb_id, RetrievalInput(question="test"))
    with pytest.raises(AppError, match="FILTER_RESOURCE_NOT_FOUND"):
        await retrieval.search(
            actor,
            kb_id,
            RetrievalInput(question="test", metadata_filter=MetadataFilter(document_ids=[uuid4()])),
        )
    assert (
        await retrieval.search(
            actor,
            kb_id,
            RetrievalInput(question="test", metadata_filter=MetadataFilter(file_types=["pdf"])),
        )
        == []
    )
    updated = await documents.patch(actor, str(document.id), DocumentPatch(tags=["新标签"]))
    assert updated.job.status == "queued"
    assert (
        await retrieval.search(
            actor,
            kb_id,
            RetrievalInput(question="test", metadata_filter=MetadataFilter(tags_any=["测试"])),
        )
        == []
    )
    old_version = str(document.active_index_version_id)
    await worker.run_once()
    rebuilt = await documents.get(actor, str(document.id))
    assert str(rebuilt.active_index_version_id) != old_version
    with pytest.raises(AppError):
        await documents.chunk(actor, hits[0]["chunk_id"])
    # 旧向量仍实际存在，但有效版本过滤不返回旧 Chunk。
    assert await vectors.count(profile["collection"], kb_id, old_version) == 1
    assert all(
        h["index_version_id"] != old_version
        for h in await retrieval.search(actor, kb_id, RetrievalInput(question="test"))
    )
    deleted = await documents.delete(actor, str(document.id))
    assert await retrieval.search(actor, kb_id, RetrievalInput(question="test")) == []
    await worker.run_once()
    assert (await documents.get_job(actor, str(deleted.cleanup_job_id))).status == "succeeded"
    await worker.maintain()
    assert await vectors.count(profile["collection"], kb_id, old_version) == 0
    # 模拟清理完成后迟到的同一批次 upsert；失效版本巡检必须再次删除。
    await vectors.upsert(
        profile,
        [
            {
                "id": hits[0]["chunk_id"],
                "vector": {"dense": [1.0, 0.0, 0.0]},
                "payload": {
                    "kb_id": kb_id,
                    "index_version_id": old_version,
                    "document_id": str(document.id),
                },
            }
        ],
    )
    assert await vectors.count(profile["collection"], kb_id, old_version) == 1
    assert await retrieval.search(actor, kb_id, RetrievalInput(question="test")) == []
    await worker.maintain()
    assert await vectors.count(profile["collection"], kb_id, old_version) == 0
    await b.knowledge.delete(actor, kb_id)


async def test_leases_recovery_late_worker_and_bounded_attempts(indexing):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    result = await upload(indexing)
    first, second = await asyncio.gather(worker.claim(), worker.claim())
    lease = first or second
    assert (first is None) != (second is None)
    async with b.database.transaction() as repo:
        await repo.lock_authorization()
        job = await repo.job(lease.job_id, lock=True)
        job.lease_expires_at = (await repo.now()) - timedelta(seconds=1)
        await repo.save(job)
    await worker.recover()
    newer = await worker.claim()
    assert newer.token != lease.token and newer.version_id != lease.version_id
    with pytest.raises(LeaseLost):
        await worker.publish(lease, 1, await embedding.probe())
    await worker.fail(newer, IndexFailure("TEMP", "模拟暂时故障", retryable=True))
    job = await documents.get_job(actor, str(result.job.id))
    assert job.status == "retry_wait" and job.attempt_count == 2
    async with b.database.transaction() as repo:
        job = await repo.job(lease.job_id, lock=True)
        job.available_at = await repo.now()
        await repo.save(job)
    final = await worker.claim()
    await worker.fail(final, IndexFailure("TEMP", "再次暂时故障", retryable=True))
    failed = await documents.get_job(actor, lease.job_id)
    assert failed.status == "failed" and failed.attempt_count == 3
    retries = await asyncio.gather(
        documents.retry(actor, lease.job_id),
        documents.retry(actor, lease.job_id),
        return_exceptions=True,
    )
    assert sum(isinstance(r, AppError) for r in retries) == 1
    assert sum(not isinstance(r, Exception) for r in retries) == 1


async def test_failed_rebuild_keeps_old_index_and_incomplete_never_visible(indexing):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    result = await upload(indexing)
    await worker.run_once()
    before = await documents.get(actor, str(result.document.id))
    await documents.reindex(actor, str(before.id))
    lease = await worker.claim()
    await worker.fail(lease, IndexFailure("EMBEDDING_REQUEST_REJECTED", "模拟凭据错误"))
    failed = await documents.get(actor, str(before.id))
    assert failed.latest_job.status == "failed"
    assert failed.active_index_version_id == before.active_index_version_id
    assert failed.availability == "searchable"
    await documents.retry(actor, str(failed.latest_job.id))
    next_lease = await worker.claim()
    await worker.bind_profile(next_lease, await embedding.probe())
    assert (
        await documents.get(actor, str(before.id))
    ).active_index_version_id == before.active_index_version_id
    profile = await embedding.probe()
    await vectors.upsert(
        profile,
        [
            {
                "id": str(uuid4()),
                "vector": {"dense": [1.0, 0.0, 0.0]},
                "payload": {
                    "kb_id": kb_id,
                    "index_version_id": next_lease.version_id,
                    "document_id": str(before.id),
                },
            }
        ],
    )
    retrieval = RetrievalService(b.database, embedding, vectors, b.settings)
    candidates = await retrieval.search(actor, kb_id, RetrievalInput(question="test", top_k=20))
    assert candidates and all(
        c["index_version_id"] == str(before.active_index_version_id) for c in candidates
    )
    await documents.delete(actor, str(before.id))
    with pytest.raises(LeaseLost):
        await worker.publish(next_lease, 1, await embedding.probe())


async def test_scanned_pdf_fails_without_fake_success(indexing):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    result = await documents.upload(
        actor, kb_id, UploadFile(BytesIO(buffer.getvalue()), filename="扫描.pdf"), Metadata()
    )
    await worker.run_once()
    doc = await documents.get(actor, str(result.document.id))
    assert doc.availability == "unavailable" and doc.latest_job.status == "failed"
    assert doc.latest_job.error.code == "DOCUMENT_TEXT_EMPTY"


async def test_missing_points_invalidate_and_schedule_repair(indexing):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    result = await upload(indexing)
    await worker.run_once()
    doc = await documents.get(actor, str(result.document.id))
    await vectors.delete_version(embedding.collection, str(doc.active_index_version_id))
    await worker.maintain()
    repaired = await documents.get(actor, str(doc.id))
    assert repaired.availability == "unavailable" and repaired.latest_job.status == "queued"
    assert repaired.latest_job.id != doc.latest_job.id


async def test_real_worker_process_kill_then_lease_recovery(indexing):
    import os
    import signal
    import sys
    import time

    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    result = await upload(indexing, "任务恢复验证。" * 1000)
    # 独立进程组只包含本测试 Worker 及其解析子进程，SIGKILL 不影响应用实例。
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        claimed = None
        while time.monotonic() < deadline:
            assert process.returncode is None, "独立 Worker 提前退出"
            async with b.database.transaction() as repo:
                job = await repo.job(str(result.job.id))
                if job.status == "running":
                    claimed = job.lease_token
                    break
            await asyncio.sleep(0.02)
        assert claimed, "独立 Worker 未能领取任务"
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        async with b.database.transaction() as repo:
            await repo.lock_authorization()
            job = await repo.job(str(result.job.id), lock=True)
            assert job.status == "running" and job.lease_token == claimed
            # 通过数据库时钟推进过期条件，避免测试等待默认 90 秒。
            job.lease_expires_at = (await repo.now()) - timedelta(seconds=1)
            await repo.save(job)
        await worker.recover()
        lease = await worker.claim()
        assert lease.token != claimed and lease.version_id
        # 新 Worker 继续处理真实原文件；本集成测试的向量来源明确是测试替身。
        await worker.index(lease)
        document = await documents.get(actor, str(result.document.id))
        assert document.availability == "searchable" and document.latest_job.attempt_count == 2
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


async def test_skip_locked_and_heartbeat_use_real_mysql(indexing):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    first = await upload(indexing, "第一份文件")
    second = await upload(indexing, "第二份文件")
    async with b.database.sessions() as held, held.begin():
        await held.scalar(select(Job).where(Job.id == str(first.job.id)).with_for_update())
        lease = await asyncio.wait_for(worker.claim(), 3)
        assert lease.job_id == str(second.job.id)
    async with b.database.transaction() as repo:
        job = await repo.job(lease.job_id)
        previous = job.lease_expires_at
    import time
    from dataclasses import replace

    settings = b.settings.model_copy(update={"index_heartbeat_seconds": 0.01})
    fast = IndexingService(b.database, worker.files, embedding, vectors, settings)
    task = asyncio.create_task(fast.heartbeat(lease))
    try:
        deadline = time.monotonic() + 3
        extended = None
        while time.monotonic() < deadline:
            async with b.database.transaction() as repo:
                current = await repo.job(lease.job_id)
                if current.lease_expires_at > previous:
                    extended = current.lease_expires_at
                    break
            await asyncio.sleep(0.01)
        assert extended and extended > previous
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    from datetime import datetime

    with pytest.raises(LeaseLost):
        await worker.publish(
            replace(lease, deadline_at=datetime(2000, 1, 1)), 1, await embedding.probe()
        )


async def test_profile_drift_is_rejected_and_parent_foreign_keys_are_real(indexing):
    from sqlalchemy.exc import DBAPIError

    from app.infrastructure.models import Chunk

    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    result = await upload(indexing)
    await worker.run_once()
    original = await documents.get(actor, str(result.document.id))
    await documents.reindex(actor, str(original.id))
    lease = await worker.claim()
    profile = await embedding.probe()
    with pytest.raises(IndexFailure, match="EMBEDDING_PROFILE_MISMATCH"):
        await worker.bind_profile(lease, {**profile, "id": "different-space", "dimension": 4})
    assert (
        await documents.get(actor, str(original.id))
    ).active_index_version_id == original.active_index_version_id
    other = await upload(indexing, "另一份资料")
    with pytest.raises(DBAPIError) as exc:
        async with b.database.transaction() as repo:
            # Chunk 的版本属于首个文档，不能指向另一文档，即使两者同库。
            await repo.save(
                Chunk(
                    id=str(uuid4()),
                    kb_id=kb_id,
                    document_id=str(other.document.id),
                    index_version_id=str(original.active_index_version_id),
                    ordinal=999,
                    text="test-only",
                    text_sha256="0" * 64,
                    char_start=0,
                    char_end=1,
                    metadata_json={},
                )
            )
    assert exc.value.orig.args[0] == 1452


async def test_shared_collection_filters_other_kb_and_rechecks_revocation_after_embedding(indexing):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    first = await upload(indexing, "当前知识库公开给本库成员的正文。")
    await worker.run_once()
    other_kb = await b.knowledge.create(actor, KnowledgeBaseCreate(name="另一私有知识库"))
    await documents.upload(
        actor,
        str(other_kb.id),
        UploadFile(BytesIO("另一知识库独有正文".encode()), filename="private.txt"),
        Metadata(),
    )
    await worker.run_once()
    from app.modules.knowledge_bases.schemas import Role

    bob = str(b.users["bob"].id)
    await b.knowledge.put_member(actor, kb_id, bob, Role.VIEWER)
    retrieval = RetrievalService(b.database, embedding, vectors, b.settings)
    hits = await retrieval.search(bob, kb_id, RetrievalInput(question="正文", top_k=20))
    assert {h["document_id"] for h in hits} == {str(first.document.id)}
    assert all("另一知识库独有正文" not in h["text"] for h in hits)

    class RevokingEmbedding(TestEmbedding):
        async def embed(self, texts, dimension=None):
            await b.knowledge.remove_member(actor, kb_id, bob)
            return await super().embed(texts, dimension)

    revoking = RevokingEmbedding()
    revoking.collection = embedding.collection
    denied = RetrievalService(b.database, revoking, vectors, b.settings)
    with pytest.raises(AppError, match="RESOURCE_NOT_FOUND"):
        await denied.search(bob, kb_id, RetrievalInput(question="正文"))


async def test_new_representation_rebuild_keeps_raw_sources_and_failed_rebuild_keeps_version(
    indexing,
):
    b, documents, worker, embedding, vectors, actor, kb_id = indexing
    raw = "# 规则\n\n## 上传\n\n1. 必须验证权限。\n2. 文件大小有限制。\n"
    worker.settings = b.settings.model_copy(
        update={"chunk_structure": "recursive-v1", "embedding_title_context": False}
    )
    uploaded = await upload(indexing, raw, "规则.md")
    document_id = str(uploaded.document.id)
    await worker.run_once()
    old = (await documents.get(actor, document_id)).active_index_version_id
    worker.settings = b.settings.model_copy(
        update={"chunk_structure": "paragraph-v2", "embedding_title_context": True}
    )
    inputs = []
    original = embedding.embed

    async def capture(texts, dimension=None):
        inputs.extend(texts)
        return await original(texts, dimension)

    embedding.embed = capture
    await documents.reindex(actor, document_id)
    await worker.run_once()
    current = (await documents.get(actor, document_id)).active_index_version_id
    assert current != old
    assert any(text.startswith("章节：规则 > 上传\n") and "必须验证权限" in text for text in inputs)
    async with b.database.transaction() as repo:
        version = await repo.version(str(current))
        assert version.config_snapshot["document_representation"] == "heading-body-v2"
        chunks = (
            await repo.session.scalars(select(Chunk).where(Chunk.index_version_id == str(current)))
        ).all()
        assert chunks and all(
            c.metadata_json["document_representation"] == "heading-body-v2" for c in chunks
        )
        assert all(raw[c.char_start : c.char_end] == c.text for c in chunks)
    retrieval = RetrievalService(b.database, embedding, vectors, b.settings)
    hits = await retrieval.search(actor, kb_id, RetrievalInput(question="上传规则"))
    assert hits and all(h["index_version_id"] == str(current) for h in hits)
    assert all(not h["text"].startswith("章节：") for h in hits)
    with pytest.raises(AppError):
        await retrieval.search(str(b.users["bob"].id), kb_id, RetrievalInput(question="上传规则"))

    async def fail(texts, dimension=None):
        raise IndexFailure("EMBEDDING_REQUEST_REJECTED", "测试凭据失败")

    embedding.embed = fail
    await documents.reindex(actor, document_id)
    await worker.run_once()
    after = await documents.get(actor, document_id)
    assert after.latest_job.status == "failed" and after.active_index_version_id == current
