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
    rag_configured: bool = False
    rag_available: bool = False


class ReadyResponse(HealthModel):
    status: Literal["ok", "unavailable"]
    checks: Checks
    capabilities: Capabilities = Capabilities()


class HealthService:
    def __init__(self, probes: DependencyProbes, rag_configured=False):
        self.probes = probes
        self.rag_configured = rag_configured

    async def readiness(self) -> ReadyResponse:
        (mysql, migrations), qdrant = await asyncio.gather(
            self.probes.mysql(), self.probes.qdrant()
        )
        # 健康检查不调用付费/外部模型；available 只表示配置和基础依赖满足，非供应商实时探活。
        ready = all(value == "ok" for value in (mysql, migrations, qdrant))
        return ReadyResponse(
            status="ok" if ready else "unavailable",
            checks=Checks(
                mysql=Check(status=mysql),
                qdrant=Check(status=qdrant),
                migrations=Check(status=migrations),
            ),
            capabilities=Capabilities(
                rag_configured=self.rag_configured, rag_available=self.rag_configured and ready
            ),
        )
