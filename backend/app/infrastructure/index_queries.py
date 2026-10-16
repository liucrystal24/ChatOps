from sqlalchemy import delete, func, select

from app.infrastructure.models import Chunk, Citation, Document, IndexVersion, Job, KnowledgeBase


class IndexQueries:
    async def now(self):
        return await self.session.scalar(select(func.utc_timestamp(6)))

    async def queued_job(self):
        return await self.session.scalar(
            select(Job)
            .where(
                Job.status.in_(["queued", "retry_wait"]), Job.available_at <= func.utc_timestamp(6)
            )
            .order_by(Job.available_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    async def expired_jobs(self):
        return (
            await self.session.scalars(
                select(Job)
                .where(Job.status == "running", Job.lease_expires_at <= func.utc_timestamp(6))
                .order_by(Job.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()

    async def job_versions(self, job_id):
        return (
            await self.session.scalars(
                select(IndexVersion).where(
                    IndexVersion.job_id == job_id, IndexVersion.status == "staging"
                )
            )
        ).all()

    async def version_chunks(self, version_id):
        return (
            await self.session.scalars(
                select(Chunk).where(Chunk.index_version_id == version_id).order_by(Chunk.ordinal)
            )
        ).all()

    async def add_chunks(self, chunks):
        self.session.add_all(chunks)
        await self.session.flush()

    async def stale_versions(self, after=None):
        query = select(IndexVersion).where(IndexVersion.status.in_(["failed", "retired"]))
        if after:
            query = query.where(IndexVersion.id > after)
        return (await self.session.scalars(query.order_by(IndexVersion.id).limit(50))).all()

    async def discard_chunks(self, version_id):
        # 历史引用保留旧片段供内部追溯；来源读取仍要求当前文档版本有效。
        await self.session.execute(
            delete(Chunk).where(
                Chunk.index_version_id == version_id,
                ~select(Citation.id).where(Citation.chunk_id == Chunk.id).exists(),
            )
        )

    async def active_versions(self, kb_id=None, after=None):
        query = (
            select(Document, IndexVersion)
            .join(IndexVersion, Document.active_index_version_id == IndexVersion.id)
            .join(KnowledgeBase, KnowledgeBase.id == Document.kb_id)
            .where(
                Document.deleted_at.is_(None),
                KnowledgeBase.deleted_at.is_(None),
                IndexVersion.status == "published",
            )
        )
        if kb_id:
            query = query.where(Document.kb_id == kb_id)
        if after:
            query = query.where(Document.id > after)
        return (await self.session.execute(query.order_by(Document.id).limit(200))).all()

    async def chunks_by_ids(self, ids):
        return (
            (await self.session.scalars(select(Chunk).where(Chunk.id.in_(ids)))).all()
            if ids
            else []
        )

    async def storage_keys(self):
        return set((await self.session.scalars(select(Document.storage_key))).all())
