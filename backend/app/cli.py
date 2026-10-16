"""可信本机用户管理入口；密码只从交互终端或显式 stdin 读取。"""

import argparse
import asyncio
import getpass
import json
import sys

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import ConfigurationError, load_settings
from app.core.errors import AppError
from app.core.security import LoginLimiter, Security
from app.infrastructure.database import create_engine
from app.infrastructure.repositories import Database
from app.modules.identity.service import IdentityService


async def run(args, password=None):
    settings = load_settings()
    engine = create_engine(settings)
    security = Security(settings)
    try:
        service = IdentityService(
            Database(async_sessionmaker(engine, expire_on_commit=False)),
            security,
            LoginLimiter(),
        )
        if args.action == "create":
            result = (await service.create_user(args.username, password, args.admin)).model_dump(
                mode="json"
            )
        elif args.action in ("disable", "enable"):
            result = await service.set_active(args.username, args.action == "enable")
        else:
            result = await service.list_users()
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await engine.dispose()
        security.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="resource", required=True)
    users = commands.add_parser("users").add_subparsers(dest="action", required=True)
    create = users.add_parser("create")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    create.add_argument("--password-stdin", action="store_true")
    for action in ("disable", "enable"):
        users.add_parser(action).add_argument("username")
    users.add_parser("list")
    args = parser.parse_args()
    password = None
    try:
        if args.action == "create":
            if args.password_stdin:
                raw = sys.stdin.buffer.read(1026)
                password = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
            else:
                if not sys.stdin.isatty():
                    raise ValueError("非交互输入须显式使用 --password-stdin")
                password = getpass.getpass("密码（12–256 字符）：")
                if password != getpass.getpass("再次输入密码："):
                    raise ValueError("两次密码不一致")
        asyncio.run(run(args, password))
    except (AppError, ConfigurationError, ValueError) as error:
        parser.exit(
            1,
            f"{error.code + ': ' + error.message if isinstance(error, AppError) else str(error)}\n",
        )
    except SQLAlchemyError:
        parser.exit(1, "STORAGE_UNAVAILABLE: 数据服务暂不可用或尚未迁移\n")


if __name__ == "__main__":
    main()
