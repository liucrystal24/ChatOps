from fastapi import HTTPException
from starlette.datastructures import Headers

from app.core.errors import error_response


class UploadLimitMiddleware:
    """在 multipart 解析前校验总长度，分块传输也受同一上限约束。"""

    def __init__(self, app, limit=11 * 1024**2):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].endswith("/documents")
        ):
            return await self.app(scope, receive, send)
        from starlette.requests import Request

        request = Request(scope)
        content_length = Headers(scope=scope).get("content-length", "0")
        try:
            oversized = int(content_length) > self.limit
        except ValueError:
            oversized = True
        if oversized:
            return await error_response(
                request, 413, "DOCUMENT_TOO_LARGE", "上传请求不能超过 11 MiB"
            )(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.limit:
                # Starlette 用 HTTPException 处理解析异常，统一错误处理器保留安全 413。
                raise HTTPException(413, "上传请求超过上限")
            return message

        return await self.app(scope, bounded_receive, send)
