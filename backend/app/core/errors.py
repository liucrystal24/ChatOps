import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException


class AppError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        headers: dict | None = None,
        *,
        details: dict | None = None,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.details = details or {}
        super().__init__(code)


class ErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    request_id: str
    details: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorInfo


def error_response(request: Request, status: int, code: str, message: str, details=None):
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(
            error=ErrorInfo(
                code=code,
                message=message,
                request_id=request.state.request_id,
                details=details or {},
            )
        ).model_dump(),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        response = error_response(request, exc.status, exc.code, exc.message, exc.details)
        response.headers.update(exc.headers)
        return response

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request: Request, exc: SQLAlchemyError):
        return error_response(request, 503, "STORAGE_UNAVAILABLE", "数据服务暂不可用")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {
            404: ("RESOURCE_NOT_FOUND", "资源不存在或不可访问"),
            405: ("METHOD_NOT_ALLOWED", "请求方法不支持"),
            413: ("DOCUMENT_TOO_LARGE", "上传请求超过大小限制"),
        }
        code, message = codes.get(exc.status_code, ("HTTP_ERROR", "请求无法处理"))
        response = error_response(request, exc.status_code, code, message)
        if exc.status_code == 405 and exc.headers and "Allow" in exc.headers:
            response.headers["Allow"] = exc.headers["Allow"]
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = []
        for item in exc.errors():
            # 非法 JSON 或动态字段名也可能带用户原文，不直接回传 loc 中的任意字符串。
            location = [
                part
                if isinstance(part, int) or re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", str(part))
                else "field"
                for part in item["loc"]
            ]
            fields.append({"location": location, "type": item["type"]})
        return error_response(request, 422, "VALIDATION_ERROR", "请求参数无效", {"fields": fields})
