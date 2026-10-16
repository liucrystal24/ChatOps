import os
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import load_settings
from app.core.pagination import Cursors
from app.core.security import LoginLimiter, Security
from app.infrastructure.database import create_engine
from app.infrastructure.models import (
    AuthorizationGuard,
    Chunk,
    Document,
    IndexVersion,
    Job,
    KnowledgeBase,
    Membership,
    User,
)
from app.infrastructure.repositories import Database
from app.main import create_app
from app.modules.identity.service import IdentityService
from app.modules.knowledge_bases.service import KnowledgeBaseService


class IntegrationBundle(SimpleNamespace):
    def __repr__(self):
        return "IntegrationBundle(专用测试资源，凭据不展示)"


@pytest.fixture(scope="session")
def prepared_settings():
    settings = load_settings()
    database = make_url(settings.database_url.get_secret_value()).database
    if os.getenv("ALLOW_TEST_DATABASE") != "1" or not database or not database.endswith("_test"):
        pytest.fail("只允许显式启用的 *_test 专用数据库")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], capture_output=True, timeout=30
    )
    assert result.returncode == 0, "专用测试库迁移失败（未输出可能含凭据的驱动错误）"
    return settings


@pytest.fixture
async def bundle(prepared_settings):
    settings = prepared_settings
    engine = create_engine(settings)
    database = Database(async_sessionmaker(engine, expire_on_commit=False))
    identity = IdentityService(database, Security(settings), LoginLimiter())
    knowledge = KnowledgeBaseService(database, Cursors(settings.auth_secret.get_secret_value()))
    prefix = f"test_{uuid4().hex}"
    password = f"private-{uuid4().hex}"
    users = {}
    ids = []
    client = None
    try:
        for name in ("alice", "bob", "admin"):
            user = await identity.create_user(f"{prefix}_{name}", password, name == "admin")
            users[name] = user
            ids.append(str(user.id))
        client = TestClient(create_app(settings))
        client.__enter__()
        headers = {}
        for name, user in users.items():
            response = client.post(
                "/api/v1/auth/login", json={"username": user.username, "password": password}
            )
            assert response.status_code == 200
            headers[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
        yield IntegrationBundle(
            settings=settings,
            engine=engine,
            database=database,
            identity=identity,
            knowledge=knowledge,
            users=users,
            headers=headers,
            password=password,
            owned_ids=ids,
            client=client,
        )
    finally:
        if client:
            client.__exit__(None, None, None)
        if ids:
            # 仅清理由本 fixture 创建的 UUID，不清空业务表或共享数据库。
            async with engine.begin() as connection:
                await connection.execute(select(AuthorizationGuard.id).with_for_update())
                kbs = (
                    (
                        await connection.execute(
                            select(KnowledgeBase.id).where(KnowledgeBase.created_by.in_(ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if kbs:
                    keys = (
                        await connection.scalars(
                            select(Document.storage_key).where(Document.kb_id.in_(kbs))
                        )
                    ).all()
                    await connection.execute(
                        update(Document)
                        .where(Document.kb_id.in_(kbs))
                        .values(active_index_version_id=None, current_job_id=None)
                    )
                    await connection.execute(delete(Chunk).where(Chunk.kb_id.in_(kbs)))
                    await connection.execute(
                        delete(IndexVersion).where(IndexVersion.kb_id.in_(kbs))
                    )
                    await connection.execute(
                        update(Job).where(Job.kb_id.in_(kbs)).values(retry_of_job_id=None)
                    )
                    await connection.execute(delete(Job).where(Job.kb_id.in_(kbs)))
                    await connection.execute(delete(Document).where(Document.kb_id.in_(kbs)))
                    for key in keys:
                        (settings.files_dir / key).unlink(missing_ok=True)
                    await connection.execute(delete(Membership).where(Membership.kb_id.in_(kbs)))
                    await connection.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(kbs)))
                await connection.execute(delete(User).where(User.id.in_(ids)))
        await engine.dispose()
        identity.security.close()
