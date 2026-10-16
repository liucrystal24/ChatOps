import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


def normalize_username(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value):
        raise ValueError("用户名需为 1–64 位字母、数字、下划线、点或连字符，首位为字母或数字")
    return value


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(utc)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LoginInput(Schema):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256, repr=False)

    _username = field_validator("username")(normalize_username)


class TokenResponse(Schema):
    access_token: str = Field(repr=False)
    token_type: str = "bearer"
    expires_in: int


class UserResponse(Schema):
    id: UUID
    username: str
    is_admin: bool
