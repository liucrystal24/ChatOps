from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.mysql import CHAR, DATETIME, JSON, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=sql_text("CURRENT_TIMESTAMP(6)")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=sql_text("CURRENT_TIMESTAMP(6)"),
        onupdate=func.utc_timestamp(6),
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


# 循环指针后建复合外键，保证当前任务/有效版本确实属于同一文档和知识库。
from sqlalchemy import BigInteger, Double, ForeignKeyConstraint, Integer, UniqueConstraint  # noqa: E402
from sqlalchemy.dialects.mysql import MEDIUMTEXT  # noqa: E402


class Document(Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "kb_id", name="uq_document_scope"),
        ForeignKeyConstraint(
            ["current_job_id", "id", "kb_id"],
            ["jobs.id", "jobs.document_id", "jobs.kb_id"],
            name="fk_document_current_job",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["active_index_version_id", "id", "kb_id"],
            [
                "document_index_versions.id",
                "document_index_versions.document_id",
                "document_index_versions.kb_id",
            ],
            name="fk_document_active_version",
            use_alter=True,
        ),
        CheckConstraint("file_type IN ('txt','md','pdf')", name="ck_document_type"),
        CheckConstraint("size_bytes > 0", name="ck_document_size"),
        Index("ix_document_kb_deleted_created", "kb_id", "deleted_at", "created_at"),
        Index("ix_document_kb_sha", "kb_id", "content_sha256"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    file_type: Mapped[str] = mapped_column(String(8, collation="utf8mb4_bin"))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_sha256: Mapped[str] = mapped_column(CHAR(64, charset="ascii"))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    tags: Mapped[list] = mapped_column(JSON)
    active_index_version_id: Mapped[str | None] = mapped_column(CHAR(36, charset="ascii"))
    current_job_id: Mapped[str | None] = mapped_column(CHAR(36, charset="ascii"))
    deleted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    purged_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class Job(Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("id", "document_id", "kb_id", name="uq_job_scope"),
        ForeignKeyConstraint(
            ["document_id", "kb_id"], ["documents.id", "documents.kb_id"], name="fk_job_document"
        ),
        CheckConstraint("kind IN ('index','purge')", name="ck_job_kind"),
        CheckConstraint(
            "status IN ('queued','running','retry_wait','succeeded','failed','cancelled')",
            name="ck_job_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 3", name="ck_job_attempts"
        ),
        Index("ix_job_queue", "status", "available_at", "id"),
        Index("ix_job_lease", "status", "lease_expires_at"),
        Index("ix_job_document_created", "document_id", "created_at"),
        Index("ix_job_kb_created", "kb_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    kb_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    document_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    kind: Mapped[str] = mapped_column(String(16, collation="utf8mb4_bin"))
    status: Mapped[str] = mapped_column(String(20, collation="utf8mb4_bin"), default="queued")
    stage: Mapped[str] = mapped_column(String(24), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress_completed: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=sql_text("CURRENT_TIMESTAMP(6)")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_token: Mapped[str | None] = mapped_column(CHAR(36, charset="ascii"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    retry_of_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    target_config: Mapped[dict] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class IndexVersion(Base):
    __tablename__ = "document_index_versions"
    __table_args__ = (
        UniqueConstraint("id", "document_id", "kb_id", name="uq_version_scope"),
        UniqueConstraint("job_id", "attempt_no", name="uq_version_attempt"),
        ForeignKeyConstraint(
            ["document_id", "kb_id"],
            ["documents.id", "documents.kb_id"],
            name="fk_version_document",
        ),
        ForeignKeyConstraint(
            ["job_id", "document_id", "kb_id"],
            ["jobs.id", "jobs.document_id", "jobs.kb_id"],
            name="fk_version_job",
        ),
        CheckConstraint(
            "status IN ('staging','published','retired','failed')", name="ck_version_status"
        ),
        Index("ix_version_document_status", "document_id", "status"),
        Index("ix_version_kb_status", "kb_id", "status"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    kb_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    document_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    job_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16, collation="utf8mb4_bin"), default="staging")
    embedding_profile_id: Mapped[str | None] = mapped_column(String(64))
    collection_name: Mapped[str | None] = mapped_column(String(100))
    config_snapshot: Mapped[dict] = mapped_column(JSON)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    source_sha256: Mapped[str] = mapped_column(CHAR(64, charset="ascii"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=sql_text("CURRENT_TIMESTAMP(6)")
    )
    published_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("id", "document_id", "index_version_id", name="uq_chunk_citation_scope"),
        UniqueConstraint("index_version_id", "ordinal", name="uq_chunk_ordinal"),
        ForeignKeyConstraint(
            ["index_version_id", "document_id", "kb_id"],
            [
                "document_index_versions.id",
                "document_index_versions.document_id",
                "document_index_versions.kb_id",
            ],
            name="fk_chunk_version",
        ),
        CheckConstraint(
            "ordinal >= 0 AND char_start >= 0 AND char_end > char_start", name="ck_chunk_location"
        ),
        Index("ix_chunk_scope", "kb_id", "document_id", "index_version_id"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    kb_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    document_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    index_version_id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(MEDIUMTEXT)
    text_sha256: Mapped[str] = mapped_column(CHAR(64, charset="ascii"))
    page_no: Mapped[int | None] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(String(255))
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=sql_text("CURRENT_TIMESTAMP(6)")
    )


class Conversation(Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["active_message_id", "id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_conversation_active_message",
            use_alter=True,
        ),
        CheckConstraint("next_sequence_no > 0", name="ck_conversation_sequence"),
        Index("ix_conversation_user_created", "user_id", "created_at", "id"),
        Index("ix_conversation_user_updated", "user_id", "updated_at"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"))
    title: Mapped[str] = mapped_column(String(120))
    next_sequence_no: Mapped[int] = mapped_column(Integer, default=1)
    active_message_id: Mapped[str | None] = mapped_column(CHAR(36, charset="ascii"))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("id", "conversation_id", name="uq_message_scope"),
        UniqueConstraint("conversation_id", "sequence_no", name="uq_message_sequence"),
        UniqueConstraint("conversation_id", "client_request_id", name="uq_message_request"),
        ForeignKeyConstraint(
            ["reply_to_message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_message_reply",
        ),
        CheckConstraint("sequence_no > 0", name="ck_message_sequence"),
        CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
        CheckConstraint(
            "status IN ('completed','generating','failed','cancelled','interrupted')",
            name="ck_message_status",
        ),
        CheckConstraint(
            "(role='user' AND client_request_id IS NOT NULL AND status='completed' "
            "AND reply_to_message_id IS NULL) OR "
            "(role='assistant' AND client_request_id IS NULL AND reply_to_message_id IS NOT NULL)",
            name="ck_message_request_role",
        ),
        Index("ix_message_deadline", "status", "generation_deadline_at"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16, collation="utf8mb4_bin"))
    status: Mapped[str] = mapped_column(String(20, collation="utf8mb4_bin"))
    content: Mapped[str] = mapped_column(MEDIUMTEXT, default="")
    client_request_id: Mapped[str | None] = mapped_column(CHAR(36, charset="ascii"))
    reply_to_message_id: Mapped[str | None] = mapped_column(CHAR(36, charset="ascii"))
    retrieval_query: Mapped[str | None] = mapped_column(Text)
    retrieval_config: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    model_config: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    usage: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    timings: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    answer_type: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    generation_deadline_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=sql_text("CURRENT_TIMESTAMP(6)")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))


class Citation(Base):
    __tablename__ = "message_citations"
    __table_args__ = (
        UniqueConstraint("message_id", "source_label", name="uq_citation_label"),
        ForeignKeyConstraint(
            ["chunk_id", "document_id", "index_version_id"],
            ["chunks.id", "chunks.document_id", "chunks.index_version_id"],
            name="fk_citation_chunk_scope",
        ),
        Index("ix_citation_chunk", "chunk_id"),
    )
    id: Mapped[str] = mapped_column(CHAR(36, charset="ascii"), primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    source_label: Mapped[str] = mapped_column(String(16))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    index_version_id: Mapped[str] = mapped_column(ForeignKey("document_index_versions.id"))
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"))
    locator_snapshot: Mapped[dict] = mapped_column(JSON)
    retrieval_score: Mapped[float | None] = mapped_column(Double)
    rerank_score: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), server_default=sql_text("CURRENT_TIMESTAMP(6)")
    )
