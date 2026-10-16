from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.health import router
from app.core.config import Settings, load_settings
from app.core.errors import install_error_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.infrastructure.database import create_engine, expected_heads
from app.infrastructure.health import DependencyProbes
from app.modules.health.service import HealthService


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else load_settings()
        configure_logging()
        engine = create_engine(config)
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

    app = FastAPI(title="Chat RAG API", version="0.2.0", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    return app
