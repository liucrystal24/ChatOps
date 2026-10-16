import json
import logging
import re
from time import monotonic
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import Headers, MutableHeaders

from app.core.errors import error_response

logger = logging.getLogger("chat_rag.requests")


def configure_logging() -> None:
    parent = logging.getLogger("chat_rag")
    if not parent.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    parent.propagate = False


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = Headers(scope=scope).get("x-request-id", "")
        request_id = supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = monotonic()
        status = 500
        response_started = False
        error_type = None

        async def send_with_context(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
                if scope["path"].startswith("/api/v1/"):
                    MutableHeaders(scope=message)["Cache-Control"] = (
                        "no-store, no-transform"
                        if "text/event-stream"
                        in MutableHeaders(scope=message).get("content-type", "")
                        else "no-store"
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception as error:
            # 原始异常可能含 SQL、连接串或正文；日志只记异常类型和路由模板。
            error_type = type(error).__name__
            if response_started:
                raise
            response = error_response(
                Request(scope), 500, "INTERNAL_ERROR", "服务内部错误，请使用 request_id 联系维护者"
            )
            await response(scope, receive, send_with_context)
        finally:
            route = scope.get("route")
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": getattr(route, "path", "unmatched"),
                        "status": status,
                        "duration_ms": round((monotonic() - started) * 1000, 2),
                        "error_type": error_type,
                    },
                    ensure_ascii=False,
                )
            )
