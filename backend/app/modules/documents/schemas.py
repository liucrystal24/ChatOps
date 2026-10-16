import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.core.pagination import Pagination
from app.modules.identity.schemas import Schema, UtcDateTime

Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Tags = Annotated[list[Tag], Field(max_length=10)]
JobStatus = Literal["queued", "running", "retry_wait", "succeeded", "failed", "cancelled"]


def filename(value: str) -> str:
    value = value.strip()
    if (
        not value
        or len(value) > 255
        or value in {".", ".."}
        or re.search(r"[\\/\x00-\x1f\x7f]", value)
    ):
        raise ValueError("文件名无效")
    return value


class Metadata(Schema):
    tags: Tags = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def distinct_tags(cls, value):
        return list(dict.fromkeys(value)) if value is not None else value


class DocumentPatch(Metadata):
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    tags: Tags | None = None

    @field_validator("filename")
    @classmethod
    def valid_filename(cls, value):
        return filename(value) if value is not None else value

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("至少提供一个非空字段")
        return self


class JobError(Schema):
    code: str
    message: str


class JobResponse(Schema):
    id: UUID
    document_id: UUID
    kind: Literal["index", "purge"]
    status: JobStatus
    stage: str
    attempt_count: int
    max_attempts: int
    progress_completed: int
    progress_total: int | None
    available_at: UtcDateTime
    error: JobError | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None


class DocumentResponse(Schema):
    id: UUID
    kb_id: UUID
    filename: str
    file_type: Literal["txt", "md", "pdf"]
    media_type: str
    size_bytes: int
    tags: list[str]
    availability: Literal["searchable", "unavailable"]
    active_index_version_id: UUID | None
    latest_job: JobResponse | None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class UploadResponse(Schema):
    document: DocumentResponse
    job: JobResponse


class JobAccepted(Schema):
    job: JobResponse


class DeleteAccepted(Schema):
    document_id: UUID
    deleted: Literal[True] = True
    cleanup_job_id: UUID


class DocumentPage(Pagination):
    availability: Literal["searchable", "unavailable"] | None = None


class JobPage(Pagination):
    document_id: UUID | None = None
    status: JobStatus | None = None


class DocumentList(Schema):
    items: list[DocumentResponse]
    next_cursor: str | None


class JobList(Schema):
    items: list[JobResponse]
    next_cursor: str | None


class ChunkResponse(Schema):
    id: UUID
    document_id: UUID
    index_version_id: UUID
    filename: str
    text: str
    page_no: int | None
    section_title: str | None
    char_start: int
    char_end: int


class ChunkList(Schema):
    items: list[ChunkResponse]
    next_cursor: str | None
