from fastapi import APIRouter, Request

from app.api.dependencies import CurrentUser, Identity
from app.modules.identity.schemas import LoginInput, TokenResponse, UserResponse

router = APIRouter(prefix="/api/v1/auth", tags=["identity"])


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginInput, request: Request, service: Identity):
    return await service.login(
        data.username, data.password, request.client.host if request.client else "unknown"
    )


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser):
    return user
