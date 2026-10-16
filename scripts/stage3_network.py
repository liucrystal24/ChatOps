"""只供独立阶段 3 验收脚本调用，使用真实 CLI + Nginx + HTTP + MySQL。"""

import json
import secrets
import urllib.error
import urllib.request
from uuid import uuid4


def verify(run, base: str, secret_values: list[str]):
    prefix = "test_network_" + uuid4().hex
    password = secrets.token_urlsafe(24)
    secret_values.append(password)
    users = []

    def http(method, path, token=None, body=None, expected=200):
        headers = {"X-Request-ID": "stage3-network"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            base + "/api/v1" + path,
            method=method,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            assert response.status == expected, f"网络接口验收失败：{method} {path}"
            assert response.headers["X-Request-ID"] == "stage3-network"
            assert response.headers["Cache-Control"] == "no-store"
            data = response.read()
            return json.loads(data) if data else None

    try:
        for suffix in ("alice", "bob"):
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
                        prefix + "_" + suffix,
                        "--password-stdin",
                    ],
                    input=password + "\n",
                )
            )
            users.append(user)
        tokens = []
        for user in users:
            result = http(
                "POST", "/auth/login", body={"username": user["username"], "password": password}
            )
            tokens.append(result["access_token"])
            secret_values.append(result["access_token"])
        alice, bob = tokens
        assert http("GET", "/auth/me", alice)["id"] == users[0]["id"]
        kb = http("POST", "/knowledge-bases", alice, {"name": "独立网络验收"}, 201)["id"]
        path = "/knowledge-bases/" + kb
        assert http("GET", "/knowledge-bases", bob)["items"] == []
        http("GET", path, bob, expected=404)
        http("GET", path + "/members", bob, expected=404)
        member = path + "/members/" + users[1]["id"]
        http("PUT", member, alice, {"role": "viewer"})
        assert http("GET", path, bob)["my_role"] == "viewer"
        http("PATCH", path, bob, {"name": "拒绝写入"}, 403)
        http("PUT", member, alice, {"role": "editor"})
        http("GET", path + "/members", bob, expected=403)
        error = http("DELETE", path + "/members/" + users[0]["id"], alice, expected=409)
        assert error["error"]["code"] == "LAST_OWNER_REQUIRED"
        http("DELETE", member, alice, expected=204)
        http("GET", path, bob, expected=404)
        run(
            [
                "exec",
                "-T",
                "api",
                "python",
                "-m",
                "app.cli",
                "users",
                "disable",
                users[1]["username"],
            ]
        )
        error = http("GET", "/auth/me", bob, expected=401)
        assert error["error"]["code"] == "USER_DISABLED"
        run(
            [
                "exec",
                "-T",
                "api",
                "python",
                "-m",
                "app.cli",
                "users",
                "enable",
                users[1]["username"],
            ]
        )
        http("PATCH", path, alice, {"description": "网络接口实际写入"})
        http("DELETE", path, alice, expected=204)
        http("GET", path, alice, expected=404)
    finally:
        if users:
            ids = [user["id"] for user in users]
            cleanup = f"""
import asyncio
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from app.core.config import load_settings
from app.infrastructure.database import create_engine
from app.infrastructure.models import AuthorizationGuard, KnowledgeBase, Membership, User
async def main():
    settings = load_settings()
    assert make_url(settings.database_url.get_secret_value()).database.endswith('_test')
    engine = create_engine(settings)
    ids = {ids!r}
    try:
        async with engine.begin() as conn:
            await conn.execute(select(AuthorizationGuard.id).with_for_update())
            kbs = (await conn.execute(select(KnowledgeBase.id).where(KnowledgeBase.created_by.in_(ids)))).scalars().all()
            if kbs:
                await conn.execute(delete(Membership).where(Membership.kb_id.in_(kbs)))
                await conn.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(kbs)))
            await conn.execute(delete(User).where(User.id.in_(ids)))
    finally:
        await engine.dispose()
asyncio.run(main())
"""
            run(["exec", "-T", "api", "python", "-"], input=cleanup)
    print("真实 CLI + Web HTTP 双账号、角色、撤权、停用和删除检查通过", flush=True)
