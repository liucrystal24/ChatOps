import asyncio

import httpx
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class DependencyProbes:
    def __init__(
        self, engine: AsyncEngine, client: httpx.AsyncClient, heads: set[str], timeout: float
    ):
        self.engine = engine
        self.client = client
        self.heads = heads
        self.timeout = timeout

    async def mysql(self) -> tuple[str, str]:
        connected = False
        try:
            async with asyncio.timeout(self.timeout), self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                connected = True
                actual = await connection.run_sync(
                    lambda sync: set(MigrationContext.configure(sync).get_current_heads())
                )
                return "ok", "ok" if actual == self.heads else "pending"
        except Exception:
            return "ok" if connected else "unavailable", "unavailable"

    async def qdrant(self) -> str:
        try:
            async with asyncio.timeout(self.timeout):
                response = await self.client.get("/collections")
                response.raise_for_status()
                payload = response.json()
                if payload["status"] != "ok" or not isinstance(
                    payload["result"]["collections"], list
                ):
                    return "unavailable"
            return "ok"
        except Exception:
            return "unavailable"
