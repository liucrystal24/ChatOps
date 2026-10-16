import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError

from app.core.errors import AppError
from app.infrastructure.models import AuthorizationGuard
from app.modules.knowledge_bases.schemas import KnowledgeBaseCreate, Role

pytestmark = pytest.mark.integration
BASE = "/api/v1/knowledge-bases"


def create(bundle, name="私有知识库"):
    response = bundle.client.post(BASE, headers=bundle.headers["alice"], json={"name": name})
    assert response.status_code == 201
    assert response.json()["my_role"] == "owner"
    assert response.json()["embedding_profile_id"] is None
    return response.json()["id"]


async def test_private_isolation_admin_cannot_bypass_and_immediate_removal(bundle):
    b = bundle
    kb = create(b)
    for user in ("bob", "admin"):
        assert b.client.get(BASE, headers=b.headers[user]).json()["items"] == []
        assert b.client.get(f"{BASE}/{kb}", headers=b.headers[user]).status_code == 404
        assert b.client.get(f"{BASE}/{kb}/members", headers=b.headers[user]).status_code == 404
    path = f"{BASE}/{kb}/members/{b.users['bob'].id}"
    assert (
        b.client.put(path, headers=b.headers["alice"], json={"role": "viewer"}).status_code == 200
    )
    assert b.client.get(f"{BASE}/{kb}", headers=b.headers["bob"]).status_code == 200
    assert b.client.delete(path, headers=b.headers["alice"]).status_code == 204
    assert b.client.get(f"{BASE}/{kb}", headers=b.headers["bob"]).status_code == 404
    assert b.client.get(BASE, headers=b.headers["bob"]).json()["items"] == []


@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_role_boundaries_and_owner_changes(bundle, role):
    b = bundle
    kb = create(b)
    assert (
        b.client.put(
            f"{BASE}/{kb}/members/{b.users['bob'].id}",
            headers=b.headers["alice"],
            json={"role": role},
        ).status_code
        == 200
    )
    assert b.client.get(f"{BASE}/{kb}", headers=b.headers["bob"]).status_code == 200
    for method, path, body in (
        ("patch", f"{BASE}/{kb}", {"name": "forbidden"}),
        ("delete", f"{BASE}/{kb}", None),
        ("get", f"{BASE}/{kb}/members", None),
        ("put", f"{BASE}/{kb}/members/{b.users['admin'].id}", {"role": "owner"}),
        ("delete", f"{BASE}/{kb}/members/{b.users['alice'].id}", None),
    ):
        response = b.client.request(method, path, headers=b.headers["bob"], json=body)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"
    response = b.client.patch(
        f"{BASE}/{kb}", headers=b.headers["alice"], json={"name": "新名称", "description": "已修改"}
    )
    assert response.status_code == 200 and response.json()["name"] == "新名称"
    assert response.json()["created_at"].endswith("Z")


async def test_last_owner_remove_demote_disable_and_deleted_parent(bundle):
    b = bundle
    kb = create(b)
    path = f"{BASE}/{kb}/members/{b.users['alice'].id}"
    for response in (
        b.client.delete(path, headers=b.headers["alice"]),
        b.client.put(path, headers=b.headers["alice"], json={"role": "editor"}),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LAST_OWNER_REQUIRED"
    with pytest.raises(AppError, match="LAST_OWNER_REQUIRED"):
        await b.identity.set_active(b.users["alice"].username, False)
    assert b.client.get("/api/v1/auth/me", headers=b.headers["alice"]).status_code == 200
    assert b.client.delete(f"{BASE}/{kb}", headers=b.headers["alice"]).status_code == 204
    assert b.client.get(f"{BASE}/{kb}", headers=b.headers["alice"]).status_code == 404
    assert b.client.get(f"{BASE}/{kb}/members", headers=b.headers["alice"]).status_code == 404
    await b.identity.set_active(b.users["alice"].username, False)


async def test_disabled_user_rejected_with_existing_jwt_and_disabled_target(bundle):
    b = bundle
    kb = create(b)
    await b.identity.set_active(b.users["bob"].username, False)
    response = b.client.get("/api/v1/auth/me", headers=b.headers["bob"])
    assert response.status_code == 401 and response.json()["error"]["code"] == "USER_DISABLED"
    assert b.client.get(BASE, headers=b.headers["bob"]).status_code == 401
    assert (
        b.client.put(
            f"{BASE}/{kb}/members/{b.users['bob'].id}",
            headers=b.headers["alice"],
            json={"role": "owner"},
        ).status_code
        == 404
    )
    for username, password in (
        (b.users["bob"].username, b.password),
        (b.users["alice"].username, "wrong-password"),
        ("unknown-account", b.password),
    ):
        error = b.client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert error.status_code == 401 and error.json()["error"]["code"] == "AUTH_INVALID"
        assert b.password not in error.text
    await b.identity.set_active(b.users["bob"].username, True)
    assert b.client.get("/api/v1/auth/me", headers=b.headers["bob"]).status_code == 200


async def test_http_validation_no_auth_rate_limit_and_no_private_fields(bundle):
    b = bundle
    kb = create(b)
    assert b.client.get(BASE).status_code == 401
    assert b.client.get(BASE, headers={"Authorization": "Bearer invalid-token"}).status_code == 401
    for data in (
        {},
        {"name": None},
        {"description": None},
        {"name": "  "},
        {"created_by": str(b.users["bob"].id)},
    ):
        assert (
            b.client.patch(f"{BASE}/{kb}", headers=b.headers["alice"], json=data).status_code == 422
        )
    assert b.client.get(BASE + "?limit=101", headers=b.headers["alice"]).status_code == 422
    assert b.client.get(BASE + "?user_id=foreign", headers=b.headers["alice"]).status_code == 422
    assert b.client.get(BASE + "?cursor=garbage", headers=b.headers["alice"]).status_code == 422
    response = b.client.get("/api/v1/auth/me", headers=b.headers["alice"])
    assert set(response.json()) == {"id", "username", "is_admin"}
    assert response.headers["cache-control"] == "no-store"
    for _ in range(11):
        response = b.client.post(
            "/api/v1/auth/login", json={"username": "rate-limit-target", "password": "wrong"}
        )
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


async def test_pagination_scopes_stable_order_and_member_pagination(bundle):
    b = bundle
    for name in ("一", "二", "三"):
        create(b, name)
    first = b.client.get(BASE + "?limit=2", headers=b.headers["alice"]).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = b.client.get(
        BASE, headers=b.headers["alice"], params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert len({item["id"] for item in first["items"] + second["items"]}) == 3
    assert (
        b.client.get(
            BASE, headers=b.headers["bob"], params={"cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    kb = first["items"][0]["id"]
    for name in ("bob", "admin"):
        b.client.put(
            f"{BASE}/{kb}/members/{b.users[name].id}",
            headers=b.headers["alice"],
            json={"role": "viewer"},
        )
    members = b.client.get(f"{BASE}/{kb}/members?limit=2", headers=b.headers["alice"]).json()
    assert len(members["items"]) == 2 and members["next_cursor"]
    assert (
        b.client.get(
            BASE, headers=b.headers["alice"], params={"cursor": members["next_cursor"]}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("operation", ["demote", "remove", "disable", "mixed"])
async def test_concurrent_owner_changes_cannot_remove_last_active_owner(bundle, operation):
    b = bundle
    alice, bob = str(b.users["alice"].id), str(b.users["bob"].id)
    kb = await b.knowledge.create(alice, KnowledgeBaseCreate(name="并发 owner"))
    await b.knowledge.put_member(alice, str(kb.id), bob, Role.OWNER)
    barrier = asyncio.Barrier(2)

    async def change(user_id, username, action):
        await barrier.wait()
        if action == "disable":
            return await b.identity.set_active(username, False)
        if action == "remove":
            return await b.knowledge.remove_member(user_id, str(kb.id), user_id)
        return await b.knowledge.put_member(user_id, str(kb.id), user_id, Role.VIEWER)

    results = await asyncio.gather(
        change(alice, b.users["alice"].username, "disable" if operation == "mixed" else operation),
        change(bob, b.users["bob"].username, "demote" if operation == "mixed" else operation),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], AppError)
    assert errors[0].code == "LAST_OWNER_REQUIRED"
    async with b.database.transaction() as repo:
        assert await repo.active_owner_count(str(kb.id), str(uuid4())) == 1


async def test_mysql_guard_really_blocks_independent_connection(bundle):
    b = bundle
    waiting = asyncio.Event()

    def before_execute(conn, cursor, statement, params, context, many):
        if "authorization_guard" in statement and "FOR UPDATE" in statement:
            waiting.set()

    task = None
    async with b.engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(select(AuthorizationGuard.id).with_for_update())
        event.listen(b.engine.sync_engine, "before_cursor_execute", before_execute)
        try:
            task = asyncio.create_task(b.identity.set_active(b.users["bob"].username, False))
            await asyncio.wait_for(waiting.wait(), 3)
            assert not task.done()
            await transaction.commit()
            result = await asyncio.wait_for(task, 5)
            assert result["is_active"] is False
        finally:
            event.remove(b.engine.sync_engine, "before_cursor_execute", before_execute)
            if transaction.is_active:
                await transaction.rollback()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_multi_library_disable_is_atomic_and_mysql_constraints(bundle):
    b = bundle
    alice, bob = str(b.users["alice"].id), str(b.users["bob"].id)
    one = await b.knowledge.create(alice, KnowledgeBaseCreate(name="可移交"))
    await b.knowledge.put_member(alice, str(one.id), bob, Role.OWNER)
    await b.knowledge.create(alice, KnowledgeBaseCreate(name="唯一 owner"))
    with pytest.raises(AppError, match="LAST_OWNER_REQUIRED"):
        await b.identity.set_active(b.users["alice"].username, False)
    assert (await b.identity.me(alice)).id == b.users["alice"].id
    for statement, params in (
        (
            "UPDATE knowledge_base_members SET role='admin' WHERE kb_id=:kb AND user_id=:user",
            {"kb": str(one.id), "user": alice},
        ),
        (
            "UPDATE knowledge_bases SET embedding_profile_id='invalid' WHERE id=:kb",
            {"kb": str(one.id)},
        ),
        ("INSERT INTO authorization_guard (id) VALUES (2)", {}),
    ):
        with pytest.raises(DBAPIError) as error:
            async with b.engine.begin() as connection:
                await connection.execute(text(statement), params)
        assert error.value.orig.args[0] == 3819
    for statement, params, errno in (
        (
            "INSERT INTO knowledge_base_members (kb_id,user_id,role) VALUES (:kb,:user,'viewer')",
            {"kb": str(one.id), "user": str(uuid4())},
            1452,
        ),
        (
            "UPDATE users SET username=:name WHERE id=:id",
            {"name": b.users["alice"].username, "id": bob},
            1062,
        ),
    ):
        with pytest.raises(DBAPIError) as error:
            async with b.engine.begin() as connection:
                await connection.execute(text(statement), params)
        assert error.value.orig.args[0] == errno


async def test_cli_existing_user_policy_list_and_owner_disable(bundle):
    b = bundle
    create(b)
    for args, code in (
        (["users", "list"], 0),
        (["users", "disable", b.users["alice"].username], 1),
        (["users", "disable", b.users["bob"].username], 0),
        (["users", "enable", b.users["bob"].username], 0),
        (["users", "create", b.users["alice"].username, "--password-stdin"], 1),
    ):
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "app.cli", *args],
            input=b.password + "\n",
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == code
        assert b.password not in result.stdout + result.stderr
        if args[1] == "list":
            assert all("password_hash" not in row for row in json.loads(result.stdout))


async def test_cli_creates_real_user_without_exposing_password(bundle):
    b = bundle
    username = f"test_cli_{uuid4().hex}"
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "app.cli", "users", "create", username, "--password-stdin"],
        input=b.password + "\n",
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    created = json.loads(result.stdout)
    b.owned_ids.append(created["id"])
    assert b.password not in result.stdout + result.stderr
    response = b.client.post(
        "/api/v1/auth/login", json={"username": username, "password": b.password}
    )
    assert response.status_code == 200
    assert created["username"] == username and "password_hash" not in created


async def test_waiting_writer_rechecks_disabled_actor_after_guard(bundle):
    b = bundle
    waiting = asyncio.Event()

    def capture(conn, cursor, statement, params, context, many):
        if "authorization_guard" in statement and "FOR UPDATE" in statement:
            waiting.set()

    task = None
    async with b.engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(select(AuthorizationGuard.id).with_for_update())
        event.listen(b.engine.sync_engine, "before_cursor_execute", capture)
        try:
            task = asyncio.create_task(
                b.knowledge.create(str(b.users["alice"].id), KnowledgeBaseCreate(name="排队写入"))
            )
            await asyncio.wait_for(waiting.wait(), 3)
            await connection.execute(
                text("UPDATE users SET is_active=0 WHERE id=:id"), {"id": str(b.users["alice"].id)}
            )
            await transaction.commit()
            with pytest.raises(AppError, match="USER_DISABLED"):
                await asyncio.wait_for(task, 5)
        finally:
            event.remove(b.engine.sync_engine, "before_cursor_execute", capture)
            if transaction.is_active:
                await transaction.rollback()
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
