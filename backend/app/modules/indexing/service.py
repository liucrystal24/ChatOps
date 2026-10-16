import asyncio
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4, uuid5

from app.core.embedding_profile import TOKENIZER_REVISION
from app.infrastructure.models import Chunk, IndexVersion
from app.modules.documents.service import new_job
from app.modules.indexing.errors import IndexFailure, LeaseLost

logger = logging.getLogger("chat_rag.indexing")


@dataclass(frozen=True)
class Lease:
    job_id: str
    token: str
    kb_id: str
    document_id: str
    version_id: str | None
    kind: str
    source: dict
    deadline_at: datetime


class IndexingService:
    def __init__(self, database, files, embedding, vectors, settings, *, worker_id=None):
        self.database, self.files = database, files
        self.embedding, self.vectors, self.settings = embedding, vectors, settings
        self.worker_id = worker_id or ("worker-" + uuid4().hex)

    def snapshot(self):
        return {
            "parser": "pypdf-utf8-v1",
            "splitter": "recursive-cn-v1",
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
            "tokenizer_revision": TOKENIZER_REVISION,
        }

    async def claim(self):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            job = await repo.queued_job()
            if not job:
                return None
            document = await repo.document(job.document_id, lock=True)
            kb = await repo.knowledge_base(job.kb_id, lock=True)
            now = await repo.now()
            if kb.deleted_at is not None or (
                job.kind == "index"
                and (document.deleted_at is not None or document.current_job_id != job.id)
            ):
                job.status, job.finished_at = "cancelled", now
                await repo.save(job)
                return None
            job.status, job.stage = "running", "parse" if job.kind == "index" else "cleanup"
            job.attempt_count += 1
            job.lease_owner, self_token = self.worker_id, str(uuid4())
            job.lease_token = self_token
            job.lease_expires_at = now + timedelta(seconds=self.settings.index_lease_seconds)
            job.heartbeat_at = now
            job.started_at = job.started_at or now
            job.error_code = job.error_message = None
            job.progress_completed, job.progress_total = 0, None
            version = None
            if job.kind == "index":
                job.target_config = self.snapshot()
                version = await repo.save(
                    IndexVersion(
                        id=str(uuid4()),
                        kb_id=job.kb_id,
                        document_id=job.document_id,
                        job_id=job.id,
                        attempt_no=job.attempt_count,
                        config_snapshot=job.target_config,
                        source_sha256=document.content_sha256,
                    )
                )
            await repo.save(job)
            return Lease(
                job.id,
                self_token,
                job.kb_id,
                document.id,
                version.id if version else None,
                job.kind,
                dict(
                    storage_key=document.storage_key,
                    file_type=document.file_type,
                    sha256=document.content_sha256,
                    tags=document.tags,
                    created_at=document.created_at.isoformat(),
                ),
                now + timedelta(seconds=self.settings.index_deadline_seconds),
            )

    async def owned(self, repo, lease, *, allow_deadline=False):
        await repo.lock_authorization()
        document = await repo.document(lease.document_id, lock=True)
        job = await repo.job(lease.job_id, lock=True)
        now = await repo.now()
        if (
            job.status != "running"
            or job.lease_token != lease.token
            or not job.lease_expires_at
            or job.lease_expires_at <= now
            or (now >= lease.deadline_at and not allow_deadline)
        ):
            raise LeaseLost
        if lease.kind == "index" and (
            document.deleted_at is not None or document.current_job_id != job.id
        ):
            raise LeaseLost
        return job, document, now

    async def heartbeat(self, lease):
        while True:
            await asyncio.sleep(self.settings.index_heartbeat_seconds)
            async with self.database.transaction() as repo:
                job, _, now = await self.owned(repo, lease)
                job.heartbeat_at = now
                job.lease_expires_at = now + timedelta(seconds=self.settings.index_lease_seconds)
                await repo.save(job)

    async def progress(self, lease, stage, completed=0, total=None):
        async with self.database.transaction() as repo:
            job, _, _ = await self.owned(repo, lease)
            job.stage, job.progress_completed, job.progress_total = stage, completed, total
            await repo.save(job)

    async def parse(self, lease):
        path = self.files.path(lease.source["storage_key"])
        if not path.is_file():
            raise IndexFailure("DOCUMENT_FILE_UNAVAILABLE", "原文件不存在，请重新上传")

        def sha():
            with path.open("rb") as file:
                return hashlib.file_digest(file, "sha256").hexdigest()

        if await asyncio.to_thread(sha) != lease.source["sha256"]:
            raise IndexFailure("DOCUMENT_HASH_MISMATCH", "原文件校验失败，请重新上传")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.modules.indexing.parser",
            str(path),
            lease.source["file_type"],
            str(self.settings.tokenizer_path),
            str(self.settings.chunk_size),
            str(self.settings.chunk_overlap),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), self.settings.parser_timeout_seconds
            )
            if process.returncode != 0 or len(stdout) > 24 * 1024**2:
                raise IndexFailure("DOCUMENT_PARSE_FAILED", "文档解析超过资源限制或异常退出")
            result = json.loads(stdout)
            if "error" in result:
                raise IndexFailure(result["error"]["code"], result["error"]["message"])
            return result["chunks"]
        except TimeoutError:
            raise IndexFailure("DOCUMENT_PARSE_TIMEOUT", "文档解析超时") from None
        except (ValueError, KeyError):
            raise IndexFailure("DOCUMENT_PARSE_FAILED", "文档解析失败") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def bind_profile(self, lease, profile):
        async with self.database.transaction() as repo:
            job, _, _ = await self.owned(repo, lease)
            kb = await repo.knowledge_base(lease.kb_id, lock=True)
            if kb.embedding_profile_id and (
                kb.embedding_profile_id != profile["id"] or kb.embedding_profile != profile
            ):
                raise IndexFailure(
                    "EMBEDDING_PROFILE_MISMATCH", "知识库已绑定其他向量配置，不能直接更换模型"
                )
            if not kb.embedding_profile_id:
                kb.embedding_profile_id, kb.embedding_profile = profile["id"], profile
                await repo.save(kb)
            version = await repo.version(lease.version_id)
            version.embedding_profile_id, version.collection_name = (
                profile["id"],
                profile["collection"],
            )
            version.config_snapshot = {**version.config_snapshot, "embedding_profile": profile}
            job.target_config = version.config_snapshot
            await repo.save(version)
            await repo.save(job)

    async def index(self, lease):
        parts = await self.parse(lease)
        await self.progress(lease, "chunk", len(parts), len(parts))
        profile = await self.embedding.probe()
        await self.bind_profile(lease, profile)
        await self.vectors.ensure(profile)
        chunks = []
        for ordinal, part in enumerate(parts):
            chunks.append(
                Chunk(
                    id=str(uuid5(UUID(lease.version_id), str(ordinal))),
                    kb_id=lease.kb_id,
                    document_id=lease.document_id,
                    index_version_id=lease.version_id,
                    ordinal=ordinal,
                    text_sha256=hashlib.sha256(part["text"].encode()).hexdigest(),
                    metadata_json={},
                    **part,
                )
            )
        for start in range(0, len(chunks), 100):
            async with self.database.transaction() as repo:
                await self.owned(repo, lease)
                await repo.add_chunks(chunks[start : start + 100])
        size = self.settings.embedding_batch_size
        for start in range(0, len(chunks), size):
            await self.progress(lease, "embed", start, len(chunks))
            batch = chunks[start : start + size]
            vectors = await self.embedding.embed([c.text for c in batch], profile["dimension"])
            await self.progress(lease, "upsert", start, len(chunks))
            points = [
                {
                    "id": c.id,
                    "vector": {"dense": vector},
                    "payload": {
                        "kb_id": lease.kb_id,
                        "document_id": lease.document_id,
                        "index_version_id": lease.version_id,
                        "chunk_id": c.id,
                        "embedding_profile_id": profile["id"],
                        "file_type": lease.source["file_type"],
                        "tags": lease.source["tags"],
                        "document_created_at": lease.source["created_at"],
                        "page_no": c.page_no,
                    },
                }
                for c, vector in zip(batch, vectors, strict=True)
            ]
            await self.vectors.upsert(profile, points)
        await self.progress(lease, "publish", len(chunks), len(chunks))
        if await self.vectors.count(profile["collection"], lease.kb_id, lease.version_id) != len(
            chunks
        ):
            raise IndexFailure("INDEX_INCOMPLETE", "向量写入数量不完整", retryable=True)
        await self.publish(lease, len(chunks), profile)

    async def publish(self, lease, count, profile):
        async with self.database.transaction() as repo:
            job, document, now = await self.owned(repo, lease)
            kb = await repo.knowledge_base(lease.kb_id, lock=True)
            version = await repo.version(lease.version_id)
            if kb.embedding_profile_id != profile["id"] or version.status != "staging":
                raise LeaseLost
            # 只有完整版本可切换指针；重建失败/迟到发布均不会破坏旧版本。
            if document.active_index_version_id:
                old = await repo.version(document.active_index_version_id)
                old.status = "retired"
                await repo.save(old)
            version.status, version.chunk_count, version.published_at = "published", count, now
            await repo.save(version)
            document.active_index_version_id = version.id
            await repo.save(document)
            self.succeed(job, now, count)
            await repo.save(job)

    @staticmethod
    def succeed(job, now, count=0):
        job.status, job.finished_at = "succeeded", now
        job.progress_completed = job.progress_total = count
        job.lease_token = job.lease_expires_at = None

    async def purge(self, lease):
        async with self.database.transaction() as repo:
            _, document, _ = await self.owned(repo, lease)
            if document.deleted_at is None:
                raise IndexFailure("PURGE_SCOPE_INVALID", "只能清理已删除文档")
            versions = await repo.document_versions(document.id)
        for version in versions:
            if version.collection_name:
                await self.vectors.delete_version(version.collection_name, version.id)
        await self.files.remove(lease.source["storage_key"])
        async with self.database.transaction() as repo:
            job, document, now = await self.owned(repo, lease)
            for version in versions:
                await repo.discard_chunks(version.id)
            document.purged_at = now
            await repo.save(document)
            self.succeed(job, now)
            await repo.save(job)

    async def fail(self, lease, error):
        try:
            async with self.database.transaction() as repo:
                job, _, now = await self.owned(repo, lease, allow_deadline=True)
                if lease.version_id:
                    version = await repo.version(lease.version_id)
                    version.status, version.error_code = "failed", error.code
                    await repo.save(version)
                retry = error.retryable and job.attempt_count < job.max_attempts
                job.status = "retry_wait" if retry else "failed"
                job.error_code, job.error_message = error.code, error.message
                job.available_at = now + timedelta(seconds=min(60, 2**job.attempt_count))
                job.finished_at = None if retry else now
                job.lease_token = job.lease_expires_at = None
                await repo.save(job)
        except LeaseLost:
            pass

    async def recover(self):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            for job in await repo.expired_jobs():
                now = await repo.now()
                for version in await repo.job_versions(job.id):
                    version.status, version.error_code = "failed", "LEASE_EXPIRED"
                    await repo.save(version)
                job.status = "retry_wait" if job.attempt_count < job.max_attempts else "failed"
                job.error_code, job.error_message = (
                    "LEASE_EXPIRED",
                    "Worker 租约已过期，正在恢复或已达重试上限",
                )
                job.available_at = now
                job.finished_at = now if job.status == "failed" else None
                job.lease_token = job.lease_expires_at = None
                await repo.save(job)

    async def run_once(self):
        await self.recover()
        lease = await self.claim()
        if not lease:
            return False
        task = asyncio.create_task(
            self.index(lease) if lease.kind == "index" else self.purge(lease)
        )
        heartbeat = asyncio.create_task(self.heartbeat(lease))
        try:
            done, _ = await asyncio.wait(
                [task, heartbeat],
                timeout=self.settings.index_deadline_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise IndexFailure("INDEX_DEADLINE_EXCEEDED", "索引任务超过总时限", retryable=True)
            if heartbeat in done:
                await heartbeat
                raise LeaseLost
            await task
            logger.info(
                json.dumps({"event": "job_finished", "job_id": lease.job_id, "kb_id": lease.kb_id})
            )
        except LeaseLost:
            pass
        except IndexFailure as exc:
            await self.fail(lease, exc)
        except Exception:
            # 仅保存稳定错误码，不把供应商/解析器异常及正文写入任务或日志。
            await self.fail(
                lease, IndexFailure("INDEX_INTERNAL_ERROR", "索引处理失败，请重试", retryable=True)
            )
        finally:
            for pending in (task, heartbeat):
                pending.cancel()
            await asyncio.gather(task, heartbeat, return_exceptions=True)
        return True

    async def maintain(self):
        await self.recover()
        after = None
        while True:
            async with self.database.transaction() as repo:
                stale = await repo.stale_versions(after)
            if not stale:
                break
            for version in stale:
                if version.collection_name:
                    await self.vectors.delete_version(version.collection_name, version.id)
                async with self.database.transaction() as repo:
                    # 失效版本永远不会重新发布，重复清理可处理迟到 upsert。
                    current = await repo.version(version.id)
                    if current.status in {"retired", "failed"}:
                        await repo.discard_chunks(version.id)
            after = stale[-1].id
        after = None
        while True:
            async with self.database.transaction() as repo:
                rows = await repo.active_versions(after=after)
            if not rows:
                break
            for document, version in rows:
                count = await self.vectors.count(
                    version.collection_name, document.kb_id, version.id
                )
                if count != version.chunk_count:
                    async with self.database.transaction() as repo:
                        await repo.lock_authorization()
                        current = await repo.document(document.id, lock=True)
                        if (
                            current.deleted_at is None
                            and current.active_index_version_id == version.id
                        ):
                            current.active_index_version_id = None
                            version = await repo.version(version.id)
                            version.status, version.error_code = "failed", "INDEX_INCOMPLETE"
                            await repo.save(version)
                            await repo.save(current)
                            job = (
                                await repo.job(current.current_job_id)
                                if current.current_job_id
                                else None
                            )
                            if not job or job.status not in {"queued", "running", "retry_wait"}:
                                await new_job(repo, current)
            after = rows[-1][0].id
        async with self.database.transaction() as repo:
            keys = await repo.storage_keys()
        cutoff = time.time() - 86400

        def cleanup_files():
            for path in self.files.root.iterdir():
                if path.is_file() and path.name not in keys and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)

        await asyncio.to_thread(cleanup_files)
