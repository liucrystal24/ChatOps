import asyncio
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.core.pagination import Cursors
from app.core.security import LoginLimiter, Security
from app.modules.identity.schemas import normalize_username
from app.modules.knowledge_bases.schemas import KnowledgeBasePatch
from tests.unit.test_config import config

pytestmark = pytest.mark.unit


@pytest.fixture
def security():
    instance = Security(config())
    try:
        yield instance
    finally:
        instance.close()


def test_username_normalization_and_invalid_names():
    assert normalize_username("Ａlice.Admin") == "alice.admin"
    for username in ("", " bad", "管理员", "-alice", "a" * 65):
        with pytest.raises(ValueError):
            normalize_username(username)


async def test_argon2_success_wrong_unknown_and_creation_policy(security):
    password = "  a-private-password  "
    encoded = await security.hash_password(password)
    assert encoded.startswith("$argon2id$")
    assert password not in encoded
    assert await security.verify(password, encoded)
    assert not await security.verify(password.strip(), encoded)
    assert not await security.verify("wrong-password", encoded)
    assert not await security.verify("wrong-password", None)
    unicode_password = "🔐" * 200
    assert await security.verify(unicode_password, await security.hash_password(unicode_password))
    for value in ("short", "a" * 257):
        with pytest.raises(AppError, match="PASSWORD_POLICY_INVALID"):
            await security.hash_password(value)


async def test_password_workers_remain_bounded_when_requests_cancel(security, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    lock = threading.Lock()
    active = [0]

    def slow_hash(self, password):
        with lock:
            active[0] += 1
            if active[0] >= 4:
                started.set()
        release.wait(5)
        return "test-only-hash"

    monkeypatch.setattr(type(security.hasher), "hash", slow_hash)
    tasks = [asyncio.create_task(security.hash_password("valid-test-password")) for _ in range(5)]
    try:
        assert await asyncio.to_thread(started.wait, 3)
        for task in tasks[:4]:
            task.cancel()
        await asyncio.gather(*tasks[:4], return_exceptions=True)
        await asyncio.sleep(0)
        assert active[0] == 4 and not tasks[4].done()
        release.set()
        assert await asyncio.wait_for(tasks[4], 3) == "test-only-hash"
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_jwt_requires_signature_claims_and_expiration(security):
    user_id = str(uuid4())
    token = security.issue(user_id)
    assert security.subject(token) == user_id
    claims = jwt.decode(token, security.key, algorithms=["HS256"], audience="chat-rag-web")
    assert claims["exp"] - claims["iat"] == 1800
    assert "role" not in claims and "password" not in claims
    for changes in (
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"aud": "another-app"},
        {"iss": "other"},
        {"sub": "invalid"},
    ):
        invalid = jwt.encode({**claims, **changes}, security.key, algorithm="HS256")
        with pytest.raises(AppError, match="AUTH_INVALID"):
            security.subject(invalid)
    for invalid in (
        jwt.encode(claims, "another-secret" * 4, algorithm="HS256"),
        jwt.encode(claims, security.key, algorithm="HS384"),
        jwt.encode({"sub": user_id}, security.key, algorithm="HS256"),
        "invalid-token",
        "a" * 5000,
    ):
        with pytest.raises(AppError, match="AUTH_INVALID"):
            security.subject(invalid)


def test_login_limits_account_peer_expiry_and_bounded_state():
    now = [100.0]
    limiter = LoginLimiter(lambda: now[0])
    for i in range(10):
        limiter.consume(f"peer{i}", "alice")
    with pytest.raises(AppError) as error:
        limiter.consume("new-peer", "alice")
    assert error.value.status == 429 and error.value.headers["Retry-After"] == "60"
    now[0] += 60
    limiter.consume("peer", "alice")
    for i in range(99):
        limiter.consume("peer", f"user{i}")
    with pytest.raises(AppError):
        limiter.consume("peer", "different-user")
    assert all("alice" not in key for key in limiter.buckets)
    limiter.buckets = {f"occupied-{index}": deque([now[0]]) for index in range(9999)}
    with pytest.raises(AppError, match="LOGIN_RATE_LIMITED"):
        limiter.consume("new-peer", "new-user")
    assert len(limiter.buckets) == 9999


def test_cursor_signature_scope_and_position():
    cursors = Cursors("cursor-key" * 8)
    point = (datetime(2026, 10, 15, 1, 2, 3, 123456), str(uuid4()))
    token = cursors.encode("alice-list", *point)
    assert cursors.decode(token, "alice-list") == point
    for invalid, scope in [(token, "bob-list"), (token[1:], "alice-list"), ("!", "alice-list")]:
        with pytest.raises(AppError, match="INVALID_CURSOR"):
            cursors.decode(invalid, scope)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": None},
        {"description": None},
        {"name": "  "},
        {"embedding_profile_id": "private-model"},
    ],
)
def test_patch_rejects_empty_null_and_internal_fields(body):
    with pytest.raises(ValidationError):
        KnowledgeBasePatch.model_validate(body)
