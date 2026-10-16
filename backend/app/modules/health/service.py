import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.infrastructure.health import DependencyProbes


class HealthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiveResponse(HealthModel):
    status: Literal["ok"] = "ok"


class Check(HealthModel):
    status: Literal["ok", "unavailable", "pending"]


class Checks(HealthModel):
    mysql: Check
    qdrant: Check
    migrations: Check


class Capabilities(HealthModel):
    rag_configured: Literal[False] = False
    rag_available: Literal[False] = False


class ReadyResponse(HealthModel):
    status: Literal["ok", "unavailable"]
    checks: Checks
    capabilities: Capabilities = Capabilities()


class HealthService:
    def __init__(self, probes: DependencyProbes):
        self.probes = probes

    async def readiness(self) -> ReadyResponse:
        (mysql, migrations), qdrant = await asyncio.gather(
            self.probes.mysql(), self.probes.qdrant()
        )
        # 缺模型配置不影响基础服务就绪，但本阶段也不能宣称 RAG 已实现。
        ready = all(value == "ok" for value in (mysql, migrations, qdrant))
        return ReadyResponse(
            status="ok" if ready else "unavailable",
            checks=Checks(
                mysql=Check(status=mysql),
                qdrant=Check(status=qdrant),
                migrations=Check(status=migrations),
            ),
        )
