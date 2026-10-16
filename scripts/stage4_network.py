"""真实 CLI、Nginx 上传、Worker 和共享卷验收；只清理本次 *_test 资源。"""

import json
import secrets
import time
import urllib.error
import urllib.request
from uuid import uuid4


def verify(run, base, secret_values):
    password = secrets.token_urlsafe(24)
    secret_values.append(password)
    users = []
    kb = None
    worker_stopped = False

    def http(method, path, token, body=None, expected=200, multipart=False, raw=False):
        headers = {"Authorization": "Bearer " + token, "X-Request-ID": "stage4-network"}
        if body is not None:
            headers["Content-Type"] = (
                "multipart/form-data; boundary=stage4boundary" if multipart else "application/json"
            )
        data = body if multipart else json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            base + "/api/v1" + path, method=method, headers=headers, data=data
        )
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, (
                f"网络接口失败: {method} {path} -> {response.status}"
            )
            assert response.headers["X-Request-ID"] == "stage4-network"
            assert response.headers["Cache-Control"] == "no-store"
            data = response.read()
            return data if raw else json.loads(data) if data else None

    def wait(path, token, predicate):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            value = http("GET", path, token)
            if predicate(value):
                return value
            time.sleep(0.5)
        raise AssertionError("后台任务未在 90 秒内达到预期")

    def retrieve(actor, kb_id, expected_ids):
        code = f"""
import asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import load_settings
from app.infrastructure.database import create_engine
from app.infrastructure.repositories import Database
from app.infrastructure.embedding import Embedding
from app.infrastructure.vectors import Vectors
from app.modules.retrieval.schemas import RetrievalInput
from app.modules.retrieval.service import RetrievalService
async def main():
    config=load_settings();engine=create_engine(config)
    embedding,vectors=Embedding(config),Vectors(config)
    try:
        service=RetrievalService(Database(async_sessionmaker(engine,expire_on_commit=False)),embedding,vectors,config)
        hits=await service.search({actor!r},{kb_id!r},RetrievalInput(question='观测站什么时候进行维护？'))
        assert {{hit['document_id'] for hit in hits}}==set({expected_ids!r})
        print('authorized_service_retrieval_ok')
    finally:
        await embedding.close();await vectors.close();await engine.dispose()
asyncio.run(main())
"""
        assert "authorized_service_retrieval_ok" in run(
            ["exec", "-T", "api", "python", "-"], input=code
        )

    try:
        from http.client import HTTPConnection
        from urllib.parse import urlsplit

        address = urlsplit(base)
        connection = HTTPConnection(address.hostname, address.port, timeout=10)
        connection.request(
            "POST",
            "/api/v1/knowledge-bases/oversized/documents",
            headers={"Content-Length": str(12 * 1024**2)},
        )
        oversized = connection.getresponse()
        assert oversized.status == 413
        assert oversized.getheader("X-Request-ID")
        assert json.loads(oversized.read())["error"]["code"] == "DOCUMENT_TOO_LARGE"
        connection.close()
        for _ in range(2):
            user = json.loads(
                run(
                    [
                        "exec",
                        "-T",
                        "api",
                        "python",
                        "-m",
                        "app.cli",
                        "users",
                        "create",
                        "test_stage4_network_" + uuid4().hex,
                        "--password-stdin",
                    ],
                    input=password + "\n",
                )
            )
            users.append(user)
        tokens = []
        for user in users:
            result = http(
                "POST",
                "/auth/login",
                "",
                {"username": user["username"], "password": password},
            )
            tokens.append(result["access_token"])
            secret_values.append(result["access_token"])
        alice, bob = tokens
        kb = http("POST", "/knowledge-bases", alice, {"name": "阶段四真实网络验收"}, 201)["id"]
        path = f"/knowledge-bases/{kb}"
        source = "观测站每周三上午九点进行设备维护，维护期间暂停望远镜预约。".encode()
        multipart = (
            b'--stage4boundary\r\nContent-Disposition: form-data; name="file"; filename="observatory.txt"\r\nContent-Type: text/plain\r\n\r\n'
            + source
            + b'\r\n--stage4boundary\r\nContent-Disposition: form-data; name="tags"\r\n\r\n["network"]\r\n--stage4boundary--\r\n'
        )
        uploaded = http("POST", path + "/documents", alice, multipart, 202, multipart=True)
        doc = "/documents/" + uploaded["document"]["id"]
        wait(doc, alice, lambda value: value["availability"] == "searchable")
        http("DELETE", path, alice, expected=409)
        assert http("GET", doc + "/file", alice, raw=True) == source
        chunks = http("GET", doc + "/chunks", alice)["items"]
        assert chunks and chunks[0]["text"].encode() == source
        chunk_path = "/chunks/" + chunks[0]["id"]
        for endpoint in [
            path + "/documents",
            doc,
            doc + "/file",
            chunk_path,
            "/jobs/" + uploaded["job"]["id"],
        ]:
            http("GET", endpoint, bob, expected=404)
        http("PUT", path + "/members/" + users[1]["id"], alice, {"role": "viewer"})
        assert http("GET", chunk_path, bob)["text"].encode() == source
        http("POST", path + "/documents", bob, multipart, 403, multipart=True)
        http("DELETE", path + "/members/" + users[1]["id"], alice, expected=204)
        http("GET", chunk_path, bob, expected=404)
        retrieve(users[0]["id"], kb, [uploaded["document"]["id"]])
        # 重建 API/Worker 后文件及有效版本仍存在，验证共享命名卷而非容器临时层。
        run(["up", "-d", "--no-deps", "--force-recreate", "--wait", "api", "worker"])
        assert http("GET", doc + "/file", alice, raw=True) == source
        run(["stop", "worker"])
        worker_stopped = True
        deleted = http("DELETE", doc, alice, expected=202)
        http("GET", doc + "/file", alice, expected=404)
        retrieve(users[0]["id"], kb, [])
        run(["up", "-d", "worker"])
        worker_stopped = False
        wait(
            "/jobs/" + deleted["cleanup_job_id"],
            alice,
            lambda value: value["status"] == "succeeded",
        )
        http("DELETE", path, alice, expected=204)
    finally:
        if worker_stopped:
            run(["up", "-d", "worker"])
        if users:
            ids = [u["id"] for u in users]
            cleanup = f"""
import asyncio
from sqlalchemy import delete,select,update
from sqlalchemy.engine import make_url
from app.core.config import load_settings
from app.infrastructure.database import create_engine
from app.infrastructure.models import AuthorizationGuard,Chunk,Document,IndexVersion,Job,KnowledgeBase,Membership,User
from app.infrastructure.vectors import Vectors
from app.infrastructure.files import Files
async def main():
    config=load_settings();assert make_url(config.database_url.get_secret_value()).database.endswith('_test')
    engine=create_engine(config);vectors=Vectors(config);files=Files(config)
    try:
        async with engine.connect() as conn:
            kbs=(await conn.scalars(select(KnowledgeBase.id).where(KnowledgeBase.created_by.in_({ids!r})))).all()
            versions=(await conn.execute(select(IndexVersion.collection_name,IndexVersion.id).where(IndexVersion.kb_id.in_(kbs)))).all()
            keys=(await conn.scalars(select(Document.storage_key).where(Document.kb_id.in_(kbs)))).all()
        for collection,version in versions:
            if collection: await vectors.delete_version(collection,version)
        for key in keys: await files.remove(key)
        async with engine.begin() as conn:
            await conn.execute(select(AuthorizationGuard.id).with_for_update())
            await conn.execute(update(Document).where(Document.kb_id.in_(kbs)).values(active_index_version_id=None,current_job_id=None))
            for model in [Chunk,IndexVersion]: await conn.execute(delete(model).where(model.kb_id.in_(kbs)))
            await conn.execute(update(Job).where(Job.kb_id.in_(kbs)).values(retry_of_job_id=None))
            for model in [Job,Document,Membership]: await conn.execute(delete(model).where(model.kb_id.in_(kbs)))
            await conn.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(kbs)))
            await conn.execute(delete(User).where(User.id.in_({ids!r})))
    finally:
        await vectors.close();await engine.dispose()
asyncio.run(main())
"""
            run(["exec", "-T", "api", "python", "-"], input=cleanup)
    print(
        "真实上传/Worker/来源/撤权/删除残留过滤/API与Worker共享卷重建检查通过",
        flush=True,
    )
