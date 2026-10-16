from uuid import uuid4

from sqlalchemy import func

from app.core.errors import AppError
from app.infrastructure.document_queries import ACTIVE
from app.infrastructure.models import Document, Job
from app.modules.documents.schemas import (
    ChunkList,
    ChunkResponse,
    DeleteAccepted,
    DocumentList,
    DocumentResponse,
    JobAccepted,
    JobError,
    JobList,
    JobResponse,
    UploadResponse,
)
from app.modules.knowledge_bases.service import access


def missing():
    return AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")


def writable(member):
    if member.role not in {"owner", "editor"}:
        raise AppError(403, "ROLE_FORBIDDEN", "仅 owner 或 editor 可以管理文档")


def job_response(job):
    return JobResponse(
        **{
            key: getattr(job, key)
            for key in (
                "id",
                "document_id",
                "kind",
                "status",
                "stage",
                "attempt_count",
                "max_attempts",
                "progress_completed",
                "progress_total",
                "available_at",
                "created_at",
                "started_at",
                "finished_at",
            )
        },
        error=JobError(code=job.error_code, message=job.error_message) if job.error_code else None,
    )


async def document_response(repo, document):
    job = await repo.job(document.current_job_id) if document.current_job_id else None
    return DocumentResponse(
        **{
            key: getattr(document, key)
            for key in (
                "id",
                "kb_id",
                "filename",
                "file_type",
                "media_type",
                "size_bytes",
                "tags",
                "active_index_version_id",
                "created_at",
                "updated_at",
            )
        },
        availability="searchable" if document.active_index_version_id else "unavailable",
        latest_job=job_response(job) if job else None,
    )


async def document_access(repo, actor_id, document_id, *, write=False, deleted=False):
    # 写操作先锁 guard，再查资源/锁父库，保证与撤权和用户停用保持同一顺序。
    if write:
        await repo.lock_authorization()
    document = await repo.document(document_id)
    if not document or (document.deleted_at is not None and not deleted):
        raise missing()
    kb, member = await access(repo, actor_id, document.kb_id, lock=write)
    if write:
        writable(member)
        document = await repo.document(document_id, lock=True)
        if document.deleted_at is not None and not deleted:
            raise missing()
    return document, kb


async def new_job(repo, document, kind="index", retry_of=None):
    job = await repo.save(
        Job(
            id=str(uuid4()),
            kb_id=document.kb_id,
            document_id=document.id,
            kind=kind,
            target_config={},
            retry_of_job_id=retry_of,
        )
    )
    if kind == "index":
        document.current_job_id = job.id
        await repo.save(document)
    return job


async def require_idle(repo, document):
    current = await repo.job(document.current_job_id) if document.current_job_id else None
    if current and current.status in ACTIVE:
        raise AppError(409, "INDEX_JOB_CONFLICT", "此文档已有待执行或正在运行的索引任务")


class DocumentService:
    def __init__(self, database, files, cursors, settings):
        self.database, self.files, self.cursors, self.settings = database, files, cursors, settings

    async def upload(self, actor_id, kb_id, upload, metadata):
        async with self.database.transaction() as repo:
            _, member = await access(repo, actor_id, kb_id)
            writable(member)
        stored = await self.files.store(upload)
        committed = False
        try:
            async with self.database.transaction() as repo:
                await repo.lock_authorization()
                _, member = await access(repo, actor_id, kb_id, lock=True)
                writable(member)
                count, size = await repo.document_totals(kb_id)
                if (
                    count >= self.settings.kb_document_limit
                    or size + stored["size_bytes"] > self.settings.kb_max_bytes
                ):
                    raise AppError(409, "KB_DOCUMENT_LIMIT", "知识库文档数量或大小配额已用完")
                document = await repo.save(
                    Document(
                        id=str(uuid4()),
                        kb_id=kb_id,
                        uploaded_by=actor_id,
                        tags=metadata.tags,
                        **stored,
                    )
                )
                job = await new_job(repo, document)
                result = UploadResponse(
                    document=await document_response(repo, document), job=job_response(job)
                )
            committed = True
            return result
        finally:
            if not committed:
                await self.files.remove(stored["storage_key"])

    async def list(self, actor_id, kb_id, page):
        scope = f"docs:{kb_id}:{actor_id}:{page.availability}"
        position = self.cursors.decode(page.cursor, scope)
        async with self.database.transaction() as repo:
            await access(repo, actor_id, kb_id)
            rows = await repo.documents(kb_id, page, position)
            return DocumentList(
                items=[await document_response(repo, d) for d in rows[: page.limit]],
                next_cursor=self.cursor(rows, page, scope),
            )

    def cursor(self, rows, page, scope):
        return (
            self.cursors.encode(scope, rows[page.limit - 1].created_at, rows[page.limit - 1].id)
            if len(rows) > page.limit
            else None
        )

    async def get(self, actor_id, document_id):
        async with self.database.transaction() as repo:
            document, _ = await document_access(repo, actor_id, document_id)
            return await document_response(repo, document)

    async def file(self, actor_id, document_id):
        async with self.database.transaction() as repo:
            document, _ = await document_access(repo, actor_id, document_id)
            path = self.files.path(document.storage_key)
            if not path.is_file():
                raise AppError(503, "DOCUMENT_FILE_UNAVAILABLE", "原文件暂不可用")
            return path, document.filename, document.media_type

    async def delete(self, actor_id, document_id):
        async with self.database.transaction() as repo:
            document, _ = await document_access(repo, actor_id, document_id, write=True)
            document.deleted_at = func.utc_timestamp(6)
            document.active_index_version_id = None
            current = (
                await repo.job(document.current_job_id, lock=True)
                if document.current_job_id
                else None
            )
            if current and current.status in ACTIVE:
                current.status, current.lease_token, current.lease_expires_at = (
                    "cancelled",
                    None,
                    None,
                )
                current.finished_at = func.utc_timestamp(6)
                await repo.save(current)
            for version in await repo.document_versions(document.id):
                if version.status in {"published", "staging"}:
                    version.status = "retired" if version.status == "published" else "failed"
                    await repo.save(version)
            document.current_job_id = None
            await repo.save(document)
            purge = await new_job(repo, document, "purge")
            return DeleteAccepted(document_id=document.id, cleanup_job_id=purge.id)

    async def reindex(self, actor_id, document_id):
        async with self.database.transaction() as repo:
            document, _ = await document_access(repo, actor_id, document_id, write=True)
            await require_idle(repo, document)
            return JobAccepted(job=job_response(await new_job(repo, document)))

    async def patch(self, actor_id, document_id, data):
        async with self.database.transaction() as repo:
            document, _ = await document_access(repo, actor_id, document_id, write=True)
            await require_idle(repo, document)
            changes = data.model_dump(exclude_unset=True)
            for key, value in changes.items():
                setattr(document, key, value)
            await repo.save(document)
            if "tags" in changes:
                job = await new_job(repo, document)
                return UploadResponse(
                    document=await document_response(repo, document), job=job_response(job)
                )
            return await document_response(repo, document)

    async def jobs(self, actor_id, kb_id, page):
        scope = f"jobs:{kb_id}:{actor_id}:{page.document_id}:{page.status}"
        position = self.cursors.decode(page.cursor, scope)
        async with self.database.transaction() as repo:
            await access(repo, actor_id, kb_id)
            if page.document_id:
                document = await repo.document(str(page.document_id))
                if not document or document.kb_id != kb_id:
                    raise missing()
            rows = await repo.jobs(kb_id, page, position)
            return JobList(
                items=[job_response(j) for j in rows[: page.limit]],
                next_cursor=self.cursor(rows, page, scope),
            )

    async def get_job(self, actor_id, job_id):
        async with self.database.transaction() as repo:
            job = await repo.job(job_id)
            if not job:
                raise missing()
            await access(repo, actor_id, job.kb_id)
            return job_response(job)

    async def retry(self, actor_id, job_id):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            job = await repo.job(job_id)
            if not job:
                raise missing()
            _, member = await access(repo, actor_id, job.kb_id, lock=True)
            writable(member)
            document = await repo.document(job.document_id, lock=True)
            job = await repo.job(job_id, lock=True)
            successor = await repo.retry_successor(job.id)
            if job.status != "failed" or successor:
                raise AppError(
                    409,
                    "JOB_NOT_RETRYABLE",
                    "仅失败且没有重试后继的任务可以重试",
                    details={"successor_job_id": successor.id} if successor else {},
                )
            if job.kind == "index":
                if document.deleted_at is not None:
                    raise missing()
                await require_idle(repo, document)
            successor = await new_job(repo, document, job.kind, job.id)
            return JobAccepted(job=job_response(successor))

    async def chunk(self, actor_id, chunk_id):
        async with self.database.transaction() as repo:
            chunk = await repo.chunk(chunk_id)
            if not chunk:
                raise missing()
            document, _ = await document_access(repo, actor_id, chunk.document_id)
            if document.active_index_version_id != chunk.index_version_id:
                raise missing()
            return ChunkResponse(
                **{
                    key: getattr(chunk, key)
                    for key in (
                        "id",
                        "document_id",
                        "index_version_id",
                        "text",
                        "page_no",
                        "section_title",
                        "char_start",
                        "char_end",
                    )
                },
                filename=document.filename,
            )

    async def chunks(self, actor_id, document_id, page):
        async with self.database.transaction() as repo:
            document, _ = await document_access(repo, actor_id, document_id)
            scope = f"chunks:{actor_id}:{document_id}:{document.active_index_version_id}"
            position = self.cursors.decode(page.cursor, scope)
            if not document.active_index_version_id:
                return ChunkList(items=[], next_cursor=None)
            rows = await repo.current_chunks(
                document.active_index_version_id, page.limit, position[1] if position else None
            )
            items = [
                ChunkResponse(
                    **{
                        key: getattr(c, key)
                        for key in (
                            "id",
                            "document_id",
                            "index_version_id",
                            "text",
                            "page_no",
                            "section_title",
                            "char_start",
                            "char_end",
                        )
                    },
                    filename=document.filename,
                )
                for c in rows[: page.limit]
            ]
            return ChunkList(items=items, next_cursor=self.cursor(rows, page, scope))
