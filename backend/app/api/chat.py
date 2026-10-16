from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.dependencies import CurrentUser, bearer
from app.core.pagination import Pagination
from app.modules.chat.schemas import (
    ChatInput,
    ConversationCreate,
    ConversationList,
    ConversationPage,
    ConversationResponse,
    MessageList,
    MessageResponse,
)
from app.modules.chat.service import ChatService, Generation

router = APIRouter(prefix="/api/v1", tags=["chat"])


def chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


Chat = Annotated[ChatService, Depends(chat_service)]
Page = Annotated[Pagination, Query()]


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
async def create(data: ConversationCreate, user: CurrentUser, service: Chat):
    return await service.create(str(user.id), data)


@router.get("/conversations", response_model=ConversationList)
async def conversations(
    page: Annotated[ConversationPage, Query()], user: CurrentUser, service: Chat
):
    return await service.list(str(user.id), page)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def conversation(conversation_id: UUID, user: CurrentUser, service: Chat):
    return await service.get(str(user.id), str(conversation_id))


@router.get("/conversations/{conversation_id}/messages", response_model=MessageList)
async def messages(conversation_id: UUID, page: Page, user: CurrentUser, service: Chat):
    return await service.messages(str(user.id), str(conversation_id), page)


@router.get("/messages/{message_id}", response_model=MessageResponse)
async def message(message_id: UUID, user: CurrentUser, service: Chat):
    return await service.message(str(user.id), str(message_id))


async def prepare(
    conversation_id: UUID,
    data: ChatInput,
    user: CurrentUser,
    service: Chat,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
):
    security = request.app.state.identity_service.security
    return await service.prepare(
        str(user.id),
        str(conversation_id),
        data,
        request.state.request_id,
        authenticate=lambda: security.subject(credentials.credentials),
        disconnected=request.is_disconnected,
    )


@router.post("/conversations/{conversation_id}/messages:stream", response_class=EventSourceResponse)
async def stream(
    run: Annotated[Generation, Depends(prepare)], service: Chat
) -> AsyncIterator[ServerSentEvent]:
    async with aclosing(service.stream(run)) as events:
        async for event in events:
            yield event
