from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import auth_invalid
from app.modules.identity.schemas import UserResponse
from app.modules.identity.service import IdentityService
from app.modules.knowledge_bases.service import KnowledgeBaseService

bearer = HTTPBearer(auto_error=False)


def identity_service(request: Request) -> IdentityService:
    return request.app.state.identity_service


def knowledge_base_service(request: Request) -> KnowledgeBaseService:
    return request.app.state.knowledge_base_service


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    service: Annotated[IdentityService, Depends(identity_service)],
) -> UserResponse:
    if not credentials:
        raise auth_invalid()
    return await service.me(service.security.subject(credentials.credentials))


CurrentUser = Annotated[UserResponse, Depends(current_user)]
Identity = Annotated[IdentityService, Depends(identity_service)]
KnowledgeBases = Annotated[KnowledgeBaseService, Depends(knowledge_base_service)]
