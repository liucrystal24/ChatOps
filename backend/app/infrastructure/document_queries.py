from sqlalchemy import func, select

from app.infrastructure.models import Chunk, Document, IndexVersion, Job

ACTIVE = ("queued", "running", "retry_wait")


class DocumentQueries:
    async def save(self, entity):
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def document(self, document_id, *, lock=False):
        query = select(Document).where(Document.id == document_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def job(self, job_id, *, lock=False):
        query = select(Job).where(Job.id == job_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def version(self, version_id):
        return await self.session.get(IndexVersion, version_id)

    async def chunk(self, chunk_id):
        return await self.session.get(Chunk, chunk_id)

    async def document_totals(self, kb_id):
        return (
            await self.session.execute(
                select(func.count(), func.coalesce(func.sum(Document.size_bytes), 0)).where(
                    Document.kb_id == kb_id, Document.deleted_at.is_(None)
                )
            )
        ).one()

    async def kb_has_resources(self, kb_id):
        count, _ = await self.document_totals(kb_id)
        active = await self.session.scalar(
            select(func.count()).select_from(Job).where(Job.kb_id == kb_id, Job.status.in_(ACTIVE))
        )
        unpurged = await self.session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.kb_id == kb_id,
                Document.deleted_at.is_not(None),
                Document.purged_at.is_(None),
            )
        )
        return bool(count or active or unpurged)

    async def documents(self, kb_id, page, position):
        from app.infrastructure.repositories import before_position

        query = select(Document).where(Document.kb_id == kb_id, Document.deleted_at.is_(None))
        if page.availability:
            condition = Document.active_index_version_id.is_not(None)
            query = query.where(condition if page.availability == "searchable" else ~condition)
        if position:
            query = query.where(before_position(Document.created_at, Document.id, position))
        return (
            await self.session.scalars(
                query.order_by(Document.created_at.desc(), Document.id.desc()).limit(page.limit + 1)
            )
        ).all()

    async def jobs(self, kb_id, page, position):
        from app.infrastructure.repositories import before_position

        query = select(Job).where(Job.kb_id == kb_id)
        if page.document_id:
            query = query.where(Job.document_id == str(page.document_id))
        if page.status:
            query = query.where(Job.status == page.status)
        if position:
            query = query.where(before_position(Job.created_at, Job.id, position))
        return (
            await self.session.scalars(
                query.order_by(Job.created_at.desc(), Job.id.desc()).limit(page.limit + 1)
            )
        ).all()

    async def retry_successor(self, job_id):
        return await self.session.scalar(select(Job).where(Job.retry_of_job_id == job_id))

    async def document_versions(self, document_id):
        return (
            await self.session.scalars(
                select(IndexVersion).where(IndexVersion.document_id == document_id)
            )
        ).all()

    async def current_chunks(self, version_id, limit, after_id=None):
        query = select(Chunk).where(Chunk.index_version_id == version_id)
        if after_id:
            previous = await self.chunk(after_id)
            if not previous or previous.index_version_id != version_id:
                return []
            query = query.where(Chunk.ordinal > previous.ordinal)
        return (await self.session.scalars(query.order_by(Chunk.ordinal).limit(limit + 1))).all()
