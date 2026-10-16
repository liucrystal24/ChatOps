import asyncio
from dataclasses import dataclass

from app.core.errors import AppError
from app.infrastructure.tokenization import load_tokenizer
from app.modules.indexing.errors import IndexFailure
from app.modules.knowledge_bases.service import access


@dataclass(frozen=True)
class Scope:
    kb_id: str
    profile: dict | None
    versions: tuple[str, ...]
    filters: dict


class RetrievalService:
    def __init__(self, database, embedding, vectors, settings):
        self.database, self.embedding, self.vectors, self.settings = (
            database,
            embedding,
            vectors,
            settings,
        )
        self._tokenizer = None

    async def scope(self, actor_id, kb_id, data):
        async with self.database.transaction() as repo:
            kb, _ = await access(repo, actor_id, kb_id)
            requested = data.metadata_filter
            if requested.document_ids:
                for document_id in requested.document_ids:
                    document = await repo.document(str(document_id))
                    if not document or document.kb_id != kb_id or document.deleted_at is not None:
                        raise AppError(404, "FILTER_RESOURCE_NOT_FOUND", "筛选文档不存在或不可访问")
            filters = {}
            if requested.document_ids:
                filters["document_id"] = [str(value) for value in requested.document_ids]
            if requested.file_types:
                filters["file_type"] = requested.file_types
            if requested.tags_any:
                filters["tags"] = requested.tags_any
            rows = await repo.active_versions(kb_id)
            versions = []
            for document, version in rows:
                if not matches(document, requested):
                    continue
                if (
                    version.embedding_profile_id != kb.embedding_profile_id
                    or version.config_snapshot["embedding_profile"] != kb.embedding_profile
                ):
                    raise IndexFailure(
                        "EMBEDDING_PROFILE_MISMATCH", "索引版本与知识库模型配置不一致"
                    )
                versions.append(version.id)
            return Scope(kb_id, kb.embedding_profile, tuple(versions), filters)

    async def search(self, actor_id, kb_id, data):
        scope = await self.scope(actor_id, kb_id, data)
        if not scope.versions:
            return []
        if self._tokenizer is None:
            self._tokenizer = await asyncio.to_thread(load_tokenizer, self.settings.tokenizer_path)
        count = await asyncio.to_thread(lambda: len(self._tokenizer.encode(data.question).ids))
        if count > 8192:
            raise AppError(422, "QUERY_TOKEN_LIMIT", "检索问题超过模型 token 上限")
        if (
            scope.profile["model"] != self.settings.embedding_model
            or scope.profile["provider"] != "siliconflow"
        ):
            raise IndexFailure("EMBEDDING_PROFILE_MISMATCH", "当前服务与知识库模型不一致")
        vector = (await self.embedding.embed([data.question], scope.profile["dimension"]))[0]
        seen = set()
        results = []
        # 并发删除/重建会丢弃候选；最多两轮补召回，每轮重新读取权限和有效版本。
        for _ in range(3):
            current = await self.scope(actor_id, kb_id, data)
            if not current.versions:
                break
            if current.profile != scope.profile:
                raise IndexFailure("EMBEDDING_PROFILE_MISMATCH", "查询期间模型配置发生变化")
            hits = await self.vectors.query(
                scope.profile,
                kb_id,
                current.versions,
                vector,
                min(100, data.top_k * 3),
                current.filters,
                seen,
                data.score_threshold,
            )
            if not hits:
                break
            seen.update(str(hit["id"]) for hit in hits)
            async with self.database.transaction() as repo:
                kb, _ = await access(repo, actor_id, kb_id)
                chunks = {c.id: c for c in await repo.chunks_by_ids([str(h["id"]) for h in hits])}
                for hit in hits:
                    chunk = chunks.get(str(hit["id"]))
                    if (
                        not chunk
                        or chunk.kb_id != kb_id
                        or chunk.index_version_id not in current.versions
                    ):
                        continue
                    document = await repo.document(chunk.document_id)
                    if (
                        not document
                        or document.deleted_at is not None
                        or document.kb_id != kb_id
                        or document.active_index_version_id != chunk.index_version_id
                        or not matches(document, data.metadata_filter)
                    ):
                        continue
                    version = await repo.version(chunk.index_version_id)
                    if (
                        version.status != "published"
                        or version.embedding_profile_id != kb.embedding_profile_id
                    ):
                        continue
                    results.append(
                        dict(
                            chunk_id=chunk.id,
                            document_id=document.id,
                            index_version_id=chunk.index_version_id,
                            filename=document.filename,
                            text=chunk.text,
                            page_no=chunk.page_no,
                            section_title=chunk.section_title,
                            char_start=chunk.char_start,
                            char_end=chunk.char_end,
                            score=hit["score"],
                        )
                    )
                    if len(results) == data.top_k:
                        break
            if len(results) >= data.top_k:
                break
        # 输出前再复核全部结果，避免补召回期间早先候选失效。
        async with self.database.transaction() as repo:
            await access(repo, actor_id, kb_id)
            valid = []
            for result in results:
                document = await repo.document(result["document_id"])
                if (
                    document
                    and document.deleted_at is None
                    and document.active_index_version_id == result["index_version_id"]
                    and matches(document, data.metadata_filter)
                ):
                    valid.append(result)
            return valid[: data.top_k]


def matches(document, filters):
    return (
        (not filters.document_ids or document.id in {str(value) for value in filters.document_ids})
        and (not filters.file_types or document.file_type in filters.file_types)
        and (not filters.tags_any or bool(set(document.tags) & set(filters.tags_any)))
    )
