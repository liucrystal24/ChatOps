import httpx

from app.modules.indexing.errors import IndexFailure


class Vectors:
    def __init__(self, settings):
        self.client = httpx.AsyncClient(
            base_url=str(settings.qdrant_url),
            headers={"api-key": settings.qdrant_api_key.get_secret_value()},
            timeout=5,
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, body=None, *, allow_missing=False):
        try:
            response = await self.client.request(method, path, json=body)
            if allow_missing and response.status_code == 404:
                return None
            if not response.is_success:
                raise IndexFailure("VECTOR_STORAGE_UNAVAILABLE", "向量存储请求失败", retryable=True)
            return response.json()["result"]
        except (httpx.HTTPError, ValueError, KeyError):
            raise IndexFailure(
                "VECTOR_STORAGE_UNAVAILABLE", "向量存储暂不可用", retryable=True
            ) from None

    async def ensure(self, profile):
        name = profile["collection"]
        details = await self.request("GET", f"/collections/{name}", allow_missing=True)
        if details is None:
            # 多 Worker 可同时发现不存在；创建冲突后重新读取并校验，不能忽略其他错误。
            try:
                await self.request(
                    "PUT",
                    f"/collections/{name}",
                    {"vectors": {"dense": {"size": profile["dimension"], "distance": "Cosine"}}},
                )
            except IndexFailure:
                pass
            details = await self.request("GET", f"/collections/{name}")
        if details["config"]["params"]["vectors"] != {
            "dense": {"size": profile["dimension"], "distance": "Cosine"}
        }:
            vector = details["config"]["params"]["vectors"].get("dense", {})
            if vector.get("size") != profile["dimension"] or vector.get("distance") != "Cosine":
                raise IndexFailure("EMBEDDING_PROFILE_MISMATCH", "向量库结构与模型配置不一致")
        for field in ["kb_id", "index_version_id", "document_id", "file_type", "tags"]:
            await self.request(
                "PUT",
                f"/collections/{name}/index?wait=true",
                {"field_name": field, "field_schema": "keyword"},
            )

    async def upsert(self, profile, points):
        await self.request(
            "PUT", f"/collections/{profile['collection']}/points?wait=true", {"points": points}
        )

    async def delete_version(self, collection, version_id):
        await self.request(
            "POST",
            f"/collections/{collection}/points/delete?wait=true",
            {"filter": {"must": [{"key": "index_version_id", "match": {"value": version_id}}]}},
            allow_missing=True,
        )

    async def count(self, collection, kb_id, version_id):
        result = await self.request(
            "POST",
            f"/collections/{collection}/points/count",
            {"filter": scope_filter(kb_id, [version_id]), "exact": True},
        )
        return result["count"]

    async def query(
        self, profile, kb_id, versions, vector, limit, filters=None, exclude=(), threshold=None
    ):
        # 空有效版本集合必须短路，绝不能降级成无 filter 的检索。
        if not versions:
            return []
        scope = scope_filter(kb_id, versions)
        for field, values in (filters or {}).items():
            if values:
                scope["must"].append({"key": field, "match": {"any": values}})
        if exclude:
            scope["must_not"] = [{"has_id": list(exclude)}]
        body = {
            "query": vector,
            "using": "dense",
            "filter": scope,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        if threshold is not None:
            body["score_threshold"] = threshold
        result = await self.request(
            "POST", f"/collections/{profile['collection']}/points/query", body
        )
        return result["points"]


def scope_filter(kb_id, versions):
    return {
        "must": [
            {"key": "kb_id", "match": {"value": kb_id}},
            {"key": "index_version_id", "match": {"any": list(versions)}},
        ]
    }
