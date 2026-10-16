from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.api.dependencies import CurrentUser
from app.core.errors import AppError
from app.core.pagination import Pagination
from app.modules.documents.schemas import (
    ChunkList,
    ChunkResponse,
    DeleteAccepted,
    DocumentList,
    DocumentPage,
    DocumentPatch,
    DocumentResponse,
    JobAccepted,
    JobList,
    JobPage,
    JobResponse,
    Metadata,
    UploadResponse,
)
from app.modules.documents.service import DocumentService

router = APIRouter(prefix="/api/v1", tags=["documents"])


def service(request: Request):
    return request.app.state.document_service


Documents = Annotated[DocumentService, Depends(service)]


@router.post("/knowledge-bases/{kb_id}/documents", status_code=202, response_model=UploadResponse)
async def upload(
    kb_id: UUID,
    actor: CurrentUser,
    documents: Documents,
    response: Response,
    request: Request,
    file: Annotated[UploadFile, File()],
    tags: Annotated[str, Form()] = "[]",
):
    form = await request.form()
    fields = list(form.multi_items())
    if (
        any(key not in {"file", "tags"} for key, _ in fields)
        or sum(key == "file" for key, _ in fields) != 1
        or sum(key == "tags" for key, _ in fields) > 1
    ):
        raise AppError(422, "VALIDATION_ERROR", "只允许单个 file 和可选 tags")
    try:
        metadata = Metadata.model_validate_json('{"tags":' + tags + "}")
    except ValidationError:
        raise AppError(422, "VALIDATION_ERROR", "tags 必须是最多 10 项的 JSON 字符串数组") from None
    result = await documents.upload(str(actor.id), str(kb_id), file, metadata)
    response.headers["Location"] = f"/api/v1/jobs/{result.job.id}"
    return result


@router.get("/knowledge-bases/{kb_id}/documents", response_model=DocumentList)
async def list_documents(
    kb_id: UUID, actor: CurrentUser, documents: Documents, page: Annotated[DocumentPage, Query()]
):
    return await documents.list(str(actor.id), str(kb_id), page)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID, actor: CurrentUser, documents: Documents):
    return await documents.get(str(actor.id), str(document_id))


@router.patch("/documents/{document_id}", response_model=DocumentResponse | UploadResponse)
async def patch_document(
    document_id: UUID,
    data: DocumentPatch,
    actor: CurrentUser,
    documents: Documents,
    response: Response,
):
    result = await documents.patch(str(actor.id), str(document_id), data)
    if isinstance(result, UploadResponse):
        response.status_code = 202
        response.headers["Location"] = f"/api/v1/jobs/{result.job.id}"
    return result


@router.get("/documents/{document_id}/file", response_class=FileResponse)
async def download(document_id: UUID, actor: CurrentUser, documents: Documents):
    path, name, media = await documents.file(str(actor.id), str(document_id))
    return FileResponse(
        path,
        media_type=media,
        filename=name,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.delete("/documents/{document_id}", status_code=202, response_model=DeleteAccepted)
async def delete_document(document_id: UUID, actor: CurrentUser, documents: Documents):
    return await documents.delete(str(actor.id), str(document_id))


@router.post("/documents/{document_id}/reindex", status_code=202, response_model=JobAccepted)
async def reindex(document_id: UUID, actor: CurrentUser, documents: Documents):
    return await documents.reindex(str(actor.id), str(document_id))


@router.get("/knowledge-bases/{kb_id}/jobs", response_model=JobList)
async def list_jobs(
    kb_id: UUID, actor: CurrentUser, documents: Documents, page: Annotated[JobPage, Query()]
):
    return await documents.jobs(str(actor.id), str(kb_id), page)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, actor: CurrentUser, documents: Documents):
    return await documents.get_job(str(actor.id), str(job_id))


@router.post("/jobs/{job_id}/retry", status_code=202, response_model=JobAccepted)
async def retry_job(job_id: UUID, actor: CurrentUser, documents: Documents):
    return await documents.retry(str(actor.id), str(job_id))


@router.get("/chunks/{chunk_id}", response_model=ChunkResponse)
async def get_chunk(chunk_id: UUID, actor: CurrentUser, documents: Documents):
    return await documents.chunk(str(actor.id), str(chunk_id))


@router.get("/documents/{document_id}/chunks", response_model=ChunkList)
async def list_chunks(
    document_id: UUID,
    actor: CurrentUser,
    documents: Documents,
    page: Annotated[Pagination, Query()],
):
    return await documents.chunks(str(actor.id), str(document_id), page)
