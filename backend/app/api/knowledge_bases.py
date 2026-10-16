from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import CurrentUser, KnowledgeBases
from app.core.pagination import Pagination
from app.modules.knowledge_bases.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseList,
    KnowledgeBasePatch,
    KnowledgeBaseResponse,
    MemberInput,
    MemberList,
    MemberResponse,
)

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge_bases"])
Page = Annotated[Pagination, Query()]


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create(data: KnowledgeBaseCreate, user: CurrentUser, service: KnowledgeBases):
    return await service.create(str(user.id), data)


@router.get("", response_model=KnowledgeBaseList)
async def list_knowledge_bases(page: Page, user: CurrentUser, service: KnowledgeBases):
    return await service.list(str(user.id), page)


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get(kb_id: UUID, user: CurrentUser, service: KnowledgeBases):
    return await service.get(str(user.id), str(kb_id))


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update(kb_id: UUID, data: KnowledgeBasePatch, user: CurrentUser, service: KnowledgeBases):
    return await service.update(str(user.id), str(kb_id), data)


@router.delete("/{kb_id}", status_code=204)
async def delete(kb_id: UUID, user: CurrentUser, service: KnowledgeBases):
    await service.delete(str(user.id), str(kb_id))
    return Response(status_code=204)


@router.get("/{kb_id}/members", response_model=MemberList)
async def members(kb_id: UUID, page: Page, user: CurrentUser, service: KnowledgeBases):
    return await service.members(str(user.id), str(kb_id), page)


@router.put("/{kb_id}/members/{user_id}", response_model=MemberResponse)
async def put_member(
    kb_id: UUID, user_id: UUID, data: MemberInput, user: CurrentUser, service: KnowledgeBases
):
    return await service.put_member(str(user.id), str(kb_id), str(user_id), data.role)


@router.delete("/{kb_id}/members/{user_id}", status_code=204)
async def remove_member(kb_id: UUID, user_id: UUID, user: CurrentUser, service: KnowledgeBases):
    await service.remove_member(str(user.id), str(kb_id), str(user_id))
    return Response(status_code=204)
