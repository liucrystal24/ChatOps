import asyncio
import hashlib
import json
import math

import httpx

from app.core.embedding_profile import TOKENIZER_REVISION
from app.modules.indexing.errors import IndexFailure


class Embedding:
    def __init__(self, settings):
        self.settings = settings
        key = (
            settings.siliconflow_api_key.get_secret_value() if settings.siliconflow_api_key else ""
        )
        self.configured = bool(key)
        self.client = httpx.AsyncClient(
            base_url="https://api.siliconflow.cn/v1/",
            headers={"Authorization": "Bearer " + key},
            timeout=settings.embedding_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )
        self.slots = asyncio.Semaphore(2)

    async def close(self):
        await self.client.aclose()

    async def embed(self, texts, dimension=None):
        if not self.configured:
            raise IndexFailure("MODEL_NOT_CONFIGURED", "请配置 SILICONFLOW_API_KEY")
        if not texts or len(texts) > self.settings.embedding_batch_size:
            raise IndexFailure("EMBEDDING_INPUT_INVALID", "向量化批量参数无效")
        try:
            async with self.slots:
                response = await self.client.post(
                    "embeddings",
                    json={
                        "model": self.settings.embedding_model,
                        "input": texts,
                        "encoding_format": "float",
                    },
                )
        except httpx.TimeoutException:
            raise IndexFailure("UPSTREAM_TIMEOUT", "向量服务请求超时", retryable=True) from None
        except httpx.HTTPError:
            raise IndexFailure("UPSTREAM_UNAVAILABLE", "向量服务暂不可用", retryable=True) from None
        if response.status_code in {429, 408} or response.status_code >= 500:
            raise IndexFailure("UPSTREAM_UNAVAILABLE", "向量服务繁忙，请稍后重试", retryable=True)
        if not response.is_success:
            raise IndexFailure(
                "EMBEDDING_REQUEST_REJECTED", "向量服务拒绝请求，请检查凭据、模型权限及输入限制"
            )
        try:
            payload = response.json()
            if payload["model"] != self.settings.embedding_model or len(payload["data"]) != len(
                texts
            ):
                raise ValueError
            values = sorted(payload["data"], key=lambda item: item["index"])
            if [item["index"] for item in values] != list(range(len(texts))):
                raise ValueError
            vectors = [item["embedding"] for item in values]
            size = dimension or len(vectors[0])
            if size < 1 or size > 65536:
                raise ValueError
            for vector in vectors:
                if (
                    len(vector) != size
                    or not all(
                        isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x)
                        for x in vector
                    )
                    or not any(vector)
                ):
                    raise ValueError
            return vectors
        except (KeyError, ValueError, TypeError, IndexError):
            raise IndexFailure(
                "EMBEDDING_RESPONSE_INVALID", "向量数量、模型、维度或数值校验失败"
            ) from None

    async def probe(self):
        vectors = await self.embed(["向量维度与模型配置探测"])
        profile = {
            "provider": "siliconflow",
            "model": self.settings.embedding_model,
            "revision": "provider-managed",
            "dimension": len(vectors[0]),
            "distance": "Cosine",
            "preprocessing": "utf8-page-text-v1",
            "tokenizer_revision": TOKENIZER_REVISION,
        }
        digest = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        profile.update(id=digest, collection="rag_" + digest[:40])
        return profile
