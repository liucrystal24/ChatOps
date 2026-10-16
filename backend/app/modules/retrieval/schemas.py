from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.modules.documents.schemas import Tags
from app.modules.identity.schemas import Schema


class MetadataFilter(Schema):
    document_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=50)
    file_types: list[Literal["txt", "md", "pdf"]] | None = Field(
        default=None, min_length=1, max_length=3
    )
    tags_any: Tags | None = Field(default=None, min_length=1)


class RetrievalInput(Schema):
    question: str = Field(min_length=1, max_length=4000)
    strategy: Literal["dense"] = "dense"
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    score_threshold: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    metadata_filter: MetadataFilter = Field(default_factory=MetadataFilter)

    @field_validator("question")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()
