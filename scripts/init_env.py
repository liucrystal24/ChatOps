"""生成本地配置，不覆盖既有文件，也不打印随机密钥。"""

import argparse
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".env")
    parser.add_argument("--test", action="store_true", help="使用独立测试项目、数据库和 Web 端口")
    args = parser.parse_args()
    password = secrets.token_hex(24)
    database = "chat_rag_test" if args.test else "chat_rag"
    project = "chat-rag-stage2-test" if args.test else "chat-rag"
    port = 18080 if args.test else 8080
    values = {
        "COMPOSE_PROJECT_NAME": project,
        "WEB_PORT": port,
        "MYSQL_DATABASE": database,
        "MYSQL_USER": "chat_rag",
        "MYSQL_PASSWORD": password,
        "MYSQL_ROOT_PASSWORD": secrets.token_hex(24),
        "MYSQL_PORT": 13307 if args.test else 3307,
        "QDRANT_PORT": 16333 if args.test else 6333,
        "QDRANT_API_KEY": secrets.token_hex(32),
        "DATABASE_URL": f"mysql+asyncmy://chat_rag:{password}@127.0.0.1:"
        f"{13307 if args.test else 3307}/{database}?charset=utf8mb4",
        "QDRANT_URL": f"http://127.0.0.1:{16333 if args.test else 6333}",
        "HEALTH_TIMEOUT_SECONDS": 3,
    }
    path = Path(args.output)
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
