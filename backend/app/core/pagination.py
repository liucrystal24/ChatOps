import base64
import hashlib
import hmac
import json
from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.errors import AppError
from app.modules.identity.schemas import Schema


class Pagination(Schema):
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)


class Cursors:
    def __init__(self, key: str):
        self.key = key.encode()

    def encode(self, scope: str, created_at: datetime, entity_id: str) -> str:
        payload = json.dumps(
            [scope, created_at.isoformat(timespec="microseconds"), entity_id], separators=(",", ":")
        ).encode()
        signature = hmac.digest(self.key, b"cursor-v1:" + payload, hashlib.sha256)
        return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")

    def decode(self, cursor: str | None, scope: str):
        if cursor is None:
            return None
        try:
            if len(cursor) > 1024:
                raise ValueError
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            expected = hmac.digest(self.key, b"cursor-v1:" + payload, hashlib.sha256)
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            actual_scope, created_at, entity_id = json.loads(payload)
            position = datetime.fromisoformat(created_at)
            if actual_scope != scope or position.tzinfo is not None:
                raise ValueError
            return position, str(UUID(entity_id))
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise AppError(422, "INVALID_CURSOR", "分页游标无效，请重新加载列表") from None
