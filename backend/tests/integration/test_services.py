import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.infrastructure.database import create_engine
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings():
    settings = load_settings()
    database = make_url(settings.database_url.get_secret_value()).database
    if os.getenv("ALLOW_TEST_DATABASE") != "1" or not database or not database.endswith("_test"):
        pytest.fail("集成测试只允许 ALLOW_TEST_DATABASE=1 且库名以 _test 结尾的专用数据库")
    return settings


def migrate(*args):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args], capture_output=True, text=True, timeout=30
    )
    # 失败时不把驱动输出（可能带连接信息）直接打印到测试报告。
    assert result.returncode == 0, "Alembic 迁移失败，请在独立测试环境诊断"


def test_mysql_migrations_readiness_and_idempotency(settings):
    async def tables():
        engine = create_engine(settings)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        finally:
            await engine.dispose()

    existing = asyncio.run(tables())
    expected = {
        "documents",
        "jobs",
        "document_index_versions",
        "chunks",
        "alembic_version",
        "authorization_guard",
        "users",
        "knowledge_bases",
        "knowledge_base_members",
    }
    assert set(existing) <= expected, "迁移回退只允许已知结构的专用空测试库"

    async def assert_empty():
        engine = create_engine(settings)
        try:
            async with engine.connect() as connection:
                for table in ("users", "knowledge_bases", "knowledge_base_members"):
                    if table in existing:
                        assert (
                            await connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0
                        ), "测试库含记录，拒绝回退迁移"
        finally:
            await engine.dispose()

    asyncio.run(assert_empty())
    migrate("downgrade", "base")
    try:
        with TestClient(create_app(settings)) as client:
            before = client.get("/health/ready")
            assert before.status_code == 503
            assert before.json()["checks"]["migrations"]["status"] == "pending"
            migrate("upgrade", "head")
            migrate("upgrade", "head")
            assert set(asyncio.run(tables())) == expected
            report = client.get("/health/ready")
            assert report.status_code == 200
            assert all(check["status"] == "ok" for check in report.json()["checks"].values())
            assert client.get("/health/live").status_code == 200
    finally:
        migrate("upgrade", "head")


async def test_mysql_utf8mb4_utc_and_qdrant_auth(settings):
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT @@character_set_connection")) == "utf8mb4"
            assert await connection.scalar(text("SELECT @@session.time_zone")) == "+00:00"
    finally:
        await engine.dispose()
    async with httpx.AsyncClient(base_url=str(settings.qdrant_url), trust_env=False) as client:
        assert (await client.get("/collections")).status_code in {401, 403}
        authenticated = await client.get(
            "/collections", headers={"api-key": settings.qdrant_api_key.get_secret_value()}
        )
        assert authenticated.status_code == 200


async def test_qdrant_real_write_read_and_delete_test_collection(settings):
    # 明确的测试向量只验证真实存储协议，不代表已接入 Embedding 或检索功能。
    collection = f"test_stage2_{uuid4().hex}"
    headers = {"api-key": settings.qdrant_api_key.get_secret_value()}
    async with httpx.AsyncClient(
        base_url=str(settings.qdrant_url), headers=headers, trust_env=False, timeout=10
    ) as client:
        created = await client.put(
            f"/collections/{collection}", json={"vectors": {"size": 2, "distance": "Cosine"}}
        )
        assert created.status_code == 200
        try:
            point = str(uuid4())
            response = await client.put(
                f"/collections/{collection}/points?wait=true",
                json={"points": [{"id": point, "vector": [1.0, 0.0], "payload": {"test": True}}]},
            )
            assert response.status_code == 200
            read = await client.get(f"/collections/{collection}/points/{point}")
            assert read.status_code == 200 and read.json()["result"]["payload"] == {"test": True}
        finally:
            assert (await client.delete(f"/collections/{collection}")).status_code == 200
