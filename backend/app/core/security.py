import asyncio
import hashlib
import math
import secrets
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings
from app.core.errors import AppError


def auth_invalid():
    return AppError(401, "AUTH_INVALID", "凭据无效", {"WWW-Authenticate": "Bearer"})


class Security:
    def __init__(self, settings: Settings):
        self.key = settings.auth_secret.get_secret_value()
        self.ttl = settings.auth_token_ttl_seconds
        self.hasher = PasswordHasher()
        self.hash_slots = asyncio.Semaphore(4)
        # 请求取消不会终止已开始的 Argon2；专用执行器保证物理线程数仍最多为 4。
        self.hash_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="auth")
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    def close(self):
        self.hash_pool.shutdown(wait=True, cancel_futures=True)

    async def hash_password(self, password: str) -> str:
        if not 12 <= len(password) <= 256:
            raise AppError(422, "PASSWORD_POLICY_INVALID", "创建密码需为 12–256 字符")
        async with self.hash_slots:
            return await asyncio.get_running_loop().run_in_executor(
                self.hash_pool, self.hasher.hash, password
            )

    async def verify(self, password: str, encoded: str | None) -> bool:
        def check():
            try:
                return self.hasher.verify(encoded or self.dummy_hash, password)
            except (VerificationError, InvalidHashError):
                return False

        async with self.hash_slots:
            return await asyncio.get_running_loop().run_in_executor(self.hash_pool, check)

    def issue(self, user_id: str) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": user_id,
                "iat": now,
                "nbf": now,
                "exp": now + timedelta(seconds=self.ttl),
                "iss": "chat-rag",
                "aud": "chat-rag-web",
                "jti": str(uuid4()),
            },
            self.key,
            algorithm="HS256",
        )

    def subject(self, token: str) -> str:
        try:
            if len(token) > 4096:
                raise ValueError
            claims = jwt.decode(
                token,
                self.key,
                algorithms=["HS256"],
                issuer="chat-rag",
                audience="chat-rag-web",
                options={"require": ["sub", "exp", "iat", "nbf", "iss", "aud", "jti"]},
            )
            return str(UUID(claims["sub"]))
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
            raise auth_invalid() from None


class LoginLimiter:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.window = 60
        self.buckets: dict[str, deque] = {}

    def consume(self, peer: str, username: str):
        now = self.clock()
        # 单进程事件循环中不 await，两个配额检查和写入作为一次不可穿插操作。
        for key in list(self.buckets):
            bucket = self.buckets[key]
            while bucket and bucket[0] <= now - self.window:
                bucket.popleft()
            if not bucket:
                del self.buckets[key]
        keys = [
            ("peer:" + hashlib.sha256(peer.encode()).hexdigest(), 100),
            ("user:" + hashlib.sha256(username.encode()).hexdigest(), 10),
        ]
        if len(self.buckets) + sum(key not in self.buckets for key, _ in keys) > 10000:
            raise AppError(
                429, "LOGIN_RATE_LIMITED", "登录过于频繁，请稍后重试", {"Retry-After": "60"}
            )
        for key, limit in keys:
            bucket = self.buckets.get(key, ())
            if len(bucket) >= limit or (key not in self.buckets and len(self.buckets) >= 10000):
                retry = max(1, math.ceil(bucket[0] + self.window - now)) if bucket else 60
                raise AppError(
                    429,
                    "LOGIN_RATE_LIMITED",
                    "登录过于频繁，请稍后重试",
                    {"Retry-After": str(retry)},
                )
        for key, _ in keys:
            self.buckets.setdefault(key, deque()).append(now)
