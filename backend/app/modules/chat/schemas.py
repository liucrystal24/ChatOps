from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.pagination import Pagination
from app.modules.identity.schemas import Schema, UtcDateTime
from app.modules.retrieval.schemas import MetadataFilter


class ConversationCreate(Schema):
    kb_id: UUID
    title: str = Field(default="新对话", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def title_valid(cls, value):
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()


class ConversationPage(Pagination):
    kb_id: UUID | None = None


class ConversationResponse(Schema):
    id: UUID
    kb_id: UUID
    title: str
    active_message_id: UUID | None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ConversationList(Schema):
    items: list[ConversationResponse]
    next_cursor: str | None


class RetrievalOptions(Schema):
    strategy: Literal["dense"] = "dense"
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    score_threshold: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    metadata_filter: MetadataFilter = Field(default_factory=MetadataFilter)


class ChatInput(Schema):
    client_request_id: UUID
    question: str = Field(min_length=1, max_length=4000)
    retrieval: RetrievalOptions = Field(default_factory=RetrievalOptions)

    @field_validator("question")
    @classmethod
    def question_valid(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class Source(Schema):
    source_label: str
    chunk_id: UUID
    document_id: UUID
    index_version_id: UUID
    filename: str
    page_no: int | None
    section_title: str | None
    char_start: int
    char_end: int
    excerpt: str
    retrieval_score: float
    rerank_score: float | None = None


class CitationResponse(Schema):
    source_label: str
    chunk_id: UUID
    document_id: UUID
    index_version_id: UUID
    available: bool
    filename: str | None
    page_no: int | None
    section_title: str | None


class MessageResponse(Schema):
    id: UUID
    conversation_id: UUID
    sequence_no: int
    role: Literal["user", "assistant"]
    status: Literal["completed", "generating", "failed", "cancelled", "interrupted"]
    content: str
    client_request_id: UUID | None
    reply_to_message_id: UUID | None
    answer_type: Literal["answer", "abstention"] | None
    error_code: str | None
    citations: list[CitationResponse]
    usage: dict | None
    timings: dict | None
    created_at: UtcDateTime
    completed_at: UtcDateTime | None


class MessageList(Schema):
    items: list[MessageResponse]
    next_cursor: str | None
