"""在独立 Compose 项目验证阶段 2；不删除任何卷，所有临时记录均有专用名称。"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def main(stage=2, services_only=False) -> None:
    env_file = ROOT / f".env.stage{stage}-test"
    project = f"chat-rag-stage{stage}-test"
    if not env_file.exists():
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/init_env.py"),
                "--test",
                "--stage",
                str(stage),
                "--output",
                str(env_file),
            ],
            check=True,
        )
    values = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if line and not line.startswith("#")
    )
    if values.get("COMPOSE_PROJECT_NAME") != project or not values.get(
        "MYSQL_DATABASE", ""
    ).endswith("_test"):
        raise SystemExit("只接受对应阶段的专用测试项目及 _test 数据库")
    secrets = [
        values[key]
        for key in (
            "MYSQL_PASSWORD",
            "MYSQL_ROOT_PASSWORD",
            "QDRANT_API_KEY",
            "AUTH_SECRET",
        )
    ]
    if stage >= 4:
        key = os.environ.get("SILICONFLOW_API_KEY", "")
        if not key:
            for line in (ROOT / ".env").read_text().splitlines():
                if line.startswith("SILICONFLOW_API_KEY="):
                    key = line.partition("=")[2].strip().strip("\"'")
                    break
        if not key:
            raise SystemExit("阶段 4 live 验收需要本地 SILICONFLOW_API_KEY")
        os.environ["SILICONFLOW_API_KEY"] = key
        secrets.append(key)
    compose = ["docker", "compose", "--env-file", str(env_file), "-p", project]
    ARTIFACTS.mkdir(exist_ok=True)
    log_path = ARTIFACTS / f"stage{stage}-verification.log"
    log_path.write_text("")

    def run(args, *, input=None, timeout=600):
        result = subprocess.run(
            [*compose, *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            input=input,
            timeout=timeout,
            check=False,
        )
        output = result.stdout + result.stderr
        safe_output = output
        for secret in secrets:
            safe_output = safe_output.replace(secret, "[REDACTED]")
        with log_path.open("a") as log:
            log.write("\n$ compose " + " ".join(args) + "\n" + safe_output)
        if result.returncode:
            print(safe_output[-6000:])
            raise RuntimeError(f"命令失败：compose {' '.join(args[:3])}")
        return result.stdout

    def step(message):
        print(message, flush=True)

    base = f"http://127.0.0.1:{int(values['WEB_PORT'])}"

    def get(path):
        request = urllib.request.Request(
            base + path, headers={"X-Request-ID": "stage2-verification"}
        )
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def wait_ready(expected=200, dependency=None):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status, _, body = get("/api/health/ready")
            report = json.loads(body)
            if status == expected and (
                not dependency or report["checks"][dependency]["status"] == "unavailable"
            ):
                return report
            time.sleep(1)
        raise AssertionError("健康状态未在 90 秒内达到预期")

    if services_only:
        step("本次只执行服务验收（步骤 3–7）；不构建、不运行 pytest，测试结果需另行记录")
    else:
        step("1/7 检查 Compose、构建运行镜像与测试镜像")
        run(["config", "--quiet"])
        run(["stop", "worker"]) if stage >= 4 else None
        run(
            ["build", "api", "migrate", "web", "test", *(["worker"] if stage >= 4 else [])],
            timeout=1200,
        )
        step("2/7 启动真实 MySQL/Qdrant，执行单元及服务集成测试")
        run(["up", "-d", "--wait", "--wait-timeout", "180", "mysql", "qdrant"])
        output = run(
            [
                "run",
                "--rm",
                "-e",
                "ALLOW_TEST_DATABASE=1",
                "test",
                "pytest",
                "-m",
                "unit or integration",
                "-q",
            ]
        )
        print(output.strip(), flush=True)
        if stage >= 4:
            live = run(
                [
                    "run",
                    "--rm",
                    "-e",
                    "ALLOW_TEST_DATABASE=1",
                    "test",
                    "pytest",
                    "-m",
                    "live",
                    "-q",
                    "-s",
                ]
            )
            print(live.strip(), flush=True)
    step("3/7 启动完整骨架，验证迁移任务、页面、代理及 OpenAPI")
    run(["up", "-d", "--wait", "--wait-timeout", "180"])
    wait_ready()
    status, _, page = get("/")
    assert status == 200 and '<html lang="zh-CN">' in page.decode()
    status, headers, body = get("/api/health/live")
    assert status == 200 and json.loads(body) == {"status": "ok"}
    assert headers["X-Request-ID"] == "stage2-verification"
    actual_spec = json.loads(
        run(
            [
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json').read().decode())",
            ]
        )
    )
    assert actual_spec == json.loads((ROOT / "docs/openapi.json").read_text())
    if stage >= 3:
        from stage3_network import verify

        verify(run, base, secrets)
    if stage >= 4:
        from stage4_network import verify as verify_documents

        verify_documents(run, base, secrets)
    if stage >= 5:
        from stage5_network import verify as verify_chat

        verify_chat(run, base, secrets)

    token = uuid4().hex
    collection = f"test_stage2_persistence_{token}"
    point = str(uuid4())
    probe_script = """
import asyncio, os
import httpx
from sqlalchemy import text
from app.core.config import load_settings
from app.infrastructure.database import create_engine

async def main():
    settings = load_settings()
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            DB_OPERATION
    finally:
        await engine.dispose()
    async with httpx.AsyncClient(base_url=str(settings.qdrant_url), headers={"api-key": settings.qdrant_api_key.get_secret_value()}, trust_env=False, timeout=10) as client:
        QDRANT_OPERATION
asyncio.run(main())
"""

    def probe(db_operation, qdrant_operation):
        script = probe_script.replace("DB_OPERATION", db_operation).replace(
            "QDRANT_OPERATION", qdrant_operation
        )
        run(["exec", "-T", "api", "python", "-"], input=script)

    created = False
    try:
        step("4/7 写入独立持久化测试记录")
        created = True
        probe(
            'await conn.execute(text("CREATE TABLE stage2_persistence_probe (id CHAR(32) PRIMARY KEY)")); '
            f'await conn.execute(text("INSERT INTO stage2_persistence_probe VALUES (:id)"), {{"id": "{token}"}})',
            f'response = await client.put("/collections/{collection}", json={{"vectors": {{"size": 2, "distance": "Cosine"}}}}); response.raise_for_status(); '
            f'response = await client.put("/collections/{collection}/points?wait=true", json={{"points": [{{"id": "{point}", "vector": [1.0, 0.0], "payload": {{"probe": "{token}"}}}}]}}); response.raise_for_status()',
        )
        step("5/7 分别停止 MySQL/Qdrant，验证 ready=503、live=200，再恢复服务")
        for dependency in ("mysql", "qdrant"):
            try:
                run(["stop", dependency])
                wait_ready(503, dependency)
                assert get("/api/health/live")[0] == 200
            finally:
                run(["up", "-d", "--wait", "--wait-timeout", "180", dependency])
            wait_ready()

        step("6/7 重建两个数据容器，验证命名卷中的记录仍存在")
        run(
            [
                "up",
                "-d",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "180",
                "mysql",
                "qdrant",
            ]
        )
        wait_ready()
        for dependency, destination in (
            ("mysql", "/var/lib/mysql"),
            ("qdrant", "/qdrant/storage"),
        ):
            container = run(["ps", "-q", dependency]).strip()
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{json .Mounts}}", container],
                capture_output=True,
                text=True,
                check=True,
            )
            mounts = json.loads(result.stdout)
            assert any(
                mount["Type"] == "volume" and mount["Destination"] == destination
                for mount in mounts
            )
        probe(
            f'assert await conn.scalar(text("SELECT id FROM stage2_persistence_probe")) == "{token}"',
            f'response = await client.get("/collections/{collection}/points/{point}"); response.raise_for_status(); assert response.json()["result"]["payload"]["probe"] == "{token}"',
        )
    finally:
        if created:
            probe(
                'await conn.execute(text("DROP TABLE IF EXISTS stage2_persistence_probe"))',
                f'response = await client.delete("/collections/{collection}"); assert response.status_code in (200, 404)',
            )

    step("7/7 检查日志和浏览器静态资源没有后端密钥")
    logs = run(
        [
            "logs",
            "--no-color",
            "api",
            "migrate",
            "web",
            "mysql",
            "qdrant",
            *(["worker"] if stage >= 4 else []),
        ]
    )
    assert not any(secret in logs for secret in secrets), "日志中检测到密钥"
    sources = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.decode())
    assert sources
    for source in sources:
        status, _, asset = get(source)
        assert status == 200 and not any(secret.encode() in asset for secret in secrets)
    states = run(["ps", "--all", "--format", "{{.Service}} {{.State}} {{.Health}}"])
    print(states.strip(), flush=True)
    print(
        f"阶段 {stage} 服务验收全部通过；原始命令结果已脱敏保存到 {log_path}",
        flush=True,
    )


if __name__ == "__main__":
    main()
