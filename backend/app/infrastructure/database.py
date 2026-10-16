from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


def migration_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


def expected_heads() -> set[str]:
    return set(ScriptDirectory.from_config(migration_config()).get_heads())


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=settings.health_timeout_seconds,
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.connect_timeout,
            "init_command": "SET time_zone = '+00:00'",
        },
    )
