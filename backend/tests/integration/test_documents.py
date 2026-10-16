from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.core.errors import AppError
from app.core.pagination import Cursors
from app.infrastructure.files import Files
from app.modules.documents.schemas import DocumentPatch, Metadata
from app.modules.documents.service import DocumentService
from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate, Role

pytestmark = pytest.mark.integration


async def test_upload_isolation_limits_and_logical_delete(bundle):
    b = bundle
    kb = await b.knowledge.create(str(b.users["alice"].id), KnowledgeBaseCreate(name="文档测试"))
    kb_id = str(kb.id)
    root = "/api/v1"
    path = f"{root}/knowledge-bases/{kb_id}/documents"
    client = b.client
    alice, bob = b.headers["alice"], b.headers["bob"]
    assert (
        client.post(path, headers=bob, files={"file": ("note.txt", b"secret")}).status_code == 404
    )
    await b.knowledge.put_member(
        str(b.users["alice"].id), kb_id, str(b.users["bob"].id), Role.VIEWER
    )
    assert (
        client.post(path, headers=bob, files={"file": ("note.txt", b"secret")}).status_code == 403
    )
    upload = client.post(
        path,
        headers=alice,
        files={"file": ("笔记.md", "# 真实文档\n这是中文内容。".encode())},
        data={"tags": '["架构","架构"]'},
    )
    assert upload.status_code == 202, upload.text
    result = upload.json()
    document, job = result["document"], result["job"]
    assert upload.headers["location"].endswith(job["id"])
    assert document["availability"] == "unavailable" and job["status"] == "queued"
    assert document["tags"] == ["架构"] and "storage_key" not in document
    assert "lease_token" not in job
    doc_path = f"{root}/documents/{document['id']}"
    assert (
        client.get(doc_path + "/file", headers=bob).content == "# 真实文档\n这是中文内容。".encode()
    )
    assert client.get(doc_path, headers=b.headers["admin"]).status_code == 404
    assert client.get(f"{root}/jobs/{job['id']}", headers=b.headers["admin"]).status_code == 404
    assert client.get(path, headers=bob).json()["items"][0]["id"] == document["id"]
    assert client.delete(f"{root}/knowledge-bases/{kb_id}", headers=alice).status_code == 409
    assert client.post(doc_path + "/reindex", headers=alice).status_code == 409
    assert client.patch(doc_path, headers=bob, json={"filename": "new.txt"}).status_code == 403
    assert client.delete(doc_path, headers=bob).status_code == 403
    deleted = client.delete(doc_path, headers=alice)
    assert deleted.status_code == 202
    for endpoint in [doc_path, doc_path + "/file"]:
        assert client.get(endpoint, headers=bob).status_code == 404
    assert (
        client.get(f"{root}/jobs/{deleted.json()['cleanup_job_id']}", headers=bob).status_code
        == 200
    )
    assert client.get(path, headers=alice).json()["items"] == []
    assert client.delete(f"{root}/knowledge-bases/{kb_id}", headers=alice).status_code == 409
    assert (
        client.get(
            f"{root}/knowledge-bases/{kb_id}/jobs?document_id={uuid4()}", headers=alice
        ).status_code
        == 404
    )


async def test_upload_format_validation_and_rollback_removes_file(bundle):
    b = bundle
    actor = str(b.users["alice"].id)
    kb = await b.knowledge.create(actor, KnowledgeBaseCreate(name="配额"))
    path = f"/api/v1/knowledge-bases/{kb.id}/documents"
    for name, content, expected in [
        ("a.exe", b"hello", 415),
        ("../a.txt", b"hello", 422),
        ("a.pdf", b"fake", 415),
        ("a.txt", b"%PDF-1.7", 415),
        ("a.txt", b"\xff\x00", 415),
        ("a.txt", b"", 422),
    ]:
        assert (
            b.client.post(
                path, headers=b.headers["alice"], files={"file": (name, content)}
            ).status_code
            == expected
        )
    assert (
        b.client.post(
            path,
            headers=b.headers["alice"],
            files={"file": ("a.txt", b"ok")},
            data={"tags": "[null]"},
        ).status_code
        == 422
    )
    settings = b.settings.model_copy(update={"upload_max_bytes": 8, "kb_document_limit": 1})
    files = Files(settings)
    service = DocumentService(
        b.database, files, Cursors(settings.auth_secret.get_secret_value()), settings
    )

    def upload(text):
        return UploadFile(BytesIO(text), filename="ok.txt")

    with pytest.raises(AppError, match="DOCUMENT_TOO_LARGE"):
        await service.upload(actor, str(kb.id), upload(b"longer than eight"), Metadata())
    await service.upload(actor, str(kb.id), upload(b"first"), Metadata())
    before = set(files.root.iterdir())
    with pytest.raises(AppError, match="KB_DOCUMENT_LIMIT"):
        await service.upload(actor, str(kb.id), upload(b"second"), Metadata())
    assert set(files.root.iterdir()) == before
    with pytest.raises(ValueError):
        DocumentPatch.model_validate({"tags": None})


async def test_failed_purge_cannot_orphan_files_by_deleting_parent(bundle):
    b = bundle
    actor = str(b.users["alice"].id)
    kb = await b.knowledge.create(actor, KnowledgeBaseCreate(name="清理失败保护"))
    settings = b.settings
    service = DocumentService(
        b.database, Files(settings), Cursors(settings.auth_secret.get_secret_value()), settings
    )
    result = await service.upload(
        actor, str(kb.id), UploadFile(BytesIO(b"cleanup"), filename="cleanup.txt"), Metadata()
    )
    deleted = await service.delete(actor, str(result.document.id))
    async with b.database.transaction() as repo:
        await repo.lock_authorization()
        job = await repo.job(str(deleted.cleanup_job_id), lock=True)
        job.status = "failed"
        await repo.save(job)
    with pytest.raises(AppError, match="KB_NOT_EMPTY"):
        await b.knowledge.delete(actor, str(kb.id))
    assert Files(settings).path(await _storage_key(b, str(result.document.id))).is_file()
    assert (await service.retry(actor, str(deleted.cleanup_job_id))).job.status == "queued"


async def _storage_key(bundle, document_id):
    async with bundle.database.transaction() as repo:
        return (await repo.document(document_id)).storage_key
