from app.core.errors import AppError
from app.core.pagination import Cursors, Pagination
from app.infrastructure.repositories import Database
from app.modules.identity.service import active_user, require_other_owner
from app.modules.knowledge_bases.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseList,
    KnowledgeBasePatch,
    KnowledgeBaseResponse,
    MemberList,
    MemberResponse,
    Role,
)


def kb_response(kb, role):
    return KnowledgeBaseResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        my_role=role,
        embedding_profile_id=kb.embedding_profile_id,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


def member_response(member, user):
    return MemberResponse(
        user_id=user.id,
        username=user.username,
        role=member.role,
        is_active=user.is_active,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


async def access(repo, actor_id: str, kb_id: str, *, owner=False, lock=False):
    await active_user(repo, actor_id)
    kb = await repo.knowledge_base(kb_id, lock=lock)
    member = await repo.membership(kb_id, actor_id) if kb and kb.deleted_at is None else None
    if not member:
        # 无成员关系和不存在的库使用相同结果；管理员也不能旁路读取。
        raise AppError(404, "RESOURCE_NOT_FOUND", "知识库不存在或不可访问")
    if owner and member.role != Role.OWNER:
        raise AppError(403, "ROLE_FORBIDDEN", "仅 owner 可以执行此操作")
    return kb, member


class KnowledgeBaseService:
    def __init__(self, database: Database, cursors: Cursors):
        self.database = database
        self.cursors = cursors

    async def create(self, actor_id: str, data: KnowledgeBaseCreate):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            await active_user(repo, actor_id)
            kb = await repo.create_knowledge_base(actor_id, data.name, data.description)
            return kb_response(kb, Role.OWNER)

    async def list(self, actor_id: str, page: Pagination):
        scope = f"knowledge-bases:{actor_id}"
        position = self.cursors.decode(page.cursor, scope)
        async with self.database.transaction() as repo:
            await active_user(repo, actor_id)
            rows = await repo.list_knowledge_bases(actor_id, page.limit + 1, position)
            items = [kb_response(kb, member.role) for kb, member in rows[: page.limit]]
            cursor = (
                self.cursors.encode(
                    scope, rows[page.limit - 1][0].created_at, rows[page.limit - 1][0].id
                )
                if len(rows) > page.limit
                else None
            )
            return KnowledgeBaseList(items=items, next_cursor=cursor)

    async def get(self, actor_id: str, kb_id: str):
        async with self.database.transaction() as repo:
            kb, member = await access(repo, actor_id, kb_id)
            return kb_response(kb, member.role)

    async def update(self, actor_id: str, kb_id: str, data: KnowledgeBasePatch):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            kb, member = await access(repo, actor_id, kb_id, owner=True, lock=True)
            await repo.update_knowledge_base(kb, data.model_dump(exclude_unset=True))
            return kb_response(kb, member.role)

    async def delete(self, actor_id: str, kb_id: str):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            kb, _ = await access(repo, actor_id, kb_id, owner=True, lock=True)
            # 阶段 3 尚无文档/任务表；阶段 4 开放上传前须在此事务检查非空库条件。
            await repo.delete_knowledge_base(kb)

    async def members(self, actor_id: str, kb_id: str, page: Pagination):
        scope = f"members:{kb_id}:{actor_id}"
        position = self.cursors.decode(page.cursor, scope)
        async with self.database.transaction() as repo:
            await access(repo, actor_id, kb_id, owner=True)
            rows = await repo.list_members(kb_id, page.limit + 1, position)
            items = [member_response(member, user) for member, user in rows[: page.limit]]
            cursor = (
                self.cursors.encode(
                    scope, rows[page.limit - 1][0].created_at, rows[page.limit - 1][0].user_id
                )
                if len(rows) > page.limit
                else None
            )
            return MemberList(items=items, next_cursor=cursor)

    async def put_member(self, actor_id: str, kb_id: str, user_id: str, role: Role):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            await access(repo, actor_id, kb_id, owner=True, lock=True)
            target = await repo.user(user_id)
            if not target or not target.is_active:
                raise AppError(404, "RESOURCE_NOT_FOUND", "目标用户不存在或已停用")
            previous = await repo.membership(kb_id, user_id)
            if previous and previous.role == Role.OWNER and role != Role.OWNER:
                await require_other_owner(repo, kb_id, user_id)
            member = await repo.put_member(kb_id, user_id, role.value)
            return member_response(member, target)

    async def remove_member(self, actor_id: str, kb_id: str, user_id: str):
        async with self.database.transaction() as repo:
            await repo.lock_authorization()
            await access(repo, actor_id, kb_id, owner=True, lock=True)
            member = await repo.membership(kb_id, user_id)
            if not member:
                raise AppError(404, "RESOURCE_NOT_FOUND", "成员不存在")
            if member.role == Role.OWNER:
                await require_other_owner(repo, kb_id, user_id)
            await repo.remove_member(member)
