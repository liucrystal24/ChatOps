from app.core.errors import AppError
from app.core.security import LoginLimiter, Security, auth_invalid
from app.infrastructure.repositories import Database
from app.modules.identity.schemas import TokenResponse, UserResponse, normalize_username


async def active_user(repo, user_id: str):
    user = await repo.user(user_id)
    if user is None:
        raise auth_invalid()
    if not user.is_active:
        raise AppError(401, "USER_DISABLED", "账号已停用", {"WWW-Authenticate": "Bearer"})
    return user


async def require_other_owner(repo, kb_id: str, user_id: str):
    if not await repo.active_owner_count(kb_id, user_id):
        raise AppError(409, "LAST_OWNER_REQUIRED", "知识库至少需要一名有效 owner，请先移交权限")


class IdentityService:
    def __init__(self, database: Database, security: Security, limiter: LoginLimiter):
        self.database = database
        self.security = security
        self.limiter = limiter

    async def login(self, username: str, password: str, peer: str):
        username = normalize_username(username)
        self.limiter.consume(peer, username)
        async with self.database.transaction() as repo:
            user = await repo.find_user(username)
            encoded = user.password_hash if user else None
            user_id = user.id if user else None
            is_active = user.is_active if user else False
        # Argon2 消耗 CPU/内存，不占用数据库事务；未知账号也校验随机 dummy hash。
        valid = await self.security.verify(password, encoded)
        if not valid or not is_active:
            raise auth_invalid()
        async with self.database.transaction() as repo:
            current = await repo.user(user_id)
            if not current or not current.is_active:
                raise auth_invalid()
        return TokenResponse(
            access_token=self.security.issue(user_id), expires_in=self.security.ttl
        )

    async def me(self, user_id: str):
        async with self.database.transaction() as repo:
            return UserResponse.model_validate(await active_user(repo, user_id))

    async def create_user(self, username: str, password: str, is_admin=False):
        username = normalize_username(username)
        encoded = await self.security.hash_password(password)
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            if await repo.find_user(username):
                raise AppError(409, "USERNAME_EXISTS", "用户名已存在")
            return UserResponse.model_validate(await repo.create_user(username, encoded, is_admin))

    async def set_active(self, username: str, active: bool):
        username = normalize_username(username)
        async with self.database.transaction() as repo:
            # 停用与成员增删共用锁，避免两个操作各自看到“另一个 owner 仍有效”。
            await repo.lock_authorization()
            user = await repo.find_user(username)
            if not user:
                raise AppError(404, "RESOURCE_NOT_FOUND", "用户不存在")
            if not active and user.is_active:
                for kb in await repo.owned_knowledge_bases(user.id):
                    await require_other_owner(repo, kb.id, user.id)
            await repo.set_user_active(user, active)
            return {"id": user.id, "username": user.username, "is_active": active}

    async def list_users(self):
        async with self.database.transaction() as repo:
            return [
                {
                    "id": user.id,
                    "username": user.username,
                    "is_active": user.is_active,
                    "is_admin": user.is_admin,
                }
                for user in await repo.users()
            ]
