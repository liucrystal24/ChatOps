from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.models import AuthorizationGuard, KnowledgeBase, Membership, User


def before_position(date_column, id_column, position):
    created_at, entity_id = position
    return or_(date_column < created_at, and_(date_column == created_at, id_column < entity_id))


class Database:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    @asynccontextmanager
    async def transaction(self):
        async with self.sessions() as session, session.begin():
            yield Repository(session)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_authorization(self):
        result = await self.session.execute(
            select(AuthorizationGuard.id).where(AuthorizationGuard.id == 1).with_for_update()
        )
        result.scalar_one()

    async def user(self, user_id: str):
        return await self.session.get(User, user_id)

    async def find_user(self, username: str):
        return await self.session.scalar(select(User).where(User.username == username))

    async def users(self):
        return (await self.session.scalars(select(User).order_by(User.username))).all()

    async def create_user(self, username: str, password_hash: str, is_admin: bool):
        user = User(
            id=str(uuid4()),
            username=username,
            password_hash=password_hash,
            is_active=True,
            is_admin=is_admin,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def set_user_active(self, user: User, active: bool):
        user.is_active = active
        await self.session.flush()

    async def knowledge_base(self, kb_id: str, *, lock=False):
        query = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def membership(self, kb_id: str, user_id: str):
        return await self.session.get(Membership, (kb_id, user_id))

    async def owned_knowledge_bases(self, user_id: str):
        return (
            await self.session.scalars(
                select(KnowledgeBase)
                .join(Membership, Membership.kb_id == KnowledgeBase.id)
                .where(
                    Membership.user_id == user_id,
                    Membership.role == "owner",
                    KnowledgeBase.deleted_at.is_(None),
                )
                .order_by(KnowledgeBase.id)
                .with_for_update()
            )
        ).all()

    async def active_owner_count(self, kb_id: str, excluding_user: str):
        return await self.session.scalar(
            select(func.count())
            .select_from(Membership)
            .join(User, Membership.user_id == User.id)
            .where(
                Membership.kb_id == kb_id,
                Membership.role == "owner",
                User.is_active.is_(True),
                User.id != excluding_user,
            )
        )

    async def create_knowledge_base(self, owner_id: str, name: str, description: str):
        kb = KnowledgeBase(id=str(uuid4()), name=name, description=description, created_by=owner_id)
        self.session.add(kb)
        await self.session.flush()
        await self.session.refresh(kb)
        await self.put_member(kb.id, owner_id, "owner")
        return kb

    async def update_knowledge_base(self, kb: KnowledgeBase, changes: dict):
        for key, value in changes.items():
            setattr(kb, key, value)
        await self.session.flush()
        await self.session.refresh(kb)
        return kb

    async def delete_knowledge_base(self, kb: KnowledgeBase):
        kb.deleted_at = func.utc_timestamp(6)
        await self.session.flush()

    async def list_knowledge_bases(self, user_id: str, limit: int, position=None):
        query = (
            select(KnowledgeBase, Membership)
            .join(Membership, Membership.kb_id == KnowledgeBase.id)
            .where(Membership.user_id == user_id, KnowledgeBase.deleted_at.is_(None))
        )
        if position:
            query = query.where(
                before_position(KnowledgeBase.created_at, KnowledgeBase.id, position)
            )
        return (
            await self.session.execute(
                query.order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc()).limit(
                    limit
                )
            )
        ).all()

    async def list_members(self, kb_id: str, limit: int, position=None):
        query = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.kb_id == kb_id)
        )
        if position:
            query = query.where(
                before_position(Membership.created_at, Membership.user_id, position)
            )
        return (
            await self.session.execute(
                query.order_by(Membership.created_at.desc(), Membership.user_id.desc()).limit(limit)
            )
        ).all()

    async def put_member(self, kb_id: str, user_id: str, role: str):
        member = await self.membership(kb_id, user_id)
        if member:
            member.role = role
        else:
            member = Membership(kb_id=kb_id, user_id=user_id, role=role)
            self.session.add(member)
        await self.session.flush()
        await self.session.refresh(member)
        return member

    async def remove_member(self, member: Membership):
        await self.session.delete(member)
        await self.session.flush()
