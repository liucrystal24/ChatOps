from enum import StrEnum
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.modules.identity.schemas import Schema, UtcDateTime


class Role(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class KnowledgeBaseCreate(Schema):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("名称不能为空")
        return value


class KnowledgeBasePatch(KnowledgeBaseCreate):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("至少提供一个非 null 的修改字段")
        return self


class KnowledgeBaseResponse(Schema):
    id: UUID
    name: str
    description: str
    my_role: Role
    embedding_profile_id: str | None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class MemberInput(Schema):
    role: Role


class MemberResponse(Schema):
    user_id: UUID
    username: str
    role: Role
    is_active: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class KnowledgeBaseList(Schema):
    items: list[KnowledgeBaseResponse]
    next_cursor: str | None


class MemberList(Schema):
    items: list[MemberResponse]
    next_cursor: str | None
