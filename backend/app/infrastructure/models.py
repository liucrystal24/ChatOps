from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.mysql import CHAR, DATETIME, JSON, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=text("CURRENT_TIMESTAMP(6)"), onupdate=func.utc_timestamp(6)
    )


class AuthorizationGuard(Base):
    __tablename__ = "authorization_guard"
    __table_args__ = (CheckConstraint("id = 1", name="ck_authorization_guard_singleton"),)
    id: Mapped[int] = mapped_column(TINYINT(unsigned=True), primary_key=True, autoincrement=False)


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    username: Mapped[str] = mapped_column(String(64, collation="utf8mb4_bin"), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class KnowledgeBase(Timestamps, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(
            "(embedding_profile_id IS NULL AND embedding_profile IS NULL) OR "
            "(embedding_profile_id IS NOT NULL AND embedding_profile IS NOT NULL)",
            name="ck_kb_embedding_profile_pair",
        ),
        Index("ix_kb_creator_deleted_created", "created_by", "deleted_at", "created_at"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    embedding_profile_id: Mapped[str | None] = mapped_column(String(64), default=None)
    embedding_profile: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), default=None)


class Membership(Timestamps, Base):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="ck_member_role"),
        Index("ix_member_user_kb", "user_id", "kb_id"),
    )
    kb_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16, collation="utf8mb4_bin"))
