"""生成本地配置，不覆盖既有文件，也不打印随机密钥。"""

import argparse
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".env")
    parser.add_argument("--test", action="store_true", help="使用独立测试项目、数据库和 Web 端口")
    parser.add_argument("--stage", type=int, choices=[2, 3, 4, 5], default=2)
    parser.add_argument("--upgrade", action="store_true", help="仅为已有配置补齐缺失的认证配置")
    args = parser.parse_args()
    path = Path(args.output)
    if args.upgrade:
        if not path.is_file():
            parser.exit(1, "配置文件不存在，请先生成配置\n")
        content = path.read_text()
        keys = {line.split("=", 1)[0] for line in content.splitlines() if "=" in line}
        additions = {
            "AUTH_SECRET": secrets.token_hex(32),
            "AUTH_TOKEN_TTL_SECONDS": 1800,
        }
        missing = {key: value for key, value in additions.items() if key not in keys}
        if missing:
            with path.open("a") as output:
                if not content.endswith("\n"):
                    output.write("\n")
                output.writelines(f"{key}={value}\n" for key, value in missing.items())
        path.chmod(0o600)
        print(f"已补齐 {path} 中缺失的认证配置；未修改已有值、未输出密钥。")
        return
    password = secrets.token_hex(24)
    database = "chat_rag_test" if args.test else "chat_rag"
    project = "chat-rag-stage2-test" if args.test else "chat-rag"
    port = 18080 if args.test else 8080
    mysql_port = 13307 if args.test else 3307
    qdrant_port = 16333 if args.test else 6333
    if args.test and args.stage == 3:
        database, project, port = "chat_rag_stage3_test", "chat-rag-stage3-test", 18081
        mysql_port, qdrant_port = 13308, 16334
    if args.test and args.stage == 4:
        database, project, port = "chat_rag_stage4_test", "chat-rag-stage4-test", 18082
        mysql_port, qdrant_port = 13309, 16335
    if args.test and args.stage == 5:
        database, project, port = "chat_rag_stage5_test", "chat-rag-stage5-test", 18083
        mysql_port, qdrant_port = 13310, 16336
    values = {
        "COMPOSE_PROJECT_NAME": project,
        "WEB_PORT": port,
        "MYSQL_DATABASE": database,
        "MYSQL_USER": "chat_rag",
        "MYSQL_PASSWORD": password,
        "MYSQL_ROOT_PASSWORD": secrets.token_hex(24),
        "MYSQL_PORT": mysql_port,
        "QDRANT_PORT": qdrant_port,
        "QDRANT_API_KEY": secrets.token_hex(32),
        "AUTH_SECRET": secrets.token_hex(32),
        "AUTH_TOKEN_TTL_SECONDS": 1800,
        "DATABASE_URL": f"mysql+asyncmy://chat_rag:{password}@127.0.0.1:"
        f"{mysql_port}/{database}?charset=utf8mb4",
        "QDRANT_URL": f"http://127.0.0.1:{qdrant_port}",
        "HEALTH_TIMEOUT_SECONDS": 3,
    }
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.exit(1, f"文件已存在，未覆盖：{path}\n")
    with os.fdopen(fd, "w") as output:
        output.write("# 自动生成的本地配置；禁止提交。\n")
        output.writelines(f"{key}={value}\n" for key, value in values.items())
    print(f"已生成 {path}（权限 0600）；未输出密钥。")


if __name__ == "__main__":
    main()
