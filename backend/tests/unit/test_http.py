import io
import json
import logging
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.api.health import health_service
from app.infrastructure.health import DependencyProbes
from app.main import create_app
from app.modules.health.service import HealthService
from tests.unit.test_config import SECRET, config

pytestmark = pytest.mark.unit


def client_for(mysql=("ok", "ok"), qdrant="ok"):
    app = create_app(config())
    probes = AsyncMock()
    probes.mysql.return_value = mysql
    probes.qdrant.return_value = qdrant
    app.dependency_overrides[health_service] = lambda: HealthService(probes)
    return app, TestClient(app), probes


@pytest.mark.parametrize(
    ("mysql", "qdrant", "status"),
    [
        (("ok", "ok"), "ok", 200),
        (("unavailable", "unavailable"), "ok", 503),
        (("ok", "pending"), "ok", 503),
        (("ok", "ok"), "unavailable", 503),
    ],
)
def test_health_contract_and_independent_liveness(mysql, qdrant, status):
    _, client, probes = client_for(mysql, qdrant)
    live = client.get("/health/live")
    assert live.status_code == 200 and live.json() == {"status": "ok"}
    probes.mysql.assert_not_called()
    response = client.get("/health/ready")
    assert response.status_code == status
    assert response.json()["checks"]["migrations"]["status"] == mysql[1]
    assert response.json()["capabilities"] == {"rag_configured": False, "rag_available": False}
    assert response.headers["cache-control"] == "no-store"
    assert SECRET not in response.text


def test_request_ids_http_errors_and_openapi():
    _, client, _ = client_for()
    response = client.get("/missing", headers={"X-Request-ID": "trace_123"})
    assert response.status_code == 404
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"] == "trace_123"
    response = client.post("/health/live", headers={"X-Request-ID": "invalid id"})
    assert response.status_code == 405
    UUID(response.headers["x-request-id"])
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    spec = client.get("/openapi.json").json()
    assert {
        "/health/live",
        "/health/ready",
        "/api/v1/auth/login",
        "/api/v1/knowledge-bases",
    } <= set(spec["paths"])
    assert not any("documents" in path or "conversations" in path for path in spec["paths"])
    assert "503" in spec["paths"]["/health/ready"]["get"]["responses"]


def test_validation_and_unexpected_errors_do_not_leak_body_or_exception():
    app, client, _ = client_for()

    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")
        password: int

    @app.post("/test-input")
    async def validate(body: Input):
        return body

    @app.get("/test-failure")
    async def failure():
        raise RuntimeError(f"database connection and SQL: {SECRET}")

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("chat_rag.requests")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        invalid = client.post("/test-input", json={"password": SECRET, "token": SECRET})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["details"]["fields"]
        failed = client.get(f"/test-failure?secret={SECRET}", headers={"Authorization": SECRET})
        assert failed.status_code == 500
        assert failed.json()["error"]["request_id"] == failed.headers["x-request-id"]
        assert SECRET not in invalid.text + failed.text + stream.getvalue()
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert events[-1]["error_type"] == "RuntimeError"
        assert events[-1]["route"] == "/test-failure"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


async def test_qdrant_probe_checks_credentials_and_handles_timeout():
    async def unauthorized(request):
        assert request.headers["api-key"] == "test-key"
        return httpx.Response(401, text=SECRET)

    async with httpx.AsyncClient(
        base_url="http://qdrant:6333",
        headers={"api-key": "test-key"},
        transport=httpx.MockTransport(unauthorized),
    ) as client:
        probes = DependencyProbes(None, client, {"0001_baseline"}, 0.1)
        assert await probes.qdrant() == "unavailable"

    async def timeout(request):
        raise httpx.ReadTimeout(SECRET)

    async with httpx.AsyncClient(
        base_url="http://qdrant:6333", transport=httpx.MockTransport(timeout)
    ) as client:
        assert await DependencyProbes(None, client, set(), 0.1).qdrant() == "unavailable"
