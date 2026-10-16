from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.core.errors import ErrorResponse
from app.modules.health.service import HealthService, LiveResponse, ReadyResponse

router = APIRouter(tags=["health"])


def health_service(request: Request) -> HealthService:
    return request.app.state.health_service


@router.get("/health/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse()


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}, 500: {"model": ErrorResponse}},
)
async def ready(
    response: Response, service: Annotated[HealthService, Depends(health_service)]
) -> ReadyResponse:
    report = await service.readiness()
    response.status_code = 200 if report.status == "ok" else 503
    response.headers["Cache-Control"] = "no-store"
    return report
