from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.documents import router as document_router
from app.api.health import router
from app.api.identity import router as identity_router
from app.api.knowledge_bases import router as knowledge_base_router
from app.core.config import Settings, load_settings
from app.core.errors import ErrorResponse, install_error_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.pagination import Cursors
from app.core.security import LoginLimiter, Security
from app.core.upload_limit import UploadLimitMiddleware
from app.infrastructure.database import create_engine, expected_heads
from app.infrastructure.files import Files
from app.infrastructure.health import DependencyProbes
from app.infrastructure.repositories import Database
from app.modules.documents.service import DocumentService
from app.modules.health.service import HealthService
from app.modules.identity.service import IdentityService
from app.modules.knowledge_bases.service import KnowledgeBaseService


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else load_settings()
        configure_logging()
        engine = create_engine(config)
        database = Database(async_sessionmaker(engine, expire_on_commit=False))
        security = Security(config)
        app.state.identity_service = IdentityService(database, security, LoginLimiter())
        app.state.knowledge_base_service = KnowledgeBaseService(
            database, Cursors(config.auth_secret.get_secret_value())
        )
        app.state.document_service = DocumentService(
            database, Files(config), Cursors(config.auth_secret.get_secret_value()), config
        )
        try:
            async with httpx.AsyncClient(
                base_url=str(config.qdrant_url),
                headers={"api-key": config.qdrant_api_key.get_secret_value()},
                timeout=config.health_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                app.state.health_service = HealthService(
                    DependencyProbes(
                        engine, client, expected_heads(), config.health_timeout_seconds
                    )
                )
                yield
        finally:
            await engine.dispose()
            security.close()

    app = FastAPI(title="Chat RAG API", version="0.4.0", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(UploadLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    errors = {
        status: {"model": ErrorResponse}
        for status in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
    }
    app.include_router(identity_router, responses=errors)
    app.include_router(knowledge_base_router, responses=errors)
    app.include_router(document_router, responses=errors)
    return app
