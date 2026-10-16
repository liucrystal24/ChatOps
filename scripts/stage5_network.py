"""真实 Nginx POST SSE、断开取消、依赖失败和 API SIGKILL 恢复。"""

import json
import secrets
import time
from contextlib import contextmanager
from http.client import HTTPConnection
from urllib.parse import urljoin, urlsplit
from uuid import uuid4


def verify(run, base, secret_values):
    password = secrets.token_urlsafe(24)
    secret_values.append(password)
    username = "stage5_" + uuid4().hex
    created = json.loads(
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
                username,
                "--password-stdin",
            ],
            input=password + "\n",
        )
    )
    user_id = created["id"]
    kb_id = doc_id = token = None
    client = Http(base + "/api/v1/")
    try:
        response = client.post(
            "auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        secret_values.append(token)
        client.headers.update(
            {"Authorization": "Bearer " + token, "X-Request-ID": "stage5-network"}
        )
        response = client.post("knowledge-bases", json={"name": "阶段5真实流验收"})
        assert response.status_code == 201
        kb_id = response.json()["id"]
        response = client.post(
            f"knowledge-bases/{kb_id}/documents",
            files={
                "file": (
                    "培训说明.txt",
                    "松柏项目新人培训在每周二上午十点，地点是三楼青松会议室。资料未记载费用。".encode(),
                    "text/plain",
                )
            },
        )
        assert response.status_code == 202
        doc_id = response.json()["document"]["id"]
        wait(
            lambda: client.get(f"documents/{doc_id}").json(),
            lambda d: d["availability"] == "searchable",
            90,
        )
        conv = client.post(
            "conversations", json={"kb_id": kb_id, "title": "真实两轮问答"}
        ).json()["id"]

        def ask(question, request_id=None, stop_at=None):
            events = []
            request_id = request_id or str(uuid4())
            with client.stream(
                "POST",
                f"conversations/{conv}/messages:stream",
                json={"client_request_id": request_id, "question": question},
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["cache-control"] == "no-store, no-transform"
                name = None
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    if line.startswith("data:"):
                        events.append((name, json.loads(line[5:])))
                        if name == stop_at:
                            break
            return events

        first_id = str(uuid4())
        first = ask("松柏项目新人培训什么时候开始？", first_id)
        assert first[-1][0] == "done" and first[-1][1]["citations"]
        assert "周二" in "".join(e[1].get("text", "") for e in first)
        duplicate = client.post(
            f"conversations/{conv}/messages:stream",
            json={"client_request_id": first_id, "question": "重复请求"},
        )
        assert (
            duplicate.status_code == 409
            and duplicate.json()["error"]["code"] == "REQUEST_ALREADY_EXISTS"
        )
        second = ask("那地点在哪里？")
        assert second[-1][0] == "done" and "青松" in "".join(
            e[1].get("text", "") for e in second
        )
        missing = ask("培训费用每人多少元？")
        assert (
            missing[-1][0] == "done" and missing[-1][1]["answer_type"] == "abstention"
        )
        disconnected = ask(
            "请详细解释这份培训安排并逐条列出所有信息。", stop_at="delta"
        )
        message_id = disconnected[0][1]["message_id"]
        saved = wait(
            lambda: client.get(f"messages/{message_id}").json(),
            lambda m: m["status"] != "generating",
            12,
        )
        assert saved["status"] == "cancelled", saved["status"]
        print("真实 SSE 两轮、拒答、幂等与断开取消通过", flush=True)
        try:
            run(["stop", "qdrant"])
            failure = ask("新人培训时间？")
            assert failure[-1][0] == "error" and not any(
                e[0] == "done" for e in failure
            )
            assert client.get(base + "/api/health/ready").status_code == 503
        finally:
            run(["start", "qdrant"])
        wait(
            lambda: client.get(base + "/api/health/ready").status_code,
            lambda s: s == 200,
            30,
        )
        crash_request = str(uuid4())
        with client.stream(
            "POST",
            f"conversations/{conv}/messages:stream",
            json={
                "client_request_id": crash_request,
                "question": "详细说明全部培训安排及地点，附上引用。",
            },
        ) as response:
            crashed = None
            for line in response.iter_lines():
                if line.startswith("data:"):
                    value = json.loads(line[5:])
                    if value.get("message_id"):
                        crashed = value["message_id"]
                        break
            assert crashed
            run(["kill", "-s", "SIGKILL", "api"])
        run(["up", "-d", "--no-deps", "--wait", "api"])
        wait(
            lambda: client.get(base + "/api/health/ready").status_code,
            lambda s: s == 200,
            30,
        )
        # 缩短本次专用消息的测试时钟窗口；由真实 Worker 巡检写 interrupted。
        run(
            ["exec", "-T", "api", "python", "-"],
            input=f"""
import asyncio
from datetime import timedelta
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.engine import make_url
from app.core.config import load_settings
from app.infrastructure.database import create_engine
from app.infrastructure.repositories import Database
async def main():
 s=load_settings();assert make_url(s.database_url.get_secret_value()).database.endswith('_test')
 e=create_engine(s)
 try:
  async with Database(async_sessionmaker(e,expire_on_commit=False)).transaction() as repo:
   await repo.lock_authorization()
   c=await repo.conversation({conv!r},lock=True);assert c.user_id=={user_id!r}
   m=await repo.message({crashed!r});assert m.conversation_id==c.id and m.status=='generating'
   m.generation_deadline_at=await repo.now()-timedelta(seconds=1)
 finally:await e.dispose()
asyncio.run(main())
""",
        )
        # 不读取业务详情触发惰性恢复，先等待 Worker 真实处理。
        status = run(
            ["exec", "-T", "api", "python", "-"],
            input=f"""
import asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import load_settings
from app.infrastructure.database import create_engine
from app.infrastructure.repositories import Database
async def main():
 e=create_engine(load_settings());db=Database(async_sessionmaker(e,expire_on_commit=False))
 try:
  for _ in range(30):
   async with db.transaction() as repo:m=await repo.message({crashed!r});status=m.status
   if status!='generating':print(status);return
   await asyncio.sleep(.25)
  raise AssertionError('Worker recovery timed out')
 finally:await e.dispose()
asyncio.run(main())
""",
        )
        assert status.strip() == "interrupted"
        repeat = client.post(
            f"conversations/{conv}/messages:stream",
            json={"client_request_id": crash_request, "question": "重复"},
        )
        assert repeat.status_code == 409
        print("Qdrant 故障流内错误、真实 API SIGKILL 与 Worker 恢复通过", flush=True)
    finally:
        # 先按事实清除本次用户的会话引用，再复用阶段 4 的文档/向量/文件清理。
        cleanup = f"""
import asyncio
from sqlalchemy import select,delete,update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import load_settings
from app.infrastructure.database import create_engine
from app.infrastructure.repositories import Database
from app.infrastructure.models import Citation,Conversation,Message,Chunk,Document,IndexVersion,Job,Membership,KnowledgeBase,User
from app.infrastructure.vectors import Vectors
from app.infrastructure.files import Files
async def main():
 s=load_settings();assert make_url(s.database_url.get_secret_value()).database.endswith('_test')
 e=create_engine(s);db=Database(async_sessionmaker(e,expire_on_commit=False));vectors=Vectors(s);files=Files(s)
 try:
  async with db.transaction() as repo:
   kbs=(await repo.session.scalars(select(KnowledgeBase.id).where(KnowledgeBase.created_by=={user_id!r}))).all()
   versions=(await repo.session.scalars(select(IndexVersion).where(IndexVersion.kb_id.in_(kbs)))).all()
   keys=(await repo.session.scalars(select(Document.storage_key).where(Document.kb_id.in_(kbs)))).all()
  for v in versions:
   if v.collection_name:await vectors.delete_version(v.collection_name,v.id)
  for key in keys:await files.remove(key)
  async with db.transaction() as repo:
   await repo.lock_authorization()
   convs=select(Conversation.id).where(Conversation.user_id=={user_id!r});msgs=select(Message.id).where(Message.conversation_id.in_(convs))
   await repo.session.execute(delete(Citation).where(Citation.message_id.in_(msgs)))
   await repo.session.execute(update(Conversation).where(Conversation.user_id=={user_id!r}).values(active_message_id=None))
   await repo.session.execute(delete(Message).where(Message.conversation_id.in_(convs),Message.role=='assistant'))
   await repo.session.execute(delete(Message).where(Message.conversation_id.in_(convs)))
   await repo.session.execute(delete(Conversation).where(Conversation.user_id=={user_id!r}))
   await repo.session.execute(update(Document).where(Document.kb_id.in_(kbs)).values(active_index_version_id=None,current_job_id=None))
   for model in [Chunk,IndexVersion]:await repo.session.execute(delete(model).where(model.kb_id.in_(kbs)))
   await repo.session.execute(update(Job).where(Job.kb_id.in_(kbs)).values(retry_of_job_id=None))
   for model in [Job,Document,Membership]:await repo.session.execute(delete(model).where(model.kb_id.in_(kbs)))
   await repo.session.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(kbs)))
   await repo.session.execute(delete(User).where(User.id=={user_id!r}))
 finally:await vectors.close();await e.dispose()
asyncio.run(main())
"""
        try:
            run(["exec", "-T", "api", "python", "-"], input=cleanup)
        finally:
            client.close()


def wait(read, predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.25)
    raise AssertionError("网络状态未在约定时间达到预期")


class HttpResponse:
    def __init__(self, response):
        self.response = response
        self.status_code = response.status
        self.headers = dict((k.lower(), v) for k, v in response.getheaders())
        self.body = None

    def json(self):
        if self.body is None:
            self.body = self.response.read()
        return json.loads(self.body)

    def iter_lines(self):
        for line in self.response:
            yield line.decode("utf-8").rstrip("\r\n")


class Http:
    def __init__(self, base):
        self.base, self.headers = base, {}

    @contextmanager
    def stream(self, method, path, *, json=None, files=None):
        target = urlsplit(urljoin(self.base, path))
        connection = HTTPConnection(target.hostname, target.port, timeout=140)
        headers = dict(self.headers)
        body = None
        if json is not None:
            import json as encoding

            body = encoding.dumps(json).encode()
            headers["Content-Type"] = "application/json"
        if files:
            name, content, media = files["file"]
            boundary = "stage5" + uuid4().hex
            body = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: {media}\r\n\r\n'
                ).encode()
                + content
                + f"\r\n--{boundary}--\r\n".encode()
            )
            headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        try:
            connection.request(
                method,
                target.path + ("?" + target.query if target.query else ""),
                body=body,
                headers=headers,
            )
            yield HttpResponse(connection.getresponse())
        finally:
            connection.close()

    def post(self, path, **kwargs):
        with self.stream("POST", path, **kwargs) as response:
            response.body = response.response.read()
            return response

    def get(self, path):
        with self.stream("GET", path) as response:
            response.body = response.response.read()
            return response

    def close(self):
        pass
