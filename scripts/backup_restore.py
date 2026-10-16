"""冷备份三个命名卷；恢复仅允许尚不存在的 *-test 项目，绝不覆盖已有卷。"""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOLUMES = ("mysql_data", "qdrant_data", "files_data")
ARCHIVE = "import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz'); t.add('/source',arcname='.'); t.close()"
EXTRACT = """
import sys,tarfile
# data_filter 拒绝目录穿越和危险链接；恢复数字 UID/GID，保留非 root API 文件所有权。
def safe(member, destination):
    if member.issym() and member.name in ('./mysql.sock','mysql.sock') and member.linkname == '/var/run/mysqld/mysqld.sock':
        return None
    uid,gid=member.uid,member.gid
    checked=tarfile.data_filter(member,destination)
    if checked is not None:
        checked.uid,checked.gid=uid,gid
        checked.uname=checked.gname=None
    return checked
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|gz') as archive:
    archive.extractall('/restore',filter=safe)
"""


def run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    if result.returncode:
        raise RuntimeError("备份/恢复子命令失败；未展示环境或原始错误")
    return result


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["backup", "restore-empty"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--helper-image", default="chat-rag-backend:0.5.0")
    args = parser.parse_args()
    values = dict(
        line.split("=", 1)
        for line in args.env_file.read_text().splitlines()
        if line and not line.startswith("#")
    )
    project = values.get("COMPOSE_PROJECT_NAME", "chat-rag")
    if not project or any(
        c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in project
    ):
        raise SystemExit("项目名不合法")
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(args.env_file.resolve()),
        "-p",
        project,
    ]
    folder = args.directory.resolve()
    if args.mode == "backup":
        folder.mkdir(parents=True, exist_ok=False, mode=0o700)
        metadata = {
            "created_at": datetime.now(UTC).isoformat(),
            "source_project": project,
            "archives": {},
            "method": "cold-three-volumes",
            "environment_included": False,
        }
        # 已有容器 start 会保留实际端口映射；禁止用 up 重建覆盖本机开发配置。
        result = subprocess.run(
            [*compose, "ps", "--services", "--status", "running"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        running = [
            service
            for service in result.stdout.splitlines()
            if service in ("web", "api", "worker", "mysql", "qdrant")
        ]
        try:
            run(
                [*compose, "stop", *running],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for volume in VOLUMES:
                name = f"{project}_{volume}"
                run(
                    ["docker", "volume", "inspect", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                path = folder / f"{volume}.tar.gz"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as output:
                    run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--network",
                            "none",
                            "--user",
                            "0",
                            "--entrypoint",
                            "python",
                            "-v",
                            f"{name}:/source:ro",
                            args.helper_image,
                            "-c",
                            ARCHIVE,
                        ],
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                    )
                metadata["archives"][volume] = {
                    "filename": path.name,
                    "sha256": digest(path),
                    "bytes": path.stat().st_size,
                }
            (folder / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
            (folder / "manifest.json").chmod(0o600)
        finally:
            if running:
                run(
                    [*compose, "start", *running],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        print("冷备份完成；原运行容器已启动。环境凭据需另外妥善保管：" + str(folder))
    else:
        if not project.endswith("-test") or not values.get(
            "MYSQL_DATABASE", ""
        ).endswith("_test"):
            raise SystemExit(
                "自动恢复只允许新建的 *-test 项目与 *_test 数据库；生产恢复需单独审核操作"
            )
        metadata = json.loads((folder / "manifest.json").read_text())
        for volume in VOLUMES:
            record = metadata["archives"][volume]
            if (
                record["filename"] != f"{volume}.tar.gz"
                or digest(folder / record["filename"]) != record["sha256"]
            ):
                raise SystemExit("归档文件校验失败")
            exists = subprocess.run(
                ["docker", "volume", "inspect", f"{project}_{volume}"],
                capture_output=True,
            )
            if exists.returncode == 0:
                raise SystemExit("目标卷已存在，拒绝覆盖：" + volume)
        for volume in VOLUMES:
            name = f"{project}_{volume}"
            run(
                [
                    "docker",
                    "volume",
                    "create",
                    "--label",
                    f"com.docker.compose.project={project}",
                    "--label",
                    f"com.docker.compose.volume={volume}",
                    name,
                ],
                stdout=subprocess.DEVNULL,
            )
            with (folder / metadata["archives"][volume]["filename"]).open(
                "rb"
            ) as archive:
                run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-i",
                        "--network",
                        "none",
                        "--user",
                        "0",
                        "--entrypoint",
                        "python",
                        "-v",
                        f"{name}:/restore",
                        args.helper_image,
                        "-c",
                        EXTRACT,
                    ],
                    stdin=archive,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        print("已恢复到新的测试卷；未启动容器、未覆盖任何已有数据。")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
